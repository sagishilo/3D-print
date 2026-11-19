"""The nightly roll-up (SQL): one business day of reporting.daily_sku from the ledgers and the read models, then
reporting.daily_niche from it. Idempotent: a day's rows are replaced, so a re-run or a backfill never doubles.
The day is a business day in Asia/Jerusalem, as the finance ledger books it."""

from __future__ import annotations

TZ = "Asia/Jerusalem"
ITEM_OF_JOB = "substring(j.job_id FROM '-i([0-9]+)-u[0-9.]+-a[0-9]+$')"

DAILY_SKU = f"""
WITH sold AS (
    SELECT l.product_type AS sku, l.niche, sum(l.quantity) AS v
    FROM reporting.order_lines_v l
    JOIN coordinator.saga_history h ON h.saga_id = l.order_id AND h.to_state = 'SHIPPED'
    WHERE NOT l.stock AND (h.at AT TIME ZONE '{TZ}')::date = %(d)s GROUP BY 1, 2
), rev AS (
    SELECT product_type AS sku, niche, sum(net_agorot) AS v FROM reporting.niche_revenue_v
    WHERE entry_date = %(d)s GROUP BY 1, 2
), cost AS (
    SELECT sku, niche, sum(v) AS v FROM (
        SELECT product_type AS sku, niche, sum(net_agorot) AS v FROM reporting.sku_costs_v
        WHERE NOT stock AND entry_date = %(d)s GROUP BY 1, 2
        UNION ALL
        SELECT sku, niche, sum(cost_agorot) FROM reporting.stock_sales_v WHERE entry_date = %(d)s GROUP BY 1, 2
    ) c GROUP BY 1, 2
), hours AS (
    SELECT j.product_type AS sku, coalesce(i.niche, 'unassigned') AS niche, sum(j.est_minutes) / 60.0 AS v
    FROM fleet.jobs j
    LEFT JOIN coordinator.order_items i ON i.order_id = j.order_id AND i.item_id = {ITEM_OF_JOB}
    WHERE j.status = 'completed' AND (j.updated_at AT TIME ZONE '{TZ}')::date = %(d)s GROUP BY 1, 2
), qc AS (
    SELECT CASE WHEN i.product = 'catalog' AND i.sku <> '' THEN i.sku ELSE i.product END AS sku, i.niche,
           sum(i.quantity) AS checked,
           sum((SELECT count(*) FROM jsonb_array_elements_text(q.failed_keys) k
                WHERE split_part(k, '.', 1) = i.item_id)) AS failed
    FROM coordinator.qc_checks q JOIN coordinator.order_items i ON i.order_id = q.order_id
    WHERE (q.checked_at AT TIME ZONE '{TZ}')::date = %(d)s GROUP BY 1, 2
), caps AS (
    SELECT c.sku_guess AS sku, coalesce(p.niche, 'unassigned') AS niche, count(*) AS v
    FROM social.captures c LEFT JOIN catalog.products p ON p.sku = c.sku_guess
    WHERE c.sku_guess IS NOT NULL AND (c.received_at AT TIME ZONE '{TZ}')::date = %(d)s GROUP BY 1, 2
), leads AS (
    SELECT l.sku, coalesce(p.niche, 'unassigned') AS niche, count(*) AS v,
           count(*) FILTER (WHERE l.status = 'posted') AS posted
    FROM social.leads l LEFT JOIN catalog.products p ON p.sku = l.sku
    WHERE l.sku IS NOT NULL AND (l.created_at AT TIME ZONE '{TZ}')::date = %(d)s GROUP BY 1, 2
), keys AS (
    SELECT sku, niche FROM sold UNION SELECT sku, niche FROM rev UNION SELECT sku, niche FROM cost
    UNION SELECT sku, niche FROM hours UNION SELECT sku, niche FROM qc UNION SELECT sku, niche FROM caps
    UNION SELECT sku, niche FROM leads
)
INSERT INTO reporting.daily_sku (day, sku, niche, units_sold, revenue_agorot, cost_agorot, print_hours, qc_checked,
                                 qc_failed, captures, leads, posted)
SELECT %(d)s, k.sku, k.niche, coalesce(sold.v, 0), coalesce(rev.v, 0), coalesce(cost.v, 0), coalesce(hours.v, 0),
       coalesce(qc.checked, 0), coalesce(qc.failed, 0), coalesce(caps.v, 0), coalesce(leads.v, 0),
       coalesce(leads.posted, 0)
FROM keys k
LEFT JOIN sold USING (sku, niche) LEFT JOIN rev USING (sku, niche) LEFT JOIN cost USING (sku, niche)
LEFT JOIN hours USING (sku, niche) LEFT JOIN qc USING (sku, niche) LEFT JOIN caps USING (sku, niche)
LEFT JOIN leads USING (sku, niche)
WHERE k.sku IS NOT NULL AND k.niche IS NOT NULL
"""

DAILY_NICHE = """
INSERT INTO reporting.daily_niche (day, niche, units_sold, revenue_agorot, cost_agorot, print_hours, qc_checked,
                                   qc_failed, captures, leads, posted)
SELECT day, niche, sum(units_sold), sum(revenue_agorot), sum(cost_agorot), sum(print_hours), sum(qc_checked),
       sum(qc_failed), sum(captures), sum(leads), sum(posted)
FROM reporting.daily_sku WHERE day = %(d)s GROUP BY day, niche
"""

# The windows the trend rules read: 7 and 28 days ending on %(d)s, and the windows before them.
WINDOWS = """
SELECT {key} AS subject, {niche} AS niche,
  coalesce(sum(units_sold) FILTER (WHERE day > %(d)s - 7), 0) AS units_7d,
  coalesce(sum(units_sold) FILTER (WHERE day <= %(d)s - 7 AND day > %(d)s - 14), 0) AS units_prev_7d,
  coalesce(sum(units_sold) FILTER (WHERE day > %(d)s - 28), 0) AS units_28d,
  coalesce(sum(units_sold) FILTER (WHERE day <= %(d)s - 28), 0) AS units_prev_28d,
  coalesce(sum(leads) FILTER (WHERE day > %(d)s - 7), 0) AS leads_7d,
  coalesce(sum(leads) FILTER (WHERE day <= %(d)s - 7 AND day > %(d)s - 14), 0) AS leads_prev_7d,
  coalesce(sum(revenue_agorot) FILTER (WHERE day > %(d)s - 7), 0) AS revenue_7d
FROM {table} WHERE day > %(d)s - 56 AND day <= %(d)s GROUP BY 1, 2
"""
SKU_WINDOWS = WINDOWS.format(key="sku", niche="niche", table="reporting.daily_sku")
NICHE_WINDOWS = WINDOWS.format(key="niche", niche="niche", table="reporting.daily_niche")
