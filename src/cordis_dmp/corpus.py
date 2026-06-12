"""Corpus construction: enrich DMP deliverables with project metadata.

Joins data/dmp_deliverables.csv with the CORDIS project dumps
(project.csv, organization.csv, euroSciVoc.csv, policyPriorities.csv) to add:

- domain labels (health / agriculture / climate) from two independent
  signals, kept in separate columns so analyses can require agreement:
  * euroSciVoc taxonomy paths (`domains_esv`)
  * funding-programme cluster, via topic identifier and legal basis
    (`domains_cluster`). Caveat: Horizon Europe Cluster 5 is "Climate,
    Energy and Mobility" and Cluster 6 is "Food, Bioeconomy, Natural
    Resources, Agriculture and Environment" — both broader than the
    plain domain name.
- the EU climate-expenditure marker (`climate_policy_pct`, 0/40/100,
  Horizon Europe only)
- project covariates: budget, dates, funding scheme, coordinator
  (name, country, activity type), consortium size
- DMP version handling: `version_rank` parsed from the deliverable title
  and `is_latest` marking the most recent DMP per project.
"""

import csv
import io
import logging
import re
import zipfile
from collections import defaultdict
from pathlib import Path

import requests

log = logging.getLogger(__name__)

PROJECT_SOURCES = {
    "h2020": "https://cordis.europa.eu/data/cordis-h2020projects-csv.zip",
    "horizon": "https://cordis.europa.eu/data/cordis-HORIZONprojects-csv.zip",
}

PROJECT_FILES = ["project.csv", "organization.csv", "euroSciVoc.csv", "policyPriorities.csv"]

DOMAIN_RULES = {
    "health": {
        "esv": ["/medical and health sciences"],
        "topic_prefixes": ["HORIZON-HLTH"],
        "legal_prefixes": ["HORIZON.2.1", "H2020-EU.3.1"],
    },
    "agriculture": {
        "esv": ["/agricultural sciences"],
        "topic_prefixes": ["HORIZON-CL6"],
        "legal_prefixes": ["HORIZON.2.6", "H2020-EU.3.2"],
    },
    "climate": {
        "esv": ["climat"],  # climatology, climatic changes, climate research
        "topic_prefixes": ["HORIZON-CL5"],
        "legal_prefixes": ["HORIZON.2.5", "H2020-EU.3.5"],
    },
}

PROJECT_FIELDS = [
    "status", "startDate", "endDate", "totalCost", "ecMaxContribution",
    "legalBasis", "topics", "fundingScheme", "masterCall",
]

CORPUS_FIELDS = (
    ["programme", "id", "projectID", "projectAcronym", "title", "deliverableType",
     "url", "contentUpdateDate", "version_rank", "is_latest"]
    + PROJECT_FIELDS
    + ["coordinator_name", "coordinator_country", "coordinator_type", "coordinator_org_id",
       "n_participants", "n_countries",
       "domains_esv", "domains_cluster", "climate_policy_pct", "domains"]
)

_VERSION_NUM_RE = re.compile(r"\bv(?:ersion)?\s*\.?\s*(\d+)", re.I)
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6}
_STAGE_KEYWORDS = [
    (("final",), 90.0),
    (("updated", "revised", "update", "revision"), 2.0),
    (("interim", "intermediate", "mid-term", "midterm"), 1.5),
    (("initial", "draft", "preliminary"), 1.0),
]


def version_rank(title: str) -> float:
    """Heuristic ordering of DMP versions from the deliverable title.

    Explicit version numbers (v2, version 3) and ordinals rank by number;
    stage words rank initial < interim < updated < final. 0 if no signal.
    """
    t = (title or "").lower()
    rank = 0.0
    m = _VERSION_NUM_RE.search(t)
    if m:
        rank = max(rank, float(m.group(1)))
    words = set(re.findall(r"[a-z-]+", t))
    for word, num in _ORDINALS.items():
        if word in words:
            rank = max(rank, float(num))
    for keywords, score in _STAGE_KEYWORDS:
        if any(k in words for k in keywords):
            rank = max(rank, score)
    return rank


def _fetch_project_dump(data_dir: Path, prog: str, refresh: bool = False) -> Path:
    """Download and extract the projects zip for a programme (cached)."""
    dest_dir = data_dir / "metadata" / prog / "projects"
    if not refresh and (dest_dir / "project.csv").exists():
        log.info("%s: using cached project dump in %s", prog, dest_dir)
        return dest_dir
    url = PROJECT_SOURCES[prog]
    dest_dir.mkdir(parents=True, exist_ok=True)
    log.info("Downloading %s project dump from %s", prog, url)
    resp = requests.get(url, timeout=600)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        for member in zf.namelist():
            base = Path(member).name
            if base in PROJECT_FILES:
                with zf.open(member) as src, open(dest_dir / base, "wb") as dst:
                    dst.write(src.read())
    found = [f for f in PROJECT_FILES if (dest_dir / f).exists()]
    log.info("%s: extracted %s", prog, ", ".join(found))
    if "project.csv" not in found:
        raise RuntimeError(f"project.csv missing from {url}")
    return dest_dir


def _read_csv(path: Path):
    with open(path, newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f, delimiter=";")


def _load_projects(dump_dir: Path) -> dict:
    """Index project metadata, coordinator, consortium and domain signals by id."""
    projects = {}
    for row in _read_csv(dump_dir / "project.csv"):
        rec = {k: (row.get(k) or "").strip() for k in PROJECT_FIELDS}
        rec["domains_esv"] = set()
        rec["domains_cluster"] = set()
        rec["climate_policy_pct"] = ""
        rec.update(coordinator_name="", coordinator_country="", coordinator_type="",
                   coordinator_org_id="", n_participants=0)
        rec["_countries"] = set()
        topics, legal = row.get("topics") or "", row.get("legalBasis") or ""
        for domain, rules in DOMAIN_RULES.items():
            if any(topics.startswith(p) for p in rules["topic_prefixes"]) or \
               any(legal.startswith(p) for p in rules["legal_prefixes"]):
                rec["domains_cluster"].add(domain)
        projects[row["id"].strip()] = rec

    esv_path = dump_dir / "euroSciVoc.csv"
    if esv_path.exists():
        for row in _read_csv(esv_path):
            rec = projects.get((row.get("projectID") or "").strip())
            if not rec:
                continue
            path = (row.get("euroSciVocPath") or "").lower()
            for domain, rules in DOMAIN_RULES.items():
                if any(s in path for s in rules["esv"]):
                    rec["domains_esv"].add(domain)

    pol_path = dump_dir / "policyPriorities.csv"
    if pol_path.exists():
        for row in _read_csv(pol_path):
            rec = projects.get((row.get("projectID") or "").strip())
            if rec is not None:
                rec["climate_policy_pct"] = (row.get("climate") or "").strip()

    for row in _read_csv(dump_dir / "organization.csv"):
        rec = projects.get((row.get("projectID") or "").strip())
        if not rec:
            continue
        rec["n_participants"] += 1
        country = (row.get("country") or "").strip()
        if country:
            rec["_countries"].add(country)
        if (row.get("role") or "").strip().lower() == "coordinator":
            rec["coordinator_name"] = (row.get("name") or "").strip()
            rec["coordinator_country"] = country
            rec["coordinator_type"] = (row.get("activityType") or "").strip()
            rec["coordinator_org_id"] = (row.get("organisationID") or "").strip()
    return projects


def build_corpus(data_dir: Path, programmes=None, refresh: bool = False) -> Path:
    """Write data/corpus.csv: one enriched row per DMP deliverable."""
    csv.field_size_limit(10**9)
    programmes = programmes or list(PROJECT_SOURCES)
    index_path = data_dir / "dmp_deliverables.csv"
    if not index_path.exists():
        raise FileNotFoundError(f"{index_path} missing — run `cordis-dmp filter` first")

    rows = []
    for prog in programmes:
        dump_dir = _fetch_project_dump(data_dir, prog, refresh=refresh)
        projects = _load_projects(dump_dir)
        n_prog = n_unmatched = 0
        with open(index_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["programme"] != prog:
                    continue
                rec = projects.get(row["projectID"])
                out = {
                    "programme": prog,
                    "id": row["id"],
                    "projectID": row["projectID"],
                    "projectAcronym": row["projectAcronym"],
                    "title": row["title"],
                    "deliverableType": row["deliverableType"],
                    "url": row["url"],
                    "contentUpdateDate": row["contentUpdateDate"],
                    "version_rank": version_rank(row["title"]),
                    "is_latest": "",
                }
                if rec:
                    for k in PROJECT_FIELDS:
                        out[k] = rec[k]
                    out.update(
                        coordinator_name=rec["coordinator_name"],
                        coordinator_country=rec["coordinator_country"],
                        coordinator_type=rec["coordinator_type"],
                        coordinator_org_id=rec["coordinator_org_id"],
                        n_participants=rec["n_participants"],
                        n_countries=len(rec["_countries"]),
                        domains_esv="|".join(sorted(rec["domains_esv"])),
                        domains_cluster="|".join(sorted(rec["domains_cluster"])),
                        climate_policy_pct=rec["climate_policy_pct"],
                        domains="|".join(sorted(rec["domains_esv"] | rec["domains_cluster"])),
                    )
                else:
                    n_unmatched += 1
                    for k in CORPUS_FIELDS:
                        out.setdefault(k, "")
                rows.append(out)
                n_prog += 1
        log.info("%s: %d DMP rows enriched (%d without project metadata)", prog, n_prog, n_unmatched)

    # Latest DMP per project: highest version_rank, then contentUpdateDate.
    by_project = defaultdict(list)
    for out in rows:
        by_project[(out["programme"], out["projectID"])].append(out)
    for group in by_project.values():
        latest = max(group, key=lambda r: (r["version_rank"], r["contentUpdateDate"], r["id"]))
        for r in group:
            r["is_latest"] = "true" if r is latest else "false"

    out_path = data_dir / "corpus.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CORPUS_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    n_domains = sum(1 for r in rows if r["domains"])
    log.info("Wrote %d rows to %s (%d with a health/agriculture/climate label, %d projects)",
             len(rows), out_path, n_domains, len(by_project))
    return out_path


def load_corpus_selection(data_dir: Path, domains=None, latest_only: bool = False) -> list:
    """Read corpus.csv applying domain / latest-version filters."""
    path = data_dir / "corpus.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing — run `cordis-dmp enrich` first")
    wanted = set(domains) if domains else None
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if latest_only and row["is_latest"] != "true":
                continue
            if wanted and not (wanted & set(row["domains"].split("|"))):
                continue
            rows.append(row)
    return rows
