"""
Phase 9 — AI advisor, scenario lab, report builder, validation.

EVIDENCE-FIRST, WITHOUT INVENTING STRUCTURE
-------------------------------------------
The brief asked for four separate cards: ANSWER / EVIDENCE / RECOMMENDATION
/ CAVEAT. The backend's ask() returns ONE prose answer — there is no
recommendation field to display. Rather than have the frontend fabricate
that split, the cards are populated from what genuinely exists:

    ANSWER      the model's prose, verbatim
    VERIFIED    the guardrail report — how many figures were checked
    EVIDENCE    the rows the query returned, plus the SQL that produced them
    CAVEAT      limitations from SNAPSHOT_META

Recommendations appear on the Segments page instead, where the payload
really does carry an action per segment.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from components import backend as be
from components.ui import (ai_block, card_close, card_open, compare_strip,
                           kpi_row, notice, page_header, section,
                           verification)

SUGGESTIONS = [
    "Which segment has the highest churn rate?",
    "How much revenue is at risk from churning customers?",
    "How does churn vary by number of service calls?",
    "Which states have the worst churn among larger states?",
    "Do international plan subscribers churn more?",
    "How did churn change compared to last quarter?",
]


# ==========================================================================
def advisor() -> None:
    page_header(
        "AI Business Advisor",
        "Ask a question about the portfolio. Every figure in the answer is "
        "checked against the data that produced it before you see it.",
        "observed")

    st.session_state.setdefault("history", [])
    st.session_state.setdefault("pending", "")

    section("Suggested questions")
    # The last suggestion is deliberately unanswerable. The system should
    # decline it rather than invent a trend, which is worth demonstrating.
    for row in (SUGGESTIONS[:3], SUGGESTIONS[3:]):
        cols = st.columns(len(row), gap="small")
        for i, q in enumerate(row):
            with cols[i]:
                if st.button(q if len(q) <= 44 else q[:42] + "…",
                             key=f"sg_{q[:14]}", help=q):
                    st.session_state.pending = q
                    st.rerun()

    card_open()
    question = st.text_input(
        "Your question", value=st.session_state.pending,
        placeholder="e.g. which customers should we prioritise for retention?",
        key="q_in", label_visibility="collapsed")
    a, b, _ = st.columns([1, 1, 3])
    with a:
        asked = st.button("Ask", type="primary", key="ask")
    with b:
        if st.button("Clear", key="clr"):
            st.session_state.history = []
            st.session_state.pending = ""
            st.rerun()
    card_close()

    if asked and question.strip():
        with st.spinner("Generating SQL · validating · executing · narrating"):
            try:
                st.session_state.history.insert(0, be.ask(question.strip()))
                st.session_state.pending = ""
            except Exception as exc:
                st.error(f"The question could not be answered — {exc}")

    if not st.session_state.history:
        notice("info",
               "<b>How this works.</b> Your question becomes a SQL query, "
               "validated against an allowlist and run read-only. The model "
               "then explains the returned rows — it never produces a "
               "number itself, and every figure it quotes is checked "
               "against the query result before display.")
        return

    section("Conversation")
    for i, r in enumerate(st.session_state.history):
        _answer(r, i)


def _answer(r: dict, idx: int) -> None:
    st.markdown(f'<div style="font-size:.95rem;color:#8794A8;'
                f'margin:1.6rem 0 .7rem 0">◦ {r["question"]}</div>',
                unsafe_allow_html=True)

    if r.get("error") and not r.get("answer"):
        st.error(f"Could not answer — {r['error']}")
        return

    ai_block("answer", "Answer", r.get("answer") or "No answer returned.", "✦")

    v = r.get("validation") or {}
    verification(r.get("valid", False), v.get("numbers_checked", 0),
                 "the query result")
    if not r.get("valid"):
        for x in v.get("violations", []):
            st.warning(f"**{x['type']}** — {x['detail']}")

    rows = r.get("rows") or []
    if rows:
        with st.expander(f"◈  Evidence — {len(rows)} rows returned"):
            st.dataframe(pd.DataFrame(rows), use_container_width=True,
                         hide_index=True)
            if r.get("sql"):
                st.markdown("**Query executed**")
                st.markdown(f'<div class="sqlbox">{r["sql"]}</div>',
                            unsafe_allow_html=True)
            if str(r.get("path", "")).startswith("fallback"):
                st.caption(f"Generated SQL was rejected, so the answer came "
                           f"from a curated metric ({r['path']}).")

    ai_block("caveat", "Caveat",
             "Single snapshot with no dates: nothing here describes change "
             "over time, and no figure is a forecast. State-level rates "
             "rest on small samples.", "▲")


# ==========================================================================
def scenario_lab() -> None:
    page_header(
        "What-If Scenario Lab",
        "Explore potential outcomes under controlled assumptions.",
        "projected")

    notice("projected",
           "<b>HYPOTHETICAL — NOT A FORECAST.</b> This answers: if this many "
           "customers moved between these groups, how would the arithmetic "
           "change? The data is <i>observational</i> — flagged customers "
           "churn more, but that does not mean removing the flag removes "
           "the churn. Results are therefore a range, never one number.")

    controls, results = st.columns([1, 2.4], gap="large")

    levers = be.scenario_levers()
    lookup = {l["label"]: l for l in levers}

    with controls:
        card_open("Parameters")
        choice = st.radio("Lever", list(lookup), key="lv",
                          label_visibility="collapsed")
        lever = lookup[choice]
        st.caption(lever["description"])
        st.markdown("<div style='height:.6rem'></div>",
                    unsafe_allow_html=True)
        pct = st.slider("Magnitude", 5, int(lever["max_pct"]), 30, 5,
                        format="%d%%", key="pc")
        card_close()

        notice("info",
               "Only one lever at a time. The engine has no combined model, "
               "and stacking sliders would imply an interaction the data "
               "does not support.")

    with results:
        try:
            r = be.run_scenario(lever["id"], float(pct))
        except Exception as exc:
            st.error(f"Scenario could not be computed — {exc}")
            return

        h, band = r["headline"], r["band"]
        compare_strip(
            {"label": "Current (observed)",
             "value": f"{h['baseline_churn_pct']}%", "sub": "Measured churn"},
            {"label": "Scenario (projected)",
             "value": f"{h['scenario_churn_pct']}%",
             "sub": "At the central efficacy assumption"})

        st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)
        kpi_row([
            {"label": "Improvement", "value": f"{h['improvement_pp']} pp",
             "sub": f"Range {band['low']['improvement_pp']}–"
                    f"{band['high']['improvement_pp']} pp",
             "icon": "▼", "accent": "projected"},
            {"label": "Customers affected",
             "value": f"{h['customers_affected']:.0f}",
             "sub": f"of {band['central']['group_size']} in the group",
             "icon": "◈", "accent": "projected"},
            {"label": "Net value †", "value": f"${h['net_value']:,.0f}",
             "sub": "Over the assumed horizon", "icon": "◆",
             "accent": "projected"},
        ])

    section("Sensitivity to the efficacy assumption")
    card_open("How much of the risk gap does the intervention recover?",
              "The upper bound treats the entire observed association as "
              "causal and fully reversible — the most optimistic reading "
              "available, not the expected case.")
    st.dataframe(pd.DataFrame([{
        "Efficacy": f"{band[n]['efficacy_assumed']:.0%}"
                    + {"central": "  (central)",
                       "upper_bound": "  (upper bound)"}.get(n, ""),
        "Scenario churn %": band[n]["scenario_churn_pct"],
        "Improvement (pp)": band[n]["improvement_pp"],
        "Churners avoided": band[n]["churners_avoided"],
        "Net value †": round(band[n]["economics"]["net_value"], 2),
    } for n in ("low", "central", "high", "upper_bound")]),
        use_container_width=True, hide_index=True)
    st.info(r["range_note"])
    card_close()

    section("All levers at this magnitude")
    comp = be.compare_levers(float(pct))
    card_open("Comparison", comp["meta"]["warning"])
    st.dataframe(pd.DataFrame([{
        "Lever": x["label"],
        "Customers affected": round(x["customers_affected"]),
        "Improvement (pp)": x["improvement_pp"],
        "Net value †": round(x["net_value"], 2),
    } for x in comp["ranked"]]), use_container_width=True, hide_index=True)
    card_close()

    e = be.economics()
    notice("projected",
           f"<b>† Assumptions.</b> Save-rate band {e['save_rate_low']:.0%}–"
           f"{e['save_rate_high']:.0%}, contact cost "
           f"${e['retention_contact_cost']:.2f}, acquisition cost "
           f"${e['acquisition_cost']:.2f}, horizon "
           f"{e['value_horizon_periods']} periods. Not measurements.")


# ==========================================================================
def report_builder() -> None:
    page_header(
        "Executive Report",
        "A PDF assembling the KPIs, drivers, segments, cohorts and "
        "simulated structure, with validated AI narrative throughout.",
        "observed")

    left, right = st.columns([1, 1.5], gap="large")

    with left:
        card_open("Report builder")
        st.markdown("**Report type**")
        st.selectbox("Type", ["Portfolio Risk Report"], key="rtype",
                     label_visibility="collapsed", disabled=True)
        st.markdown("<div style='height:.5rem'></div>",
                    unsafe_allow_html=True)
        st.markdown("**Options**")
        charts = st.checkbox("Include charts and AI captions", value=True,
                             key="rc")
        st.caption(
            "~15 model calls, 60–90 seconds." if charts
            else "~5 model calls, about 20 seconds. Text and tables only.")
        card_close()

        st.markdown("<div style='height:.8rem'></div>", unsafe_allow_html=True)
        if st.button("▤  Generate report", type="primary", key="gen"):
            with st.spinner("Assembling sections · validating narration · "
                            "rendering PDF"):
                try:
                    data, name = be.generate_report(include_charts=charts)
                    st.session_state.setdefault("reports", []).append(
                        (data, name))
                    st.rerun()
                except Exception as exc:
                    st.error(f"The report could not be generated — {exc}")

    with right:
        card_open("Report contents")
        for item, desc in [
            ("Portfolio at a glance", "KPIs with AI executive summary"),
            ("Confirmed churn drivers", "Three thresholds, with charts"),
            ("Risk segments and actions", "Segments, offers, target sizes"),
            ("Tenure cohorts", "The negative result, stated plainly"),
            ("Portfolio structure", "Simulated history, clearly banded"),
            ("What this cannot tell you", "Limitations as content"),
            ("Provenance", "Reconciliation and validation outcome"),
        ]:
            st.markdown(
                f'<div style="padding:.55rem 0;border-bottom:1px solid '
                f'#161E2B"><span style="color:#93BBFC">◦</span> '
                f'<b style="font-size:.87rem">{item}</b>'
                f'<div style="font-size:.76rem;color:#6E7A8E;'
                f'margin-left:1rem">{desc}</div></div>',
                unsafe_allow_html=True)
        card_close()

    reports = st.session_state.get("reports", [])
    if reports:
        section("Generated this session")
        st.caption("These stay available while the session is open, "
                   "including after you navigate to another page.")
        for i, (data, name) in enumerate(reversed(reports)):
            a, b = st.columns([3, 1], gap="medium")
            with a:
                st.markdown(
                    f'<div class="card card-flat" style="padding:.75rem 1.1rem">'
                    f'<b style="font-size:.87rem">{name}</b>'
                    f'<div style="font-size:.75rem;color:#6E7A8E">'
                    f'{len(data) / 1024:,.0f} KB</div></div>',
                    unsafe_allow_html=True)
            with b:
                st.download_button("↓  Download", data, file_name=name,
                                   mime="application/pdf", key=f"dl_{i}",
                                   use_container_width=True)
        notice("info",
               "Each PDF is a snapshot of the data at the moment it was "
               "built, not a live view. Regenerate to pick up any change to "
               "thresholds or data.")


# ==========================================================================
def quality() -> None:
    page_header(
        "Data Quality",
        "Every figure traces back to the source data. These are the checks "
        "that prove it.", "observed")

    rec = be.reconciliation()
    labels = {"customers": ("Customers", "◱"), "churned": ("Churned", "◲"),
              "revenue": ("Revenue", "◆")}

    section("Reconciliation")
    st.caption("Segment totals must equal the portfolio. If rules overlapped "
               "or left gaps, customers would silently vanish from the "
               "totals and every recommendation would be quietly wrong.")

    kpi_row([{
        "label": labels.get(k, (k.title(), "◇"))[0],
        "value": (f"${v['portfolio']:,.2f}" if k == "revenue"
                  else f"{v['portfolio']:,}"),
        "sub": ("✓  PASS · segments match" if v["match"]
                else "✕  MISMATCH"),
        "icon": labels.get(k, (k, "◇"))[1],
        "accent": "success" if v["match"] else "danger",
    } for k, v in rec.items()])

    if all(v["match"] for v in rec.values()):
        st.success("All reconciliation checks pass — segment totals equal "
                   "the portfolio exactly.")
    else:
        st.error("Reconciliation FAILING. Figures elsewhere may be "
                 "unreliable until resolved.")

    section("System status")
    st.caption("Read from live checks, not asserted.")
    for s in be.system_status():
        icon = {"on": "✓", "warn": "●", "off": "✕"}[s["state"]]
        colour = {"on": "#22C55E", "warn": "#F59E0B",
                  "off": "#EF4444"}[s["state"]]
        detail = (f'<div style="color:#5C6779;font-size:.75rem;'
                  f'margin-top:.15rem">{s["detail"]}</div>'
                  if s.get("detail") else "")
        st.markdown(
            f'<div class="card card-flat" style="padding:.7rem 1.1rem;'
            f'margin-bottom:.5rem"><span style="color:{colour};'
            f'font-weight:700">{icon}</span>&nbsp;&nbsp;'
            f'<span style="font-size:.87rem">{s["label"]}</span>{detail}'
            f'</div>', unsafe_allow_html=True)

    section("Offline test suites")
    with st.expander("◈  Eight suites, ~183 checks, no API quota"):
        for name, desc in [
            ("Phase 1 — data", "CSV reconciles to the database to the cent"),
            ("Phase 2 — views", "Revenue agrees across eight view paths"),
            ("Phase 6 — rules", "Segments exhaustive and non-overlapping"),
            ("Phase 6.5 — simulated", "Panel is flat and reconciles"),
            ("Phase 4 — guardrails", "Fabricated figures and trends caught"),
            ("Phase 5 — SQL guard", "13 injection patterns blocked"),
            ("Phase 7 — report", "Three number types disclosed"),
            ("Phase 8 — scenarios", "Results banded, framed hypothetically"),
        ]:
            st.markdown(f'<div style="padding:.4rem 0;font-size:.84rem">'
                        f'<b>{name}</b> — <span style="color:#6E7A8E">'
                        f'{desc}</span></div>', unsafe_allow_html=True)
        st.code("python run.py check", language="bash")

    section("Known limitations")
    for c in be.caveats():
        notice("info", c)



# ==========================================================================
def risk_model() -> None:
    """Phase 10 — per-customer risk scoring with SHAP.

    Three tabs mirroring the three explanation levels: which model and why,
    what drives the scores globally, and why one customer scored what they
    did. The per-customer tab is the operationally useful one.
    """
    page_header(
        "Churn Risk Model",
        "Ranks customers by how closely they resemble those who already "
        "churned. Not a forecast, and not a verdict.",
        "observed")

    if not be.model_available():
        notice("info",
               "<b>No trained model found.</b> Run "
               "<code>python run.py train</code> from the project root, "
               "then reload this page.")
        return

    notice("projected",
           "<b>WHAT THIS DOES AND DOES NOT CLAIM.</b> The model ranks "
           "customers by resemblance to past churners. It says nothing "
           "about <i>when</i> anyone will leave, and a high score is a "
           "probability rather than a verdict. It was trained on a single "
           "snapshot and validated on a held-out quarter of it.")

    tabs = st.tabs(["Model comparison", "What drives the score",
                    "Customer risk"])
    with tabs[0]:
        _model_comparison()
    with tabs[1]:
        _model_importance()
    with tabs[2]:
        _customer_risk()


def _model_comparison() -> None:
    m = be.model_metrics()
    winner = m["models"][m["winner"]]

    kpi_row([
        {"label": "Best model", "value": winner["label"].split("(")[0].strip(),
         "sub": "selected on PR-AUC", "icon": "◆", "accent": "success"},
        {"label": "PR-AUC", "value": f"{winner['pr_auc']:.3f}",
         "sub": "Precision-recall area, suited to imbalance", "icon": "◈"},
        {"label": "Base rate", "value": f"{m['base_rate_pct']}%",
         "sub": f"'nobody churns' scores "
                f"{100 - m['base_rate_pct']:.1f}% accuracy", "icon": "▲",
         "accent": "warning"},
    ])

    notice("info",
           f"<b>Accuracy is not reported as a headline.</b> "
           f"{m['meta']['accuracy_note'].capitalize()}.")

    section("Three models compared")
    card_open("Balanced operating point",
              "Precision and recall at the threshold maximising F1, rather "
              "than at the capacity threshold — which achieves high "
              "precision by construction and would overstate the model.")
    st.dataframe(pd.DataFrame([{
        "Model": r["label"],
        "ROC-AUC": r["roc_auc"],
        "PR-AUC": r["pr_auc"],
        "Precision": r["balanced_point"]["precision"],
        "Recall": r["balanced_point"]["recall"],
        "F1": r["balanced_point"]["f1"],
        "Interpretability": r["interpretable"],
    } for r in m["models"].values()]), use_container_width=True,
        hide_index=True)
    card_close()

    section("Operating points")
    card_open("Capacity against balance", m["meta"]["threshold_basis"])
    rows = []
    for r in m["models"].values():
        b = r["balanced_point"]
        rows.append({"Model": r["label"], "Point": "capacity",
                     "Flagged": r["flagged"], "Precision": r["precision"],
                     "Recall": r["recall"],
                     "Missed churners": r["false_negatives"]})
        rows.append({"Model": r["label"], "Point": "balanced",
                     "Flagged": b["flagged"], "Precision": b["precision"],
                     "Recall": b["recall"],
                     "Missed churners": b["false_negatives"]})
    st.dataframe(pd.DataFrame(rows), use_container_width=True,
                 hide_index=True)
    card_close()

    _ai_button("comparison", "Explain this comparison")


def _model_importance() -> None:
    imp = be.model_importance()

    section("Global SHAP attribution")
    notice("info", f"<b>{imp['meta']['measures'].capitalize()}.</b>")

    card_open("Feature contributions",
              f"Mean absolute SHAP value across a sample of "
              f"{imp['meta']['sample_size']} customers.")
    st.dataframe(pd.DataFrame(imp["features"]), use_container_width=True,
                 hide_index=True)
    card_close()

    confirms = imp.get("confirms_rules_engine") or []
    if confirms:
        notice("info",
               f"<b>The model agrees with the rules engine.</b> Its top "
               f"contributors include {', '.join(confirms)} — the same "
               f"factors the deterministic segments already use. The model "
               f"was given raw columns only, with no knowledge of the "
               f"thresholds.")

    _ai_button("importance", "Explain these drivers")


def _customer_risk() -> None:
    section("Customer risk scores")
    st.caption("The model should score churners high AND retained customers "
               "low. Both directions matter — a model that flags everyone "
               "would score churners perfectly and be useless.")

    # Score the whole base so retained customers are visible too. Showing
    # only the top 200 hides half the model's job: a reader could not tell
    # whether low scores are actually being assigned to people who stayed.
    rows = be.scored_customers(3333)
    df = pd.DataFrame(rows)

    cols = st.columns([1, 1, 2])
    with cols[0]:
        outcome = st.radio("Show", ["All", "Churned", "Retained"],
                           horizontal=True, key="risk_outcome")
    with cols[1]:
        band = st.radio("Score band", ["All", "High", "Low"],
                        horizontal=True, key="risk_band")

    view = df
    if outcome == "Churned":
        view = view[view.churned == 1]
    elif outcome == "Retained":
        view = view[view.churned == 0]
    if band == "High":
        view = view[view.flagged == 1]
    elif band == "Low":
        view = view[view.flagged == 0]

    with cols[2]:
        st.markdown(
            f'<div style="padding-top:1.6rem;font-size:.82rem;color:#8794A8">'
            f'{len(view):,} of {len(df):,} customers · mean score '
            f'{view.risk_score.mean():.3f}</div>' if len(view)
            else '<div style="padding-top:1.6rem">no customers match</div>',
            unsafe_allow_html=True)

    show = view[["customer_id", "risk_score", "flagged",
                 "customer_service_calls", "international_plan",
                 "day_charge", "total_charge", "churned"]].copy()
    show["risk_score"] = show.risk_score.round(4)
    st.dataframe(show, use_container_width=True, hide_index=True, height=300)

    # --- how well do the two groups separate? -------------------------
    section("Does the model separate the two groups?")
    churned = df[df.churned == 1].risk_score
    retained = df[df.churned == 0].risk_score
    kpi_row([
        {"label": "Mean score — churned", "value": f"{churned.mean():.3f}",
         "sub": f"{len(churned):,} customers", "icon": "◲",
         "accent": "danger"},
        {"label": "Mean score — retained", "value": f"{retained.mean():.3f}",
         "sub": f"{len(retained):,} customers", "icon": "◱",
         "accent": "success"},
        {"label": "Separation",
         "value": f"{churned.mean() - retained.mean():.3f}",
         "sub": "Gap between the two means", "icon": "◈"},
    ])

    section("Look up any customer")
    st.caption("Enter any customer ID — churned or retained. Seeing why a "
               "retained customer scored LOW is as informative as seeing "
               "why a churner scored high.")

    pick = st.columns([1, 1, 2])
    with pick[0]:
        chosen = st.number_input(
            "Customer ID", min_value=int(df.customer_id.min()),
            max_value=int(df.customer_id.max()),
            value=int(df.customer_id.iloc[0]), step=1, key="risk_cust_id")
    with pick[1]:
        st.markdown("<div style='height:1.6rem'></div>",
                    unsafe_allow_html=True)
        if st.button("Random retained customer", key="rand_retained"):
            st.session_state.risk_cust_id = int(
                df[df.churned == 0].sample(1).customer_id.iloc[0])
            st.rerun()

    try:
        detail = be.explain_customer(int(chosen))
    except Exception as exc:
        st.error(f"Customer {chosen} could not be scored — {exc}")
        return

    _render_customer_detail(detail)


def _render_customer_detail(detail: dict) -> None:
    churned = bool(detail["actually_churned"])
    flagged = bool(detail["flagged"])
    # Four outcomes, and the two errors are worth naming explicitly rather
    # than leaving a reader to work out whether the model was right.
    verdict = {
        (True, True): ("Correctly flagged", "success"),
        (False, False): ("Correctly not flagged", "success"),
        (True, False): ("MISSED — churned but scored below threshold",
                        "danger"),
        (False, True): ("FALSE ALARM — retained but flagged", "warning"),
    }[(churned, flagged)]
    verdict_icon = {"success": "✓", "warning": "!", "danger": "✕"}[verdict[1]]

    kpi_row([
        {"label": "Risk score", "value": f"{detail['risk_score']:.3f}",
         "sub": ("above the flagging threshold" if flagged
                 else "below the threshold"),
         "icon": "◈", "accent": "danger" if flagged else "success"},
        {"label": "Actual outcome",
         "value": "Churned" if churned else "Retained",
         "sub": "From the source data", "icon": "◱",
         "accent": "danger" if churned else "success"},
        {"label": "Model verdict", "value": verdict[0].split("—")[0].strip(),
         "sub": verdict[0], "icon": verdict_icon, "accent": verdict[1]},
    ])

    card_open("SHAP contributions",
              "How much each factor moved THIS customer's score. Positive "
              "raises risk, negative lowers it — and for a retained "
              "customer the negative contributions are the interesting "
              "part.")
    st.dataframe(pd.DataFrame(detail["contributions"]),
                 use_container_width=True, hide_index=True)
    card_close()

    # Name what an error MEANS. A reader who sees "MISSED" without an
    # explanation may conclude the model is broken, when in fact these are
    # customers the three drivers simply do not describe.
    if churned and not flagged:
        notice("info",
               "<b>This customer churned without a strong signal.</b> Their "
               "features resemble retained customers, so the model scored "
               "them low. Roughly a third of churners fall into this group "
               "at the current threshold — they left for reasons the three "
               "drivers do not capture. Lowering the threshold would catch "
               "some of them, at the cost of flagging retained customers "
               "too.")
    elif flagged and not churned:
        notice("info",
               "<b>False alarm.</b> This customer carries risk factors but "
               "did not churn. Contacting them is not wasted — a retention "
               "offer to a satisfied high-risk customer is cheap insurance "
               "— but it is worth knowing the model over-flagged here.")

    if detail["recommended_actions"]:
        section("Recommended action")
        st.caption("Looked up deterministically from the rules engine — the "
                   "model says WHY, the rules say WHAT TO DO.")
        for a in detail["recommended_actions"]:
            st.markdown(
                f'<div class="card card-flat" style="padding:.8rem 1.1rem;'
                f'margin-bottom:.5rem">'
                f'<b style="font-size:.9rem">{a["action"]}</b>'
                f'<div style="font-size:.78rem;color:#6E7A8E;'
                f'margin-top:.25rem">Driver: {a["driver"]} · '
                f'likely cause: {a["cause"]}</div></div>',
                unsafe_allow_html=True)
    elif not flagged:
        notice("info",
               "<b>No action recommended.</b> No factor raised this "
               "customer's score materially — which is what a correctly "
               "scored retained customer looks like.")

    _ai_button("customer", "Explain this score",
               customer_id=detail["customer_id"])

def _ai_button(kind: str, label: str, customer_id: int | None = None) -> None:
    key = f"ai_model_{kind}_{customer_id or ''}"
    col, _ = st.columns([1, 3])
    with col:
        if st.button(f"✦  {label}", key=f"btn_{key}"):
            with st.spinner("Reading the model output…"):
                st.session_state[key] = be.narrate_model(kind, customer_id)

    result = st.session_state.get(key)
    if not result:
        return
    if result.get("error"):
        st.error(f"Explanation unavailable — {result['error']}")
        return

    v = result.get("validation") or {}
    ai_block("answer", "AI explanation", result["text"], "✦")
    verification(result.get("valid", False), v.get("numbers_checked", 0),
                 "the model output")
    if not result.get("valid"):
        for x in v.get("violations", []):
            st.warning(f"**{x['type']}** — {x['detail']}")