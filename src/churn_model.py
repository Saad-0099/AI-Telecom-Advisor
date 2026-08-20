"""
Phase 10 — churn risk model.

WHAT THIS DOES AND DOES NOT CLAIM
---------------------------------
It ranks customers by how closely they resemble those who already churned.
It does NOT forecast timing, and a high score is not a verdict — the model
was trained on a single snapshot and validated on a held-out portion of it.

"Which customers are at risk" is answerable from cross-sectional data.
"When will they leave" and "is churn rising" are not, and nothing here
attempts them.

FEATURE SELECTION — the leakage trap
------------------------------------
v_churn_features contains BOTH raw columns and thresholded flags derived
from them: high_service_calls from customer_service_calls, heavy_day_usage
from day_charge, risk_factor_count from all three.

Feeding both forms is not leakage in the strict sense — the flags contain
no target information — but it splits each driver's SHAP contribution
across two correlated columns and makes the attributions unreadable.
RAW features only. The model rediscovers the thresholds on its own, which
is the more interesting result anyway: if it lands near 4 calls and $45,
that independently confirms the rules engine.

METRICS
-------
Accuracy is not reported. At a 14.49% base rate, predicting "nobody
churns" scores 85.51% and is useless. Precision, recall, F1 and ROC-AUC
only.

Run:  python run.py train
"""

from __future__ import annotations

import json
import logging
import pickle
import sys

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, confusion_matrix,
                             f1_score, precision_recall_curve,
                             precision_score, recall_score, roc_auc_score)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier, export_text
from sqlalchemy import text

import config as C
import metrics

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s",
                    stream=sys.stdout)
log = logging.getLogger("model")

RANDOM_SEED = 42
TEST_SIZE = 0.25

MODEL_DIR = C.PROJECT_ROOT / "models"
MODEL_PATH = MODEL_DIR / "churn_model.pkl"
METRICS_PATH = MODEL_DIR / "model_metrics.json"

# RAW features only — see the module docstring on why the derived flags
# are excluded.
FEATURES = [
    "account_length_days",
    "international_plan",
    "voice_mail_plan",
    "customer_service_calls",
    "day_charge",
    "intl_charge",
    "total_charge",
    "total_minutes",
]

TARGET = "churned"

# A retention team can work a few hundred customers, not a few thousand.
# The operating threshold is chosen to flag roughly this many, rather than
# using the default 0.5 which optimises a metric nobody acts on.
TARGET_FLAG_COUNT = 450


# ==========================================================================
def load_data() -> pd.DataFrame:
    return pd.read_sql(text("SELECT * FROM v_churn_features"),
                       metrics.engine())


def split(df: pd.DataFrame):
    """Stratified split. At a 14.49% base rate an unstratified split can
    easily hand the test set a materially different churn proportion."""
    X = df[FEATURES]
    y = df[TARGET]
    return train_test_split(X, y, test_size=TEST_SIZE,
                            stratify=y, random_state=RANDOM_SEED)


# ==========================================================================
def build_models() -> dict:
    """Three candidates, spanning interpretability and power.

    Logistic regression is the baseline every churn study should report.
    A single decision tree is included because this dataset's drivers are
    THRESHOLDS, which is precisely what a tree represents natively — it may
    genuinely compete, and a tree that fits on one slide is more useful to
    a business than an ensemble that does not.
    """
    from xgboost import XGBClassifier

    return {
        "logistic_regression": {
            "label": "Logistic regression",
            "model": LogisticRegression(max_iter=2000,
                                        class_weight="balanced",
                                        random_state=RANDOM_SEED),
            "scale": True,
            "interpretable": "coefficients readable directly",
        },
        "decision_tree": {
            "label": "Decision tree (depth 4)",
            "model": DecisionTreeClassifier(max_depth=4, min_samples_leaf=25,
                                            class_weight="balanced",
                                            random_state=RANDOM_SEED),
            "scale": False,
            "interpretable": "full logic fits on one page",
        },
        "xgboost": {
            "label": "XGBoost",
            "model": XGBClassifier(
                n_estimators=220, max_depth=4, learning_rate=0.08,
                subsample=0.85, colsample_bytree=0.85,
                eval_metric="logloss", random_state=RANDOM_SEED,
                # Counter the 5.9:1 imbalance rather than letting the model
                # learn that predicting "no churn" is usually right.
                scale_pos_weight=5.9),
            "scale": False,
            "interpretable": "SHAP attributions per customer",
        },
    }


def evaluate(y_true, proba, threshold: float) -> dict:
    pred = (proba >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, pred).ravel()
    return {
        "threshold": round(float(threshold), 4),
        "roc_auc": round(float(roc_auc_score(y_true, proba)), 4),
        "pr_auc": round(float(average_precision_score(y_true, proba)), 4),
        "precision": round(float(precision_score(y_true, pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y_true, pred, zero_division=0)), 4),
        "flagged": int(tp + fp),
        "true_positives": int(tp),
        "false_positives": int(fp),
        "false_negatives": int(fn),
        # Reported ONLY to show why it is the wrong metric here.
        "accuracy_misleading": round(float((tp + tn) / len(y_true)), 4),
    }


def _best_f1(y_true, proba) -> dict:
    """The threshold maximising F1 — what the model can do when recall
    matters as much as precision, rather than being capped by capacity."""
    prec, rec, thr = precision_recall_curve(y_true, proba)
    f1 = np.divide(2 * prec * rec, prec + rec,
                   out=np.zeros_like(prec), where=(prec + rec) > 0)
    i = int(np.argmax(f1[:-1])) if len(thr) else 0
    return evaluate(y_true, proba, float(thr[i]) if len(thr) else 0.5)


def pick_threshold(y_true, proba, target_flags: int) -> float:
    """Choose the operating point by CAPACITY, not by maximising a metric.

    A model that catches 90% of churners while flagging half the base is
    useless. The retention team can work a few hundred customers, so the
    threshold is set to flag approximately that many.
    """
    n_flag = min(target_flags, len(proba))
    return float(np.sort(proba)[::-1][n_flag - 1])


# ==========================================================================
def train_all() -> dict:
    df = load_data()
    X_train, X_test, y_train, y_test = split(df)

    log.info("train %d rows (%.2f%% churn) | test %d rows (%.2f%% churn)",
             len(X_train), y_train.mean() * 100,
             len(X_test), y_test.mean() * 100)
    log.info("features: %s", ", ".join(FEATURES))

    baseline = 1 - y_test.mean()
    log.info("NOTE: predicting 'nobody churns' would score %.2f%% accuracy. "
             "Accuracy is therefore not reported as a headline metric.",
             baseline * 100)

    results, fitted = {}, {}
    scaler = StandardScaler().fit(X_train)

    for key, spec in build_models().items():
        model = spec["model"]
        xtr = scaler.transform(X_train) if spec["scale"] else X_train
        xte = scaler.transform(X_test) if spec["scale"] else X_test

        model.fit(xtr, y_train)
        proba = model.predict_proba(xte)[:, 1]

        # TWO operating points, because one number hides the tradeoff.
        # The capacity threshold gives near-perfect precision by
        # construction (the top-scoring customers are unambiguous), so
        # reporting only that would overstate the model. The balanced
        # threshold shows what happens when you try to catch everyone.
        thr = pick_threshold(y_test, proba,
                             int(TARGET_FLAG_COUNT * TEST_SIZE))
        res = evaluate(y_test, proba, thr)
        res["label"] = spec["label"]
        res["interpretable"] = spec["interpretable"]
        res["operating_point"] = "capacity"

        res["balanced_point"] = _best_f1(y_test, proba)
        results[key] = res
        fitted[key] = {"model": model, "scale": spec["scale"]}

        b = res["balanced_point"]
        log.info("  %-24s AUC=%.4f PR-AUC=%.4f | capacity P=%.3f R=%.3f | "
                 "balanced P=%.3f R=%.3f F1=%.3f",
                 spec["label"], res["roc_auc"], res["pr_auc"],
                 res["precision"], res["recall"],
                 b["precision"], b["recall"], b["f1"])

    winner = max(results, key=lambda k: results[k]["pr_auc"])
    log.info("best by PR-AUC: %s", results[winner]["label"])

    # PR-AUC rather than ROC-AUC: with 14.49% positives, ROC-AUC is
    # optimistic because the large negative class inflates it.
    payload = {
        "meta": {
            "data_origin": "MODEL",
            "trained_on": "single snapshot, no time dimension",
            "claim": ("ranks customers by resemblance to those who already "
                      "churned"),
            "not_a_claim": ("does not forecast timing; a high score is a "
                            "probability, not a verdict"),
            "accuracy_note": (
                f"predicting 'nobody churns' scores "
                f"{round(baseline * 100, 2)}% accuracy, which is why "
                f"accuracy is not used to compare these models"),
            "selection_metric": "PR-AUC (appropriate for imbalanced classes)",
            "threshold_basis": (
                f"the CAPACITY point flags about {TARGET_FLAG_COUNT} "
                f"customers portfolio-wide, matching what a retention team "
                f"can work. The BALANCED point maximises F1 instead. Both "
                f"are reported because the capacity point achieves high "
                f"precision by construction and would overstate the model "
                f"if quoted alone"),
        },
        "base_rate_pct": round(float(y_test.mean() * 100), 2),
        "train_rows": len(X_train),
        "test_rows": len(X_test),
        "features": FEATURES,
        "models": results,
        "winner": winner,
    }

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with open(MODEL_PATH, "wb") as fh:
        pickle.dump({"fitted": fitted, "scaler": scaler,
                     "features": FEATURES, "winner": winner,
                     "threshold": results[winner]["threshold"]}, fh)
    METRICS_PATH.write_text(json.dumps(payload, indent=2))

    _log_tree(fitted["decision_tree"]["model"])
    return payload


def _log_tree(tree) -> None:
    """Print the tree's thresholds. If they land near 4 service calls and
    $45 day charge, the model independently rediscovered the rules engine's
    cut points, which is a stronger validation than any metric."""
    log.info("\nDecision tree logic (the model's own thresholds):\n%s",
             export_text(tree, feature_names=FEATURES, max_depth=3))


# ==========================================================================
def load_model():
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"No trained model at {MODEL_PATH}. Run: python run.py train")
    with open(MODEL_PATH, "rb") as fh:
        return pickle.load(fh)


def model_metrics() -> dict:
    if not METRICS_PATH.exists():
        raise FileNotFoundError(
            f"No metrics at {METRICS_PATH}. Run: python run.py train")
    return json.loads(METRICS_PATH.read_text())


def score_customers(limit: int | None = None) -> pd.DataFrame:
    """Risk score for every customer, using the winning model."""
    bundle = load_model()
    df = load_data()
    X = df[bundle["features"]]

    entry = bundle["fitted"][bundle["winner"]]
    X = bundle["scaler"].transform(X) if entry["scale"] else X
    df["risk_score"] = entry["model"].predict_proba(X)[:, 1]
    df["flagged"] = (df.risk_score >= bundle["threshold"]).astype(int)

    out = df.sort_values("risk_score", ascending=False)
    return out.head(limit) if limit else out


if __name__ == "__main__":
    train_all()
