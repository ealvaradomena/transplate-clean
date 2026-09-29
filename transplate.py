"""Transplate Clean command-line entry point.

Use `python transplate.py experiment|verify-extraction|template ...` from the project root.
Implementation is grouped in scripts/; existing commands remain unchanged.
"""
import sys
from scripts.common import (ROOT, VERSION, CRITERIA, STAGES, PROMPTS, OUTPUT_LIMIT,
                            digest, save_json, append_jsonl, read_jsonl, write_csv, load_config)
from scripts.extraction import source_file, extract_blocks, review_extraction
from scripts.experiment import (prepare, jobs, payload, estimated_tokens, cost, completed_keys,
                                preflight, checked_output, apply_patches, experiment)
from scripts.evaluation import template
from scripts.cli import main

if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
