CREATE TEMP TABLE aggregated_cells ON COMMIT DROP AS

WITH months AS (
    SELECT price_id, date_trunc('month', price_date)::date AS month, max(price_date) AS last_date
    FROM core.price_dates
    WHERE price_date <= %(as_of)s
    GROUP BY price_id, date_trunc('month', price_date)::date
)

SELECT m.month,
       p.loading_city_gcid,
       p.unloading_city_gcid,
       b.body_type,
       p.weight_category,
       -- Коридоры
       percentile_cont(ARRAY[0.25, 0.5, 0.75]) WITHIN GROUP (ORDER BY p.price_rub) AS trip,
       avg(p.price_rub) AS avg_trip,
       percentile_cont(ARRAY[0.25, 0.5, 0.75]) WITHIN GROUP (ORDER BY p.price_rub / nullif(p.distance, 0)) AS km,
       -- Data Trust
       count(DISTINCT p.price_id) AS trips_count,
       count(DISTINCT p.organization_id) FILTER (WHERE p.price_type IN (3, 9)) AS carriers_count,
       -- Коэффициент вариации (CV)
       stddev_samp(p.price_rub) / nullif(avg(p.price_rub), 0) AS cv_rate,
       -- Свежесть данных в рамках ячейки (для расчета уровня надежности)
       max(m.last_date) AS last_price_date
FROM core.prices p
JOIN months m ON m.price_id = p.price_id
JOIN core.price_body_types b ON b.price_id = p.price_id
-- Бизнес-фильтры
WHERE p.is_fact AND p.price_type IN (1, 2, 3, 4, 5, 9)
  AND NOT p.complex AND NOT p.outlier_mark AND p.price_rub > 0 AND p.distance > 0
GROUP BY m.month, p.loading_city_gcid, p.unloading_city_gcid, b.body_type, p.weight_category;
CREATE UNIQUE INDEX ON aggregated_cells
    (loading_city_gcid, unloading_city_gcid, body_type, weight_category, month);
ANALYZE aggregated_cells;

DELETE FROM mart.mart_route_card;
INSERT INTO mart.mart_route_card
SELECT ac.month,
       ac.loading_city_gcid,
       ac.unloading_city_gcid,
       ac.body_type,
       ac.weight_category,
       ac.trip[1],
       ac.trip[2],
       ac.trip[3],
       ac.avg_trip,
       ac.km[1],
       ac.km[2],
       ac.km[3],
       ac.trips_count,
       ac.carriers_count,
       ac.cv_rate,
       (ac.trip[2] / nullif(prev.trip[2], 0) - 1) * 100,
       (ac.trip[2] / nullif(future.trip[2], 0) - 1) * 100,
       ac.last_price_date,
       CASE
           WHEN ac.trips_count >= 30 AND ac.carriers_count >= 5
                   AND %(as_of)s::date - ac.last_price_date <= 30 THEN 'HIGH'
           WHEN ac.trips_count >= 10 THEN 'MEDIUM' ELSE 'LOW_DATA'
       END,
       %(as_of)s
FROM aggregated_cells ac
LEFT JOIN aggregated_cells prev
    ON (prev.loading_city_gcid, prev.unloading_city_gcid, prev.body_type, prev.weight_category)
     = (ac.loading_city_gcid, ac.unloading_city_gcid, ac.body_type, ac.weight_category)
    AND prev.month = (ac.month - interval '1 month')::date
LEFT JOIN aggregated_cells future
    ON (future.loading_city_gcid, future.unloading_city_gcid, future.body_type, future.weight_category)
     = (ac.loading_city_gcid, ac.unloading_city_gcid, ac.body_type, ac.weight_category)
    AND future.month = (ac.month - interval '1 year')::date;
