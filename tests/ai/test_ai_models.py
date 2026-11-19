"""T1: contracts for the AI layer."""

import shutil
from datetime import date

import pytest
from pydantic import ValidationError

from adm.ai.models import (
    FOCUS_QUESTIONS,
    AnalysisRequest,
    AnalysisResponse,
    Fact,
    FactKind,
    FactPack,
    Focus,
)
from adm.config import DEFAULT_CONFIG_DIR, load_config

REQ = AnalysisRequest(focus=Focus.ESCALATION)


def fact(fid="RAID-R-031", kind=FactKind.RECORD, sources=("RAID-R-031",), **data):
    return Fact(fact_id=fid, kind=kind, source_ids=sources, squad_id="IDS", data=data)


def pack(*facts, **kw):
    return FactPack(use_case="raid_analyst", request=REQ, as_of=date(2026, 11, 19),
                    rule_version="1.2", facts=facts, **kw)


# --- request ---------------------------------------------------------------------------

def test_request_defaults_to_programme_scope():
    assert REQ.is_programme and REQ.scope == "programme"
    assert REQ.question == FOCUS_QUESTIONS[Focus.ESCALATION]


def test_every_focus_has_a_question():
    assert set(FOCUS_QUESTIONS) == set(Focus)


def test_request_rejects_blank_scope_and_unknown_fields():
    with pytest.raises(ValidationError):
        AnalysisRequest(scope="  ", focus=Focus.ESCALATION)
    with pytest.raises(ValidationError):
        AnalysisRequest(focus=Focus.ESCALATION, provider="x")


# --- evidence --------------------------------------------------------------------------

def test_fact_requires_a_source():
    with pytest.raises(ValidationError):
        Fact(fact_id="AGG:x", kind=FactKind.AGGREGATE, source_ids=())


def test_record_fact_must_trace_to_itself():
    with pytest.raises(ValidationError, match="own ID"):
        fact(fid="RAID-R-031", sources=("RAID-R-032",))


def test_fact_pack_rejects_duplicate_ids():
    with pytest.raises(ValidationError, match="duplicate"):
        pack(fact(), fact())


def test_fact_pack_hash_is_stable_and_content_sensitive():
    a = pack(fact(score=20), fact("RULE:x:RAID-R-031", FactKind.RULE_RESULT))
    b = pack(fact(score=20), fact("RULE:x:RAID-R-031", FactKind.RULE_RESULT))
    c = pack(fact(score=16), fact("RULE:x:RAID-R-031", FactKind.RULE_RESULT))
    assert a.hash == b.hash and len(a.hash) == 64
    assert a.hash != c.hash


def test_fact_pack_exposes_ids_and_sources():
    p = pack(fact(), fact("AGG:overdue_actions:IDS", FactKind.AGGREGATE,
                          ("RAID-A-018", "RAID-A-019"), count=2))
    assert p.fact_ids == {"RAID-R-031", "AGG:overdue_actions:IDS"}
    assert p.source_ids == ["RAID-A-018", "RAID-A-019", "RAID-R-031"]


# --- answer ----------------------------------------------------------------------------

VALID = {
    "summary": "Identity & Security carries the highest unmanaged risk.",
    "findings": [{"statement": "RAID-R-031 scores 20 with no mitigation.",
                  "fact_ids": ["RAID-R-031"], "confidence": "high"}],
    "recommendations": [{"action": "Agree a mitigation owner for RAID-R-031",
                         "rationale": "Highest score with no mitigation.", "owner_role": "RTE",
                         "urgency": "immediate", "fact_ids": ["RAID-R-031"],
                         "finding_refs": [0]}],
}


def test_valid_response_parses():
    r = AnalysisResponse.model_validate(VALID)
    assert r.findings[0].confidence == "high" and r.recommendations[0].finding_refs == [0]


def test_response_cannot_carry_a_rag_field():
    bad = {**VALID, "rag": "RED"}
    with pytest.raises(ValidationError, match="rag"):
        AnalysisResponse.model_validate(bad)
    bad_item = {**VALID, "findings": [{**VALID["findings"][0], "status": "RED"}]}
    with pytest.raises(ValidationError):
        AnalysisResponse.model_validate(bad_item)


def test_response_rejects_bad_enums_and_negative_refs():
    with pytest.raises(ValidationError):
        AnalysisResponse.model_validate(
            {**VALID, "findings": [{**VALID["findings"][0], "confidence": "certain"}]})
    rec = {**VALID["recommendations"][0], "finding_refs": [-1]}
    with pytest.raises(ValidationError):
        AnalysisResponse.model_validate({**VALID, "recommendations": [rec]})


def test_json_schema_lists_required_fields():
    schema = AnalysisResponse.json_schema()
    assert "summary" in schema["required"]
    assert schema["additionalProperties"] is False


# --- config ----------------------------------------------------------------------------

def test_ai_settings_load_from_project_config():
    cfg = load_config()
    assert cfg.settings.ai.max_facts == 80
    assert cfg.llm.timeout_seconds == 120 and cfg.llm.max_tokens == 16000


def test_ai_section_is_optional(tmp_path):
    """Backward compatible: a settings.yaml without the ai section still loads."""
    for name in ("thresholds.yaml", "llm.yaml.example"):
        shutil.copy(DEFAULT_CONFIG_DIR / name, tmp_path / name)
    text = (DEFAULT_CONFIG_DIR / "settings.yaml").read_text(encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(text.split("\nai:")[0] + "\n", encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.settings.ai.max_facts == 80
