"""T2: provider interface, mock provider and factory."""

import json
import re
from datetime import date

import pytest

from adm.ai.models import (
    AnalysisRequest,
    AnalysisResponse,
    Fact,
    FactKind,
    FactPack,
    Focus,
    LLMRequest,
)
from adm.ai.providers import LLMProvider, MockProvider, ProviderError, get_provider
from adm.ai.providers.mock import FAULTS
from adm.config import LLMSettings


def rule(rid, rule_name, severity, message, squad="IDS"):
    return Fact(fact_id=f"RULE:{rule_name}:{rid}", kind=FactKind.RULE_RESULT,
                source_ids=(rid,), squad_id=squad,
                data={"rule": rule_name, "label": rule_name.replace("_", " ").capitalize(),
                      "severity": severity, "message": message})


def record(rid, squad="IDS", **data):
    return Fact(fact_id=rid, kind=FactKind.RECORD, source_ids=(rid,), squad_id=squad, data=data)


PACK = FactPack(
    use_case="raid_analyst",
    request=AnalysisRequest(focus=Focus.IMMEDIATE_ATTENTION),
    as_of=date(2026, 11, 19), rule_version="1.2",
    facts=(
        record("RAID-R-031", probability=4, impact=5, mitigation=None),
        record("RAID-A-018", due_date="2026-11-02"),
        rule("RAID-R-031", "high_risk_no_mitigation", "high",
             "RAID-R-031 scores 20 but has no mitigation recorded."),
        rule("RAID-A-018", "overdue_action", "high",
             "RAID-A-018 was due 2026-11-02 (17 days overdue)."),
        rule("RAID-R-012", "stale_item", "medium",
             "RAID-R-012 has not been updated for 40 days.", squad="MOB"),
        Fact(fact_id="AGG:open_findings:IDS", kind=FactKind.AGGREGATE,
             source_ids=("RAID-R-031", "RAID-A-018"), squad_id="IDS",
             data={"label": "open rule findings for IDS", "count": 2}),
    ),
)


def request(pack=PACK):
    return LLMRequest(system="sys", user="user", fact_pack=pack)


# --- interface -------------------------------------------------------------------------

def test_mock_satisfies_the_provider_interface():
    assert isinstance(MockProvider(), LLMProvider)


def test_factory_returns_mock_by_default():
    p = get_provider(LLMSettings())
    assert isinstance(p, MockProvider) and p.name == "mock" and p.model == "mock-grounded-v1"


def test_factory_keeps_configured_model_name():
    assert get_provider(LLMSettings(model="mock-demo")).model == "mock-demo"


# --- grounded mode ---------------------------------------------------------------------

def test_grounded_response_is_valid_json_matching_the_contract():
    resp = MockProvider().complete(request())
    parsed = AnalysisResponse.model_validate_json(resp.text)
    assert resp.provider == "mock" and resp.model == "mock-grounded-v1"
    assert parsed.findings and parsed.recommendations


def test_grounded_response_is_deterministic():
    assert MockProvider().complete(request()).text == MockProvider().complete(request()).text


def test_grounded_response_only_cites_facts_in_the_pack():
    parsed = AnalysisResponse.model_validate_json(MockProvider().complete(request()).text)
    cited = {i for f in parsed.findings for i in f.fact_ids}
    cited |= {i for r in parsed.recommendations for i in r.fact_ids}
    assert cited and cited <= PACK.fact_ids
    assert all(f.fact_ids for f in parsed.findings)
    assert all(r.fact_ids for r in parsed.recommendations)


def test_high_severity_findings_come_first_and_map_to_immediate_actions():
    parsed = AnalysisResponse.model_validate_json(MockProvider().complete(request()).text)
    assert "RULE:high_risk_no_mitigation:RAID-R-031" in parsed.findings[0].fact_ids
    first = parsed.recommendations[0]
    assert first.urgency == "immediate" and "RAID-R-031" in first.action
    assert first.finding_refs == [0]


def test_grounded_response_never_mentions_a_rag_status():
    # Whole words only: "recorded" and "ordered" contain "red" but are not statuses.
    text = MockProvider().complete(request()).text
    assert not re.search(r"\b(red|amber|green)\b", text, flags=re.IGNORECASE)


def test_mock_records_what_it_was_sent():
    p = MockProvider()
    p.complete(request())
    assert p.calls[0].fact_pack.hash == PACK.hash


def test_grounded_mode_requires_a_fact_pack():
    with pytest.raises(ProviderError, match="fact pack"):
        MockProvider().complete(LLMRequest(system="s", user="u"))


# --- scripted mode ---------------------------------------------------------------------

def test_scripted_mode_returns_responses_in_order_then_errors():
    p = MockProvider(script=["first", "second"])
    assert p.complete(request()).text == "first"
    assert p.complete(request()).text == "second"
    with pytest.raises(ProviderError, match="exhausted"):
        p.complete(request())


# --- fault mode ------------------------------------------------------------------------

@pytest.mark.parametrize("fault", [f for f in FAULTS if f != "malformed_json"])
def test_fault_modes_still_produce_json_but_corrupt_one_thing(fault):
    data = json.loads(MockProvider(fault=fault).complete(request()).text)
    first = data["findings"][0]
    expected = {
        "invented_id": lambda: "RAID-R-999" in first["fact_ids"],
        "wrong_number": lambda: "47" in first["statement"],
        "rag_override": lambda: "RED" in first["statement"],
        "uncited_claim": lambda: first["fact_ids"] == [],
    }[fault]
    assert expected()


def test_malformed_json_fault_is_not_parseable():
    with pytest.raises(json.JSONDecodeError):
        json.loads(MockProvider(fault="malformed_json").complete(request()).text)


def test_unknown_fault_is_rejected():
    with pytest.raises(ValueError):
        MockProvider(fault="something")
