"""Typed configuration loading.

Three YAML files live in ``config/``:
- ``settings.yaml``   application and database settings
- ``thresholds.yaml`` deterministic RAG cut-offs (versioned)
- ``llm.yaml``        LLM provider settings (optional; falls back to the example, then to mock)

The LLM config is deliberately separate so the provider can change without touching the app.
"""

from __future__ import annotations

import os
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"


class Direction(StrEnum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"


class MetricThreshold(BaseModel):
    direction: Direction
    green: float
    amber: float

    @model_validator(mode="after")
    def _check_order(self) -> MetricThreshold:
        if self.direction is Direction.HIGHER_IS_BETTER and self.green < self.amber:
            raise ValueError("higher_is_better requires green >= amber")
        if self.direction is Direction.LOWER_IS_BETTER and self.green > self.amber:
            raise ValueError("lower_is_better requires green <= amber")
        return self


class WindowSettings(BaseModel):
    say_do_sprints: int = Field(default=3, ge=1)
    velocity_sprints: int = Field(default=5, ge=3)


class RaidThresholds(BaseModel):
    stale_after_days: int = Field(ge=1)
    high_risk_score: int = Field(ge=1, le=25)
    issue_ageing_days: int = Field(default=30, ge=1)
    duplicate_similarity: float = Field(default=0.6, gt=0, le=1)


class DependencyThresholds(BaseModel):
    late_tolerance_days: int = Field(ge=0)
    unconfirmed_warning_days: int = Field(default=14, ge=0)


class MilestoneThresholds(BaseModel):
    amber_slip_days: int = Field(default=1, ge=0)
    red_slip_days: int = Field(default=15, ge=1)

    @model_validator(mode="after")
    def _order(self) -> MilestoneThresholds:
        if self.red_slip_days <= self.amber_slip_days:
            raise ValueError("red_slip_days must be greater than amber_slip_days")
        return self


class CompositeThresholds(BaseModel):
    green: float = Field(default=0.80, ge=0, le=1)
    amber: float = Field(default=0.50, ge=0, le=1)
    red_if_owns_red_milestone: bool = True

    @model_validator(mode="after")
    def _order(self) -> CompositeThresholds:
        if self.green < self.amber:
            raise ValueError("composite green must be >= amber")
        return self


class Thresholds(BaseModel):
    version: str
    windows: WindowSettings = WindowSettings()
    metrics: dict[str, MetricThreshold]
    raid: RaidThresholds
    dependencies: DependencyThresholds
    milestones: MilestoneThresholds = MilestoneThresholds()
    health_weights: dict[str, float]
    composite: CompositeThresholds = CompositeThresholds()

    @model_validator(mode="after")
    def _check_weights(self) -> Thresholds:
        unknown = set(self.health_weights) - set(self.metrics)
        if unknown:
            raise ValueError(f"health_weights reference unknown metrics: {sorted(unknown)}")
        total = sum(self.health_weights.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"health_weights must sum to 1.0 (got {total:.3f})")
        return self


class AppSettings(BaseModel):
    programme_name: str
    programme_description: str = ""
    as_of_date: date


class DatabaseSettings(BaseModel):
    path: str

    def resolved_path(self, root: Path = PROJECT_ROOT) -> Path:
        p = Path(self.path)
        return p if p.is_absolute() else root / p


class Settings(BaseModel):
    app: AppSettings
    database: DatabaseSettings


class LLMSettings(BaseModel):
    provider: Literal["mock", "openai_compatible", "anthropic"] = "mock"
    model: str = "mock-grounded-v1"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1500, ge=1)
    base_url: str | None = None
    api_key_env: str | None = None

    def api_key(self) -> str | None:
        """Read the key from the environment at call time; never stored in config."""
        return os.environ.get(self.api_key_env) if self.api_key_env else None


class AppConfig(BaseModel):
    settings: Settings
    thresholds: Thresholds
    llm: LLMSettings
    llm_source: str  # which file the LLM settings came from, shown in the UI


def _read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_config(config_dir: Path | None = None) -> AppConfig:
    config_dir = Path(config_dir or os.environ.get("ADM_CONFIG_DIR") or DEFAULT_CONFIG_DIR)

    settings = Settings.model_validate(_read_yaml(config_dir / "settings.yaml"))
    thresholds = Thresholds.model_validate(_read_yaml(config_dir / "thresholds.yaml"))

    llm_source = "built-in default (mock)"
    llm_data: dict = {}
    for name in ("llm.yaml", "llm.yaml.example"):
        candidate = config_dir / name
        if candidate.exists():
            llm_data = _read_yaml(candidate)
            llm_source = name
            break
    llm = LLMSettings.model_validate(llm_data)

    return AppConfig(settings=settings, thresholds=thresholds, llm=llm, llm_source=llm_source)
