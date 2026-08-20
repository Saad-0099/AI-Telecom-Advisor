"""
Phase 9 — sidebar navigation.

Navigation is grouped into four sections rather than presented as one long
radio list. Selection is held in session_state and rendered as buttons, so
the active page carries an accent border instead of a radio circle.

The status card sits at the FOOT of the sidebar deliberately: it is
reference information, not navigation, and should not compete with it.
Every line is READ from the backend — nothing here asserts that a service
is online without checking.
"""

from __future__ import annotations

import streamlit as st

from components import backend as be
from components.ui import status_row

# (group label, [(page key, icon, label), ...])
NAV = [
    ("Analytics", [
        ("overview",  "◱", "Executive Overview"),
        ("churn",     "◲", "Churn Analytics"),
        ("segments",  "◈", "Customer Segments"),
        ("cohorts",   "◷", "Cohort Analysis"),
        ("revenue",   "◆", "Revenue & Risk"),
    ]),
    ("AI & Decisions", [
        ("advisor",   "✦", "AI Business Advisor"),
        ("scenarios", "◎", "What-If Lab"),
        ("risk",      "◉", "Churn Risk Model"),
    
    ]),
    ("Reporting", [
        ("report",    "▤", "Executive Report"),
    ]),
    ("System", [
        ("quality",   "✓", "Data Quality"),
    ]),
]

DEFAULT_PAGE = "overview"


def render() -> str:
    """Draw the sidebar and return the selected page key."""
    if "page" not in st.session_state:
        st.session_state.page = DEFAULT_PAGE

    with st.sidebar:
        st.markdown(
            '<div class="brand">'
            '<div class="n">◆ AI Telecom Advisor</div>'
            '<div class="s">Decision Intelligence Platform</div>'
            '</div>', unsafe_allow_html=True)

        for group, items in NAV:
            st.markdown(f'<div class="navgroup">{group}</div>',
                        unsafe_allow_html=True)
            for key, icon, label in items:
                active = st.session_state.page == key
                # type="primary" is what the CSS hooks to for the active
                # state — Streamlit gives no other selected-button styling.
                if st.button(f"{icon}   {label}", key=f"nav_{key}",
                             type="primary" if active else "secondary",
                             use_container_width=True):
                    st.session_state.page = key
                    st.rerun()

        _reports_block()
        _status_block()

    return st.session_state.page


def _reports_block() -> None:
    """Generated reports live in session_state, so they survive page
    changes. Surfacing them here keeps them reachable from anywhere."""
    reports = st.session_state.get("reports", [])
    if not reports:
        return
    st.markdown('<div class="navgroup">Ready to download</div>',
                unsafe_allow_html=True)
    for i, (data, name) in enumerate(reversed(reports[-3:])):
        short = name.replace("portfolio_risk_report_", "").replace(".pdf", "")
        st.download_button(f"↓  {short}", data, file_name=name,
                           mime="application/pdf", key=f"sbdl_{i}",
                           use_container_width=True)


def _status_block() -> None:
    """Compact, subordinate, and honest about what it does not know."""
    try:
        rows = be.system_status()
    except Exception as exc:
        rows = [{"label": "Backend unreachable", "state": "off",
                 "detail": str(exc)[:60]}]

    html = '<div class="statuscard"><div class="t">System status</div>'
    for s in rows:
        html += status_row(s["label"], s["state"], s.get("detail", ""))
    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)

    st.markdown(
        '<div class="statuscard" style="margin-top:.7rem">'
        '<div class="t">Provenance</div>'
        '<div style="font-size:.72rem;line-height:2">'
        '<span class="badge badge-observed">Observed</span> '
        '<span style="color:#5C6779">measured</span><br>'
        '<span class="badge badge-simulated">Simulated</span> '
        '<span style="color:#5C6779">generated</span><br>'
        '<span class="badge badge-projected">Projected</span> '
        '<span style="color:#5C6779">assumed</span>'
        '</div></div>', unsafe_allow_html=True)