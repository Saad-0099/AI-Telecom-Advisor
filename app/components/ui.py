"""
Phase 9 — UI primitives.

Small HTML builders shared by every page, so a card looks the same
wherever it appears and a provenance badge cannot drift between pages.

These emit strings and call Streamlit. They never fetch data and never
compute.
"""

from __future__ import annotations

import streamlit as st

from components.styles import PALETTE

ACCENT = {
    "observed": PALETTE["observed"],
    "simulated": PALETTE["simulated"],
    "projected": PALETTE["projected"],
    "accent": PALETTE["accent"],
    "success": PALETTE["success"],
    "warning": PALETTE["warning"],
    "danger": PALETTE["danger"],
    "muted": PALETTE["muted"],
}


# ==========================================================================
def badge(kind: str, label: str | None = None) -> str:
    """kind: observed | simulated | projected | pass | fail"""
    return f'<span class="badge badge-{kind}">{label or kind}</span>'


def page_header(title: str, subtitle: str = "",
                origin: str = "observed") -> None:
    """Consistent header with the provenance badge on the right.

    Every page states its data origin here so a reader never has to infer
    whether a figure was measured, generated, or assumed.
    """
    st.markdown(
        f'<div class="pagehead"><div>'
        f'<div class="t">{title}</div>'
        f'<div class="s">{subtitle}</div></div>'
        f'<div>{badge(origin)}</div></div>',
        unsafe_allow_html=True)


def section(label: str) -> None:
    st.markdown(f'<div class="section">{label}</div>', unsafe_allow_html=True)


def notice(kind: str, text: str) -> None:
    """kind: info | simulated | projected"""
    st.markdown(f'<div class="notice notice-{kind}">{text}</div>',
                unsafe_allow_html=True)


# ==========================================================================
def kpi(label: str, value: str, sub: str = "", icon: str = "",
        accent: str = "accent", delay: int = 1) -> None:
    st.markdown(
        f'<div class="card kpi rise d{delay}" '
        f'style="--a:{ACCENT.get(accent, accent)}">'
        f'<div class="row"><span class="lab">{label}</span>'
        f'<span class="ico">{icon}</span></div>'
        f'<div class="val">{value}</div>'
        f'<div class="sub">{sub}</div></div>',
        unsafe_allow_html=True)


def kpi_row(tiles: list[dict]) -> None:
    """tiles: [{label, value, sub, icon, accent}, ...]"""
    cols = st.columns(len(tiles), gap="medium")
    for i, t in enumerate(tiles):
        with cols[i]:
            kpi(t["label"], t["value"], t.get("sub", ""), t.get("icon", ""),
                t.get("accent", "accent"), delay=i + 1)


# ==========================================================================
def card_open(title: str = "", subtitle: str = "",
              flat: bool = False) -> None:
    """Open a card. MUST be paired with card_close().

    Streamlit cannot wrap arbitrary widgets in custom HTML, so a card is
    opened and closed around st.plotly_chart or similar. Forgetting the
    close leaves an unterminated div and the layout collapses.
    """
    cls = "card card-flat fade" if flat else "card fade"
    head = ""
    if title:
        head = (f'<div class="card-head"><div class="ct">{title}</div>'
                + (f'<div class="cs">{subtitle}</div>' if subtitle else "")
                + '</div>')
    st.markdown(f'<div class="{cls}">{head}', unsafe_allow_html=True)


def card_close() -> None:
    st.markdown('</div>', unsafe_allow_html=True)


# ==========================================================================
def ai_block(kind: str, head: str, body: str, icon: str = "") -> None:
    """kind: answer | evidence | action | caveat"""
    st.markdown(
        f'<div class="ai ai-{kind} rise"><div class="h">{icon} {head}</div>'
        f'<div class="b">{body}</div></div>', unsafe_allow_html=True)


def verification(valid: bool, checked: int, context: str) -> None:
    """The claim no ordinary LLM demo can make: every figure was checked."""
    st.markdown(
        f'<div class="verify{"" if valid else " bad"}">'
        f'{"✓" if valid else "✕"} {checked} figures '
        f'{f"verified against {context}" if valid else "— VALIDATION FAILED"}'
        f'</div>', unsafe_allow_html=True)


def status_row(label: str, state: str, detail: str = "") -> str:
    """state: on | warn | off"""
    d = f'<div class="d">{detail}</div>' if detail else ""
    return (f'<div class="srow"><span class="dot dot-{state}"></span>'
            f'<div class="txt">{label}{d}</div></div>')


def compare_strip(left: dict, right: dict, accent: str = "projected") -> None:
    """Current vs scenario, side by side."""
    c = ACCENT.get(accent, accent)
    st.markdown(
        f'<div class="card card-flat fade" style="padding:0">'
        f'<div class="compare">'
        f'<div class="side"><div class="lab" style="color:#6E7A8E">'
        f'{left["label"]}</div>'
        f'<div class="val" style="color:#A9B4C6">{left["value"]}</div>'
        f'<div class="sub">{left.get("sub", "")}</div></div>'
        f'<div class="arrow">→</div>'
        f'<div class="side"><div class="lab" style="color:{c}">'
        f'{right["label"]}</div>'
        f'<div class="val" style="color:{c}">{right["value"]}</div>'
        f'<div class="sub">{right.get("sub", "")}</div></div>'
        f'</div></div>', unsafe_allow_html=True)