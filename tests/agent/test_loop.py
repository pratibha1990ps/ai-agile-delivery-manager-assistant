"""A3: the agent loop, driven by a scripted (fake) AI so it is free, offline and repeatable."""

import json

import pytest

from adm.agent.loop import (
    DEFAULT_MAX_TOOL_CALLS,
    AgentLoop,
    RunStatus,
    parse_action,
)
from adm.ai.models import LLMRequest, LLMResponse
from adm.ai.providers import MockProvider
from adm.ai.providers.base import ProviderError

GOAL = "Prepare this week's leadership report."


def call(tool, **args):
    return json.dumps({"action": "call_tool", "tool": tool, "args": args})


def final(**answer):
    return json.dumps({"action": "final", "answer": answer or {"headline": "ok"}})


def run_script(toolbox, script, **kw):
    provider = MockProvider(script=script)
    return AgentLoop(provider, toolbox, "system text", **kw).run(GOAL), provider


# --- reading the model's reply ------------------------------------------------------------

def test_parse_accepts_plain_and_fenced_json():
    plain = parse_action(call("get_programme_health"))
    fenced = parse_action("```json\n" + call("get_programme_health") + "\n```")
    assert plain == fenced and plain.tool == "get_programme_health"


@pytest.mark.parametrize("text,fragment", [
    ("not json at all", "not valid JSON"),
    ("[1, 2]", "one JSON object"),
    ('{"action": "dance"}', "invalid action"),
    ('{"action": "call_tool"}', "tool"),
    ('{"action": "final"}', "answer"),
    ('{"action": "final", "answer": {}, "extra": 1}', "invalid action"),
])
def test_parse_rejects_unusable_replies_with_a_plain_reason(text, fragment):
    with pytest.raises(ValueError, match=fragment):
        parse_action(text)


# --- the happy path -----------------------------------------------------------------------

def test_agent_calls_tools_then_finishes(toolbox):
    run, provider = run_script(toolbox, [
        call("get_programme_health"),
        call("get_late_dependencies"),
        final(headline="Data squad is blocked."),
    ])
    assert run.status is RunStatus.COMPLETED and run.ok
    assert run.tool_calls_used == 2 and run.turns == 3 and len(provider.calls) == 3
    assert [s.kind for s in run.steps] == ["tool_call", "tool_call", "final"]
    assert run.final_answer == {"headline": "Data squad is blocked."}


def test_evidence_is_the_union_of_what_the_tools_returned(toolbox):
    run, _ = run_script(toolbox, [call("get_programme_health"), call("get_late_dependencies"),
                                  final()])
    ids = set(run.evidence)
    assert "HEALTH:DAT" in ids and "DEPSTATE:DEP-012" in ids
    assert ids == {i for s in run.steps for i in s.fact_ids}


def test_each_prompt_shows_the_goal_tools_and_what_was_learned(toolbox):
    _, provider = run_script(toolbox, [call("get_programme_health"), final()])
    first, second = provider.calls[0].user, provider.calls[1].user
    assert GOAL in first and "get_escalation_candidates" in first
    assert "nothing yet" in first and "Tool calls left: 8" in first
    assert "HEALTH:DAT" in second and "Tool calls left: 7" in second
    assert provider.calls[0].system == "system text"


def test_run_is_repeatable(toolbox):
    script = [call("get_squad_metrics", squad="DAT"), final()]
    a, _ = run_script(toolbox, list(script))
    b, _ = run_script(toolbox, list(script))
    assert [s.fact_ids for s in a.steps] == [s.fact_ids for s in b.steps]


# --- the guardrails -----------------------------------------------------------------------

def test_default_limit_is_eight_tool_calls():
    assert DEFAULT_MAX_TOOL_CALLS == 8


def test_a_ninth_tool_call_is_refused_and_the_run_stops(toolbox):
    tools = ["get_programme_health", "get_late_dependencies", "get_escalation_candidates",
             "get_milestone_status", "get_open_raid_items"]
    script = [call(t) for t in tools]
    script += [call("get_squad_metrics", squad=s) for s in ("DAT", "IDS", "MOB")]  # 8th call
    script += [call("get_squad_metrics", squad="PAY")]                              # 9th: no
    run, provider = run_script(toolbox, script)
    assert run.status is RunStatus.TOOL_LIMIT and not run.ok
    assert run.tool_calls_used == 8 and len(provider.calls) == 9
    assert "more than 8" in run.error
    assert "PAY" not in " ".join(run.evidence).replace("HEALTH:PAY", "")  # never fetched


def test_limit_can_be_lowered(toolbox):
    run, _ = run_script(toolbox, [call("get_programme_health"), call("get_milestone_status")],
                        max_tool_calls=1)
    assert run.status is RunStatus.TOOL_LIMIT and run.tool_calls_used == 1


def test_the_prompt_tells_the_model_when_it_must_stop(toolbox):
    _, provider = run_script(toolbox, [call("get_programme_health"), final()], max_tool_calls=1)
    assert "Tool calls left: 0" in provider.calls[1].user


def test_repeating_an_identical_call_is_refused_and_not_charged(toolbox):
    run, provider = run_script(toolbox, [call("get_programme_health"),
                                         call("get_programme_health"), final()])
    assert [s.kind for s in run.steps] == ["tool_call", "duplicate", "final"]
    assert run.tool_calls_used == 1 and run.ok
    assert "already called" in provider.calls[2].user


def test_endless_duplicates_end_with_a_turn_limit(toolbox):
    run, _ = run_script(toolbox, [call("get_programme_health")] * 20, max_tool_calls=2)
    assert run.status is RunStatus.TURN_LIMIT and run.turns == 6
    assert run.tool_calls_used == 1


# --- mistakes the model can recover from --------------------------------------------------

def test_a_bad_tool_call_is_explained_and_the_agent_can_recover(toolbox):
    run, provider = run_script(toolbox, [call("delete_everything"),
                                         call("get_squad_metrics", squad="XYZ"),
                                         call("get_squad_metrics", squad="DAT"), final()])
    assert [s.kind for s in run.steps] == ["tool_error", "tool_error", "tool_call", "final"]
    assert "Unknown tool" in provider.calls[1].user
    assert "Unknown squad" in provider.calls[2].user
    assert run.ok and run.tool_calls_used == 1
    assert not any(i.startswith("HEALTH:PAY") for i in run.evidence)


def test_one_unreadable_reply_is_forgiven(toolbox):
    run, provider = run_script(toolbox, ["Sure! Let me look.", call("get_programme_health"),
                                         final()])
    assert run.ok and run.steps[0].kind == "bad_output"
    assert "could not be used" in provider.calls[1].user


def test_two_unreadable_replies_in_a_row_end_the_run(toolbox):
    run, _ = run_script(toolbox, ["hello", "still not json", final()])
    assert run.status is RunStatus.INVALID_OUTPUT and run.final_answer is None


# --- failures never crash the loop --------------------------------------------------------

def test_provider_error_ends_the_run_and_keeps_the_trace(toolbox):
    run, _ = run_script(toolbox, [call("get_programme_health")])   # script runs out
    assert run.status is RunStatus.PROVIDER_ERROR
    assert "exhausted" in run.error
    assert run.tool_calls_used == 1 and "HEALTH:DAT" in run.evidence


def test_unexpected_exception_becomes_a_provider_error(toolbox):
    class Exploding:
        name, model = "boom", "boom-1"

        def complete(self, request: LLMRequest) -> LLMResponse:
            raise KeyError("secret-detail")

    run = AgentLoop(Exploding(), toolbox, "s").run(GOAL)
    assert run.status is RunStatus.PROVIDER_ERROR
    assert run.error == "Unexpected provider failure (KeyError)."
    assert "secret-detail" not in run.error


def test_usage_is_added_up_across_turns(toolbox):
    class Counting:
        name, model = "counting", "c-1"

        def __init__(self):
            self.replies = [call("get_programme_health"), final()]

        def complete(self, request):
            return LLMResponse(text=self.replies.pop(0), provider="counting", model="c-1",
                               usage={"input_tokens": 100, "output_tokens": 10})

    run = AgentLoop(Counting(), toolbox, "s").run(GOAL)
    assert run.usage == {"input_tokens": 200, "output_tokens": 20}


def test_a_failing_provider_error_message_is_passed_through(toolbox):
    class Down:
        name, model = "down", "d-1"

        def complete(self, request):
            raise ProviderError("Could not reach the model server.")

    run = AgentLoop(Down(), toolbox, "s").run(GOAL)
    assert run.status is RunStatus.PROVIDER_ERROR
    assert run.error == "Could not reach the model server."


def test_loop_needs_at_least_one_tool_call(toolbox):
    with pytest.raises(ValueError):
        AgentLoop(MockProvider(script=[]), toolbox, "s", max_tool_calls=0)
