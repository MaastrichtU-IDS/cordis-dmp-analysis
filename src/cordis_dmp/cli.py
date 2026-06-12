"""Command-line interface: cordis-dmp {metadata,filter,download,all}."""

import argparse
import logging
from pathlib import Path

from . import corpus as cp
from . import download as dl
from . import metadata as md


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="cordis-dmp",
        description="Download Data Management Plans (DMPs) from the EU CORDIS database.",
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="output directory (default: ./data)")
    parser.add_argument(
        "--programmes", type=lambda s: s.split(","), default=None, metavar="PROG[,PROG]",
        help="comma-separated subset of: " + ",".join(md.METADATA_SOURCES) + " (default: all)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("metadata", help="download CORDIS projectDeliverables metadata dumps")
    sub.add_parser("filter", help="extract DMP deliverables into data/dmp_deliverables.csv")

    p_enrich = sub.add_parser("enrich", help="join project metadata: domains, coordinator, budget -> data/corpus.csv")
    p_enrich.add_argument("--refresh", action="store_true", help="re-download the project dumps even if cached")

    p_tpl = sub.add_parser("template", help="locate HE template question answers in one extracted DMP")
    p_tpl.add_argument("doc", help="deliverable id (stem of a data/text/*.json file)")
    p_tpl.add_argument("--threshold", type=float, default=0.55, help="match threshold (default: 0.55)")
    p_tpl.add_argument("--max-chars", type=int, default=500, help="truncate answers (0 = full text)")
    p_tpl.add_argument("--json", action="store_true", help="emit JSON instead of a readable report")

    p_dl = sub.add_parser("download", help="download the DMP documents (PDFs)")
    p_ex = sub.add_parser("extract", help="extract section-structured text from PDFs -> data/text/*.json")
    p_all = sub.add_parser("all", help="metadata + filter + download in one go")
    for p in (p_dl, p_ex, p_all):
        p.add_argument("--limit", type=int, default=None, help="stop after N documents (for testing)")
    for p in (p_dl, p_all):
        p.add_argument("--workers", type=int, default=4, help="parallel downloads (default: 4)")
        p.add_argument("--delay", type=float, default=0.5, help="per-worker delay between downloads in seconds")
    for p in (p_dl, p_ex):
        p.add_argument("--domains", type=lambda s: s.split(","), default=None, metavar="DOM[,DOM]",
                       help="restrict to corpus domains, e.g. health,agriculture,climate (needs `enrich` first)")
        p.add_argument("--latest-only", action="store_true",
                       help="only the latest DMP version per project (needs `enrich` first)")

    args = parser.parse_args(argv)
    if args.programmes:
        unknown = set(args.programmes) - set(md.METADATA_SOURCES)
        if unknown:
            parser.error(f"unknown programme(s): {', '.join(sorted(unknown))}")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command in ("metadata", "all"):
        md.fetch_metadata(args.data_dir, args.programmes)
    if args.command in ("filter", "all"):
        md.filter_dmps(args.data_dir, args.programmes)
    if args.command == "enrich":
        cp.build_corpus(args.data_dir, args.programmes, refresh=args.refresh)
    if args.command in ("download", "all"):
        dl.download_all(args.data_dir, workers=args.workers, delay=args.delay, limit=args.limit,
                        domains=getattr(args, "domains", None),
                        latest_only=getattr(args, "latest_only", False))
    if args.command == "extract":
        from . import extract as ex
        ex.extract_all(args.data_dir, domains=args.domains, latest_only=args.latest_only, limit=args.limit)
    if args.command == "template":
        import json as _json

        from . import template as tpl
        doc_json = args.data_dir / "text" / (args.doc + ".json")
        if args.json:
            print(_json.dumps(tpl.match_document(doc_json, threshold=args.threshold),
                              ensure_ascii=False, indent=1))
        else:
            print(tpl.report(doc_json, threshold=args.threshold, max_chars=args.max_chars))


if __name__ == "__main__":
    main()
