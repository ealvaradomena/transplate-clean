"""Existing Transplate command-line interface (keeps old commands unchanged)."""
import argparse
import sys
from pathlib import Path
from . import common
from .common import load_config
from .extraction import review_extraction
from .experiment import experiment, prepare, jobs, recover_failed, preflight
from .evaluation import template


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("experiment", help="Prepare PDFs and dry-run by default; --live explicitly authorizes API calls")
    p.add_argument("--config", default=str(common.ROOT / "config.json"))
    p.add_argument("--dry-run", action="store_true", help="Explicitly state the default free behavior")
    p.add_argument("--live", action="store_true", help="Explicitly authorize paid requests")
    p.add_argument("--max-cost-usd", type=float, help="Required positive incremental cap when --live")
    p.add_argument("--skip-failed", action="store_true",
                   help="Explicitly skip previously recorded failures; preserve their evidence and do not retry")
    p = sub.add_parser("recover", help="Offline revalidate previously billed failed responses (zero API calls)")
    p.add_argument("--config", default=str(common.ROOT / "config.json"))
    p.add_argument("--run", required=True, help="Must be the run for this exact current prompt and configuration")
    p = sub.add_parser("verify-extraction", help="Render source PDF and raw block transcript for manual fidelity review (free)")
    p.add_argument("--config", default=str(common.ROOT / "config.json"))
    p.add_argument("--document", required=True, help="Configured source document ID, e.g. france")
    p = sub.add_parser("template", help="Create blind translation evaluation CSV (zero API calls)")
    p.add_argument("--run", help="Completed or partial experiment run directory")
    p.add_argument("--translations", help="Independent CSV of existing English/Spanish translations")
    p.add_argument("--out", help="Destination folder for review CSV + separate private model key")
    p = sub.add_parser("production", help="One XLSX: approve English edits and review reused/new Spanish translations")
    p.add_argument("phase", choices=("prepare", "authorize", "translate", "export"))
    p.add_argument("--run", help="Completed experiment run containing stored examination outputs (prepare)")
    p.add_argument("--document", help="Document ID from the experiment (prepare)")
    p.add_argument("--examine-model", help="Model whose stored examination outputs to review (prepare)")
    p.add_argument("--translate-model", help="Model to translate authorized English (prepare)")
    p.add_argument("--dir", help="Production working directory (authorize/translate/export)")
    p.add_argument("--config", default=str(common.ROOT / "config.json"))
    p.add_argument("--live", action="store_true", help="Explicit paid translation authorization")
    p.add_argument("--max-cost-usd", type=float, help="Required positive incremental cap for live translation")
    args = parser.parse_args(argv)
    if args.command == "verify-extraction":
        cfg = load_config(Path(args.config))
        if args.document not in cfg["documents"]:
            parser.error("--document must be a document ID from config.json")
        review_extraction(cfg, args.document)
    elif args.command == "experiment":
        if args.dry_run and args.live:
            parser.error("Select dry-run OR live")
        experiment(args)
    elif args.command == "recover":
        cfg = load_config(Path(args.config))
        run = Path(args.run).resolve()
        # Preparing the inputs offline recomputes the complete identity, preventing
        # accidental reuse of responses generated under other instructions.
        out, _, blocks, samples = prepare(cfg)
        if run != out.resolve():
            raise ValueError(f"This run has different experimental inputs; exact current run: {out}")
        recovered, excluded = recover_failed(out, jobs(cfg, blocks, samples))
        for key, warnings in recovered:
            print(f"RECOVERED (no API call): {key}; {sum(map(len, warnings.values()))} assessment warning(s)")
        for key, why in excluded:
            print(f"STILL INVALID (not retried): {key}; {why}")
        print(f"Offline recovery: {len(recovered)} recovered, {len(excluded)} still invalid; original failed.jsonl retained.")
    elif args.command == "production":
        from . import production
        if args.phase == "prepare":
            if not all((args.run, args.document, args.examine_model, args.translate_model)):
                parser.error("production prepare requires --run, --document, --examine-model, --translate-model")
            production.prepare(args.run, args.document, args.examine_model, args.translate_model, args.dir)
        else:
            if not args.dir:
                parser.error("production authorize/translate/export requires --dir")
            if args.phase == "authorize":
                production.authorize(args.dir)
            elif args.phase == "translate":
                production.translate(args.dir, args.config, args.live, args.max_cost_usd)
            else:
                production.export(args.dir)
    else:
        template(args)


