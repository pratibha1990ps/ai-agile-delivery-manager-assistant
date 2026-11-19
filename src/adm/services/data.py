"""Load the imported programme data into typed DataFrames for the rules engine."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

import pandas as pd

from adm.db import repositories as repo

DATE_COLUMNS = {
    "programme": ["start_date", "end_date"],
    "program_increment": ["start_date", "end_date"],
    "sprint": ["start_date", "end_date"],
    "work_item": ["created_date", "done_date"],
    "milestone": ["baseline_date", "forecast_date", "actual_date"],
    "dependency": ["needed_by_date", "committed_date", "delivered_date"],
    "raid_item": ["raised_date", "due_date", "last_updated"],
}


def _to_date(v: object) -> date | None:
    if isinstance(v, str) and v:
        return date.fromisoformat(v[:10])
    return None


@dataclass
class ProgrammeData:
    programme: pd.DataFrame
    squads: pd.DataFrame
    pis: pd.DataFrame
    sprints: pd.DataFrame
    commitments: pd.DataFrame
    features: pd.DataFrame
    work_items: pd.DataFrame
    objectives: pd.DataFrame
    milestones: pd.DataFrame
    dependencies: pd.DataFrame
    raid: pd.DataFrame

    @property
    def is_empty(self) -> bool:
        return self.squads.empty or self.sprints.empty


def _load(conn: sqlite3.Connection, entity: str) -> pd.DataFrame:
    df = repo.fetch_df(conn, entity)
    for col in DATE_COLUMNS.get(entity, []):
        df[col] = df[col].map(_to_date).astype(object)
    return df


def load_programme_data(conn: sqlite3.Connection) -> ProgrammeData:
    return ProgrammeData(
        programme=_load(conn, "programme"),
        squads=_load(conn, "squad"),
        pis=_load(conn, "program_increment"),
        sprints=_load(conn, "sprint"),
        commitments=_load(conn, "sprint_squad_commitment"),
        features=_load(conn, "feature"),
        work_items=_load(conn, "work_item"),
        objectives=_load(conn, "pi_objective"),
        milestones=_load(conn, "milestone"),
        dependencies=_load(conn, "dependency"),
        raid=_load(conn, "raid_item"),
    )
