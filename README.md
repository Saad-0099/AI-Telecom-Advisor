# Telecom Decision Intelligence Platform

An LLM-powered decision support system built on telecom customer data.
Where a traditional BI dashboard answers *"What happened?"*, this system
answers *why*, *what to do next*, and *which customers to target* — with
every number computed in SQL or Python and verified before it reaches the
user.

**Stack:** Python 3.13 · pandas · SQLAlchemy · SQLite · FastAPI · Streamlit
· matplotlib · Plotly · ReportLab · scikit-learn · XGBoost · SHAP · Groq
(GPT OSS 120B)

---

## The governing principle

> **The LLM never produces a number.**

Every figure — churn rate, revenue, segment size, scenario outcome, risk
score, SHAP contribution — is computed in SQL or Python. The model receives
finished results as structured input and only *narrates, explains, and
recommends*. A post-response validator then checks every figure in the
generated text against the payload that produced it, before display.

This matters because a fabricated number reads exactly as fluently as a real
one. Separating computation from narration, and verifying the boundary, is
what makes the output trustworthy.

---

## Four kinds of number, marked differently

The platform never lets these blur together. The distinction is carried by
colour and badge in the UI, by dagger and tinted rows in the PDF, and by a
`data_origin` marker inside every payload — which the guardrail reads to
decide which rules apply.

| | Meaning | Guardrail behaviour |
|---|---|---|
| **OBSERVED** | measured from the source data | no temporal claims at all |
| **SIMULATED** | generated history; structure only | structure allowed, but only when disclosed; never churn trends |
| **PROJECTED** | hypothetical, resting on stated assumptions | conditional allowed, but only when framed as a hypothetical |
| **MODEL** | risk scores and SHAP attributions | *adds* rules: no causal language, no certainty language |

Enforcement is structural, not documentary. Origin is detected from the
payload itself, never passed in as a flag, so a caller cannot accidentally
unlock relaxed rules for real snapshot data.

---

## Key findings

**1. Churn is a cliff, not a slope — three times over.**

| Driver | Threshold | Below | Above | Customers |
|---|---|---|---|---|
| Customer service calls | 4 or more | 11.3% | **51.7%** | 267 |
| International plan | subscribed | 11.5% | **42.4%** | 323 |
| Daytime charge | >= $45 | 11.4% | **60.0%** | 210 |

Modelling any of them as a linear predictor finds almost nothing — day
charge *dips* from 11.6% to 8.1% before exploding at $45.

The third driver was not found by inspection. It surfaced in Phase 6 when a
rule intended to identify a *safe* high-spend segment returned 73% churn.
Phase 2's original segmentation had reported a "baseline" of 8.2% — a figure
concealing 166 customers churning at 59%.

**2. Drivers compound, producing a six-segment ladder.**

| Drivers present | Customers | Churn rate |
|---|---|---|
| 0 | 2,605 | **5.0%** |
| 1 | 657 | **46.9%** |
| 2 | 70 | **64.3%** |
| 3 | 1 | 100% |

Segments are mutually exclusive and exhaustive: counts sum to 3,333 and
revenue to $198,146.03. Every segment exports a list of real customer IDs.

**3. Tenure does *not* predict churn** (13.1%–15.3% across cohorts). This was
expected to be a driver and is not — a genuine finding, and a reason to
target by behaviour rather than by how long someone has been a customer.

**4. No single intervention moves the needle much.** At the central efficacy
assumption, the best scenario lever yields 0.39 percentage points, and all
three land within 0.03 of each other. The flagged groups are small (210–323
of 3,333), so a large relative improvement is a small absolute one.

**5. The three drivers explain roughly 70% of churn — the rest is invisible.**
See the model section below. This is the most useful conclusion in the
project, because it says what data to go and collect.

---

## A design constraint worth understanding

**The dataset has no time dimension.** Twenty-one columns and not one is a
date. `total_day_minutes` is a lifetime aggregate; `churn = True` doesn't say
*when*.

This makes any *"X increased/decreased"* statement uncomputable. An LLM asked
for a "weekly report" will invent one because it reads as a fluent sentence.

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

## The churn risk model

Phase 10 answers *"which customers are at risk"* — which cross-sectional
data can support — while continuing to refuse *"when"* and *"is churn
rising"*, which it cannot.

### Three algorithms, measured rather than assumed

| Model | ROC-AUC | PR-AUC | Precision | Recall | F1 | Interpretability |
|---|---|---|---|---|---|---|
| Logistic regression | 0.820 | 0.431 | 0.380 | 0.703 | 0.493 | coefficients |
| Decision tree (depth 4) | 0.888 | 0.809 | **1.000** | 0.711 | **0.831** | fits on one page |
| XGBoost | 0.888 | **0.833** | **1.000** | 0.711 | **0.831** | SHAP per customer |

Metrics at the balanced operating point. **Accuracy is not reported**: at a
14.49% base rate, predicting "nobody churns" scores 85.51%.

**The depth-4 tree matches XGBoost.** Three thresholds capture essentially
all the signal, and a model you can print on one page performs as well as a
boosted ensemble.

### The model rediscovered the rules engine

Trained on **raw columns only** — the derived flags (`high_service_calls`,
`heavy_day_usage`, `risk_factor_count`) were excluded, because feeding both
raw and thresholded forms splits each driver's SHAP contribution across two
correlated columns and makes the attributions unreadable.

The tree's own splits came out as:

```
customer_service_calls <= 3.50
international_plan     <= 0.50
total_charge           <= 74.03
```

Those are the rules engine's cut points, found independently from data the
model was never told about. SHAP's top three global contributors are
`total_charge` (25%), `international_plan` (17%) and
`customer_service_calls` (15%) — the same three.

### Why some churners are missed

The model separates the groups sharply — churners average a score of 0.888,
retained customers 0.071. But 149 of 483 churners fall below the flagging
threshold, and the reason is worth stating precisely.

| | Missed churners | Caught churners | Retained |
|---|---|---|---|
| Service calls | **1.60** | 2.51 | 1.45 |
| Day charge | **30.59** | 37.22 | 29.78 |
| Carry no driver | **44%** | 19% | 87% |

**Missed churners look almost exactly like retained customers.** Of the 149,
129 sit in the zero-driver group — 2,605 customers with a 5.0% churn rate —
where churners and non-churners have *identical* mean service calls (1.31
each).

No algorithm can separate these, because the 21 columns do not contain
whatever caused them to leave: a competitor offer, a move, a price rise, an
experience never logged as a call.

That is an information limit, not a tuning failure. **The honest ceiling is
around 70% recall at usable precision**, and getting past it needs different
data rather than a better model.

### What the model does and does not claim

It ranks customers by resemblance to those who already churned. It says
nothing about *when* anyone will leave, and a high score is a probability,
not a verdict. The guardrail enforces this: `will churn` and
`caused the churn` are both rejected on MODEL payloads.

The **action does not come from the model.** SHAP says why a customer scored
high; `rules.py` says what to do about each driver. Keeping those separate
means the recommendation stays deterministic even though the score is
probabilistic.

---

## Architecture

```
telecom_churn.csv
       |
       v
   etl.py --------------->  6 normalised tables  (no date columns)
       |                            |
       |                            v
       |                 simulate_history.py ---> customer_snapshot_simulated
       |                                           (SIMULATED, flat by design)
       v
   views.sql ------------>  12 real views  +  4 simulated views
       |
       v
   metrics.py ----------->  JSON payloads carrying provenance metadata
       |
   +---+--------+-----------+--------------+--------------+-------------+
   v            v           v              v              v             v
 charts.py  recommend.py scenario.py  text_to_sql.py  report_pdf.py churn_model.py
                                                                        |
                                                                  churn_explain.py
                                                                    (SHAP)
   |            |           |              |              |             |
   +------------+-----+-----+--------------+--------------+-------------+
                      v
                 narrate.py  ·  LLM explains, never computes
                      v
                guardrails.py  ·  every figure checked
                      v
           FastAPI (api.py)   +   Streamlit (app/)
```

---

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_your_key_here
GROQ_MODEL=openai/gpt-oss-120b
GROQ_SQL_MODEL=openai/gpt-oss-120b
LLM_TEMPERATURE=0
LLM_MAX_TOKENS=1200
LLM_SQL_MAX_TOKENS=2500
LLM_MIN_INTERVAL=3
```

Get a free key at [console.groq.com](https://console.groq.com) — no card
required. Set `LLM_PROVIDER=mock` to run the entire system offline with no
API calls.

`LLM_TEMPERATURE=0` is deliberate: every call in this project is factual
narration of pre-computed figures, so sampling randomness buys nothing and
costs reproducibility.

Place `telecom_churn.csv` in `data/`, then:

```bash
python run.py all      # ETL -> simulate -> views -> all 9 check suites
python run.py train    # train and compare the three risk models
```

---

## Commands

| Command | What it does | Cost |
|---|---|---|
| `python run.py all` | full rebuild + every offline check | free |
| `python run.py check` | all 9 offline suites (~215 checks) | free |
| `python run.py train` | train and compare three risk models | free |
| `python run.py ui` | Streamlit interface | free |
| `python run.py api` | FastAPI + Swagger docs | free |
| `python run.py report` | generate the PDF | ~15 calls |
| `python run.py charts` | list chart definitions | free |
| `python run.py export` | write chart PNGs | free |
| `python run.py evals --live` | adversarial LLM evals | ~8 calls |
| `python run.py sqlevals --live` | SQL pipeline evals | ~20 calls |
| `python run.py sqlevals --compare` | benchmark two models | ~40 calls |

The interface opens on `http://localhost:8501`; the API docs on
`http://127.0.0.1:8000/docs`.

---

## Interface

Ten pages, grouped in the sidebar:

**Analytics** — Executive Overview · Churn Analytics · Customer Segments ·
Cohort Analysis · Revenue & Risk
**AI & Decisions** — AI Business Advisor · What-If Lab · Churn Risk Model
**Reporting** — Executive Report
**System** — Data Quality

Worth demonstrating in this order:

1. **Executive Overview** — KPIs, risk segmentation, the three drivers
2. **AI Business Advisor** — ask *"how did churn change last quarter?"* and
   watch it decline rather than invent a trend; each answer shows how many
   figures were verified against the query result
3. **What-If Lab** — a hypothetical answered as a *range*, not a number
4. **Churn Risk Model** — pick a customer, see the score, the SHAP
   breakdown, and the deterministic action. Then filter to *Retained + High*
   to see a false alarm, or open a missed churner to see why the model had
   nothing to go on
5. **Data Quality** — reconciliation, live system status, 215 checks

---

## Testing

```bash
python run.py check          # 9 suites, ~215 checks, offline and free
```

| Suite | Covers |
|---|---|
| Phase 1 — data | CSV reconciles to the database to the cent |
| Phase 2 — views | revenue agrees across nine independent view paths |
| Phase 6 — rules | segments exhaustive and non-overlapping |
| Phase 6.5 — simulated | panel is flat, reconciles, and is not monotonic |
| Phase 4 — guardrails | fabricated figures and temporal claims caught |
| Phase 5 — SQL guard | 13 injection patterns blocked |
| Phase 7 — report | three number types disclosed |
| Phase 8 — scenarios | results banded and framed as hypotheticals |
| Phase 10 — risk model | no derived features, honest metrics, bounded claims |

Live suites (`--live`) exercise the real model and consume quota.

The regression cases are the valuable part: several encode *actual* model
outputs that the validator wrongly rejected, so a future change cannot
silently reintroduce a false positive.

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

```
run.py                  entry point for every command

src/
  config.py             paths, cohort boundaries, column aliases
  models.py             SQLAlchemy schema (6 tables)
  etl.py                CSV -> clean -> feature-engineer -> load
  simulate_history.py   seeded monthly panel (SIMULATED)
  views.sql             12 real metric views
  views_simulated.sql   4 simulated-history views
  build_views.py        idempotent view builder
  metrics.py            query layer, JSON payloads with provenance
  chart_specs.py        12 chart definitions, one source of truth
  charts.py             matplotlib + plotly renderers, AI explainer
  llm_provider.py       provider abstraction (mock / groq / ollama)
  prompts.py            system prompt encoding every data constraint
  guardrails.py         post-response validator, four payload origins
  narrate.py            narration pipeline
  sql_guard.py          SQL validation + allowlist
  text_to_sql.py        natural language -> SQL -> execute -> narrate
  rules.py              recommendation rules + economic assumptions
  recommend.py          rules engine, target lists, reconciliation
  scenario.py           what-if levers with efficacy bands
  churn_model.py        three algorithms, stratified split, honest metrics
  churn_explain.py      SHAP attribution + three levels of AI explanation
  report_content.py     report assembly (no rendering)
  report_pdf.py         PDF rendering (no content decisions)
  api.py                FastAPI service

app/
  app.py                Streamlit entry point and routing
  components/
    styles.py           CSS design system
    ui.py               shared card / KPI / badge primitives
    navigation.py       grouped sidebar
    backend.py          THE ONLY module importing from src/
    pages_analytics.py  overview, churn, segments, cohorts, revenue
    pages_ai.py         advisor, scenario lab, risk model, report, quality

tests/                  9 offline check suites
data/                   CSV + generated SQLite database
models/                 trained model + metrics JSON
exports/                chart PNGs and generated PDFs
```

---

## Model selection, and a forced migration

Role-based model routing was built so narration and SQL generation could use
different models, then **measured rather than assumed**:

| Model | valid SQL | correct view | retries | seconds |
|---|---|---|---|---|
| Llama 3.3 70B | 20/20 | 18/20 | 2 | 90 |
| Qwen 3.6 27B | 0/20 | 0/20 | 20 | 451 |
| **GPT OSS 120B** | **20/20** | **19/20** | **0** | 98 |

**Qwen's result was invalid**, and worth stating plainly: the token budget
was tuned for a non-reasoning model, so Qwen was truncated mid-thought on
every call. That is a configuration bug, not a capability verdict.

**Groq decommissioned Llama 3.3 70B on 16 August 2026** with roughly two
weeks' notice. Migration was a two-line `.env` change plus a re-run of the
eval suites, because `llm_provider.py` deliberately imports no vendor SDK and
reads every model name from configuration. The replacement scored *better* —
19/20 on view selection with zero retries, at roughly a quarter of the prompt
cost.

The migration also exposed two latent bugs that no amount of testing against
a single model would have found:

- A **regex that split `$29,016.57` into a phantom `16.57`**, because the
  thousands-separator branch had no provision for a trailing decimal. Llama's
  formatting happened never to take that path. A validator tuned against one
  model's output style carries hidden assumptions about that style.
- A **prompt that invited computation** — "say how much churn changes across
  the threshold" is practically a request to subtract, and the model
  occasionally obliged with a figure the payload did not contain. The
  validator caught it, which is the system working rather than failing.

---

## Known limitations

- **No time dimension.** The dataset supports segment comparison only, not
  trend analysis or forecasting.
- **Simulated history shows structure, not trends.** The monthly panel is
  generated from the snapshot, so it contains no information the snapshot did
  not already hold. Churn in it is deliberately flat — a trend there would
  reflect the generator's random seed rather than customer behaviour.
- **Projected values rest on assumptions.** Save rate, contact cost and
  acquisition cost are industry-typical placeholders; the dataset has no cost
  data. Every projection is reported with a sensitivity band.
- **Scenarios are hypotheticals, not forecasts.** The data is observational:
  flagged customers churn more, but removing the flag may not remove the
  churn. Results carry an explicit efficacy assumption and are reported as a
  range, never as a single number.
- **Risk scores rank, they do not forecast.** The model was trained on a
  single snapshot and validated on a held-out quarter of it. It says nothing
  about when anyone will leave, and roughly 30% of churners are invisible to
  it because the dataset does not contain what drove them away.
- **State-level results are noisy.** 51 states averaging 65 customers each.
  Geography is the weakest dimension in this data.
- **The guardrail validator uses phrase matching**, which cannot fully
  distinguish *asserting* a claim from *refuting*, *comparing*, or
  *prescribing* one. Eight false positives were found and fixed during
  development — two of them only after changing models. No false negative has
  been observed. The failure mode is conservative: it blocks good answers
  rather than passing bad ones. A stronger design would use entailment
  checking or a second model as judge.
- **Free tier limits.** 1,000 requests/day and 8,000 tokens/minute on Groq —
  roughly 60 full reports or 300 conversational questions per day.

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
| 10 — Churn risk model + SHAP | Complete |

All planned phases are built. Natural extensions, none of them started:
retrieval over policy documents, an agent workflow that chains the existing
tools, and — the one that would matter most — ingesting real monthly billing
records so the trend questions the platform currently refuses become
answerable.

---

## Data source

[Telecom Churn Dataset](https://www.kaggle.com/datasets/mnassrib/telecom-churn-datasets)
— 3,333 customers, 21 columns, single snapshot.