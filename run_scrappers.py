"""
Entry point: runs the LinkedIn job-search scraper and the LinkedIn feed
scraper (merged/deduped into one jobs CSV), plus the hackathon/meetup events
scraper (its own CSV, different shape of data -- dates/locations, not job
postings). One command, two output files in output/.

Previously this imported a Django backend (backend.settings, jobs.models)
and a ZipRecruiter scraper that no longer exist in this project -- replaced
with a plain CSV pipeline so this runs standalone.
"""

import csv
import os
from datetime import datetime, timezone

import job_scrapper_linkedin
import job_scrapper_linkedin_feed
import events_scraper

OUTPUT_DIR = "output"

OUTPUT_FIELDS = [
    "source",
    "title",
    "company",
    "location",
    "experience",
    "salary",
    "sponsorship_text_signal",
    "h1b_match",
    "h1b_matched_employer",
    "h1b_total_approvals",
    "h1b_years",
    "url",
    "date_scraped",
    "job_description",
]


def dedupe_by_url(jobs):
    seen_urls = set()
    deduped = []
    for job in jobs:
        url = job.get("url")
        # feed posts sometimes have no url -- keep those, dedupe on the
        # description text instead so we don't drop them all
        key = url or job.get("job_description")
        if not key or key in seen_urls:
            continue
        seen_urls.add(key)
        deduped.append(job)
    return deduped


def write_jobs_csv(jobs, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for job in jobs:
            writer.writerow(job)


def run_both_scrapers():
    print("Running LinkedIn job-search scraper")
    raw_search_jobs = job_scrapper_linkedin.scrape_linkedin_jobs_paginated()
    search_jobs = job_scrapper_linkedin.build_final_records_from_raw_jobs(raw_search_jobs)
    print(f"Job-search scraper: {len(search_jobs)} matching jobs")

    print("\nRunning LinkedIn feed scraper")
    raw_feed_posts = job_scrapper_linkedin_feed.scrape_linkedin_feed()
    feed_jobs = job_scrapper_linkedin_feed.build_final_records_from_feed_posts(raw_feed_posts)
    print(f"Feed scraper: {len(feed_jobs)} hiring-looking posts")

    all_jobs = dedupe_by_url(search_jobs + feed_jobs)
    print(f"\nTotal after dedupe: {len(all_jobs)}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    jobs_out_path = os.path.join(OUTPUT_DIR, f"jobs_{timestamp}.csv")
    write_jobs_csv(all_jobs, jobs_out_path)
    print(f"Wrote {len(all_jobs)} rows to {jobs_out_path}")

    print("\nFinding hackathons/meetups (Fri-Sun, worth traveling to)")
    events = events_scraper.run_events_scraper()
    events_out_path = os.path.join(OUTPUT_DIR, f"events_{timestamp}.csv")
    events_scraper.write_events_csv(events, events_out_path)
    print(f"Wrote {len(events)} rows to {events_out_path}")


if __name__ == "__main__":
    run_both_scrapers()
