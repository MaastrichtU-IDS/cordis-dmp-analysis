# cordis-dmp-analysis

Harvesting and analysing **Data Management Plans (DMPs)** published as open
project deliverables in the EU [CORDIS](https://cordis.europa.eu/) database
(Horizon 2020 + Horizon Europe), as the corpus for a study of **responsible
data management** based on the **TAPS responsibility matrix** (Transparency,
Accountability, Privacy & Confidentiality, Social Values × Actors, Objects,
Processes, Impacts).

The study targets EU-funded projects in **health, agriculture and climate**
and asks: how well do DMPs adhere to the recommended templates (RQ1), how
comprehensively do they cover the TAPS-RM dimensions (RQ2), how do
project-specific factors influence this (RQ3), and does TAPS-rated quality
relate to stated open-science/FAIR commitment (RQ4)?

## What is implemented

A five-stage pipeline behind one CLI (`cordis-dmp`), all stages tested
against the live CORDIS/EC services:

| Stage | Command | Output | Status |
|---|---|---|---|
| 1. Metadata harvest | `cordis-dmp metadata` | `data/metadata/*/projectDeliverables.csv` | done — H2020 (~194k deliverables) + Horizon Europe (~49k) |
| 2. DMP filtering | `cordis-dmp filter` | `data/dmp_deliverables.csv` | done — ~12k DMPs in ~10.4k projects, matched by title ("Data Management Plan" / bare "DMP") |
| 3. Corpus enrichment | `cordis-dmp enrich` | `data/corpus.csv` | done — domain labels, covariates, version handling (details below) |
| 4. Document download | `cordis-dmp download` | `data/pdfs/<programme>/*.pdf` | done — resumable, parallel, rate-limited |
| 5. Text extraction | `cordis-dmp extract` | `data/text/*.json` | done — section-structured text per DMP |
| 6. Template matching | `cordis-dmp template <doc>` | per-question answers (report/JSON) | done — lexical baseline, 35/39 questions on a template-faithful DMP |

### Stage details and design decisions

**Download mechanics (stage 4).** The CORDIS deliverable `url` points at the
EC participant portal (`downloadPublic?documentIds=...&appId=PPGMS`), which
does *not* serve the file directly: it returns an interstitial HTML page that
sets a session cookie and redirects via `window.location` to a one-time
tokenised URL. The downloader replays this two-step flow per document.
Progress is journalled in `data/manifest.jsonl`, so reruns skip completed
files. Average DMP is ~1 MB; the full corpus is ~12–15 GB, the three-domain
Horizon Europe corpus ~3 GB.

**Domain labelling (stage 3).** Two independent signals, kept in separate
columns so analyses can require agreement:

- `domains_esv` — euroSciVoc taxonomy paths (`medical and health sciences`,
  `agricultural sciences`, paths containing `climat`);
- `domains_cluster` — Horizon Europe funding cluster via topic prefix and
  legal basis (`HORIZON-HLTH`/`HORIZON.2.1` → health, `HORIZON-CL5`/`2.5` →
  climate, `HORIZON-CL6`/`2.6` → agriculture). Caveat: CL5 is "Climate,
  Energy and Mobility", CL6 is "Food, Bioeconomy, Natural Resources,
  Agriculture and Environment" — both broader than the plain domain name.
- `climate_policy_pct` — the EU climate-expenditure marker (0/40/100).

Horizon Europe counts (June 2026 dump): **health 1,538 / agriculture 882 /
climate 744** DMP deliverables (union of both signals).

**Covariates for RQ3 (stage 3).** Per project: budget (`ecMaxContribution`,
`totalCost`), start/end dates, funding scheme, coordinator (name, country,
activity type HES/REC/PRC/PUB, organisation id), consortium size and
country count.

**DMP version handling (stage 3).** Projects publish several DMP versions
(initial/updated/final, v1/v2/...). `version_rank` is parsed from the
deliverable title (initial < interim < updated/v2 < final) and `is_latest`
marks one DMP per project (rank, then `contentUpdateDate`), so analyses can
use the latest version cross-sectionally and keep earlier versions for
within-project evolution.

**Text extraction (stage 5).** PyMuPDF-based: heading detection from font
size, boldness and numbering patterns (table-of-contents dot-leader lines
filtered), producing a section tree per document
(`{sections: [{heading, page, text}]}`). Scanned/image-only PDFs are flagged
`needs_ocr` instead of being silently dropped. Inspection of extracted DMPs
confirmed the two adherence styles the analysis must handle: some documents
reproduce the official HE template questions verbatim as headings ("Will
data be deposited in a trusted repository?"), others restructure the content
freely — so template matching downstream must be semantic, not string-based.

## Install

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
```

## Usage

```bash
# everything for a quick start: metadata -> filter -> download
cordis-dmp all --limit 20            # smoke test with 20 documents

# the full study corpus, step by step
cordis-dmp --programmes horizon metadata
cordis-dmp --programmes horizon filter
cordis-dmp --programmes horizon enrich
cordis-dmp --programmes horizon download --domains health,agriculture,climate --latest-only
cordis-dmp --programmes horizon extract  --domains health,agriculture,climate --latest-only
```

Global flags: `--data-dir` (default `./data`), `--programmes h2020,horizon`.
Download flags: `--workers` (default 4), `--delay` (default 0.5 s), `--limit`.
Downloads and extraction are resumable; rerunning skips completed items.

## Output layout

```
data/
├── metadata/
│   ├── h2020/projectDeliverables.csv
│   ├── horizon/projectDeliverables.csv
│   └── horizon/projects/          # project.csv, organization.csv, euroSciVoc.csv, ...
├── dmp_deliverables.csv      # programme, id, title, type, projectID, acronym, url, date
├── corpus.csv                # + domains, coordinator, budget, version_rank, is_latest
├── manifest.jsonl            # one record per attempted download
├── extract_log.jsonl         # one record per extracted PDF
├── pdfs/
│   └── horizon/101210495_1_DELIVHORIZON.pdf
└── text/
    └── 101210495_1_DELIVHORIZON.json   # {sections: [{heading, page, text}], needs_ocr, ...}
```

## HE template question matching (`cordis-dmp template`)

`src/cordis_dmp/template.py` encodes the official Horizon Europe DMP template
as 39 canonical questions across its 7 parts (Data Summary; FAIR —
Findable/Accessible/Interoperable/Reusable; Other research outputs;
Allocation of resources; Data security; Ethics; Other issues) and locates the
answer text for each question in one extracted DMP:

```bash
cordis-dmp template 101210495_1_DELIVHORIZON          # readable report
cordis-dmp template 101210495_1_DELIVHORIZON --json   # machine-readable
```

Matching is lexical (difflib) with two PDF-specific fixes: text-less heading
fragments are folded into the next section's heading (multi-line headings),
and similarity is computed against the same-length *prefix* of the question
(truncated headings). This is the verbatim baseline for RQ1; freely
restructured DMPs need the planned embedding-based matcher.

**Example** — OPALS (project 101210495), a template-faithful DMP:
**35/39 questions matched** (full report:
[`docs/example-template-matching-OPALS.md`](docs/example-template-matching-OPALS.md)).

| Q | Question (abridged) | Answer found (abridged) |
|---|---|---|
| DS-2 | What types and formats of data will be generated or re-used? | Algorithms, code (Python), ML models, performance measurements… |
| DS-4 | What is the expected size of the data? | Code < 500 MB, ML models and training data < 30 GB, measurements < 5 GB |
| F-1 | Will data be identified by a persistent identifier? | Public repositories (GitHub, arXiv/Zenodo) attributing persistent identifiers |
| F-3 | Will search keywords be provided in the metadata? | "Yes." |
| R-2 | Will data be freely available under standard licenses? | Public data and outcomes under standard licenses (e.g. MIT) |
| RES-2 | Who will be responsible for data management? | "The fellow and the host organization at large." |
| OTH-1 | Other national/funder data management procedures? | "No other data management procedures … are planned to be used." |

The 4 misses are instructive for the analysis design: one answer was merged
into the preceding section (question line not typeset as a heading), two
headings were fragmented beyond lexical similarity, and one section carries
the part title instead of the question text. Realistic recall of the verbatim
baseline on template-faithful documents is therefore ~90%. Answer brevity
varies from a bare "Yes." to full paragraphs — the elaboration difference the
ordinal coverage scale in the TAPS annotation stage is meant to capture.

## Next steps (analysis stages, not yet implemented)

- **RQ1** — extend the lexical template matcher with embedding similarity to
  handle freely restructured DMPs (adherence scores corpus-wide); intra- vs.
  inter-domain similarity with
  boilerplate/near-duplicate detection (MinHash) separated from semantic
  similarity.
- **RQ2** — TAPS-RM codebook (16 cells × indicator questions), human-coded
  gold standard on a stratified sample, LLM-assisted annotation validated
  against it, then full-corpus scoring.
- **RQ3** — regression of TAPS coverage on the covariates already in
  `corpus.csv`; PERMANOVA on embedding distances.
- **RQ4** — FAIR/open-science commitment score extracted independently of
  TAPS scoring; optional validation against actual deposits via the OpenAIRE
  Graph (project IDs are already in the corpus).

## Notes

- CORDIS dumps refresh monthly; rerun stages 1–3 to pick up new deliverables —
  stage 4 then downloads only the new ones.
- Be polite to the EC portal: defaults (4 workers, 0.5 s delay) keep the
  request rate modest.
- Data source: © European Union, CORDIS, reusable under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
