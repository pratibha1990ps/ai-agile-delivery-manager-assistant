# AI Agile Delivery Manager Assistant

An independent portfolio project that simulates an enterprise technology programme
("Project Meridian", five Agile squads) using **synthetic data only**.

**Principle: rules compute, AI interprets, humans decide.** All metrics, RAG statuses and
escalation candidates come from deterministic, tested Python rules. The AI only explains and
recommends from a cited "fact pack"; every statement is validated against the facts it cites,
and suggested actions take effect only when a named person approves them.

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

## AI RAID Analyst (Phase 3)
Open **AI RAID Analyst**, choose a scope (programme or squad) and one of five questions, then
**Analyse**. Results are shown in four separate layers:

1. **Verified facts** computed by the rules (shown even if the AI fails)
2. **AI interpretation**: findings with citation chips, marked verified, flagged or rejected
3. **Suggested actions**: approve or reject, with your name and a comment
4. **Human decisions**: the review history

Every run is recorded on **AI Audit Log** (provider, model, prompt version, fact pack and its
SHA-256 fingerprint, raw answer, validation outcome, reviews). The *Demo: simulate a faulty AI
response* option shows the validator rejecting invented IDs, wrong numbers and RAG overrides.

## LLM configuration
Copy `config/llm.yaml.example` to `config/llm.yaml` (git-ignored). The default provider is
`mock`: deterministic templates, not a language model, with no key and no cost. Optional real
providers (`pip install -e ".[llm]"`):

| Provider | Settings |
|---|---|
| Ollama / LM Studio (free, local) | `provider: openai_compatible`, `base_url: http://localhost:11434/v1`, `model: llama3.1` |
| OpenAI-compatible hosted service | `provider: openai_compatible`, `base_url`, `model`, `api_key_env: OPENAI_API_KEY` |
| Anthropic Claude | `provider: anthropic`, `model: claude-opus-5-5`, `api_key_env: ANTHROPIC_API_KEY` (or rely on the SDK's own credential lookup) |

Keys are read from environment variables at call time and never written to config or logs.
**Local models:** a programme-wide fact pack is roughly 8-10k tokens. Many local models default
to a smaller context window and silently cut the prompt; lower `ai.max_facts` in
`config/settings.yaml` (for example to 30) or raise the model's context size.

## Synthetic data
```powershell
python -m adm.synthetic.generate   # writes data/synthetic/*.csv, programme_data.xlsx
```
Then open **Data Import → Load demo dataset**. `data/samples/dirty_import.xlsx` shows
row-level validation errors. Planted scenarios (the golden test answers) are listed in
`src/adm/synthetic/scenarios.py`.

## Development notes
- Streamlit reloads files under `app/` automatically, but not the `adm` package under `src/`.
  Restart `streamlit run` after changing rules, services or AI code.
- Thresholds and weights live in `config/thresholds.yaml` (versioned; currently 1.3).
- Prompts live in `src/adm/ai/prompts/<id>/<version>.md`. Published versions are immutable
  (a test pins their SHA-256); change wording by adding a new version.

## Status
- Phase 0 (foundations): done
- Phase 1 (schema, synthetic data, validated CSV/Excel import with lineage): done
- Phase 2 (rules engine + Delivery Control Tower, non-AI MVP): done. Golden tests in
  `tests/scenario/` prove every planted scenario is detected and the control squad
  (Payments) stays GREEN.
- Phase 3 (AI foundation + AI RAID Analyst + AI Audit Log): done. Provider abstraction (mock,
  OpenAI-compatible, Anthropic), fact packs, versioned prompts, grounding validator, audit log
  and human review. All tests run without network access or a paid API.

Full product documentation (vision, discovery, architecture and ADRs, backlog, evidence log)
is in [`docs/`](docs/README.md).
