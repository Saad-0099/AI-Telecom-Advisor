"""
Phase 9 — design system.

Rewritten for the card-based layout. Three rules drive every choice:

  SPACING IS THE MAIN FIX. The previous version crowded unrelated controls
  into single rows. Sections now carry generous vertical rhythm and every
  chart sits inside a card rather than against the page background.

  ANIMATIONS RUN ONCE. 200-420ms, on appearance, never looping. A dashboard
  that keeps moving is harder to read, not more impressive. All disabled
  under prefers-reduced-motion.

  PROVENANCE COLOURS CARRY MEANING and stay consistent with the PDF report
  and the matplotlib charts:
      blue    OBSERVED    measured from the source data
      amber   SIMULATED   generated history, structure only
      violet  PROJECTED   hypothetical, rests on stated assumptions
"""

from __future__ import annotations

PALETTE = {
    "bg": "#080B12",
    "surface": "#111721",
    "surface_2": "#161D29",
    "border": "#212B3A",
    "text": "#E8EDF5",
    "text_2": "#A9B4C6",
    "muted": "#6E7A8E",
    "accent": "#3B82F6",
    "violet": "#A78BFA",
    "success": "#22C55E",
    "warning": "#F59E0B",
    "danger": "#EF4444",
    "observed": "#3B82F6",
    "simulated": "#F59E0B",
    "projected": "#A78BFA",
}

CSS = """
<style>
.stApp {
    background:
        radial-gradient(1200px 600px at 15% -12%, #12203a26 0%, transparent 62%),
        radial-gradient(1000px 500px at 85% -8%, #241b4a20 0%, transparent 58%),
        #080B12;
    color:#E8EDF5;
}
.main .block-container { padding:2.4rem 2.6rem 5rem 2.6rem; max-width:1340px; }
#MainMenu, footer, header { visibility:hidden; }
h1,h2,h3,h4 { color:#E8EDF5; letter-spacing:-.02em; font-weight:640; }

@keyframes riseIn { from{opacity:0;transform:translateY(12px)} to{opacity:1;transform:none} }
@keyframes fadeIn { from{opacity:0} to{opacity:1} }
.rise { animation:riseIn 400ms cubic-bezier(.22,.68,.32,1) both; }
.fade { animation:fadeIn 320ms ease both; }
.d1{animation-delay:50ms}.d2{animation-delay:100ms}.d3{animation-delay:150ms}
.d4{animation-delay:200ms}.d5{animation-delay:250ms}.d6{animation-delay:300ms}
@media (prefers-reduced-motion: reduce){ .rise,.fade{animation:none !important} }

.pagehead { display:flex; align-items:flex-start; justify-content:space-between;
            gap:1.5rem; padding-bottom:1.3rem; margin-bottom:1.9rem;
            border-bottom:1px solid #1A2230; animation:fadeIn 300ms both; }
.pagehead .t { font-size:1.72rem; font-weight:650; margin:0 0 .35rem 0; }
.pagehead .s { color:#8794A8; font-size:.93rem; margin:0; max-width:74ch;
               line-height:1.55; }

.card { background:linear-gradient(168deg,#111721 0%,#0F141D 100%);
        border:1px solid #212B3A; border-radius:16px; padding:1.35rem 1.5rem;
        transition:transform 200ms cubic-bezier(.22,.68,.32,1),
                   border-color 200ms ease, box-shadow 200ms ease; }
.card:hover { transform:translateY(-3px); border-color:#2E3B4F;
              box-shadow:0 10px 34px -18px #000, 0 0 0 1px #3B82F614; }
.card-flat:hover { transform:none; box-shadow:none; border-color:#212B3A; }
.card-head { margin-bottom:1rem; }
.card-head .ct { font-size:1rem; font-weight:600; color:#E8EDF5; margin:0; }
.card-head .cs { font-size:.81rem; color:#6E7A8E; margin:.25rem 0 0 0;
                 line-height:1.5; }

.kpi { position:relative; overflow:hidden; padding:1.25rem 1.35rem; }
.kpi::before { content:""; position:absolute; top:0; left:0; right:0; height:2px;
               background:linear-gradient(90deg,var(--a,#3B82F6),transparent 78%); }
.kpi .row { display:flex; align-items:center; justify-content:space-between;
            margin-bottom:.7rem; }
.kpi .lab { font-size:.68rem; text-transform:uppercase; letter-spacing:.1em;
            color:#6E7A8E; font-weight:660; }
.kpi .ico { font-size:.95rem; color:var(--a,#3B82F6); opacity:.9; }
.kpi .val { font-size:1.95rem; font-weight:670; line-height:1.1;
            font-variant-numeric:tabular-nums; margin-bottom:.4rem; }
.kpi .sub { font-size:.77rem; color:#6E7A8E; line-height:1.45; }

.badge { display:inline-flex; align-items:center; gap:.35rem;
         padding:.26rem .68rem; border-radius:7px; font-size:.66rem;
         font-weight:700; letter-spacing:.09em; text-transform:uppercase;
         border:1px solid; white-space:nowrap; }
.badge-observed  { color:#93BBFC; background:#3B82F614; border-color:#3B82F63D; }
.badge-simulated { color:#FCD34D; background:#F59E0B14; border-color:#F59E0B44; }
.badge-projected { color:#C4B5FD; background:#A78BFA14; border-color:#A78BFA44; }
.badge-pass      { color:#86EFAC; background:#22C55E14; border-color:#22C55E44; }
.badge-fail      { color:#FCA5A5; background:#EF444414; border-color:#EF444444; }

.notice { border-radius:12px; padding:.85rem 1.1rem; margin:.4rem 0 1.4rem 0;
          font-size:.83rem; line-height:1.6; border:1px solid;
          border-left-width:3px; }
.notice-simulated { background:#F59E0B0A; border-color:#F59E0B33;
                    border-left-color:#F59E0B; color:#E4C88A; }
.notice-projected { background:#A78BFA0A; border-color:#A78BFA33;
                    border-left-color:#A78BFA; color:#CFC2FA; }
.notice-info      { background:#3B82F60A; border-color:#3B82F633;
                    border-left-color:#3B82F6; color:#B4CDF7; }

.ai { border-radius:14px; padding:1.1rem 1.3rem; margin-bottom:.75rem;
      border:1px solid #212B3A; }
.ai .h { display:flex; align-items:center; gap:.5rem; font-size:.66rem;
         font-weight:720; letter-spacing:.12em; text-transform:uppercase;
         margin-bottom:.6rem; }
.ai .b { font-size:.91rem; line-height:1.68; color:#CFD8E6; }
.ai-answer   { background:linear-gradient(140deg,#3B82F60F,#A78BFA08);
               border-color:#3B82F633; }
.ai-answer .h{ color:#93BBFC; }
.ai-evidence { background:#111721; }
.ai-evidence .h { color:#86EFAC; }
.ai-action   { background:#22C55E08; border-color:#22C55E26; }
.ai-action .h{ color:#86EFAC; }
.ai-caveat   { background:#F59E0B08; border-color:#F59E0B26; }
.ai-caveat .h{ color:#FCD34D; }
.ai-caveat .b{ font-size:.83rem; color:#A89468; }

.verify { display:inline-flex; align-items:center; gap:.4rem; font-size:.72rem;
          color:#86EFAC; margin-top:.65rem; padding:.3rem .6rem;
          background:#22C55E0F; border-radius:6px; }
.verify.bad { color:#FCA5A5; background:#EF44440F; }

section[data-testid="stSidebar"] { background:#0A0E16;
                                   border-right:1px solid #161E2B; }
section[data-testid="stSidebar"] > div { padding-top:1.4rem; }
.brand { padding:0 .25rem 1.3rem .25rem; margin-bottom:.4rem;
         border-bottom:1px solid #161E2B; }
.brand .n { font-size:1.02rem; font-weight:660; color:#E8EDF5;
            display:flex; align-items:center; gap:.45rem; }
.brand .s { font-size:.7rem; color:#5C6779; margin-top:.25rem;
            letter-spacing:.03em; }
.navgroup { font-size:.62rem; font-weight:720; letter-spacing:.14em;
            text-transform:uppercase; color:#4E5868;
            margin:1.5rem .3rem .55rem .3rem; }

section[data-testid="stSidebar"] div[data-testid="stButton"] > button {
    background:transparent; border:1px solid transparent; color:#8794A8;
    border-radius:9px; font-size:.855rem; font-weight:500;
    padding:.54rem .75rem; text-align:left; width:100%;
    justify-content:flex-start; transition:all 160ms ease; }
section[data-testid="stSidebar"] div[data-testid="stButton"] > button:hover {
    background:#141B27; color:#D6DEEA; border-color:#1E2836; }
section[data-testid="stSidebar"] div[data-testid="stButton"] > button:focus {
    box-shadow:none; color:#D6DEEA; }
section[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="primary"] {
    background:#3B82F614; color:#BFD5FB; border-color:#3B82F63D;
    border-left:2px solid #3B82F6; font-weight:600; }
section[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="primary"]:hover {
    background:#3B82F61F; color:#DBE7FD; }

.statuscard { background:#0D131C; border:1px solid #161E2B; border-radius:11px;
              padding:.8rem .9rem; margin-top:1.6rem; }
.statuscard .t { font-size:.6rem; font-weight:720; letter-spacing:.14em;
                 text-transform:uppercase; color:#4E5868; margin-bottom:.6rem; }
.srow { display:flex; align-items:flex-start; gap:.5rem; padding:.26rem 0; }
.srow .dot { width:6px; height:6px; border-radius:50%; margin-top:.36rem;
             flex:none; }
.srow .txt { font-size:.74rem; color:#94A0B2; line-height:1.35; }
.srow .txt .d { font-size:.68rem; color:#5C6779; }
.dot-on  { background:#22C55E; box-shadow:0 0 0 2.5px #22C55E1F; }
.dot-warn{ background:#F59E0B; box-shadow:0 0 0 2.5px #F59E0B1F; }
.dot-off { background:#EF4444; box-shadow:0 0 0 2.5px #EF44441F; }

.main div[data-testid="stButton"] > button {
    background:#161D29; color:#C2CCDB; border:1px solid #253044;
    border-radius:10px; font-size:.84rem; font-weight:500;
    padding:.58rem 1rem; transition:all 170ms ease; width:100%; }
.main div[data-testid="stButton"] > button:hover {
    background:#1C2634; border-color:#3B82F64D; color:#E8EDF5;
    transform:translateY(-1px); }
.main div[data-testid="stButton"] > button[kind="primary"] {
    background:linear-gradient(135deg,#3B82F6,#6366F1); color:#fff;
    border:none; font-weight:600; }
.main div[data-testid="stButton"] > button[kind="primary"]:hover {
    box-shadow:0 6px 20px -8px #3B82F6AA; }
.stDownloadButton > button { background:#161D29; color:#C2CCDB;
    border:1px solid #253044; border-radius:10px; font-size:.82rem;
    width:100%; }
.stDownloadButton > button:hover { border-color:#3B82F64D; color:#E8EDF5; }

.stDataFrame { border:1px solid #212B3A; border-radius:12px; overflow:hidden; }
.stTabs [data-baseweb="tab-list"] { gap:.3rem; border-bottom:1px solid #1A2230;
                                    margin-bottom:1.2rem; }
.stTabs [data-baseweb="tab"] { background:transparent; color:#6E7A8E;
    border-radius:8px 8px 0 0; padding:.55rem 1rem; font-size:.86rem;
    font-weight:500; }
.stTabs [aria-selected="true"] { color:#E8EDF5; background:#161D29; }
.streamlit-expanderHeader { background:#111721; border:1px solid #212B3A;
                            border-radius:10px; font-size:.85rem; }

.section { font-size:.66rem; font-weight:720; letter-spacing:.13em;
           text-transform:uppercase; color:#5C6779;
           margin:2.3rem 0 .9rem 0; display:flex; align-items:center;
           gap:.7rem; }
.section::after { content:""; flex:1; height:1px; background:#161E2B; }

.sqlbox { background:#0A0E16; border:1px solid #212B3A; border-radius:10px;
          padding:.8rem 1rem; font-family:ui-monospace,monospace;
          font-size:.77rem; color:#8FB0DE; white-space:pre-wrap;
          line-height:1.6; }

.compare { display:flex; align-items:stretch; }
.compare .side { flex:1; padding:1.1rem 1.3rem; }
.compare .arrow { display:flex; align-items:center; padding:0 1.1rem;
                  color:#3E4A5C; font-size:1.3rem; }
.compare .lab { font-size:.64rem; text-transform:uppercase;
                letter-spacing:.11em; font-weight:700; margin-bottom:.5rem; }
.compare .val { font-size:1.75rem; font-weight:670; line-height:1.1;
                font-variant-numeric:tabular-nums; }
.compare .sub { font-size:.74rem; color:#6E7A8E; margin-top:.3rem; }
</style>
"""


def inject() -> str:
    return CSS

def plotly_theme(fig, height: int = 380):
    """Apply the dark theme to a Plotly figure built in backend.py.

    Kept here rather than in backend.py so the chart palette and the CSS
    palette are defined in one file and cannot drift apart.
    """
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=PALETTE["text"], size=12,
                  family="system-ui, -apple-system, sans-serif"),
        title_font=dict(color=PALETTE["text"], size=15),
        margin=dict(t=64, b=48, l=54, r=24),
        height=height,
        hoverlabel=dict(bgcolor=PALETTE["surface_2"],
                        bordercolor=PALETTE["border"],
                        font_color=PALETTE["text"]),
        legend=dict(bgcolor="rgba(0,0,0,0)",
                    font=dict(color=PALETTE["text_2"], size=11)),
    )
    fig.update_xaxes(gridcolor="#161E2B", zerolinecolor="#161E2B",
                     linecolor="#212B3A",
                     tickfont=dict(color=PALETTE["muted"], size=11),
                     title_font=dict(color=PALETTE["muted"], size=11))
    fig.update_yaxes(gridcolor="#161E2B", zerolinecolor="#161E2B",
                     linecolor="#212B3A",
                     tickfont=dict(color=PALETTE["muted"], size=11),
                     title_font=dict(color=PALETTE["muted"], size=11))
    return fig