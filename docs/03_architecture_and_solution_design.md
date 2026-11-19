# 03 · Architecture & Solution Design

**Guiding principle: rules compute, AI interprets.** Deterministic, tested Python produces every number, RAG status and flag. The LLM only explains, summarises and recommends, using a cited fact pack. The app validates its output before showing it.

## 1. Solution architecture

```mermaid
flowchart TB
    subgraph UI["Streamlit UI (app/)"]
        CT[Control Tower] --- RA[RAID Analyst] --- HA[Sprint & PI Health] --- LR[Leadership Report] --- DA[Decision Assistant]
        DI[Data Import] --- AL[AI Audit Log]
    end

    subgraph SVC["Services layer (src/adm/services)"]
        S[One orchestrator per module]
    end

    subgraph CORE["Domain core"]
        R["Rules engine<br/>(src/adm/rules)<br/>pure functions + YAML thresholds"]
        F["Fact pack builder<br/>(src/adm/facts)<br/>facts + source IDs + hash"]
    end

    subgraph AI["AI layer (src/adm/ai)"]
        P["LLMProvider interface<br/>Mock · OpenAI-compatible · Anthropic"]
        G["Grounding validator<br/>schema · citations · numbers · no-RAG"]
        AU[(ai_output / ai_citation<br/>audit trail)]
    end

    subgraph DATA["Data layer"]
        ING["Ingestion (src/adm/ingestion)<br/>pydantic row models · FK checks · lineage"]
        DB[("SQLite<br/>src/adm/db/schema.sql")]
        SRC[/"CSV / Excel<br/>synthetic data"/]
    end

    UI --> SVC
    SVC --> R
    SVC --> F
    R --> F
    F --> P --> G --> AU
    G --> SVC
    SRC --> ING --> DB
    R --> DB
    SVC --> DB
```

### Request flow for an AI-assisted view

```mermaid
sequenceDiagram
    participant U as Delivery Manager
    participant UI as Streamlit page
    participant S as Service
    participant R as Rules engine
    participant F as Fact pack builder
    participant L as LLM provider
    participant V as Grounding validator
    participant DB as SQLite

    U->>UI: Open RAID Analyst
    UI->>S: analyse(scope)
    S->>DB: load records
    S->>R: compute flags and metrics
    R-->>S: results + source IDs
    S->>F: build fact pack
    F-->>S: JSON + hash
    S->>L: complete(system, user, schema)
    L-->>S: JSON response
    S->>V: validate(response, fact pack)
    V-->>S: verified / flagged / rejected findings
    S->>DB: store ai_output + citations
    S-->>UI: findings with citation chips
    U->>UI: click citation
    UI->>DB: fetch source record
```

### Key design decisions (summary)

| Concern | Decision | ADR |
|---|---|---|
| Storage | SQLite, single file | [ADR-001](#adr-001--sqlite-as-the-database) |
| Data access | stdlib `sqlite3` with repository functions, no ORM | [ADR-002](#adr-002--stdlib-sqlite3-without-an-orm) |
| UI | Streamlit multipage app | [ADR-003](#adr-003--streamlit-for-the-ui) |
| LLM default | Mock provider; real providers are opt-in | [ADR-004](#adr-004--mock-llm-provider-as-the-default) |
| Responsibility split | Rules decide; AI interprets | [ADR-005](#adr-005--rules-engine-separated-from-the-ai-layer) |
| AI safety | Structured JSON plus a grounding validator | [ADR-006](#adr-006--structured-json-output-plus-a-grounding-validator) |
| Thresholds | Versioned YAML | [ADR-007](#adr-007--thresholds-in-versioned-yaml) |
| Import | All-or-nothing by default | [ADR-008](#adr-008--all-or-nothing-import-by-default) |

## 2. Application module breakdown

| Module | Deterministic (rules) | AI (interpretation) |
|---|---|---|
| **Delivery Control Tower** | Squad RAG grid, milestone status, critical-path dependencies, top risks, trend vs. last sprint, drill-down | Optional "what changed this week" summary, with citations |
| **AI RAID Analyst** | Missing owner, overdue actions, stale items, high-score risks without mitigation, ageing issues, risks past trigger date, duplicate candidates | Themes, patterns across squads, suggested mitigations, escalation candidates |
| **Sprint & PI Health Analyser** | Say/do, velocity and coefficient of variation, scope change %, carry-over %, blocked days, PI objective achievement, capacity utilisation | Squad narrative, root causes linked to evidence, coaching suggestions |
| **AI Leadership Reporting** | Overall RAG, KPIs, milestones, top risks, decisions needed | Executive summary and highlights/lowlights; edit → approve → export (Markdown/DOCX) |
| **Delivery Decision Assistant** | What-if impact on capacity, dates and dependent items | Option analysis and recommendation; logged to the `decision` table |
| Data Import *(supporting)* | Upload, validate, row-level error report, lineage | — |
| AI Audit Log *(supporting)* | Every AI call: model, prompt version, fact-pack hash, validation | — |

**Package layout**: real paths on disk, with some still placeholders:

| Path | Responsibility | State |
|---|---|---|
| [`src/adm/config.py`](../src/adm/config.py) | Typed loader for settings, thresholds and LLM config | Built (Phase 0) |
| [`src/adm/rules/rag.py`](../src/adm/rules/rag.py) | The single place a RAG status is assigned | Built (Phase 0) |
| [`src/adm/db/schema.sql`](../src/adm/db/schema.sql) | SQLite schema | Built (Phase 1) |
| [`src/adm/db/connection.py`](../src/adm/db/connection.py) | Connect, init, reset, `ADM_DB_PATH` override | Built (Phase 1) |
| [`src/adm/db/repositories.py`](../src/adm/db/repositories.py) | Read-side queries and cross-entity record lookup | Built (Phase 1) |
| [`src/adm/ingestion/schemas.py`](../src/adm/ingestion/schemas.py) | Pydantic row models and the entity registry | Built (Phase 1) |
| `src/adm/ingestion/loaders.py`, `validator.py`, `importer.py` | CSV/Excel loading, cross-entity validation, import with lineage | In progress (Phase 1) |
| `src/adm/synthetic/` | Seeded synthetic data generator and planted scenarios | In progress (Phase 1) |
| `src/adm/facts/`, `ai/`, `services/`, `reporting/` | Later phases | Empty packages |
| [`app/Home.py`](../app/Home.py), `app/pages/*` | Streamlit UI | Home built; module pages are placeholders |

## 3. Anti-hallucination contract

```mermaid
flowchart LR
    A[Fact pack<br/>facts with source_id] --> B[LLM]
    B --> C{Valid JSON<br/>against schema?}
    C -- no --> X[Reject call]
    C -- yes --> D{Every citation<br/>in fact pack?}
    D -- no --> Y[Drop / mark unverified]
    D -- yes --> E{Numbers match<br/>cited facts?}
    E -- no --> Y
    E -- yes --> F{AI assigned<br/>a RAG status?}
    F -- yes --> Y
    F -- no --> G[Show with<br/>citation chips]
```

| # | Control | What it prevents |
|---|---|---|
| 1 | **Restricted input**: the LLM sees only the fact pack | Using outside or invented knowledge |
| 2 | **Structured output**: `{findings:[{text, citations, confidence}], recommendations:[…]}` | Uncheckable free text |
| 3 | **Citation check**: every ID must exist in the fact pack | Invented records |
| 4 | **Number check**: numbers in the text must match the cited facts | Wrong dates, counts or percentages |
| 5 | **No AI status decisions**: wording that sets a status is rejected | AI overriding the rules engine |
| 6 | **Visible verification**: unverified findings are hidden or labelled "⚠ unverified" | Silent trust |
| 7 | **Human approval** for reports and decisions | Unchecked output reaching leadership |
| 8 | **Audit trail**: provider, model, prompt version, fact-pack hash, raw response | Unexplainable outputs |
| 9 | **Adversarial tests**: fake IDs, wrong numbers, invented RAG, malformed JSON | Validator regressions |

**Example fact pack** (from the discovery Q&A):

```json
{
  "use_case": "raid_analysis",
  "as_of": "2026-11-19",
  "scope": "squad:IDS",
  "facts": [
    {"source_id": "RAID-R-031", "type": "risk", "score": 20, "owner": null, "mitigation": null},
    {"source_id": "RULE:RAID_NO_MITIGATION:RAID-R-031", "type": "rule_result", "result": "FLAGGED"}
  ]
}
```

## 4. Traceability and lineage design

```mermaid
flowchart LR
    F[/Source file<br/>name + SHA hash/] --> IB[import_batch]
    IB --> REC["Every imported row<br/>import_batch_id + source_row"]
    REC --> HS["health_snapshot<br/>value · rag · rule_version · source_ids"]
    REC --> FP[Fact pack<br/>source_id per fact]
    HS --> FP
    FP --> AO["ai_output<br/>fact_pack_hash · model · prompt_version"]
    AO --> AC["ai_citation<br/>source_id · valid"]
    AC -. drill-down .-> REC
```

| Question an auditor asks | Answered by |
|---|---|
| Which file did this record come from, and which row? | `import_batch_id` and `source_row` on every imported table |
| Was this file imported before? | `import_batch.file_hash` (re-import is idempotent) |
| Why is this squad Amber? | `health_snapshot.source_ids` and `rule_version` |
| What exactly did the AI see? | `ai_output.fact_pack_json` and `fact_pack_hash` |
| Which claims were verified? | `ai_citation.valid` |
| Who approved the report? | `ai_output.approved_by` / `approved_at` |

## 5. Database schema

The diagram below reflects [`src/adm/db/schema.sql`](../src/adm/db/schema.sql) as it exists now. Lineage columns (`import_batch_id`, `source_row`, `created_at`, `updated_at`) are on every imported table and are left out of the diagram for readability.

```mermaid
erDiagram
    programme ||--o{ squad : has
    programme ||--o{ program_increment : has
    programme ||--o{ milestone : has
    program_increment ||--o{ sprint : contains
    program_increment ||--o{ feature : plans
    program_increment ||--o{ pi_objective : sets
    sprint ||--o{ sprint_squad_commitment : "per squad"
    squad ||--o{ sprint_squad_commitment : commits
    squad ||--o{ feature : owns
    squad ||--o{ work_item : delivers
    squad ||--o{ pi_objective : owns
    squad |o--o{ milestone : "may own"
    squad |o--o{ raid_item : "may own (null = programme)"
    feature |o--o{ work_item : groups
    sprint |o--o{ work_item : schedules
    sprint |o--o{ feature : "target sprint"
    squad ||--o{ dependency : requests
    squad |o--o{ dependency : provides
    milestone |o--o{ dependency : "blocked by"
    import_batch ||--o{ raid_item : "lineage (all tables)"
    ai_output ||--o{ ai_citation : cites
    ai_output |o--o{ decision : informs

    programme {
        text programme_id PK
        text name
        text start_date
        text end_date
    }
    squad {
        text squad_id PK
        text programme_id FK
        text name
        int team_size
        int capacity_points_per_sprint
    }
    program_increment {
        text pi_id PK
        text programme_id FK
        text start_date
        text end_date
    }
    sprint {
        text sprint_id PK
        text pi_id FK
        int number
        text start_date
        text end_date
    }
    sprint_squad_commitment {
        text commitment_id PK
        text sprint_id FK
        text squad_id FK
        int committed_points
        int completed_points
        int added_points
        int removed_points
        int carried_over_points
    }
    feature {
        text feature_id PK
        text squad_id FK
        text pi_id FK
        int business_value_planned
        int business_value_actual
        text status
    }
    work_item {
        text work_item_id PK
        text feature_id FK
        text squad_id FK
        text sprint_id FK
        text type
        int story_points
        text status
        int blocked_days
    }
    pi_objective {
        text objective_id PK
        text pi_id FK
        text squad_id FK
        int committed
        int planned_bv
        int actual_bv
    }
    milestone {
        text milestone_id PK
        text squad_id FK
        text baseline_date
        text forecast_date
        text actual_date
        text status
    }
    dependency {
        text dependency_id PK
        text requesting_squad_id FK
        text providing_squad_id FK
        text external_party
        text needed_by_date
        text status
        text blocks_milestone_id FK
    }
    raid_item {
        text raid_id PK
        text type
        text squad_id FK
        text owner
        int probability
        int impact
        text status
        text due_date
        text last_updated
        text linked_ids
    }
    import_batch {
        int import_batch_id PK
        text file_name
        text file_hash
        text status
    }
    health_snapshot {
        int snapshot_id PK
        text scope_id
        text metric
        real value
        text rag
        text rule_version
        text source_ids
    }
    ai_output {
        int ai_output_id PK
        text use_case
        text model
        text prompt_version
        text fact_pack_hash
        text validation_status
    }
    ai_citation {
        int ai_output_id FK
        text source_id
        int valid
    }
    decision {
        text decision_id PK
        text source
        int ai_output_id FK
    }
```

**Changes from the planned schema made during Phase 1** (documented for transparency):

| Area | Plan | As built | Reason |
|---|---|---|---|
| Dependency direction | `from_squad_id` / `to_squad_id` | `requesting_squad_id` / `providing_squad_id`, plus `external_party`, `delivered_date` and `blocks_milestone_id` | Clearer meaning; supports external suppliers and milestone impact |
| RAID types | R/A/I/D | `RISK`, `ASSUMPTION`, `ISSUE`, `ACTION`, `DECISION` | Supports both actions ("overdue actions") and assumptions |
| Sprint commitment | Composite key | `commitment_id` surrogate key, plus `completed_points` and `carried_over_points`, unique on (sprint, squad) | Gives each sprint result a citable ID |
| Milestone | `status` | `status` limited to `Planned` / `Complete`, plus `squad_id` | Lateness is *calculated* by rules, not reported |
| Import batch | One entity per batch | `entities`, `warning_count`, and `status` (`imported` / `rejected` / `partial`) | One file can hold several entities (Excel sheets) |

Source-side status fields are kept factual (for example Planned or Complete). Derived judgements such as "late" or "at risk" are always calculated by the rules engine.

## 6. Synthetic data design

**Scenario:** "Project Meridian", a fictional retail-bank digital modernisation programme. It covers 2 PIs × 5 two-week sprints, with the as-of point at PI 2, Sprint 3 ([`config/settings.yaml`](../config/settings.yaml) sets `as_of_date: 2026-11-19`).

| Squad | Personality (planted pattern) | Expected health |
|---|---|---|
| Payments | High performer, stable velocity: the **control group** | Green |
| Customer Onboarding | Scope creep: high mid-sprint additions, falling say/do | Amber |
| Data Platform | Blocked by dependencies; late milestone | Red |
| Mobile App | Volatile velocity, high carry-over, defect spike | Amber |
| Identity & Security | Risk-heavy: high-score risks without owner or mitigation; overdue actions | Amber/Red |

| Design element | Approach |
|---|---|
| Volumes (target) | 10 sprints, about 40 features, about 650 work items, 50 PI objectives, about 90 RAID items, about 35 dependencies (8 late), 12 milestones |
| Determinism | Fixed random seed, so runs are reproducible and tests are stable |
| Realism | Templated banking vocabulary; no Faker dependency |
| Outputs | One CSV per entity plus one Excel workbook (one sheet per entity), the same files a user would import |
| Planted scenarios | Known facts, for example "RAID-R-031 is high-score with no mitigation" or "DEP-012 is 3 weeks late and blocks MS-07". They double as **golden test expectations** |
| Negative sample | A "dirty" file (bad dates, unknown squad, missing fields) to demonstrate validation |

> Status: implemented in [`src/adm/synthetic/generate.py`](../src/adm/synthetic/generate.py). It produces 634 work items and uses one independent seeded stream per entity. Future sprints (S09–S10) contain only planned `To Do` backlog with no commitment rows. Evidence is in [05_delivery_evidence_log.md](05_delivery_evidence_log.md#h--generator-gap-closure-srcadmsyntheticgeneratepy).

## 7. LLM provider abstraction

```mermaid
classDiagram
    class LLMProvider {
        <<interface>>
        +complete(system, user, schema) str
    }
    class MockProvider {
        +canned grounded JSON
        +no network or key
    }
    class OpenAICompatibleProvider {
        +base_url
        +Ollama / LM Studio / OpenAI / Azure
    }
    class AnthropicProvider {
        +api_key_env
        +Claude models
    }
    LLMProvider <|.. MockProvider
    LLMProvider <|.. OpenAICompatibleProvider
    LLMProvider <|.. AnthropicProvider
```

| Setting ([`config/llm.yaml.example`](../config/llm.yaml.example)) | Default |
|---|---|
| `provider` | `mock` (one of `mock`, `openai_compatible`, `anthropic`) |
| `model` | `mock-grounded-v1` |
| `temperature` | `0.0` |
| `base_url` / `api_key_env` | `null`. Keys are read from environment variables at call time and never stored in config |

The config loads with this precedence ([`src/adm/config.py`](../src/adm/config.py)): `config/llm.yaml` (git-ignored), then `config/llm.yaml.example`, then the built-in mock default. The provider SDKs are an optional `[llm]` extra in [`pyproject.toml`](../pyproject.toml).

## 8. Testing strategy

| Layer | Approach | State |
|---|---|---|
| Config | Loads; rejects inverted thresholds and weights that don't sum to 1.0; LLM precedence | ✅ [`tests/unit/test_config.py`](../tests/unit/test_config.py) |
| RAG rules | Boundary tests (for example 0.85 vs 0.849) in both directions | ✅ [`tests/unit/test_rag.py`](../tests/unit/test_rag.py) |
| UI smoke | `streamlit.testing.v1.AppTest` renders every page without exceptions | ✅ [`tests/ui/test_pages_smoke.py`](../tests/ui/test_pages_smoke.py) |
| Ingestion | Valid import, dirty-file errors per row, idempotent re-import, lineage | Phase 1 |
| Repositories / integration | In-memory SQLite; import → rules → snapshot | Phases 1–2 |
| Scenario / golden | Every planted fact detected; Payments stays Green | Phase 2 |
| AI grounding | Adversarial canned responses must be rejected or flagged | Phase 3 |
| Prompt regression | Snapshot of the rendered prompt and fact pack | Phase 3 |
| Optional live eval | Citation-validity rate against the real LLM (not in CI) | Phase 3+ |
| CI | GitHub Actions running ruff and pytest; no keys needed | Phase 7 |

## 9. Architecture Decision Records

### ADR-001 — SQLite as the database
- **Context:** A single-user, local portfolio app with about 1,000 rows of synthetic data. It must be free and run with no setup.
- **Decision:** Use SQLite (one file, `data/adm.db`, git-ignored).
- **Consequences:** ✅ No server, no setup, deploys on the free Streamlit Cloud. ⚠️ No concurrent multi-user writes. Plain SQL in repository functions keeps the path to PostgreSQL open.

### ADR-002 — stdlib sqlite3 without an ORM
- **Context:** The schema is small and stable, and the goal is transparency and few dependencies.
- **Decision:** Use the stdlib `sqlite3` module, a `schema.sql` file, and repository functions returning dicts or DataFrames.
- **Consequences:** ✅ The SQL is visible and the code is simple to test. ⚠️ Mapping is manual and there is no migration tool (the schema is recreated with `reset_db` while in development).

### ADR-003 — Streamlit for the UI
- **Context:** A data-heavy dashboard built by one Python developer, needing fast iteration and free hosting.
- **Decision:** A Streamlit multipage app (`app/Home.py` plus `app/pages/`).
- **Consequences:** ✅ Pure Python, built-in charts, tables and file upload, a testing API (`AppTest`) and free Community Cloud hosting. ⚠️ Limited control over layout. Business logic is kept out of pages so the UI could be replaced.

### ADR-004 — Mock LLM provider as the default
- **Context:** The brief required the LLM to be configured separately and no paid tools. Tests must be deterministic.
- **Decision:** A provider interface with `mock` as the default. OpenAI-compatible (including free local Ollama) and Anthropic are opt-in.
- **Consequences:** ✅ Zero cost, works offline, deterministic CI, and reviewers need no keys. ⚠️ Mock output only shows the pattern, so a live evaluation script is needed to measure real model behaviour.

### ADR-005 — Rules engine separated from the AI layer
- **Context:** Delivery health must be repeatable and auditable, and the brief says AI must never invent facts.
- **Decision:** Pure-function rules compute every metric and RAG status. AI only interprets rule outputs through fact packs and cannot set a status.
- **Consequences:** ✅ The app is testable and explainable, and works with AI turned off. ⚠️ Two layers to maintain, and AI insight is limited to what the rules expose.

### ADR-006 — Structured JSON output plus a grounding validator
- **Context:** Free-text LLM output can't be checked automatically.
- **Decision:** Require schema-validated JSON in which each finding has citations. The validator checks citations, numbers and that no RAG status was assigned. Failures are dropped or marked unverified.
- **Consequences:** ✅ Every displayed fact is traceable. ⚠️ Some valid insights may be filtered out, and the number check needs a tolerance policy (OQ2).

### ADR-007 — Thresholds in versioned YAML
- **Context:** RAG cut-offs are business policy and will be tuned over time.
- **Decision:** Keep thresholds and weights in [`config/thresholds.yaml`](../config/thresholds.yaml) with a `version`, validated by pydantic (direction order; weights sum to 1.0). The version is stored with each `health_snapshot`.
- **Consequences:** ✅ Policy changes are reviewable in git and need no code change. Historic snapshots stay explainable. ⚠️ Config errors must fail fast, which the validation covers.

### ADR-008 — All-or-nothing import by default
- **Context:** A partial import can leave orphaned or inconsistent delivery data, which would then produce misleading health results.
- **Decision:** By default, validate everything first. If any error is found, import nothing and return a row-level error report. Partial import is an explicit opt-in. Files are tracked by hash so re-imports are idempotent.
- **Consequences:** ✅ The database is always internally consistent, and errors are actionable (file, row, field, message). ⚠️ One bad row blocks the whole file unless partial mode is chosen.

> ADR-008 records the approach chosen for Phase 1 ingestion. Its implementation is in progress.
