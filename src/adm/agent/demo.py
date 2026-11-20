"""Demo agent: a free, offline stand-in for a real model that plays the agent's part.

Like the Mock provider for the RAID Analyst, this is **not artificial intelligence**. It follows
a fixed tool order and then writes the report from the facts it was shown, using templates. It
exists so the whole agent pipeline (tools, loop, checker, review, audit) runs with no API key and
no cost, and so tests and demos are repeatable.

It reads only the text it is sent: the tool results the loop shows in the prompt. It keeps no
hidden state, exactly as a real model would.

Fault modes let a demo show the guardrails working:

* ``invented_id``   - adds a finding about a dependency that does not exist (checker rejects it)
* ``rag_override``  - tries to downgrade a squad's status (checker rejects it)
* ``uncited_claim`` - adds a finding with no citation (checker rejects it)
* ``wrong_number``  - adds a finding with a number the facts do not support (checker flags it)
* ``never_stops``   - keeps asking for tools until the loop's limit stops it
"""

from __future__ import annotations

import json
import re
from typing import Any

from adm.ai.models import LLMRequest, LLMResponse
from adm.ai.providers.mock import ACTIONS

DEMO_MODEL = "mock-agent-v1"
FAULTS = ("invented_id", "rag_override", "uncited_claim", "wrong_number", "never_stops")

BASE_PLAN: list[tuple[str, dict[str, Any]]] = [
    ("get_programme_health", {}),
    ("get_late_dependencies", {}),
    ("get_escalation_candidates", {}),
]
NEVER_STOPS_PLAN = BASE_PLAN + [
    ("get_milestone_status", {}), ("get_open_raid_items", {}),
    *[("get_squad_metrics", {"squad": s}) for s in ("DAT", "IDS", "MOB", "ONB", "PAY")]]

_RESULT_HEADER = re.compile(r"--- result of (\w+) (\{.*?\}) ---\n")
MAX_OFF_TRACK, MAX_DEPENDENCIES, MAX_ESCALATIONS = 3, 3, 3
RAG_RANK = {"RED": 0, "AMBER": 1}


def _key(tool: str, args: dict[str, Any]) -> str:
    return f"{tool}:{json.dumps(args, sort_keys=True)}"


def tool_results_in(prompt: str) -> dict[str, dict[str, Any]]:
    """Read the tool results the loop printed into the prompt: key -> payload."""
    decoder = json.JSONDecoder()
    found = {}
    for m in _RESULT_HEADER.finditer(prompt):
        payload, _ = decoder.raw_decode(prompt, m.end())
        found[_key(m.group(1), json.loads(m.group(2)))] = payload
    return found


class DemoAgentProvider:
    name = "mock"
    model = DEMO_MODEL

    def __init__(self, fault: str | None = None) -> None:
        if fault is not None and fault not in FAULTS:
            raise ValueError(f"Unknown fault '{fault}'. Choose one of {FAULTS}.")
        self.fault = fault
        self.calls: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        seen = tool_results_in(request.user)
        plan = NEVER_STOPS_PLAN if self.fault == "never_stops" else BASE_PLAN
        for tool, args in plan:
            if _key(tool, args) not in seen:
                action: dict[str, Any] = {"action": "call_tool", "tool": tool, "args": args}
                break
        else:
            facts = [f for payload in seen.values() for f in payload["facts"]]
            omitted = sum(p["omitted_count"] for p in seen.values())
            action = {"action": "final", "answer": build_report(facts, omitted, self.fault)}
        return LLMResponse(text=json.dumps(action), provider=self.name, model=self.model,
                           latency_ms=0)


# --- the template report -------------------------------------------------------------------

def _finding(section: str, statement: str, fact_ids: list[str]) -> dict[str, Any]:
    return {"section": section, "statement": statement, "fact_ids": fact_ids,
            "confidence": "high"}


def _ask(template: tuple[str, str], ids: str, rationale: str, fact_ids: list[str],
         ref: int) -> dict[str, Any]:
    action, owner = template
    return {"action": action.format(ids=ids, squad=""), "rationale": rationale,
            "owner_role": owner.replace(" ()", ""), "urgency": "immediate",
            "fact_ids": fact_ids, "finding_refs": [ref]}


def build_report(facts: list[dict[str, Any]], omitted: int, fault: str | None) -> dict[str, Any]:
    health = sorted((f for f in facts if f["fact_id"].startswith("HEALTH:")
                     and f["data"].get("rag") in RAG_RANK),
                    key=lambda f: (RAG_RANK[f["data"]["rag"]], f["fact_id"]))
    deps = sorted((f for f in facts if f["fact_id"].startswith("DEPSTATE:")),
                  key=lambda f: (-f["data"].get("days_late", 0), f["fact_id"]))
    escalations = [f for f in facts if f["fact_id"].startswith("ESC:")]

    findings: list[dict[str, Any]] = []
    asks: list[dict[str, Any]] = []
    for f in health[:MAX_OFF_TRACK]:
        findings.append(_finding("off_track",
                                 f"{f['data']['squad']} is {f['data']['rag']} this week.",
                                 [f["fact_id"]]))
    first_dep_ref = None
    for f in deps[:MAX_DEPENDENCIES]:
        first_dep_ref = len(findings) if first_dep_ref is None else first_dep_ref
        findings.append(_finding("risks_dependencies",
                                 f"{f['source_ids'][0]}: {f['data']['message']}",
                                 [f["fact_id"]]))
    esc_refs = []
    for f in escalations[:MAX_ESCALATIONS]:
        esc_refs.append(len(findings))
        findings.append(_finding("risks_dependencies", f["data"]["message"], [f["fact_id"]]))

    for f, ref in zip(escalations[:MAX_ESCALATIONS], esc_refs, strict=True):
        asks.append(_ask(ACTIONS["escalation_candidate"], f["source_ids"][0],
                         f["data"]["message"], [f["fact_id"]], ref))
    if deps and first_dep_ref is not None:
        top = deps[0]
        asks.append(_ask(ACTIONS["dependency_late"], top["source_ids"][0],
                         top["data"]["message"], [top["fact_id"]], first_dep_ref))

    gaps = ["Generated by the demo agent: a fixed tool order and template wording, not a "
            "language model."]
    if omitted:
        gaps.append("Some lower-priority facts were left out of the tool results.")
    _apply_fault(fault, findings, health, deps)
    return {"headline": "Attention this week is concentrated in the squads and dependencies "
                        "listed below.",
            "findings": findings, "leadership_asks": asks, "data_gaps": gaps}


def _apply_fault(fault: str | None, findings: list[dict[str, Any]],
                 health: list[dict[str, Any]], deps: list[dict[str, Any]]) -> None:
    if fault == "invented_id":
        findings.append(_finding("risks_dependencies",
                                 "DEP-999 is blocking the release and needs urgent action.",
                                 ["DEPSTATE:DEP-999"]))
    elif fault == "rag_override" and health:
        findings.append(_finding("off_track",
                                 f"{health[0]['data']['squad']} should be downgraded to AMBER "
                                 "given recent progress.", [health[0]["fact_id"]]))
    elif fault == "uncited_claim":
        findings.append(_finding("off_track",
                                 "Overall delivery confidence is improving across the "
                                 "programme.", []))
    elif fault == "wrong_number" and deps:
        findings.append(_finding("risks_dependencies",
                                 f"{deps[0]['source_ids'][0]} is 99 days late and blocks the "
                                 "milestone.", [deps[0]["fact_id"]]))
