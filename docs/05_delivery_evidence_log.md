# 05 · Delivery Evidence Log

A dated record of what was done, how, and the evidence for it. Command outputs are quoted exactly as captured during the build session. Nothing is paraphrased into a better result.

**Environment:** Windows 11 Home · PowerShell and Git Bash · built with an AI pair programmer (Claude Code)

## Timeline

| # | Date | Stage | Activity | Evidence |
|---|---|---|---|---|
| 1 | 2026-11-19 | Idea | Product brief received: five modules, synthetic data, Python/Streamlit/SQLite, deterministic rules, AI must not invent facts. Explicit instruction to **plan first, no files** | [01_product_vision.md](01_product_vision.md) |
| 2 | 2026-11-19 | Discovery | Workspace inspected: empty folder (`Glob **/*` → "No files found") | §Evidence A |
| 3 | 2026-11-19 | Discovery | Implementation plan written: architecture, modules, schema, synthetic data, roadmap, testing, complexity. **Approved by the product owner** | [02_discovery.md](02_discovery.md), [03](03_architecture_and_solution_design.md) |
| 4 | 2026-11-19 | Discovery | Six design questions answered (SQLite vs. PostgreSQL, rules vs. AI, fact pack, RAG validation, LLM ↔ Python, hallucination prevention) | [02 §2](02_discovery.md#2-stakeholder-qa-design-clarifications) |
| 5 | 2026-11-19 | Execution: Phase 0 | Python found to be missing. Product owner chose an install method: **"I'll run winget (Recommended)"** | §Evidence B |
| 6 | 2026-11-19 | Execution: Phase 0 | Python 3.12.10 installed for the current user with winget | §Evidence C |
| 7 | 2026-11-19 | Execution: Phase 0 | Git repository initialised; `.venv` created; project installed in editable mode with dev extras | §Evidence D |
| 8 | 2026-11-19 | Execution: Phase 0 | Phase 0 files created (config, loader, RAG classifier, Home, 7 placeholder pages, tests) | §Phase 0 artefacts |
| 9 | 2026-11-19 | Execution: Phase 0 | Tests: **30 passed**. Ruff found 1 issue (E501), fixed, re-run clean | §Evidence E |
| 10 | 2026-11-19 | Execution: Phase 0 | Streamlit home page started and checked in the browser at `http://localhost:8501` | §Evidence F |
| 11 | 2026-11-19 | Execution: Phase 1 | Phase 1 started: schema, connection, repositories, ingestion and synthetic scenarios files created (**in progress**) | §Phase 1 artefacts |
| 12 | 2026-11-19 | Documentation | Product documentation set (this `docs/` folder) written in a parallel session | [README.md](README.md) |
| 13 | 2026-11-19 | Execution: Phases 1–2 | Ingestion, generator, Data Import page, rules engine and Control Tower completed in the main build session. Test suite at **112 passed** before the audit | §Evidence G |
| 14 | 2026-11-19 | Execution: Phase 1 | **Generator audit** against the data brief. Four gaps closed: forward backlog (~650 items), feature ↔ work-item consistency, RAID descriptions, per-entity seeded streams. No schema or importer changes | §Evidence H |

## Evidence

### A · Empty workspace (greenfield)
```text
Glob **/*  →  No files found
```

### B · Prerequisite check
```text
> python --version
Python was not found; run without arguments to install from the Microsoft Store, or disable this
shortcut from Settings > Apps > Advanced app settings > App execution aliases.
> git --version
git version 2.56.1.windows.1
```
Decision recorded: the product owner selected **"I'll run winget (Recommended)"** over a manual install or `uv`.

### C · Python installation
```text
> winget install --id Python.Python.3.12 --scope user --silent ...
Found Python 3.12 [Python.Python.3.12] Version 3.12.10
Downloading https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
Successfully verified installer hash
Starting package install...
Successfully installed

> & "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" --version
Python 3.12.10
```

### D · Repository, virtual environment and dependencies
```text
> git init -q
> python -m venv .venv
> .\.venv\Scripts\python.exe -m pip install -q -e ".[dev]"
> python -c "import streamlit, pandas, pydantic; print(...)"
1.67.0 3.1.1 2.14.3          # streamlit, pandas, pydantic versions
```
The git repository has **no commits yet**. Committing is waiting for the product owner's go-ahead.

### E · Tests and lint (Phase 0 exit check)
```text
> pytest -q
..............................                                           [100%]
30 passed in 6.02s

> ruff check .
E501 Line too long (102 > 100)
  --> app\Home.py:29:101
Found 1 error.
```
The long line in [`app/Home.py`](../app/Home.py) was wrapped. Re-run:
```text
> ruff check .
All checks passed!
```
The 30 tests break down as config loading and validation ([`tests/unit/test_config.py`](../tests/unit/test_config.py)), RAG boundary cases ([`tests/unit/test_rag.py`](../tests/unit/test_rag.py)), and a render smoke test of Home plus the 7 pages ([`tests/ui/test_pages_smoke.py`](../tests/ui/test_pages_smoke.py)).

### F · Streamlit home page verified in the browser
The server was started from [`.claude/launch.json`](../.claude/launch.json) on port 8501. Page text captured from `http://localhost:8501/`:
```text
AI Agile Delivery Manager Assistant
Project Meridian · Fictional retail-bank digital modernisation programme (synthetic data)
Portfolio demo using synthetic data only. No real client, company or employer data.
As-of date            2026-11-19
Threshold rules version  1.0
LLM provider          mock
LLM settings loaded from: llm.yaml.example · model mock-grounded-v1 · v0.1.0
```
The sidebar listed all 8 pages: Home, Control Tower, RAID Analyst, Sprint PI Health, Leadership Report, Decision Assistant, Data Import and AI Audit Log.

### G · Baseline before the generator audit
```text
> pytest -q
112 passed in 8.23s
```
Regenerating in memory with seed 42 matched all 11 CSVs on disk exactly (`match` for every entity). Audit findings at that point:

| Check | Result |
|---|---|
| Volumes | programme 1, squad 5, PI 2, sprint 10, commitment 40, feature 40, **work_item 580**, objective 50, milestone 12, dependency 35, RAID 90 |
| Feature ↔ work-item consistency | **19 contradictions.** For example, items marked Done on "Not Started" features (F-IDS-08 ×8), and Not Done items on "Done" PI-1 features |
| RAID descriptions | **0 of 90 filled** |
| Hardcoded RAG | None. Milestones are only Planned/Complete; RAG is calculated by the rules |

### H · Generator gap closure ([`src/adm/synthetic/generate.py`](../src/adm/synthetic/generate.py))

| Gap | Change |
|---|---|
| Volume | Refined backlog (6–8 `To Do` items per squad) for S09–S10, with no commitment rows |
| Consistency | Sprint work links only to started features. PI-1 end-of-PI spill-over moves off "Done" features (Payments → `F-PAY-05`) |
| Descriptions | Templated, fictional descriptions for generic RAID items; hand-written ones for the 12 planted items |
| Stability | `entity_rng(seed, entity)`: one independent seeded stream per entity |

```text
> pytest -q
118 passed in 8.54s            # 112 existing + 6 new generator tests

> python -m adm.synthetic.generate
work_item                   634 rows      (other volumes unchanged)

> python -m adm.synthetic.generate --out <scratch>/run2   # then cmp each CSV
identical  (all 11 CSVs)

Strict import of the regenerated CSVs into an in-memory DB:
clean csv import: imported errors: 0 warnings: 0
xlsx import: imported True        # same row counts as the CSV import
dirty import: rejected errors: 9 rows written: 0

> ruff check .
All checks passed!
```

Squad personalities after regeneration (closed sprints S01–S07, from `sprint_squad_commitment.csv`):

| Squad | Say/do | Velocity CV | Added pts | Carry-over pts |
|---|---|---|---|---|
| PAY | 0.99 | 0.05 | 20 | 23 |
| ONB | 0.85 | 0.08 | 95 | 129 |
| DAT | 0.81 | 0.19 | 19 | 51 |
| MOB | 0.79 | 0.41 | 55 | 101 |
| IDS | 0.88 | 0.11 | 14 | 30 |

Note: `data/adm.db` keeps the previously imported data until the database is reset and the demo dataset is re-imported.

## Artefacts by phase

### Phase 0 artefacts ✅ Done

| File | Purpose |
|---|---|
| [`pyproject.toml`](../pyproject.toml) | Package `adm` (src layout); core dependencies; `[dev]` and `[llm]` extras; ruff and pytest config |
| [`.gitignore`](../.gitignore), [`.env.example`](../.env.example), [`README.md`](../README.md) | Repository hygiene, key template, quick start |
| [`config/settings.yaml`](../config/settings.yaml) | Programme name, as-of date `2026-11-19`, database path |
| [`config/thresholds.yaml`](../config/thresholds.yaml) | RAG cut-offs v1.0 for 5 metrics, RAID and dependency thresholds, health weights |
| [`config/llm.yaml.example`](../config/llm.yaml.example) | Provider `mock` by default; Ollama, OpenAI and Anthropic examples |
| [`src/adm/config.py`](../src/adm/config.py) | Typed pydantic loader with validation and LLM config precedence |
| [`src/adm/rules/rag.py`](../src/adm/rules/rag.py) | `classify()`: the single place a RAG status is assigned |
| [`app/Home.py`](../app/Home.py), `app/pages/1–7` | Home page plus placeholder module pages |
| `tests/unit/*`, `tests/ui/*` | 30 tests (see Evidence E) |
| [`.claude/launch.json`](../.claude/launch.json) | Dev server config for the in-app browser preview |

### Phase 1 artefacts ✅ Done

The table below was written while Phase 1 was in progress. Since then, all of these components, the generated data, the Data Import page and their tests have been completed. Evidence G and H above verify them, with 118 tests passing.

| File | Purpose | State |
|---|---|---|
| [`src/adm/db/schema.sql`](../src/adm/db/schema.sql) | 16 tables: 11 imported entities, plus `import_batch`, `health_snapshot`, `ai_output`, `ai_citation` and `decision` | Written |
| [`src/adm/db/connection.py`](../src/adm/db/connection.py) | `connect`, `init_db`, `reset_db`, `ADM_DB_PATH` override | Written |
| [`src/adm/db/repositories.py`](../src/adm/db/repositories.py) | `fetch_df`, `get_record`, `find_record` (citation drill-down), counts, import history | Written |
| [`src/adm/ingestion/schemas.py`](../src/adm/ingestion/schemas.py) | Pydantic row models for 11 entities and the `ENTITIES` registry with foreign-key map and import order | Written |
| [`src/adm/ingestion/loaders.py`](../src/adm/ingestion/loaders.py) | CSV/Excel loading | Written, not yet tested |
| [`src/adm/ingestion/validator.py`](../src/adm/ingestion/validator.py) | Row and cross-entity validation | Written, not yet tested |
| [`src/adm/ingestion/importer.py`](../src/adm/ingestion/importer.py) | Import with lineage | Written, not yet tested |
| [`src/adm/synthetic/scenarios.py`](../src/adm/synthetic/scenarios.py) | Planted scenarios for the synthetic data | Written, not yet tested |
| [`src/adm/synthetic/generate.py`](../src/adm/synthetic/generate.py), `data/synthetic/*`, `data/samples/dirty_import.xlsx` | Seeded generator and its output | ✅ Done (see Evidence H) |
| [`app/pages/6_Data_Import.py`](../app/pages/6_Data_Import.py), `tests/integration/`, `tests/unit/test_validation.py`, `tests/unit/test_synthetic.py` | Import UI and Phase 1 tests | ✅ Done |

**Deviation recorded:** the plan put the generator at `data/generator/`. It is being built as an importable package under `src/adm/synthetic/`, so that tests and the UI can import it.

## How to reproduce the Phase 0 evidence
```bash
.\.venv\Scripts\python.exe -m pytest -q
```
```bash
.\.venv\Scripts\python.exe -m ruff check .
```
```bash
.\.venv\Scripts\python.exe -m streamlit run app/Home.py
```
