"""Shared RAG presentation helpers for Streamlit pages."""

from __future__ import annotations

import pandas as pd

from adm.rules.rag import RAG

SOLID = {RAG.GREEN: "#1e8e3e", RAG.AMBER: "#e37400", RAG.RED: "#d93025"}
CELL = {
    RAG.GREEN: "background-color: #e6f4ea; color: #0d652d",
    RAG.AMBER: "background-color: #fef7e0; color: #8a4b00",
    RAG.RED: "background-color: #fce8e6; color: #a50e0e",
}
ICON = {RAG.GREEN: "🟢", RAG.AMBER: "🟠", RAG.RED: "🔴"}


def as_rag(value: object) -> RAG | None:
    if isinstance(value, RAG):
        return value
    try:
        return RAG(str(value).split()[-1]) if value else None
    except ValueError:
        return None


def label(rag: RAG | None) -> str:
    return f"{ICON[rag]} {rag.value}" if rag else "n/a"


def badge(rag: RAG | None, text: str | None = None, size: str = "1rem") -> str:
    if rag is None:
        return f"<span>{text or 'n/a'}</span>"
    return (
        f"<span style='background:{SOLID[rag]};color:#fff;padding:0.15rem 0.6rem;"
        f"border-radius:0.4rem;font-weight:600;font-size:{size}'>{text or rag.value}</span>"
    )


def style_by_rag(display: pd.DataFrame, rags: pd.DataFrame) -> pd.io.formats.style.Styler:
    """Colour each cell of `display` using the RAG in the same position of `rags`."""

    def _apply(_: pd.DataFrame) -> pd.DataFrame:
        return rags.apply(lambda col: col.map(lambda r: CELL.get(as_rag(r), "")))

    return display.style.apply(_apply, axis=None)
