# How this project works (plain-English walkthrough)

## The problem you're solving

You want a list of new-grad software engineering jobs on LinkedIn, and for each one, you want to know if that company has actually sponsored H-1B visas before (not just guessed from the job ad text).

## How the project actually works, step by step

**Step 1 — Log in once, save the login (`login_linkedIn.py`)**
You open a real browser window, log into LinkedIn yourself. The script then saves your "logged in" cookies (the little tokens a website gives your browser after you log in) to a file called `linkedin_state.json`.
*Concept:* **cookies / sessions** — instead of typing your password every single time you scrape, the script re-uses the saved cookies, like keeping yourself logged in.

**Step 2 — Visit LinkedIn's job search pages automatically (`job_scrapper_linkedin.py`)**
A tool called **Playwright** drives a real Chrome browser like a robot — it opens pages, scrolls, clicks, reads text — using that saved login.
*Concept:* **browser automation** — controlling a browser with code instead of your hands.

It visits the job search results page 5 times (page 1, page 2, ... using a `start=25`, `start=50` trick in the URL), and for each page it reads the job cards (title, company, location).
*Concept:* **pagination** — grabbing content one "page" at a time instead of all at once.

It waits and scrolls slowly, and sleeps for a random amount of time between actions (6-12 seconds, not always exactly the same).
*Concept:* **rate limiting** — going slow and looking "human" on purpose, so LinkedIn doesn't flag your real account for scraping too fast.

**Step 3 — Open each job and read the full description**
For every job found, it opens that job's page and grabs the full text description.

**Step 4 — Decide: is this actually a new-grad job? (regex/keyword matching)**
It reads the title and description looking for patterns:
- Does the title say "new grad," "entry level," "junior"? (and reject "senior," "staff," "lead")
- Does the description mention "0-1 years" or "1+ years" experience?
*Concept:* **regex (pattern matching in text)** — searching text for specific word patterns instead of reading it like a human would.

**Step 5 — Check H-1B sponsorship for real (`h1b_sponsorship.py`)** — this is the meatier part

The US government (USCIS) publishes a giant spreadsheet of every company that has ever filed H-1B paperwork, and how many were approved/denied.

- You download that spreadsheet (a CSV file — basically a giant Excel sheet as plain text) into a folder.
- The script reads that file into memory and builds something like a phone book: "company name" → "how many H-1Bs they got approved."
*Concept:* **hash map / dictionary lookup** — instead of re-reading the whole spreadsheet every time you check a company (slow), it builds one lookup table once, so checking any company afterward is instant.

- The tricky part: the job posting might say "Amazon," but the government spreadsheet has "AMAZON WEB SERVICES INC," "AMAZON COM SERVICES LLC," etc. — 13 different legal names for the same company. So the script:
  1. First tries an exact match.
  2. If that fails, tries "does one name start with the other" (catches Amazon → Amazon Web Services).
  3. If that fails, tries "close enough spelling" matching as a last resort.
*Concept:* **fuzzy matching / entity resolution** — matching two things that refer to the same real-world thing but are written slightly differently. This is a genuinely common real-world data problem (matching customer records, matching product names, etc).

**Step 6 — Also check your LinkedIn feed (`job_scrapper_linkedin_feed.py`)**
Same idea, but instead of the structured job search page, it scrolls your personal feed looking for posts from your connections that sound like hiring announcements (keywords like "we're hiring," "open role"). This part hasn't been tested live yet.

**Step 7 — Save everything to a spreadsheet (`run_scrappers.py`)**
It combines both sources, removes duplicate jobs (same URL), and writes one CSV file you can open in Excel/Sheets.

## The short list of concepts, plainly

| Concept | In plain words |
|---|---|
| Cookies/sessions | Staying "logged in" without re-typing your password every run |
| Browser automation | Code that clicks/scrolls/reads a real browser for you |
| Pagination | Getting results one page at a time |
| Rate limiting | Going slow on purpose so you don't get flagged as a bot |
| Regex | Finding word patterns in text ("0-1 years", "senior", etc.) |
| CSV parsing | Reading a giant spreadsheet file into your program |
| Hash map / dictionary | A fast lookup table (company name → sponsorship data) instead of re-scanning everything each time |
| Fuzzy matching | Matching "Amazon" to "AMAZON WEB SERVICES INC" — same company, different spelling |
| Deduplication | Removing repeat entries (same job showing up twice) |

See also `docs/currentstatus.md` for what's currently working/verified, and the published interview-notes artifact for interview-specific talking points and STAR stories from this project.
