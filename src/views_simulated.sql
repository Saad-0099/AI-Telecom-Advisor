-- ==========================================================================
-- Phase 6.5 — SIMULATED history views
-- ==========================================================================
-- EVERY VIEW HERE IS DERIVED FROM SIMULATED DATA.
--
-- The names carry "_sim" and every view exposes a data_origin column, so
-- provenance travels with the rows into any payload, chart or report. That
-- is deliberate: a README note is not enough when a screenshot can outlive
-- its caption.
--
-- WHAT THESE VIEWS CAN SHOW
--   portfolio structure over time: active customers, revenue, cohort mix
--
-- WHAT THEY CANNOT SHOW
--   churn trends. Churn is FLAT by construction because the source data
--   contains no information about how churn moved over time. Month-to-month
--   variation here is sampling noise. Forecasting needs real history;
--   per-customer risk is a separate problem (Phase 10).
-- ==========================================================================

DROP VIEW IF EXISTS v_sim_monthly_portfolio;
DROP VIEW IF EXISTS v_sim_monthly_revenue;
DROP VIEW IF EXISTS v_sim_tenure_curve;
DROP VIEW IF EXISTS v_sim_customer_timeline;


-- --------------------------------------------------------------------------
-- 1. MONTHLY PORTFOLIO — the headline time series.
-- --------------------------------------------------------------------------
CREATE VIEW v_sim_monthly_portfolio AS
SELECT
    'SIMULATED'                              AS data_origin,
    snapshot_month,
    COUNT(*)                                 AS active_customers,
    SUM(churned_this_month)                  AS churn_events,
    ROUND(SUM(churned_this_month) * 100.0 / COUNT(*), 2)
                                             AS churn_rate_pct,
    ROUND(SUM(total_charge), 2)              AS revenue,
    ROUND(AVG(total_charge), 2)              AS arpu,
    SUM(service_calls_this_month)            AS service_calls,
    ROUND(AVG(tenure_month), 2)              AS avg_tenure_month
FROM customer_snapshot_simulated
GROUP BY snapshot_month;


-- --------------------------------------------------------------------------
-- 2. MONTHLY REVENUE BY PERIOD — revenue mix over time.
-- --------------------------------------------------------------------------
CREATE VIEW v_sim_monthly_revenue AS
SELECT
    'SIMULATED'                 AS data_origin,
    snapshot_month,
    ROUND(SUM(day_charge), 2)   AS day_revenue,
    ROUND(SUM(eve_charge), 2)   AS eve_revenue,
    ROUND(SUM(night_charge), 2) AS night_revenue,
    ROUND(SUM(intl_charge), 2)  AS intl_revenue,
    ROUND(SUM(total_charge), 2) AS total_revenue,
    ROUND(SUM(day_charge) * 100.0 / SUM(total_charge), 1)
                                AS day_share_pct
FROM customer_snapshot_simulated
GROUP BY snapshot_month;


-- --------------------------------------------------------------------------
-- 3. TENURE CURVE — the legitimate use of this panel.
--    How usage and service calls evolve with tenure, aggregated across all
--    customers regardless of calendar month. This is a lifecycle view, not
--    a trend, so it is not vulnerable to the flat-churn caveat.
-- --------------------------------------------------------------------------
CREATE VIEW v_sim_tenure_curve AS
SELECT
    'SIMULATED'                              AS data_origin,
    tenure_month,
    COUNT(*)                                 AS observations,
    COUNT(DISTINCT customer_id)              AS customers,
    ROUND(AVG(total_charge), 2)              AS avg_charge,
    ROUND(AVG(day_charge), 2)                AS avg_day_charge,
    ROUND(AVG(service_calls_this_month), 3)  AS avg_service_calls,
    SUM(churned_this_month)                  AS churn_events
FROM customer_snapshot_simulated
GROUP BY tenure_month;


-- --------------------------------------------------------------------------
-- 4. CUSTOMER TIMELINE — per-customer month-by-month, for drill-down.
-- --------------------------------------------------------------------------
CREATE VIEW v_sim_customer_timeline AS
SELECT
    'SIMULATED'                 AS data_origin,
    s.customer_id,
    s.snapshot_month,
    s.tenure_month,
    s.service_calls_this_month,
    s.service_calls_cumulative,
    ROUND(s.total_charge, 2)    AS total_charge,
    ROUND(s.day_charge, 2)      AS day_charge,
    s.churned_this_month,
    s.is_final_month,
    c.state,
    c.cohort_label
FROM customer_snapshot_simulated s
JOIN v_customer_profile c ON c.customer_id = s.customer_id;