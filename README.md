# cordis-dmp-analysis

Download and analyse **Data Management Plans (DMPs)** published as project
deliverables in the EU [CORDIS](https://cordis.europa.eu/) database, covering
**Horizon 2020** and **Horizon Europe**.

## How it works

1. **Metadata** — CORDIS publishes monthly bulk dumps of project deliverables
   metadata per framework programme:
   - H2020: `cordis-h2020projectDeliverables-csv.zip` (~19 MB, ~194k deliverables)
   - Horizon Europe: `cordis-HORIZONprojectDeliverables-csv.zip` (~5 MB, ~49k deliverables)
2. **Filter** — deliverables whose title mentions a *Data Management Plan*
   (or bare *DMP*) are extracted to `data/dmp_deliverables.csv`
   (~12k documents across ~10.4k projects, as of mid-2026).
3. **Download** — each deliverable `url` points at the EC participant portal
   (`downloadPublic?documentIds=...&appId=PPGMS`), which serves an
   interstitial HTML page that sets a session cookie and redirects via
   `window.location` to a one-time tokenised URL. The downloader replays this
   two-step flow per document and stores the PDFs under
   `data/pdfs/<programme>/<deliverable_id>.pdf`.

Average DMP is ~1 MB, so the full corpus is roughly **12–15 GB**.

## Install

```bash
pip install -e .
```

## Usage

```bash
# everything: metadata → filter → download
cordis-dmp all

# or step by step
cordis-dmp metadata                  # fetch CORDIS metadata dumps into data/metadata/
cordis-dmp filter                    # write data/dmp_deliverables.csv
cordis-dmp download --workers 4      # download PDFs into data/pdfs/

# smoke test with 20 documents, Horizon Europe only
cordis-dmp --programmes horizon all --limit 20   # or --programmes h2020,horizon
```

Downloads are **resumable**: progress is journalled in `data/manifest.jsonl`
and completed documents are skipped on rerun. Failures are recorded with the
error message and can be retried by simply rerunning `cordis-dmp download`
after removing their `error` lines (or leaving them — only `ok` entries are
skipped).

## Output layout

```
data/
├── metadata/
│   ├── h2020/projectDeliverables.csv
│   └── horizon/projectDeliverables.csv
├── dmp_deliverables.csv      # programme, id, title, type, projectID, acronym, url, date
├── manifest.jsonl            # one record per attempted download
└── pdfs/
    ├── h2020/634013_37_DELIV.pdf
    └── horizon/...
```

## Notes

- CORDIS dumps are refreshed monthly; rerun `cordis-dmp metadata && cordis-dmp filter`
  to pick up new deliverables, then `cordis-dmp download` fetches only the new ones.
- Be polite to the EC portal: the defaults (4 workers, 0.5 s delay) keep the
  request rate modest. The full corpus takes a few hours.
- Data source: © European Union, CORDIS, reusable under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
