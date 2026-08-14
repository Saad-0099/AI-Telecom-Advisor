markdown
# Telecom Decision Intelligence Platform

An LLM-powered decision support system built on telecom customer data.
Where a traditional BI dashboard answers *"What happened?"*, this system
answers *why*, *what to do next*, and *which customers to target* — with
every number computed in SQL and verified before it reaches the user.

**Stack:** Python 3.13 · pandas · SQLAlchemy · SQLite · FastAPI · Streamlit
· Groq (Llama 3.3 70B)

---

## The governing principle

> **The LLM never produces a number.**

Every figure — churn rate, revenue, segment size, scenario outcome — is
computed in SQL or Python. The model receives finished results as structured
input and only *narrates, explains, and recommends*. A post-response
validator then checks every figure in the generated text against the payload
that produced it, before display.

This matters because a fabricated number reads exactly as fluently as a real
one. Separating computation from narration, and verifying the boundary, is
what makes the output trustworthy.

---

## Three kinds of number, marked differently

The platform never lets these blur together. The distinction is carried by
colour, badge and banner in the UI, by dagger and tinted rows in the PDF,
and by a `data_origin` marker inside every payload.

| | Meaning | UI colour |
|---|---|---|
| **OBSERVED** | measured from the source data | blue |
| **SIMULATED** | generated history; structure only, churn flat by construction | amber |
| **PROJECTED** | hypothetical, resting on stated assumptions | violet |

The guardrail validator enforces this at generation time: a temporal claim
from simulated data is rejected unless the prose itself discloses the
simulation, and a scenario result is rejected unless framed as a
hypothetical.

---

## Key findings

**1. Churn is a cliff, not a slope — three times over.**

| Driver | Threshold | Below | Above | Customers |
|---|---|---|---|---|
| Customer service calls | 4 or more | 11.3% | **51.7%** | 267 |
| International plan | subscribed | 11.5% | **42.4%** | 323 |
| Daytime charge | ≥ $45 | ~11% | **60.0%** | 210 |

All three are thresholds. Modelling any of them as a linear predictor finds
almost nothing — day charge in particular *dips* from 11.6% to 8.1% before
exploding at $45.

**2. A six-segment risk model falls out of those drivers.**

| Segment | Customers | Churn % |
|---|---|---|
| Critical — all three drivers | 1 | 100.0% |
| Severe — 4+ calls plus a second driver | 42 | 66.7% |
| Severe — heavy daytime usage | 194 | 59.3% |
| High — 4+ service calls only | 224 | 48.7% |
| Elevated — international plan only | 267 | 37.8% |
| Baseline — no drivers | 2,605 | 5.0% |

Segments are mutually exclusive and exhaustive: counts sum to 3,333 and
revenue to $198,146.03.

**3. Tenure does *not* predict churn** (13.1%–15.3% across cohorts). This was
expected to be a driver and is not — a genuine finding, and a reason to
target by behaviour rather than by how long someone has been a customer.

---

## A design constraint worth understanding

**The dataset has no time dimension.** Twenty-one columns and not one is a
date. `total_day_minutes` is a lifetime aggregate; `churn = True` doesn't say
*when*.

This makes any *"X increased/decreased"* statement uncomputable. An LLM asked
for a "weekly report" will happily write *"revenue increased 5% this quarter"*
because it's a fluent sentence — it has no way to know the number is invented.

Rather than fabricate dates, the system uses **tenure cohorts** as the ordered
axis, and enforces the constraint at four levels:

1. No date column exists in any real table or view (asserted by a test)
2. Constraint metadata ships inside every LLM payload
3. The validator rejects any response asserting change over time
4. Text-to-SQL cannot reach the simulated views at all

Asked *"How did churn change compared to last quarter?"*, the system
generates `SELECT 'no time dimension' AS answer FROM v_kpi_summary LIMIT 1`
and explains why no comparison is possible.

---

## Architecture

telecom_churn.csv
│
▼
etl.py ──────────────► 6 normalised tables (no date columns)
│ │
│ ▼
│ simulate_history.py ──► customer_snapshot_simulated
│ (SIMULATED, flat by design)
▼
views.sql ───────────► 12 real views + 4 simulated views
│
▼
metrics.py ──────────► JSON payloads carrying provenance metadata
│
┌───┴──────────┬──────────────┬───────────────┬──────────────┐
▼ ▼ ▼ ▼ ▼
charts.py recommend.py scenario.py text_to_sql.py report_pdf.py
(2 renderers) (rules engine) (what-if) (NL → SQL) (PDF)
│ │ │ │ │
└──────────────┴──────┬───────┴───────────────┴──────────────┘
▼
narrate.py · LLM explains, never computes
▼
guardrails.py · every figure checked
▼
FastAPI (api.py) + Streamlit (app/)


---

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file in the project root:

LLM_PROVIDER=groq
GROQ_API_KEY=gsk_your_key_here
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_SQL_MODEL=llama-3.3-70b-versatile
LLM_SQL_MAX_TOKENS=500
LLM_MIN_INTERVAL=3


Get a free key at [console.groq.com](https://console.groq.com) — no card
required. Set `LLM_PROVIDER=mock` to run the entire system offline with no
API calls.

Place `telecom_churn.csv` in `data/`, then:

```bash
python run.py all      # ETL → simulate → views → all 8 check suites
```

---

## Commands

| Command | What it does | Cost |
|---|---|---|
| `python run.py all` | full rebuild + every check | free |
| `python run.py check` | all 8 offline suites | free |
| `python run.py ui` | Streamlit interface | free |
| `python run.py api` | FastAPI + Swagger docs | free |
| `python run.py report` | generate the PDF | ~15 calls |
| `python run.py charts` | list chart definitions | free |
| `python run.py export` | write chart PNGs | free |
| `python run.py evals --live` | adversarial LLM evals | ~8 calls |
| `python run.py sqlevals --live` | SQL pipeline evals | ~20 calls |

The interface opens on `http://localhost:8501`; the API docs on
`http://127.0.0.1:8000/docs`.

---

## Interface

Nine pages, grouped in the sidebar:

**Analytics** — Executive Overview · Churn Analytics · Customer Segments ·
Cohort Analysis · Revenue & Risk
**AI & Decisions** — AI Business Advisor · What-If Lab
**Reporting** — Executive Report
**System** — Data Quality

Worth demonstrating in this order:

1. **Executive Overview** — KPIs, risk segmentation, the three drivers
2. **AI Business Advisor** — ask *"how did churn change last quarter?"* and
   watch it decline rather than invent a trend
3. **What-If Lab** — a hypothetical answered as a *range*, not a number
4. **Data Quality** — reconciliation, live system status, 183 checks

---

## Testing

```bash
python run.py check          # 8 suites, ~183 checks, offline and free
```

| Suite | Covers |
|---|---|
| Phase 1 — data | CSV reconciles to the database to the cent |
| Phase 2 — views | revenue agrees across eight independent view paths |
| Phase 6 — rules | segments exhaustive and non-overlapping |
| Phase 6.5 — simulated | panel is flat, reconciles, and is not monotonic |
| Phase 4 — guardrails | fabricated figures and temporal claims caught |
| Phase 5 — SQL guard | 13 injection patterns blocked |
| Phase 7 — report | three number types disclosed |
| Phase 8 — scenarios | results banded and framed as hypotheticals |

Live suites (`--live`) exercise the real model and consume quota.

---

## Security

Three independent layers protect the database from generated SQL:

1. **Static validation** — single statement, `SELECT` only, keyword blocklist
2. **Read-only connection** — SQLite opened `mode=ro`; writes fail at the
   driver with *"attempt to write a readonly database"*
3. **View allowlist** — only the 12 curated views are reachable

Layer 2 is the one that actually guarantees safety; a parser can always be
fooled. Layers 1 and 3 fail fast with useful messages and keep the model on
the curated metrics so generated answers stay consistent with the dashboard.

Secrets live in `.env`, which is gitignored.

---

## Project structure

run.py entry point for every command

src/
config.py paths, cohort boundaries, column aliases
models.py SQLAlchemy schema (6 tables)
etl.py CSV → clean → feature-engineer → load
simulate_history.py seeded monthly panel (SIMULATED)
views.sql 12 real metric views
views_simulated.sql 4 simulated-history views
build_views.py idempotent view builder
metrics.py query layer, JSON payloads with provenance
chart_specs.py 12 chart definitions, one source of truth
charts.py matplotlib + plotly renderers, AI explainer
llm_provider.py provider abstraction (mock / groq / ollama)
prompts.py system prompt encoding every data constraint
guardrails.py post-response validator
narrate.py narration pipeline
sql_guard.py SQL validation + allowlist
text_to_sql.py natural language → SQL → execute → narrate
rules.py recommendation rules + economic assumptions
recommend.py rules engine, target lists, reconciliation
scenario.py what-if levers with efficacy bands
report_content.py report assembly (no rendering)
report_pdf.py PDF rendering (no content decisions)
api.py FastAPI service

app/
app.py Streamlit entry point and routing
components/
styles.py CSS design system
ui.py shared card / KPI / badge primitives
navigation.py grouped sidebar
backend.py THE ONLY module importing from src/
pages_analytics.py overview, churn, segments, cohorts, revenue
pages_ai.py advisor, scenario lab, report, validation

tests/ 8 offline check suites
data/ CSV + generated SQLite database
exports/ chart PNGs and generated PDFs


---

## Model selection

Role-based model routing was built so narration and SQL generation could use
different models, then **measured rather than assumed**:

| Model | valid SQL | correct view | seconds |
|---|---|---|---|
| Llama 3.3 70B | 20/20 | 18/20 | 90 |
| Qwen 3.6 27B | 0/20 | 0/20 | 451 |

Result: single model for both roles. The abstraction was kept — it cost
fifteen lines and it's what made the measurement possible.

**Caveat:** Qwen's score reflects a token budget tuned for a non-reasoning
model, which truncated its output mid-thought. The honest reading is *"Qwen
underperformed under this configuration"*, not *"Qwen can't write SQL."*

---

## Known limitations

- **No time dimension.** The dataset supports segment comparison only, not
  trend analysis or forecasting.
- **Simulated history shows structure, not trends.** The monthly panel is
  generated from the snapshot, so it contains no information the snapshot did
  not already hold. Churn in it is deliberately flat — a trend there would
  reflect the generator's random seed.
- **Projected values rest on assumptions.** Save rate, contact cost and
  acquisition cost are industry-typical placeholders; the dataset has no cost
  data. Every projection is reported with a sensitivity band.
- **Scenarios are hypotheticals, not forecasts.** The data is observational:
  flagged customers churn more, but removing the flag may not remove the
  churn. Results carry an explicit efficacy assumption and are reported as a
  range.
- **State-level results are noisy.** 51 states averaging 65 customers each.
  Geography is the weakest dimension in this data.
- **The guardrail validator uses phrase matching**, which cannot fully
  distinguish *asserting* a claim from *refuting* one. Six false positives
  were found and fixed during development; no false negative has been
  observed. The failure mode is conservative — it blocks good answers rather
  than passing bad ones.
- **Free tier limits.** ~100,000 Groq tokens per day, roughly 100–200 `/ask`
  calls or 6–8 full reports.

---

## Roadmap

| Phase | Status |
|---|---|
| 0 — Scope lock, metric definitions | Complete |
| 1 — Data engineering | Complete |
| 2 — Deterministic metrics layer | Complete |
| 3 — Charts with AI explanation | Complete |
| 4 — LLM narration + guardrails | Complete |
| 5 — Text-to-SQL | Complete |
| 6 — Rule-based recommendation engine | Complete |
| 6.5 — Simulated history panel | Complete |
| 7 — Executive PDF report | Complete |
| 8 — Scenario analysis | Complete |
| 9 — Streamlit interface | Complete |
| 10 — Churn risk model + SHAP | Planned |

Phase 10 adds per-customer risk scoring with SHAP explanations narrated by
the LLM — the answer to *"which customers will churn"*, which the current
segment model deliberately does not claim to provide.

---

## Data source

[Telecom Churn Dataset](https://www.kaggle.com/datasets/mnassrib/telecom-churn-datasets)
— 3,333 customers, 21 columns, single snapshot.