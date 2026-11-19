-- AI Agile Delivery Manager Assistant: SQLite schema (v1)
-- Imported tables carry lineage columns: import_batch_id, source_row, created_at, updated_at.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS import_batch (
    import_batch_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name         TEXT NOT NULL,
    file_hash         TEXT NOT NULL,
    entities          TEXT NOT NULL,          -- comma-separated entity names in the file
    row_count         INTEGER NOT NULL,
    error_count       INTEGER NOT NULL,
    warning_count     INTEGER NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('imported', 'rejected', 'partial')),
    imported_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS programme (
    programme_id      TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    description       TEXT,
    start_date        TEXT NOT NULL,
    end_date          TEXT NOT NULL,
    import_batch_id   INTEGER REFERENCES import_batch(import_batch_id),
    source_row        INTEGER,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS squad (
    squad_id                    TEXT PRIMARY KEY,
    programme_id                TEXT NOT NULL REFERENCES programme(programme_id),
    name                        TEXT NOT NULL,
    domain                      TEXT,
    team_size                   INTEGER NOT NULL,
    capacity_points_per_sprint  INTEGER NOT NULL,
    import_batch_id             INTEGER REFERENCES import_batch(import_batch_id),
    source_row                  INTEGER,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS program_increment (
    pi_id             TEXT PRIMARY KEY,
    programme_id      TEXT NOT NULL REFERENCES programme(programme_id),
    name              TEXT NOT NULL,
    start_date        TEXT NOT NULL,
    end_date          TEXT NOT NULL,
    import_batch_id   INTEGER REFERENCES import_batch(import_batch_id),
    source_row        INTEGER,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sprint (
    sprint_id         TEXT PRIMARY KEY,
    pi_id             TEXT NOT NULL REFERENCES program_increment(pi_id),
    name              TEXT NOT NULL,
    number            INTEGER NOT NULL,
    start_date        TEXT NOT NULL,
    end_date          TEXT NOT NULL,
    import_batch_id   INTEGER REFERENCES import_batch(import_batch_id),
    source_row        INTEGER,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sprint_squad_commitment (
    commitment_id            TEXT PRIMARY KEY,
    sprint_id                TEXT NOT NULL REFERENCES sprint(sprint_id),
    squad_id                 TEXT NOT NULL REFERENCES squad(squad_id),
    committed_points         INTEGER NOT NULL,
    completed_points         INTEGER NOT NULL,
    added_points             INTEGER NOT NULL,
    removed_points           INTEGER NOT NULL,
    carried_over_points      INTEGER NOT NULL,
    available_capacity_days  REAL NOT NULL,
    import_batch_id          INTEGER REFERENCES import_batch(import_batch_id),
    source_row               INTEGER,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL,
    UNIQUE (sprint_id, squad_id)
);

CREATE TABLE IF NOT EXISTS feature (
    feature_id              TEXT PRIMARY KEY,
    squad_id                TEXT NOT NULL REFERENCES squad(squad_id),
    pi_id                   TEXT NOT NULL REFERENCES program_increment(pi_id),
    title                   TEXT NOT NULL,
    business_value_planned  INTEGER NOT NULL,
    business_value_actual   INTEGER,
    status                  TEXT NOT NULL,
    target_sprint_id        TEXT REFERENCES sprint(sprint_id),
    import_batch_id         INTEGER REFERENCES import_batch(import_batch_id),
    source_row              INTEGER,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_item (
    work_item_id        TEXT PRIMARY KEY,
    feature_id          TEXT REFERENCES feature(feature_id),
    squad_id            TEXT NOT NULL REFERENCES squad(squad_id),
    sprint_id           TEXT REFERENCES sprint(sprint_id),
    type                TEXT NOT NULL,
    title               TEXT NOT NULL,
    story_points        INTEGER NOT NULL,
    status              TEXT NOT NULL,
    created_date        TEXT NOT NULL,
    done_date           TEXT,
    blocked_days        INTEGER NOT NULL DEFAULT 0,
    carried_over_count  INTEGER NOT NULL DEFAULT 0,
    import_batch_id     INTEGER REFERENCES import_batch(import_batch_id),
    source_row          INTEGER,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pi_objective (
    objective_id      TEXT PRIMARY KEY,
    pi_id             TEXT NOT NULL REFERENCES program_increment(pi_id),
    squad_id          TEXT NOT NULL REFERENCES squad(squad_id),
    description       TEXT NOT NULL,
    committed         INTEGER NOT NULL,       -- 1 committed, 0 stretch
    planned_bv        INTEGER NOT NULL,
    actual_bv         INTEGER,
    import_batch_id   INTEGER REFERENCES import_batch(import_batch_id),
    source_row        INTEGER,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS milestone (
    milestone_id      TEXT PRIMARY KEY,
    programme_id      TEXT NOT NULL REFERENCES programme(programme_id),
    squad_id          TEXT REFERENCES squad(squad_id),
    name              TEXT NOT NULL,
    baseline_date     TEXT NOT NULL,
    forecast_date     TEXT NOT NULL,
    actual_date       TEXT,
    status            TEXT NOT NULL,
    owner             TEXT,
    import_batch_id   INTEGER REFERENCES import_batch(import_batch_id),
    source_row        INTEGER,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dependency (
    dependency_id        TEXT PRIMARY KEY,
    requesting_squad_id  TEXT NOT NULL REFERENCES squad(squad_id),
    providing_squad_id   TEXT REFERENCES squad(squad_id),
    external_party       TEXT,
    description          TEXT NOT NULL,
    needed_by_date       TEXT NOT NULL,
    committed_date       TEXT,
    delivered_date       TEXT,
    status               TEXT NOT NULL,
    blocks_milestone_id  TEXT REFERENCES milestone(milestone_id),
    import_batch_id      INTEGER REFERENCES import_batch(import_batch_id),
    source_row           INTEGER,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raid_item (
    raid_id           TEXT PRIMARY KEY,
    type              TEXT NOT NULL,
    squad_id          TEXT REFERENCES squad(squad_id),   -- NULL = programme level
    title             TEXT NOT NULL,
    description       TEXT,
    owner             TEXT,
    probability       INTEGER,
    impact            INTEGER,
    status            TEXT NOT NULL,
    raised_date       TEXT NOT NULL,
    due_date          TEXT,
    last_updated      TEXT NOT NULL,
    mitigation        TEXT,
    linked_ids        TEXT,                              -- semicolon-separated record IDs
    import_batch_id   INTEGER REFERENCES import_batch(import_batch_id),
    source_row        INTEGER,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

-- Populated from Phase 2 onwards ------------------------------------------------

CREATE TABLE IF NOT EXISTS health_snapshot (
    snapshot_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of_date      TEXT NOT NULL,
    scope           TEXT NOT NULL,
    scope_id        TEXT NOT NULL,
    metric          TEXT NOT NULL,
    value           REAL,
    rag             TEXT,
    rule_version    TEXT NOT NULL,
    source_ids      TEXT NOT NULL,          -- JSON array
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_output (
    ai_output_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    use_case           TEXT NOT NULL,
    provider           TEXT NOT NULL,
    model              TEXT NOT NULL,
    prompt_version     TEXT NOT NULL,
    fact_pack_hash     TEXT NOT NULL,
    fact_pack_json     TEXT NOT NULL,
    raw_response       TEXT,
    parsed_json        TEXT,
    validation_status  TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    approved_by        TEXT,
    approved_at        TEXT
);

CREATE TABLE IF NOT EXISTS ai_citation (
    ai_output_id   INTEGER NOT NULL REFERENCES ai_output(ai_output_id),
    finding_index  INTEGER NOT NULL,
    source_table   TEXT,
    source_id      TEXT NOT NULL,
    valid          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS decision (
    decision_id     TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    context         TEXT,
    options_json    TEXT,
    chosen_option   TEXT,
    rationale       TEXT,
    decided_by      TEXT,
    decided_date    TEXT,
    source          TEXT NOT NULL CHECK (source IN ('manual', 'assistant')),
    ai_output_id    INTEGER REFERENCES ai_output(ai_output_id),
    created_at      TEXT NOT NULL
);
