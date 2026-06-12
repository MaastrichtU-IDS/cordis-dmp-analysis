"""Fetch CORDIS projectDeliverables metadata dumps and filter DMP entries.

CORDIS publishes monthly bulk dumps of project deliverables metadata
(deliverable id, title, type, projectID, acronym, document URL) per
framework programme. DMPs are identified by their deliverable title.
"""

import csv
import io
import logging
import zipfile
from pathlib import Path

import requests

log = logging.getLogger(__name__)

METADATA_SOURCES = {
    "h2020": "https://cordis.europa.eu/data/cordis-h2020projectDeliverables-csv.zip",
    "horizon": "https://cordis.europa.eu/data/cordis-HORIZONprojectDeliverables-csv.zip",
}

DMP_TITLE_KEYWORDS = ("data management plan", "dmp")

FILTERED_FIELDS = ["programme", "id", "title", "deliverableType", "projectID", "projectAcronym", "url", "contentUpdateDate"]


def fetch_metadata(data_dir: Path, programmes=None) -> dict:
    """Download and extract projectDeliverables.csv for each programme.

    Returns {programme: path_to_csv}.
    """
    programmes = programmes or list(METADATA_SOURCES)
    out = {}
    for prog in programmes:
        url = METADATA_SOURCES[prog]
        dest_dir = data_dir / "metadata" / prog
        dest_csv = dest_dir / "projectDeliverables.csv"
        dest_dir.mkdir(parents=True, exist_ok=True)
        log.info("Downloading %s metadata from %s", prog, url)
        resp = requests.get(url, timeout=300)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = [n for n in zf.namelist() if n.endswith("projectDeliverables.csv")]
            if not names:
                raise RuntimeError(f"projectDeliverables.csv not found in {url}: {zf.namelist()}")
            with zf.open(names[0]) as src, open(dest_csv, "wb") as dst:
                dst.write(src.read())
        log.info("Extracted %s (%.1f MB)", dest_csv, dest_csv.stat().st_size / 1e6)
        out[prog] = dest_csv
    return out


def is_dmp(row: dict) -> bool:
    """A deliverable is treated as a DMP if its title mentions one."""
    title = (row.get("title") or "").strip().lower()
    if "data management plan" in title:
        return True
    # Catch bare "DMP" titles like "DMP", "DMP v2", "Initial DMP" without
    # matching unrelated acronym collisions inside longer words.
    words = title.replace("-", " ").replace("(", " ").replace(")", " ").split()
    return "dmp" in words


def filter_dmps(data_dir: Path, programmes=None, require_url: bool = True) -> Path:
    """Scan the metadata CSVs and write data/dmp_deliverables.csv."""
    csv.field_size_limit(10**9)
    programmes = programmes or list(METADATA_SOURCES)
    out_path = data_dir / "dmp_deliverables.csv"
    n_total = 0
    with open(out_path, "w", newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=FILTERED_FIELDS)
        writer.writeheader()
        for prog in programmes:
            src = data_dir / "metadata" / prog / "projectDeliverables.csv"
            if not src.exists():
                raise FileNotFoundError(f"{src} missing — run `cordis-dmp metadata` first")
            n_prog = 0
            with open(src, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f, delimiter=";"):
                    if not is_dmp(row):
                        continue
                    if require_url and not (row.get("url") or "").strip():
                        continue
                    writer.writerow({
                        "programme": prog,
                        "id": row.get("id", ""),
                        "title": row.get("title", ""),
                        "deliverableType": row.get("deliverableType", ""),
                        "projectID": row.get("projectID", ""),
                        "projectAcronym": row.get("projectAcronym", ""),
                        "url": (row.get("url") or "").strip(),
                        "contentUpdateDate": row.get("contentUpdateDate", ""),
                    })
                    n_prog += 1
            log.info("%s: %d DMP deliverables", prog, n_prog)
            n_total += n_prog
    log.info("Wrote %d DMP rows to %s", n_total, out_path)
    return out_path
