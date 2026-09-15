"""
Checks whether a company has a real history of H-1B petitions, using the
USCIS H-1B Employer Data Hub (not text-guessing from a job description).

Get CSVs from either USCIS export path and drop them in data/h1b/ -- both are
auto-detected (encoding, delimiter, and column names all vary between them):

1. Archive files (one file per fiscal year, simple schema):
   https://www.uscis.gov/archive/h-1b-employer-data-hub-files
   e.g. https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-2023.csv
   UTF-8, comma-delimited. Columns: "Fiscal Year",Employer,"Initial Approval",
   "Initial Denial","Continuing Approval","Continuing Denial",NAICS,"Tax ID",State,City,ZIP

2. Live interactive tool ("Crosstab View" -> "Download to Excel" -> CSV):
   https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub
   UTF-16, TAB-delimited. Columns include "Employer (Petitioner) Name" and six
   separate approval/denial category pairs (New Employment, Continuation,
   Change with Same Employer, New Concurrent, Change of Employer, Amended).
   Note: USCIS blanks out the employer name on rows with very small counts
   (privacy suppression) -- those rows are skipped since there's nothing to
   match on.

Either way: more recent years are more predictive of current sponsorship
practice; a few recent fiscal years is usually enough signal.
"""

import csv
import difflib
import glob
import os
from collections import defaultdict

DEFAULT_H1B_DIR = os.path.join("data", "h1b")

# Common legal-entity suffixes that make exact string matching fail
# (e.g. "Google" in a job post vs "GOOGLE LLC" in the USCIS data).
_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company",
    "llc", "llp", "lp", "ltd", "limited", "plc", "pllc", "pc", "the",
}


def normalize_company_name(name):
    if not name:
        return ""
    n = name.lower()
    n = n.replace("&", " and ")
    n = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in n)
    tokens = [t for t in n.split() if t not in _SUFFIXES]
    return " ".join(tokens).strip()


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _detect_encoding(path):
    with open(path, "rb") as f:
        head = f.read(4)
    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        return "utf-16"  # auto-picks LE/BE from the BOM
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    return "utf-8"


def _detect_delimiter(header_line):
    return "\t" if header_line.count("\t") > header_line.count(",") else ","


def _load_one_csv(path, index):
    encoding = _detect_encoding(path)

    with open(path, newline="", encoding=encoding) as f:
        first_line = f.readline()
        if not first_line:
            return
        delimiter = _detect_delimiter(first_line)
        f.seek(0)

        reader = csv.DictReader(f, delimiter=delimiter)
        if not reader.fieldnames:
            print(f"Skipping {path}: empty/unreadable header")
            return

        # header names vary (trailing spaces, different wording) -- match by
        # stripped/lowercased substring instead of an exact expected name
        raw_by_stripped = {(name or "").strip(): name for name in reader.fieldnames}
        stripped_names = list(raw_by_stripped.keys())

        employer_field = next(
            (n for n in stripped_names if n.lower().startswith("employer")), None
        )
        fiscal_year_field = next(
            (n for n in stripped_names if n.lower().startswith("fiscal year")), None
        )
        approval_fields = [n for n in stripped_names if "approval" in n.lower()]
        denial_fields = [n for n in stripped_names if "denial" in n.lower()]

        if not employer_field:
            print(f"Skipping {path}: no Employer-like column found in header {stripped_names}")
            return

        employer_key = raw_by_stripped[employer_field]
        fiscal_year_key = raw_by_stripped[fiscal_year_field] if fiscal_year_field else None
        approval_keys = [raw_by_stripped[n] for n in approval_fields]
        denial_keys = [raw_by_stripped[n] for n in denial_fields]

        rows_read = 0
        rows_matched = 0
        for row in reader:
            rows_read += 1
            employer = (row.get(employer_key) or "").strip()
            if not employer:
                continue  # suppressed/blank employer name row -- nothing to match on

            key = normalize_company_name(employer)
            if not key:
                continue

            rec = index[key]
            if rec["employer_name"] is None:
                rec["employer_name"] = employer
            rec["total_approval"] += sum(_to_int(row.get(k)) for k in approval_keys)
            rec["total_denial"] += sum(_to_int(row.get(k)) for k in denial_keys)

            if fiscal_year_key:
                fy = (row.get(fiscal_year_key) or "").strip()
                if fy:
                    rec["fiscal_years"].add(fy)
            rows_matched += 1

        print(f"Loaded {path}: {rows_matched}/{rows_read} rows with an employer name")


def load_h1b_index(data_dir=DEFAULT_H1B_DIR):
    """
    Reads every *.csv in data_dir (USCIS H-1B Employer Data Hub exports,
    either the archive format or the live tool's export format) and
    aggregates approval/denial counts per normalized employer name across
    every fiscal year found.

    Returns a dict: normalized_name -> {
        "employer_name": original employer string (first seen),
        "total_approval": int, "total_denial": int,
        "fiscal_years": set(str),
    }
    Returns an empty dict if no CSVs are present yet.
    """
    index = defaultdict(lambda: {
        "employer_name": None,
        "total_approval": 0,
        "total_denial": 0,
        "fiscal_years": set(),
    })

    csv_paths = sorted(glob.glob(os.path.join(data_dir, "*.csv")))
    if not csv_paths:
        return index

    for path in csv_paths:
        _load_one_csv(path, index)

    return index


def _find_prefix_matches(key, h1b_index):
    """
    Catches the common case where the job posting uses a brand name but
    USCIS filings are under a subsidiary/legal entity name that adds words
    (e.g. "Amazon" job posting -> "AMAZON WEB SERVICES INC", "AMAZON COM
    SERVICES LLC" in the data), or vice versa (e.g. "Google Cloud" posting
    -> "GOOGLE LLC" in the data).
    """
    matches = []
    for k in h1b_index:
        if k.startswith(key + " ") or key.startswith(k + " "):
            matches.append(k)
    return matches


def _combine_records(records):
    names = sorted({r["employer_name"] for r in records if r["employer_name"]})
    display_name = ", ".join(names[:3])
    if len(names) > 3:
        display_name += f" (+{len(names) - 3} more)"

    return {
        "employer_name": display_name,
        "total_approval": sum(r["total_approval"] for r in records),
        "total_denial": sum(r["total_denial"] for r in records),
        "fiscal_years": set().union(*(r["fiscal_years"] for r in records)),
    }


def check_h1b_sponsor(company_name, h1b_index):
    """
    Looks up `company_name` (as scraped from a job posting) against the
    loaded USCIS index.

    Returns:
      {
        "h1b_match": "Yes" | "Filed, 0 Net Approvals" | "No Match Found" | "No Data",
        "h1b_matched_employer": str or None,   # the USCIS legal entity name matched
        "h1b_total_approvals": int,            # summed across all approval categories
        "h1b_years": "2021, 2022, 2023",       # fiscal years with data, if matched
      }

    "No Data" means no USCIS CSVs have been loaded at all (download them first).
    "No Match Found" means CSVs are loaded but this company never appears.
    A miss doesn't prove a company won't sponsor -- it just never filed an H-1B
    (or files under a different legal name, e.g. a staffing subsidiary).
    """
    result = {
        "h1b_match": "No Data",
        "h1b_matched_employer": None,
        "h1b_total_approvals": 0,
        "h1b_years": "",
    }

    if not h1b_index:
        return result

    key = normalize_company_name(company_name)
    if not key:
        return result

    rec = h1b_index.get(key)

    if rec is None:
        # Brand name vs. legal filing entity (e.g. "Amazon" -> 13 different
        # "AMAZON ... INC/LLC" subsidiaries) -- combine all matches so the
        # approval count reflects the whole company, not just one entity.
        # Capped generously (real conglomerates can have a dozen+ filing
        # entities); an overly generic key blowing past this is treated as
        # too ambiguous to aggregate rather than silently merging unrelated
        # companies that happen to share a common first word.
        prefix_matches = _find_prefix_matches(key, h1b_index)
        if 0 < len(prefix_matches) <= 25:
            rec = _combine_records([h1b_index[k] for k in prefix_matches])

    if rec is None and len(key) >= 6:
        # Fuzzy fallback for near-miss legal names (e.g. abbreviations,
        # punctuation differences) -- only for the exact key missing.
        # Skipped for short keys: on short strings a single extra
        # character (e.g. "tech" vs "vtech") can cross the similarity
        # cutoff and produce an unrelated match.
        close = difflib.get_close_matches(key, h1b_index.keys(), n=1, cutoff=0.87)
        if close:
            rec = h1b_index[close[0]]

    if rec is None:
        result["h1b_match"] = "No Match Found"
        return result

    total_approvals = rec["total_approval"]
    result["h1b_matched_employer"] = rec["employer_name"]
    result["h1b_total_approvals"] = total_approvals
    result["h1b_years"] = ", ".join(sorted(rec["fiscal_years"]))
    result["h1b_match"] = "Yes" if total_approvals > 0 else "Filed, 0 Net Approvals"
    return result
