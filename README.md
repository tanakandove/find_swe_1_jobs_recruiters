# find_swe_1_jobs_recruiters

Finds new-grad SWE job postings on LinkedIn, verifies each company's real
H-1B sponsorship history against USCIS data, and surfaces recruiting-focused
hackathons/meetups (Fri-Sun) worth traveling to.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

**H-1B data** (required for sponsorship checks): download 1-2 recent fiscal
years of CSVs from the [USCIS H-1B Employer Data Hub](https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub)
into `data/h1b/`. Both known export formats are auto-detected — see
`h1b_sponsorship.py` for details.

## Usage

```bash
python3 login_linkedIn.py    # one-time: opens a browser, log in manually,
                              # saves session cookies to linkedin_state.json
python3 run_scrappers.py     # scrapes jobs + events, writes CSVs to output/
```

Outputs `output/jobs_<timestamp>.csv` and `output/events_<timestamp>.csv`.

## Files

| File | What it does |
|---|---|
| `login_linkedIn.py` | Interactive login, saves session cookies |
| `job_scrapper_linkedin.py` | Scrapes LinkedIn Jobs search results |
| `job_scrapper_linkedin_feed.py` | Scrapes your feed for hiring posts (untested against live LinkedIn) |
| `h1b_sponsorship.py` | Matches companies against USCIS H-1B filing records |
| `events_scraper.py` | Finds hackathons (Devpost) and tech meetups (Luma) on weekends |
| `run_scrappers.py` | Entry point — runs everything, writes CSVs |

See `docs/currentstatus.md` for current status, known limitations, and
`docs/how_it_works_explained.md` for a plain-English walkthrough.
