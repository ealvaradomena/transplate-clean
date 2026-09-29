# transplate-clean

A compact RStudio/Python project with **two processing workflows and an offline evaluation dashboard**:

- **A · `experiment`:** compare configurable OpenAI models on combined mathematical/editorial/technical examination and Spanish translation of every eligible source unit.
- **B · `template`:** produce a blinded, spreadsheet-ready CSV for human evaluation of translations, either from experiment results **or any existing translation CSV**. This command never uses the API.
- **Dashboard · `dashboard.py`:** generate France-only and two-document self-contained HTML interfaces for blind evaluation of the experiment’s examination and translation outputs (historical runs can additionally display their former parsing results). It uses an existing run and makes no API calls.

No R packages, Google integrations, private/public modes, or staged production pipelines are needed. Open `transplate-clean.Rproj` in RStudio; use its **Terminal** to run Python.

## Installation

From the RStudio Terminal (Python 3.10+):

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Windows Git Bash: source .venv/Scripts/activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
```

Edit `config.json`: change `documents` (PDF URL mapping), `models`, `random_seed`, `output_directory`, or manually maintained token prices. The two specified OECD documents are configured initially. Prices are **illustrative historical values, not live verified quotes**; check official pricing before authorizing spend. Unknown models can be dry-run but need prices configured for a live run. All eligible, verified source blocks are processed by each model; there is no sampling setting.

## Deterministic extraction and retained model fields

**PDF extraction and source-unit identification are programmatic, NOT LLM-driven.** PyMuPDF extracts text blocks and the existing pipeline orders them using page coordinates, with stable document-local numeric `id`, source-block `unit_id`, page and bounding-box provenance. Manually verified graphical text (currently `data/france_graphic_text.json`) remains in the complete source record, marked as visual transcription and excluded from the model experiment. Publication furniture is likewise preserved but excluded. The source PDF should still be visually checked when the document layout warrants it; deterministic extraction does not by itself guarantee perfect semantic paragraph boundaries on arbitrary PDFs.

There are **two** model tasks: combined examination and translation, with prompts and schemas in `prompts/{examine,translate}.md` and `schemas/{examine,translate}.schema.json`. Examination keeps the legacy `id/v/m/ml/e/wr/mi/di/rn` fields plus `findings` (structured technical findings); translation keeps `id/sp`. Their inputs are identical across candidate models. `v=T` means supplied block provenance, not a claim that every PDF has passed human visual verification. Experimental translation uses unchanged extracted `x`, not the examination proposals; a future production workflow will assemble authorized English `en` after independently validated patches. Experimental CSVs retain their existing formats; production review uses one XLSX only.

**Historical evidence stays historical:** existing runs with `parse.jsonl` and dashboards showing LLM parsing are not rewritten or deleted. The current experiment no longer requests, scores or pays for LLM parsing. Its dashboard shows only examination and translation.

## A · Experiment

```bash
python transplate.py experiment --dry-run
```

This free command downloads/caches the PDFs, extracts all text blocks with their document, page, and bounding box, records the full eligible-unit list, and prints **pending requests, approximate tokens, and incremental estimated USD cost**. It performs **no OpenAI API calls**. If offline, run this step later when connected; existing downloaded PDFs are reused. Inspect original PDFs whenever layout or mathematics matter: PDF text extraction is not an authoritative rendering of equations, tables, or figures. Scanned PDFs require separate OCR and are not supported.

Only after reviewing the dry-run estimate, setting `OPENAI_API_KEY`, and **explicitly authorizing** the incremental paid requests:

```bash
python transplate.py experiment --live --max-cost-usd 1.00
```

`--max-cost-usd` is a required invocation-level safeguard. Before each request, the runner reserves approximately two times its estimated input tokens plus the configured maximum output tokens and stops if that would exceed the remaining cap. This is **an estimate, not an API-enforced spending ceiling**; actual provider invoices can differ due to tokenization, price changes or billing rules. Never interpret the estimate as a guaranteed cost. The program records actual returned token usage and approximate USD cost after every successful API response, including output-invalid responses. It **does not automatically retry model requests** (including SDK-level automatic retries). A network interruption after submission can still leave an ambiguous provider charge; check your provider usage before intentionally rerunning. A structurally invalid response is recorded in `failed.jsonl` **without automatic retry**; the current invocation continues with other planned requests. Subsequent invocations block unresolved failed requests until deliberately investigated. `python transplate.py recover --run output/runs/YOUR-RUN-ID` performs **offline, zero-API revalidation** of previously billed failures under the same current experimental inputs. It recovers only structurally valid responses and preserves original failure and usage logs, annotating any substantive consistency problems without changing the response. Run it before spending more. If structural failures remain, `python transplate.py experiment --live --skip-failed --max-cost-usd 0.25` can complete only unattempted calls, leaving failed outputs as nonqualifying diagnostics. There is never an automatic paid retry. Keep the same prompts, configuration, and PDFs when resuming; changing them creates a different run. Do not put sensitive data or API keys in configuration.

PyMuPDF establishes the same source blocks and IDs for all models, with extraction fidelity checked separately using `verify-extraction`. Models **do not parse or segment the PDF**. Examination assesses the same source blocks for mathematical notation and conservative proofreading patches, plus separate comments-only flags for wrong, misleading, and narrowly pedagogically dismissive content, backed by one structured, excerpt-grounded `findings` entry per independent technical issue. Every separate proofreading problem receives its own ordered `e` patch; neither list has a preset issue-count limit. Every eligible original English block is translated by every model; proposed examination patches do not silently alter those experimental translation inputs. Mathematical and layout-sensitive extraction still calls for viewing the original PDF.

Outputs are grouped in `output/runs/<run-id>/`, where the ID incorporates settings, source PDF bytes, and prompts. A rerun with the same settings reuses completed calls rather than incurring duplicate costs. Principal outputs:

| File | Meaning |
|---|---|
| `manifest.json`, `units.json` | Frozen provenance, source hashes, model identities, complete eligible-unit lists |
| `source_blocks.csv` | All PDF text blocks including excluded page furniture; verbatim extracted text, page, bbox |
| `examine.jsonl` | Each model's math/editorial/technical examination, per original block |
| `translate.jsonl` | Each model's Spanish translations of all eligible source units |
| `usage.jsonl` | Per-request reported token usage (including cache/reasoning detail when available), latency and estimated USD bill |
| `failed.jsonl` | Immutable audit of structurally invalid responses and older semantic rejections, even after explicit offline recovery |

Each JSONL record identifies the model, document, stage, page where relevant, and original unit IDs. PDF sources are cached in `output/sources/` with originating URLs and cached-content hashes; no source text is silently corrected. All generated results are kept locally and Git-ignored.

### Distinguishing full-unit coverage from proposed-issue accuracy

Examination returns **one `e` patch per independently identified mechanical error** and **one `findings` entry per independently identified technical problem**, with an exact source excerpt, category (`wr`, `mi`, or `di`) and reason. Technical flags and the existing `rn` summary must agree with these findings. The API enforces the expected response structure and source IDs. Python records non-verbatim excerpts, non-applicable patches, and inconsistent model flags as **visible assessment warnings**, not grounds for discarding otherwise evaluable paid responses. Reviewers independently score the original proposals and whole-unit coverage; no model text, flags, or findings are silently corrected, and no issue-count limit is imposed. A separate prompt/schema version creates a **new experimental run**; historical model responses remain historical and cannot be represented as though they contained newly requested structured findings.

The blinded dashboard retains **five whole-unit examination criteria**, notably the overall Conservative proofreading criterion, which can fail when a model overlooks necessary corrections even if every proposed correction was individually correct. Every individual proposed `e` patch and every individual technical finding also receives its **own binary judgment**. For clean responses with no proposed issues, the five whole-unit criteria still apply (including omissions). Identical complete outputs continue to share judgments across anonymous models. Dashboard JSON exports keep `examination` (whole-unit), `individual_issues` (separate proposals), diagnostic equivalents and `translation_rankings` distinct; the private model key maps each shared response to all producing models. **Never use proposed-issue accuracy as a proxy for recall of missed errors.**

Only after completing blind evaluation, generate the **private, unblinded comparison locally** (no network/API calls). Export the dashboard's completed evaluation JSON once and keep it under `output/runs/<run-id>/evaluation/`. The private mapping and original model-response JSONL files are already stored in the same run. For the completed France evaluation:

```bash
python scripts/compare.py --run output/runs/a7d1af509269 --scope france
```

The script automatically locates the matching export and private attribution key, checks their run/scope, and verifies that each attributed model has original stored examination/translation results. It creates three local files in that run's `evaluation/` directory: `private_comparison_france.html` (visual comparison), `private_comparison_france.csv` (separate metric summaries and denominators), and `private_comparison_france-detail.csv` (attributed per-unit scores, independent issues, and ranks). It does **not** rerun experiments, change source responses, or make API calls. To read an export saved elsewhere, add `--evaluation PATH_TO_JSON`. For a completed combined-document export, use `--scope all` (and the matching combined export).

Unit-level criteria (including overall Conservative proofreading), independently proposed correction/technical finding accuracy, and translation rankings are reported separately; no combined score is constructed. Shared anonymous outputs are attributed separately to every originating model; historical origins remain separate. **These generated reports reveal model identities: never share them with blinded evaluators.** The previous two-positional-argument `scripts/compare.py EXPORT.json PRIVATE_KEY.csv` command also remains supported.

## B · Independent binary translation evaluation CSV (optional)

After **one or more translations have actually completed**, use the run directory printed by the dry-run/live command:

```bash
python transplate.py template --run output/runs/YOUR-RUN-ID
```

Or run B entirely independently, with **no PDFs, API key, or A results**. Supply a UTF-8 CSV containing `document_id,page,unit_id,source_english,translation_spanish` and optionally `model,pdf_url`:

```bash
python transplate.py template --translations examples/translation_input.csv --out output/example_review
# Replace the example CSV with your own for substantive evaluation.
```

This optional **independent, binary spreadsheet pathway is separate from the new comparative ranking dashboard**. It remains useful for standalone input and historical experiments. B writes two separate files: `translation_evaluation.csv` (**give to reviewer**) and `model_key.csv` (**keep concealed until review is complete**). Anonymous `evaluation_id` values are deterministic, shuffled independently within each unit, and do not expose the model. Identical duplicate translation rows are rejected. The review CSV contains English/Spanish, document/page/unit provenance, and four empty binary human-evaluation fields:

- `semantic_fidelity`: no additions, omissions, altered claims or meaning.
- `technical_accuracy`: correct specialized terms and technical concepts.
- `spanish_quality`: idiomatic, natural, publication-ready Spanish.
- `structure_preservation`: retains numbers, citations, notation and relevant structure.

Score each field **1 = meets requirement**, **0 = fails**, **blank = not evaluated**. Optional `notes` and `review_flag` capture exceptions. Every translation remains one review row with a stable ID, which joins to `model_key.csv` after assessment. CSV can be opened in Excel, R, or imported into Google Sheets; no Google API is required. **Regenerating the template overwrites its files:** save a separate copy before entering human scores. The CSV is an evaluation template, **not an automated claim that any model is better**. If reviewing technical math or layout fidelity, consult the original PDF using the recorded document URL and page.

## C · Experimental dashboard (France or both documents)

**Review historical results without rerunning paid requests.** When a new full-coverage run has only a dry-run manifest, build its new-format dashboard using the earlier experiment as evidence:

```bash
python dashboard.py output/runs/8179466b4f81 --reuse-from output/runs/e514a34f97c0
```

The command validates PDF hashes, source text, source-unit IDs, eligibility, and model lists before creating the dashboard. It maps the earlier valid examination and translation outputs onto the deterministic source units, retains historical invalid outputs as diagnostics, deduplicates identical responses, and explicitly marks absent model outputs. The private model-key CSV records each response's originating run and distinguishes historical experimental conditions. **It does not edit either run's JSONL records, mark any new API calls as completed, or imply that older and newer prompts/batching were equivalent.** This compatibility view lets you evaluate available historical responses in the revised interface, but is not a completed full-coverage experiment. Model-level comparisons must account for the missing outputs and different experimental conditions.

For **new full-coverage experiments**, the dashboard reads saved model outputs directly. You do **not** need to generate the independent binary translation CSV first. Historical runs retain their original dashboard workflow (including generating the CSV first):

```bash
python dashboard.py output/runs/YOUR-RUN-ID
```

Open `evaluation/blind_dashboard_france.html` for the France cases or `evaluation/blind_dashboard_all.html` for both documents. Each shows source text and anonymous outputs, with original PDF-page images when locally cached. New-run examination shows each *distinct complete response* per source unit once: identical flags, exact correction patches and explanations share one human binary evaluation across all matching models. Translation shows all distinct anonymous translations of a source unit together, with rank selectors that permit ties. Identical translations share a rank automatically. Examination and translation use separate reporting metrics; rankings are never interpreted as binary scores. Notes, review flags, browser-local progress and JSON checkpoints remain available. Download checkpoints periodically. Checkpoints are tied to their run, document scope and current full-coverage evaluation format (five unit-level binary examination judgments, separate binary scores for every proposed correction and structured technical finding, and translation rankings); historical checkpoint formats cannot be imported.

**Failed responses with usable per-unit content are visible**, clearly identified by their validation error; missing or unusable model responses are counted as missing, not silently called successful. Reviewers may score them for diagnostic purposes, but they remain machine-invalid and cannot qualify regardless of human scores. New-run exports keep machine-valid examination judgments and rejected diagnostic judgments separate; translation rankings are exported separately and the private CSV associates every grouped response with its original model. Historical dashboards preserve their prior export fields. Never aggregate diagnostic scores into qualifying results. The corresponding `dashboard_model_key_france.csv` and `dashboard_model_key_all.csv` are private and must not be shared. Dashboard generation neither replays paid requests nor changes experimental records.

## Working-directory guide

| Path | Purpose |
|---|---|
| `transplate-clean.Rproj` | Open the working directory in RStudio; use its Terminal for Python commands. |
| `config.json`, `requirements.txt` | Experiment settings and Python dependencies. |
| `transplate.py`, `dashboard.py` | Simple root-level commands; implementation lives in `scripts/`. |
| `scripts/` | PDF extraction, experiment, evaluation CSV, dashboard generation, shared helpers and CLI. |
| `prompts/` | The two current, editable model instructions; changes create a new run ID. |
| `data/france_graphic_text.json` | Manually verified graphical text for the current France PDF (retained in source records). |
| `dashboard/dashboard.html`, `dashboard/full_dashboard.html` | Historical and current dashboard templates; both retain the established visual design. |
| `examples/` | Sample input for standalone workflow B. |
| `tests/` | Existing offline verification. |
| `output/sources/` | Cached PDF sources and extraction metadata. |
| `output/runs/<run-id>/` | Source blocks, experimental outputs, provenance, request usage, and `evaluation/` exports. |
| `schemas/` | Current examination and translation JSON contracts. |
| `old/` | Local backups created by PatchMyMess; not part of the running application. |

## Minimal design and limits

One experiment entry point, one dashboard entry point, focused scripts, two model prompts/schemas, two templates to preserve historical evaluation compatibility, one JSON configuration, two dependencies, one RStudio project, and existing offline checks. Output contracts remain checked by the existing Python validator. Historical runs are retained as evidence.

- **Source preservation:** original PDF files cached intact; deterministic text blocks and provenance stored independently of model outputs; inspect page renderings where source fidelity or reading order is uncertain. Human source-PDF inspection is still needed for layout-dependent correctness.
- **Comparability:** identical deterministic examination and translation blocks for every model; seed and source hashes recorded. No model-generated segmentation.
- **Execution safety:** downloading and dry-running costs no API credits; live requires both `--live` and a positive cost cap; successful calls are resumed; invalid outputs are logged and not automatically retried. No background API activity.
- **Practical scope:** born-digital, English PDF reports with selectable text; no general-purpose OCR, automated semantic reconstruction of arbitrary PDF layout, external fact verification, automatic human scoring or automated human scoring. The dashboard is an offline evaluation interface, not a statistical analysis or full translation-production system.

To run the consolidated offline tests, without any network/API requests:

```bash
python -m unittest discover -s tests -v
```

**Examination response boundary:** New runs use a per-request strict OpenAI JSON schema keyed by the exact programmatic source IDs. Python converts the keyed API response into the existing `items` records and checks their field types and IDs. Content mistakes (contradictory flags, non-applicable edits or non-verbatim evidence) are recorded as assessment warnings for human scoring, not rejected as schema failures. Explicit `recover` can reclaim older paid responses from the *same* prompt/schema/input run without rewriting their original logs or making API calls; it refuses other experimental conditions. The unchanged response protocol preserves the existing run ID. Translation retains its existing response format.

## Production translation and human review (France)

**One XLSX, no redundant approvals or translation requests.** The completed France experiment already contains examination results and GPT-4.1 mini translations of the original English. Production uses the same 19 historical human-review columns **plus two fields**: `approve_patch_indices` and `authorization`. Experimental scores are evidence, **not** authorization to change English. No model parsing or new examination request is involved.

```bash
python transplate.py production prepare --run output/runs/a7d1af509269 --document france --examine-model gpt-5-nano --translate-model gpt-4.1-mini
```

Open **`output/runs/a7d1af509269/production/france/translation_review.xlsx`**. It displays the original English, proposed patches, technical review, and **existing Spanish drafts** for immediate context. Enter the **1-based indices** of patches you authorize (`1,3`, or blank for none), and set `authorization=Approved` for **each unit**, including unchanged units. Preserve source and proposed-patch fields. You can use `status`, `sp_human` and `human_note` for later Spanish review; `Keep sp`/`Edit sp` remain compatible with `google_apps_script/Code.gs` after importing and converting this XLSX into a native Google Sheet; Apps Script does not execute inside Excel. Do not treat an existing experimental Spanish draft as final for a unit whose English you change.

```bash
python transplate.py production authorize --dir output/runs/a7d1af509269/production/france
python transplate.py production translate --dir output/runs/a7d1af509269/production/france
```

The second command is **a free dry run**: it reuses previously paid GPT-4.1 mini translations whenever authorized English equals the original, and reports **only** missing or changed units as pending. If it reports zero pending requests, **do not make API calls**. Otherwise, explicitly approve a sufficient cost cap before requesting only those missing translations:

```bash
python transplate.py production translate --dir output/runs/a7d1af509269/production/france --live --max-cost-usd 0.10
```

Finally, fill any missing Spanish drafts **in the same XLSX** (no second spreadsheet):

```bash
python transplate.py production export --dir output/runs/a7d1af509269/production/france
```

`export` never replaces an existing human-reviewed draft or erases `sp_human`, `status` or `human_note`; it refuses conflicting edits. `text_class`/`level` remain empty unless verified. If you already have `authorization.csv` or the previous `translation_review.csv`, repeat `production prepare` to migrate saved approvals and human edits. Production CSV files are removed **only after the XLSX is written and checked**. Experimental CSVs are unchanged. No API calls are made by `prepare`, `authorize`, or `export`.
