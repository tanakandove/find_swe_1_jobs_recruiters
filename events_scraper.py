"""
Finds hackathons and tech meetups happening on a Friday or weekend that are
worth traveling to for recruiting exposure (sponsor booths, "meet the team"
sessions, interview fast-tracks for hackathon winners, etc).

Two sources, both fetched over plain HTTP (no login, no Playwright needed --
both expose structured JSON directly):

1. Devpost (https://devpost.com/hackathons) -- has a public JSON API behind
   its hackathon browse page. Used for in-person hackathons nationwide.
   Confirmed working: GET https://devpost.com/api/hackathons?status[]=upcoming
   &challenge_type[]=in-person&page=N returns {"hackathons": [...], "meta": {...}}.

2. Luma (https://lu.ma/<city>/tech) -- each city's "tech" category discover
   page is a Next.js app that embeds the event list as JSON in a
   <script id="__NEXT_DATA__"> tag, readable straight from the HTML. Used
   for local tech meetups/mixers (the kind that often have recruiters or
   hiring managers actually there). The plain city page (no /tech) also
   works but returns the whole city's calendar -- yoga classes, art
   openings, etc. -- so /tech is used specifically to pre-filter that out.

NOT included: MLH (mlh.io/mlh.com). Its event listing page is fully
client-side rendered with no public JSON API found -- would need real
browser automation (Playwright) for an untested payoff, and most
MLH-affiliated hackathons already cross-list on Devpost anyway, so the
marginal coverage isn't worth the fragility. Revisit if Devpost coverage
turns out to be thin.
"""

import csv
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

OUTPUT_DIR = "output"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

WEEKEND_WEEKDAYS = {4, 5, 6}  # Fri, Sat, Sun (Python: Mon=0 ... Sun=6)

# Luma city "discover" pages -- edit this list to add/drop cities. Willing-
# to-travel is nationwide per the user, so this leans toward major tech
# hiring hubs rather than one home city.
LUMA_CITIES = ["nyc", "sf", "la", "seattle", "austin", "boston", "chicago", "dc", "atlanta"]

# Tried a "/tech" category suffix (e.g. lu.ma/seattle/tech) first, expecting
# server-side filtering -- checked the raw JSON and it's byte-identical to
# the plain city page for every city tested ("kind": "discover-place" both
# times, same event list). lu.ma/tech with no city IS a real category page
# ("kind": "category"), but it's a curated calendar-list view with no flat
# per-city event array, so it doesn't compose with the city list below.
# Real fix: keyword-filter event names client-side instead.
TECH_TOPIC_KEYWORDS = [
    "tech", "startup", "founder", "engineer", "developer", "coding", "code",
    "software", "hackathon", "hack night", "build night", "builder",
    " ai ", "ai/", "/ai", "ai)", "(ai", "machine learning", "data science",
    "product manager", " vc ", "venture", "demo day", "pitch night",
    "web3", "crypto", "saas", "devops", "cloud", "open source",
    "recruit", "hiring", "career", "talent", "networking",
]

RECRUITING_KEYWORDS = [
    "recruit", "hiring", "hire", "career", "talent",
    "meet the team", "info session", "resume", "job fair", "interview",
    "networking", "demo day", "showcase", "founders", "happy hour",
]


def looks_tech_focused(text):
    if not text:
        return False
    t = f" {text.lower()} "
    return any(k in t for k in TECH_TOPIC_KEYWORDS)


def _http_get(url, headers=None):
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"Request failed for {url}: {e}")
        return None


def looks_recruiting_focused(text):
    if not text:
        return False
    t = text.lower()
    return any(k in t for k in RECRUITING_KEYWORDS)


# ---------------------------------------------------------------------------
# Devpost
# ---------------------------------------------------------------------------

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_RANGE_CROSS_MONTH = re.compile(
    r"^([A-Za-z]{3})[a-z]*\s+(\d{1,2})\s*-\s*([A-Za-z]{3})[a-z]*\s+(\d{1,2}),\s*(\d{4})$"
)
_RANGE_SAME_MONTH = re.compile(
    r"^([A-Za-z]{3})[a-z]*\s+(\d{1,2})\s*-\s*(\d{1,2}),\s*(\d{4})$"
)
_SINGLE_DATE = re.compile(r"^([A-Za-z]{3})[a-z]*\s+(\d{1,2}),\s*(\d{4})$")


def parse_devpost_date_range(text):
    """
    Devpost's submission_period_dates is free text like "Oct 03 - 04, 2026",
    "Jan 25 - Feb 11, 2027", or "Sep 18 - 19, 2026". Returns (start_date,
    end_date) as date objects, or (None, None) if the format doesn't match
    (e.g. odd multi-stage listings like "Grand Finals 2027").
    """
    if not text:
        return (None, None)
    t = text.strip()

    m = _RANGE_CROSS_MONTH.match(t)
    if m:
        mon1, d1, mon2, d2, year = m.groups()
        mon1_num = _MONTHS.get(mon1.lower())
        mon2_num = _MONTHS.get(mon2.lower())
        if mon1_num and mon2_num:
            try:
                return (date(int(year), mon1_num, int(d1)), date(int(year), mon2_num, int(d2)))
            except ValueError:
                return (None, None)
        return (None, None)

    m = _RANGE_SAME_MONTH.match(t)
    if m:
        mon, d1, d2, year = m.groups()
        mon_num = _MONTHS.get(mon.lower())
        if mon_num:
            try:
                return (date(int(year), mon_num, int(d1)), date(int(year), mon_num, int(d2)))
            except ValueError:
                return (None, None)
        return (None, None)

    m = _SINGLE_DATE.match(t)
    if m:
        mon, d, year = m.groups()
        mon_num = _MONTHS.get(mon.lower())
        if mon_num:
            try:
                d_ = date(int(year), mon_num, int(d))
                return (d_, d_)
            except ValueError:
                return (None, None)

    return (None, None)


def date_range_touches_weekend(start, end):
    if not start or not end:
        return False
    d = start
    while d <= end:
        if d.weekday() in WEEKEND_WEEKDAYS:
            return True
        d += timedelta(days=1)
    return False


def fetch_devpost_hackathons(max_pages=5, pause_seconds=1.0):
    """
    Pulls upcoming in-person hackathons from Devpost's public JSON API,
    filtered down to ones with a Friday/Saturday/Sunday somewhere in their
    date range.
    """
    print("Fetching Devpost hackathons...")
    results = []

    for page in range(1, max_pages + 1):
        url = (
            "https://devpost.com/api/hackathons"
            f"?status[]=upcoming&challenge_type[]=in-person&sort_by=recently-added&page={page}"
        )
        body = _http_get(url)
        if not body:
            break

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            print(f"Devpost page {page}: couldn't parse JSON, stopping")
            break

        hackathons = data.get("hackathons", [])
        if not hackathons:
            break

        for h in hackathons:
            date_text = h.get("submission_period_dates")
            start, end = parse_devpost_date_range(date_text)
            if not date_range_touches_weekend(start, end):
                continue

            location = (h.get("displayed_location") or {}).get("location")
            title = h.get("title", "")
            org = h.get("organization_name", "")

            results.append({
                "source": "devpost",
                "name": title,
                "host_or_org": org,
                "start_date": start.isoformat() if start else "",
                "end_date": end.isoformat() if end else "",
                "date_text_raw": date_text,
                "location": location,
                "format": "in-person",
                "url": h.get("url"),
                "prize_or_focus": h.get("prize_amount", ""),
                "looks_recruiting_focused": looks_recruiting_focused(f"{title} {org}"),
                "date_scraped": datetime.now().date().isoformat(),
            })

        meta = data.get("meta", {})
        total = meta.get("total_count", 0)
        per_page = meta.get("per_page", len(hackathons)) or len(hackathons)
        if page * per_page >= total:
            break

        time.sleep(pause_seconds)

    print(f"Devpost: {len(results)} in-person hackathons with a Fri/Sat/Sun date")
    return results


# ---------------------------------------------------------------------------
# Luma
# ---------------------------------------------------------------------------

_NEXT_DATA_PATTERN = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S
)


def _parse_luma_local_datetime(iso_str, tz_name):
    if not iso_str:
        return None
    try:
        dt_utc = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return None
    if tz_name:
        try:
            return dt_utc.astimezone(ZoneInfo(tz_name))
        except Exception:
            return dt_utc
    return dt_utc


def fetch_luma_events_for_city(city_slug, pause_seconds=1.5):
    url = f"https://lu.ma/{city_slug}"
    body = _http_get(url)
    if not body:
        return []

    m = _NEXT_DATA_PATTERN.search(body)
    if not m:
        print(f"Luma ({city_slug}): couldn't find embedded event data -- page structure may have changed")
        return []

    try:
        payload = json.loads(m.group(1))
    except json.JSONDecodeError:
        print(f"Luma ({city_slug}): couldn't parse embedded JSON")
        return []

    try:
        raw_events = payload["props"]["pageProps"]["initialData"]["data"]["events"]
    except (KeyError, TypeError):
        print(f"Luma ({city_slug}): unexpected data shape, skipping")
        return []

    results = []
    for entry in raw_events:
        ev = entry.get("event", {})
        if ev.get("location_type") == "virtual":
            continue  # only want events worth traveling to

        tz_name = ev.get("timezone")
        start_local = _parse_luma_local_datetime(ev.get("start_at"), tz_name)
        if not start_local or start_local.weekday() not in WEEKEND_WEEKDAYS:
            continue

        name = ev.get("name", "")
        if not looks_tech_focused(name):
            continue  # city discover pages are the whole city's calendar,
            # not just tech -- filter here since Luma has no working
            # per-city category endpoint (see TECH_TOPIC_KEYWORDS comment)

        geo = ev.get("geo_address_info") or {}
        slug = ev.get("url")
        full_url = f"https://lu.ma/{slug}" if slug else None

        results.append({
            "source": "luma",
            "name": name,
            "host_or_org": "",
            "start_date": start_local.date().isoformat(),
            "end_date": start_local.date().isoformat(),
            "date_text_raw": start_local.strftime("%a %b %d, %Y %I:%M %p %Z"),
            "location": geo.get("city_state") or geo.get("city") or city_slug,
            "format": "in-person",
            "url": full_url,
            "prize_or_focus": "",
            "looks_recruiting_focused": looks_recruiting_focused(name),
            "date_scraped": datetime.now().date().isoformat(),
        })

    time.sleep(pause_seconds)
    return results


def fetch_luma_events(cities=None, pause_seconds=1.5):
    cities = cities or LUMA_CITIES
    print(f"Fetching Luma events for {len(cities)} cities...")
    all_results = []
    for city in cities:
        city_results = fetch_luma_events_for_city(city, pause_seconds=pause_seconds)
        print(f"  {city}: {len(city_results)} Fri/Sat/Sun in-person events")
        all_results.extend(city_results)
    print(f"Luma: {len(all_results)} total Fri/Sat/Sun in-person events")
    return all_results


# ---------------------------------------------------------------------------
# Combined pipeline
# ---------------------------------------------------------------------------

EVENT_OUTPUT_FIELDS = [
    "source", "name", "host_or_org", "start_date", "end_date", "date_text_raw",
    "location", "format", "url", "prize_or_focus", "looks_recruiting_focused",
    "date_scraped",
]


def run_events_scraper():
    devpost_events = fetch_devpost_hackathons()
    luma_events = fetch_luma_events()
    return devpost_events + luma_events


def write_events_csv(events, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=EVENT_OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for e in events:
            writer.writerow(e)


if __name__ == "__main__":
    events = run_events_scraper()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(OUTPUT_DIR, f"events_{timestamp}.csv")
    write_events_csv(events, out_path)
    print(f"Wrote {len(events)} events to {out_path}")
