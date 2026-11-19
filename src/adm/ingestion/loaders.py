"""Read CSV / Excel files into per-entity DataFrames of strings.

- CSV: the file stem names the entity (``raid_item.csv``).
- Excel: each sheet name names an entity; unknown sheets are reported and ignored.
All cells are normalised to strings so pydantic does the typing in one place.
"""

from __future__ import annotations

import hashlib
import io
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from adm.ingestion.schemas import ENTITY_BY_NAME

HEADER_ROWS = 1  # source_row = DataFrame index + 1 header + 1 (1-based)


@dataclass
class LoadedFile:
    file_name: str
    file_hash: str
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)   # file-level problems
    ignored: list[str] = field(default_factory=list)    # unknown sheets / files

    @property
    def row_count(self) -> int:
        return sum(len(df) for df in self.frames.values())


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cell_to_str(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        return str(int(v)) if v.is_integer() else str(v)
    if isinstance(v, datetime | pd.Timestamp):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v).strip()


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    df = df.dropna(how="all")
    for col in df.columns:
        df[col] = df[col].map(_cell_to_str)
    return df.reset_index(drop=True)


def load_file(file_name: str, data: bytes) -> LoadedFile:
    loaded = LoadedFile(file_name=file_name, file_hash=file_hash(data))
    suffix = Path(file_name).suffix.lower()
    try:
        if suffix == ".csv":
            entity = Path(file_name).stem.lower()
            if entity not in ENTITY_BY_NAME:
                loaded.problems.append(
                    f"Unknown entity '{entity}'. CSV files must be named after an entity, "
                    f"e.g. raid_item.csv. Known: {', '.join(ENTITY_BY_NAME)}"
                )
                return loaded
            df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
            loaded.frames[entity] = _normalise(df)
        elif suffix in (".xlsx", ".xlsm"):
            sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, dtype=object)
            for sheet, df in sheets.items():
                entity = str(sheet).strip().lower()
                if entity in ENTITY_BY_NAME:
                    loaded.frames[entity] = _normalise(df)
                else:
                    loaded.ignored.append(str(sheet))
            if not loaded.frames:
                loaded.problems.append("Workbook contains no sheets named after a known entity.")
        else:
            loaded.problems.append(f"Unsupported file type '{suffix}'. Use .csv or .xlsx.")
    except Exception as exc:  # malformed file: report, never crash the import
        loaded.problems.append(f"Could not read file: {exc}")
    return loaded


def load_path(path: str | Path) -> LoadedFile:
    p = Path(path)
    return load_file(p.name, p.read_bytes())
