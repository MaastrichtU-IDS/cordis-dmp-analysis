"""Horizon Europe DMP template: canonical questions and answer matching.

Encodes the official HE DMP template (data-management-plan_he_en.docx) as a
flat list of canonical questions, and matches them against the extracted
section tree of a DMP (data/text/<id>.json) to locate the answer text for
each question.

Matching here is lexical (difflib), which is sufficient for DMPs that
reproduce the template questions as headings. DMPs that restructure freely
need semantic (embedding) matching — that is a later analysis stage; this
module provides the schema and the verbatim baseline.
"""

import difflib
import json
import re
from pathlib import Path

HE_TEMPLATE = [
    # (question id, template part, canonical question text)
    ("DS-1", "1. Data Summary",
     "Will you re-use any existing data and what will you re-use it for? State the reasons if re-use of any existing data has been considered but discarded."),
    ("DS-2", "1. Data Summary",
     "What types and formats of data will the project generate or re-use?"),
    ("DS-3", "1. Data Summary",
     "What is the purpose of the data generation or re-use and its relation to the objectives of the project?"),
    ("DS-4", "1. Data Summary",
     "What is the expected size of the data that you intend to generate or re-use?"),
    ("DS-5", "1. Data Summary",
     "What is the origin/provenance of the data, either generated or re-used?"),
    ("DS-6", "1. Data Summary",
     "To whom might your data be useful ('data utility'), outside your project?"),
    ("F-1", "2.1 Findable",
     "Will data be identified by a persistent identifier?"),
    ("F-2", "2.1 Findable",
     "Will rich metadata be provided to allow discovery? What metadata will be created? What disciplinary or general standards will be followed?"),
    ("F-3", "2.1 Findable",
     "Will search keywords be provided in the metadata to optimize the possibility for discovery and then potential re-use?"),
    ("F-4", "2.1 Findable",
     "Will metadata be offered in such a way that it can be harvested and indexed?"),
    ("A-1", "2.2 Accessible",
     "Will the data be deposited in a trusted repository?"),
    ("A-2", "2.2 Accessible",
     "Have you explored appropriate arrangements with the identified repository where your data will be deposited?"),
    ("A-3", "2.2 Accessible",
     "Does the repository ensure that the data is assigned an identifier? Will the repository resolve the identifier to a digital object?"),
    ("A-4", "2.2 Accessible",
     "Will all data be made openly available? If certain datasets cannot be shared (or need to be shared under restricted access conditions), explain why."),
    ("A-5", "2.2 Accessible",
     "If an embargo is applied to give time to publish or seek protection of the intellectual property, specify why and how long this will apply."),
    ("A-6", "2.2 Accessible",
     "Will the data be accessible through a free and standardized access protocol?"),
    ("A-7", "2.2 Accessible",
     "If there are restrictions on use, how will access be provided to the data, both during and after the end of the project?"),
    ("A-8", "2.2 Accessible",
     "How will the identity of the person accessing the data be ascertained?"),
    ("A-9", "2.2 Accessible",
     "Is there a need for a data access committee (e.g. to evaluate/approve access requests to personal/sensitive data)?"),
    ("A-10", "2.2 Accessible",
     "Will metadata be made openly available and licensed under a public domain dedication CC0, as per the Grant Agreement? Will metadata contain information to enable the user to access the data?"),
    ("A-11", "2.2 Accessible",
     "How long will the data remain available and findable? Will metadata be guaranteed to remain available after data is no longer available?"),
    ("A-12", "2.2 Accessible",
     "Will documentation or reference about any software be needed to access or read the data be included? Will it be possible to include the relevant software (e.g. in open source code)?"),
    ("I-1", "2.3 Interoperable",
     "What data and metadata vocabularies, standards, formats or methodologies will you follow to make your data interoperable to allow data exchange and re-use within and across disciplines?"),
    ("I-2", "2.3 Interoperable",
     "Will you follow community-endorsed interoperability best practices? Which ones?"),
    ("I-3", "2.3 Interoperable",
     "In case it is unavoidable that you use uncommon or generate project specific ontologies or vocabularies, will you provide mappings to more commonly used ontologies? Will you openly publish the generated ontologies or vocabularies to allow reusing, refining or extending them?"),
    ("I-4", "2.3 Interoperable",
     "Will your data include qualified references to other data (e.g. other data from your project, or datasets from previous research)?"),
    ("R-1", "2.4 Reusable",
     "How will you provide documentation needed to validate data analysis and facilitate data re-use (e.g. readme files with information on methodology, codebooks, data cleaning, analyses, variable definitions, units of measurement, etc.)?"),
    ("R-2", "2.4 Reusable",
     "Will your data be made freely available in the public domain to permit the widest re-use possible? Will your data be licensed using standard reuse licenses, in line with the obligations set out in the Grant Agreement?"),
    ("R-3", "2.4 Reusable",
     "Will the data produced in the project be useable by third parties, in particular after the end of the project?"),
    ("R-4", "2.4 Reusable",
     "Will the provenance of the data be well-documented using the appropriate standards?"),
    ("R-5", "2.4 Reusable",
     "Describe all relevant data quality assurance processes."),
    ("OUT-1", "3. Other research outputs",
     "Will the project generate other research outputs (such as software, workflows, protocols, models, etc.) and how will they be managed, shared and made re-usable in line with the FAIR principles?"),
    ("RES-1", "4. Allocation of resources",
     "What will the costs be for making data or other research outputs FAIR in your project (e.g. direct and indirect costs related to storage, archiving, re-use, security, etc.)? How will these be covered?"),
    ("RES-2", "4. Allocation of resources",
     "Who will be responsible for data management in your project?"),
    ("RES-3", "4. Allocation of resources",
     "How will long term preservation be ensured? Discuss the necessary resources to accomplish this (costs and potential value, who decides and how, what data will be kept and for how long)?"),
    ("SEC-1", "5. Data security",
     "What provisions are or will be in place for data security (including data recovery as well as secure storage/archiving and transfer of sensitive data)? Will the data be safely stored in trusted repositories for long term preservation and curation?"),
    ("ETH-1", "6. Ethics",
     "Are there, or could there be, any ethics or legal issues that can have an impact on data sharing?"),
    ("ETH-2", "6. Ethics",
     "Will informed consent for data sharing and long term preservation be included in questionnaires dealing with personal data?"),
    ("OTH-1", "7. Other issues",
     "Do you, or will you, make use of other national/funder/sectorial/departmental procedures for data management? If yes, which ones (please list and briefly describe them)?"),
]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9? ]+", "", re.sub(r"\s+", " ", s.lower())).strip()


def _merge_fragmented_headings(sections: list) -> list:
    """PDF headings often wrap over several lines; the extractor then emits
    text-less sections for the leading lines. Fold those prefixes into the
    next section's heading."""
    merged, prefix = [], ""
    for s in sections:
        heading = (prefix + " " + s["heading"]).strip()
        if s["heading"] and not s["text"].strip():
            prefix = heading
            continue
        prefix = ""
        merged.append({**s, "heading": heading})
    return merged


def _score(question: str, heading: str) -> float:
    """Similarity robust to truncated headings: compare the heading against
    the same-length prefix of the question."""
    q, h = _norm(question), _norm(heading)
    if not q or not h:
        return 0.0
    return difflib.SequenceMatcher(None, q[: max(len(h), 20)], h).ratio()


def match_document(doc_json: Path, threshold: float = 0.55) -> list:
    """Locate the answer text for each HE template question in one DMP.

    Returns [{id, part, question, matched_heading, score, answer}];
    `answer` is None when no section matches above the threshold.
    """
    with open(doc_json, encoding="utf-8") as f:
        doc = json.load(f)
    sections = _merge_fragmented_headings(doc["sections"])

    results = []
    for qid, part, question in HE_TEMPLATE:
        best, best_score = None, threshold
        for sec in sections:
            score = _score(question, sec["heading"])
            if score > best_score:
                best, best_score = sec, score
        results.append({
            "id": qid,
            "part": part,
            "question": question,
            "matched_heading": best["heading"] if best else None,
            "score": round(best_score, 2) if best else None,
            "answer": best["text"].strip() if best else None,
        })
    return results


def report(doc_json: Path, threshold: float = 0.55, max_chars: int = 500) -> str:
    """Human-readable question -> answer report for one DMP."""
    lines, part_seen = [], None
    results = match_document(doc_json, threshold=threshold)
    n_found = sum(1 for r in results if r["answer"])
    lines.append(f"# {doc_json.stem} — {n_found}/{len(results)} template questions matched\n")
    for r in results:
        if r["part"] != part_seen:
            part_seen = r["part"]
            lines.append(f"\n## {part_seen}\n")
        lines.append(f"[{r['id']}] {r['question']}")
        if r["answer"]:
            answer = r["answer"]
            if max_chars and len(answer) > max_chars:
                answer = answer[:max_chars].rsplit(" ", 1)[0] + " [...]"
            lines.append(f"  -> (match {r['score']}) {answer}\n")
        else:
            lines.append("  -> NOT FOUND\n")
    return "\n".join(lines)
