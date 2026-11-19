import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from adm.config import DEFAULT_CONFIG_DIR, MetricThreshold, Thresholds, load_config


def test_project_config_loads():
    cfg = load_config()
    assert cfg.settings.app.programme_name == "Project Meridian"
    assert isinstance(cfg.settings.app.as_of_date, date)
    assert cfg.thresholds.version == "1.2"
    assert "say_do_ratio" in cfg.thresholds.metrics


def test_llm_defaults_to_mock_from_example(tmp_path: Path):
    for name in ("settings.yaml", "thresholds.yaml", "llm.yaml.example"):
        shutil.copy(DEFAULT_CONFIG_DIR / name, tmp_path / name)
    cfg = load_config(tmp_path)
    assert cfg.llm.provider == "mock"
    assert cfg.llm_source == "llm.yaml.example"


def test_llm_falls_back_to_builtin_mock(tmp_path: Path):
    for name in ("settings.yaml", "thresholds.yaml"):
        shutil.copy(DEFAULT_CONFIG_DIR / name, tmp_path / name)
    cfg = load_config(tmp_path)
    assert cfg.llm.provider == "mock"
    assert cfg.llm_source.startswith("built-in")


def test_llm_yaml_overrides_example(tmp_path: Path):
    for name in ("settings.yaml", "thresholds.yaml", "llm.yaml.example"):
        shutil.copy(DEFAULT_CONFIG_DIR / name, tmp_path / name)
    (tmp_path / "llm.yaml").write_text(
        "provider: openai_compatible\nmodel: llama3.1\nbase_url: http://localhost:11434/v1\n",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.llm.provider == "openai_compatible"
    assert cfg.llm_source == "llm.yaml"


def test_api_key_read_from_environment(monkeypatch):
    from adm.config import LLMSettings

    monkeypatch.setenv("ADM_TEST_KEY", "secret")
    assert LLMSettings(api_key_env="ADM_TEST_KEY").api_key() == "secret"
    assert LLMSettings().api_key() is None


def test_unknown_provider_rejected():
    from adm.config import LLMSettings

    with pytest.raises(ValidationError):
        LLMSettings(provider="something_else")


@pytest.mark.parametrize(
    "direction,green,amber",
    [("higher_is_better", 0.5, 0.8), ("lower_is_better", 0.3, 0.1)],
)
def test_inverted_thresholds_rejected(direction, green, amber):
    with pytest.raises(ValidationError):
        MetricThreshold(direction=direction, green=green, amber=amber)


def test_weights_must_sum_to_one():
    data = yaml.safe_load((DEFAULT_CONFIG_DIR / "thresholds.yaml").read_text(encoding="utf-8"))
    data["health_weights"]["say_do_ratio"] = 0.9
    with pytest.raises(ValidationError, match="sum to 1.0"):
        Thresholds.model_validate(data)


def test_weights_must_reference_known_metrics():
    data = yaml.safe_load((DEFAULT_CONFIG_DIR / "thresholds.yaml").read_text(encoding="utf-8"))
    data["health_weights"] = {"not_a_metric": 1.0}
    with pytest.raises(ValidationError, match="unknown metrics"):
        Thresholds.model_validate(data)
