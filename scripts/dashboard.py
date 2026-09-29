"""Build an offline, blind evaluation dashboard from an existing Transplate Clean run.

Usage: python dashboard.py output/runs/<run_id>
No network calls, no OpenAI calls, and no changes to experimental records.
"""
import argparse
import base64
import csv
import hashlib
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "dashboard"


def lines(path):
    return [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines() if s.strip()]


def build(run, reuse_from=None):
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    with (run / "source_blocks.csv").open(encoding="utf-8-sig", newline="") as f:
        source = list(csv.DictReader(f))
    by_id = {b["unit_id"]: b for b in source}
    by_numeric = {(b["document_id"], int(b["id"])): b for b in source if b.get("id")}

    def item_source(doc, item):
        return by_numeric[(doc, item["id"])] if "id" in item else by_id[item["unit_id"]]
    records = {stage: lines(run / (stage + ".jsonl")) if (run / (stage + ".jsonl")).exists() else []
               for stage in ("parse", "examine", "translate")}
    # Failed responses remain evidence, but never count as machine-valid outputs.
    failures = lines(run / "failed.jsonl") if (run / "failed.jsonl").exists() else []
    # A recovered paid response is a single qualifying record, not also a
    # duplicate rejected option merely because its original audit log remains.
    completed_request_keys = {r.get("key") for stage in ("examine", "translate")
                              for r in records[stage]}
    failures = [r for r in failures if r.get("key") not in completed_request_keys]
    for failed in failures:
        parts = failed["key"].split(":", 3)
        if len(parts) != 4 or parts[0] not in ("parse", "examine"):
            continue
        stage, model, doc, page_ref = parts
        if not page_ref.startswith("p") or not page_ref[1:].isdigit():
            continue
        page = int(page_ref[1:])
        # Display the actual rejected response without modifying it or relaxing validation.
        try:
            raw = json.loads(failed.get("raw_output", ""))
        except (ValueError, TypeError):
            raw = {}
        items = raw.get("items", []) if isinstance(raw, dict) else []
        if not isinstance(items, list):
            items = []
        items = [item for item in items if isinstance(item, dict)]
        original = [b for b in source if b["document_id"] == doc and int(b["page"]) == page and
                    b.get("eligible", "").lower() in ("true", "1", "yes", "t")]
        # The source IDs of a successful peer response are preferable when present.
        peer = next((r for r in records[stage]
                     if r["document_id"] == doc and int(r["page"]) == page), None)
        source_ids = peer["source_unit_ids"] if peer else [b["unit_id"] for b in original]
        records[stage].append({"key": failed["key"], "model": model, "document_id": doc,
                               "page": page, "source_unit_ids": source_ids,
                               "items": items, "invalid": True, "validation_error": failed["error"],
                               "raw_output": failed.get("raw_output", "")})
    source_images = {}
    # Render the original PDF pages if already cached locally; otherwise show extracted text and PDF links.
    try:
        import pymupdf
        for doc, data in manifest["documents"].items():
            candidates = [run.parent.parent / "sources" / Path(data["path"].replace("\\", "/")).name,
                          Path(data["path"])]
            pdf_path = next((p for p in candidates if p.is_file()), None)
            if not pdf_path or hashlib.sha256(pdf_path.read_bytes()).hexdigest() != data["sha256"]:
                continue
            with pymupdf.open(pdf_path) as pdf:
                for page_idx, page in enumerate(pdf):
                    pix = page.get_pixmap(matrix=pymupdf.Matrix(1.2, 1.2), alpha=False)
                    source_images[f"{doc}:{page_idx+1}"] = "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode("ascii")
    except ImportError:
        pass  # A textual source comparison remains available without cached PDF rendering.

    if manifest.get("evaluation_mode") == "full":
        # Historical data are visualization-only: never write old results into this
        # run's experimental JSONL or count them as completed API requests.
        if reuse_from:
            older = reuse_from.resolve()
            if older == run:
                raise ValueError("--reuse-from must identify a different historical run")
            old_manifest = json.loads((older / "manifest.json").read_text(encoding="utf-8"))
            if old_manifest["models"] != manifest["models"]:
                raise ValueError("Historical and current model lists do not match")
            if {d: v["sha256"] for d, v in old_manifest["documents"].items()} != {
                    d: v["sha256"] for d, v in manifest["documents"].items()}:
                raise ValueError("Historical and current PDF hashes do not match")
            with (older / "source_blocks.csv").open(encoding="utf-8-sig", newline="") as f:
                previous = list(csv.DictReader(f))
            def units(rows):
                return {(b["document_id"], b["unit_id"]):
                        (b["english"], b["page"], str(b["eligible"]).lower()) for b in rows}
            if units(previous) != units(source):
                raise ValueError("Historical and current source units/eligibility differ")
            # Historical page-batched examination and sampled translation can be
            # evaluated, but are NOT substitutes for the current experiment's calls.
            for stage in ("examine", "translate"):
                current = {(r["model"], r["document_id"], item["id"])
                           for r in records[stage] for item in r["items"]}
                for old in lines(older / f"{stage}.jsonl"):
                    items = [u for u in old["items"] if
                             (old["model"], old["document_id"], u["id"]) not in current]
                    if items:
                        records[stage].append({**old, "items": items,
                                               "origin_run_id": old_manifest["run_id"]})
                print(f"Imported historical {stage} results for DASHBOARD ONLY from {older.name}")
            # Failed historical batches are shown strictly as nonqualifying diagnostics.
            # Existing current successes take precedence over old failures.
            seen_current = {(r["model"], r["document_id"], item["id"])
                            for stage in ("examine", "translate") for r in records[stage]
                            if "origin_run_id" not in r for item in r["items"]}
            for old in lines(older / "failed.jsonl") if (older / "failed.jsonl").exists() else []:
                if old.get("key", "").split(":", 1)[0] not in ("examine", "translate"):
                    continue
                try:
                    payload = json.loads(old.get("raw_output", ""))
                    old_items = payload.get("items", []) if isinstance(payload, dict) else []
                    parts = old["key"].split(":")
                    if any((parts[1], parts[2], u["id"]) not in seen_current for u in old_items
                           if isinstance(u, dict) and type(u.get("id")) is int):
                        failures.append({**old, "origin_run_id": old_manifest["run_id"]})
                except (ValueError, KeyError, IndexError, TypeError):
                    # No recoverable per-unit output; it remains a missing response.
                    pass
        return build_full(run, manifest, source, records, failures, source_images,
                          historical_run_id=old_manifest["run_id"] if reuse_from else None)

    # Existing translation CSV supplies authoritative per-unit anonymous labels and a private key.
    with (run / "evaluation" / "translation_evaluation.csv").open(encoding="utf-8-sig", newline="") as f:
        evaluation = list(csv.DictReader(f))
    with (run / "evaluation" / "model_key.csv").open(encoding="utf-8-sig", newline="") as f:
        translation_key = {r["evaluation_id"]: r["model"] for r in csv.DictReader(f)}
    translation_lookup = {(r["document_id"], r["unit_id"], translation_key[r["evaluation_id"]]): r
                          for r in evaluation}
    grouped = {}
    for stage in ("parse", "examine"):
        for r in records[stage]:
            grouped.setdefault((stage, r["document_id"], r["page"]), []).append(r)
    for r in records["translate"]:
        for item in r["items"]:
            block = item_source(r["document_id"], item)
            grouped.setdefault(("translate", r["document_id"], block["unit_id"]), []).append((r, item))

    cases, key_rows = [], []
    order = {"parse": 0, "examine": 1, "translate": 2}
    for (stage, doc, locator), responses in sorted(grouped.items(), key=lambda kv: (order[kv[0][0]], kv[0][1], str(kv[0][2]))):
        case_id = f"{stage}:{doc}:{locator}"
        randomized = list(responses)
        random.Random(f"{manifest['seed']}:{case_id}").shuffle(randomized)
        options = []
        for i, r in enumerate(randomized):
            if stage == "translate":
                batch, item = r
                model = batch["model"]
                ev = translation_lookup[(doc, locator, model)]
                anonymous_id = ev["evaluation_id"]
                items = [item]  # New id/sp output; historical unit_id/spanish still supported.
            else:
                model = r["model"]
                anonymous_id = f"{case_id}:{chr(65+i)}"
                items = r["items"]
                if stage == "parse":
                    items = [{**u, "source_english": " ".join(by_id[s]["english"] for s in u.get("src", u.get("source_ids", []))),
                              "source_match": " ".join(u.get("x", u.get("english", "")).split()) == " ".join(" ".join(by_id[s]["english"] for s in u.get("src", u.get("source_ids", []))).split())}
                             for u in items]
            options.append({"label": chr(65+i), "anonymous_id": anonymous_id, "items": items,
                            "invalid": bool(r.get("invalid")) if stage != "translate" else False,
                            "validation_error": r.get("validation_error", "") if stage != "translate" else "",
                            "raw_output": r.get("raw_output", "") if stage != "translate" else ""})
            key_rows.append({"case_id": case_id, "stage": stage, "anonymous_id": anonymous_id, "option": chr(65+i), "model": model})
        if stage == "translate":
            blocks = [by_id[locator]]
            page = int(by_id[locator]["page"])
        else:
            blocks = [by_id[s] for s in randomized[0]["source_unit_ids"]]
            page = int(locator)
        cases.append({"id": case_id, "stage": stage, "document": doc, "page": page,
                      "unit_id": locator if stage == "translate" else "", "blocks":
                      [{"unit_id": b["unit_id"], "id": int(b["id"]) if b.get("id") else None, "english": b["english"]} for b in blocks],
                      "pdf_url": manifest["documents"][doc]["url"], "options": options})

    template = (HERE / "dashboard.html").read_text(encoding="utf-8")
    destination = run / "evaluation"
    destination.mkdir(exist_ok=True)
    for scope, selected in (("france", [c for c in cases if c["document"] == "france"]),
                            ("all", cases)):
        visible = {"run_id": manifest["run_id"], "seed": manifest["seed"], "scope": scope,
                   "cases": selected, "source_images": {k: v for k, v in source_images.items()
                                                      if scope == "all" or k.startswith("france:")}}
        # Public page contains only anonymized responses and validation messages, never model IDs.
        safe_json = json.dumps(visible, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        page = template.replace("/* TRANSPLATE_DATA */", "const DATA = " + safe_json + ";")
        suffix = "france" if scope == "france" else "all"
        html = destination / f"blind_dashboard_{suffix}.html"
        key = destination / f"dashboard_model_key_{suffix}.csv"
        html.write_text(page, encoding="utf-8")
        selected_ids = {c["id"] for c in selected}
        selected_keys = [r for r in key_rows if r["case_id"] in selected_ids]
        with key.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=("case_id", "stage", "anonymous_id", "option", "model", "machine_valid", "validation_error"))
            writer.writeheader()
            writer.writerows(selected_keys)
        invalid_count = sum(o["invalid"] for c in selected for o in c["options"])
        print(f"Dashboard: {html} ({len(selected)} cases, {len(selected_keys)} blind options; "
              f"{invalid_count} invalid responses shown for diagnostic review)")
        print(f"PRIVATE mapping: {key} (never distribute to reviewers)")
    print("Open a blind_dashboard_*.html file in your browser. Neither dashboard makes API calls.")


def build_full(run, manifest, source, records, failures, source_images, historical_run_id=None):
    """One case per deterministic source unit; no model names in reviewer data."""
    from collections import defaultdict

    by_num = {(b["document_id"], int(b["id"])): b for b in source if b.get("id")}
    grouped = defaultdict(list)
    warnings_by_item = {}
    for stage in ("examine", "translate"):
        for batch in records[stage]:
            for item in batch["items"]:
                block = by_num[(batch["document_id"], item["id"])]
                warnings_by_item[(stage, batch["document_id"], block["unit_id"], batch["model"])] = (
                    batch.get("assessment_warnings", {}).get(str(item["id"]), []))
                grouped[(stage, batch["document_id"], block["unit_id"])].append(
                    (batch["model"], item, False, "", "", batch.get("origin_run_id", manifest["run_id"])))
    # Individual invalid units are displayed as diagnostics and never become qualifying results.
    for failed in failures:
        key = failed.get("key", "")
        match = key.split(":")
        if len(match) < 4 or match[0] not in ("examine", "translate"):
            continue
        stage, model, doc = match[:3]
        try:
            raw = json.loads(failed.get("raw_output", ""))
            items = raw.get("items", []) if isinstance(raw, dict) else []
            if not items and isinstance(raw, dict) and isinstance(raw.get("units"), dict):
                items = [{"id": int(k), **v} for k, v in raw["units"].items()
                         if str(k).isdigit() and isinstance(v, dict)]
        except (ValueError, TypeError):
            items = []
        if not isinstance(items, list):
            items = []
        # Per-item diagnostic data can be displayed even when the entire batch failed.
        for item in items:
            if not isinstance(item, dict) or type(item.get("id")) is not int:
                continue
            block = by_num.get((doc, item["id"]))
            if block:
                grouped[(stage, doc, block["unit_id"])].append(
                    (model, item, True, failed.get("error", ""), failed.get("raw_output", ""),
                     failed.get("origin_run_id", manifest["run_id"])))

    # Include every eligible source unit, even when no model returned a usable response.
    for b in source:
        if str(b.get("eligible", "")).lower() in ("true", "1", "yes", "t"):
            for stage in ("examine", "translate"):
                grouped.setdefault((stage, b["document_id"], b["unit_id"]), [])

    cases, private = [], []
    source_order = {(b["document_id"], b["unit_id"]): i for i, b in enumerate(source)}
    stage_order = {"examine": 0, "translate": 1}
    for (stage, doc, unit), responses in sorted(grouped.items(),
            key=lambda v: (stage_order[v[0][0]], v[0][1],
                           source_order[(v[0][1], v[0][2])])):
        block = next(b for b in source if b["unit_id"] == unit and b["document_id"] == doc)
        case_id = f"{stage}:{doc}:{unit}"
        # Identical substantive results share one human judgment, even when a peer
        # response failed machine validation elsewhere in its batch. Eligibility stays
        # model-specific in the private mapping and in separate exported diagnostics.
        equivalents = {}
        for model, item, invalid, error, raw, origin in responses:
            substantive = (item.get("sp", item.get("spanish", "")) if stage == "translate"
                           else json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
            group = equivalents.setdefault(substantive, {"item": item, "members": [], "raw_output": "",
                                                       "assessment_warnings": []})
            group["members"].append((model, invalid, error, origin))
            for warning in warnings_by_item.get((stage, doc, unit, model), []):
                if warning not in group["assessment_warnings"]:
                    group["assessment_warnings"].append(warning)
            if invalid and not group["raw_output"]:
                group["raw_output"] = raw
        distinct = list(equivalents.values())
        random.Random(f"{manifest['seed']}:{case_id}").shuffle(distinct)
        options = []
        for idx, group in enumerate(distinct):
            label = chr(65 + idx)
            # Use the grouping key, not the first model's item, for stable identity.
            signature = json.dumps((stage, next(k for k, v in equivalents.items() if v is group)),
                                   sort_keys=True, ensure_ascii=False)
            anon = f"{case_id}:{hashlib.sha256(signature.encode()).hexdigest()[:12]}"
            qualified = sum(not invalid for _, invalid, _, _ in group["members"])
            diagnostic = len(group["members"]) - qualified
            errors = sorted({error for _, invalid, error, _ in group["members"] if invalid})
            options.append({"label": label, "anonymous_id": anon, "item": group["item"],
                            "invalid": qualified == 0, "mixed": qualified > 0 and diagnostic > 0,
                            "validation_error": "; ".join(errors),
                            "raw_output": group["raw_output"], "assessment_warnings": group["assessment_warnings"],
                            "duplicate_count": len(group["members"]),
                            "qualified_count": qualified, "diagnostic_count": diagnostic})
            for model, invalid, error, origin in group["members"]:
                private.append({"case_id": case_id, "stage": stage, "anonymous_id": anon,
                                "option": label, "model": model, "machine_valid": not invalid,
                                "validation_error": error if invalid else "",
                                "assessment_warnings": "; ".join(group["assessment_warnings"]),
                                "origin_run_id": origin,
                                "historical_conditions": origin != manifest["run_id"]})
        # Missing outputs (including failed batches with no usable item) must not disappear from reporting.
        observed = {m for group in distinct for m, _, _, _ in group["members"]}
        missing = sorted(set(manifest["models"]) - observed)
        cases.append({"id": case_id, "stage": stage, "document": doc,
                      "page": int(block["page"]), "unit_id": unit, "english": block["english"],
                      "pdf_url": manifest["documents"][doc]["url"], "options": options,
                      "missing_count": len(missing)})
        for model in missing:
            private.append({"case_id": case_id, "stage": stage, "anonymous_id": "",
                            "option": "MISSING", "model": model,
                            "origin_run_id": "", "historical_conditions": False,
                            "assessment_warnings": ""})

    dest = run / "evaluation"
    dest.mkdir(exist_ok=True)
    template = (HERE / "full_dashboard.html").read_text(encoding="utf-8")
    for scope, selected in (("france", [c for c in cases if c["document"] == "france"]),
                            ("all", cases)):
        public = {"run_id": manifest["run_id"], "scope": scope, "cases": selected,
                  "historical_run_id": historical_run_id,
                  "source_images": {k: v for k, v in source_images.items()
                                    if scope == "all" or k.startswith("france:")}}
        # Private model mappings are strictly separate from the self-contained reviewer HTML.
        data = json.dumps(public, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        (dest / f"blind_dashboard_{scope}.html").write_text(
            template.replace("/* TRANSPLATE_DATA */", "const DATA = " + data + ";"), encoding="utf-8")
        selected_ids = {c["id"] for c in selected}
        with (dest / f"dashboard_model_key_{scope}.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=("case_id", "stage", "anonymous_id", "option", "model", "machine_valid",
                                                   "validation_error", "origin_run_id", "historical_conditions",
                                                   "assessment_warnings"))
            writer.writeheader()
            writer.writerows(r for r in private if r["case_id"] in selected_ids)
        print(f"Dashboard: {dest / f'blind_dashboard_{scope}.html'} "
              f"({len(selected)} full-coverage cases; identical responses consolidated)")
        print(f"PRIVATE mapping: {dest / f'dashboard_model_key_{scope}.csv'}")
    return cases


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("run", type=Path, help="Existing output/runs/<run_id> directory")
    cli.add_argument("--reuse-from", type=Path, help="Include historical responses in the new-format reviewer dashboard only")
    args = cli.parse_args()
    build(args.run.resolve(), args.reuse_from)
