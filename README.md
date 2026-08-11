# Telecom Decision Intelligence Platform

An LLM-powered decision support system built on telecom customer data.
Where a traditional BI dashboard answers *"What happened?"*, this system
answers *why*, *what to do next*, and *which customers to target* — with
every number computed in SQL and verified before it reaches the user.

**Stack:** Python 3.13 · pandas · SQLAlchemy · SQLite · FastAPI · Groq (Llama 3.3 70B)

---

## The governing principle

> **The LLM never produces a number.**

Every figure — churn rate, revenue, segment size — is computed in SQL. The
model receives computed results as structured input and only *narrates,
explains, and recommends*. A post-response validator then checks every
figure in the generated text against the payload that produced it.

This matters because a fabricated number reads exactly as fluently as a real
one. Separating computation from narration, and verifying the boundary, is
what makes the output trustworthy.

---

## Key findings from the data

**1. Churn is a cliff, not a slope.**

| Service calls | Customers | Churn |
|---|---|---|
| 0–3 | 3,066 | 11.3% |
| 4+ | 267 | **51.7%** |

Churn is flat from 0 to 3 calls, then jumps sharply. Modelling this as a
linear relationship would badly misrepresent it.

**2. A four-segment risk model falls out of two drivers.**

| Segment                        | Customers | Churn %   | Revenue at risk |
|--------------------------------|-----------|-----------|-----------------|
| Critical: 4+ calls + intl plan | 28        | **67.9%** | $1,090          |
| High: 4+ service calls         | 239       | **49.8%** | $6,375          |
| Elevated: intl plan            | 295       | **40.0%** | $7,493          |
| Baseline                       | 2,771     | 8.2%      | $16,609         |

**3. Tenure does *not* predict churn** (13.1%–15.3% across cohorts). This
was expected to be a driver and was not — a genuine analytical finding, and
one the system is explicitly guarded against misreporting.

---

## Architecture

```
        telecom_churn.csv
               │
               ▼
        etl.py  ──────────────►  6 normalised tables
               │                 (no date columns — see below)
               ▼
        views.sql  ───────────►  11 SQL views
               │                 every metric defined here
               ▼
        metrics.py  ──────────►  JSON payloads + constraint metadata
               │
        ┌──────┴───────────────────────────┐
        ▼                                  ▼
   api.py (FastAPI)              text_to_sql.py
   deterministic endpoints       question → SQL → guard → read-only exec
        │                                  │
        └──────────────┬───────────────────┘
                       ▼
                  narrate.py
              LLM explains the rows
                       │
                       ▼
                guardrails.py
        every figure checked against the payload
                       │
                       ▼
                    answer
```

---

## A design constraint worth understanding

**The dataset has no time dimension.** Twenty-one columns and not one is a
date — no signup date, no billing month, no churn date. `total_day_minutes`
is a lifetime aggregate; `churn = True` doesn't say *when*.

This makes any *"X increased/decreased"* statement uncomputable. An LLM
asked for a "weekly report" will happily write *"revenue increased 5% this
quarter"* because it's a fluent sentence — it has no way to know the number
is invented.

Rather than fabricate dates, the system uses **tenure cohorts** as the
ordered analytical axis, and enforces the constraint at three levels:

1. No date column exists in any table or view (asserted by a test)
2. Constraint metadata ships inside every LLM payload
3. The validator rejects any response asserting change over time

Asked *"How did churn change compared to last quarter?"*, the system
generates `SELECT 'no time dimension' AS answer FROM v_kpi_summary LIMIT 1`
and explains why no comparison is possible.

---

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_your_key_here
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_SQL_MODEL=llama-3.3-70b-versatile
LLM_SQL_MAX_TOKENS=500
LLM_MIN_INTERVAL=3
```

Get a free key at [console.groq.com](https://console.groq.com) — no card
required. Set `LLM_PROVIDER=mock` to run the entire system offline with no
API calls.

Place `telecom_churn.csv` in `data/`.

---

## Running

Build the database and metrics layer:

```bash
python etl.py            # CSV → 6 normalised tables
python checks.py         # 18 reconciliation checks
python build_views.py    # 11 SQL views
python checks_views.py   # 25 reconciliation checks
```

Start the API:

```bash
uvicorn api:app --reload
```

Open **http://127.0.0.1:8000/docs**

### Endpoints worth trying

| Endpoint                                           | Shows                                       |
|----------------------------------------------------|---------------------------------------------|
| `/metrics/risk-segments`                           | the four-segment risk model, no AI involved |
| `/metrics/cohort-risk-matrix`                      | the service-call cliff in every cohort      |
| `/narrate/risk-segments`                           | the same numbers, explained in prose        |
| `/ask?question=Which state has the worst churn?`   | full text-to-SQL pipeline                   |
| `/ask?question=How did churn change last quarter?` | the model declining to invent a trend       |

The `/ask` response carries the generated SQL, the returned rows, the
narrated answer, and the validation report — the whole chain in one payload.

---

## Testing

```bash
python evals.py              # 22 guardrail cases    (offline, free)
python sql_evals.py          # 23 SQL guard cases    (offline, free)
python evals.py --live       # adversarial LLM evals (~8 API calls)
python sql_evals.py --live   # SQL pipeline          (~20 API calls)
```

88 tests run offline at zero cost. Current status: **30/30** on the
narration suite, **19/19 valid SQL** on the pipeline.

The SQL guard blocks 13 injection patterns including stacked statements,
raw table access, `PRAGMA`, `load_extension`, and `'; DROP TABLE ...; --`.

---

## Security

Three independent layers protect the database from generated SQL:

1. **Static validation** — single statement, `SELECT` only, keyword blocklist
2. **Read-only connection** — SQLite opened `mode=ro`; writes fail at the
   driver with *"attempt to write a readonly database"*
3. **View allowlist** — only the 11 curated views are reachable

Layer 2 is the one that actually guarantees safety; a parser can always be
fooled. Layers 1 and 3 fail fast with useful messages and keep the model on
the curated metrics so generated answers stay consistent with the dashboard.

Secrets live in `.env`, which is gitignored.

---

## Project structure

```
config.py          paths, cohort boundaries, column aliases
models.py          SQLAlchemy schema (6 tables)
etl.py             CSV → clean → feature-engineer → load
checks.py          18 reconciliation checks

views.sql          11 metric view definitions
build_views.py     idempotent view builder
metrics.py         query layer, JSON payloads
checks_views.py    25 reconciliation checks
api.py             FastAPI service

llm_provider.py    provider abstraction (mock / groq / ollama)
prompts.py         system prompt encoding all data constraints
guardrails.py      post-response validator
narrate.py         narration pipeline
evals.py           narration eval suite

sql_guard.py       SQL validation + allowlist
text_to_sql.py     natural language → SQL → execute → narrate
sql_evals.py       SQL guard tests + model benchmark
```

---

## Model selection

Role-based model routing was built so narration and SQL generation could use
different models, then **measured rather than assumed**:

| Model         | valid SQL | correct view | seconds |
|---------------|-----------|--------------|---------|
| Llama 3.3 70B | 20/20     | 18/20        | 90      |
| Qwen 3.6 27B  | 0/20      | 0/20         | 451     |

Result: single model (Llama 3.3 70B) for both roles. The abstraction was
kept — it cost fifteen lines and it's what made the measurement possible.

**Caveat:** Qwen's score reflects a token budget tuned for a non-reasoning
model, which truncated its output mid-thought. The honest reading is *"Qwen
underperformed under this configuration"*, not *"Qwen can't write SQL."*

---

## Known limitations

- **No time dimension** — the dataset supports segment comparison only, not
  trend analysis. See above.
- **State-level results are noisy** — 51 states averaging 65 customers each.
  Use the `min_customers` filter; the state dimension is the weakest in this
  data.
- **The guardrail validator uses phrase matching**, which cannot fully
  distinguish *asserting* a claim from *refuting* one. Four false positives
  were found and fixed during development; a fifth is plausible. Its failure
  mode is conservative — it blocks good answers rather than passing bad ones.
- **Free tier limits** — 100,000 Groq tokens per day, roughly 100–200 `/ask`
  calls.

---

## Roadmap

| Phase | Status |
|---|---|
| 0 — Scope lock, metric definitions   | Complete |
| 1 — Data engineering                 | Complete |
| 2 — Deterministic metrics layer      | Complete |
| 4 — LLM narration + guardrails       | Complete |
| 5 — Text-to-SQL                      | Complete |
| 6 — Rule-based recommendation engine | Next    |
| 3 — Power BI dashboard               | Pending |
| 7 — Executive report generator (PDF) | Pending |
| 8 — Scenario analysis                | Pending |
| 9 — Streamlit frontend               | Pending |
| 10 — Churn model + SHAP, RAG, agents | Stretch |

The dashboard was deferred deliberately: every remaining phase consumes the
metrics API rather than the dashboard, and dashboard Page 5 ("AI
recommendations") depends on Phase 6.

---

## Data source

[Telecom Churn Dataset](https://www.kaggle.com/datasets/mnassrib/telecom-churn-datasets) — 3,333 customers, 21 columns.
