"""Grounding validator: decides which parts of an AI answer may be shown, and how.

Nothing the model writes is trusted until it passes these checks against the fact pack.

| Check                                   | Outcome on failure        |
|-----------------------------------------|---------------------------|
| JSON parses and matches AnalysisResponse| whole answer ``invalid``   |
| item cites at least one fact            | item ``rejected``          |
| every cited fact_id exists in the pack  | item ``rejected``          |
| record IDs in the text exist in the pack| item ``rejected``          |
| ... and belong to the facts it cites    | item ``flagged``           |
| no RAG status assigned or changed       | item ``rejected``          |
| numbers / dates match the cited facts   | item ``flagged``           |
| recommendation backed by a finding      | item ``flagged``           |

*Rejected* items are hidden by default (kept for transparency); *flagged* items are shown with
a warning; *verified* items passed every check. Note what this cannot do: it proves an item is
**traceable** to evidence, not that the advice is **wise**. That judgement stays with a human.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from adm.ai.models import AIFinding, AIRecommendation, AnalysisResponse, FactKind, FactPack

# Record-ID shapes used by Project Meridian (squad codes like "IDS" are deliberately excluded).
ID_PATTERN = re.compile(
    r"\b(?:RAID-(?:AS|R|A|I|D)-\d+|DEP-\d+|MS-\d+|F-[A-Z]{3}-\d+|OBJ-PI\d+-[A-Z]{3}-\d+"
    r"|SC-S\d+-[A-Z]{3}|[A-Z]{3}-\d{4}|PI-\d+|S\d{2})\b")
DATE_PATTERN = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
NUMBER_PATTERN = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(\s?%)?")
RAG_PATTERN = re.compile(r"\b(red|amber|green)\b", re.IGNORECASE)
RAG_CHANGE_PATTERN = re.compile(
    r"\b(downgrad\w*|upgrad\w*|should\s+be|must\s+be|needs?\s+to\s+be|move[ds]?\s+to|"
    r"chang\w*\s+to|set\s+to|mark\w*|treat\w*|classif\w*|re-?rat\w*|reclassif\w*|"
    r"rate\s+it|turn\w*)\b",
    re.IGNORECASE)
PERCENT_TOLERANCE = 1.0    # "71%" is accepted for a fact value between 0.70 and 0.72
RAG_WINDOW = 30            # characters before a status word searched for a change verb


class Verdict(StrEnum):
    VERIFIED = "verified"
    FLAGGED = "flagged"
    REJECTED = "rejected"


class Status(StrEnum):
    VALID = "valid"
    VALID_WITH_WARNINGS = "valid_with_warnings"
    PARTIALLY_REJECTED = "partially_rejected"
    INVALID = "invalid"
    PROVIDER_ERROR = "provider_error"     # set by the service when the provider fails


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str          # "reject" | "flag"
    message: str


@dataclass(frozen=True)
class Citation:
    fact_id: str
    valid: bool
    kind: str | None = None                 # fact kind, for the audit log
    source_ids: tuple[str, ...] = ()


@dataclass
class ValidatedItem:
    index: int
    kind: str                               # "finding" | "recommendation"
    item: AIFinding | AIRecommendation
    citations: list[Citation]
    issues: list[Issue] = field(default_factory=list)

    @property
    def verdict(self) -> Verdict:
        if any(i.severity == "reject" for i in self.issues):
            return Verdict.REJECTED
        return Verdict.FLAGGED if self.issues else Verdict.VERIFIED

    @property
    def shown(self) -> bool:
        return self.verdict is not Verdict.REJECTED


@dataclass
class ValidatedAnalysis:
    status: Status
    response: AnalysisResponse | None = None
    findings: list[ValidatedItem] = field(default_factory=list)
    recommendations: list[ValidatedItem] = field(default_factory=list)
    summary_issues: list[Issue] = field(default_factory=list)
    response_issues: list[Issue] = field(default_factory=list)   # e.g. markdown wrapper
    error: str | None = None

    @property
    def summary_shown(self) -> bool:
        return self.response is not None and not any(
            i.severity == "reject" for i in self.summary_issues)

    @property
    def items(self) -> list[ValidatedItem]:
        return self.findings + self.recommendations

    def counts(self) -> dict[str, int]:
        out = {v.value: 0 for v in Verdict}
        for item in self.items:
            out[item.verdict.value] += 1
        return out

    def report(self) -> dict[str, Any]:
        """JSON-safe summary for the audit log."""
        def item(v: ValidatedItem) -> dict[str, Any]:
            return {"kind": v.kind, "index": v.index, "verdict": v.verdict.value,
                    "issues": [i.__dict__ for i in v.issues]}
        return {"status": self.status.value, "error": self.error, "counts": self.counts(),
                "summary_issues": [i.__dict__ for i in self.summary_issues],
                "response_issues": [i.__dict__ for i in self.response_issues],
                "items": [item(v) for v in self.items]}


# --- helpers -------------------------------------------------------------------------------

def _strings_and_numbers(value: Any, strings: list[str], numbers: list[float]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        numbers.append(float(value))
    elif isinstance(value, str):
        strings.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            _strings_and_numbers(v, strings, numbers)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _strings_and_numbers(v, strings, numbers)


def numbers_in_text(text: str) -> list[tuple[float, bool]]:
    """Numbers written in text as (value, is_percent), ignoring record IDs and ISO dates."""
    cleaned = DATE_PATTERN.sub(" ", ID_PATTERN.sub(" ", text))
    return [(float(m.group(1)), bool(m.group(2))) for m in NUMBER_PATTERN.finditer(cleaned)]


@dataclass
class Evidence:
    """What a set of facts can support: numbers, dates and RAG values."""
    numbers: set[float] = field(default_factory=set)
    percent_strings: set[float] = field(default_factory=set)
    dates: set[str] = field(default_factory=set)
    rags: set[str] = field(default_factory=set)

    @classmethod
    def from_facts(cls, facts: Iterable) -> Evidence:
        ev = cls()
        for f in facts:
            strings: list[str] = []
            numbers: list[float] = []
            _strings_and_numbers(f.data, strings, numbers)
            ev.numbers.update(numbers)
            for s in strings:
                ev.dates.update(DATE_PATTERN.findall(s))
                for n, pct in numbers_in_text(s):
                    (ev.percent_strings if pct else ev.numbers).add(n)
                    if pct:
                        ev.numbers.add(n)
            rag = f.data.get("rag")
            if isinstance(rag, str):
                ev.rags.add(rag.upper())
        return ev

    def supports_number(self, value: float, is_percent: bool) -> bool:
        if any(abs(value - n) < 1e-9 for n in self.numbers):
            return True
        if is_percent:
            return any(abs(value - n * 100) <= PERCENT_TOLERANCE
                       for n in self.numbers if 0 <= n <= 1.5)
        return False


# --- checks --------------------------------------------------------------------------------

def check_citations(fact_ids: list[str], pack: FactPack) -> tuple[list[Citation], list[Issue]]:
    by_id = pack.by_id
    citations, issues = [], []
    if not fact_ids:
        issues.append(Issue("no_citation", "reject", "Cites no facts, so it cannot be verified."))
    for fid in dict.fromkeys(fact_ids):
        fact = by_id.get(fid)
        citations.append(Citation(fid, fact is not None, fact.kind.value if fact else None,
                                  fact.source_ids if fact else ()))
        if fact is None:
            issues.append(Issue("unknown_fact_id", "reject",
                                f"Cites '{fid}', which is not in the fact pack."))
    return citations, issues


def check_ids_in_text(text: str, cited: list[Citation], pack: FactPack) -> list[Issue]:
    allowed = {c.fact_id for c in cited if c.valid} | {s for c in cited for s in c.source_ids}
    known = pack.fact_ids | set(pack.source_ids)
    issues = []
    for rid in dict.fromkeys(ID_PATTERN.findall(text)):
        if rid not in known:
            issues.append(Issue("unknown_id_in_text", "reject",
                                f"Mentions {rid}, which is not in the fact pack."))
        elif rid not in allowed:
            issues.append(Issue("uncited_id_in_text", "flag",
                                f"Mentions {rid} without citing the fact that supports it."))
    return issues


def check_rag(text: str, evidence: Evidence) -> list[Issue]:
    matches = list(RAG_PATTERN.finditer(text))
    if not matches:
        return []
    mentions = {m.group(1).upper() for m in matches}
    # A change verb shortly before a status word ("should be downgraded to RED") is an
    # attempt to set a status. Looking only at a short window avoids false alarms such as
    # "MS-07 is RED, so the RTE should be informed".
    if any(RAG_CHANGE_PATTERN.search(text[max(0, m.start() - RAG_WINDOW):m.start()])
           for m in matches):
        return [Issue("rag_override", "reject",
                      "Attempts to assign or change a RAG status; statuses come only from the "
                      "rules engine.")]
    unsupported = sorted(mentions - evidence.rags)
    if unsupported:
        return [Issue("rag_unsupported", "reject",
                      f"States a {', '.join(unsupported)} status that no cited fact gives.")]
    return []


def check_numbers(text: str, evidence: Evidence) -> list[Issue]:
    issues = []
    for d in dict.fromkeys(DATE_PATTERN.findall(text)):
        if d not in evidence.dates:
            issues.append(Issue("unsupported_date", "flag",
                                f"Date {d} does not appear in the cited facts."))
    seen = set()
    for value, pct in numbers_in_text(text):
        if (value, pct) in seen:
            continue
        seen.add((value, pct))
        if not evidence.supports_number(value, pct):
            shown = f"{value:g}{'%' if pct else ''}"
            issues.append(Issue("unsupported_number", "flag",
                                f"The number {shown} does not match any cited fact."))
    return issues


# --- main entry point ----------------------------------------------------------------------

def _strip_markdown(text: str) -> tuple[str, bool]:
    m = re.fullmatch(r"\s*```(?:json)?\s*(.*?)\s*```\s*", text, flags=re.DOTALL)
    return (m.group(1), True) if m else (text.strip(), False)


def _validate_item(index: int, kind: str, item: AIFinding | AIRecommendation, text: str,
                   pack: FactPack) -> ValidatedItem:
    citations, issues = check_citations(item.fact_ids, pack)
    cited_facts = [pack.by_id[c.fact_id] for c in citations if c.valid]
    evidence = Evidence.from_facts(cited_facts)
    issues += check_ids_in_text(text, citations, pack)
    issues += check_rag(text, evidence)
    issues += check_numbers(text, evidence)
    return ValidatedItem(index, kind, item, citations, issues)


def validate(raw_text: str, pack: FactPack) -> ValidatedAnalysis:
    text, wrapped = _strip_markdown(raw_text)
    response_issues = ([Issue("markdown_wrapper", "flag",
                              "The answer was wrapped in a markdown code block.")]
                       if wrapped else [])
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return ValidatedAnalysis(Status.INVALID, error=f"The AI answer is not valid JSON "
                                 f"(line {exc.lineno}, column {exc.colno}).")
    try:
        response = AnalysisResponse.model_validate(payload)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'answer'}: {e['msg']}"
                             for e in exc.errors()[:4])
        return ValidatedAnalysis(Status.INVALID,
                                 error=f"The AI answer does not match the schema: {problems}")

    findings = [_validate_item(i, "finding", f, f.statement, pack)
                for i, f in enumerate(response.findings)]

    recommendations = []
    for i, r in enumerate(response.recommendations):
        v = _validate_item(i, "recommendation", r,
                           f"{r.action} {r.rationale} {r.owner_role}", pack)
        refs_ok = [findings[j] for j in r.finding_refs if 0 <= j < len(findings)]
        if len(refs_ok) != len(r.finding_refs):
            v.issues.append(Issue("bad_finding_ref", "flag",
                                  "Refers to a finding number that does not exist."))
        if not any(f.shown for f in refs_ok):
            v.issues.append(Issue("no_supporting_finding", "flag",
                                  "Not linked to any finding that passed validation."))
        cited_kinds = {c.kind for c in v.citations if c.valid}
        if cited_kinds and cited_kinds <= {FactKind.CONTEXT.value}:
            v.issues.append(Issue("context_only_evidence", "flag",
                                  "Supported only by context facts, not by delivery evidence."))
        recommendations.append(v)

    # The summary has no citations: check it against the whole pack.
    whole = Evidence.from_facts(pack.facts)
    all_ids = [Citation(fid, True, None, pack.by_id[fid].source_ids) for fid in pack.fact_ids]
    summary_issues = (check_ids_in_text(response.summary, all_ids, pack)
                      + check_rag(response.summary, whole)
                      + check_numbers(response.summary, whole))
    for gap in response.data_gaps:
        summary_issues += [Issue(i.code, "flag", f"Data gap note: {i.message}")
                           for i in check_ids_in_text(gap, all_ids, pack)]

    result = ValidatedAnalysis(Status.VALID, response, findings, recommendations,
                               summary_issues, response_issues)
    rejected = (any(not v.shown for v in result.items)
                or any(i.severity == "reject" for i in summary_issues))
    flagged = (any(v.verdict is Verdict.FLAGGED for v in result.items)
               or summary_issues or response_issues)
    if rejected:
        result.status = Status.PARTIALLY_REJECTED
    elif flagged:
        result.status = Status.VALID_WITH_WARNINGS
    return result
