# AI Agile Delivery Manager Assistant

An independent portfolio project that simulates an enterprise technology programme
("Project Meridian", five Agile squads) using **synthetic data only**.

**Principle: rules compute, AI interprets.** All metrics and RAG statuses come from
deterministic, tested Python rules. The LLM only explains and recommends from a cited
"fact pack", and every claim is validated against source record IDs.

## Modules
1. Delivery Control Tower
2. AI RAID Analyst
3. Sprint and PI Health Analyser
4. AI Leadership Reporting
5. Delivery Decision Assistant

## Quick start (Windows PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
streamlit run app/Home.py
```

## LLM configuration
Copy `config/llm.yaml.example` to `config/llm.yaml`. The default provider is `mock`
(no key, no cost). OpenAI-compatible endpoints (including local Ollama) and Anthropic
are optional: `pip install -e ".[llm]"`.

## Synthetic data
```powershell
python -m adm.synthetic.generate   # writes data/synthetic/*.csv, programme_data.xlsx
```
Then open **Data Import → Load demo dataset**. `data/samples/dirty_import.xlsx` shows
row-level validation errors. Planted scenarios (the golden test answers) are listed in
`src/adm/synthetic/scenarios.py`.

## Status
- Phase 0 (foundations): done
- Phase 1 (schema, synthetic data, validated CSV/Excel import with lineage): done
- Phase 2 (rules engine + Delivery Control Tower, non-AI MVP): done. Thresholds and weights
  live in `config/thresholds.yaml`; golden tests in `tests/scenario/` prove every planted
  scenario is detected and the control squad (Payments) stays GREEN.
