"""PDF -> structured text extraction for downloaded DMPs.

Uses PyMuPDF to read each PDF, detects section headings from font size,
boldness and numbering patterns, and writes one JSON file per document to
data/text/<deliverable id>.json:

    {id, n_pages, n_chars, chars_per_page, needs_ocr,
     sections: [{heading, page, text}], }

Documents with very little extractable text per page (scanned PDFs) are
flagged `needs_ocr` and their sections left empty rather than dropped, so
the exclusion is visible downstream. Progress is journalled in
data/extract_log.jsonl; existing outputs are skipped on rerun.
"""

import json
import logging
import re
import statistics
from pathlib import Path

import fitz  # PyMuPDF

from .corpus import load_corpus_selection

log = logging.getLogger(__name__)

MIN_CHARS_PER_PAGE = 200  # below this we assume a scanned/image PDF
NUMBERED_HEADING_RE = re.compile(r"^(\d+|[A-Z])(\.\d+)*\.?\s+\S")
MAX_HEADING_LEN = 120


def _iter_lines(doc):
    """Yield (page_no, text, font_size, is_bold) for every text line."""
    for pno, page in enumerate(doc, 1):
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = " ".join(s["text"].strip() for s in spans)
                size = max(s["size"] for s in spans)
                bold = all(s["flags"] & 16 for s in spans)
                yield pno, text, size, bold


def _is_heading(text: str, size: float, bold: bool, body_size: float) -> bool:
    if len(text) > MAX_HEADING_LEN or not re.search(r"[A-Za-z]", text):
        return False
    if "...." in text:  # table-of-contents dot leaders
        return False
    if text.endswith((".", ";", ",")) and not NUMBERED_HEADING_RE.match(text):
        return False
    larger = size > body_size + 0.8
    emphasised = bold and size >= body_size - 0.1
    numbered = bool(NUMBERED_HEADING_RE.match(text)) and (bold or larger)
    return larger or emphasised or numbered


def extract_pdf(path: Path) -> dict:
    """Extract a section tree from one PDF."""
    doc = fitz.open(path)
    lines = list(_iter_lines(doc))
    n_pages = doc.page_count
    doc.close()

    n_chars = sum(len(t) for _, t, _, _ in lines)
    chars_per_page = n_chars / max(n_pages, 1)
    result = {
        "id": path.stem,
        "n_pages": n_pages,
        "n_chars": n_chars,
        "chars_per_page": round(chars_per_page, 1),
        "needs_ocr": chars_per_page < MIN_CHARS_PER_PAGE,
        "sections": [],
    }
    if result["needs_ocr"]:
        return result

    body_size = statistics.median(size for _, t, size, _ in lines for _ in t)
    sections = [{"heading": "", "page": 1, "text": []}]
    for pno, text, size, bold in lines:
        if _is_heading(text, size, bold, body_size):
            sections.append({"heading": text, "page": pno, "text": []})
        else:
            sections[-1]["text"].append(text)
    for s in sections:
        s["text"] = "\n".join(s["text"])
    result["sections"] = [s for s in sections if s["heading"] or s["text"]]
    return result


def extract_all(data_dir: Path, domains=None, latest_only: bool = False,
                limit: int | None = None) -> None:
    """Extract every downloaded PDF matching the corpus selection."""
    out_dir = data_dir / "text"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "extract_log.jsonl"

    pdf_index = {p.stem: p for p in (data_dir / "pdfs").rglob("*.pdf")}
    if (data_dir / "corpus.csv").exists():
        selection = load_corpus_selection(data_dir, domains=domains, latest_only=latest_only)
        from .download import _safe_name
        ids = [_safe_name(r["id"]) for r in selection]
        targets = [pdf_index[i] for i in ids if i in pdf_index]
        log.info("Selection: %d corpus rows, %d with a downloaded PDF", len(selection), len(targets))
    else:
        if domains or latest_only:
            raise FileNotFoundError("corpus.csv missing — run `cordis-dmp enrich` to use --domains/--latest-only")
        targets = sorted(pdf_index.values())
        log.info("No corpus.csv — extracting all %d downloaded PDFs", len(targets))

    targets = [p for p in targets if not (out_dir / (p.stem + ".json")).exists()]
    if limit:
        targets = targets[:limit]
    log.info("%d PDFs to extract", len(targets))

    n_ok = n_ocr = n_err = 0
    with open(log_path, "a", encoding="utf-8") as log_f:
        for i, path in enumerate(targets, 1):
            try:
                result = extract_pdf(path)
                with open(out_dir / (path.stem + ".json"), "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=1)
                status = "needs_ocr" if result["needs_ocr"] else "ok"
                n_ocr += result["needs_ocr"]
                n_ok += not result["needs_ocr"]
                log_f.write(json.dumps({
                    "id": path.stem, "status": status, "n_pages": result["n_pages"],
                    "n_sections": len(result["sections"]), "chars_per_page": result["chars_per_page"],
                }) + "\n")
            except Exception as e:  # noqa: BLE001 — journal and move on
                n_err += 1
                log_f.write(json.dumps({"id": path.stem, "status": "error", "error": str(e)}) + "\n")
            if i % 200 == 0 or i == len(targets):
                log.info("%d/%d extracted (%d ok, %d need OCR, %d errors)", i, len(targets), n_ok, n_ocr, n_err)
    log.info("Done. Section JSONs in %s, log in %s", out_dir, log_path)
