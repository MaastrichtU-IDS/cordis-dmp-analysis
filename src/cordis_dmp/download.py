"""Download DMP documents from the EC participant portal.

The CORDIS deliverable `url` field points at
`.../documents/downloadPublic?documentIds=...&appId=PPGMS`, which does not
serve the file directly: it returns an interstitial HTML page that sets a
session cookie and redirects via `window.location` to a one-time tokenised
URL. Fetching that token URL with the same session yields the actual
document (almost always a PDF).
"""

import csv
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

log = logging.getLogger(__name__)

WINDOW_LOCATION_RE = re.compile(r"window\.location\s*=\s*'([^']+)'")
USER_AGENT = "cordis-dmp-analysis/0.1 (research use; mailto:remzi.celebi@maastrichtuniversity.nl)"

CONTENT_TYPE_EXT = {
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


def _safe_name(deliverable_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", deliverable_id)


def _guess_ext(content: bytes, content_type: str) -> str:
    if content[:5] == b"%PDF-":
        return ".pdf"
    ct = (content_type or "").split(";")[0].strip().lower()
    return CONTENT_TYPE_EXT.get(ct, ".bin")


def download_one(url: str, timeout: int = 120) -> tuple[bytes, str]:
    """Resolve the two-step downloadPublic flow; return (content, extension)."""
    with requests.Session() as s:
        s.headers["User-Agent"] = USER_AGENT
        r1 = s.get(url, timeout=timeout)
        r1.raise_for_status()
        ctype = r1.headers.get("Content-Type", "")
        if "text/html" not in ctype:
            return r1.content, _guess_ext(r1.content, ctype)
        m = WINDOW_LOCATION_RE.search(r1.text)
        if not m:
            raise RuntimeError("no redirect found in interstitial page (document may be restricted)")
        r2 = s.get(m.group(1), timeout=timeout)
        r2.raise_for_status()
        content = r2.content
        if content[:5] != b"%PDF-" and b"<html" in content[:500].lower():
            raise RuntimeError("token URL returned HTML instead of a document")
        return content, _guess_ext(content, r2.headers.get("Content-Type", ""))


def download_all(
    data_dir: Path,
    workers: int = 4,
    delay: float = 0.5,
    limit: int | None = None,
    retries: int = 3,
    domains=None,
    latest_only: bool = False,
) -> None:
    """Download every document listed in data/dmp_deliverables.csv.

    With `domains`/`latest_only`, the selection is narrowed via
    data/corpus.csv (built by `cordis-dmp enrich`). Files land in
    data/pdfs/<programme>/<deliverable id>.<ext>; progress is journalled in
    data/manifest.jsonl so reruns skip completed entries.
    """
    csv.field_size_limit(10**9)
    if domains or latest_only:
        from .corpus import load_corpus_selection
        keep = {r["id"] for r in load_corpus_selection(data_dir, domains=domains, latest_only=latest_only)}
        log.info("Corpus selection: %d deliverables match domains=%s latest_only=%s", len(keep), domains, latest_only)
    else:
        keep = None
    index_path = data_dir / "dmp_deliverables.csv"
    if not index_path.exists():
        raise FileNotFoundError(f"{index_path} missing — run `cordis-dmp filter` first")

    manifest_path = data_dir / "manifest.jsonl"
    done = set()
    if manifest_path.exists():
        with open(manifest_path, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                if rec.get("status") == "ok":
                    done.add(rec["id"])

    with open(index_path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f)
                if r["id"] not in done and (keep is None or r["id"] in keep)]
    if limit:
        rows = rows[:limit]
    log.info("%d documents to download (%d already done)", len(rows), len(done))

    manifest_lock = threading.Lock()
    manifest_f = open(manifest_path, "a", encoding="utf-8")

    def record(rec: dict) -> None:
        with manifest_lock:
            manifest_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            manifest_f.flush()

    def worker(row: dict) -> str:
        out_dir = data_dir / "pdfs" / row["programme"]
        out_dir.mkdir(parents=True, exist_ok=True)
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                content, ext = download_one(row["url"])
                path = out_dir / (_safe_name(row["id"]) + ext)
                path.write_bytes(content)
                record({"id": row["id"], "status": "ok", "path": str(path), "bytes": len(content)})
                time.sleep(delay)
                return "ok"
            except Exception as e:  # noqa: BLE001 — journal and move on
                last_err = e
                time.sleep(delay * attempt * 2)
        record({"id": row["id"], "status": "error", "url": row["url"], "error": str(last_err)})
        return "error"

    n_ok = n_err = 0
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(worker, row) for row in rows]
            for i, fut in enumerate(as_completed(futures), 1):
                if fut.result() == "ok":
                    n_ok += 1
                else:
                    n_err += 1
                if i % 100 == 0 or i == len(rows):
                    log.info("%d/%d done (%d ok, %d failed)", i, len(rows), n_ok, n_err)
    finally:
        manifest_f.close()
    log.info("Finished: %d ok, %d failed. Manifest: %s", n_ok, n_err, manifest_path)
