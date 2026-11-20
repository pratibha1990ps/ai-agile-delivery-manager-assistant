"""Render the versioned weekly-report prompt for the agent (A5).

The wording lives in ``src/adm/ai/prompts/weekly_report/<version>.md`` and is managed by the
same ``PromptRegistry`` as the RAID Analyst prompt: published versions are immutable, the audit
trail records ``weekly_report@v1`` plus a SHA-256 of the file.

* ``system``  -> the agent's standing instructions (how to work, the rules, the reply format).
* ``user``    -> the goal for this run (becomes the loop's GOAL).

The report schema inside the prompt is generated from ``ReportAnswer``, the same model that
validates the answer, so the instructions and the checker cannot drift apart. Notes from the
user go only into the ``user`` part and are length-limited; they cannot alter the rules.
"""

from __future__ import annotations

import json
from datetime import date

from adm.agent.report import ReportAnswer
from adm.ai.prompt_registry import PLACEHOLDER, PromptRegistry, RenderedPrompt

PROMPT_ID = "weekly_report"
DEFAULT_MAX_NOTE_CHARS = 500


def render_report_prompt(as_of: date, note: str = "", version: str = "latest",
                         registry: PromptRegistry | None = None,
                         max_note_chars: int = DEFAULT_MAX_NOTE_CHARS) -> RenderedPrompt:
    note = (note or "").strip()
    if len(note) > max_note_chars:
        raise ValueError(f"The note is {len(note)} characters; the limit is {max_note_chars}.")
    template = (registry or PromptRegistry()).get(PROMPT_ID, version)
    values = {
        "as_of": as_of.isoformat(),
        "note": note or "(none)",
        "report_schema": json.dumps(ReportAnswer.model_json_schema(), sort_keys=True,
                                    separators=(",", ":")),
    }

    def fill(text: str) -> str:                       # single pass: filled text is never re-read
        return PLACEHOLDER.sub(lambda m: values[m.group(1)], text)

    return RenderedPrompt(system=fill(template.sections["system"]),
                          user=fill(template.sections["user"]),
                          prompt_ref=template.ref, prompt_sha256=template.sha256)
