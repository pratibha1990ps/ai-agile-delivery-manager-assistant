"""AI RAID Analyst service: one call from a user's question to a validated, audited answer.

    request ─► rules (deterministic) ─► fact pack ─► prompt ─► provider (the only AI step)
            ─► grounding validator ─► audit log ─► AnalysisResult

The provider is injected, so the same service runs on the mock, a local model or Claude, and
tests can pass in fakes. Steps 1-4 never touch AI: the same data and request always produce
the same fact pack (and hash), whichever provider answers.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from adm.ai.audit import AuditEntry, log_analysis
from adm.ai.grounding import Status, ValidatedAnalysis, ValidatedItem, validate
from adm.ai.models import AnalysisRequest, Fact, FactKind, FactPack, LLMRequest
from adm.ai.prompt_registry import PromptRegistry, render
from adm.ai.providers import LLMProvider, MockProvider, ProviderError, get_provider
from adm.config import AppConfig
from adm.facts.fact_pack import USE_CASE, build_fact_pack, compute_rule_bundle
from adm.services.data import load_programme_data

PROMPT_ID = "raid_analyst"
VERIFIED_FACT_KINDS = (FactKind.RULE_RESULT, FactKind.METRIC, FactKind.AGGREGATE)


class AnalysisError(ValueError):
    """The analysis cannot start (no data, unknown scope, note too long). No AI is called."""


@dataclass
class AnalysisResult:
    request: AnalysisRequest
    pack: FactPack
    status: Status
    validation: ValidatedAnalysis | None
    ai_output_id: int
    provider: str
    model: str
    prompt_ref: str
    latency_ms: int | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status not in (Status.INVALID, Status.PROVIDER_ERROR)

    def verified_facts(self) -> list[Fact]:
        """Layer 1 of the UI: what the rules computed (shown whether or not AI succeeded)."""
        return [f for f in self.pack.facts if f.kind in VERIFIED_FACT_KINDS]

    def facts_for(self, item: ValidatedItem) -> list[Fact]:
        by_id = self.pack.by_id
        return [by_id[c.fact_id] for c in item.citations if c.valid]


class RaidAnalyst:
    def __init__(self, conn: sqlite3.Connection, provider: LLMProvider, cfg: AppConfig,
                 registry: PromptRegistry | None = None, prompt_version: str = "latest") -> None:
        self.conn = conn
        self.provider = provider
        self.cfg = cfg
        self.registry = registry or PromptRegistry()
        self.prompt_version = prompt_version

    def analyse(self, request: AnalysisRequest) -> AnalysisResult:
        settings, llm = self.cfg.settings, self.cfg.llm
        if len(request.note) > settings.ai.max_note_chars:
            raise AnalysisError(f"The note is longer than {settings.ai.max_note_chars} "
                                "characters.")

        # 1-3: deterministic. No AI involved.
        data = load_programme_data(self.conn)
        if data.is_empty:
            raise AnalysisError("No programme data. Load the demo dataset on Data Import.")
        bundle = compute_rule_bundle(data, settings.app.as_of_date, self.cfg.thresholds)
        try:
            pack = build_fact_pack(data, bundle, request, settings.ai.max_facts)
        except ValueError as exc:
            raise AnalysisError(str(exc)) from exc

        # 4: versioned prompt
        template = self.registry.get(PROMPT_ID, self.prompt_version)
        prompt = render(template, pack)

        # 5: the only AI step
        llm_request = LLMRequest(
            system=prompt.system, user=prompt.user, temperature=llm.temperature,
            max_tokens=llm.max_tokens, timeout_seconds=llm.timeout_seconds,
            response_schema=None, fact_pack=pack,
        )
        raw_text: str | None = None
        model = self.provider.model
        latency: int | None = None
        usage: dict[str, int] = {}
        error: str | None = None
        start = time.perf_counter()
        try:
            response = self.provider.complete(llm_request)
            raw_text, model, latency, usage = (response.text, response.model,
                                               response.latency_ms, response.usage)
        except ProviderError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - a provider bug must not crash the page
            error = f"Unexpected provider failure ({type(exc).__name__})."
        if latency is None:
            latency = int((time.perf_counter() - start) * 1000)

        # 6: grounding validation
        validation = validate(raw_text, pack) if raw_text is not None else None
        status = validation.status if validation else Status.PROVIDER_ERROR

        # 7: audit (always, including failures). The key is used only to redact.
        output_id = log_analysis(self.conn, AuditEntry(
            use_case=USE_CASE, provider=self.provider.name, model=model,
            prompt_ref=prompt.prompt_ref, prompt_sha256=prompt.prompt_sha256, pack=pack,
            raw_text=raw_text, validation=validation, latency_ms=latency, usage=usage,
            error=error,
        ), secrets=[llm.api_key() or ""])

        return AnalysisResult(request=request, pack=pack, status=status,
                              validation=validation, ai_output_id=output_id,
                              provider=self.provider.name, model=model,
                              prompt_ref=prompt.prompt_ref, latency_ms=latency, error=error)


def create_analyst(conn: sqlite3.Connection, cfg: AppConfig,
                   simulate_fault: str | None = None) -> RaidAnalyst:
    """Build the analyst with the configured provider. ``simulate_fault`` swaps in the mock's
    fault mode to demonstrate the validator (clearly labelled in the UI)."""
    provider = (MockProvider(fault=simulate_fault) if simulate_fault
                else get_provider(cfg.llm))
    return RaidAnalyst(conn, provider, cfg)
