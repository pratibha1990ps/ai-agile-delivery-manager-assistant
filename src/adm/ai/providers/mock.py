"""Mock AI provider: free, offline and deterministic.

It is a *stand-in* for a real model, not artificial intelligence. It has three modes:

* **grounded** (default) - builds a well-formed, fully cited answer from the fact pack, using
  simple templates. It proves the whole pipeline works without any API key or cost.
* **scripted** - returns pre-written responses in order. Tests use it to feed the validator
  exact inputs.
* **fault** - produces a grounded answer and then corrupts it in one specific way (invented ID,
  wrong number, RAG override, uncited claim, broken JSON). The UI uses this to demonstrate that
  the validator catches bad AI output.

Fact data conventions it relies on (produced by the fact pack builder):
``rule_result`` facts carry ``data = {"rule", "label", "severity", "message"}`` and list the
records they concern in ``source_ids``; ``aggregate`` facts carry ``{"label", "count"}``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable

from adm.ai.models import (
    AnalysisResponse,
    Fact,
    FactKind,
    FactPack,
    LLMRequest,
    LLMResponse,
)
from adm.ai.providers.base import ProviderError

MOCK_MODEL = "mock-grounded-v1"
MAX_FINDINGS = 8
MAX_RECOMMENDATIONS = 6
URGENCY = {"high": "immediate", "medium": "this_sprint", "low": "monitor"}

# Rule -> (suggested action template, owner role template). {ids} = cited record IDs.
ACTIONS: dict[str, tuple[str, str]] = {
    "escalation_candidate": ("Raise {ids} at the ART sync and agree an accountable decision "
                             "owner", "RTE"),
    "high_risk_no_mitigation": ("Agree and record a mitigation plan for {ids}",
                                "Risk owner / squad lead ({squad})"),
    "missing_owner": ("Assign an accountable owner to {ids}", "Squad lead ({squad})"),
    "overdue_action": ("Agree a new due date for {ids} or close it if no longer needed",
                       "Action owner ({squad})"),
    "risk_past_trigger": ("Decide whether {ids} has materialised and should become an issue",
                          "Squad lead ({squad})"),
    "ageing_issue": ("Agree a resolution plan and date for {ids}", "Squad lead ({squad})"),
    "stale_item": ("Review and update {ids} at the next RAID review", "Squad lead ({squad})"),
    "duplicate_candidate": ("Merge the duplicate RAID entries {ids}", "Scrum master ({squad})"),
    "dependency_late": ("Confirm a recovery date for {ids} with the providing team", "RTE"),
    "objective_exposure": ("Review the PI objectives exposed by {ids} and agree a fallback",
                           "Product owner ({squad})"),
}
DEFAULT_ACTION = ("Review {ids} with the owning squad", "Squad lead ({squad})")

FAULTS = ("invented_id", "wrong_number", "rag_override", "uncited_claim", "malformed_json")


class MockProvider:
    name = "mock"

    def __init__(self, model: str = MOCK_MODEL, script: Iterable[str] | None = None,
                 fault: str | None = None) -> None:
        if fault is not None and fault not in FAULTS:
            raise ValueError(f"unknown fault '{fault}'; choose from {FAULTS}")
        self.model = model
        self._script = list(script) if script is not None else None
        self.fault = fault
        self.calls: list[LLMRequest] = []      # lets tests inspect what was sent

    # --- public API -------------------------------------------------------------------

    def complete(self, request: LLMRequest) -> LLMResponse:
        start = time.perf_counter()
        self.calls.append(request)
        if self._script is not None:
            if not self._script:
                raise ProviderError("Mock script exhausted: no more scripted responses.")
            text = self._script.pop(0)
        else:
            if request.fact_pack is None:
                raise ProviderError("The mock provider needs a fact pack in the request.")
            response = grounded_response(request.fact_pack)
            text = (apply_fault(response, request.fact_pack, self.fault) if self.fault
                    else response.model_dump_json(indent=2))
        return LLMResponse(text=text, provider=self.name, model=self.model,
                           latency_ms=int((time.perf_counter() - start) * 1000))


# --- grounded generation ------------------------------------------------------------------

def _cited(fact: Fact, pack: FactPack) -> list[str]:
    """The rule fact itself plus any of its source records that are also in the pack."""
    ids = pack.fact_ids
    return [fact.fact_id] + [s for s in fact.source_ids if s in ids and s != fact.fact_id]


def _record_ids(fact: Fact) -> str:
    # An escalation's first source is the RAID item; the rest are the reasons it qualifies.
    if fact.data.get("rule") == "escalation_candidate":
        return fact.source_ids[0]
    return ", ".join(fact.source_ids)


def grounded_response(pack: FactPack) -> AnalysisResponse:
    # Keep the fact pack's order: the builder already ranked facts for the user's question
    # (e.g. escalation candidates first for the escalation focus).
    rules = [f for f in pack.facts if f.kind is FactKind.RULE_RESULT][:MAX_FINDINGS]

    findings = []
    for f in rules:
        label = f.data.get("label") or f.data.get("rule", "Rule finding")
        message = f.data.get("message", "")
        findings.append({
            "statement": f"{label}: {message}".strip(),
            "fact_ids": _cited(f, pack),
            "confidence": "high" if f.data.get("severity") == "high" else "medium",
        })

    aggregates = [f for f in pack.facts if f.kind is FactKind.AGGREGATE
                  and isinstance(f.data.get("count"), int) and f.data["count"] > 0]
    if aggregates and len(findings) < MAX_FINDINGS:
        top = max(aggregates, key=lambda f: (f.data["count"], f.fact_id))
        findings.append({
            "statement": f"Largest concentration: {top.data.get('label', top.fact_id)} "
                         f"({top.data['count']}).",
            "fact_ids": [top.fact_id],
            "confidence": "medium",
        })

    recommendations = []
    for idx, f in enumerate(rules):
        if len(recommendations) >= MAX_RECOMMENDATIONS:
            break
        severity = str(f.data.get("severity", "low"))
        if severity == "low" and len(recommendations) >= 3:
            continue
        action, owner = ACTIONS.get(str(f.data.get("rule")), DEFAULT_ACTION)
        squad = f.squad_id or "programme"
        recommendations.append({
            "action": action.format(ids=_record_ids(f), squad=squad),
            "rationale": f"Flagged by the deterministic rule '{f.data.get('rule')}' "
                         f"({severity} severity).",
            "owner_role": owner.format(squad=squad),
            "urgency": URGENCY.get(severity, "monitor"),
            "fact_ids": _cited(f, pack),
            "finding_refs": [idx],
        })

    gaps = ["Generated by the mock provider: deterministic templates, not a language model."]
    if pack.omitted_count:
        gaps.append("Some lower-priority facts were left out by the fact size limit.")
    if not rules:
        gaps.append("No rule findings were supplied for this scope and focus.")

    scope = "the programme" if pack.request.is_programme else f"squad {pack.request.scope}"
    summary = (f"Mock analysis of {scope} for the question: {pack.request.question} "
               "Items follow the fact pack's priority order for this question.")
    return AnalysisResponse.model_validate({
        "summary": summary, "findings": findings,
        "recommendations": recommendations, "data_gaps": gaps,
    })


# --- deliberate faults (validator demos and tests) ------------------------------------------

def apply_fault(response: AnalysisResponse, pack: FactPack, fault: str | None) -> str:
    data = response.model_dump(mode="json")
    if fault == "malformed_json":
        return json.dumps(data)[:-25] + " ...truncated"
    if not data["findings"]:
        data["findings"].append({"statement": "Placeholder finding for fault injection.",
                                 "fact_ids": sorted(pack.fact_ids)[:1], "confidence": "low"})
    first = data["findings"][0]
    if fault == "invented_id":
        first["statement"] += " See also RAID-R-999."
        first["fact_ids"].append("RAID-R-999")
    elif fault == "wrong_number":
        first["statement"] += " This affects 47 work items."
    elif fault == "rag_override":
        first["statement"] += " The squad should be downgraded to RED."
    elif fault == "uncited_claim":
        first["fact_ids"] = []
    return json.dumps(data, indent=2)
