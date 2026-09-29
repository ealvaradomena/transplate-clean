"""Experimental jobs, validation, cost planning, and optional live execution."""
import hashlib
from copy import deepcopy
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from . import common
from .common import (VERSION, PROMPTS, OUTPUT_LIMIT, STAGES, digest, save_json, write_csv,
                     read_jsonl, append_jsonl, load_config)
from .extraction import source_file, extract_blocks

EXAMINE_SCHEMA = Path(__file__).resolve().parent.parent / "schemas" / "examine.schema.json"


def prepare(cfg):
    sources, all_blocks, samples = {}, [], {}
    for doc_id, url in cfg["documents"].items():
        path = source_file(cfg, doc_id, url)
        sources[doc_id] = {"path": str(path), "url": url, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        blocks = extract_blocks(doc_id, path)
        for block_id, b in enumerate(blocks, 1):
            b["id"] = block_id  # Deterministic document-local id; src retains source-block provenance.
        all_blocks.extend(blocks)
        eligible = [b for b in blocks if b["eligible"]]
        # Eligible, verified source blocks are the fixed units for both stages.
        samples[doc_id] = eligible
    # Run identity changes if prompt, source bytes, models, sample or config changes.
    ident = digest({"version": VERSION, "prompts": PROMPTS, "sources": sources, "models": cfg["models"],
                    "coverage": "all-eligible", "response_protocol": "examine-strict-keyed-v1",
                    "schema": json.loads(EXAMINE_SCHEMA.read_text(encoding="utf-8")),
                    "seed": cfg["random_seed"]})
    out = common.ROOT / cfg["output_directory"] / "runs" / ident
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"run_id": ident, "documents": sources, "models": cfg["models"], "seed": cfg["random_seed"],
                "coverage": "all-eligible", "evaluation_mode": "full",
                "response_protocol": "examine-strict-keyed-v1", "version": VERSION}
    save_json(out / "manifest.json", manifest)
    save_json(out / "units.json", {d: [b["unit_id"] for b in rows] for d, rows in samples.items()})
    write_csv(out / "source_blocks.csv", [{**b, "bbox": json.dumps(b["bbox"])} for b in all_blocks],
              ("document_id", "pdf_sha256", "page", "unit_id", "id", "english", "eligible", "bbox"))
    return out, sources, all_blocks, samples


def jobs(cfg, blocks, samples):
    """Both tasks receive every eligible deterministic unit; small batches limit output truncation."""
    grouped = {}
    for b in blocks:
        if b["eligible"]:
            grouped.setdefault((b["document_id"], b["page"]), []).append(b)
    tasks = []
    for model in cfg["models"]:
        for (doc_id, page), page_blocks in grouped.items():
            for offset in range(0, len(page_blocks), 4):
                units = page_blocks[offset:offset + 4]
                for stage in STAGES:
                    tasks.append({"stage": stage, "model": model, "document_id": doc_id,
                                  "page": page, "units": units,
                                  "key": f"{stage}:{model}:{doc_id}:p{page}:id{units[0]['id']}"})
    return tasks


def payload(task):
    stage = task["stage"]
    return {"units": [{"id": b["id"], "x": b["english"]} for b in task["units"]]}


def request_text(task):
    """Identify the precise supplied unit boundaries outside the input JSON."""
    data = payload(task)
    expected = [u["id"] for u in data["units"]]
    if task["stage"] == "examine":
        return ("The API supplies a strict response schema with a units object keyed by source ID. "
                f"Fill exactly these {len(expected)} source-ID keys: {json.dumps(expected)}. "
                "Do not renumber, add, or invent keys or items. Do not include an id field "
                "inside a unit; Python restores IDs after validation.\n"
                + json.dumps(data, ensure_ascii=False))
    return ("Return one JSON object with an items array. "
            f"Return exactly {len(expected)} items with these IDs only, once each: "
            f"{json.dumps(expected)}. Do not renumber, add, or invent items.\n"
            + json.dumps(data, ensure_ascii=False))


def examination_format(task):
    """Strict, task-specific schema: only the supplied IDs can appear as keys.

    The repository's examination schema remains the source of truth for item fields;
    only the redundant id property and unsupported strict-schema constraints are removed.
    """
    template = json.loads(EXAMINE_SCHEMA.read_text(encoding="utf-8"))["items"]
    template = deepcopy(template)
    template["properties"].pop("id")
    for flag in ("v", "m", "ml", "wr", "mi", "di"):
        template["properties"][flag]["type"] = "string"
    template["properties"]["findings"]["items"]["properties"]["kind"]["type"] = "string"
    template["required"].remove("id")
    for patch_field in ("old", "new"):
        template["properties"]["e"]["items"]["properties"][patch_field].pop("minLength", None)
    for field in ("excerpt", "reason"):
        template["properties"]["findings"]["items"]["properties"][field].pop("minLength", None)
    template["properties"]["rn"].pop("minLength", None)
    ids = [str(unit["id"]) for unit in task["units"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Cannot constrain a task with duplicate source IDs")
    return {"type": "json_schema", "name": "examination_by_source_id", "strict": True,
            "schema": {"type": "object", "additionalProperties": False,
                       "properties": {"units": {"type": "object", "additionalProperties": False,
                                                "properties": {key: deepcopy(template) for key in ids},
                                                "required": ids}},
                       "required": ["units"]}}


def decode_examination(task, data):
    """Convert strict wire format to existing canonical items without losing validation."""
    expected = [str(u["id"]) for u in task["units"]]
    if (not isinstance(data, dict) or set(data) != {"units"}
            or not isinstance(data["units"], dict) or set(data["units"]) != set(expected)):
        raise ValueError("Constrained examination returned missing or unexpected source IDs")
    if any(not isinstance(data["units"][key], dict) or "id" in data["units"][key]
           for key in expected):
        raise ValueError("Constrained examination returned an invalid unit object")
    return {"items": [{"id": int(key), **data["units"][key]} for key in expected]}


def estimated_tokens(task):
    # Rough character heuristic including instructions/JSON overhead, not tokenizer billing.
    text = PROMPTS[task["stage"]] + request_text(task)
    # Include the schema supplied alongside examination instructions in cost estimates.
    schema_chars = len(json.dumps(examination_format(task)["schema"])) if task["stage"] == "examine" else 0
    incoming = int((len(text) + schema_chars) / 4) + 130
    outgoing = {"examine": 165, "translate": 58}[task["stage"]] * len(task["units"]) + 45
    return incoming, outgoing


def cost(usage, prices, cached_input=0):
    return ((usage[0] - cached_input) * prices["input"] +
            cached_input * prices.get("cached_input", prices["input"]) +
            usage[1] * prices["output"]) / 1_000_000


def completed_keys(folder):
    return {r["key"] for stage in STAGES for r in read_jsonl(folder / f"{stage}.jsonl")}


def remaining_tasks(tasks, out, skip_failed=False):
    """Reuse completed calls; skip recorded failures only with explicit permission."""
    completed = completed_keys(out)
    failures = {r["key"] for r in read_jsonl(out / "failed.jsonl")}
    return [t for t in tasks if t["key"] not in completed
            and not (skip_failed and t["key"] in failures)]


def preflight(cfg, out, tasks, skip_failed=False):
    finished = completed_keys(out)
    failures = [r for r in read_jsonl(out / "failed.jsonl") if r["key"] not in finished]
    pending = remaining_tasks(tasks, out, skip_failed)
    by_model = {}
    by_stage = {stage: 0 for stage in STAGES}
    for t in pending:
        by_stage[t["stage"]] += 1
        price = cfg["pricing_usd_per_million_tokens"].get(t["model"])
        v = by_model.setdefault(t["model"], {"requests": 0, "input_tokens_approx": 0,
                                              "output_tokens_approx": 0, "cost_usd_approx": 0})
        i, o = estimated_tokens(t)
        v["requests"] += 1
        v["input_tokens_approx"] += i
        v["output_tokens_approx"] += o
        if price:
            v["cost_usd_approx"] += cost((i, o), price)
        else:
            v["cost_usd_approx"] = None
    for v in by_model.values():
        if v["cost_usd_approx"] is not None:
            v["cost_usd_approx"] = round(v["cost_usd_approx"], 6)
    return {"run_directory": str(out), "total_requests": len(tasks), "completed_requests": len(finished),
            "incremental_requests": len(pending), "pending_by_stage": by_stage, "by_model": by_model,
            "blocked_failed_requests": [] if skip_failed else [r["key"] for r in failures],
            "skipped_failed_requests": sorted({r["key"] for r in failures}) if skip_failed else [],
            "pricing_note": "User-editable illustrative prices; verify current official pricing. "
                            "Token figures are estimates, not guaranteed bills; reasoning tokens count as output."}


def apply_patches(english, patches):
    """Validate and apply conservative source-grounded patches deterministically."""
    if not isinstance(patches, list):
        raise ValueError("Examination patches must be a list")
    updated = english
    for patch in patches:
        if (not isinstance(patch, dict) or set(patch) != {"old", "new"}
                or not isinstance(patch["old"], str) or not patch["old"]
                or not isinstance(patch["new"], str)):
            raise ValueError("Each patch needs a nonempty old string and a new string")
        old, new = patch["old"], patch["new"]
        if " ".join(old.split()) == " ".join(new.split()):
            raise ValueError("No-op or whitespace-only patch")
        if updated.count(old) != 1:
            raise ValueError("Patch old span must match uniquely in the current English unit")
        updated = updated.replace(old, new, 1)
    return updated


def checked_output(task, result):
    """Enforce response *structure* and exact source coverage, not model accuracy.

    Inconsistent mathematical flags, unjustified findings and bad proposed edits are
    precisely the mistakes the human evaluation is designed to measure. Rejecting an
    entire paid batch for such mistakes discards evaluable data and biases comparison.
    """
    stage = task["stage"]
    items = result.get("items") if isinstance(result, dict) else None
    if not isinstance(items, list):
        raise ValueError("Missing items array")
    source = [b["id"] for b in task["units"]]
    ids = [u.get("id") for u in items if isinstance(u, dict)]
    if (len(items) != len(source) or len(ids) != len(items)
            or any(type(i) is not int for i in ids) or sorted(ids) != sorted(source)):
        raise ValueError(f"{stage} returned missing, duplicate, or unknown numeric ids")
    if stage == "translate":
        for u in items:
            if set(u) != {"id", "sp"} or not isinstance(u["sp"], str) or not u["sp"].strip():
                raise ValueError("Translation items require only id and nonempty sp")
        return items
    fields = {"id", "v", "m", "ml", "e", "wr", "mi", "di", "rn", "findings"}
    for u in items:
        if (set(u) != fields or u["v"] != "T"
                or any(u[k] not in ("T", "F") for k in ("m", "ml", "wr", "mi", "di"))
                or not isinstance(u["e"], list) or not isinstance(u["findings"], list)
                or not isinstance(u["rn"], str)):
            raise ValueError("Examination response has invalid fields or field types")
        for e in u["e"]:
            if (not isinstance(e, dict) or set(e) != {"old", "new"}
                    or not all(isinstance(e[k], str) for k in ("old", "new"))):
                raise ValueError("Examination correction has invalid fields or field types")
        for f in u["findings"]:
            if (not isinstance(f, dict) or set(f) != {"kind", "excerpt", "reason"}
                    or f["kind"] not in ("wr", "mi", "di")
                    or not all(isinstance(f[k], str) for k in ("excerpt", "reason"))):
                raise ValueError("Examination finding has invalid fields or field types")
    return items


def assessment_warnings(task, items):
    """Report independently assessable mistakes without altering model output.

    Keyed by the requested numeric source ID. These warnings are *not* machine
    disqualifications: the independent whole-unit and issue-level rubrics grade them.
    """
    if task["stage"] != "examine":
        return {}
    original = {b["id"]: b["english"] for b in task["units"]}
    warnings = {}
    for u in items:
        notes = []
        if u["ml"] == "T" and (u["m"] != "T" or not u["e"]):
            notes.append("LaTeX normalization is T without mathematical content and an implementing patch")
        try:
            apply_patches(original[u["id"]], u["e"])
        except ValueError as exc:
            notes.append(f"Proposed correction cannot be applied conservatively: {exc}")
        for position, finding in enumerate(u["findings"], 1):
            if not finding["excerpt"].strip() or finding["excerpt"] not in original[u["id"]]:
                notes.append(f"Technical finding {position} lacks a verbatim source excerpt")
            if not finding["reason"].strip():
                notes.append(f"Technical finding {position} lacks an explanation")
        for flag in ("wr", "mi", "di"):
            if (u[flag] == "T") != any(f["kind"] == flag for f in u["findings"]):
                notes.append(f"{flag} flag disagrees with the structured findings")
        if (u["rn"].strip() == "OK") != (len(u["findings"]) == 0):
            notes.append("Technical review note disagrees with the structured findings")
        if not u["rn"].strip():
            notes.append("Technical review note is empty")
        if notes:
            warnings[str(u["id"])] = notes
    return warnings


def recover_failed(out, tasks):
    """Offline recovery of previously billed, structurally valid responses only.

    Does not retry an API call, alter the original failure log, or silently repair
    any reported content. Only responses for tasks of this exact run are eligible.
    """
    by_key = {t["key"]: t for t in tasks}
    done = completed_keys(out)
    recovered, unrecoverable = [], []
    for failed in read_jsonl(out / "failed.jsonl"):
        key = failed["key"]
        if key in done:
            continue
        task = by_key.get(key)
        if task is None:
            unrecoverable.append((key, "not part of this run"))
            continue
        try:
            data = json.loads(failed["raw_output"])
            item_data = decode_examination(task, data) if task["stage"] == "examine" else data
            items = checked_output(task, item_data)
        except (ValueError, TypeError, KeyError) as exc:
            unrecoverable.append((key, str(exc)))
            continue
        warnings = assessment_warnings(task, items)
        append_jsonl(out / f"{task['stage']}.jsonl", {
            "key": key, "model": task["model"], "document_id": task["document_id"],
            "page": task["page"], "source_unit_ids": [b["unit_id"] for b in task["units"]],
            "items": items, "assessment_warnings": warnings,
            "recovered_from_failure": True,
            "validation_policy": "structure-v2; content evaluated by human"})
        done.add(key)
        recovered.append((key, warnings))
    return recovered, unrecoverable


def experiment(args):
    cfg = load_config(Path(args.config))
    out, sources, blocks, sample = prepare(cfg)
    tasks = jobs(cfg, blocks, sample)
    plan = preflight(cfg, out, tasks, skip_failed=args.skip_failed)
    print(json.dumps(plan, indent=2))
    if not args.live:
        print("DRY RUN: PDFs and extraction prepared; ZERO paid API requests.")
        return
    if args.max_cost_usd is None or args.max_cost_usd <= 0:
        raise ValueError("Live execution requires --max-cost-usd > 0 and explicit --live")
    if plan["blocked_failed_requests"]:
        raise RuntimeError("Previous failed calls exist. Inspect failed.jsonl first; no automatic paid retry.")
    pending = remaining_tasks(tasks, out, skip_failed=args.skip_failed)
    missing = {t["model"] for t in pending if t["model"] not in cfg["pricing_usd_per_million_tokens"]}
    if missing:
        raise ValueError(f"Live cost control needs prices for models: {sorted(missing)}")
    rough = sum(cost(estimated_tokens(t), cfg["pricing_usd_per_million_tokens"][t["model"]]) for t in pending)
    if rough > args.max_cost_usd:
        raise ValueError(f"Estimated incremental cost ${rough:.5f} exceeds --max-cost-usd ${args.max_cost_usd:.5f}")
    if not pending:
        print("All requests previously completed; no API calls made.")
        return
    from openai import OpenAI
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("Set OPENAI_API_KEY before live execution")
    client = OpenAI(max_retries=0)  # Avoid SDK-level automatic repeated submissions.
    spent = 0.0
    new_failures = []
    # Only new costs in THIS invocation count toward the explicit incremental cap.
    for task in pending:
        price = cfg["pricing_usd_per_million_tokens"][task["model"]]
        stage = task["stage"]
        # Reserve worst-case output ceiling, rather than claiming the approximate bill is a ceiling.
        i, _ = estimated_tokens(task)
        output_limit = OUTPUT_LIMIT[stage]
        reserve = cost((i * 2, output_limit), price)
        if spent + reserve > args.max_cost_usd:
            print(f"STOP: remaining budget cannot reserve next request ({task['key']}).")
            break
        print("REQUEST", task["key"], flush=True)
        started = time.perf_counter()
        response = client.responses.create(
            model=task["model"],
            instructions=PROMPTS[stage],
            input=request_text(task),
            text={"format": examination_format(task) if stage == "examine" else {"type": "json_object"}},
            max_output_tokens=output_limit,
            **({"reasoning": {"effort": "low"}} if task["model"] == "gpt-5-nano" else {}),
            store=False,
        )
        elapsed = round(time.perf_counter() - started, 3)
        usage = response.usage
        actual_in = usage.input_tokens if usage else 0
        actual_out = usage.output_tokens if usage else 0
        cached_in = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
        observed = cost((actual_in, actual_out), price, cached_in)
        spent += observed
        record = {"time_utc": datetime.now(timezone.utc).isoformat(), "key": task["key"],
                  "stage": stage, "model": task["model"], "served_model": response.model,
                  "reasoning_tokens": getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", None),
                  "elapsed_seconds": elapsed, "cached_input_tokens": cached_in,
                  "input_tokens": actual_in,
                  "output_tokens": actual_out, "observed_cost_usd": round(observed, 8),
                  "api_response_id": response.id}
        append_jsonl(out / "usage.jsonl", record)  # Track a billed response even if parsing fails.
        try:
            data = json.loads(response.output_text)
            validated = checked_output(task, decode_examination(task, data) if stage == "examine" else data)
        except (ValueError, KeyError, TypeError) as exc:
            append_jsonl(out / "failed.jsonl", {"key": task["key"], "error": str(exc),
                                                 "raw_output": response.output_text, "usage": record})
            new_failures.append(task["key"])
            print(f"REJECTED {task['key']}: {exc}; recorded, not retried. Continuing.", flush=True)
            continue
        warnings = assessment_warnings(task, validated)
        append_jsonl(out / f"{stage}.jsonl", {"key": task["key"], "model": task["model"],
                      "document_id": task["document_id"], "page": task["page"],
                      "source_unit_ids": [u["unit_id"] for u in task["units"]], "items": validated,
                      "assessment_warnings": warnings,
                      "validation_policy": "structure-v2; content evaluated by human"})
        count = sum(map(len, warnings.values()))
        print(f"COMPLETED {task['key']}; {count} assessment warning(s); "
              f"invocation cost so far ${spent:.6f}", flush=True)
    print(f"Invocation finished. Observed API cost from usage: ${spent:.6f}; "
          f"{len(new_failures)} structurally invalid response(s) recorded without retry.")
    if all(t["key"] in completed_keys(out) for t in tasks):
        print("All experiment requests complete. Generate translation template using 'template --run'.")
    elif new_failures or read_jsonl(out / "failed.jsonl"):
        print("Any unresolved structural failures remain diagnostic only. Run offline recovery, "
              "or use --skip-failed to finish missing requests without repeating them.")


