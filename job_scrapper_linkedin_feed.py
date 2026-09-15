"""
Scrapes your LinkedIn feed for posts from your network that mention hiring /
open roles -- the case where a connection posts "my team is hiring a new grad
SWE, DM me" rather than a structured LinkedIn Jobs listing.

NOTE ON SELECTORS: LinkedIn's feed DOM classes change often and differ by
account/rollout. The selectors below are a best-effort starting point and
were NOT verified against a live logged-in session (this environment has no
browser). Expect to adjust FEED_POST_SELECTOR / FEED_TEXT_SELECTORS /
FEED_AUTHOR_SELECTORS after your first real run -- open the feed, right-click
a post, "Inspect", and compare class names.

Unlike job_scrapper_linkedin.py, feed posts are unstructured text, not a
structured job listing, so:
  - "company" is best-effort (regex "at <Company>" in the post text, else
    falls back to the post author's name so you can still follow up).
  - there's rarely a job URL -- job_url is only set if the post itself links
    out (e.g. to an ATS). Otherwise url is the LinkedIn post's own URL, so
    you can click through and DM/comment.
"""

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from datetime import datetime, timezone
import re
import time
import random

import h1b_sponsorship
from job_scrapper_linkedin import (
    check_experience,
    is_entry_level_experience,
    extract_sponsorship,
    is_internship_title,
)

FEED_URL = "https://www.linkedin.com/feed/"

FEED_POST_SELECTOR = "div.feed-shared-update-v2"
FEED_TEXT_SELECTORS = [
    "div.update-components-text",
    "div.feed-shared-update-v2__description",
    "span.break-words",
]
FEED_AUTHOR_SELECTORS = [
    "span.update-components-actor__name",
    "span.feed-shared-actor__name",
]
FEED_POST_LINK_SELECTOR = "a.app-aware-link"

DEFAULT_MAX_SCROLLS = 12
DEFAULT_MAX_POSTS = 60

# Posts have to look like an actual hiring mention, not just "congrats on the
# new job" -- require a hiring verb/phrase near a role-ish keyword.
HIRING_PHRASES = [
    "we're hiring", "we are hiring", "is hiring", "now hiring",
    "open role", "open roles", "open position", "open positions",
    "looking for a new grad", "looking to hire", "join our team",
    "referral", "apply here", "dm me if interested", "reach out if interested",
]

ROLE_KEYWORDS = [
    "software engineer", "swe", "sde", "new grad", "entry level",
    "entry-level", "junior developer", "junior engineer", "developer",
    "engineer",
]

COMPANY_FROM_TEXT_PATTERN = re.compile(
    r"\bat\s+([A-Z][A-Za-z0-9&.,'\-]*(?:\s+[A-Z][A-Za-z0-9&.,'\-]*){0,3})"
)


def looks_like_hiring_post(text):
    if not text:
        return False
    t = text.lower()
    has_hiring_phrase = any(phrase in t for phrase in HIRING_PHRASES)
    has_role_keyword = any(kw in t for kw in ROLE_KEYWORDS)
    return has_hiring_phrase and has_role_keyword


def guess_company_from_text(text):
    if not text:
        return None
    match = COMPANY_FROM_TEXT_PATTERN.search(text)
    if match:
        return match.group(1).strip().rstrip(".,")
    return None


def _first_matching_text(card, selectors):
    for sel in selectors:
        loc = card.locator(sel)
        if loc.count():
            try:
                return loc.first.inner_text().strip()
            except Exception:
                continue
    return None


def extract_feed_posts(page, seen_post_ids):
    """
    Extracts hiring-looking posts from whatever is currently rendered in the
    feed. Returns a list of raw post dicts.
    """
    posts = []

    try:
        page.wait_for_selector(FEED_POST_SELECTOR, timeout=15000)
    except PlaywrightTimeoutError:
        print("No feed posts found (timeout). LinkedIn feed selectors may need updating.")
        return posts

    cards = page.locator(FEED_POST_SELECTOR)

    for i in range(cards.count()):
        card = cards.nth(i)

        text = _first_matching_text(card, FEED_TEXT_SELECTORS)
        if not looks_like_hiring_post(text):
            continue

        author = _first_matching_text(card, FEED_AUTHOR_SELECTORS)

        post_url = None
        if card.locator(FEED_POST_LINK_SELECTOR).count():
            href = card.locator(FEED_POST_LINK_SELECTOR).first.get_attribute("href")
            if href:
                post_url = href if href.startswith("http") else f"https://www.linkedin.com{href}"

        post_id = post_url or text[:120]
        if post_id in seen_post_ids:
            continue
        seen_post_ids.add(post_id)

        posts.append({
            "title": None,  # unstructured -- see job_description for the actual text
            "company": guess_company_from_text(text) or author,
            "posted_by": author,
            "location": None,
            "url": post_url,
            "job_description": text,
            "source": "linkedin_feed",
        })

    return posts


def scrape_linkedin_feed(max_scrolls=DEFAULT_MAX_SCROLLS, max_posts=DEFAULT_MAX_POSTS):
    print("Starting LinkedIn feed scraper")
    print(f"Goal: up to {max_posts} hiring-looking posts across {max_scrolls} scrolls")

    all_posts = []
    seen_post_ids = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state="linkedin_state.json")
        page = context.new_page()

        page.goto(FEED_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        for scroll_num in range(max_scrolls):
            if len(all_posts) >= max_posts:
                print("Reached post limit")
                break

            new_posts = extract_feed_posts(page, seen_post_ids)
            if new_posts:
                print(f"Scroll {scroll_num + 1}: found {len(new_posts)} new hiring-looking posts")
            all_posts.extend(new_posts)

            page.mouse.wheel(0, 3500)
            # slower than the job-search scraper: this is your real feed,
            # be conservative
            time.sleep(random.uniform(5, 10))

        browser.close()

    print(f"Finished feed scrape. Total hiring-looking posts: {len(all_posts)}")
    return all_posts[:max_posts]


def build_final_records_from_feed_posts(raw_posts):
    """
    Applies the same experience/sponsorship/H1B checks used for structured
    job listings, but against unstructured post text.
    """
    final_posts = []
    if not raw_posts:
        return final_posts

    h1b_index = h1b_sponsorship.load_h1b_index()

    for raw in raw_posts:
        text = raw.get("job_description")

        # hard reject: no internships, same rule as the job-search scraper
        if is_internship_title(text):
            continue

        min_years, max_years = check_experience(text)
        experience_ok = is_entry_level_experience(min_years, max_years)

        # unlike structured listings, there's no separate title field to
        # fall back on -- role_keyword match already happened in
        # looks_like_hiring_post, so accept on experience OR just keep it
        # (feed posts are opt-in signal, not auto-filtered as hard as search)
        if experience_ok and min_years is not None and max_years is not None:
            exp_label = f"{min_years}-{max_years}"
        elif experience_ok and min_years is not None:
            exp_label = f"{min_years}+"
        elif experience_ok and max_years is not None:
            exp_label = f"0-{max_years}"
        else:
            exp_label = "unclear (feed post)"

        sponsorship_text_signal = extract_sponsorship(text)
        h1b_result = h1b_sponsorship.check_h1b_sponsor(raw.get("company"), h1b_index)

        final_posts.append({
            "title": raw.get("title") or "(see job_description -- feed post)",
            "company": raw.get("company"),
            "location": raw.get("location"),
            "job_description": text,
            "experience": exp_label,
            "salary": None,
            "sponsorship_text_signal": sponsorship_text_signal,
            "h1b_match": h1b_result["h1b_match"],
            "h1b_matched_employer": h1b_result["h1b_matched_employer"],
            "h1b_total_approvals": h1b_result["h1b_total_approvals"],
            "h1b_years": h1b_result["h1b_years"],
            "url": raw.get("url"),
            "date_scraped": datetime.now(timezone.utc).date().isoformat(),
            "source": raw.get("source", "linkedin_feed"),
        })

    return final_posts


if __name__ == "__main__":
    raw_posts = scrape_linkedin_feed()
    final_posts = build_final_records_from_feed_posts(raw_posts)

    for p in final_posts[:20]:
        print("Company/Author:", p["company"])
        print("H1B Match (USCIS data):", p["h1b_match"], "-", p["h1b_matched_employer"])
        print("URL:", p["url"])
        print("Text (first 300 chars):", (p["job_description"] or "")[:300])
        print("---")
