"""
Phase 10 validation — churn risk model.

Run:  python run.py check        (offline, free — no LLM calls)

The checks that matter are about HONEST EVALUATION. A churn model on an
imbalanced dataset can look excellent while being useless, and the usual
way that happens is reporting accuracy against a 14.49% base rate.
"""

from __future__ import annotations

# This test lives in tests/ but imports modules from src/. Adding src/ to the
# path keeps the flat "import metrics" style working from either directory.
import pathlib as _pathlib
import sys as _sys
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent / "src"))

import sys

import guardrails
import churn_model as M

results: list[tuple[bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((passed, name))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def run() -> int:
    if not M.MODEL_PATH.exists():
        print("  no trained model. Run: python run.py train")
        return 1

    m = M.model_metrics()

    print("\n=== FEATURES ARE RAW, NOT DERIVED ===")
    # Feeding both customer_service_calls AND high_service_calls splits each
    # driver's SHAP contribution across two correlated columns and makes the
    # attributions unreadable.
    derived = {"high_service_calls", "heavy_day_usage", "risk_factor_count",
               "service_call_bucket", "cohort_label"}
    leaked = set(m["features"]) & derived
    check("no derived flags among the features", not leaked, str(leaked))
    check("target is not a feature", M.TARGET not in m["features"])
    check("customer_id is not a feature", "customer_id" not in m["features"])

    print("\n=== SPLIT IS HONEST ===")
    total = m["train_rows"] + m["test_rows"]
    check("train and test sum to the dataset", total == 3333, str(total))
    check("test set is a meaningful share",
          0.15 <= m["test_rows"] / total <= 0.35,
          f"{m['test_rows'] / total:.0%}")
    # Stratification matters: at 14.49% positives an unstratified split can
    # hand the test set a materially different churn proportion.
    check("test base rate matches the portfolio",
          abs(m["base_rate_pct"] - 14.49) < 1.5,
          f"{m['base_rate_pct']}% vs 14.49%")

    print("\n=== METRICS ARE APPROPRIATE FOR THE IMBALANCE ===")
    check("accuracy is not the selection metric",
          "accuracy" not in m["meta"]["selection_metric"].lower(),
          m["meta"]["selection_metric"])
    check("the base-rate trap is stated",
          "nobody churns" in m["meta"]["accuracy_note"].lower())
    for key, res in m["models"].items():
        check(f"{key} reports PR-AUC", "pr_auc" in res)
        check(f"{key} reports recall", "recall" in res)
        break
    # If accuracy IS shown anywhere it must be labelled as misleading.
    sample = next(iter(m["models"].values()))
    check("any accuracy figure is flagged as misleading",
          "accuracy_misleading" in sample and "accuracy" not in sample)

    print("\n=== TWO OPERATING POINTS ARE REPORTED ===")
    # The capacity point achieves high precision by construction, so
    # quoting it alone would overstate the model.
    check("balanced point reported alongside capacity",
          all("balanced_point" in r for r in m["models"].values()))
    check("threshold basis is explained",
          "capacity" in m["meta"]["threshold_basis"].lower())
    xgb = m["models"].get("xgboost", sample)
    check("capacity point does not claim full recall",
          xgb["recall"] < 1.0, f"recall {xgb['recall']}")

    print("\n=== MODELS COMPARED, NOT ASSUMED ===")
    check("at least three models compared", len(m["models"]) >= 3,
          str(list(m["models"])))
    check("a winner is named", m["winner"] in m["models"])
    check("every model reports interpretability",
          all("interpretable" in r for r in m["models"].values()))

    print("\n=== CLAIMS ARE BOUNDED ===")
    meta = m["meta"]
    check("marked as MODEL origin", meta["data_origin"] == "MODEL")
    check("states what it ranks", "resembl" in meta["claim"].lower())
    check("disclaims forecasting", "timing" in meta["not_a_claim"].lower())
    check("disclaims certainty", "verdict" in meta["not_a_claim"].lower())

    print("\n=== GUARDRAILS ON MODEL NARRATION ===")
    payload = {"meta": {"data_origin": "MODEL"}, "risk_score": 0.73,
               "contributions": [{"shap": 0.31}]}

    good = ("The score of 0.73 is driven mainly by service calls, "
            "contributing 0.31 to the score.")
    check("proper attribution passes", guardrails.validate(good, payload)["passed"])

    causal = "Service calls caused the churn, contributing 0.31."
    v = guardrails.validate(causal, payload)
    check("causal language rejected", not v["passed"],
          str([x["type"] for x in v["violations"]]))

    certain = "With a score of 0.73 this customer will churn."
    v = guardrails.validate(certain, payload)
    check("certainty language rejected", not v["passed"],
          str([x["type"] for x in v["violations"]]))

    fabricated = "Service calls contributed 0.55 to the score."
    v = guardrails.validate(fabricated, payload)
    check("fabricated SHAP value rejected", not v["passed"])

    # The relaxation must be narrow: observed payloads keep the old rules.
    obs = {"data": {"churn_rate_pct": 14.49}}
    check("observed payloads are unaffected",
          guardrails.validate("Churn is 14.49% across the portfolio.",
                              obs)["passed"])

    print("\n=== SCORING WORKS END TO END ===")
    scored = M.score_customers(limit=50)
    check("scores are probabilities",
          bool((scored.risk_score >= 0).all() and (scored.risk_score <= 1).all()))
    check("scores are sorted descending",
          bool((scored.risk_score.diff().dropna() <= 1e-9).all()))
    check("flag column present", "flagged" in scored.columns)

    print("\n=== COMPARISON SUMMARY ===")
    print(f"  {'model':<26}{'ROC-AUC':>9}{'PR-AUC':>9}"
          f"{'precision':>11}{'recall':>8}{'F1':>7}")
    for res in m["models"].values():
        b = res["balanced_point"]
        print(f"  {res['label']:<26}{res['roc_auc']:>9.4f}{res['pr_auc']:>9.4f}"
              f"{b['precision']:>11.3f}{b['recall']:>8.3f}{b['f1']:>7.3f}")
    print(f"\n  base rate {m['base_rate_pct']}% — predicting 'nobody churns' "
          f"would score {100 - m['base_rate_pct']:.2f}% accuracy")

    failed = [n for ok, n in results if not ok]
    print("\n" + "=" * 60)
    if failed:
        print(f"FAILED {len(failed)}/{len(results)}: {failed}")
        return 1
    print(f"ALL {len(results)} CHECKS PASSED — model is honestly evaluated.")
    return 0


if __name__ == "__main__":
    sys.exit(run())