"""
Phase 3 — chart rendering.

Two renderers, one specification:

    chart_specs.py  →  render_matplotlib()  →  PNG bytes   (report, PDF)
                    →  render_plotly()      →  HTML string (browser, Streamlit)

Charts are generated from the database at request time, never cached to
disk. A cached PNG is a chart that silently disagrees with the API the
moment a threshold changes; regenerating costs well under a second on
3,333 rows, so there is nothing to optimise away.

export_all() materialises PNGs on demand for the Phase 7 report or slides.
Exported files carry a footer stamp recording the thresholds in force, so a
stray PNG found later can be checked rather than assumed current.

Run:  python run.py export
"""

from __future__ import annotations

import io
from typing import Any

import pandas as pd

import chart_specs as CS
import metrics


# ==========================================================================
# Data preparation — shared by both renderers so they cannot diverge
# ==========================================================================
def chart_data(name: str) -> tuple[dict, pd.DataFrame]:
    """Fetch and prepare the rows for a chart. Returns (spec, dataframe)."""
    spec = CS.get_spec(name)
    fn = metrics.REGISTRY[spec["metric"]]
    payload = fn(**spec.get("metric_kwargs", {}))

    rows = payload["data"]
    df = pd.DataFrame(rows)
    if df.empty:
        return spec, df

    if spec.get("sort_by") and spec["sort_by"] in df.columns:
        df = df.sort_values(spec["sort_by"],
                            ascending=not spec.get("sort_desc", False))
    return spec, df.reset_index(drop=True)


def _low_sample(spec: dict, df: pd.DataFrame) -> list[bool]:
    """Which rows rest on too few customers to be read as evidence.

    A 100% churn rate on 2 customers is noise, and a chart that draws it
    the same as a 45.8% rate on 166 customers is lying by omission.
    """
    col, floor = spec.get("sample_size_col"), spec.get("min_sample")
    if not col or floor is None or col not in df.columns:
        return [False] * len(df)
    return [bool(n < floor) for n in df[col]]


def _bar_colors(spec: dict, df: pd.DataFrame) -> list[str]:
    """Highlight logic, applied identically in both renderers."""
    x, y = spec["x"], spec["y"]
    weak = _low_sample(spec, df)
    colors = []
    for i, (_, row) in enumerate(df.iterrows()):
        if weak[i]:
            colors.append(CS.COLORS["muted"])
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
        colors.append(CS.COLORS["danger"] if danger else CS.COLORS["normal"])
    return colors


def _line_color(spec: dict) -> str:
    """Simulated series are amber; observed series are blue."""
    return CS.COLORS["warning"] if spec.get("simulated") else CS.COLORS["normal"]


def _fmt(value: Any, spec: dict) -> str:
    prefix = spec.get("value_prefix", "")
    if isinstance(value, (int, float)):
        if prefix == "$":
            return f"${value:,.0f}"
        return f"{value:,.1f}" if isinstance(value, float) else f"{value:,}"
    return str(value)


def _sample_notes(spec: dict, df: pd.DataFrame) -> list[str]:
    """'(n=2)' suffixes for bars that rest on too little data."""
    weak = _low_sample(spec, df)
    col = spec.get("sample_size_col")
    if not col or col not in df.columns:
        return [""] * len(df)
    return [f" (n={int(df[col].iloc[i])})" if weak[i] else ""
            for i in range(len(df))]


def _stamp() -> str:
    """Threshold provenance for exported files."""
    import rules
    return (f"Exported chart · service calls >= 4 · "
            f"day_charge >= {rules.HEAVY_DAY_CHARGE} · "
            f"single snapshot, no time dimension")


# ==========================================================================
# Matplotlib renderer — static PNG
# ==========================================================================
def render_matplotlib(name: str, stamp: bool = False,
                      dpi: int = 110) -> bytes:
    import matplotlib
    matplotlib.use("Agg")           # headless; no display required
    import matplotlib.pyplot as plt

    spec, df = chart_data(name)
    kind = spec.get("kind_override", spec["kind"])
    x, y = spec["x"], spec["y"]

    fig, ax = plt.subplots(figsize=(9, 5.2))

    if df.empty:
        ax.text(0.5, 0.5, "no data", ha="center", va="center")

    elif kind == "grouped_bar":
        series_col = spec["series"]
        groups = list(dict.fromkeys(df[x]))
        levels = list(dict.fromkeys(df[series_col]))
        width = 0.8 / max(len(levels), 1)
        palette = [CS.COLORS["normal"], CS.COLORS["danger"],
                   CS.COLORS["warning"], CS.COLORS["safe"]]
        for i, level in enumerate(levels):
            sub = df[df[series_col] == level].set_index(x).reindex(groups)
            positions = [g + i * width for g in range(len(groups))]
            ax.bar(positions, sub[y].values, width,
                   label=str(level), color=palette[i % len(palette)])
        ax.set_xticks([g + width * (len(levels) - 1) / 2
                       for g in range(len(groups))])
        ax.set_xticklabels(groups, rotation=0)
        ax.legend(frameon=False, fontsize=9)

    elif kind == "barh":
        colors = _bar_colors(spec, df)
        ax.barh(df[x].astype(str), df[y], color=colors)
        ax.invert_yaxis()
        notes = _sample_notes(spec, df)
        for i, val in enumerate(df[y]):
            ax.text(val, i, "  " + _fmt(val, spec) + notes[i],
                    va="center", fontsize=9, color=CS.COLORS["text"])

    elif kind == "line":
        ax.plot(df[x].astype(str), df[y], marker="o", linewidth=2,
                markersize=5, color=_line_color(spec))
        # Simulated series start the y-axis at zero. Autoscaling a flat
        # series magnifies noise into what looks like a trend: an active
        # base moving between 569 and 654 would fill the frame and read
        # as a collapse.
        if spec.get("simulated"):
            ax.set_ylim(0, max(df[y]) * 1.25)
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right", fontsize=8)

    else:   # bar
        colors = _bar_colors(spec, df)
        ax.bar(df[x].astype(str), df[y], color=colors)
        notes = _sample_notes(spec, df)
        for i, val in enumerate(df[y]):
            ax.text(i, val, _fmt(val, spec) + notes[i], ha="center",
                    va="bottom", fontsize=8.5, color=CS.COLORS["text"])
        if len(df) > 6:
            plt.setp(ax.get_xticklabels(), rotation=30, ha="right")

    # --- annotation ----------------------------------------------------
    ann = spec.get("annotation")
    if ann and not df.empty and kind in ("bar",):
        labels = list(df[x].astype(str))
        target = (str(ann["at_x"]) if "at_x" in ann else ann.get("at_label"))
        if target in labels:
            idx = labels.index(target)
            ax.annotate(
                ann["text"],
                xy=(idx, df[y].iloc[idx]),
                xytext=(idx - 0.3, max(df[y]) * 1.02),
                fontsize=9, color=CS.COLORS["danger"],
                arrowprops=dict(arrowstyle="->", color=CS.COLORS["danger"]),
            )

    if spec.get("y_max"):
        ax.set_ylim(0, spec["y_max"])

    ax.set_title(spec["title"], fontsize=13, fontweight="bold",
                 color=CS.COLORS["text"], loc="left", pad=34)
    if spec.get("subtitle"):
        ax.text(0, 1.035, spec["subtitle"], transform=ax.transAxes,
                fontsize=9.5, color="#6B7280", va="bottom")
    ax.set_xlabel(spec.get("x_label", ""), fontsize=10)
    ax.set_ylabel(spec.get("y_label", ""), fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x" if kind == "barh" else "y", alpha=0.25)
    ax.set_axisbelow(True)

    footer = spec.get("caption", "")
    if any(_low_sample(spec, df)):
        footer = (footer + f"  Greyed bars have fewer than "
                  f"{spec['min_sample']} customers and are too small to "
                  f"read as evidence.").strip()
    if stamp:
        footer = (footer + "\n" + _stamp()).strip()
    if footer:
        # Rotated tick labels push the axis label down, so line charts need
        # the caption further clear or the two collide.
        offset = -0.14 if kind == "line" else -0.02
        fig.text(0.01, offset, footer, fontsize=8, color="#8A94A6",
                 wrap=True, va="top")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    return buf.getvalue()


# ==========================================================================
# Plotly renderer — interactive HTML
# ==========================================================================
def render_plotly(name: str, full_html: bool = False) -> str:
    try:
        import plotly.graph_objects as go
    except ImportError:
        raise RuntimeError(
            "plotly is not installed. Run: pip install plotly. "
            "The matplotlib renderer still works without it.")

    spec, df = chart_data(name)
    kind = spec.get("kind_override", spec["kind"])
    x, y = spec["x"], spec["y"]
    fig = go.Figure()

    if df.empty:
        fig.add_annotation(text="no data", showarrow=False)

    elif kind == "grouped_bar":
        series_col = spec["series"]
        palette = [CS.COLORS["normal"], CS.COLORS["danger"],
                   CS.COLORS["warning"], CS.COLORS["safe"]]
        for i, level in enumerate(dict.fromkeys(df[series_col])):
            sub = df[df[series_col] == level]
            fig.add_bar(x=sub[x].astype(str), y=sub[y], name=str(level),
                        marker_color=palette[i % len(palette)])
        fig.update_layout(barmode="group")

    elif kind == "barh":
        colors = _bar_colors(spec, df)
        notes = _sample_notes(spec, df)
        fig.add_bar(x=df[y], y=df[x].astype(str), orientation="h",
                    marker_color=colors,
                    text=[_fmt(v, spec) + notes[i]
                          for i, v in enumerate(df[y])],
                    textposition="outside")
        fig.update_yaxes(autorange="reversed")

    elif kind == "line":
        fig.add_scatter(x=df[x].astype(str), y=df[y], mode="lines+markers",
                        line=dict(width=2, color=_line_color(spec)),
                        marker=dict(size=7))
        # Same zero-based rule as matplotlib: a flat simulated series must
        # not be autoscaled into something that looks like a trend.
        if spec.get("simulated"):
            fig.update_yaxes(range=[0, max(df[y]) * 1.25])

    else:
        colors = _bar_colors(spec, df)
        notes = _sample_notes(spec, df)
        fig.add_bar(x=df[x].astype(str), y=df[y], marker_color=colors,
                    text=[_fmt(v, spec) + notes[i]
                          for i, v in enumerate(df[y])],
                    textposition="outside")

    subtitle = spec.get("subtitle", "")
    fig.update_layout(
        title={"text": f"<b>{spec['title']}</b>"
                       + (f"<br><sup>{subtitle}</sup>" if subtitle else ""),
               "x": 0, "xanchor": "left"},
        xaxis_title=spec.get("x_label", ""),
        yaxis_title=spec.get("y_label", ""),
        template="plotly_white",
        showlegend=(kind == "grouped_bar"),
        margin=dict(t=90, b=70, l=60, r=30),
        height=460,
    )
    if spec.get("y_max"):
        fig.update_yaxes(range=[0, spec["y_max"]])
    if spec.get("caption"):
        fig.add_annotation(text=spec["caption"], xref="paper", yref="paper",
                           x=0, y=-0.28, showarrow=False, align="left",
                           font=dict(size=10, color="#8A94A6"))

    return fig.to_html(full_html=full_html, include_plotlyjs="cdn")


# ==========================================================================
# Explanation — the same payload that drew the chart
# ==========================================================================
def explain(name: str) -> dict:
    """LLM explanation of a chart, grounded in the chart's own data.

    Module 8 from the original design. The payload passed to the model is
    the payload that produced the chart, so the explanation cannot describe
    something other than what was plotted.

    The instruction is deliberately per-chart. A single generic template
    across ten structurally different charts produced circular explanations
    ("the driver is the day-charge cliff, which is a driver of churn") and
    non-actions ("closely monitor these customers"). Each spec now carries
    an explain_focus saying what is actually interesting, and optional
    hypotheses offering candidate business interpretations.

    Note that SNAPSHOT_META is NOT sent in full for chart explanations. Its
    driver notes are pre-written conclusions, and the model copied them
    instead of reading the plotted rows. The chart data alone grounds every
    number, so the constraint text is trimmed to what the guardrails need.
    """
    import guardrails
    import prompts
    from llm_provider import LLMError, get_provider_for

    spec, df = chart_data(name)

    payload = {
        "meta": {
            "grain": metrics.SNAPSHOT_META["grain"],
            "time_dimension": None,
            "comparisons_forbidden": (
                metrics.SNAPSHOT_META["comparisons_forbidden"]),
        },
        "chart_title": spec["title"],
        "chart_subtitle": spec.get("subtitle"),
        "x_axis": spec.get("x_label") or spec["x"],
        "y_axis": spec.get("y_label") or spec["y"],
        "plotted_data": df.to_dict(orient="records"),
    }

    # Simulated charts must carry their origin INTO the payload, or
    # guardrails._is_simulated() cannot detect them and will apply the
    # stricter no-temporal-claims rule to a legitimate time series.
    if spec.get("simulated"):
        payload["data_origin"] = "SIMULATED"
        payload["meta"]["simulation_note"] = (
            "This history is generated, not observed. Churn in it is flat "
            "by construction; any variation is sampling noise.")

    weak = _low_sample(spec, df)
    if any(weak):
        payload["small_sample_warning"] = (
            f"Rows with fewer than {spec['min_sample']} customers are too "
            f"small to support a conclusion. Do not present them as findings."
        )

    instruction = (
        "Explain this chart to a non-technical manager in 3-5 sentences.\n"
        "1. OBSERVATION: describe the SHAPE of the pattern, not just the "
        "largest value. Where does it change, and how sharply?\n"
        "2. INTERPRETATION: offer a plausible business explanation. If the "
        "chart plots only one variable, do NOT name that variable as its "
        "own cause - that is circular. Say what the pattern suggests.\n"
        "3. ACTION: one specific, concrete step. 'Monitor', 'keep an eye on' "
        "and 'closely watch' are not actions - name what someone should "
        "actually do.\n"
        "Use only the plotted rows. Do not restate the chart subtitle back "
        "to me."
    )

    if spec.get("explain_focus"):
        instruction += f"\n\nFOCUS FOR THIS CHART:\n{spec['explain_focus']}"

    if spec.get("hypotheses"):
        instruction += (
            "\n\nCANDIDATE INTERPRETATIONS - these are hypotheses, not "
            "measurements. Present them as possible explanations, never as "
            "findings:\n"
            + "\n".join(f"- {h}" for h in spec["hypotheses"]))

    provider = get_provider_for("narration")
    user = prompts.build_user_prompt("", payload, instruction)

    try:
        text = provider.complete(prompts.SYSTEM_PROMPT, user)
    except LLMError as exc:
        return {"chart": name, "valid": False, "text": None,
                "error": str(exc), "payload": payload}

    report = guardrails.validate(text, payload)
    return {"chart": name, "valid": report["passed"], "text": text,
            "validation": report, "payload": payload}


# ==========================================================================
# Export
# ==========================================================================
def export_all(out_dir: str | None = None, dpi: int = 150) -> list[str]:
    """Materialise every chart as a stamped PNG. For the Phase 7 report."""
    from pathlib import Path
    import config as C
    # Default to <project root>/exports so output does not land wherever
    # the process happened to be started from.
    path = Path(out_dir) if out_dir else C.PROJECT_ROOT / "exports"
    path.mkdir(parents=True, exist_ok=True)

    written = []
    for name in CS.CHART_SPECS:
        png = render_matplotlib(name, stamp=True, dpi=dpi)
        target = path / f"{name}.png"
        target.write_bytes(png)
        written.append(str(target))
    return written


if __name__ == "__main__":
    import sys
    if "--export" in sys.argv:
        i = sys.argv.index("--export")
        out = sys.argv[i + 1] if len(sys.argv) > i + 1 else None
        for f in export_all(out):
            print("wrote", f)
    else:
        print("charts available:")
        for c in CS.list_charts():
            tag = " [SIMULATED]" if c.get("simulated") else ""
            print(f"  {c['id']:<26} {c['kind']:<13} {c['title']}{tag}")