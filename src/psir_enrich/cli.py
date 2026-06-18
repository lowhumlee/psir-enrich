"""Command-line entry point for psir-enrich.

After installation the `psir-enrich` command is on PATH. Run
`psir-enrich --help` for usage.

Output is a full-fidelity XML (same structure as the input) with new extid
blocks added in-place — ready for PSIR's XML import dialog.

Import settings in PSIR:
  Tab: XML
  Update record action: overwrite
  Update external identifiers: ✓ CHECKED
  Default field update action: overwrite (or Add new values)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

from psir_enrich import __version__
from psir_enrich.enrich import run_enrichment
from psir_enrich.wos_client import WosStarterClient


DEFAULT_INTERVALS = {"free": 2.0, "subscriber": 0.25, "advanced": 0.1}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="psir-enrich",
        description=(
            "Enrich an OMEGA-PSIR XML export with WoS Identifiers and "
            "PubMed IDs via the Clarivate WoS Starter API. Produces a "
            "full-fidelity output XML (same format as the input) ready "
            "for re-import into PSIR, plus an audit CSV."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  Dry run, no API:\n"
            "    psir-enrich -i in.xml -o enriched.xml --no-api\n\n"
            "  Real run with key from env:\n"
            "    export WOS_API_KEY=abc123\n"
            "    psir-enrich -i in.xml -o enriched.xml --plan subscriber\n\n"
            "Or run the web GUI:\n"
            "    streamlit run app.py\n"
        ),
    )
    p.add_argument("--version", action="version", version=f"psir-enrich {__version__}")
    p.add_argument("--input", "-i", required=True, type=Path,
                   help="Input PSIR XML")
    p.add_argument("--output", "-o", required=True, type=Path,
                   help="Output XML (full collection, extids updated)")
    p.add_argument("--report", "-r", type=Path, default=None,
                   help="Audit CSV (default: <output>.audit.csv)")
    p.add_argument("--no-api", action="store_true",
                   help="Skip Clarivate API — csl-WoS extraction only")
    p.add_argument("--api-base", default=None,
                   help=f"Override Clarivate base URL "
                        f"(default: {WosStarterClient.DEFAULT_BASE_URL})")
    p.add_argument("--api-key", default=None,
                   help="API key (default: env WOS_API_KEY)")
    p.add_argument("--plan", choices=tuple(DEFAULT_INTERVALS),
                   default="subscriber",
                   help="Plan tier — sets default rate limit")
    p.add_argument("--rate-limit", type=float, default=None,
                   help="Override min seconds between API calls")
    p.add_argument("--include-meeting-abstracts-pmid", action="store_true",
                   help="Don't skip meeting abstracts for PubMed lookup")
    return p


def _print_progress(i: int, total: int, state) -> None:
    if state.actions or state.notes:
        tag = " | ".join(state.actions) or "no_change"
        note = f"  {state.notes[0]}" if state.notes else ""
        print(f"  [{i:2d}/{total}] {state.psir_id[:36]}  {tag}{note}")


def main(argv: Optional[list] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.report is None:
        args.report = args.output.with_suffix(".audit.csv")

    if not args.input.exists():
        print(f"ERROR: input not found: {args.input}", file=sys.stderr)
        return 2

    api_key = None
    if not args.no_api:
        api_key = args.api_key or os.environ.get("WOS_API_KEY")
        if not api_key:
            print("WARNING: no API key supplied. Running in csl-only mode.")

    rate = args.rate_limit if args.rate_limit is not None \
        else DEFAULT_INTERVALS[args.plan]

    print(f"Loading {args.input.name}...")
    result = run_enrichment(
        xml_input=args.input,
        api_key=api_key,
        api_base=args.api_base,
        skip_meeting_abstracts=not args.include_meeting_abstracts_pmid,
        rate_limit=rate,
        input_label=args.input.name,
        progress_cb=_print_progress,
    )

    print(f"\nLoaded {result.n_articles} articles")
    if api_key:
        print(f"API calls: {result.n_api_calls}, errors: {result.n_api_errors}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(result.output_xml_bytes)
    print(f"\nOutput XML : {args.output}  ({result.n_enriched} article(s) enriched)")
    print(f"           -> all {result.n_articles} articles included (PSIR needs full collection)")

    result.audit_df.to_csv(args.report, index=False, encoding="utf-8-sig")
    print(f"Audit CSV  : {args.report}")

    print(f"\nSummary: {result.n_enriched} enriched, "
          f"{result.n_articles - result.n_enriched} already complete")

    print("\nPSIR import settings:")
    print("  Tab: XML")
    print("  Update record action: overwrite")
    print("  Update external identifiers: CHECKED  ← important")
    print("  Default field update action: overwrite")
    return 0


if __name__ == "__main__":
    sys.exit(main())
