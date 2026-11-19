import pytest

from adm.config import MetricThreshold
from adm.rules.rag import RAG, classify

HIGHER = MetricThreshold(direction="higher_is_better", green=0.85, amber=0.70)
LOWER = MetricThreshold(direction="lower_is_better", green=0.10, amber=0.20)


@pytest.mark.parametrize(
    "value,expected",
    [(0.95, RAG.GREEN), (0.85, RAG.GREEN), (0.849, RAG.AMBER), (0.70, RAG.AMBER),
     (0.699, RAG.RED), (0.0, RAG.RED)],
)
def test_higher_is_better_boundaries(value, expected):
    assert classify(value, HIGHER) is expected


@pytest.mark.parametrize(
    "value,expected",
    [(0.0, RAG.GREEN), (0.10, RAG.GREEN), (0.101, RAG.AMBER), (0.20, RAG.AMBER),
     (0.201, RAG.RED), (1.5, RAG.RED)],
)
def test_lower_is_better_boundaries(value, expected):
    assert classify(value, LOWER) is expected
