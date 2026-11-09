-- Заполняем таблицу core.cities, предварительно очищая данные от прошлых запусков
DELETE FROM core.cities;

INSERT INTO core.cities (gc_id, id, name, region, latitude, longitude)
SELECT DISTINCT ON (gc_id)
    gc_id, id, name,
    CASE
        WHEN strpos(full_name_local, ',') > 0
        THEN coalesce(nullif(btrim(substr(full_name_local, strpos(full_name_local, ',') + 1)), ''), 'Не указан')
        ELSE 'Не указан'
    END,
    latitude, longitude
FROM raw.cities
ORDER BY gc_id, id;

-- Создадим временную таблицу tmp_prices для выявления некорректных данных
CREATE TEMP TABLE tmp_prices ON COMMIT DROP AS

WITH rates AS (
    SELECT DISTINCT ON (num_code, setting_date) num_code, setting_date, value
    FROM raw.exchange_rates ORDER BY num_code, setting_date, id
),

tmp_tbl AS (
    SELECT p.*, to_jsonb(p) AS source_row,
        CASE
            WHEN p.currency_code = '643' THEN 1::double precision
            ELSE r.value
        END AS exchange_rate,
        с.gc_id AS departure_point_id, с2.gc_id AS destination_id
    FROM raw.prices p
    LEFT JOIN core.cities с ON с.gc_id = p.loading_city_gcid
    LEFT JOIN core.cities с2 ON с2.gc_id = p.unloading_city_gcid
    LEFT JOIN rates r ON r.num_code = p.currency_code AND r.setting_date = p.nearest_rate_date
    WHERE p.loading_date >= %(start_date)s AND p.loading_date < %(end_date)s
)

SELECT t.*,
    array_remove(ARRAY[
        CASE WHEN price <= 0 OR NOT price < 'Infinity'::float8 THEN 'INVALID_PRICE' END,
        CASE WHEN currency_code = '643' AND price <= 1000 THEN 'RUB_PRICE_UNDER_MIN_THRESHOLD' END,
        CASE WHEN distance <= 0 OR NOT distance < 'Infinity'::float8 THEN 'INVALID_DISTANCE' END,
        CASE WHEN total_weight < 0 OR NOT total_weight < 'Infinity'::float8 THEN 'INVALID_WEIGHT' END,
        CASE WHEN total_volume < 0 OR NOT total_volume < 'Infinity'::float8 THEN 'INVALID_VOLUME' END,
        CASE WHEN departure_point_id IS NULL THEN 'UNKNOWN_DEPARTURE_POINT' END,
        CASE WHEN destination_id IS NULL THEN 'UNKNOWN_DESTINATION' END,
        CASE WHEN currency_code IN ('840', '978') AND exchange_rate IS NULL THEN 'MISSING_FX_RATE' END,
        CASE WHEN exchange_rate <= 0 OR NOT exchange_rate < 'Infinity'::float8 THEN 'INVALID_FX_RATE' END
    ], NULL) AS reasons
FROM tmp_tbl t;
CREATE UNIQUE INDEX ON tmp_prices (id);
ANALYZE tmp_prices;

-- # Удаляем данные за период из таблицы валидных цен и карантина, если обновляем данные
DELETE FROM core.prices
WHERE (loading_date >= %(start_date)s AND loading_date < %(end_date)s)
   OR price_id IN (SELECT id FROM tmp_prices);
DELETE FROM core.dq_quarantine_prices
WHERE (loading_date >= %(start_date)s AND loading_date < %(end_date)s)
   OR price_id IN (SELECT id FROM tmp_prices);

INSERT INTO core.dq_quarantine_prices (price_id, loading_date, reasons, source_row)
SELECT id, loading_date, reasons, source_row FROM tmp_prices WHERE cardinality(reasons) > 0;

-- Заполняем таблицы
INSERT INTO core.prices (
    price_id, loading_date, created_at, loading_city_gcid, unloading_city_gcid,
    distance, total_weight, total_volume, weight_category, volume_category,
    organization_id, price_type, is_fact, complex, outlier_mark,
    price, currency_code, nearest_rate_date, exchange_rate, price_rub
)
SELECT id, loading_date, created_at, loading_city_gcid, unloading_city_gcid,
    distance, total_weight, total_volume,
    coalesce(nullif(btrim(weight_category), ''),
        CASE
            WHEN round(total_weight::numeric, 1) >= 30 THEN 'G20T'
            WHEN round(total_weight::numeric, 1) >= 10 THEN 'FTL'
            WHEN round(total_weight::numeric, 1) >= 5 THEN '10T'
            WHEN round(total_weight::numeric, 1) >= 2.5 THEN '5T'
            WHEN round(total_weight::numeric, 1) >= 0.5 THEN 'L5T' ELSE 'OTH'
        END),
    coalesce(nullif(btrim(volume_category), ''),
        CASE
            WHEN total_volume > 0 AND total_volume <= 120 THEN 'FTL' ELSE 'OTH'
        END),
    organization_id, price_type, is_fact, complex, outlier_mark,
    price::numeric, currency_code, nearest_rate_date, exchange_rate::numeric,
    price::numeric * exchange_rate::numeric
FROM tmp_prices WHERE cardinality(reasons) = 0;

INSERT INTO core.price_body_types (price_id, body_type)
SELECT DISTINCT t.id,
    CASE
        WHEN coalesce(bt.std_id, coalesce(pbt.body_type_id, t.body_type)) = 56  THEN 'Тент'
        WHEN coalesce(bt.std_id, coalesce(pbt.body_type_id, t.body_type)) = 4   THEN 'Рефрижератор'
        WHEN coalesce(bt.std_id, coalesce(pbt.body_type_id, t.body_type)) = 2   THEN 'Изотерм'
        WHEN coalesce(bt.std_id, coalesce(pbt.body_type_id, t.body_type)) = 15  THEN 'Борт'
        WHEN coalesce(pbt.body_type_id, t.body_type) IS NULL                    THEN 'Не указан'
        ELSE 'Прочие'
    END
FROM tmp_prices t
LEFT JOIN raw.price_body_types pbt ON pbt.price_id = t.id
LEFT JOIN raw.body_types bt ON bt.id = coalesce(pbt.body_type_id, t.body_type)
WHERE cardinality(t.reasons) = 0;

INSERT INTO core.price_dates (price_id, price_date)
SELECT DISTINCT pd.price_id, pd.price_date
FROM raw.price_dates pd JOIN tmp_prices p ON p.id = pd.price_id
WHERE cardinality(p.reasons) = 0;
