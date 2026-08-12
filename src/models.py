"""
Normalized schema for the Telecom Decision Intelligence Platform.

Tables
------
dim_tenure_cohort    lookup dimension, the ordered analytical axis
customer             one row per customer (grain of the dataset)
plan_subscription    1:1 with customer — plan flags
usage_record         1:3 with customer — LONG format (day / eve / night)
international_usage  1:1 with customer
churn_record         1:1 with customer — target + service calls

NOTE: there is deliberately NO date column anywhere. The source is a single
snapshot; a nullable date field would invite false temporal assumptions in
the Phase 4/5 LLM layers.
"""

from sqlalchemy import (
    Boolean, Column, Float, ForeignKey, Index, Integer, String,
    MetaData, Table, CheckConstraint,
)

metadata = MetaData()

# --------------------------------------------------------------------------
dim_tenure_cohort = Table(
    "dim_tenure_cohort", metadata,
    Column("cohort_id", Integer, primary_key=True),
    Column("label", String(40), nullable=False, unique=True),
    Column("min_days", Integer, nullable=False),
    Column("max_days", Integer, nullable=True),   # NULL = open-ended
    Column("sort_order", Integer, nullable=False),
)

# --------------------------------------------------------------------------
customer = Table(
    "customer", metadata,
    Column("customer_id", Integer, primary_key=True, autoincrement=True),
    Column("phone_number", String(20), unique=True, nullable=True),
    Column("state", String(4), nullable=False),
    Column("area_code", Integer, nullable=False),
    Column("account_length_days", Integer, nullable=False),
    Column("cohort_id", Integer, ForeignKey("dim_tenure_cohort.cohort_id"),
           nullable=False),
    CheckConstraint("account_length_days >= 0", name="ck_customer_tenure_nonneg"),
)
Index("ix_customer_state", customer.c.state)
Index("ix_customer_cohort", customer.c.cohort_id)

# --------------------------------------------------------------------------
plan_subscription = Table(
    "plan_subscription", metadata,
    Column("customer_id", Integer, ForeignKey("customer.customer_id"),
           primary_key=True),
    Column("international_plan", Boolean, nullable=False),
    Column("voice_mail_plan", Boolean, nullable=False),
    Column("number_vmail_messages", Integer, nullable=False, default=0),
)
Index("ix_plan_intl", plan_subscription.c.international_plan)

# --------------------------------------------------------------------------
# LONG format: 3 rows per customer. Makes "revenue by period" a GROUP BY
# instead of three separate column sums.
usage_record = Table(
    "usage_record", metadata,
    Column("usage_id", Integer, primary_key=True, autoincrement=True),
    Column("customer_id", Integer, ForeignKey("customer.customer_id"),
           nullable=False),
    Column("period", String(8), nullable=False),   # 'day' | 'eve' | 'night'
    Column("minutes", Float, nullable=False),
    Column("calls", Integer, nullable=False),
    Column("charge", Float, nullable=False),
    CheckConstraint("period IN ('day','eve','night')", name="ck_usage_period"),
    CheckConstraint("minutes >= 0 AND calls >= 0 AND charge >= 0",
                    name="ck_usage_nonneg"),
)
Index("ix_usage_customer", usage_record.c.customer_id)
Index("ix_usage_period", usage_record.c.period)

# --------------------------------------------------------------------------
international_usage = Table(
    "international_usage", metadata,
    Column("customer_id", Integer, ForeignKey("customer.customer_id"),
           primary_key=True),
    Column("intl_minutes", Float, nullable=False),
    Column("intl_calls", Integer, nullable=False),
    Column("intl_charge", Float, nullable=False),
)

# --------------------------------------------------------------------------
churn_record = Table(
    "churn_record", metadata,
    Column("customer_id", Integer, ForeignKey("customer.customer_id"),
           primary_key=True),
    Column("churned", Boolean, nullable=False),
    Column("customer_service_calls", Integer, nullable=False),
    Column("high_service_calls", Boolean, nullable=False),
)
Index("ix_churn_flag", churn_record.c.churned)
Index("ix_churn_servcalls", churn_record.c.customer_service_calls)


def create_all(engine):
    """Create every table. Safe to call repeatedly."""
    metadata.create_all(engine)


def drop_all(engine):
    """Drop every table — used for idempotent reloads."""
    metadata.drop_all(engine)