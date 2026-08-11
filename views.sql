-- ==========================================================================
-- Phase 2 — Deterministic metrics layer
-- ==========================================================================
-- Every number the platform will ever display is defined HERE, in SQL.
-- The LLM layers (Phase 4/5) consume these outputs. They never compute.
--
-- METRIC DEFINITIONS (frozen in Phase 0)
--   total_charge     = day_charge + eve_charge + night_charge + intl_charge
--   revenue(segment) = SUM(total_charge) over segment
--   ARPU(segment)    = AVG(total_charge) over segment
--   churn_rate(seg)  = COUNT(churned) / COUNT(*) in segment
--
-- IMPORTANT: charges are PERIOD charges from a single snapshot, NOT monthly
-- recurring revenue. There is no time dimension. All comparisons are
-- BETWEEN SEGMENTS, never across time.
--
-- SERVICE CALL BUCKETING: churn is a CLIFF at 4+ calls (11.3% -> 51.7%),
-- not a slope. Always bucket '0-3' vs '4+'. Never treat as continuous.
-- ==========================================================================

DROP VIEW IF EXISTS v_customer_profile;
DROP VIEW IF EXISTS v_churn_features;
DROP VIEW IF EXISTS v_kpi_summary;
DROP VIEW IF EXISTS v_churn_by_state;
DROP VIEW IF EXISTS v_revenue_by_state;
DROP VIEW IF EXISTS v_churn_by_service_calls;
DROP VIEW IF EXISTS v_churn_by_plan;
DROP VIEW IF EXISTS v_cohort_profile;
DROP VIEW IF EXISTS v_cohort_risk_matrix;
DROP VIEW IF EXISTS v_revenue_by_period;
DROP VIEW IF EXISTS v_risk_segments;


-- --------------------------------------------------------------------------
-- 1. BASE VIEW — flattens the normalized schema back to one row per customer.
--    Every other view builds on this.
-- --------------------------------------------------------------------------
CREATE VIEW v_customer_profile AS
SELECT
    c.customer_id,
    c.phone_number,
    c.state,
    c.area_code,
    c.account_length_days,
    d.cohort_id,
    d.label                AS cohort_label,
    d.sort_order           AS cohort_sort,

    p.international_plan,
    p.voice_mail_plan,
    p.number_vmail_messages,

    MAX(CASE WHEN u.period = 'day'   THEN u.minutes END) AS day_minutes,
    MAX(CASE WHEN u.period = 'day'   THEN u.calls   END) AS day_calls,
    MAX(CASE WHEN u.period = 'day'   THEN u.charge  END) AS day_charge,
    MAX(CASE WHEN u.period = 'eve'   THEN u.minutes END) AS eve_minutes,
    MAX(CASE WHEN u.period = 'eve'   THEN u.calls   END) AS eve_calls,
    MAX(CASE WHEN u.period = 'eve'   THEN u.charge  END) AS eve_charge,
    MAX(CASE WHEN u.period = 'night' THEN u.minutes END) AS night_minutes,
    MAX(CASE WHEN u.period = 'night' THEN u.calls   END) AS night_calls,
    MAX(CASE WHEN u.period = 'night' THEN u.charge  END) AS night_charge,

    i.intl_minutes,
    i.intl_calls,
    i.intl_charge,

    SUM(u.minutes) + i.intl_minutes AS total_minutes,
    SUM(u.charge)  + i.intl_charge  AS total_charge,

    ch.customer_service_calls,
    ch.high_service_calls,
    CASE WHEN ch.customer_service_calls <= 3 THEN '0-3' ELSE '4+' END
                                    AS service_call_bucket,
    ch.churned
FROM customer c
JOIN dim_tenure_cohort   d  ON d.cohort_id   = c.cohort_id
JOIN plan_subscription   p  ON p.customer_id = c.customer_id
JOIN usage_record        u  ON u.customer_id = c.customer_id
JOIN international_usage i  ON i.customer_id = c.customer_id
JOIN churn_record        ch ON ch.customer_id = c.customer_id
GROUP BY c.customer_id;


-- --------------------------------------------------------------------------
-- 2. CHURN FEATURES — per-customer risk drivers. Feeds Phase 6 rules and
--    any future predictive model.
-- --------------------------------------------------------------------------
CREATE VIEW v_churn_features AS
SELECT
    customer_id,
    state,
    cohort_label,
    cohort_sort,
    account_length_days,
    international_plan,
    voice_mail_plan,
    customer_service_calls,
    service_call_bucket,
    high_service_calls,
    ROUND(total_charge, 2)  AS total_charge,
    ROUND(total_minutes, 1) AS total_minutes,
    ROUND(day_charge, 2)    AS day_charge,
    ROUND(intl_charge, 2)   AS intl_charge,
    -- Count of the two confirmed drivers present on this customer.
    (CASE WHEN high_service_calls  THEN 1 ELSE 0 END)
  + (CASE WHEN international_plan  THEN 1 ELSE 0 END) AS risk_factor_count,
    churned
FROM v_customer_profile;


-- --------------------------------------------------------------------------
-- 3. KPI SUMMARY — the executive header numbers. Single row.
-- --------------------------------------------------------------------------
CREATE VIEW v_kpi_summary AS
SELECT
    COUNT(*)                                             AS total_customers,
    SUM(CASE WHEN churned THEN 1 ELSE 0 END)             AS churned_customers,
    ROUND(AVG(CASE WHEN churned THEN 1.0 ELSE 0.0 END) * 100, 2)
                                                         AS churn_rate_pct,
    ROUND(SUM(total_charge), 2)                          AS total_revenue,
    ROUND(AVG(total_charge), 2)                          AS arpu,
    ROUND(SUM(CASE WHEN churned THEN total_charge ELSE 0 END), 2)
                                                         AS revenue_at_risk,
    COUNT(DISTINCT state)                                AS states_covered,
    SUM(CASE WHEN international_plan THEN 1 ELSE 0 END)  AS intl_plan_subscribers,
    ROUND(AVG(CASE WHEN international_plan THEN 1.0 ELSE 0.0 END) * 100, 2)
                                                         AS intl_plan_adoption_pct,
    SUM(CASE WHEN high_service_calls THEN 1 ELSE 0 END)  AS high_service_call_customers,
    ROUND(AVG(customer_service_calls), 2)                AS avg_service_calls
FROM v_customer_profile;


-- --------------------------------------------------------------------------
-- 4. CHURN BY STATE
-- --------------------------------------------------------------------------
CREATE VIEW v_churn_by_state AS
SELECT
    state,
    COUNT(*)                                     AS customers,
    SUM(CASE WHEN churned THEN 1 ELSE 0 END)     AS churned,
    ROUND(AVG(CASE WHEN churned THEN 1.0 ELSE 0.0 END) * 100, 2)
                                                 AS churn_rate_pct,
    ROUND(SUM(total_charge), 2)                  AS revenue,
    ROUND(AVG(total_charge), 2)                  AS arpu,
    ROUND(SUM(CASE WHEN churned THEN total_charge ELSE 0 END), 2)
                                                 AS revenue_at_risk
FROM v_customer_profile
GROUP BY state;


-- --------------------------------------------------------------------------
-- 5. REVENUE BY STATE — ranked, for "where to spend budget" questions.
-- --------------------------------------------------------------------------
CREATE VIEW v_revenue_by_state AS
SELECT
    state,
    COUNT(*)                    AS customers,
    ROUND(SUM(total_charge), 2) AS revenue,
    ROUND(AVG(total_charge), 2) AS arpu,
    ROUND(SUM(total_charge) * 100.0 /
          (SELECT SUM(total_charge) FROM v_customer_profile), 2)
                                AS revenue_share_pct,
    ROUND(AVG(CASE WHEN churned THEN 1.0 ELSE 0.0 END) * 100, 2)
                                AS churn_rate_pct
FROM v_customer_profile
GROUP BY state;


-- --------------------------------------------------------------------------
-- 6. CHURN BY SERVICE CALLS — the primary driver. Both the raw count
--    (for the cliff chart) and the bucket (for narration).
-- --------------------------------------------------------------------------
CREATE VIEW v_churn_by_service_calls AS
SELECT
    customer_service_calls,
    CASE WHEN customer_service_calls <= 3 THEN '0-3' ELSE '4+' END
                                             AS service_call_bucket,
    COUNT(*)                                 AS customers,
    SUM(CASE WHEN churned THEN 1 ELSE 0 END) AS churned,
    ROUND(AVG(CASE WHEN churned THEN 1.0 ELSE 0.0 END) * 100, 2)
                                             AS churn_rate_pct,
    ROUND(AVG(total_charge), 2)              AS arpu
FROM v_customer_profile
GROUP BY customer_service_calls;


-- --------------------------------------------------------------------------
-- 7. CHURN BY PLAN — international plan is the second confirmed driver.
-- --------------------------------------------------------------------------
CREATE VIEW v_churn_by_plan AS
SELECT
    CASE WHEN international_plan THEN 'Intl plan'   ELSE 'No intl plan'   END AS intl_plan,
    CASE WHEN voice_mail_plan    THEN 'Voicemail'   ELSE 'No voicemail'   END AS vmail_plan,
    COUNT(*)                                 AS customers,
    SUM(CASE WHEN churned THEN 1 ELSE 0 END) AS churned,
    ROUND(AVG(CASE WHEN churned THEN 1.0 ELSE 0.0 END) * 100, 2)
                                             AS churn_rate_pct,
    ROUND(AVG(total_charge), 2)              AS arpu,
    ROUND(SUM(total_charge), 2)              AS revenue
FROM v_customer_profile
GROUP BY international_plan, voice_mail_plan;


-- --------------------------------------------------------------------------
-- 8. COHORT PROFILE — the ordered analytical axis (replaces a date axis).
--    NOTE: churn is FLAT across cohorts in this dataset (13.1-15.3%).
--    Use this for structure and ARPU, NOT as evidence of a lifecycle effect.
-- --------------------------------------------------------------------------
CREATE VIEW v_cohort_profile AS
SELECT
    cohort_id,
    cohort_label,
    cohort_sort,
    COUNT(*)                                 AS customers,
    ROUND(COUNT(*) * 100.0 /
          (SELECT COUNT(*) FROM v_customer_profile), 2) AS share_pct,
    SUM(CASE WHEN churned THEN 1 ELSE 0 END) AS churned,
    ROUND(AVG(CASE WHEN churned THEN 1.0 ELSE 0.0 END) * 100, 2)
                                             AS churn_rate_pct,
    ROUND(SUM(total_charge), 2)              AS revenue,
    ROUND(AVG(total_charge), 2)              AS arpu,
    ROUND(AVG(customer_service_calls), 2)    AS avg_service_calls,
    ROUND(AVG(CASE WHEN international_plan THEN 1.0 ELSE 0.0 END) * 100, 2)
                                             AS intl_plan_pct
FROM v_customer_profile
GROUP BY cohort_id, cohort_label, cohort_sort;


-- --------------------------------------------------------------------------
-- 9. COHORT RISK MATRIX — cohort x service-call bucket. This is where the
--    real insight lives: the 4+ cliff holds across every cohort.
-- --------------------------------------------------------------------------
CREATE VIEW v_cohort_risk_matrix AS
SELECT
    cohort_label,
    cohort_sort,
    service_call_bucket,
    COUNT(*)                                 AS customers,
    SUM(CASE WHEN churned THEN 1 ELSE 0 END) AS churned,
    ROUND(AVG(CASE WHEN churned THEN 1.0 ELSE 0.0 END) * 100, 2)
                                             AS churn_rate_pct,
    ROUND(SUM(total_charge), 2)              AS revenue
FROM v_customer_profile
GROUP BY cohort_label, cohort_sort, service_call_bucket;


-- --------------------------------------------------------------------------
-- 10. REVENUE BY PERIOD — day / eve / night / intl split.
--     Uses the long-format usage_record directly.
-- --------------------------------------------------------------------------
CREATE VIEW v_revenue_by_period AS
SELECT period,
       ROUND(SUM(charge), 2)  AS revenue,
       ROUND(SUM(minutes), 1) AS minutes,
       SUM(calls)             AS calls,
       ROUND(AVG(charge), 2)  AS avg_charge_per_customer
FROM usage_record
GROUP BY period
UNION ALL
SELECT 'intl',
       ROUND(SUM(intl_charge), 2),
       ROUND(SUM(intl_minutes), 1),
       SUM(intl_calls),
       ROUND(AVG(intl_charge), 2)
FROM international_usage;


-- --------------------------------------------------------------------------
-- 11. RISK SEGMENTS — named segments built from the two confirmed drivers.
--     This is the bridge into the Phase 6 recommendation engine.
-- --------------------------------------------------------------------------
CREATE VIEW v_risk_segments AS
SELECT
    CASE
        WHEN high_service_calls AND international_plan THEN 'Critical: 4+ calls + intl plan'
        WHEN high_service_calls                        THEN 'High: 4+ service calls'
        WHEN international_plan                        THEN 'Elevated: intl plan'
        ELSE                                                'Baseline'
    END AS segment,
    COUNT(*)                                 AS customers,
    SUM(CASE WHEN churned THEN 1 ELSE 0 END) AS churned,
    ROUND(AVG(CASE WHEN churned THEN 1.0 ELSE 0.0 END) * 100, 2)
                                             AS churn_rate_pct,
    ROUND(SUM(total_charge), 2)              AS revenue,
    ROUND(AVG(total_charge), 2)              AS arpu,
    ROUND(SUM(CASE WHEN churned THEN total_charge ELSE 0 END), 2)
                                             AS revenue_at_risk
FROM v_customer_profile
GROUP BY segment;