"""T9: end-to-end RAID analysis: rules -> fact pack -> prompt -> provider -> validation -> audit."""

import pytest

from adm.ai.audit import citations_for, get_output, record_review
from adm.ai.grounding import Status, Verdict
from adm.ai.models import AnalysisRequest, Focus, LLMResponse
from adm.ai.providers import MockProvider, ProviderError
from adm.config import load_config
from adm.db.connection import connect, init_db
from adm.ingestion.importer import import_files
from adm.services.raid_analyst import AnalysisError, RaidAnalyst, create_analyst
from tests.conftest import read_files

CFG = load_config()
ESCALATIONS = {"RAID-R-031", "RAID-R-032", "RAID-R-033", "RAID-R-020", "RAID-I-005",
               "RAID-A-018", "RAID-A-022"}


@pytest.fixture
def conn(demo_dir):
    c = connect(":memory:")
    init_db(c)
    assert import_files(c, read_files(demo_dir.glob("*.csv"))).status == "imported"
    yield c
    c.close()


class RecordingProvider:
    """A fake provider: records the request and returns a fixed text (or raises)."""
    name, model = "fake", "fake-model-1"

    def __init__(self, text="{}", exc=None):
        self.text, self.exc, self.requests = text, exc, []

    def complete(self, request):
        self.requests.append(request)
        if self.exc:
            raise self.exc
        return LLMResponse(text=self.text, provider=self.name, model=self.model, latency_ms=5)


def analyse(conn, provider=None, **req):
    analyst = RaidAnalyst(conn, provider or MockProvider(), CFG)
    return analyst.analyse(AnalysisRequest(**{"focus": Focus.ESCALATION, **req}))


# --- end to end with the mock ---------------------------------------------------------------

def test_escalation_analysis_end_to_end(conn):
    result = analyse(conn)
    assert result.status is Status.VALID and result.ok
    esc_facts = {f.fact_id for f in result.pack.facts if f.fact_id.startswith("ESC:")}
    assert esc_facts == {f"ESC:{r}" for r in ESCALATIONS}
    cited = {c.fact_id for item in result.validation.findings for c in item.citations}
    assert {f"ESC:{r}" for r in ESCALATIONS} <= cited          # every candidate discussed
    assert all(v.verdict is Verdict.VERIFIED for v in result.validation.items)

    row = get_output(conn, result.ai_output_id)
    assert row["provider"] == "mock" and row["prompt_version"] == "raid_analyst@v1"
    assert row["fact_pack_hash"] == result.pack.hash
    assert row["validation_status"] == "valid"
    assert not citations_for(conn, result.ai_output_id).empty


@pytest.mark.parametrize("focus", list(Focus))
@pytest.mark.parametrize("scope", ["programme", "DAT", "PAY"])
def test_every_scope_and_focus_produces_a_valid_audited_answer(conn, scope, focus):
    result = analyse(conn, scope=scope, focus=focus)
    assert result.status is Status.VALID, result.validation.report()
    assert get_output(conn, result.ai_output_id)["validation_status"] == "valid"


def test_verified_facts_layer_contains_only_rule_output(conn):
    result = analyse(conn, scope="IDS", focus=Focus.IMMEDIATE_ATTENTION)
    kinds = {f.kind.value for f in result.verified_facts()}
    assert kinds <= {"rule_result", "metric", "aggregate"} and kinds
    item = result.validation.findings[0]
    assert {f.fact_id for f in result.facts_for(item)} == {c.fact_id for c in item.citations}


def test_human_can_review_a_recommendation_from_a_real_run(conn):
    result = analyse(conn)
    record_review(conn, result.ai_output_id, "recommendation", 0, "approved", "Priya Nair")
    assert get_output(conn, result.ai_output_id)["approved_by"] is None   # others pending


# --- dependency injection: what the provider receives ----------------------------------------

def test_provider_receives_rendered_prompt_fact_pack_and_config(conn):
    fake = RecordingProvider(text=MockProvider().complete.__self__ and "{}")
    analyse(conn, provider=fake, scope="DAT", note="Steering on Friday")
    req = fake.requests[0]
    assert "Never assign, change" in req.system
    assert "Which RAID items require leadership escalation?" in req.user
    assert "SCOPE: squad DAT" in req.user and "Steering on Friday" in req.user
    assert '"fact_id":"ESC:RAID-I-005"' in req.user
    assert req.fact_pack is not None and req.fact_pack.request.scope == "DAT"
    assert req.max_tokens == CFG.llm.max_tokens and req.timeout_seconds == CFG.llm.timeout_seconds


def test_fact_pack_is_identical_whichever_provider_answers(conn):
    a = analyse(conn, provider=RecordingProvider())
    b = analyse(conn, provider=MockProvider())
    assert a.pack.hash == b.pack.hash          # rules and retrieval never depend on the AI


# --- failures -------------------------------------------------------------------------------

def test_faulty_ai_answer_is_caught_and_audited(conn):
    result = create_analyst(conn, CFG, simulate_fault="invented_id").analyse(
        AnalysisRequest(focus=Focus.ESCALATION))
    assert result.status is Status.PARTIALLY_REJECTED
    assert result.validation.findings[0].verdict is Verdict.REJECTED
    assert get_output(conn, result.ai_output_id)["validation_status"] == "partially_rejected"


def test_invalid_json_is_audited_as_invalid(conn):
    result = analyse(conn, provider=RecordingProvider(text="not json at all"))
    assert result.status is Status.INVALID and not result.ok
    assert result.validation.error and result.verified_facts()   # rule facts still available


def test_provider_error_is_audited_and_reported(conn):
    result = analyse(conn, provider=RecordingProvider(exc=ProviderError("Timed out after 30s.")))
    assert result.status is Status.PROVIDER_ERROR and result.error == "Timed out after 30s."
    row = get_output(conn, result.ai_output_id)
    assert row["validation_status"] == "provider_error" and row["raw_response"] is None


def test_unexpected_provider_crash_does_not_leak_details(conn):
    boom = RecordingProvider(exc=RuntimeError("internal detail sk-should-not-show"))
    result = analyse(conn, provider=boom)
    assert result.status is Status.PROVIDER_ERROR
    assert result.error == "Unexpected provider failure (RuntimeError)."
    assert "sk-should-not-show" not in get_output(conn, result.ai_output_id)["parsed_json"]


def test_echoed_api_key_is_redacted_in_the_audit(conn, monkeypatch):
    key = "sk-test-ECHOED-KEY-12345"
    monkeypatch.setenv("ADM_TEST_KEY", key)
    cfg = CFG.model_copy(update={"llm": CFG.llm.model_copy(update={"api_key_env":
                                                                   "ADM_TEST_KEY"})})
    leaky = RecordingProvider(text=f'{{"summary": "my key is {key}"}}')
    result = RaidAnalyst(conn, leaky, cfg).analyse(AnalysisRequest(focus=Focus.ESCALATION))
    row = get_output(conn, result.ai_output_id)
    assert key not in row["raw_response"] and "[REDACTED]" in row["raw_response"]


# --- input problems stop before any AI call ---------------------------------------------------

def test_note_too_long_is_refused_without_calling_ai(conn):
    fake = RecordingProvider()
    with pytest.raises(AnalysisError, match="longer than"):
        analyse(conn, provider=fake, note="x" * (CFG.settings.ai.max_note_chars + 1))
    assert not fake.requests
    assert conn.execute("SELECT COUNT(*) FROM ai_output").fetchone()[0] == 0


def test_unknown_scope_is_refused(conn):
    with pytest.raises(AnalysisError, match="Unknown scope"):
        analyse(conn, scope="XYZ")


def test_empty_database_is_refused():
    c = connect(":memory:")
    init_db(c)
    with pytest.raises(AnalysisError, match="No programme data"):
        analyse(c)


def test_create_analyst_uses_configured_provider(conn):
    assert create_analyst(conn, CFG).provider.name == "mock"
    assert create_analyst(conn, CFG, simulate_fault="wrong_number").provider.fault == \
        "wrong_number"
