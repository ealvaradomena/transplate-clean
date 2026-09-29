"""Single consolidated offline acceptance test: PDFs, planning, output rules, both CSV pathways."""
import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace, ModuleType

import pymupdf as fitz
import transplate
from scripts import common


class OfflineAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = {"documents": {}, "models": ["test-model-1", "test-model-2"],
                    "random_seed": 7,
                    "output_directory": "output", "pricing_usd_per_million_tokens": {
                        "test-model-1": {"input": 1, "output": 2},
                        "test-model-2": {"input": 2, "output": 4}}}
        for name in ("peru", "france"):
            p = self.root / (name + ".pdf")
            pdf = fitz.open()
            for page in (1, 2):
                docpage = pdf.new_page()
                docpage.insert_text((40, 60), f"{name.title()} report, page {page}: first substantive paragraph has several words.")
                docpage.insert_text((40, 100), "Second substantive paragraph with 25% growth in 2024.")
                docpage.insert_text((40, 140), "Third substantive paragraph about education and water systems.")
            pdf.save(p)
            self.cfg["documents"][name] = p.as_uri()
        self.cfg_path = self.root / "config.json"
        self.cfg_path.write_text(json.dumps(self.cfg), encoding="utf-8")
        self.old_root = common.ROOT
        common.ROOT = self.root

    def tearDown(self):
        common.ROOT = self.old_root
        self.tmp.cleanup()

    def test_historical_review_without_mutating_experiment(self):
        import shutil
        from scripts.dashboard import build
        from scripts.experiment import prepare
        new, _, blocks, _ = prepare(self.cfg)
        old = self.root / "historical-run"
        old.mkdir()
        manifest = json.loads((new / "manifest.json").read_text(encoding="utf-8"))
        manifest["run_id"] = "historical-run"
        (old / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        shutil.copyfile(new / "source_blocks.csv", old / "source_blocks.csv")
        block = next(b for b in blocks if b["eligible"])
        base = {"model": self.cfg["models"][0], "document_id": block["document_id"],
                "page": block["page"], "source_unit_ids": [block["unit_id"]]}
        examination = {**base, "items": [{"id": block["id"], "v": "T", "m": "F",
                     "ml": "F", "e": [], "wr": "F", "mi": "F", "di": "F", "rn": "OK"}]}
        translation = {**base, "items": [{"id": block["id"], "sp": "Texto de prueba."}]}
        for stage, record in (("examine", examination), ("translate", translation)):
            (old / f"{stage}.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            cases = build(new, old)
        self.assertEqual(len(cases), 24)  # Every unit appears even with absent results.
        matching = [c for c in cases if c["unit_id"] == block["unit_id"]]
        self.assertEqual(len(matching), 2)
        self.assertTrue(all(len(c["options"]) == 1 and c["missing_count"] == 1
                            for c in matching))
        self.assertFalse((new / "examine.jsonl").exists())
        self.assertFalse((new / "translate.jsonl").exists())
        with (new / "evaluation" / "dashboard_model_key_all.csv").open(
                encoding="utf-8-sig", newline="") as f:
            mapping = list(csv.DictReader(f))
        imported = [r for r in mapping if r["origin_run_id"] == "historical-run"]
        self.assertEqual(len(imported), 2)
        self.assertTrue(all(r["historical_conditions"] == "True" for r in imported))
        # The reviewer HTML must never reveal model identities.
        html = (new / "evaluation" / "blind_dashboard_all.html").read_text(encoding="utf-8")
        self.assertNotIn('"model": "test-model-1"', html)
        self.assertIn("Historical comparison", html)
        (old / "source_blocks.csv").write_text("incompatible\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            build(new, old)

    def test_both_workflows_without_paid_calls(self):
        out, sources, blocks, samples = transplate.prepare(self.cfg)
        self.assertEqual(set(samples), {"peru", "france"})
        self.assertEqual([len(s) for s in samples.values()], [6, 6])
        self.assertTrue(all(Path(s["path"]).read_bytes().startswith(b"%PDF") for s in sources.values()))
        tasks = transplate.jobs(self.cfg, blocks, samples)
        self.assertEqual(len(tasks), 16)  # 2 models x 2 docs x 2 pages x 2 stages
        self.assertEqual({t["stage"] for t in tasks}, {"examine", "translate"})
        plan = transplate.preflight(self.cfg, out, tasks)
        self.assertEqual(plan["incremental_requests"], 16)
        self.assertGreater(plan["by_model"]["test-model-1"]["cost_usd_approx"], 0)
        # Synthetic model outputs only: exercise validation and each result format without API spend.
        for t in tasks:
            if t["stage"] == "examine":
                response = {"items": [{"id": b["id"], "v": "T", "m": "F", "ml": "F", "e": [],
                                       "wr": "F", "mi": "F", "di": "F", "rn": "OK", "findings": []} for b in t["units"]]}
            else:
                response = {"items": [{"id": b["id"], "sp": "Traducción simulada de " + b["unit_id"]}
                                      for b in t["units"]]}
            validated = transplate.checked_output(t, response)
            transplate.append_jsonl(out / f"{t['stage']}.jsonl", {"key": t["key"], "model": t["model"],
                                   "document_id": t["document_id"], "page": t["page"],
                                   "source_unit_ids": [x["unit_id"] for x in t["units"]], "items": validated})
        self.assertEqual(transplate.preflight(self.cfg, out, tasks)["incremental_requests"], 0)
        with contextlib.redirect_stdout(io.StringIO()):
            transplate.template(type("Args", (), {"run": str(out), "translations": None, "out": None})())
        path = out / "evaluation" / "translation_evaluation.csv"
        with path.open(encoding="utf-8-sig", newline="") as f:
            scored = list(csv.DictReader(f))
        with (out / "evaluation" / "model_key.csv").open(encoding="utf-8-sig", newline="") as f:
            key = list(csv.DictReader(f))
        self.assertEqual(len(scored), 24)  # 2 docs x 6 complete eligible blocks x 2 models
        self.assertEqual(len(key), len(scored))
        # Full-mode dashboard is independent of the binary CSV template, with exact-match dedup.
        from scripts.dashboard import build
        with contextlib.redirect_stdout(io.StringIO()):
            full_cases = build(out)
        self.assertEqual(len(full_cases), 24)  # 12 units, both tasks.
        self.assertTrue(all(len(c["options"]) == 1 for c in full_cases
                            if c["stage"] == "examine"))  # Both models supplied identical flags and notes.
        with (out / "evaluation" / "dashboard_model_key_all.csv").open(encoding="utf-8-sig", newline="") as f:
            attribution = list(csv.DictReader(f))
        self.assertEqual(len(attribution), 48)  # 2 stages x 12 units x 2 models.
        blind = (out / "evaluation" / "blind_dashboard_all.html").read_text(encoding="utf-8")
        self.assertNotIn('"model": "test-model-1"', blind)
        self.assertIn("comparative-rank-ties-allowed", blind)
        self.assertNotIn("model", scored[0])
        self.assertEqual(set(x["model"] for x in key), set(self.cfg["models"]))
        self.assertTrue(all(r["semantic_fidelity"] == "" for r in scored))
        # Conservative corrections are explicit, deterministic, and never technical rewrites.
        examine_task = next(t for t in tasks if t["stage"] == "examine")
        clean = [{"id": b["id"], "v": "T", "m": "F", "ml": "F", "e": [],
                  "wr": "F", "mi": "F", "di": "F", "rn": "OK", "findings": []} for b in examine_task["units"]]
        clean[0]["ml"] = "T"
        transplate.checked_output(examine_task, {"items": clean})
        from scripts.experiment import assessment_warnings
        self.assertIn("LaTeX normalization", assessment_warnings(examine_task, clean)[str(clean[0]["id"])][0])
        clean[0]["ml"] = "F"
        clean[0]["wr"] = "T"
        clean[0]["rn"] = "Clear arithmetic error"  # Comments only: no proofreading patch
        clean[0]["findings"] = [{"kind":"wr", "excerpt": examine_task["units"][0]["english"][:20], "reason":"Clear arithmetic error"}]
        transplate.checked_output(examine_task, {"items": clean})
        clean[0]["rn"] = "OK"
        transplate.checked_output(examine_task, {"items": clean})
        self.assertTrue(assessment_warnings(examine_task, clean)[str(clean[0]["id"])])
        self.assertEqual(transplate.apply_patches("A typo typo.", [{"old": "typo typo", "new": "typo"}]), "A typo.")
        with self.assertRaises(ValueError):
            transplate.apply_patches("typo and typo", [{"old": "typo", "new": "word"}])
        with self.assertRaises(ValueError):
            transplate.apply_patches("x = 2", [{"old": "x", "new": "x"}])
        with self.assertRaises(ValueError):
            transplate.checked_output(tasks[0], {"items": []})
        # Repeat CSV generation: same IDs and order for reproducibility.
        before = path.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            transplate.template(type("Args", (), {"run": str(out), "translations": None, "out": None})())
        self.assertEqual(path.read_bytes(), before)
        # Standalone B: independent supplied CSV, no A artifacts needed.
        standalone = self.root / "independent.csv"
        transplate.write_csv(standalone, [{"document_id": "other", "page": 9, "unit_id": "u3",
                                        "source_english": "Hello world", "translation_spanish": "Hola mundo"}],
                             ("document_id", "page", "unit_id", "source_english", "translation_spanish"))
        with contextlib.redirect_stdout(io.StringIO()):
            transplate.template(type("Args", (), {"run": None, "translations": str(standalone),
                                                 "out": str(self.root / "custom_review")})())
        self.assertTrue((self.root / "custom_review" / "translation_evaluation.csv").exists())

    def test_multiple_independent_issues_and_dashboard(self):
        from scripts.dashboard import build
        out, _, blocks, samples = transplate.prepare(self.cfg)
        task = next(t for t in transplate.jobs(self.cfg, blocks, samples) if t["stage"] == "examine")
        unit = task["units"][0]
        # The validator accepts any number of distinct independently evidenced findings.
        source = unit["english"]
        old_a, old_b = "substantive", "paragraph"
        self.assertIn(old_a, source)
        self.assertIn(old_b, source)
        item = {"id": unit["id"], "v": "T", "m": "F", "ml": "F",
                "e": [{"old": old_a, "new": "material"}, {"old": old_b, "new": "passage"}],
                "wr": "T", "mi": "T", "di": "F", "rn": "Two separate technical issues",
                "findings": [{"kind": "wr", "excerpt": old_a, "reason": "Claim is unsupported"},
                             {"kind": "wr", "excerpt": old_b, "reason": "Separate technical problem"},
                             {"kind": "mi", "excerpt": "several words", "reason": "Misleading implication"}]}
        checked = transplate.checked_output({**task, "units": [unit]}, {"items": [item]})
        self.assertEqual(len(checked[0]["e"]), 2)
        self.assertEqual(len(checked[0]["findings"]), 3)
        incorrect = {**item, "findings": item["findings"][:-1]}
        transplate.checked_output({**task, "units": [unit]}, {"items": [incorrect]})
        from scripts.experiment import assessment_warnings
        self.assertTrue(any("mi flag" in w for w in
                            assessment_warnings({**task, "units": [unit]}, [incorrect])[str(unit["id"])]))
        incorrect = {**item, "findings": [{**item["findings"][0], "excerpt": "NOT IN SOURCE"}]}
        transplate.checked_output({**task, "units": [unit]}, {"items": [incorrect]})
        self.assertTrue(any("verbatim source excerpt" in w for w in
                            assessment_warnings({**task, "units": [unit]}, [incorrect])[str(unit["id"])]))
        # A valid same-result peer is deduplicated without losing any nested issues.
        for model in self.cfg["models"]:
            transplate.append_jsonl(out / "examine.jsonl", {"model": model, "document_id": task["document_id"],
                                     "page": task["page"], "source_unit_ids": [unit["unit_id"]],
                                     "items": checked})
        with contextlib.redirect_stdout(io.StringIO()):
            cases = build(out)
        case = next(c for c in cases if c["stage"] == "examine" and c["unit_id"] == unit["unit_id"])
        self.assertEqual(len(case["options"]), 1)
        self.assertEqual(case["options"][0]["qualified_count"], 2)
        self.assertEqual(len(case["options"][0]["item"]["findings"]), 3)
        dashboard = (out / "evaluation" / "blind_dashboard_all.html").read_text(encoding="utf-8")
        self.assertIn("Individual correction scores", dashboard)
        self.assertIn("individual_issues:issues.filter", dashboard)
        self.assertNotIn('"model": "test-model-1"', dashboard)

    def test_private_comparison_keeps_unit_and_issue_metrics_separate(self):
        from scripts.compare import compare
        review = self.root / "review.json"
        review.write_text(json.dumps({"run_id": "new-run",
            "examination": [{"case_id":"case1", "anonymous_id":"shared", "criterion":"editorial", "score":0}],
            "individual_issues": [{"case_id":"case1", "anonymous_id":"shared", "issue_type":"correction",
                                    "category":"proofreading", "score":1}],
            "translation_rankings": [{"case_id":"case2", "anonymous_id":"t1", "rank":1}]}),encoding="utf-8")
        key_path = self.root / "model_key.csv"
        with key_path.open("w",encoding="utf-8-sig",newline="") as f:
            writer=csv.DictWriter(f,fieldnames=["case_id","anonymous_id","model","machine_valid","origin_run_id"])
            writer.writeheader()
            for model in ("alpha","beta"):
                writer.writerow({"case_id":"case1","anonymous_id":"shared","model":model,
                                 "machine_valid":True,"origin_run_id":"new-run"})
            writer.writerow({"case_id":"case2","anonymous_id":"t1","model":"alpha",
                             "machine_valid":True,"origin_run_id":"new-run"})
            writer.writerow({"case_id":"case1","anonymous_id":"shared","model":"rejected",
                             "machine_valid":False,"origin_run_id":"old-run"})
        with contextlib.redirect_stdout(io.StringIO()):
            summary=compare(review,key_path)
        lookup={(r["model"],r["measure"]):r for r in summary}
        self.assertEqual(lookup[("alpha","unit")]["result"],0)
        self.assertEqual(lookup[("alpha","proposed_issue")]["result"],1)
        self.assertEqual(lookup[("beta","proposed_issue")]["result"],1)
        self.assertEqual(lookup[("alpha","translation")]["result"],1)
        self.assertFalse(any(r["model"]=="rejected" for r in summary))
        self.assertIn('mean competition rank',review.with_name('review-private-comparison.html').read_text())

    def test_private_comparison_autodiscovers_inputs_and_keeps_scores_independent(self):
        """Offline end-to-end reporting uses local responses, no model/API calls."""
        from scripts.compare import run_comparison
        run = self.root / "test-run"
        directory = run / "evaluation"
        directory.mkdir(parents=True)
        for stage in ("examine", "translate"):
            (run / (stage + ".jsonl")).write_text(json.dumps({
                "model": "model-A", "document_id": "france", "source_unit_ids": ["france:p1:b3"],
                "items": [{"id": 3}]}) + "\n", encoding="utf-8")
        rows = [
            ("examine", "e1", "model-A"),
            ("translate", "t1", "model-A"),
        ]
        with (directory / "dashboard_model_key_france.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["case_id", "anonymous_id", "model", "machine_valid", "origin_run_id"])
            writer.writeheader()
            for stage, anonymous, model in rows:
                writer.writerow({"case_id": stage + ":france:france:p1:b3", "anonymous_id": anonymous,
                                 "model": model, "machine_valid": True, "origin_run_id": "test-run"})
        review = {"schema_version": "transplate-full-dashboard-1", "run_id": "test-run",
                  "scope": "france", "complete": True, "export_type": "final_evaluation",
                  "counts": {"examination": {"completed": 2, "required": 2},
                             "translation": {"completed": 1, "required": 1}},
                  "examination": [{"case_id": "examine:france:france:p1:b3", "anonymous_id": "e1",
                                   "document_id": "france", "unit_id": "france:p1:b3", "criterion": "editorial", "score": 0}],
                  "individual_issues": [{"case_id": "examine:france:france:p1:b3", "anonymous_id": "e1",
                                         "document_id": "france", "unit_id": "france:p1:b3",
                                         "issue_type": "correction", "category": "proofreading", "issue_index": 1,
                                         "score": 1}],
                  "translation_rankings": [{"case_id": "translate:france:france:p1:b3", "anonymous_id": "t1",
                                            "document_id": "france", "unit_id": "france:p1:b3", "rank": 1}]}
        export = directory / "transplate-test-run-evaluation.json"
        export.write_text(json.dumps(review), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            results = run_comparison(run)
        measures = {(x["measure"], x["criterion"]): x["result"] for x in results}
        self.assertEqual(measures[("unit", "editorial")], 0)
        self.assertEqual(measures[("proposed_issue", "correction:proofreading")], 1)
        self.assertEqual(measures[("translation", "rank")], 1)
        self.assertTrue((directory / "private_comparison_france.html").is_file())
        with (directory / "private_comparison_france-detail.csv").open(encoding="utf-8-sig") as file:
            self.assertEqual(len(list(csv.DictReader(file))), 3)
        # No silent comparison if the exported evaluation does not match the run.
        review["run_id"] = "another-run"
        export.write_text(json.dumps(review), encoding="utf-8")
        with self.assertRaises(ValueError):
            run_comparison(run)

    def test_mocked_full_live_execution_and_budget(self):
        """Exercise the actual live orchestration with zero real API/network calls."""
        class FakeResponses:
            def create(self, **kw):
                incoming = json.loads(kw["input"].split("\n", 1)[1])
                expected = [b["id"] for b in incoming["units"]]
                self.outer_boundaries.append((expected, kw["input"]))
                if "Translate English text" in kw["instructions"]:
                    data = {"items": [{"id": b["id"], "sp": "Texto en español"}
                                      for b in incoming["units"]]}
                else:
                    fmt = kw["text"]["format"]
                    self.assert_schema(fmt, expected)
                    data = {"units": {str(b["id"]): {"v": "T", "m": "F", "ml": "F", "e": [],
                                       "wr": "F", "mi": "F", "di": "F", "rn": "OK", "findings": []}
                                      for b in incoming["units"]}}
                self.called += 1
                return SimpleNamespace(output_text=json.dumps(data), usage=SimpleNamespace(
                    input_tokens=125, output_tokens=85,
                    output_tokens_details=SimpleNamespace(reasoning_tokens=0)),
                    id=f"fake_{self.called}", model=kw["model"])
            def assert_schema(self, fmt, expected):
                assert fmt["type"] == "json_schema" and fmt["strict"] is True
                unit = fmt["schema"]["properties"]["units"]
                assert unit["additionalProperties"] is False
                assert unit["required"] == list(map(str, expected))
                assert set(unit["properties"]) == set(map(str, expected))
            called = 0
            outer_boundaries = []

        responses = FakeResponses()
        fake = SimpleNamespace(responses=responses)
        stub = ModuleType("openai")
        stub.OpenAI = lambda **kw: fake
        with patch.dict("sys.modules", {"openai": stub}), patch.dict("os.environ", {"OPENAI_API_KEY": "fake_test_key"}):
            with contextlib.redirect_stdout(io.StringIO()):
                transplate.main(["experiment", "--config", str(self.cfg_path), "--live", "--max-cost-usd", "1"])
                transplate.main(["experiment", "--config", str(self.cfg_path), "--live", "--max-cost-usd", "1"])
        self.assertEqual(responses.called, 16)  # Second run reused results: no repeated paid calls.
        self.assertTrue(all(f"{json.dumps(ids)}" in message and
                            (f"exactly these {len(ids)} source-ID keys" in message or
                             f"exactly {len(ids)} items" in message)
                            for ids, message in responses.outer_boundaries))
        run = next((self.root / "output" / "runs").iterdir())
        self.assertEqual(len(transplate.read_jsonl(run / "usage.jsonl")), 16)
        self.assertEqual(len(transplate.read_jsonl(run / "translate.jsonl")), 8)
        self.assertFalse((run / "parse.jsonl").exists())
        self.assertFalse((run / "failed.jsonl").exists())

    def test_prompt_separates_proofreading_from_technical_findings(self):
        # Regression for the actual failed response: a spelling fix was also
        # incorrectly emitted as a substantive technical finding.
        from scripts.experiment import PROMPTS
        prompt = PROMPTS["examine"]
        self.assertIn("EXCLUSIVELY in e", prompt)
        self.assertIn('"Digitialisation" to "Digitalisation"', prompt)
        self.assertIn("never create a finding merely to restate", prompt)

    def test_request_boundaries_reject_extra_placeholder_items(self):
        from scripts.experiment import request_text
        out, _, blocks, samples = transplate.prepare(self.cfg)
        task = next(t for t in transplate.jobs(self.cfg, blocks, samples) if t["stage"] == "examine")
        # A deliberate gap reproduces the observed nonconsecutive-ID failure.
        task = {**task, "units": [
            {**task["units"][0], "id": 1},
            {**task["units"][1], "id": 2},
            {**task["units"][2], "id": 3},
            {**task["units"][0], "id": 5},
        ]}
        request = request_text(task)
        self.assertIn("exactly these 4 source-ID keys", request)
        self.assertIn("[1, 2, 3, 5]", request)
        self.assertIn("Do not renumber, add, or invent keys or items", request)
        good = [{"id": b["id"], "v": "T", "m": "F", "ml": "F", "e": [],
                 "wr": "F", "mi": "F", "di": "F", "rn": "OK", "findings": []}
                for b in task["units"]]
        self.assertEqual(len(transplate.checked_output(task, {"items": good})), 4)
        from scripts.experiment import examination_format, decode_examination
        fmt = examination_format(task)
        schema = fmt["schema"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertTrue(fmt["strict"])
        self.assertEqual(schema["required"], ["units"])
        self.assertFalse(schema["additionalProperties"])
        keyed = schema["properties"]["units"]
        self.assertFalse(keyed["additionalProperties"])
        self.assertEqual(keyed["required"], ["1", "2", "3", "5"])
        self.assertEqual(set(keyed["properties"]), {"1", "2", "3", "5"})
        self.assertFalse(any("id" in item["properties"] for item in keyed["properties"].values()))
        for item in keyed["properties"].values():
            self.assertFalse(item["additionalProperties"])
            self.assertEqual(set(item["required"]), set(item["properties"]))
            for array_field in ("e", "findings"):
                nested = item["properties"][array_field]["items"]
                self.assertFalse(nested["additionalProperties"])
                self.assertEqual(set(nested["required"]), set(nested["properties"]))
        wire = {"units": {str(item["id"]): {k: v for k, v in item.items() if k != "id"} for item in good}}
        self.assertEqual(decode_examination(task, wire), {"items": good})
        # The previously observed response [30, 31, 32] must never be truncated or accepted.
        wire["units"]["4"] = wire["units"]["1"]
        with self.assertRaisesRegex(ValueError, "unexpected source IDs"):
            decode_examination(task, wire)
        del wire["units"]["4"]
        del wire["units"]["5"]
        with self.assertRaisesRegex(ValueError, "missing"):
            decode_examination(task, wire)

        extra = {**good[0], "id": 4, "v": "F"}
        with self.assertRaisesRegex(ValueError, "missing, duplicate, or unknown numeric ids"):
            transplate.checked_output(task, {"items": good + [extra]})

    def test_explicit_skip_preserves_failure_and_completed_calls(self):
        from scripts.experiment import remaining_tasks
        out, _, blocks, samples = transplate.prepare(self.cfg)
        tasks = transplate.jobs(self.cfg, blocks, samples)
        completed, failed = tasks[0]["key"], tasks[1]["key"]
        transplate.append_jsonl(out / "examine.jsonl", {"key": completed})
        transplate.append_jsonl(out / "failed.jsonl", {"key": failed, "error": "invalid patch"})
        ordinary = transplate.preflight(self.cfg, out, tasks)
        self.assertEqual(ordinary["incremental_requests"], 15)
        self.assertEqual(ordinary["blocked_failed_requests"], [failed])
        skipped = transplate.preflight(self.cfg, out, tasks, skip_failed=True)
        self.assertEqual(skipped["incremental_requests"], 14)
        self.assertEqual(skipped["skipped_failed_requests"], [failed])
        self.assertNotIn(completed, [t["key"] for t in remaining_tasks(tasks, out, True)])
        self.assertNotIn(failed, [t["key"] for t in remaining_tasks(tasks, out, True)])
        self.assertEqual(len(transplate.read_jsonl(out / "failed.jsonl")), 1)

    def test_recover_paid_response_and_grade_semantic_errors_offline(self):
        """Replay both real-world failure patterns without forgiving missing IDs."""
        from scripts.experiment import assessment_warnings, recover_failed
        out, _, blocks, samples = transplate.prepare(self.cfg)
        tasks = transplate.jobs(self.cfg, blocks, samples)
        task = next(t for t in tasks if t["stage"] == "examine")
        units = task["units"]
        response = {"units": {str(u["id"]): {
            "v": "T", "m": "F", "ml": "F", "e": [], "wr": "F", "mi": "F", "di": "F",
            "rn": "OK", "findings": []} for u in units}}
        # The actual paid failure was ml=T/m=F on a spelling-only patch.
        first = response["units"][str(units[0]["id"])]
        first["ml"] = "T"
        first["e"] = [{"old": "made-up typo", "new": "corrected"}]
        # The earlier paid failure had a spelling correction repeated as a wr finding,
        # with wr=F and rn=OK. It is a human-assessable model error, not bad JSON.
        second = response["units"][str(units[1]["id"])]
        excerpt = units[1]["english"][:18]
        second["findings"] = [{"kind": "wr", "excerpt": excerpt,
                               "reason": "Spelling error (incorrectly classified as technical)"}]
        transplate.append_jsonl(out / "failed.jsonl", {
            "key": task["key"], "error": "historical strict-semantic rejection",
            "raw_output": json.dumps(response), "usage": {"observed_cost_usd": 0.0004}})
        before = (out / "failed.jsonl").read_bytes()
        recovered, excluded = recover_failed(out, tasks)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(excluded, [])
        self.assertEqual((out / "failed.jsonl").read_bytes(), before)
        rows = transplate.read_jsonl(out / "examine.jsonl")
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["recovered_from_failure"])
        self.assertEqual(len(rows[0]["items"]), len(units))
        self.assertIn("LaTeX normalization", rows[0]["assessment_warnings"][str(units[0]["id"])][0])
        self.assertTrue(any("wr flag" in x for x in
                            rows[0]["assessment_warnings"][str(units[1]["id"])]))
        self.assertEqual(recover_failed(out, tasks), ([], []))  # Idempotent, no re-billing.
        with contextlib.redirect_stdout(io.StringIO()) as capture:
            transplate.main(["recover", "--config", str(self.cfg_path), "--run", str(out)])
        self.assertIn("0 recovered", capture.getvalue())
        with self.assertRaisesRegex(ValueError, "different experimental inputs"):
            transplate.main(["recover", "--config", str(self.cfg_path),
                            "--run", str(self.root / "unrelated-run")])
        self.assertNotIn(task["key"], transplate.preflight(self.cfg, out, tasks)["blocked_failed_requests"])
        from scripts.dashboard import build
        with contextlib.redirect_stdout(io.StringIO()):
            cases = build(out)
        case = next(c for c in cases if c["stage"] == "examine"
                    and c["unit_id"] == units[0]["unit_id"])
        self.assertTrue(case["options"][0]["assessment_warnings"])
        self.assertEqual(case["options"][0]["qualified_count"], 1)
        private = (out / "evaluation" / "dashboard_model_key_all.csv").read_text(encoding="utf-8-sig")
        self.assertIn("assessment_warnings", private)
        public = (out / "evaluation" / "blind_dashboard_all.html").read_text(encoding="utf-8")
        self.assertIn("Review these model-reported inconsistencies", public)
        self.assertNotIn('"model": "test-model-1"', public)

    def test_hard_failure_keeps_running_without_auto_retry(self):
        """A truly missing-ID response cannot destroy other paid responses in the batch run."""
        from scripts.experiment import recover_failed
        class FakeResponses:
            def __init__(self):
                self.called = 0
                self.keys = []
            def create(self, **kwargs):
                self.called += 1
                incoming = json.loads(kwargs["input"].split("\n", 1)[1])
                ids = [u["id"] for u in incoming["units"]]
                self.keys.append(tuple(ids))
                if kwargs["text"]["format"]["type"] == "json_schema":
                    data = {"units": {str(i): {"v": "T", "m": "F", "ml": "F",
                                        "e": [], "wr": "F", "mi": "F", "di": "F", "rn": "OK",
                                        "findings": []} for i in ids}}
                    if self.called == 1:  # Semantic error: retain and expose for scoring.
                        data["units"][str(ids[0])]["ml"] = "T"
                    if self.called == 3:  # Structural error: missing unit, diagnostic only.
                        data["units"].pop(str(ids[-1]))
                else:
                    data = {"items": [{"id": i, "sp": "Texto de prueba"} for i in ids]}
                return SimpleNamespace(output_text=json.dumps(data), usage=SimpleNamespace(
                    input_tokens=100, output_tokens=50,
                    output_tokens_details=SimpleNamespace(reasoning_tokens=0)),
                    id=f"fake_{self.called}", model=kwargs["model"])
        responses = FakeResponses()
        stub = ModuleType("openai")
        stub.OpenAI = lambda **kw: SimpleNamespace(responses=responses)
        with patch.dict("sys.modules", {"openai": stub}), patch.dict("os.environ", {"OPENAI_API_KEY": "test"}):
            with contextlib.redirect_stdout(io.StringIO()):
                transplate.main(["experiment", "--config", str(self.cfg_path),
                                "--live", "--max-cost-usd", "1"])
        out = next((self.root / "output" / "runs").iterdir())
        self.assertEqual(responses.called, 16)  # No early abort; no paid retry.
        self.assertEqual(len(transplate.read_jsonl(out / "failed.jsonl")), 1)
        self.assertEqual(len(transplate.read_jsonl(out / "usage.jsonl")), 16)
        self.assertEqual(len(transplate.read_jsonl(out / "examine.jsonl")), 7)
        self.assertEqual(len(transplate.read_jsonl(out / "translate.jsonl")), 8)
        self.assertTrue(any(r["assessment_warnings"] for r in
                            transplate.read_jsonl(out / "examine.jsonl")))
        self.assertEqual(transplate.preflight(self.cfg, out,
            transplate.jobs(self.cfg, transplate.prepare(self.cfg)[2], transplate.prepare(self.cfg)[3]))[
                "incremental_requests"], 1)
        with self.assertRaises(ValueError):
            transplate.checked_output({"stage": "examine", "units": [{"id": 1}]},
                                     {"items": [{"id": 1}, {"id": 2}]})

    def test_production_authorization_and_review_xlsx(self):
        from scripts import production
        from scripts.experiment import prepare
        from scripts.common import append_jsonl
        from hashlib import sha256
        out, _, blocks, _ = prepare(self.cfg)
        eligible = [b for b in blocks if b["eligible"] and b["document_id"] == "france"]
        for b in eligible:
            patch = [{"old": "substantive", "new": "material"}] if "substantive" in b["english"] else []
            append_jsonl(out / "examine.jsonl", {
                "model": "test-model-1", "document_id": "france", "page": b["page"],
                "items": [{"id": b["id"], "v": "T", "m": "F", "ml": "F", "e": patch,
                           "wr": "F", "mi": "F", "di": "F", "rn": "OK", "findings": []}]})
            append_jsonl(out / "translate.jsonl", {
                "model": "test-model-2", "document_id": "france", "page": b["page"],
                "source_unit_ids": [b["unit_id"]],
                "items": [{"id": b["id"], "sp": "Existing Spanish " + b["unit_id"]}]})
        folder = self.root / "production_review"
        production.prepare(out, "france", "test-model-1", "test-model-2", folder)
        path = folder / "translation_review.xlsx"
        rows, columns = production._xlsx_rows(path)
        self.assertEqual(len(rows), 6)
        self.assertEqual(columns, list(production.AUTH_COLUMNS))
        self.assertEqual(columns[:19], list(production.REVIEW_COLUMNS))
        self.assertTrue(all(r["spanish_draft"].startswith("Existing Spanish") for r in rows))
        with self.assertRaises(ValueError):
            production.authorize(folder)  # No automatic authorization.
        changed_id = next(r["id"] for r in rows if json.loads(r["correction_patches"]))
        for r in rows:
            r["authorization"] = "Approved"
            if r["id"] == changed_id:
                r["approve_patch_indices"] = "1"
        production._write_review(path, rows, columns)
        production.authorize(folder)
        with self.assertRaises(ValueError):
            production.authorize(folder)
        # A previously authorized CSV can still migrate without losing its lock.
        from scripts.common import write_csv as save_legacy_review
        locked_rows, locked_fields = production._xlsx_rows(path)
        path.unlink()
        legacy_review = folder / "translation_review.csv"
        save_legacy_review(legacy_review, locked_rows, locked_fields)
        production.prepare(out, "france", "test-model-1", "test-model-2", folder)
        self.assertFalse(legacy_review.exists())
        self.assertEqual(production._xlsx_rows(path)[0], locked_rows)
        rows, _ = production._xlsx_rows(path)
        self.assertEqual(sum(r["english_changed"] == "True" for r in rows), 1)
        self.assertEqual(sum(bool(r["spanish_draft"]) for r in rows), 5)
        self.assertEqual(next(r for r in rows if r["id"] == changed_id)["spanish_draft"], "")
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            production.translate(folder, self.cfg_path)  # Free.
        self.assertIn('"existing_experimental_drafts_reused": 5', capture.getvalue())
        self.assertIn('"pending_paid_requests": 1', capture.getvalue())
        source = json.loads((folder / "authorized.json").read_text(encoding="utf-8"))["units"]
        changed = next(u for u in source if u["unit_id"] == changed_id)
        append_jsonl(folder / "translation.jsonl", {
            "unit_id": changed_id, "model": "test-model-2", "sp": "Updated Spanish",
            "source_sha256": sha256(changed["english_authorized"].encode()).hexdigest()})
        rows, cols = production._xlsx_rows(path)
        unaffected = next(r for r in rows if r["id"] != changed_id)
        unaffected["sp_human"] = "A reviewed human translation"
        unaffected["status"] = "Edit sp"
        production._write_review(path, rows, cols)
        production.export(folder)
        result, result_columns = production._xlsx_rows(path)
        self.assertEqual(result_columns, list(production.AUTH_COLUMNS))
        self.assertEqual(next(r for r in result if r["id"] == changed_id)["spanish_draft"], "Updated Spanish")
        self.assertEqual(next(r for r in result if r["id"] == unaffected["id"])["sp_human"],
                         "A reviewed human translation")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            production.translate(folder, self.cfg_path)
        self.assertIn('"pending_paid_requests": 0', output.getvalue())
        next(r for r in result if r["id"] == changed_id)["authorization"] = "Pending"
        production._write_review(path, result, result_columns)
        with self.assertRaises(ValueError):
            production.translate(folder, self.cfg_path)  # Lock prevents changed approvals.

    def test_production_migrates_existing_authorization_without_overwriting(self):
        from scripts import production
        from scripts.experiment import prepare
        from scripts.common import append_jsonl, save_json
        out, _, blocks, _ = prepare(self.cfg)
        eligible = [b for b in blocks if b["eligible"] and b["document_id"] == "france"]
        evidence = []
        for b in eligible:
            item = {"id": b["id"], "v": "T", "m": "F", "ml": "F", "e": [],
                    "wr": "F", "mi": "F", "di": "F", "rn": "OK", "findings": []}
            append_jsonl(out / "examine.jsonl", {"model": "test-model-1", "document_id": "france",
                                                "page": b["page"], "items": [item]})
            evidence.append({"unit_id": b["unit_id"], "id": b["id"], "page": b["page"],
                             "english": b["english"], "item": item, "assessment_warnings": []})
        folder = self.root / "old_authorization"
        folder.mkdir()
        save_json(folder / "examination_evidence.json", evidence)
        save_json(folder / "production_manifest.json", {
            "source_experiment": str(out.resolve()), "source_experiment_id": out.name,
            "document": "france", "examine_model": "test-model-1",
            "translate_model": "test-model-2",
            "evidence_sha256": production._sha(folder / "examination_evidence.json")})
        previous = [{"unit_id": u["unit_id"], "page": u["page"],
                     "english_original": u["english"], "proposed_patches": "[]",
                     "approve_patch_indices": "", "authorization": "Approved"} for u in evidence]
        from scripts.common import write_csv
        write_csv(folder / "authorization.csv", previous, production.OLD_AUTH_COLUMNS)
        production.prepare(out, "france", "test-model-1", "test-model-2", folder)
        rows, _ = production._xlsx_rows(folder / "translation_review.xlsx")
        self.assertTrue(all(r["authorization"] == "Approved" for r in rows))
        self.assertFalse((folder / "authorization.csv").exists())
        with self.assertRaises(ValueError):
            production.prepare(out, "france", "test-model-1", "test-model-2", folder)

    def test_production_migrates_combined_csv_without_losing_human_edits(self):
        from scripts import production
        from scripts.experiment import prepare
        from scripts.common import append_jsonl, write_csv
        out, _, blocks, _ = prepare(self.cfg)
        eligible = [b for b in blocks if b["eligible"] and b["document_id"] == "france"]
        for b in eligible:
            append_jsonl(out / "examine.jsonl", {"model": "test-model-1",
                "document_id": "france", "page": b["page"], "items": [{
                    "id": b["id"], "v": "T", "m": "F", "ml": "F", "e": [],
                    "wr": "F", "mi": "F", "di": "F", "rn": "OK", "findings": []}]})
        folder = self.root / "combined_migration"
        production.prepare(out, "france", "test-model-1", "test-model-2", folder)
        workbook = folder / "translation_review.xlsx"
        previous, columns = production._xlsx_rows(workbook)
        previous[0].update({"authorization": "Approved", "sp_human": "Manual Spanish edit",
                            "status": "Edit sp", "human_note": "Keep this reviewed text"})
        workbook.unlink()  # Reproduce the pre-XLSX project state, with existing human edits.
        old_csv = folder / "translation_review.csv"
        write_csv(old_csv, previous, columns)
        production.prepare(out, "france", "test-model-1", "test-model-2", folder)
        migrated, fields = production._xlsx_rows(workbook)
        self.assertEqual(fields, list(production.AUTH_COLUMNS))
        self.assertEqual(migrated, previous)
        self.assertFalse(old_csv.exists())
        self.assertFalse((folder / "authorization.csv").exists())

    def test_dry_run_and_live_guard(self):
        out_capture = io.StringIO()
        with contextlib.redirect_stdout(out_capture):
            transplate.main(["experiment", "--config", str(self.cfg_path), "--dry-run"])
        self.assertIn("ZERO paid API requests", out_capture.getvalue())
        with self.assertRaises(ValueError):
            transplate.main(["experiment", "--config", str(self.cfg_path), "--live"])


if __name__ == "__main__":
    unittest.main()
