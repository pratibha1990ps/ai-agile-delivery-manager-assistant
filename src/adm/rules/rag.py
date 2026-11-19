"""Deterministic RAG classification. The only place a status is assigned from a value."""

from __future__ import annotations

from enum import StrEnum

from adm.config import Direction, MetricThreshold


class RAG(StrEnum):
    GREEN = "GREEN"
    AMBER = "AMBER"
    RED = "RED"


def classify(value: float, threshold: MetricThreshold) -> RAG:
    """Classify a metric value. Boundaries are inclusive towards the better status."""
    if threshold.direction is Direction.HIGHER_IS_BETTER:
        if value >= threshold.green:
            return RAG.GREEN
        return RAG.AMBER if value >= threshold.amber else RAG.RED
    if value <= threshold.green:
        return RAG.GREEN
    return RAG.AMBER if value <= threshold.amber else RAG.RED
