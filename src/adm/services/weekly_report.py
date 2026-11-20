"""Weekly Delivery Report Agent service: from a click to a validated, audited draft report.

    rules (deterministic) ─► toolbox (read-only) ─► prompt ─► agent loop (AI picks tools)
        ─► report validation (grounding checks) ─► audit log ─► ReportResult

The provider is injected, so the same service runs on the demo agent, a local model or Claude.
Everything except the model's choices is ordinary code: the rules, the tools, the limits, the
checks and the audit trail. A run never raises because of the AI; it always returns a result
that says how it ended.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from adm.agent.demo import DemoAgentProvider
from adm.agent.loop import DEFAULT_MAX_TOOL_CALLS, AgentLoop, AgentRun
from adm.agent.prompts import render_report_prompt
from adm.agent.report import ReportStatus, ValidatedReport, validate_report
from adm.agent.tools import ToolBox
from adm.ai.audit import AuditEntry, log_analysis
from adm.ai.prompt_registry import PromptRegistry
from adm.ai.providers import LLMProvider, get_provider
from adm.config import AppConfig
from adm.facts.fact_pack import compute_rule_bundle
from adm.services.data import load_programme_data

USE_CASE = "weekly_report"


class ReportError(ValueError):
    """The report cannot start (no data, note too long). No AI is called."""


@dataclass
class ReportResult:
    report: ValidatedReport
    ai_output_id: int
    provider: str
    model: str
    prompt_ref: str
    latency_ms: int

    @property
    def status(self) -> ReportStatus:
        return self.report.status

    @property
    def run(self) -> AgentRun:
        return self.report.run


def trace_for_audit(report: ValidatedReport) -> dict[str, Any]:
    """Everything needed to understand a run later: steps, sections, table, coverage."""
    run = report.run
    return {
        "goal": run.goal, "run_status": run.status.value, "run_error": run.error,
        "turns": run.turns, "tool_calls_used": run.tool_calls_used,
        "tools_consulted": list(report.tools_consulted),
        "coverage_notes": report.coverage_notes,
        "steps": [{"turn": s.turn, "kind": s.kind, "tool": s.tool, "args": s.args,
                   "fact_ids": list(s.fact_ids), "omitted_count": s.omitted_count,
                   "message": s.message, "latency_ms": s.latency_ms} for s in run.steps],
        "finding_sections": ([f.section for f in report.answer.findings]
                             if report.answer else []),
        "squad_table": [{"squad_id": r.squad_id, "name": r.name, "rag": r.rag, "score": r.score,
                         "reasons": list(r.reasons), "fact_id": r.fact_id}
                        for r in report.squad_table],
    }


class WeeklyReportAgent:
    def __init__(self, conn: sqlite3.Connection, provider: LLMProvider, cfg: AppConfig,
                 registry: PromptRegistry | None = None, prompt_version: str = "latest",
                 max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS) -> None:
        self.conn = conn
        self.provider = provider
        self.cfg = cfg
        self.registry = registry or PromptRegistry()
        self.prompt_version = prompt_version
        self.max_tool_calls = max_tool_calls

    def run(self, note: str = "") -> ReportResult:
        settings, llm = self.cfg.settings, self.cfg.llm
        as_of = settings.app.as_of_date

        # 1-3: deterministic. No AI involved.
        data = load_programme_data(self.conn)
        if data.is_empty:
            raise ReportError("No programme data. Load the demo dataset on Data Import.")
        bundle = compute_rule_bundle(data, as_of, self.cfg.thresholds)
        toolbox = ToolBox(data, bundle)
        try:
            prompt = render_report_prompt(as_of, note, self.prompt_version, self.registry,
                                          settings.ai.max_note_chars)
        except ValueError as exc:
            raise ReportError(str(exc)) from exc

        # 4: the agent loop (the only AI steps, bounded and recorded)
        start = time.perf_counter()
        run = AgentLoop(self.provider, toolbox, prompt.system,
                        max_tool_calls=self.max_tool_calls, max_tokens=llm.max_tokens,
                        timeout_seconds=llm.timeout_seconds).run(prompt.user)
        latency = int((time.perf_counter() - start) * 1000)

        # 5: grounding checks against only what the tools returned
        report = validate_report(run, as_of, bundle.rule_version)

        # 6: audit (always, including failures). The key is used only to redact.
        validation = report.validation
        output_id = log_analysis(self.conn, AuditEntry(
            use_case=USE_CASE, provider=self.provider.name, model=self.provider.model,
            prompt_ref=prompt.prompt_ref, prompt_sha256=prompt.prompt_sha256, pack=report.pack,
            raw_text=run.final_text, validation=validation, latency_ms=latency,
            usage=run.usage, error=report.error, status=report.status.value,
            extra=trace_for_audit(report)), secrets=[llm.api_key() or ""])

        return ReportResult(report=report, ai_output_id=output_id, provider=self.provider.name,
                            model=self.provider.model, prompt_ref=prompt.prompt_ref,
                            latency_ms=latency)


def create_report_agent(conn: sqlite3.Connection, cfg: AppConfig,
                        simulate_fault: str | None = None) -> WeeklyReportAgent:
    """Build the agent with the configured provider. With the default ``mock`` provider (or a
    simulated fault) the free demo agent plays the part; it is labelled as such in the UI."""
    use_demo = simulate_fault is not None or cfg.llm.provider == "mock"
    provider = DemoAgentProvider(fault=simulate_fault) if use_demo else get_provider(cfg.llm)
    return WeeklyReportAgent(conn, provider, cfg)
