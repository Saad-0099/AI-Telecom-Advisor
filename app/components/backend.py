"""
Phase 9 — backend adapter.

The ONLY module that imports from src/. Every page goes through here, so
there is exactly one place where the UI touches business logic and no page
can accidentally reimplement a calculation.

NOTHING IN THIS FILE COMPUTES ANYTHING. It fetches, caches, and reports
health. If a page needs a number the backend does not produce, the honest
answer is to say so on screen rather than derive it in the frontend.
"""

from __future__ import annotations

import pathlib
import sys

import streamlit as st

# src/ is a sibling of app/. Same shim the test suites use.
_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import chart_specs                      # noqa: E402
import charts as charts_mod             # noqa: E402
import metrics                          # noqa: E402
import recommend                        # noqa: E402
import rules as rules_mod               # noqa: E402
import scenario as scenario_mod         # noqa: E402
import text_to_sql                      # noqa: E402

CACHE_TTL = 300


# ==========================================================================
# Metrics
# ==========================================================================
@st.cache_data(ttl=CACHE_TTL)
def kpi() -> dict:
    return metrics.kpi_summary()["data"]


@st.cache_data(ttl=CACHE_TTL)
def metric(name: str, **kwargs) -> list[dict]:
    fn = metrics.REGISTRY.get(name)
    if fn is None:
        raise KeyError(f"metric '{name}' is not registered")
    payload = fn(**kwargs) if kwargs else fn()
    data = payload["data"]
    return data if isinstance(data, list) else [data]


@st.cache_data(ttl=CACHE_TTL)
def snapshot_meta() -> dict:
    return metrics.SNAPSHOT_META


def caveats() -> list[str]:
    """Limitations worth surfacing in the UI, taken from the backend meta."""
    m = metrics.SNAPSHOT_META
    out = [
        "The source data is a single snapshot with no dates. Nothing here "
        "describes change over time, and no figure is a forecast.",
        m.get("cohort_note", ""),
        "State-level rates rest on ~65 customers each and carry several "
        "points of sampling noise.",
    ]
    return [c for c in out if c]


# ==========================================================================
# Charts — rendered by the backend, themed here
# ==========================================================================
def chart_list() -> list[dict]:
    return chart_specs.list_charts()


def chart_spec(name: str) -> dict:
    return chart_specs.get_spec(name)


@st.cache_data(ttl=CACHE_TTL)
def chart_rows(name: str) -> list[dict]:
    _, df = charts_mod.chart_data(name)
    return df.to_dict(orient="records")


def chart_figure(name: str, height: int = 380):
    """Build a Plotly figure through the backend spec, then theme it.

    Deliberately rebuilds from chart_data + the spec rather than parsing
    render_plotly's HTML, so the UI and the PDF stay driven by one spec.
    """
    import plotly.graph_objects as go
    from components.styles import PALETTE, plotly_theme

    spec, df = charts_mod.chart_data(name)
    kind = spec.get("kind_override", spec["kind"])
    x, y = spec["x"], spec["y"]
    simulated = bool(spec.get("simulated"))

    fig = go.Figure()
    if df.empty:
        fig.add_annotation(text="no data", showarrow=False)
        return plotly_theme(fig, height)

    if kind == "grouped_bar":
        series = spec["series"]
        colours = [PALETTE["accent"], PALETTE["danger"],
                   PALETTE["warning"], PALETTE["success"]]
        for i, level in enumerate(dict.fromkeys(df[series])):
            sub = df[df[series] == level]
            fig.add_bar(x=sub[x].astype(str), y=sub[y], name=str(level),
                        marker_color=colours[i % len(colours)])
        fig.update_layout(barmode="group", showlegend=True)

    elif kind == "barh":
        fig.add_bar(x=df[y], y=df[x].astype(str), orientation="h",
                    marker_color=_bar_colours(spec, df, simulated))
        fig.update_yaxes(autorange="reversed")

    elif kind == "line":
        fig.add_scatter(
            x=df[x].astype(str), y=df[y], mode="lines+markers",
            line=dict(width=2.5,
                      color=PALETTE["simulated"] if simulated
                      else PALETTE["accent"]),
            marker=dict(size=7))
        # Zero-based for simulated series: autoscaling a flat line turns
        # sampling noise into what looks like a trend.
        if simulated:
            fig.update_yaxes(range=[0, max(df[y]) * 1.25])

    else:
        fig.add_bar(x=df[x].astype(str), y=df[y],
                    marker_color=_bar_colours(spec, df, simulated))

    if spec.get("y_max"):
        fig.update_yaxes(range=[0, spec["y_max"]])

    ref = spec.get("reference_line")
    if ref:
        fig.add_hline(y=ref["value"], line_dash="dash",
                      line_color=PALETTE["muted"],
                      annotation_text=ref["label"],
                      annotation_position="top right")

    fig.update_layout(
        title=dict(text=f"<b>{spec['title']}</b>", x=0, xanchor="left"),
        xaxis_title=spec.get("x_label", ""),
        yaxis_title=spec.get("y_label", ""),
    )
    return plotly_theme(fig, height)


def _bar_colours(spec: dict, df, simulated: bool) -> list[str]:
    """Mirrors charts._bar_colors so the UI and PDF highlight identically."""
    from components.styles import PALETTE
    if simulated:
        return [PALETTE["simulated"]] * len(df)

    x, y = spec["x"], spec["y"]
    col, floor = spec.get("sample_size_col"), spec.get("min_sample")
    out = []
    for _, row in df.iterrows():
        # Too few customers to read as evidence: mute it.
        if col and floor is not None and col in df.columns and row[col] < floor:
            out.append("#3A4457")
            continue
        danger = False
        if "highlight_from_x" in spec:
            try:
                danger = float(row[x]) >= spec["highlight_from_x"]
            except (TypeError, ValueError):
                danger = False
        if "highlight_labels" in spec:
            danger = danger or row[x] in spec["highlight_labels"]
        if "highlight_above_y" in spec:
            danger = danger or float(row[y]) >= spec["highlight_above_y"]
        out.append(PALETTE["danger"] if danger else PALETTE["accent"])
    return out


def explain_chart(name: str) -> dict:
    """LLM explanation. NOT cached — it costs quota, so the user asks."""
    return charts_mod.explain(name)


# ==========================================================================
# Recommendations
# ==========================================================================
@st.cache_data(ttl=CACHE_TTL)
def recommendations() -> dict:
    return recommend.recommendations_payload()


@st.cache_data(ttl=CACHE_TTL)
def rule_customers(rule_id: str, limit: int = 500) -> list[dict]:
    return recommend.customers_for_rule(rule_id, limit).to_dict("records")


def economics() -> dict:
    return rules_mod.ECONOMICS


# ==========================================================================
# Scenarios
# ==========================================================================
def scenario_levers() -> list[dict]:
    return scenario_mod.list_levers()


@st.cache_data(ttl=CACHE_TTL)
def run_scenario(lever: str, pct: float) -> dict:
    return scenario_mod.run_scenario(lever, pct)


@st.cache_data(ttl=CACHE_TTL)
def compare_levers(pct: float) -> dict:
    return scenario_mod.compare_levers(pct)


def efficacy() -> dict:
    return scenario_mod.EFFICACY


# ==========================================================================
# Ask
# ==========================================================================
def ask(question: str) -> dict:
    """Full text-to-SQL pipeline. Not cached: costs quota per call."""
    return text_to_sql.ask(question)


# ==========================================================================
# System status — reported, never asserted
# ==========================================================================
def system_status() -> list[dict]:
    """Real checks. Nothing is claimed 'online' without verifying it."""
    out = []

    try:
        n = metrics.kpi_summary()["data"]["total_customers"]
        out.append({"label": f"Database · {n:,} customers", "state": "on"})
    except Exception as exc:
        out.append({"label": "Database unavailable", "state": "off",
                    "detail": str(exc)[:80]})

    try:
        import llm_provider
        st_ = llm_provider.provider_status()
        if st_["available"]:
            label = f"AI · {st_['detail']}"
            state = "on" if st_["provider"] != "mock" else "warn"
            if st_["provider"] == "mock":
                label = "AI · mock provider (no live model)"
        else:
            label, state = f"AI unavailable · {st_['detail'][:40]}", "off"
        out.append({"label": label, "state": state})
    except Exception as exc:
        out.append({"label": "AI provider error", "state": "off",
                    "detail": str(exc)[:80]})

    try:
        rec = recommend.reconcile()
        ok = all(v["match"] for v in rec.values())
        out.append({
            "label": ("Reconciliation passing" if ok
                      else "Reconciliation FAILING"),
            "state": "on" if ok else "off"})
    except Exception:
        out.append({"label": "Reconciliation unknown", "state": "warn"})

    try:
        has_sim = bool(metrics.sim_monthly_portfolio()["data"])
        out.append({"label": "Simulated panel loaded" if has_sim
                    else "Simulated panel absent",
                    "state": "warn" if has_sim else "off"})
    except Exception:
        out.append({"label": "Simulated panel absent", "state": "off"})

    return out


@st.cache_data(ttl=CACHE_TTL)
def reconciliation() -> dict:
    return recommend.reconcile()


def has_simulated() -> bool:
    try:
        return bool(metrics.sim_monthly_portfolio()["data"])
    except Exception:
        return False

# ==========================================================================
# Report
# ==========================================================================
def generate_report(include_charts: bool = True) -> tuple[bytes, str]:
    """Build the PDF in memory and return (bytes, filename).

    NOT cached. A full report is ~15 LLM calls (5 section narrations plus
    10 chart explanations) against a 100k daily token cap, so it runs only
    when the user asks.
    """
    import tempfile
    from datetime import datetime

    import report_content
    import report_pdf

    content = report_content.build(include_charts=include_charts)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        path = fh.name
    report_pdf.render(content, path)

    data = pathlib.Path(path).read_bytes()
    pathlib.Path(path).unlink(missing_ok=True)

    name = f"portfolio_risk_report_{datetime.now():%Y%m%d_%H%M}.pdf"
    return data, name

# ==========================================================================
# Report
# ==========================================================================
def generate_report(include_charts: bool = True) -> tuple[bytes, str]:
    """Build the PDF in memory and return (bytes, filename).

    NOT cached. A full report is ~15 LLM calls (5 section narrations plus
    10 chart explanations) against a limited daily token cap, so it runs
    only when the user asks.
    """
    import tempfile
    from datetime import datetime

    import report_content
    import report_pdf

    content = report_content.build(include_charts=include_charts)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        path = fh.name
    report_pdf.render(content, path)

    data = pathlib.Path(path).read_bytes()
    pathlib.Path(path).unlink(missing_ok=True)
    name = f"portfolio_risk_report_{datetime.now():%Y%m%d_%H%M}.pdf"
    return data, name