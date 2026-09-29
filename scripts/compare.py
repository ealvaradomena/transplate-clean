"""Offline, private comparison of completed blinded dashboard evaluations.

From the project root, after exporting a completed dashboard evaluation:
    python scripts/compare.py --run output/runs/<run-id> --scope france

For an evaluation stored elsewhere, append --evaluation path/to/export.json.
The original two-positional-argument interface is retained for earlier workflows.
No API calls, external libraries, or changes to experiment results.
"""
import argparse
import csv
import html
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean


def read_key(path):
    mapping = defaultdict(list)
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            if row["machine_valid"].lower() == "true" and row["model"]:
                mapping[(row["case_id"], row["anonymous_id"])].append(row)
    return mapping


def write_csv(path, rows, columns):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def compare(review_path, key_path, destination=None, run_dir=None):
    """Keep whole-unit coverage, proposed-issue precision and ranks distinct."""
    review_path = Path(review_path)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if review.get("schema_version") not in (None, "transplate-full-dashboard-1"):
        raise ValueError("Unsupported evaluation export version")
    mapping = read_key(key_path)
    if run_dir is not None:
        run_dir = Path(run_dir)
        if review["run_id"] != run_dir.name:
            raise ValueError("Evaluation export belongs to a different experimental run")
        # Confirm the private mapping points to actual locally stored model outputs.
        available = {}
        for stage in ("examine", "translate"):
            path = run_dir / (stage + ".jsonl")
            if not path.is_file():
                raise FileNotFoundError(f"Missing original {stage} responses: {path}")
            present = set()
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    record = json.loads(line)
                    for unit_id in record.get("source_unit_ids", []):
                        present.add((record["model"], record["document_id"], unit_id))
            available[stage] = present
    else:
        available = None

    observations = defaultdict(list)
    detailed = []
    categories = (("unit", "examination"), ("proposed_issue", "individual_issues"),
                  ("translation", "translation_rankings"))
    for measure, name in categories:
        for row in review.get(name, []):
            value = row.get("rank") if measure == "translation" else row.get("score")
            if value is None:
                if review.get("complete"):
                    raise ValueError(f"Completed export has an unscored {name} record")
                continue
            if type(value) is not int or (value not in (0, 1) if measure != "translation" else value < 1):
                raise ValueError(f"Invalid {measure} score for {row.get('case_id')}")
            metric = ("rank" if measure == "translation" else row["criterion"] if measure == "unit"
                      else row["issue_type"] + ":" + row["category"])
            matches = mapping.get((row["case_id"], row["anonymous_id"]), [])
            if not matches:
                raise ValueError(f"No machine-valid private mapping for {row['case_id']} / {row['anonymous_id']}")
            for match in matches:
                origin = match.get("origin_run_id") or review["run_id"]
                stage = "translate" if measure == "translation" else "examine"
                if available is not None and origin == review["run_id"]:
                    if (match["model"], row["document_id"], row["unit_id"]) not in available[stage]:
                        raise ValueError(f"No original {stage} response for {match['model']} / {row['unit_id']}")
                identity = (match["model"], origin, measure, metric)
                observations[identity].append(value)
                detailed.append({"model": match["model"], "origin_run_id": origin,
                                 "document_id": row.get("document_id", ""),
                                 "unit_id": row.get("unit_id", ""), "measure": measure,
                                 "criterion": metric, "issue_index": row.get("issue_index", ""),
                                 "score_or_rank": value})

    if not observations:
        raise ValueError("No scored, machine-valid evaluation records match the private key")
    if review.get("complete") and run_dir is not None:
        counts = review.get("counts", {})
        for section in ("examination", "translation"):
            values = counts.get(section, {})
            if values.get("completed") != values.get("required"):
                raise ValueError(f"Incomplete {section} evaluation despite 'complete' export flag")

    summary = []
    distributions = {}
    for (model, origin, measure, metric), values in sorted(observations.items()):
        distributions[(model, origin, measure, metric)] = Counter(values)
        summary.append({"model": model, "origin_run_id": origin, "measure": measure,
                        "criterion": metric, "evaluated_count": len(values),
                        "result": round(mean(values), 4),
                        "interpretation": "mean competition rank (lower preferred)" if measure == "translation"
                        else "proportion meeting criterion among evaluated outputs"})

    if destination is None:
        target = review_path.with_name(review_path.stem + "-private-comparison")
    else:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
    write_csv(target.with_suffix(".csv"), summary, list(summary[0]))
    write_csv(target.with_name(target.name + "-detail.csv"), detailed,
              ["model", "origin_run_id", "document_id", "unit_id", "measure",
               "criterion", "issue_index", "score_or_rank"])

    names = {"math": "Mathematical notation", "editorial": "Conservative proofreading (whole unit)",
             "wrong": "Substantive error", "misleading": "Misleading",
             "dismissive": "Pedagogical dismissiveness",
             "correction:proofreading": "Individually proposed proofreading corrections"}
    headings = (("unit", "Whole-unit examination: coverage and accuracy"),
                ("proposed_issue", "Individual proposed corrections and technical findings"),
                ("translation", "Comparative translation ranks (ties retained)"))
    sections = []
    e = html.escape
    for measure, heading in headings:
        rows = [r for r in summary if r["measure"] == measure]
        if not rows:
            continue
        content = []
        for row in rows:
            value = row["result"]
            key = (row["model"], row["origin_run_id"], measure, row["criterion"])
            if measure == "translation":
                counts = distributions[key]
                ranks = ", ".join(f"{rank}: {count}" for rank, count in sorted(counts.items()))
                # A readable, labeled distribution, without conflating ranks and pass rates.
                total = row["evaluated_count"]
                bars = "".join(f'<div class="rank-row"><span>Rank {rank}</span>'
                               f'<div class="track"><div class="fill" style="width:{100 * count / total:.1f}%"></div></div>'
                               f'<span>{count}/{total}</span></div>'
                               for rank, count in sorted(counts.items()))
                display = f"{value:.3f} mean rank" + f'<div class="rank-bars" aria-label="Rank distribution: {e(ranks)}">{bars}</div>'
            else:
                display = (f"{100 * value:.1f}%"
                           f'<div class="track"><div class="fill" style="width:{100 * value:.1f}%"></div></div>')
            content.append("<tr>" + "".join(f"<td>{e(str(x))}</td>" for x in
                           (row["model"], row["origin_run_id"], names.get(row["criterion"], row["criterion"]),
                            row["evaluated_count"])) + f"<td>{display}</td></tr>")
        explanation = ("Whole-unit Conservative proofreading includes missed errors even when individual proposed corrections are accurate."
                       if measure == "unit" else
                       "Only accuracy of issues actually proposed; this does not measure missed issues."
                       if measure == "proposed_issue" else
                       "Translation uses mean competition rank (lower preferred). Rank 1 denotes a preferred translation; identical translations and ties retain the same rank.")
        sections.append(f"<section><h2>{e(heading)}</h2><p>{e(explanation)}</p>"
                        f"<table><thead><tr><th>Model</th><th>Origin run</th><th>Criterion</th>"
                        f"<th>Evaluated</th><th>Result</th></tr></thead><tbody>{''.join(content)}</tbody></table></section>")
    # Stage choices are explicit, reproducible, and restricted to the same origin run.
    # Do not combine incompatible measures into a synthetic overall model score.
    selection_sections = []
    origins = sorted({r["origin_run_id"] for r in summary})
    def metric_row(model, origin, measure, criterion):
        return next((r for r in summary if r["model"] == model and
                     r["origin_run_id"] == origin and r["measure"] == measure and
                     r["criterion"] == criterion), None)

    for origin in origins:
        for stage in ("examination", "translation"):
            primary = [(r["model"], r) for r in summary
                       if r["origin_run_id"] == origin and
                       (r["measure"], r["criterion"]) ==
                       (("unit", "editorial") if stage == "examination" else ("translation", "rank"))]
            if not primary:
                continue
            # All models must have the same unit coverage for a direct stage comparison.
            n_units = {r["evaluated_count"] for _, r in primary}
            if len(primary) < 2 or len(n_units) != 1:
                selection_sections.append(
                    f'<h3>{e(stage.title())} · {e(origin)}</h3>'
                    '<p>Comparable results are insufficient: fewer than two models, '
                    'or different numbers of evaluated units. No stage choice generated.</p>')
                continue
            best_value = (max if stage == "examination" else min)(r["result"] for _, r in primary)
            candidates = [model for model, r in primary if r["result"] == best_value]
            criterion_text = ("whole-unit Conservative proofreading pass rate (higher is better)"
                              if stage == "examination" else
                              "mean competition translation rank (lower is better; ties retained)")
            score_text = (f"{100 * best_value:.1f}%" if stage == "examination" else
                          f"{best_value:.3f}")
            choice = (" and ".join(e(model) for model in sorted(candidates)))
            if len(candidates) > 1:
                choice_label = "Tied on the primary measure"
            else:
                choice_label = "Provisional stage choice"
            rows = []
            for model, r in sorted(primary):
                if stage == "examination":
                    math = metric_row(model, origin, "unit", "math")
                    correction = metric_row(model, origin, "proposed_issue", "correction:proofreading")
                    math_info = (f"{100 * math['result']:.1f}% ({math['evaluated_count']} units)"
                                 if math else "Not evaluated")
                    correction_info = (f"{100 * correction['result']:.1f}% "
                                       f"({correction['evaluated_count']} proposed corrections)"
                                       if correction else "No individually evaluated corrections")
                    cells = [model, str(r["evaluated_count"]), f"{100 * r['result']:.1f}%",
                             math_info, correction_info]
                else:
                    distribution = distributions[(model, origin, "translation", "rank")]
                    first = distribution.get(1, 0)
                    cells = [model, str(r["evaluated_count"]), f"{r['result']:.3f}",
                             f"{first}/{r['evaluated_count']}",
                             ", ".join(f"rank {v}: {n}" for v, n in sorted(distribution.items()))]
                rows.append("<tr>" + "".join(f"<td>{e(v)}</td>" for v in cells) + "</tr>")
            headers = (["Model", "Units", "Whole-unit proofreading", "Mathematical notation",
                        "Proposed correction accuracy"] if stage == "examination" else
                       ["Model", "Units", "Mean rank", "Rank 1 (including ties)",
                        "Rank distribution"])
            note = ("Whole-unit proofreading includes missed errors; individual correction "
                    "accuracy covers only proposals and may have a very small denominator. "
                    "Technical flag pass rates are reported above, but alone do not show detection "
                    "sensitivity if few genuine issues occur in the document."
                    if stage == "examination" else
                    "Rank 1 includes ties. Differences here describe these evaluated units; "
                    "they do not establish superiority on other documents.")
            selection_sections.append(
                f'<h3>{e(stage.title())} · origin run {e(origin)}</h3>'
                f'<p><strong>{choice_label}: {choice}</strong> '
                f'({e(criterion_text)}: {score_text}; {next(iter(n_units))} units).</p>'
                '<table><thead><tr>' + ''.join(f'<th>{e(h)}</th>' for h in headers) +
                '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table>' +
                f'<p>{e(note)}</p>')
    selection_html = ('<section><h2>Model selection · stage-specific evidence</h2>'
                      '<p>Transparent provisional choices based only on the primary measure '
                      'for each stage. Evidence is reported separately by origin run; '
                      'no overall cross-stage model score is calculated. '
                      'PDF extraction and unit identification are programmatic (no LLM selection).</p>'
                      + ''.join(selection_sections) + '</section>')

    target.with_suffix(".html").write_text('''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Transplate · Private comparison</title>
<style>body{font:14px/1.55 system-ui,sans-serif;color:#172033;background:#f4f6f8;max-width:1200px;margin:32px auto;padding:0 20px}
section{background:white;border:1px solid #d6dde6;border-radius:9px;margin:18px 0;padding:19px;overflow-x:auto}
h1{font-size:27px}h2{font-size:19px;margin:0}p{color:#536174}table{width:100%;border-collapse:collapse;margin-top:10px}
th,td{text-align:left;padding:10px;border-bottom:1px solid #e3e8ef;vertical-align:middle}th{font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.track{width:110px;height:9px;border-radius:5px;background:#e6eaf5;display:inline-block;margin:0 6px;vertical-align:middle}
.fill{height:9px;border-radius:5px;background:#3157d5}.rank-bars{margin-top:6px}.rank-row{display:flex;align-items:center;gap:4px;white-space:nowrap;font-size:12px;color:#536174}
.rank-row span:first-child{min-width:46px}.rank-row .track{margin:0;width:72px}.rank-row span:last-child{min-width:38px}
footer{color:#5d697b;font-size:12px;margin-bottom:30px}</style></head><body>
<h1>Private experimental comparison</h1><p>Unblinded model identities. Each origin run is reported separately. Only machine-valid, human-evaluated responses contribute.
Whole-unit completeness, individual proposed-issue accuracy, and translation ranks are distinct measurements; no combined performance score is calculated.</p>'''
        + selection_html + "".join(sections) + "<footer>Generated locally from dashboard evaluation, private attribution key, and original stored model responses where available. No API calls.</footer></body></html>", encoding="utf-8")
    print("PRIVATE comparison:", target.with_suffix(".html"))
    print("PRIVATE summary CSV:", target.with_suffix(".csv"))
    print("PRIVATE detailed CSV:", target.with_name(target.name + "-detail.csv"))
    return summary


def run_comparison(run, scope=None, evaluation=None):
    run = Path(run)
    output = run / "evaluation"
    if evaluation is not None:
        evaluation = Path(evaluation)
    else:
        candidates = sorted(output.glob("transplate-*-evaluation.json"))
        candidates = [p for p in candidates if p.name.startswith("transplate-" + run.name + "-")]
        if scope:
            candidates = [p for p in candidates if json.loads(p.read_text(encoding="utf-8")).get("scope") == scope]
        if len(candidates) != 1:
            raise ValueError(f"Expected exactly one completed evaluation JSON in {output}; found {len(candidates)}. "
                             "Specify --scope or --evaluation. Export your completed dashboard if needed.")
        evaluation = candidates[0]
    review = json.loads(evaluation.read_text(encoding="utf-8"))
    scope = scope or review.get("scope")
    if scope not in ("france", "all") or review.get("scope") != scope:
        raise ValueError("Scope must match the exported dashboard ('france' or 'all')")
    if not review.get("complete") or review.get("export_type") != "final_evaluation":
        raise ValueError("Export is not a completed final evaluation")
    key = output / ("dashboard_model_key_" + scope + ".csv")
    if not key.is_file():
        raise FileNotFoundError(f"Private attribution key not found: {key}")
    return compare(evaluation, key, output / ("private_comparison_" + scope), run_dir=run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", nargs="?", help="Original positional interface: evaluation JSON")
    parser.add_argument("key", nargs="?", help="Original positional interface: private attribution CSV")
    parser.add_argument("--run", help="Locate saved original responses and private mapping automatically")
    parser.add_argument("--scope", choices=("france", "all"))
    parser.add_argument("--evaluation", help="Use an exported evaluation JSON outside the run folder")
    args = parser.parse_args()
    if args.run:
        if args.review or args.key:
            parser.error("Use either --run or the original two positional arguments")
        run_comparison(args.run, args.scope, args.evaluation)
    elif args.review and args.key and not args.scope and not args.evaluation:
        compare(args.review, args.key)
    else:
        parser.error("Provide --run RUN [--scope france|all] [--evaluation JSON], or REVIEW_JSON PRIVATE_KEY_CSV")
