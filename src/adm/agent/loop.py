"""The agent loop: lets an AI choose which read-only tool to use next, within hard limits.

How a run works (all of it is ordinary, testable Python; the AI only picks the next move):

1. We tell the model the goal, the tools it may use and what it has learned so far.
2. The model replies with **one JSON action**: ``call_tool`` (name + arguments) or ``final``.
3. We run the tool (rules, read-only) and add the result to what the model has learned.
4. Repeat until the model gives its final answer or a limit is hit.

Hard limits (the guardrails): at most ``max_tool_calls`` tool calls (default 8); at most
``max_tool_calls + 4`` model turns in total; two unreadable replies in a row end the run; the
same call twice is refused without being charged. A run never raises: it always returns an
``AgentRun`` saying how it ended, with every step recorded for the audit trail.

The loop speaks plain-text JSON through the existing ``LLMProvider`` interface, so it works with
the Mock, Ollama, OpenAI-compatible and Anthropic providers alike. It does not judge the final
answer: A4 checks it against ``AgentRun.evidence`` using the grounding validator.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from adm.agent.tools import ToolBox, ToolError
from adm.ai.models import Fact, LLMRequest
from adm.ai.providers.base import LLMProvider, ProviderError

DEFAULT_MAX_TOOL_CALLS = 8
EXTRA_TURNS = 4            # allowance for corrections and the final answer
MAX_CONSECUTIVE_BAD = 2


class RunStatus(StrEnum):
    COMPLETED = "completed"              # the model gave a final answer
    TOOL_LIMIT = "tool_limit"            # it asked for more tool calls than allowed
    TURN_LIMIT = "turn_limit"            # too many turns without finishing
    INVALID_OUTPUT = "invalid_output"    # replies could not be read as an action
    PROVIDER_ERROR = "provider_error"    # the AI backend failed


class AgentAction(BaseModel):
    """One move chosen by the model."""
    model_config = ConfigDict(extra="forbid")

    action: Literal["call_tool", "final"]
    tool: str | None = None
    args: dict[str, Any] = {}
    answer: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _shape(self) -> AgentAction:
        if self.action == "call_tool" and not self.tool:
            raise ValueError("call_tool needs a 'tool' name")
        if self.action == "final" and self.answer is None:
            raise ValueError("final needs an 'answer' object")
        return self


@dataclass(frozen=True)
class AgentStep:
    """One recorded turn. ``kind`` is tool_call | tool_error | duplicate | bad_output | final."""
    turn: int
    kind: str
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    fact_ids: tuple[str, ...] = ()
    omitted_count: int = 0
    message: str = ""
    latency_ms: int | None = None
    payload: dict[str, Any] | None = None      # what a successful tool returned


@dataclass
class AgentRun:
    goal: str
    provider: str
    model: str
    status: RunStatus = RunStatus.COMPLETED
    steps: list[AgentStep] = field(default_factory=list)
    evidence: dict[str, Fact] = field(default_factory=dict)
    final_answer: dict[str, Any] | None = None
    final_text: str | None = None
    tool_calls_used: int = 0
    turns: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is RunStatus.COMPLETED

    @property
    def evidence_facts(self) -> tuple[Fact, ...]:
        return tuple(self.evidence.values())


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

PROTOCOL = (
    "You are working towards the goal below by using read-only tools, one at a time.\n"
    "Reply with ONE JSON object and nothing else. Two forms are allowed:\n\n"
    '  {"action": "call_tool", "tool": "<tool name>", "args": {...}}\n'
    '  {"action": "final", "answer": {...}}\n\n'
    "Rules: use only the tools listed; the facts the tools return are your only evidence; "
    "never invent IDs, numbers or statuses. Tool calls left: __REMAINING__ "
    "(when it is 0 you must give your final answer now)."
)


def parse_action(text: str) -> AgentAction:
    """Read a model reply as an action. Raises ``ValueError`` with a plain explanation."""
    body = text.strip()
    fenced = _FENCE.match(body)
    if fenced:
        body = fenced.group(1)
    try:
        raw = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("the reply was not valid JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError("the reply must be one JSON object")
    try:
        return AgentAction.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "reply"
        raise ValueError(f"invalid action ({where}: {first['msg']})") from exc


class AgentLoop:
    def __init__(self, provider: LLMProvider, toolbox: ToolBox, system: str,
                 max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS, max_tokens: int = 4000,
                 timeout_seconds: float = 120.0) -> None:
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")
        self.provider = provider
        self.toolbox = toolbox
        self.system = system
        self.max_tool_calls = max_tool_calls
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds

    def run(self, goal: str) -> AgentRun:
        run = AgentRun(goal=goal, provider=self.provider.name, model=self.provider.model)
        max_turns = self.max_tool_calls + EXTRA_TURNS
        seen: set[str] = set()
        bad_in_a_row = 0
        note = ""

        for turn in range(1, max_turns + 1):
            run.turns = turn
            request = LLMRequest(
                system=self.system, user=self._prompt(run, note),
                temperature=0.0, max_tokens=self.max_tokens,
                timeout_seconds=self.timeout_seconds)
            try:
                response = self.provider.complete(request)
            except ProviderError as exc:
                return self._end(run, RunStatus.PROVIDER_ERROR, str(exc))
            except Exception as exc:                       # never let a run crash the page
                return self._end(run, RunStatus.PROVIDER_ERROR,
                                 f"Unexpected provider failure ({type(exc).__name__}).")
            for key, value in response.usage.items():
                run.usage[key] = run.usage.get(key, 0) + value

            try:
                action = parse_action(response.text)
            except ValueError as exc:
                bad_in_a_row += 1
                note = (f"Your last reply could not be used: {exc}. Reply with exactly one "
                        f"JSON object in one of the two allowed forms.")
                run.steps.append(AgentStep(turn, "bad_output", message=str(exc),
                                           latency_ms=response.latency_ms))
                if bad_in_a_row >= MAX_CONSECUTIVE_BAD:
                    return self._end(run, RunStatus.INVALID_OUTPUT,
                                     f"The model replied with unreadable output twice in a "
                                     f"row ({exc}).")
                continue
            bad_in_a_row = 0

            if action.action == "final":
                run.final_answer, run.final_text = action.answer, response.text
                run.steps.append(AgentStep(turn, "final", latency_ms=response.latency_ms))
                run.status = RunStatus.COMPLETED
                return run

            note = self._tool_turn(run, turn, action, seen, response.latency_ms)
            if run.status is RunStatus.TOOL_LIMIT:
                return run
        return self._end(run, RunStatus.TURN_LIMIT, f"No final answer after {max_turns} turns.")

    # --- one tool turn ----------------------------------------------------------------------

    def _tool_turn(self, run: AgentRun, turn: int, action: AgentAction, seen: set[str],
                   latency: int | None) -> str:
        """Run the requested tool and record the step. Returns a note for the next prompt."""
        tool, args = action.tool or "", action.args
        key = f"{tool}:{json.dumps(args, sort_keys=True)}"
        if key in seen:
            msg = (f"You already called {tool} with these arguments; its result is above. "
                   f"Call a different tool or give your final answer.")
            run.steps.append(AgentStep(turn, "duplicate", tool, args, message=msg,
                                       latency_ms=latency))
            return msg
        if run.tool_calls_used >= self.max_tool_calls:
            run.steps.append(AgentStep(turn, "tool_error", tool, args,
                                       message="Tool call limit reached.", latency_ms=latency))
            self._end(run, RunStatus.TOOL_LIMIT,
                      f"The model asked for more than {self.max_tool_calls} tool calls.")
            return ""
        try:
            result = self.toolbox.call(tool, args)
        except ToolError as exc:
            run.steps.append(AgentStep(turn, "tool_error", tool, args, message=str(exc),
                                       latency_ms=latency))
            return f"Tool call failed: {exc}"
        seen.add(key)
        run.tool_calls_used += 1
        for fact in result.facts:
            run.evidence.setdefault(fact.fact_id, fact)
        run.steps.append(AgentStep(turn, "tool_call", tool, args, fact_ids=result.fact_ids,
                                   omitted_count=result.omitted_count, latency_ms=latency,
                                   payload=result.to_payload()))
        return ""

    # --- prompt ------------------------------------------------------------------------------

    def _prompt(self, run: AgentRun, note: str) -> str:
        remaining = self.max_tool_calls - run.tool_calls_used
        parts = [PROTOCOL.replace("__REMAINING__", str(remaining)),
                 "GOAL:\n" + run.goal,
                 "TOOLS:\n" + json.dumps(self.toolbox.specs(), indent=1)]
        learned = [f"--- result of {s.tool} {json.dumps(s.args)} ---\n"
                   + json.dumps(s.payload, indent=1)
                   for s in run.steps if s.kind == "tool_call"]
        parts.append("WHAT YOU HAVE LEARNED SO FAR:\n" + ("\n".join(learned) or "nothing yet."))
        if note:
            parts.append("NOTE:\n" + note)
        return "\n\n".join(parts)

    @staticmethod
    def _end(run: AgentRun, status: RunStatus, error: str) -> AgentRun:
        run.status, run.error = status, error
        return run
