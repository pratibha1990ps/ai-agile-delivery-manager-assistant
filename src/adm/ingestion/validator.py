"""Validate loaded files: columns, row schemas, duplicates and referential integrity.

Validation never writes to the database. It returns the valid rows (with their lineage)
and a list of issues, each pinned to a file, entity, row and field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import ValidationError

from adm.ingestion.loaders import HEADER_ROWS, LoadedFile
from adm.ingestion.schemas import ENTITIES, RaidItemRow, Row

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Issue:
    file_name: str
    entity: str
    row: int | None        # 1-based source row (header = row 1); None = file/column level
    field: str | None
    message: str
    severity: Severity = "error"


@dataclass
class ValidRow:
    file_name: str
    source_row: int
    record: Row


@dataclass
class ValidationResult:
    rows: dict[str, list[ValidRow]] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    def valid_count(self) -> int:
        return sum(len(v) for v in self.rows.values())


def _pydantic_issues(exc: ValidationError, file_name: str, entity: str, row: int) -> list[Issue]:
    out = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or None
        msg = err["msg"].removeprefix("Value error, ")
        if err.get("input", "") is None or err["type"] == "missing":
            msg = "Required value is missing"
        out.append(Issue(file_name, entity, row, loc, msg))
    return out


def validate(files: list[LoadedFile], existing_ids: dict[str, set[str]]) -> ValidationResult:
    """Validate rows in dependency order so children are checked against accepted parents."""
    result = ValidationResult()

    for f in files:
        for problem in f.problems:
            result.issues.append(Issue(f.file_name, "-", None, None, problem))
        for sheet in f.ignored:
            result.issues.append(
                Issue(f.file_name, sheet, None, None,
                      f"Sheet '{sheet}' is not a known entity and was ignored.", "warning")
            )

    known: dict[str, set[str]] = {k: set(v) for k, v in existing_ids.items()}

    for spec in ENTITIES:
        accepted: list[ValidRow] = []
        seen: dict[str, tuple[str, int]] = {}
        known.setdefault(spec.name, set())

        for f in files:
            df = f.frames.get(spec.name)
            if df is None:
                continue
            missing = [c for c in spec.required_columns if c not in df.columns]
            if missing:
                result.issues.append(
                    Issue(f.file_name, spec.name, None, None,
                          f"Missing required column(s): {', '.join(missing)}")
                )
                continue
            extra = [c for c in df.columns if c not in spec.columns]
            if extra:
                result.issues.append(
                    Issue(f.file_name, spec.name, None, None,
                          f"Unrecognised column(s) ignored: {', '.join(extra)}", "warning")
                )

            for idx, raw in enumerate(df.to_dict(orient="records")):
                source_row = idx + HEADER_ROWS + 1
                try:
                    record = spec.model.model_validate(raw)
                except ValidationError as exc:
                    result.issues.extend(_pydantic_issues(exc, f.file_name, spec.name, source_row))
                    continue

                rid = getattr(record, spec.id_field)
                if rid in seen:
                    first_file, first_row = seen[rid]
                    result.issues.append(
                        Issue(f.file_name, spec.name, source_row, spec.id_field,
                              f"Duplicate ID '{rid}' (first seen in {first_file} row {first_row})")
                    )
                    continue

                fk_errors = []
                for fk_field, parent in spec.foreign_keys.items():
                    value = getattr(record, fk_field)
                    if value is not None and value not in known.get(parent, set()):
                        fk_errors.append(
                            Issue(f.file_name, spec.name, source_row, fk_field,
                                  f"Unknown {parent} '{value}'")
                        )
                if fk_errors:
                    result.issues.extend(fk_errors)
                    continue

                seen[rid] = (f.file_name, source_row)
                accepted.append(ValidRow(f.file_name, source_row, record))

        result.rows[spec.name] = accepted
        known[spec.name] |= {getattr(v.record, spec.id_field) for v in accepted}

    # Soft check: RAID linked_ids should point at records that exist somewhere.
    all_known = set().union(*known.values())
    for v in result.rows.get("raid_item", []):
        assert isinstance(v.record, RaidItemRow)
        for linked in v.record.linked_id_list():
            if linked not in all_known:
                result.issues.append(
                    Issue(v.file_name, "raid_item", v.source_row, "linked_ids",
                          f"Linked ID '{linked}' not found in any table", "warning")
                )

    return result
