# Transplate Clean: A Beginner's Guide

**For:** Someone who is new to Transplate, Python, terminals, API-based experiments, or RStudio projects.  
**Describes:** The current `transplate-clean` project on Windows and macOS, including its experimental dashboards.  
**Start here:** Read Sections 1–5, then choose the experiment, translation CSV, or dashboard workflow in Section 6. Begin with the free commands before considering paid execution.

---

## 1. What is Transplate Clean?

Transplate Clean is a small, Python-powered project you can open in **RStudio**. It helps you compare how different OpenAI models handle text extracted from English-language PDF documents, produce an evaluation spreadsheet, and assess model outputs in an interactive browser dashboard.

It has **three ways to work**, two of which are independent processing workflows:

1. **Experiment (`experiment`):** Download PDF documents; preserve their extracted English text; identify stable source blocks **programmatically with PyMuPDF**; ask configurable models to examine editorial/mathematical/technical issues and translate **every eligible original source block** into Spanish.
2. **Translation evaluation CSV (`template`):** Make a blinded, spreadsheet-ready review file from an existing experimental run **or from your own CSV of English–Spanish translations**. This workflow does not require an API key or a completed experiment if you provide your own translations.
3. **Experimental dashboard (`dashboard.py`):** Generate self-contained browser interfaces (France-only or both documents) for blind human evaluation of examination and translation results (and parsing only when inspecting a historical run that contains those results). It does not run any models.

**What it is not:** It is not an automatic book-translation production pipeline, an automated judgment of translation quality, a Google Sheets integration, or an R package. The human reviewer—not Transplate—decides whether an output is accurate or useful.

**RStudio versus Python:** The `.Rproj` file gives you a convenient project workspace and Terminal in RStudio. The processing programs themselves are Python scripts; you do not need to learn R or install R packages to run them.

## 2. Important terms in plain English

| Term | Meaning here |
|---|---|
| **Project root / working directory** | The `transplate-clean` folder containing `transplate.py`, `dashboard.py`, `README.md`, and `config.json`. Run commands from here. |
| **Terminal / Git Bash** | A window where you type commands. Git Bash is one option on Windows; macOS includes Terminal. RStudio also has a Terminal tab. |
| **Python script / CLI** | A `.py` program that accepts commands and options, such as `python transplate.py experiment --dry-run`. CLI means command-line interface. |
| **Dependency** | An installed package another program uses. This project needs `PyMuPDF` for PDFs and `openai` for model requests. |
| **Virtual environment (`.venv`)** | A project-specific place to install Python dependencies rather than mixing them with other projects. |
| **PDF extraction / source block** | Reading machine-readable text from a PDF and identifying pieces of it. An extracted text block is not automatically the same as the original document's intended paragraph. |
| **Deterministic source units** | PyMuPDF extracts source blocks and the pipeline assigns stable IDs and source provenance programmatically; no model is asked to regroup them. Check page images when source layout makes the extracted boundaries uncertain. |
| **Examination** | A combined pass with **two distinct responsibilities**: separate conservative proofreading/math-notation correction patches for every identifiable error, and separate excerpt-grounded structured technical findings with comments-only flags for clearly wrong, misleading, or narrowly pedagogically dismissive content. It is not an independently verified fact check; proposed corrections are **not** silently applied to experimental translation inputs. |
| **Complete coverage / random seed** | All eligible source blocks are processed by each model. The seed makes blinded option order reproducible; it does not select a sample. |
| **API / API key** | A way to send requests to OpenAI and a private credential that authorizes access. **Live** model calls can incur charges. |
| **Dry run** | A preparation and planning run: download/cache PDFs, extract and freeze all eligible units, and estimate outstanding requests and cost. **No paid model calls.** It may download PDFs over the internet. |
| **Run ID** | The directory name for one specific experiment, based on configuration and inputs; your run ID may differ from examples in this guide. |
| **JSONL** | A text file with one JSON record per line, suitable for storing individual model responses. |
| **Blind evaluation / model key** | Reviewers see anonymous alternatives; a separate file identifies which model generated each alternative. Keep the key concealed during evaluation. |
| **Checkpoint** | A saved dashboard evaluation you can export and import to resume your work. |

## 3. How the working directory is organized

This is the **usable project structure**; generated caches, local backups, and per-run files may differ on your computer.

```text
transplate-clean/
|-- transplate-clean.Rproj        Opens the project in RStudio
|-- README.md                    Short technical instructions
|-- TRANSPLATE_BEGINNERS_GUIDE.md  This guide
|-- requirements.txt             Python dependencies
|-- config.json                  Source PDFs, models, seed, cost estimates
|-- transplate.py                Short entry point for experiment and CSV
|-- dashboard.py                 Short entry point for both dashboard scopes
|-- scripts/                     PDF extraction, experiment, review CSV, dashboard logic
|-- prompts/                     Editable examine and translate instructions
|-- schemas/                     Their retained legacy-model JSON field contracts
|-- data/
|   `-- france_graphic_text.json  Manually verified France banner text
|-- dashboard/
|   |-- dashboard.html           Historical dashboard template
|   `-- full_dashboard.html      Full-coverage, deduplicated and ranking dashboard
|-- examples/
|   `-- translation_input.csv     Starter input for CSV-only evaluation
|-- tests/
|   `-- test_project.py          Existing offline project checks
|-- output/                      Generated locally when workflows run
|   |-- sources/                 Cached original PDFs and extraction metadata
|   `-- runs/
|       `-- <run-id>/             One experiment and its derived artifacts
|           |-- manifest.json, units.json, source_blocks.csv
|           |-- examine.jsonl, translate.jsonl
|           |-- usage.jsonl, failed.jsonl   When applicable
|           `-- evaluation/
|               |-- translation_evaluation.csv   Optional independent binary CSV
|               |-- model_key.csv                Its separate private key (optional)
|               |-- blind_dashboard_france.html
|               |-- blind_dashboard_all.html
|               |-- dashboard_model_key_france.csv
|               `-- dashboard_model_key_all.csv
`-- old/                         Local PatchMyMess backups, when present
```

**Source versus output:** Normally, edit `config.json`, not the Python scripts. Treat `output/` as experimental evidence, not disposable scratch space once a real run or human review has started. The `scripts/` directory contains the implementation; the short root scripts keep the commands simple. The `prompts/` directory contains the editable instructions. The `data/` directory contains manually verified source-reference material rather than generated results. The `old/` directory is backup material created by PatchMyMess, not another implementation you should run. Python may also create `.venv/` and `__pycache__/`; they are not project source files.

**Two current dashboard scopes:** The generator creates `blind_dashboard_france.html` (France only) and `blind_dashboard_all.html` (both documents). Older `blind_dashboard.html` files from prior experiments are not modified.

## 4. A mental model: how the pieces connect

```text
config.json + source PDF URLs
             |
             v
      experiment --dry-run           FREE of OpenAI API charges
      cache PDFs and source blocks
      identify ALL eligible source units programmatically
      show estimated outstanding work
             |
             v
      experiment --live             PAID: only if YOU authorize it
      programmatic blocks -> examination + translation
      save model responses, provenance and usage
             |
             +---------------------------+
             |                           |
             v                           v
      template --run                 dashboard.py <run-dir>
      translation review CSV         France-only and both-document HTML
      + separate model key           + separate dashboard model key
             |                           |
             v                           v
      spreadsheet-based             browser-based blind assessment
      binary human review           examination: binary distinct-output scoring; translation: side-by-side rankings with ties
```

**An alternative entry point:** `template --translations your_file.csv` skips the entire experiment and creates the translation review CSV directly. The new dashboard reads experiment results directly; historical dashboards also require the earlier translation evaluation CSV.

**Experimental fairness:** PyMuPDF produces one fixed set of source blocks. All models receive identical complete sets of examination and translation source blocks; models cannot redefine the textual units.

## 5. First-time setup: Windows and macOS

### Step A — Open the right project

Open `transplate-clean.Rproj` in RStudio, then open its **Terminal** tab; or open Git Bash (Windows) / Terminal (macOS). Navigate to the actual project folder:

```bash
cd ~/Documents/GitHub/transplate-clean
pwd
```

Confirm that `config.json`, `transplate.py`, `dashboard.py`, and `requirements.txt` are present. Do **not** run these instructions from `transplate-next` or the original historical `transplate` folder.

### Step B — Create a Python environment

You need **Python 3.10 or newer**. On macOS, replace `python` with `python3` if necessary.

```bash
python -m venv .venv
```

Activate it for the terminal you use:

| Terminal | Command |
|---|---|
| Windows Git Bash | `source .venv/Scripts/activate` |
| Windows PowerShell | `.\.venv\Scripts\Activate.ps1` |
| macOS Terminal | `source .venv/bin/activate` |

You should usually see `(.venv)` in the terminal prompt. Activate this environment again in each new terminal session before working on the project.

### Step C — Install dependencies

```bash
python -m pip install -r requirements.txt
```

The two declared dependencies are PyMuPDF and the OpenAI Python package. You do not need to install a web server, database, JavaScript framework, R packages, or Google API tools.

### Step D — Inspect configuration

Open `config.json` in RStudio or a text editor. The supplied configuration contains the OECD **Peru** and **France** PDF URLs, three OpenAI model IDs, random seed **42**, an output folder, and **illustrative** token prices.

Change settings **before beginning a new experiment**, not halfway through reviewing an existing one. Keep the JSON punctuation valid (quotation marks, commas, and braces). The generator offers both a France-only view and a two-document view.

## 6. Choose the workflow you need

### Route A — Run the multi-model experiment

**Start with this free command:**

```bash
python transplate.py experiment --dry-run
```

It caches/extracts your configured PDFs, records every eligible source unit, and prints the run directory, completed and pending request counts, approximate tokens, and estimated **incremental** cost. It may access the OECD website to obtain PDFs; it does **not** contact the paid OpenAI model API.

A new experiment covers **every eligible source unit** for both examination and translation. Requests are sent in small batches to limit output truncation; the dry run shows the exact number and expected costs for your sources. An older, preserved experiment planned 42 requests (including 18 LLM parsing calls); it completed 39 successfully and retained three examination validation failures. Never mix historical and new run results.

**Stop here unless you want to spend money.** Model prices in `config.json` are user-maintained estimates and may be outdated. Review current official prices and the printed estimate before deciding on a maximum budget. An API key is necessary only for the paid route. In Windows Git Bash or macOS Terminal, set it privately for that terminal session:

```bash
export OPENAI_API_KEY="YOUR_API_KEY"
```

Do not put the real key in `config.json`, Git commits, screenshots, or messages. Setting it does **not** itself send a request.

**Only if you explicitly decide to authorize paid model execution**, run the following example with a limit **you choose after reviewing the dry run**:

```bash
python transplate.py experiment --live --max-cost-usd YOUR_LIMIT
```

Replace `YOUR_LIMIT` with a positive dollar amount, such as `0.10` **only if that is an amount you have chosen to authorize**. The cap is an **internal estimate, not a guaranteed provider billing limit**. Output-token use (including reasoning tokens), pricing changes, and ambiguous network failures can affect actual cost. The program never deliberately repeats completed requests. Inconsistent mathematical/technical flags or a flawed source-grounded correction are model-quality errors that are **preserved and flagged for human assessment**, not reasons to discard a structurally complete paid response. If a response is structurally invalid, it is logged without retry, and the invocation continues with other requests. Before any more spending, run `python transplate.py recover --run output/runs/YOUR-RUN-ID` to recover any already-billed, structurally valid response from the same current experimental configuration **offline**. The original failed/usage records are preserved. Any truly invalid requests stay blocked on subsequent invocations; `python transplate.py experiment --live --skip-failed --max-cost-usd YOUR_LIMIT` explicitly continues only unattempted work without retrying them. Do not change prompts or configuration midway through the same experiment.

**Source fidelity matters:** The source PDF and independently extracted English, block IDs, and bounding boxes are stored in `source_blocks.csv`. Inspect original PDF page renderings to verify important boundaries, figures, complex layouts, and equations. Source-block construction is not scored as model performance.

### Route B — Produce an independent binary translation evaluation CSV (optional, free)

**Option 1: Use an existing run** after translations have completed (this is an independent binary CSV, **not** the new comparative translation dashboard):

```bash
python transplate.py template --run output/runs/YOUR-RUN-ID
```

Replace `YOUR-RUN-ID` with the exact folder printed by the experiment; do not literally type the placeholder. This creates `evaluation/translation_evaluation.csv` for reviewers and `evaluation/model_key.csv` to keep private.

**Option 2: Use your own translations without running an experiment:**

```bash
python transplate.py template --translations examples/translation_input.csv --out output/example_review
```

Your own UTF-8 input CSV must contain `document_id`, `page`, `unit_id`, `source_english`, and `translation_spanish`; `model` and `pdf_url` are optional. The included example illustrates the format; replace it with your own substantive material when appropriate.

The evaluation sheet has **four binary criteria**: semantic fidelity, technical accuracy, Spanish quality, and structural preservation. For each criterion, use **1 = meets it; 0 = fails; blank = not assessed**. Optional notes and review flags help record uncertainties. Keep `model_key.csv` concealed until assessment is complete; its anonymous IDs link to the review rows later.

**Protect your work:** Running `template` again for the same destination **overwrites its generated CSVs**. Before entering human scores, make a separate working copy of the review sheet. Do not sort or edit the private key alongside the reviewer sheet.

### Route C — Open an experimental dashboard (free)

You need an **existing experiment run** containing examination and translation outputs. New full-coverage runs do not require Route B or a translation CSV; historical runs still do.

```bash
python dashboard.py output/runs/YOUR-RUN-ID
```

Choose `output/runs/YOUR-RUN-ID/evaluation/blind_dashboard_france.html` for France alone or `blind_dashboard_all.html` in that folder to inspect **both Peru and France**, including preserved invalid examination responses when inspecting the historical run that recorded them. Open either HTML file in your browser; no API calls are made.

For new runs, examination evaluates each distinct response per source unit only once, sharing binary judgments (0 = does not meet; 1 = meets) across identical anonymous model outputs. Translation shows every distinct model translation of each source unit simultaneously and accepts rankings with ties. Identical translations share one rank. The historical dashboard retains its old interfaces. Validation failures appear with a warning and the original rejected response; their human scores are **diagnostic only**, never eligible for model qualification. New-run exports separate qualifying examination judgments, invalid diagnostics and translation rankings. Historical exports retain their existing fields. Each dashboard has its own **private** model-key CSV; share only the HTML with blind reviewers. Option letters may identify different models in different cases.

Progress saves locally in **that browser** when local storage is available. Periodically use **⋯ → Download checkpoint / results**. Import a checkpoint only into a dashboard of the **same run, scope and evaluation schema**. Older three-point checkpoints are incompatible, avoiding accidental reuse of differently defined scores. The complete final results export is not a resumable checkpoint.

Regenerating dashboards replaces generated HTML and private keys but not the experimental responses or saved browser state. Keep your checkpoints separately.

## 7. What's free, what may cost money, and what gets saved

| Action | Model API charge? | Main output |
|---|---:|---|
| `experiment --dry-run` | **No** | Cached PDFs, complete eligible units, cost plan |
| `experiment --live --max-cost-usd ...` | **Yes** | Saved model responses and per-request usage |
| `template --run ...` | **No** | Blind evaluation CSV and private model key |
| `template --translations ...` | **No** | Same files from a supplied CSV |
| `dashboard.py <run-dir>` | **No** | Two scoped HTML dashboards and separate private model keys |
| Browser scoring and checkpoint export | **No** | Locally saved judgments and downloaded JSON |
| `python -m unittest discover -s tests -v` | **No** | Existing offline verification output |

**Avoid accidental repeated work:** Reuse the same existing run folder, don't regenerate a scored CSV, export dashboard checkpoints regularly, and don't rerun paid requests to investigate a failure unless you have deliberately reviewed and authorized that action.

## 8. Common beginner problems

| Problem | What to check |
|---|---|
| `python: command not found` | Install Python 3.10+ and reopen the terminal. On macOS try `python3`; confirm `python --version` or `python3 --version`. |
| `No module named 'fitz'` or `No module named 'openai'` | Activate `.venv`, then run `python -m pip install -r requirements.txt` in the project root. |
| Terminal is in the wrong folder | Run `pwd`; switch to `~/Documents/GitHub/transplate-clean` and confirm `transplate.py` exists. |
| PDF download fails | Confirm internet access and the configured URL; an already cached PDF may allow an unchanged rerun offline. |
| Dry run shows an unfamiliar run ID | The run ID follows the actual sources/configuration/prompts. Copy the printed path rather than relying on an example ID. |
| `OPENAI_API_KEY` missing | Only live execution needs it. Set it privately in the terminal session executing the command. |
| Cost estimate differs from later usage | Tokens and prices are approximations; inspect `usage.jsonl` and provider billing, especially if reasoning tokens are used. |
| Structurally invalid paid response | Current invocation records it and proceeds without retry. Use offline `recover --run` to reclaim any evaluable billed response; `--skip-failed` lets a later invocation finish unattempted requests without paying to repeat failures. |
| `template --run` reports no translations | Complete at least one translation request in that run, or use `--translations` with your own CSV. |
| Dashboard cannot find translation review CSV | Generate it first using `template --run` for that same experiment. |
| Reviewer can see model identities | You may have shared a private `model_key.csv` or `dashboard_model_key_france.csv`. Distribute only the reviewer file/dashboard. |
| Dashboard progress disappeared | Check whether you changed browser/profile or cleared local storage. Import a previously exported checkpoint. |
| Dashboard checkpoint won't import | It must be a resumable checkpoint from the **same run, scope and evaluation schema**; a final results export is not a resumable checkpoint. |
| Extracted blocks look wrong | Compare `source_blocks.csv` with the rendered PDF using `python transplate.py verify-extraction --document france` (or another configured document). Block identification is programmatic, not a model response. |

## 9. What to read, edit, or leave alone

- **Read this guide first** for a step-by-step path; `README.md` is the shorter operational reference.
- **Edit `config.json`** when setting up a new experiment. Do not edit Python merely to switch source PDFs or models; all eligible units are included automatically.
- **Run `transplate.py`** for experiments and translation CSVs; **run `dashboard.py`** for either scoped dashboard. You generally don't need to edit either script to use it.
- **Preserve `output/`** after collecting real model results or beginning human evaluation. Back up scored spreadsheets and downloaded dashboard checkpoints.
- **Keep model keys private** during blind review. The dashboard's generated HTML can be shared with reviewers; the private key cannot.
- **Leave `.venv/`, `__pycache__/`, and `old/` alone** during ordinary use. `old/` contains PatchMyMess backups, not application code.
- **Use `tests/` only when checking project behavior or deliberately developing it**, not as a prerequisite for normal evaluation.

**Using Transplate** (choosing a configuration, running it, and reviewing outputs) is different from **developing Transplate** (editing its scripts). Don't change the implementation to solve a user-operation problem. For subsequent code modifications, use a narrowly scoped PatchMyMess change after identifying a demonstrated need.

## 10. A sensible first exercise (no paid requests)

After setup, try the independent CSV pathway using its included starter data:

```bash
python transplate.py template --translations examples/translation_input.csv --out output/example_review
```

Open `output/example_review/translation_evaluation.csv` in a spreadsheet program. Locate the source English, Spanish translation, empty criteria, and anonymous evaluation ID. Confirm that `model_key.csv` is a **separate file**, then keep it out of the reviewer's view. No PDF download or API key is necessary for this exercise.

If you already have a completed experiment, generate its dashboards using Route C and explore navigation **before** entering serious scores. Export a practice checkpoint, then import it, to learn how to preserve progress. Work on a copy if you intend to reset or experiment with scoring.

**The guiding principle:** Prepare for free, authorize paid work deliberately, keep source and model identities traceable, and let human evaluation—not additional engineering—determine what the results mean.

## Model fields (current experiment)

**Parsing and source-unit identification are entirely programmatic, never an LLM stage in new experiments.** Source blocks have programmatically assigned `id`, `unit_id`, page and coordinates. Current model outputs use the retained compact terms: examination `id/v/m/ml/e/wr/mi/di/rn`, and translation `id/sp`. See `prompts/` and `schemas/`. Historical parsing outputs (`src/x/m`) are still supported for reviewing old experiments, not generated anew. The evaluation CSV has descriptive column names because it is a separate human-review format. Previously completed experiments are preserved.

### Review earlier results in the revised dashboard without spending money

From the project root, after preparing the new full-coverage run with a dry run:

```bash
python dashboard.py output/runs/8179466b4f81 --reuse-from output/runs/e514a34f97c0
```

Open the new run's `evaluation/blind_dashboard_france.html` or `blind_dashboard_all.html`. The new interface assesses distinct available examination responses once and ranks available translations with ties. It shows missing outputs rather than inventing them. Keep `dashboard_model_key_*.csv` private: these files include model names and original run IDs. Historical results retain their original prompting and batch conditions and **are not counted as completed requests in the new experiment**. Generating the dashboard makes zero paid API calls.

## Independent issue evaluation (current examination schema)

Every proposed proofreading correction (`e`) has its own **Replace / With** entry and its own 0/1 judgment. Every independently reported technical finding (`findings`) includes a verbatim source excerpt, a technical category and a brief reason, with its own 0/1 judgment. There is no fixed limit on issues within one textual unit. You still score the **five whole-unit criteria independently**, especially Conservative proofreading: a model can receive 1 on a valid individual patch and 0 overall if it missed other errors. If it proposes no patches or findings, judge the whole-unit criteria for omissions. The one-click Meets control can populate scores, which remain individually editable. Old experiments do not retroactively gain structured findings.

The dashboard export keeps whole-unit examination, individual issue accuracy, and translation rankings **in different fields**. After completing blind review, place the dashboard's exported evaluation JSON in `output/runs/<run-id>/evaluation/` and run `python scripts/compare.py --run output/runs/<run-id> --scope france` (or `--scope all` for a complete combined-document export). The script locates the original local `examine.jsonl` and `translate.jsonl`, matching private model key, and completed evaluation automatically; it verifies their correspondence and generates `private_comparison_<scope>.html`, a metric-summary CSV and a detailed per-unit CSV locally. For an export stored elsewhere, append `--evaluation PATH_TO_JSON`. This makes no API calls. Individual proposed-issue accuracy is reported separately from whole-unit proofreading, which detects omissions; do not combine distinct measures. Generated reports reveal model identities and must stay private until blind scoring is finished.

**Examination response boundary:** New runs use a per-request strict OpenAI JSON schema keyed by the exact programmatic source IDs. Python converts the keyed API response into the existing `items` records and verifies that the returned structure and IDs match. Semantically inconsistent flags, invalid correction spans, and inadequate excerpts remain visible as model-quality assessment warnings rather than forcing another paid run. `recover --run` can reclaim an older failed response *within the identical experimental run* offline, retaining the original error and usage audit logs; it never combines responses from different prompt versions. Translation retains its existing response format.

## Production workflow (after completing the France experiment)

Use **one** `translation_review.xlsx` for both authorizing English corrections and reviewing the Spanish drafts. It retains the original 19 human-review columns and adds `approve_patch_indices` and `authorization`. Running `production prepare` retrieves existing experimental examination results **and** stored GPT-4.1 mini Spanish drafts; it does not call the API. If you previously generated `authorization.csv`, repeat `production prepare` after applying the patch: it copies any saved approvals into the single XLSX and preserves the old file only until migration is verified.

For each source unit, set `authorization=Approved`; optionally specify the **1-based** approved patch indices (leave blank if none). Next run `production authorize`, then `production translate` **without `--live`**. The dry run reports how many saved translations can be reused and whether corrections require any paid calls. If zero calls are pending, skip paid translation. Otherwise, use `--live --max-cost-usd` only after reviewing the estimate. Finally, run `production export` to fill the same XLSX without deleting human work. Consult README's **Production translation and human review** section for exact commands. The existing Google Apps Script recognizes `spanish_draft`, `sp_human`, and `status` by column name **after** the XLSX is imported and converted into a native Google Sheet; Excel itself does not execute Apps Script.
