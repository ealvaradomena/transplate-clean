"""Blind CSV generation from an experiment or independent translation file."""
import csv
import json
import random
from pathlib import Path
from . import common
from .common import CRITERIA, read_jsonl, write_csv


def template(args):
    if bool(args.run) == bool(args.translations):
        raise ValueError("Choose exactly one: --run RUN_DIRECTORY or --translations INPUT.csv")
    rows = []
    seed = 42
    if args.run:
        run = Path(args.run).resolve()
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        seed = manifest["seed"]
        source = {}
        with (run / "source_blocks.csv").open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                source[r["unit_id"]] = r
        numeric_source = {(r["document_id"], int(r["id"])): r for r in source.values() if r.get("id")}
        for batch in read_jsonl(run / "translate.jsonl"):
            for u in batch["items"]:
                original = (numeric_source[(batch["document_id"], u["id"])] if "id" in u
                            else source[u["unit_id"]])  # Earlier completed experiments remain readable.
                rows.append({"document_id": batch["document_id"], "pdf_url": manifest["documents"][batch["document_id"]]["url"],
                             "page": original["page"], "unit_id": original["unit_id"],
                             "source_english": original["english"], "translation_spanish": u.get("sp", u.get("spanish")),
                             "model": batch["model"]})
        if not rows:
            raise ValueError("No completed translations in this experiment run")
        output = Path(args.out).resolve() if args.out else run / "evaluation"
    else:
        src = Path(args.translations)
        with src.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            required = {"document_id", "page", "unit_id", "source_english", "translation_spanish"}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError(f"Input CSV requires columns: {sorted(required)}; model and pdf_url optional")
            rows = list(reader)
        if not rows:
            raise ValueError("Input CSV contains no translations")
        output = Path(args.out).resolve() if args.out else common.ROOT / "output" / "evaluation"
    output.mkdir(parents=True, exist_ok=True)
    # Anonymous IDs are unique across doc/unit/model, deterministic within this material.
    # Shuffle each unit's model choices so label A never consistently identifies one model.
    grouped = {}
    for r in rows:
        grouped.setdefault((r["document_id"], r["unit_id"]), []).append(r)
    exported, key = [], []
    for (doc, unit), group in sorted(grouped.items()):
        # Stable canonical ordering makes repeated exports identical regardless of input row order.
        group.sort(key=lambda r: (r.get("model", ""), r["translation_spanish"]))
        if len({(r.get("model", ""), r["translation_spanish"]) for r in group}) != len(group):
            raise ValueError(f"Repeated translation for {doc}/{unit}; remove duplicate source rows")
        rng = random.Random(f"{seed}:{doc}:{unit}")
        rng.shuffle(group)
        for idx, r in enumerate(group):
            label = f"{doc}:{unit}:{idx+1}"
            exported.append({"evaluation_id": label, "document_id": doc, "pdf_url": r.get("pdf_url", ""),
                             "page": r["page"], "unit_id": unit, "source_english": r["source_english"],
                             "translation_spanish": r["translation_spanish"],
                             **{c: "" for c in CRITERIA}, "notes": "", "review_flag": ""})
            key.append({"evaluation_id": label, "document_id": doc, "unit_id": unit, "model": r.get("model", "")})
    fields = ("evaluation_id", "document_id", "pdf_url", "page", "unit_id", "source_english",
              "translation_spanish", *CRITERIA, "notes", "review_flag")
    write_csv(output / "translation_evaluation.csv", exported, fields)
    write_csv(output / "model_key.csv", key, ("evaluation_id", "document_id", "unit_id", "model"))
    print(f"Created {output / 'translation_evaluation.csv'} ({len(exported)} translation rows)")
    print(f"Separate model mapping: {output / 'model_key.csv'} (keep hidden from reviewers)")
    print("Scores: blank = not assessed; 0 = fails; 1 = meets criterion. Avoid sorting key with review CSV.")


