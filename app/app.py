"""
Telecom Decision Intelligence Platform — Streamlit interface.

Run:  python run.py ui        (or: streamlit run app/app.py)

Presentation only. Every number comes through components.backend, the
single boundary to the business logic in src/. No page computes anything.

PROVENANCE IS THE ORGANISING PRINCIPLE of this interface, as throughout
the project:

    blue    OBSERVED    measured from the source data
    amber   SIMULATED   generated history; structure only, churn is flat
    violet  PROJECTED   hypothetical, resting on stated assumptions

A reader who cannot tell those apart at a glance is being misled by
omission, so the distinction is carried by badge, colour and notice on
every page rather than by a footnote.
"""

from __future__ import annotations

import pathlib
import sys

import streamlit as st

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# set_page_config must be the first Streamlit call in the script.
st.set_page_config(page_title="AI Telecom Advisor", page_icon="◆",
                   layout="wide", initial_sidebar_state="expanded")

from components import navigation, pages_ai, pages_analytics  # noqa: E402
from components.styles import inject                          # noqa: E402

st.markdown(inject(), unsafe_allow_html=True)

ROUTES = {
    "overview":  pages_analytics.overview,
    "churn":     pages_analytics.churn,
    "segments":  pages_analytics.segments,
    "cohorts":   pages_analytics.cohorts,
    "revenue":   pages_analytics.revenue,
    "advisor":   pages_ai.advisor,
    "scenarios": pages_ai.scenario_lab,
    "report":    pages_ai.report_builder,
    "quality":   pages_ai.quality,
    "risk":      pages_ai.risk_model,
}


def main() -> None:
    try:
        page = navigation.render()
    except Exception as exc:
        st.error(f"The interface could not start — {exc}")
        st.info("Run `python run.py all` from the project root to build the "
                "database and views, then reload.")
        return

    try:
        ROUTES[page]()
    except Exception as exc:
        # Name the cause rather than showing a blank screen or, worse,
        # partial figures that look complete.
        st.error(f"This page could not be rendered — "
                 f"{type(exc).__name__}: {exc}")
        with st.expander("Technical details"):
            import traceback
            st.code(traceback.format_exc())
        st.info("If this mentions a missing table or view, run "
                "`python run.py all` to rebuild.")


if __name__ == "__main__":
    main()