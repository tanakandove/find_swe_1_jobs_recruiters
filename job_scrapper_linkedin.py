from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from datetime import datetime, timezone
import time
import random
import re

import h1b_sponsorship

"""
Return information: 
Columns:
1. Title - return the title
2. Company  - return the compnay name
3. Location - Only US jobs
4. Job Description - return the job description
5. Experience - Filtering for jobs that are New Grad, 0-1 years, 1+ years. 
6. Salary - Return the salary
7. Sponsorship Information - Yes or No or Unknown. 
Yes - means they provide CPT/OPT, HB Visa for international students
No - They do not sponsor for international students
Unknown - we could not tell. This is issue with our software not 100% correct. 
8. Url - original of job
9. Date Scrapped - date it was scrapped. 

Filters from the webiste we are scrapping, linkedin:
1. Location - united States. 
2. title - software engineer in the url. 
3. experience - 0-1+ Years, 1+ Years. (Need to improve the way we are filtering for experience between different companies write it differently)
"""

"""
definition of new grad:
1. title - #new grads
NEW_GRAD_TITLE_KEYWORDS = [
    "new grad", "new graduate", "entry level", "entry-level",
    "junior", "jr.", "intern", "internship", "graduate program",
]
2. experience - that is between 0-1years experience or 1+ experience
"""


#location filter
#implemented in the base url as united states
#scrapping from jobs in the past 5 says; hence, TPR=r432000"
#the goal is to run the job scrapping every 5 days to check for new jobs posted within the last 5 days
BASE_SEARCH_URL = (
    "https://www.linkedin.com/jobs/search/"
    "?keywords=software%20engineer"
    "&location=United%20States"
    "&f_TPR=r432000"
)

#constants
PAGE_SIZE = 25
# Kept conservative because this now runs against a real personal LinkedIn
# account (not a throwaway) -- lower volume per run, longer pauses below.
DEFAULT_MAX_PAGES = 5
DEFAULT_MAX_JOBS = 150

# Extra human-like pause every N description fetches, on top of the
# per-request delay, so requests don't arrive in one steady drumbeat.
DESCRIPTION_BATCH_SIZE = 20
DESCRIPTION_BATCH_PAUSE_RANGE = (25, 50)  # seconds

SPONSORSHIP_POSITIVE_PATTERNS = [
    r"\bopt\b",
    r"\bcpt\b",
    r"h[\s-]?1b",
    r"\bvisa sponsorship\b",
    r"\bsponsor (?:your )?visa\b",
    r"\bwill sponsor\b",
    r"\bcan sponsor\b",
]

SPONSORSHIP_NEGATIVE_PATTERNS = [
    r"no (?:visa )?sponsorship",
    r"cannot sponsor",
    r"not able to sponsor",
    r"unable to sponsor",
    r"must be (?:a )?us citizen",
    r"citizenship required",
    r"green card holders only",
    r"gc holders only",
    r"no cpt",
    r"no opt",
    r"no h[\s-]?1b",
]

#2. experience filter
#pass the description
#return the minimum number of years. 
#if nothing just return None
def check_experience(text):
    if not text:
        return (None, None)

    t = text.lower()

    # find all numbers that appear next to "year"/"years"
    years = re.findall(r"(\d+)\s*(?:year|years|yr|yrs)", t)

    # convert to ints
    years = [int(y) for y in years]

    if not years:
        return (None, None)

    # handle range like "0-1 years"
    if "-" in t or "–" in t:
        if len(years) >= 2:
            return (min(years), max(years))

    # handle "1+ years"
    if "+" in t:
        return (min(years), None)

    # handle "up to 1 year"
    if "up to" in t:
        return (0, max(years))

    # handle "at least 1 year" / "minimum 2 years"
    if "at least" in t or "minimum" in t:
        return (min(years), None)

    # fallback: "1 year experience"
    if len(years) == 1:
        return (years[0], years[0])

    return (None, None)


#we want search for new grad keywords
NEW_GRAD_TITLE_KEYWORDS = [
    "new grad", "new graduate", "entry level", "entry-level",
    "junior", "jr", "graduate", "university grad", "early career",
    "rotational", "associate", "campus",
]
# whole-word match (was plain substring before -- "ng" as a keyword was
# silently matching inside "Engineer" and accepting almost every title
# regardless of seniority; this also fixes "sr" matching inside words like
# "disruptive")
NEW_GRAD_KEYWORD_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in NEW_GRAD_TITLE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

#reject the senior positions
SENIOR_TITLE_KEYWORDS = [
    "senior", "staff", "principal", "lead", "manager", "director", "architect", "sr",
]
SENIOR_KEYWORD_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in SENIOR_TITLE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# Numbered/roman-numeral level tiers companies actually use in titles
# (e.g. "Software Engineer I", "SDE II", "Engineer 1"). I/II and 1/2 are the
# junior tiers we want; III/IV/V and 3+ read as more senior, not 0-2 yrs.
JUNIOR_LEVEL_PATTERN = re.compile(
    r"\b(?:software\s+engineer|engineer|swe|sde|developer)[\s\-]*(?:i|ii|1|2)\b",
    re.IGNORECASE,
)
SENIOR_LEVEL_PATTERN = re.compile(
    r"\b(?:software\s+engineer|engineer|swe|sde|developer)[\s\-]*(?:iii|iv|v|[3-9])\b",
    re.IGNORECASE,
)

# word-boundary match so "intern" doesn't false-positive inside "international"
INTERN_TITLE_PATTERN = re.compile(r"\bintern(?:ship)?\b", re.IGNORECASE)


def is_internship_title(title):
    return bool(INTERN_TITLE_PATTERN.search(title or ""))


#check for new grad keywords in the title
#check for popular keywords that reference new grad roles
#a lot of jobs write in the title some indicators for new grad roles
def search_new_grad_hints_title(title):
    t = title or ""

    # reject obvious senior titles, including "Engineer III/IV/V" style tiers
    if SENIOR_KEYWORD_PATTERN.search(t) or SENIOR_LEVEL_PATTERN.search(t):
        return False

    # accept entry/new grad titles, including "Engineer I/II" style tiers
    if NEW_GRAD_KEYWORD_PATTERN.search(t) or JUNIOR_LEVEL_PATTERN.search(t):
        return True

    return False


def is_entry_level_experience(min_years, max_years):
    # Accept anything overlapping 0-2 years (new grad / SWE1 / junior).
    # Reject roles that clearly start above that, e.g. "3+ years".
    if min_years is None and max_years is None:
        return False

    if min_years is not None:
        return min_years <= 2

    if max_years is not None:
        return max_years <= 2

    return False


#extract title
def extract_salary(text):
    if not text:
        return None

    t = text.replace("\n", " ")

    # salary range
    range_match = re.search(
        r"\$\s?[\d,]+(?:\s*[kK])?.{0,10}[-–].{0,10}\$\s?[\d,]+(?:\s*[kK])?.{0,10}(?:per\s+\w+|/yr|/year|/hr|/hour)?",
        t
    )
    if range_match:
        return range_match.group(0).strip()

    # single salary like "$80K/yr"
    single_match = re.search(
        r"\$\s?[\d,]+(?:\s*[kK])?.{0,10}(?:per\s+\w+|/yr|/year|/hr|/hour)",
        t
    )
    if single_match:
        return single_match.group(0).strip()

    return None



#extract sponsorship information.
def extract_sponsorship(text): 
    """
    Returns one of:
      - "Yes" for visa sponsorship
      - "Yes (OPT/CPT/H1B)"
      - "No" does not sponsor visa
      - "Unknown"
    """
    if not text:
        return "Unknown"

    t = text.lower()

    # Check for yes that is no sponsorship provided
    for pat in SPONSORSHIP_NEGATIVE_PATTERNS:
        if re.search(pat, t):
            return "No"

    # Check for yes positive that is sponsorship provided
    found = []
    if re.search(r"\bopt\b", t):
        found.append("OPT")
    if re.search(r"\bcpt\b", t):
        found.append("CPT")
    if re.search(r"h[\s-]?1b", t):
        found.append("H1B")

    for pat in SPONSORSHIP_POSITIVE_PATTERNS:
        if re.search(pat, t):
            # even if no specific visa mentioned, we know sponsorship exists
            if found:
                return "Yes (" + "/".join(found) + ")"
            return "Yes"
    #Nothing conclusive
    return "Unknown"


def build_job_final_record(raw_job, description, h1b_index=None):
    title = raw_job.get("title")
    company = raw_job.get("company")
    location = raw_job.get("location")
    url = raw_job.get("url")
    source = raw_job.get("source", "linkedin_search")

    # hard reject: internships are never wanted, regardless of how the
    # experience text reads (some intern postings say "0-1 years" too)
    if is_internship_title(title):
        return None

    min_years, max_years = check_experience(description)

    experience_ok = is_entry_level_experience(min_years, max_years)
    title_ok = search_new_grad_hints_title(title)

    # ACCEPT if either experience OR title says entry-level or similar
    if not (experience_ok or title_ok):
        return None

    # label experience for output (ONLY ONCE)
    if experience_ok:
        if min_years is not None and max_years is not None:
            exp_label = f"{min_years}-{max_years}"
        elif min_years is not None:
            exp_label = f"{min_years}+"
        elif max_years is not None:
            exp_label = f"0-{max_years}"
        else:
            exp_label = "unclear"
    else:
        exp_label = "new grad (title)"

    salary = extract_salary(description)
    # Text-based guess from the job posting itself -- kept for reference,
    # but h1b_match below is the real signal (actual USCIS filing history).
    sponsorship_text_signal = extract_sponsorship(description)
    h1b_result = h1b_sponsorship.check_h1b_sponsor(company, h1b_index or {})
    date_scraped = datetime.now(timezone.utc).date().isoformat()

    return {
        "title": title,
        "company": company,
        "location": location,
        "job_description": description,
        "experience": exp_label,
        "salary": salary,
        "sponsorship_text_signal": sponsorship_text_signal,
        "h1b_match": h1b_result["h1b_match"],
        "h1b_matched_employer": h1b_result["h1b_matched_employer"],
        "h1b_total_approvals": h1b_result["h1b_total_approvals"],
        "h1b_years": h1b_result["h1b_years"],
        "url": url,
        "date_scraped": date_scraped,
        "source": source,
    }


#scroll - extract and save the jobs
#each card only contain following:
#title
#company
#location
#posted time
#job url
#this means we will have to check for job description on its own
#we will use the url to load browser and open the job description
def extract_jobs_from_current_page(page, seen_urls):
    """
    Extract job cards from the current search results page.
    Returns a list of raw jobs:
      {title, company, location, url, posted_time}
    """

    #jobs from that page
    jobs = []

    #load the jobs cards from the linkedin page
    try:
        page.wait_for_selector("div.job-card-container.job-card-list", timeout=15000)
    except PlaywrightTimeoutError:
        print("No job cards found on this page due to timeout. Skipping.")
        return jobs

    # Scroll until job count stops increasing
    previous_count = -1
    while True:
        job_cards = page.locator("div.job-card-container.job-card-list")
        count = job_cards.count()

        if count == previous_count:
            break

        previous_count = count
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(1000)

    job_cards = page.locator("div.job-card-container.job-card-list")
    #print(f"Found job cards on this page", count)


    #loop through each job card and extract the fields
    for i in range(job_cards.count()):
        card = job_cards.nth(i)

        #extract posted time
        posted_time = None
        if card.locator("time").count():
            posted_time = card.locator("time").inner_text().strip()

        # Title
        title = None
        if card.locator("a.job-card-container__link").count():
            raw_title = card.locator("a.job-card-container__link").inner_text().strip()
            title = raw_title.split("\n")[0].strip()

        # Company
        company = None
        if card.locator("div.artdeco-entity-lockup__subtitle span").count():
            company = card.locator("div.artdeco-entity-lockup__subtitle span").inner_text().strip()

        # Location
        location = None
        metadata_li = card.locator("ul.job-card-container__metadata-wrapper li").first
        if metadata_li.count():
            location = metadata_li.inner_text().strip()

        # build the URL
        url = None
        if card.locator("a.job-card-container__link").count():
            url = card.locator("a.job-card-container__link").first.get_attribute("href")
            if url and url.startswith("/"):
                url = "https://www.linkedin.com" + url

        #skip jobs we have already seen
        if not url or url in seen_urls:
            continue
        
        #add to seen to avoid duplicates
        seen_urls.add(url)

        jobs.append({
            "title": title,
            "company": company,
            "location": location,
            "url": url,
            "posted_time": posted_time, 
            "source": "linkedin",
        })
    return jobs


#pagination function to go through pages and extract job cards from each page
#stops when we have reached the limit. 
def scrape_linkedin_jobs_paginated(
    max_pages = DEFAULT_MAX_PAGES,
    max_jobs = DEFAULT_MAX_JOBS,
):
    """
    Visit multiple LinkedIn job search result pages and collect
    basic job info (title, company, location, URL).

    Stops when either max_pages or max_jobs is reached.
    """

    print("Starting LinkedIn scraper")
    print(f"Goal: up to {max_jobs} from {max_pages} pages")

    all_jobs = []
    seen_urls = set() #set allows deduplication while scraping


    #open playwright browser
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state="linkedin_state.json") #saved cookie when we first signed in
       
        page = context.new_page()

        for page_num in range(max_pages):
            #stop if we have reached max number of jobs
            #we are doing this avoid bot detection from linkedin
            if len(all_jobs) >= max_jobs:
                print("Reached job limit")  
                break
            
            #linkedin pagination uses "start" parameter
            start = page_num * PAGE_SIZE
            page_url = f"{BASE_SEARCH_URL}&start={start}"

            print(f"\nVisiting page {page_num + 1}: {page_url}")
            
            page.goto(page_url, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

           
           #extract jobs from the current page
            page_jobs = extract_jobs_from_current_page(page, seen_urls)
            print(f"Found {len(page_jobs)} new jobs")

            #add them to global all jobs
            all_jobs.extend(page_jobs)
            print(f"Total jobs collected: {len(all_jobs)}")

             # Sleep to avoid being flagged as a bot
            sleep_time = random.uniform(6, 12)
            time.sleep(sleep_time)

        browser.close()
    print(f"Finished scraping. Total unique jobs: {len(all_jobs)}")
    return all_jobs


#extract job description
#separately extracting description its own because description are not available on results page. 
#loading the  job description from the job url saved earlier
def fetch_job_description(page, job_url):
    """
    Opens the job URL and returns plain text job description, if available.
    """
    try:
        # Open the job posting page
        page.goto(job_url, wait_until="domcontentloaded")

        # Give LinkedIn time to load dynamic content
        page.wait_for_timeout(3000)

        # Locate the main job description section
        description_section  = page.locator("div.show-more-less-html__markup")

        # If the description exists, return its text
        if description_section.count() > 0:
            return description_section.inner_text().strip()

    except Exception as error:
        print(f"Failed to fetch description for {job_url}: {error}")
    return None


#from all the scrapped jobs
# we now want to filter and take the jobs we want. 
#this is our final job list with all filters applied. 
def build_final_records_from_raw_jobs(raw_jobs):
    final_jobs = []
    if not raw_jobs:
        return final_jobs

    h1b_index = h1b_sponsorship.load_h1b_index()
    if not h1b_index:
        print(
            "No USCIS H-1B CSVs found in data/h1b/ -- h1b_match will be 'No Data' "
            "for every job. See h1b_sponsorship.py for the download link."
        )

    #open the browser
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state="linkedin_state.json")
        page = context.new_page()

        for idx, raw in enumerate(raw_jobs, start=1):
            url = raw.get("url") #url of the job

            #open the page and fetch the description
            description = fetch_job_description(page, url)

            #combine all fields
            final_record = build_job_final_record(raw, description, h1b_index)

            if final_record is not None:
                print('Accepted the job, has sponsorship and new grad') #passed the filtering
                final_jobs.append(final_record)
            else:
                print("Job rejected") #does not sponsor or not an entry level job

            #avoid bot detection
            time.sleep(random.uniform(2.5, 6.0))

            # longer human-like break every N jobs (real account, be conservative)
            if idx % DESCRIPTION_BATCH_SIZE == 0 and idx < len(raw_jobs):
                pause = random.uniform(*DESCRIPTION_BATCH_PAUSE_RANGE)
                print(f"Pausing {pause:.0f}s after {idx} job pages...")
                time.sleep(pause)
        browser.close()
    return final_jobs

if __name__ == "__main__":
    #Scrap raw job listing from linkedin
    raw_jobs = scrape_linkedin_jobs_paginated()

    #visit each job page and build final records
    final_jobs = build_final_records_from_raw_jobs(raw_jobs)

    #test by printing first 20 jobs
    #from here we will add to the database. 
    for j in final_jobs[:20]:
        print("Title:", j["title"])
        print("Company:", j["company"])
        print("Location:", j["location"])
        print("Experience:", j["experience"])
        print("Salary:", j["salary"])
        print("Sponsorship (text signal):", j["sponsorship_text_signal"])
        print("H1B Match (USCIS data):", j["h1b_match"], "-", j["h1b_matched_employer"])
        print("H1B Total Approvals:", j["h1b_total_approvals"], "Years:", j["h1b_years"])
        print("URL:", j["url"])
        print("Date Scraped:", j["date_scraped"])
        print("Description (first 300 chars):", (j["job_description"] or "")[:300])