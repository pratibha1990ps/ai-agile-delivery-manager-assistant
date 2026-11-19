"""Versioned prompt templates, stored as Markdown files outside the business logic.

Layout: ``src/adm/ai/prompts/<prompt_id>/<version>.md`` with YAML front matter and sections::

    ---
    id: raid_analyst
    version: v1
    ...
    ---
    ## system
    ## user          (may use {{placeholders}})
    ## focus.<name>  (one per analysis focus, if requires_focus)

Rules that make prompts governable:

* **Published versions are immutable.** To change wording, add ``v2.md``; the audit log records
  ``raid_analyst@v1`` plus a SHA-256 of the file, so every answer is tied to exact wording.
* **Placeholders are checked when a template loads**, so a typo fails a test, not a demo.
* **Rendering is single-pass**, so text inside the fact pack can never be re-read as a
  placeholder.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from adm.ai.models import AnalysisResponse, FactPack, Focus

PROMPTS_DIR = Path(__file__).with_name("prompts")
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
KNOWN_PLACEHOLDERS = {"question", "scope_label", "as_of", "focus_instructions", "note",
                      "fact_count", "omitted_count", "fact_pack_json", "output_schema"}
REQUIRED_META = {"id", "version", "description", "output_schema"}


class PromptError(ValueError):
    """A prompt template is missing, malformed or uses unknown placeholders."""


@dataclass(frozen=True)
class PromptTemplate:
    prompt_id: str
    version: str
    description: str
    sections: dict[str, str]
    sha256: str
    path: Path

    @property
    def ref(self) -> str:
        return f"{self.prompt_id}@{self.version}"


@dataclass(frozen=True)
class RenderedPrompt:
    system: str
    user: str
    prompt_ref: str        # e.g. "raid_analyst@v1"
    prompt_sha256: str     # hash of the template file


def _version_key(version: str) -> tuple[int, str]:
    m = re.fullmatch(r"v(\d+)", version)
    return (int(m.group(1)), version) if m else (-1, version)


def parse_template(text: str, path: Path) -> PromptTemplate:
    text = text.replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, flags=re.DOTALL)
    if not m:
        raise PromptError(f"{path.name}: missing YAML front matter")
    meta = yaml.safe_load(m.group(1)) or {}
    missing = REQUIRED_META - set(meta)
    if missing:
        raise PromptError(f"{path.name}: front matter lacks {sorted(missing)}")

    sections: dict[str, str] = {}
    for block in re.split(r"^## ", m.group(2), flags=re.MULTILINE)[1:]:
        name, _, body = block.partition("\n")
        sections[name.strip()] = body.strip()
    for required in ("system", "user"):
        if required not in sections:
            raise PromptError(f"{path.name}: missing '## {required}' section")
    if meta.get("requires_focus"):
        absent = [f.value for f in Focus if f"focus.{f.value}" not in sections]
        if absent:
            raise PromptError(f"{path.name}: missing focus sections {absent}")
    unknown = {p for s in sections.values() for p in PLACEHOLDER.findall(s)} \
        - KNOWN_PLACEHOLDERS
    if unknown:
        raise PromptError(f"{path.name}: unknown placeholders {sorted(unknown)}")
    if str(meta["version"]) != path.stem:
        raise PromptError(f"{path.name}: front matter version '{meta['version']}' does not "
                          "match the file name")

    return PromptTemplate(
        prompt_id=str(meta["id"]), version=str(meta["version"]),
        description=str(meta["description"]), sections=sections,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(), path=path,
    )


class PromptRegistry:
    def __init__(self, root: Path = PROMPTS_DIR) -> None:
        self.root = root

    def versions(self, prompt_id: str) -> list[str]:
        folder = self.root / prompt_id
        if not folder.is_dir():
            raise PromptError(f"No prompts named '{prompt_id}'.")
        return sorted((p.stem for p in folder.glob("v*.md")), key=_version_key)

    def get(self, prompt_id: str, version: str = "latest") -> PromptTemplate:
        available = self.versions(prompt_id)
        if not available:
            raise PromptError(f"No versions of '{prompt_id}'.")
        chosen = available[-1] if version == "latest" else version
        if chosen not in available:
            raise PromptError(f"'{prompt_id}' has no version '{version}'; available: "
                              f"{', '.join(available)}.")
        path = self.root / prompt_id / f"{chosen}.md"
        return parse_template(path.read_text(encoding="utf-8"), path)


def _fill(text: str, values: dict[str, str]) -> str:
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], text)   # single pass


def render(template: PromptTemplate, pack: FactPack) -> RenderedPrompt:
    req = pack.request
    facts_json = json.dumps([f.model_dump(mode="json") for f in pack.facts],
                            sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    values = {
        "question": req.question,
        "scope_label": "whole programme" if req.is_programme else f"squad {req.scope}",
        "as_of": pack.as_of.isoformat(),
        "focus_instructions": template.sections.get(f"focus.{req.focus.value}", ""),
        "note": req.note or "(none)",
        "fact_count": str(len(pack.facts)),
        "omitted_count": str(pack.omitted_count),
        "fact_pack_json": facts_json,
        "output_schema": json.dumps(AnalysisResponse.json_schema(), sort_keys=True,
                                    separators=(",", ":")),
    }
    return RenderedPrompt(
        system=_fill(template.sections["system"], values),
        user=_fill(template.sections["user"], values),
        prompt_ref=template.ref, prompt_sha256=template.sha256,
    )
