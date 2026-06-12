"""Command-line interface: cordis-dmp {metadata,filter,download,all}."""

import argparse
import logging
from pathlib import Path

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

    p_dl = sub.add_parser("download", help="download the DMP documents (PDFs)")
    p_all = sub.add_parser("all", help="metadata + filter + download in one go")
    for p in (p_dl, p_all):
        p.add_argument("--workers", type=int, default=4, help="parallel downloads (default: 4)")
        p.add_argument("--delay", type=float, default=0.5, help="per-worker delay between downloads in seconds")
        p.add_argument("--limit", type=int, default=None, help="stop after N documents (for testing)")

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
    if args.command in ("download", "all"):
        dl.download_all(args.data_dir, workers=args.workers, delay=args.delay, limit=args.limit)


if __name__ == "__main__":
    main()
