# 02 · Discovery

Discovery turned the brief into a reviewed, approved implementation plan **before any code was written**. The brief explicitly said: "DO NOT create or modify any files yet. I want to review and approve the implementation plan first."

## 1. Requirements analysis

### 1.1 Functional requirements (from the brief)

| ID | Requirement | Module |
|---|---|---|
| FR1 | Programme and squad health overview | Delivery Control Tower |
| FR2 | RAID analysis, hygiene and recommendations | AI RAID Analyst |
| FR3 | Sprint and PI health metrics | Sprint & PI Health Analyser |
| FR4 | Leadership status reporting | AI Leadership Reporting |
| FR5 | Decision support | Delivery Decision Assistant |
| FR6 | Import CSV and Excel data | Data Import (supporting) |
| FR7 | Simulate an enterprise programme with five Agile squads | Synthetic data generator |

### 1.2 Non-functional requirements (from the brief)

| ID | Requirement | How the design meets it |
|---|---|---|
| NFR1 | Deterministic business rules for calculations and health | Pure-Python rules engine; thresholds in versioned YAML |
| NFR2 | AI for interpretation and recommendations only | AI layer sits *after* the rules and cannot assign a RAG status |
| NFR3 | **Never allow AI to invent project facts** | Fact packs, structured output, grounding validator |
| NFR4 | Traceability to source records | Import lineage, source IDs on health snapshots, AI citations |
| NFR5 | Modular and testable | Layered packages; mock LLM; pytest at every layer |
| NFR6 | No paid enterprise software | Python, Streamlit, SQLite and open-source libraries |
| NFR7 | No employer systems | Synthetic data; local-only app |
| NFR8 | LLM configurable separately | `config/llm.yaml` with a provider abstraction |
| NFR9 | Start with a simple working MVP | MVP = Phases 0–3 |

### 1.3 Deliverables requested at discovery

The brief asked for seven planning outputs. All were produced in the approved plan and are carried into [03_architecture_and_solution_design.md](03_architecture_and_solution_design.md) and [04_product_backlog.md](04_product_backlog.md).

| Deliverable | Where |
|---|---|
| Solution architecture | 03 §1 |
| Application module breakdown | 03 §2 |
| Database schema | 03 §5 |
| Synthetic data design | 03 §6 |
| Development roadmap | 04 (epics by phase) |
| Testing strategy | 03 §8 |
| Estimated implementation complexity | 04 §5 |

## 2. Stakeholder Q&A (design clarifications)

After reviewing the plan, the product owner asked six questions. The answers below are the rationale behind the main design decisions.

| # | Question | Answer (summary) | Decision it supports |
|---|---|---|---|
| Q1 | **Why SQLite instead of PostgreSQL?** | No server, free, a single file, built into Python, and deploys on the free Streamlit Community Cloud. Easily handles the data volume. PostgreSQL's multi-user strengths aren't needed by a single-user demo. Repository functions with plain SQL keep a path to PostgreSQL open later. | [ADR-001](03_architecture_and_solution_design.md#adr-001--sqlite-as-the-database) |
| Q2 | **Why separate the rules engine from the AI layer?** | Rules *decide* (repeatable, auditable, unit-testable). AI *interprets* (wording may vary, facts may not). The separation gives trust, testability, a working app without AI, and changes made through config. | [ADR-005](03_architecture_and_solution_design.md#adr-005--rules-engine-separated-from-the-ai-layer) |
| Q3 | **What is a fact pack?** | The only material the LLM is allowed to see: a JSON document of records and rule results, each with a `source_id`, plus a hash recorded with the AI output. | [03 §3](03_architecture_and_solution_design.md#3-anti-hallucination-contract) |
| Q4 | **How does RAG validation work?** | Red/Amber/Green is computed from YAML thresholds, never by AI. Results are saved with the value, status, rule version and source IDs, and the threshold edges are tested. Retrieval-Augmented Generation is done with deterministic SQL retrieval rather than vector search, so inputs are known. | [ADR-007](03_architecture_and_solution_design.md#adr-007--thresholds-in-versioned-yaml) |
| Q5 | **How does an LLM interact with Python?** | Over an HTTP API behind one `LLMProvider.complete()` interface. Mock, OpenAI-compatible (including Ollama) and Anthropic implementations sit behind it. The response is parsed by pydantic, validated, stored and then shown. | [ADR-004](03_architecture_and_solution_design.md#adr-004--mock-llm-provider-as-the-default) |
| Q6 | **How do we prevent hallucinated delivery insights?** | Nine layers: restricted input, structured output, citation check, number check, no AI status decisions, visible verification, human approval, audit trail, and adversarial tests. | [ADR-006](03_architecture_and_solution_design.md#adr-006--structured-json-output-plus-a-grounding-validator) |

The answer to Q6 states its limit plainly: the design cannot guarantee *insightful* interpretation, only that **every displayed fact is traceable to a source record**. A human judges the quality of the insight.

## 3. Environment discovery

| Finding | Evidence | Resolution |
|---|---|---|
| The project folder was empty | `Glob **/*` returned "No files found" | Greenfield build |
| Python was not installed | `python --version` showed "Python was not found…" | Product owner chose winget; Python 3.12.10 installed |
| Git was available | `git version 2.56.1.windows.1` | Repository initialised (no commits yet) |

## 4. Assumptions

| # | Assumption | Status |
|---|---|---|
| A1 | The default LLM provider is **Mock**. OpenAI-compatible (including local Ollama) and Anthropic are optional | Confirmed in plan approval |
| A2 | Plain `sqlite3` with repository functions, no ORM | Confirmed |
| A3 | Single-user local app, no auth. "Approved by" is free text | Confirmed |
| A4 | Export as Markdown and DOCX; PDF out of MVP scope | Confirmed |
| A5 | Python 3.12 installed per user via winget | Confirmed by product owner |

## 5. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| LLM output variability or hallucination | High | High | Structured JSON schema, grounding validator, mock provider, adversarial tests |
| Synthetic data feels artificial | Medium | Medium | Squad "personalities", realistic banking vocabulary, planted scenarios |
| Scope creep across five modules | Medium | Medium | MVP = Phases 0–3; later modules reuse the grounding pattern |
| Paid API cost during development | Low | Medium | Mock is the default; Ollama is a free local option |
| Windows environment friction | Medium | Low | Per-user Python install, project `.venv`, documented commands |

## 6. Open questions (for later phases)

| # | Question | Decide by |
|---|---|---|
| OQ1 | Should the "What changed this week" summary compare `health_snapshot` records or import batches? | Phase 2 |
| OQ2 | What tolerance should the grounding validator use for numbers (exact vs. ±1 decimal place)? | Phase 3 |
| OQ3 | Should duplicate-RAID detection use simple text similarity only, or add AI clustering? | Phase 3 |
| OQ4 | Should the app be deployed publicly on Streamlit Community Cloud (mock provider)? | Phase 7 |
