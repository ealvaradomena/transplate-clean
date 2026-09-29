"""One production XLSX: authorize English edits and review reused/new Spanish drafts.

The experiment's stored responses are read-only evidence. Human authorization is
independent of experimental evaluation scores. No API calls occur by default.
"""
import csv
import hashlib
import json
import os
import tempfile
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import xlsxwriter
import time
from datetime import datetime, timezone
from pathlib import Path

from . import common
from .common import read_jsonl, append_jsonl, save_json, load_config
from .experiment import apply_patches, checked_output, cost

# Keep the original 19 review columns and append only the two authorization controls.
REVIEW_COLUMNS = ("english_changed", "english_original", "english_authorized", "spanish_draft",
                  "sp_human", "status", "human_note", "correction_patches", "review_wrong",
                  "review_misleading", "review_dismissive", "review_note", "page", "source_refs",
                  "text_class", "level", "source_math", "latex_reconstruction", "id")
AUTH_COLUMNS = REVIEW_COLUMNS + ("approve_patch_indices", "authorization")
OLD_AUTH_COLUMNS = ("unit_id", "page", "english_original", "proposed_patches",
                    "approve_patch_indices", "authorization")



# The production XLSX is the single editable artifact. Historical experiment CSVs remain
# immutable inputs, and older production CSVs are read only for one-time migration.
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "p": "http://schemas.openxmlformats.org/package/2006/relationships"}
REVIEW_FILE = "translation_review.xlsx"


def _xlsx_rows(path):
    """Read the project's single-sheet XLSX without installing a second dependency."""
    with ZipFile(path) as package:
        names = set(package.namelist())
        workbook = ET.fromstring(package.read("xl/workbook.xml"))
        sheets = workbook.findall("m:sheets/m:sheet", NS)
        if len(sheets) != 1 or sheets[0].get("name") != "Translation Review":
            raise ValueError("Expected exactly one 'Translation Review' worksheet")
        relationships = ET.fromstring(package.read("xl/_rels/workbook.xml.rels"))
        rid = sheets[0].get("{" + NS["r"] + "}id")
        target = next((n.get("Target") for n in relationships if n.get("Id") == rid), None)
        if not target:
            raise ValueError("Translation Review worksheet is missing")
        target = (target.lstrip("/") if target.startswith("/") else
                  "xl/" + target if not target.startswith("xl/") else target)
        if target not in names:
            raise ValueError("Translation Review worksheet XML is missing")
        strings = []
        if "xl/sharedStrings.xml" in names:
            shared = ET.fromstring(package.read("xl/sharedStrings.xml"))
            strings = ["".join(node.itertext()) for node in shared.findall("m:si", NS)]
        sheet = ET.fromstring(package.read(target))
        data = []
        for row in sheet.findall("m:sheetData/m:row", NS):
            cells = {}
            for cell in row.findall("m:c", NS):
                ref = cell.get("r", "")
                label = "".join(ch for ch in ref if ch.isalpha())
                col = 0
                for ch in label:
                    col = col * 26 + ord(ch.upper()) - 64
                if not col:
                    continue
                if cell.find("m:f", NS) is not None:
                    raise ValueError("Formula cells are not supported in the editable review worksheet")
                kind = cell.get("t")
                value = cell.find("m:v", NS)
                inline = cell.find("m:is", NS)
                if kind == "s" and value is not None:
                    content = strings[int(value.text)]
                elif kind == "inlineStr" and inline is not None:
                    content = "".join(inline.itertext())
                elif value is not None and value.text is not None:
                    content = value.text
                else:
                    content = ""
                cells[col-1] = content
            if cells:
                data.append(cells)
        if not data:
            raise ValueError("Production workbook has no headers")
        headers = [data[0].get(i, "") for i in range(len(AUTH_COLUMNS))]
        if headers != list(AUTH_COLUMNS) or any(k >= len(headers) for k in data[0]):
            raise ValueError("Production workbook has unexpected columns")
        rows = [{name: item.get(i, "") for i, name in enumerate(headers)} for item in data[1:]]
        if any(not r["id"] for r in rows):
            raise ValueError("Production workbook contains missing unit IDs")
        return rows, headers


def _write_review(path, rows, fields=AUTH_COLUMNS):
    """Rewrite the sole XLSX atomically; retain every editable value and consistent formatting."""
    if tuple(fields) != AUTH_COLUMNS:
        raise ValueError("Unexpected production review column names")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".translation_review-", suffix=".xlsx", dir=path.parent)
    os.close(fd)
    try:
        workbook = xlsxwriter.Workbook(temp_name)
        worksheet = workbook.add_worksheet("Translation Review")
        worksheet.hide_gridlines(2)
        worksheet.freeze_panes(1, 1)
        worksheet.set_zoom(90)
        header = workbook.add_format({"bold": True, "font_color": "#FFFFFF",
            "bg_color": "#244769", "text_wrap": True, "valign": "vcenter",
            "border": 0, "font_size": 10})
        base = workbook.add_format({"font_name": "Aptos", "font_size": 10,
            "text_wrap": True, "valign": "top", "font_color": "#172033",
            "bottom": 1, "bottom_color": "#E7EDF3"})
        editable = workbook.add_format({"font_name": "Aptos", "font_size": 10,
            "bg_color": "#FFF2D1", "text_wrap": True, "valign": "top",
            "bottom": 1, "bottom_color": "#E7EDF3"})
        status = workbook.add_format({"font_name": "Aptos", "font_size": 10,
            "bg_color": "#F8DEDF", "text_wrap": True, "valign": "top",
            "bottom": 1, "bottom_color": "#E7EDF3"})
        widths = {"english_changed": 14, "english_original": 48, "english_authorized": 48,
            "spanish_draft": 48, "sp_human": 48, "status": 14, "human_note": 34,
            "correction_patches": 42, "review_wrong": 15, "review_misleading": 18,
            "review_dismissive": 18, "review_note": 42, "page": 9, "source_refs": 22,
            "text_class": 18, "level": 10, "source_math": 15,
            "latex_reconstruction": 22, "id": 24, "approve_patch_indices": 22,
            "authorization": 17}
        for col, field in enumerate(fields):
            worksheet.set_column(col, col, widths.get(field, 16))
            worksheet.write_string(0, col, field, header)
        worksheet.set_row(0, 32)
        for number, row in enumerate(rows, start=1):
            worksheet.set_row(number, 66)
            for col, field in enumerate(fields):
                fmt = (status if field in ("status", "authorization") else
                       editable if field in ("sp_human", "human_note", "approve_patch_indices") else base)
                worksheet.write_string(number, col, str(row.get(field, "")), fmt)
        worksheet.autofilter(0, 0, max(1, len(rows)), len(fields)-1)
        if rows:
            cols = {name: i for i, name in enumerate(fields)}
            worksheet.data_validation(1, cols["status"], len(rows), cols["status"],
                {"validate": "list", "source": ["Pending", "Keep sp", "Edit sp"]})
            worksheet.data_validation(1, cols["authorization"], len(rows), cols["authorization"],
                {"validate": "list", "source": ["Pending", "Approved"]})
            green = workbook.add_format({"bg_color": "#DCF2E6", "font_color": "#11633E"})
            amber = workbook.add_format({"bg_color": "#FFF0CF", "font_color": "#805100"})
            for label, fmt in (("Keep sp", green), ("Edit sp", amber)):
                worksheet.conditional_format(1, cols["status"], len(rows), cols["status"],
                    {"type": "text", "criteria": "containing", "value": label, "format": fmt})
            worksheet.conditional_format(1, cols["authorization"], len(rows), cols["authorization"],
                {"type": "text", "criteria": "containing", "value": "Approved", "format": green})
        workbook.close()
        checked, checked_fields = _xlsx_rows(Path(temp_name))
        if checked_fields != list(fields) or checked != [{f: str(r.get(f,"")) for f in fields} for r in rows]:
            raise ValueError("Saved workbook did not retain every source/review value")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text_sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _csv_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader), reader.fieldnames or []


def _patches(row):
    """Read original proposed patches, ignoring any subsequent approval annotations."""
    patches = json.loads(row["correction_patches"])
    if not isinstance(patches, list):
        raise ValueError("correction_patches must be an array")
    return [{"old": p["old"], "new": p["new"]} for p in patches]


def _choices(row, patches, unit_id):
    parts = [v.strip() for v in row["approve_patch_indices"].split(",") if v.strip()]
    if any(not p.isdecimal() for p in parts):
        raise ValueError(f"Patch approvals must be comma-separated 1-based integers: {unit_id}")
    indices = [int(p) for p in parts]
    if (indices != sorted(set(indices)) or any(i < 1 or i > len(patches) for i in indices)):
        raise ValueError(f"Patch indices must be unique, ordered and exist: {unit_id}")
    return indices


def _experimental_translations(run, document, model, units):
    """Map already-paid Spanish to the exact original source unit, never to corrected English."""
    expected = {u["unit_id"]: u for u in units}
    drafts = {}
    for batch in read_jsonl(run / "translate.jsonl"):
        if batch["document_id"] != document or batch["model"] != model:
            continue
        refs = batch.get("source_unit_ids", [])
        for item in batch["items"]:
            # The source is established by the deterministic document-local ID,
            # checked against the corresponding batch's source references and page.
            matches = [u for u in units if u["id"] == item["id"]]
            if len(matches) != 1:
                raise ValueError("Experimental translation has unexpected numeric ID")
            u = matches[0]
            if u["unit_id"] not in refs or int(batch["page"]) != u["page"]:
                raise ValueError("Experimental translation provenance does not match source")
            if u["unit_id"] in drafts:
                raise ValueError("Duplicate experimental translation: " + u["unit_id"])
            drafts[u["unit_id"]] = item["sp"]
    return drafts


def _review_note(item, warnings):
    note = item["rn"]
    findings = item.get("findings", [])
    if findings:
        note += "\nStructured technical findings:\n" + "\n".join(
            f"{v['kind']}: {v['excerpt']} — {v['reason']}" for v in findings)
    if warnings:
        note += "\nExamination warnings (verify manually): " + "; ".join(warnings)
    return note


def prepare(run, document, examine_model, translate_model, out=None):
    """Create (or migrate to) the single production review XLSX; zero API calls."""
    run = Path(run).resolve()
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    if document not in manifest["documents"]:
        raise ValueError(f"Document {document!r} is not part of the given experiment")
    if examine_model not in manifest["models"] or translate_model not in manifest["models"]:
        raise ValueError("Selected models must appear in the completed experiment")
    source, _ = _csv_rows(run / "source_blocks.csv")
    blocks = [r for r in source if r["document_id"] == document and r["eligible"].lower() in ("true", "1")]
    if not blocks:
        raise ValueError("No eligible source units")
    units = [{"unit_id": r["unit_id"], "id": int(r["id"]), "page": int(r["page"]),
              "english": r["english"]} for r in blocks]
    original = {u["id"]: u for u in units}
    responses, warnings = {}, {}
    for batch in read_jsonl(run / "examine.jsonl"):
        if batch["document_id"] != document or batch["model"] != examine_model:
            continue
        for item in batch["items"]:
            if item["id"] not in original or item["id"] in responses:
                raise ValueError("Duplicate or unrecognized examination unit")
            responses[item["id"]] = item
            warnings[item["id"]] = batch.get("assessment_warnings", {}).get(str(item["id"]), [])
    if set(responses) != set(original):
        raise ValueError(f"Examination missing units: {sorted(set(original) - set(responses))}")
    drafts = _experimental_translations(run, document, translate_model, units)
    folder = Path(out).resolve() if out else run / "production" / document
    folder.mkdir(parents=True, exist_ok=True)
    report, evidence_path, metadata = (folder / REVIEW_FILE,
                                       folder / "examination_evidence.json",
                                       folder / "production_manifest.json")
    old_review = folder / "translation_review.csv"
    if report.exists() or ((folder / "authorized.json").exists() and not old_review.exists()):
        raise ValueError("Production XLSX or authorization already exists; never overwrite human work")
    evidence = [{**u, "item": responses[u["id"]], "assessment_warnings": warnings[u["id"]]}
                for u in units]
    if metadata.exists() or evidence_path.exists():
        # Migrate the previously generated six-column authorization file, if present.
        if not (metadata.exists() and evidence_path.exists()):
            raise ValueError("Incomplete previous production preparation; inspect files")
        old_meta = json.loads(metadata.read_text(encoding="utf-8"))
        if (old_meta["source_experiment_id"] != manifest["run_id"]
                or old_meta["document"] != document
                or old_meta["examine_model"] != examine_model
                or old_meta["translate_model"] != translate_model
                or old_meta["evidence_sha256"] != _sha(evidence_path)
                or json.loads(evidence_path.read_text(encoding="utf-8")) != evidence):
            raise ValueError("Existing production evidence differs; refusing to overwrite")
    else:
        save_json(evidence_path, evidence)
        save_json(metadata, {"source_experiment": str(run), "source_experiment_id": manifest["run_id"],
                             "document": document, "examine_model": examine_model,
                             "translate_model": translate_model,
                             "evidence_sha256": _sha(evidence_path)})
    prior = folder / "authorization.csv"
    prior_rows = {}
    if prior.exists():
        previous, fields = _csv_rows(prior)
        if set(fields) != set(OLD_AUTH_COLUMNS) or len(previous) != len(units):
            raise ValueError("Previous authorization file has unexpected structure")
        prior_rows = {r["unit_id"]: r for r in previous}
        if len(prior_rows) != len(units) or set(prior_rows) != {u["unit_id"] for u in units}:
            raise ValueError("Previous authorization contains missing or duplicate units")
    rows = []
    for u in evidence:
        item = u["item"]
        previous = prior_rows.get(u["unit_id"])
        if previous and (previous["english_original"] != u["english"]
                         or previous["page"] != str(u["page"])
                         or json.loads(previous["proposed_patches"]) != item["e"]):
            raise ValueError("Previous authorization conflicts with source: " + u["unit_id"])
        rows.append({"english_changed": "False", "english_original": u["english"],
            "english_authorized": u["english"], "spanish_draft": drafts.get(u["unit_id"], ""),
            "sp_human": "", "status": "Pending", "human_note": "",
            "correction_patches": json.dumps(item["e"], ensure_ascii=False),
            "review_wrong": item["wr"], "review_misleading": item["mi"],
            "review_dismissive": item["di"], "review_note": _review_note(item, u["assessment_warnings"]),
            "page": u["page"], "source_refs": json.dumps([u["unit_id"]]),
            "text_class": "", "level": "", "source_math": item["m"],
            "latex_reconstruction": item["ml"], "id": u["unit_id"],
            "approve_patch_indices": previous["approve_patch_indices"] if previous else "",
            "authorization": previous["authorization"] if previous else "Pending"})
    # One-time migration of the prior combined review CSV, including human-entered edits.
    if old_review.exists():
        saved, saved_fields = _csv_rows(old_review)
        if saved_fields != list(AUTH_COLUMNS) or len(saved) != len(rows):
            raise ValueError("Existing combined CSV has unexpected structure; migration stopped")
        saved_map = {r["id"]: r for r in saved}
        if len(saved_map) != len(rows) or set(saved_map) != {r["id"] for r in rows}:
            raise ValueError("Existing CSV has missing or duplicate source units")
        for new in rows:
            existing = saved_map[new["id"]]
            for field in ("english_original", "correction_patches", "page"):
                if field == "correction_patches":
                    matching = _patches(existing) == json.loads(new[field])
                else:
                    matching = existing[field] == str(new[field])
                if not matching:
                    raise ValueError("Existing review CSV disagrees with original evidence: " + new["id"])
            # Prior review contains valid human edits; retain them in the new workbook.
            new.update(existing)
    _write_review(report, rows)
    if (folder / "authorized.json").exists():
        try:
            _load_authorized(folder)  # Verify locked decisions before deleting the old CSV.
        except Exception:
            report.unlink()  # Keep the original human CSV intact for investigation.
            raise
    if old_review.exists():
        old_review.unlink()  # Remove only after verified XLSX contains all legacy data.
    print(f"Single production review XLSX: {report} ({len(rows)} units; "
          f"{len(drafts)} existing Spanish drafts reused for review; zero API calls)")
    if prior.exists():
        prior.unlink()  # Its approvals have been verified in the new single workbook.
        print("Previous authorization.csv migrated to the single XLSX.")
    print("Approve patch indices and set authorization=Approved in the same XLSX; original drafts are previews until sign-off.")


def _load_review(folder, evidence):
    path = folder / REVIEW_FILE
    rows, fields = _xlsx_rows(path)
    if fields != list(AUTH_COLUMNS) or len(rows) != len(evidence):
        raise ValueError("Production review XLSX has missing/extra columns or rows")
    by_id = {r["id"]: r for r in rows}
    if len(by_id) != len(rows) or set(by_id) != {u["unit_id"] for u in evidence}:
        raise ValueError("Missing, duplicate or unknown source units in production review")
    return rows, by_id


def _approved_units(evidence, by_id):
    approved = []
    for u in evidence:
        row = by_id[u["unit_id"]]
        if (row["authorization"].strip() != "Approved"
                or row["english_original"] != u["english"]
                or row["page"] != str(u["page"])
                or _patches(row) != u["item"]["e"]):
            raise ValueError("Approve or restore original examination evidence: " + u["unit_id"])
        indices = _choices(row, u["item"]["e"], u["unit_id"])
        accepted = [u["item"]["e"][i-1] for i in indices]
        try:
            english = apply_patches(u["english"], accepted)
        except ValueError as exc:
            raise ValueError(f"Approved corrections invalid for {u['unit_id']}: {exc}") from exc
        approved.append({**u, "english_authorized": english, "accepted_patch_indices": indices})
    return approved


def authorize(folder):
    """Lock English approvals from the single XLSX; preserve reviewed Spanish."""
    folder = Path(folder).resolve()
    lock = folder / "authorized.json"
    if lock.exists():
        raise ValueError("English is already authorized; don't overwrite the existing lock")
    manifest = json.loads((folder / "production_manifest.json").read_text(encoding="utf-8"))
    evidence_path = folder / "examination_evidence.json"
    if _sha(evidence_path) != manifest["evidence_sha256"]:
        raise ValueError("Examination evidence changed after production preparation")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    rows, by_id = _load_review(folder, evidence)
    approved = _approved_units(evidence, by_id)
    changed = 0
    for u in approved:
        r = by_id[u["unit_id"]]
        if r["english_authorized"] != r["english_original"] and r["english_authorized"] != u["english_authorized"]:
            raise ValueError("Existing authorized English does not match source: " + u["unit_id"])
        if u["english_authorized"] != u["english"]:
            if r["sp_human"].strip() or r["status"] != "Pending":
                raise ValueError("An edited/approved Spanish review exists for changed English: " + u["unit_id"])
            # The existing experimental draft belongs to the uncorrected English.
            r["spanish_draft"] = ""
            changed += 1
        r["english_authorized"] = u["english_authorized"]
        r["english_changed"] = str(u["english_authorized"] != u["english"])
        chosen = set(u["accepted_patch_indices"])
        r["correction_patches"] = json.dumps(
            [{**p, "approved": i in chosen} for i, p in enumerate(u["item"]["e"], 1)],
            ensure_ascii=False)
    # The JSON lock freezes approval decisions and the underlying examination evidence;
    # Spanish review fields remain independently editable in this very same XLSX.
    _write_review(folder / REVIEW_FILE, rows, AUTH_COLUMNS)
    save_json(lock, {"evidence_sha256": _sha(evidence_path), "units": approved})
    print(f"English authorized: {len(approved)} units, {changed} changed; "
          "existing Spanish retained only for unchanged English. Zero API calls.")


def _load_authorized(folder):
    manifest = json.loads((folder / "production_manifest.json").read_text(encoding="utf-8"))
    evidence_path = folder / "examination_evidence.json"
    locked = json.loads((folder / "authorized.json").read_text(encoding="utf-8"))
    if (_sha(evidence_path) != locked["evidence_sha256"]
            or locked["evidence_sha256"] != manifest["evidence_sha256"]):
        raise ValueError("Original examination evidence changed after sign-off")
    units = locked["units"]
    rows, by_id = _load_review(folder, units)
    updated = _approved_units(units, by_id)
    for a, b in zip(units, updated):
        if (a["unit_id"] != b["unit_id"] or a["english_authorized"] != b["english_authorized"]
                or a["accepted_patch_indices"] != b["accepted_patch_indices"]
                or by_id[a["unit_id"]]["english_authorized"] != a["english_authorized"]
                or by_id[a["unit_id"]]["english_changed"] != str(a["english"] != a["english_authorized"])):
            raise ValueError("English authorization changed after sign-off: " + a["unit_id"])
    return manifest, units, rows, by_id


def _production_translations(folder, manifest, units):
    model = manifest["translate_model"]
    done = {}
    for r in read_jsonl(folder / "translation.jsonl"):
        uid = r["unit_id"]
        if uid in done or r["model"] != model:
            raise ValueError("Duplicate or mismatched saved production translation: " + uid)
        done[uid] = r
    if not set(done) <= {u["unit_id"] for u in units}:
        raise ValueError("Saved translation references an unknown source unit")
    for u in units:
        if u["unit_id"] in done and done[u["unit_id"]]["source_sha256"] != _text_sha(u["english_authorized"]):
            raise ValueError("Saved translation has different authorized English: " + u["unit_id"])
    return done


def translate(folder, cfg_path, live=False, max_cost_usd=None):
    """Reuse paid experimental drafts for unchanged English; charge only for missing/changed units."""
    folder = Path(folder).resolve()
    manifest, units, _, by_id = _load_authorized(folder)
    config = load_config(Path(cfg_path))
    model = manifest["translate_model"]
    prices = config["pricing_usd_per_million_tokens"].get(model)
    if prices is None:
        raise ValueError("Selected translation model lacks configured token prices")
    done = _production_translations(folder, manifest, units)
    origin = Path(manifest["source_experiment"])
    drafts = _experimental_translations(origin, manifest["document"], model, units)
    # No added records needed for reused translations: provenance remains in the experiment.
    reused = {u["unit_id"]: drafts[u["unit_id"]] for u in units
              if u["english_authorized"] == u["english"] and u["unit_id"] in drafts
              and u["unit_id"] not in done}
    failed = {r["unit_id"] for r in read_jsonl(folder / "translation_failed.jsonl")} - done.keys()
    pending = [u for u in units if u["unit_id"] not in done
               and u["unit_id"] not in reused and u["unit_id"] not in failed]
    estimates = [(len(common.PROMPTS["translate"]) + len(u["english_authorized"]) + 300) // 4 + 100
                 for u in pending]
    estimate = sum(cost((n, 220), prices) for n in estimates)
    print(json.dumps({"model": model, "total_units": len(units),
                      "existing_experimental_drafts_reused": len(reused),
                      "saved_new_translations": len(done), "pending_paid_requests": len(pending),
                      "blocked_failed": sorted(failed),
                      "estimated_incremental_usd": round(estimate, 6)}, indent=2))
    if not live:
        print("DRY RUN: zero paid API calls")
        return
    if max_cost_usd is None or max_cost_usd <= 0:
        raise ValueError("Live translation requires --live and positive --max-cost-usd")
    if failed:
        raise ValueError("Previously failed translations require manual inspection; no automatic retry")
    if estimate > max_cost_usd:
        raise ValueError("Estimated cost exceeds incremental cost cap")
    if not pending:
        print("All translations are already available; zero API calls")
        return
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("Set OPENAI_API_KEY before live execution")
    from openai import OpenAI
    client = OpenAI(max_retries=0)
    spent = 0.0
    for u, n in zip(pending, estimates):
        reserve = cost((n * 2, 1000), prices)
        if spent + reserve > max_cost_usd:
            print("STOP: remaining budget cannot reserve another translation")
            break
        print("REQUEST", u["unit_id"], flush=True)
        try:
            start = time.perf_counter()
            resp = client.responses.create(model=model, instructions=common.PROMPTS["translate"],
                input=("Translate exactly this one authorized English unit; return its unchanged numeric ID "
                       f"{u['id']}.\n" + json.dumps({"units": [{"id": u["id"], "x": u["english_authorized"]}]},
                                                     ensure_ascii=False)),
                text={"format": {"type": "json_object"}}, max_output_tokens=1000,
                **({"reasoning": {"effort": "low"}} if model == "gpt-5-nano" else {}), store=False)
        except Exception as exc:
            append_jsonl(folder / "translation_failed.jsonl", {"unit_id": u["unit_id"], "error": str(exc)})
            print("STOP: request failed; inspect translation_failed.jsonl before retrying")
            break
        usage = resp.usage
        in_tok, out_tok = usage.input_tokens if usage else 0, usage.output_tokens if usage else 0
        cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
        observed = cost((in_tok, out_tok), prices, cached)
        spent += observed
        record = {"unit_id": u["unit_id"], "model": model, "api_response_id": resp.id,
                  "observed_cost_usd": round(observed, 8), "elapsed_seconds": round(time.perf_counter()-start, 3),
                  "input_tokens": in_tok, "output_tokens": out_tok,
                  "time_utc": datetime.now(timezone.utc).isoformat()}
        append_jsonl(folder / "translation_usage.jsonl", record)
        try:
            output = json.loads(resp.output_text)
            checked = checked_output({"stage": "translate", "units": [{"id": u["id"]}]}, output)
        except (ValueError, TypeError, KeyError) as exc:
            append_jsonl(folder / "translation_failed.jsonl",
                         {**record, "error": str(exc), "raw_output": resp.output_text})
            print("STOP: structurally invalid paid translation; no automatic retry")
            break
        append_jsonl(folder / "translation.jsonl", {**record, "sp": checked[0]["sp"],
                     "source_sha256": _text_sha(u["english_authorized"])})
        print(f"COMPLETED {u['unit_id']}; invocation cost ${spent:.6f}", flush=True)
    print(f"Incremental observed API cost: ${spent:.6f}")


def export(folder):
    """Fill missing Spanish drafts in the *same* review XLSX without erasing human work."""
    folder = Path(folder).resolve()
    manifest, units, rows, by_id = _load_authorized(folder)
    done = _production_translations(folder, manifest, units)
    drafts = _experimental_translations(Path(manifest["source_experiment"]), manifest["document"],
                                        manifest["translate_model"], units)
    added = 0
    for u in units:
        uid = u["unit_id"]
        spanish = (done[uid]["sp"] if uid in done else
                   drafts.get(uid) if u["english_authorized"] == u["english"] else None)
        if spanish is None:
            raise ValueError("Missing translation for authorized source: " + uid)
        row = by_id[uid]
        if row["spanish_draft"] and row["spanish_draft"] != spanish:
            raise ValueError("Existing human-review draft differs; refusing to overwrite: " + uid)
        if not row["spanish_draft"]:
            row["spanish_draft"] = spanish
            added += 1
    if added:
        _write_review(folder / REVIEW_FILE, rows, AUTH_COLUMNS)
    print(f"Production review XLSX: {folder / REVIEW_FILE} "
          f"({added} missing drafts filled; zero API calls; existing human review preserved)")
