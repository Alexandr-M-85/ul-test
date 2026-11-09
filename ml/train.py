import os
import hashlib
import json
from psycopg.types.json import Jsonb
from datetime import date, timedelta
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, r2_score
from sklearn.metrics import root_mean_squared_error

from ml.features import feature_generation, split_data, CATEGORICAL_TYPES


def load_dataset(conn, as_of: date) -> pd.DataFrame:
    train_start, _ = (pd.Timestamp(as_of) - pd.DateOffset(months=3)).date(), as_of - timedelta(weeks=3)
    rows = conn.execute("""
        WITH bodies AS (
            SELECT price_id, string_agg(body_type, ' + ' ORDER BY body_type COLLATE "C") AS body_type
            FROM core.price_body_types
            GROUP BY price_id
        )
        
        SELECT  p.price_id,
                p.loading_date,
                p.loading_city_gcid,
                p.unloading_city_gcid,
                p.distance,
                p.total_weight,
                p.total_volume,
                p.weight_category,
                p.volume_category,
                p.price_rub::double precision AS price_rub,
                p.is_fact,
                p.price_type,
                p.complex,
                p.outlier_mark,
                с.region AS departure_point_region,
                с2.region AS destination_region,
                с.latitude AS departure_point_latitude,
                с.longitude AS departure_point_longitude,
                с2.latitude AS destination_latitude,
                с2.longitude AS destination_longitude,
                b.body_type
        FROM core.prices p
        JOIN core.cities с ON с.gc_id = p.loading_city_gcid
        JOIN core.cities с2 ON с2.gc_id = p.unloading_city_gcid
        JOIN bodies b ON b.price_id = p.price_id
        WHERE p.loading_date >= %s AND NOT p.complex
        ORDER BY p.loading_date, p.price_id;    
    """, (train_start,)).fetchall()
    if not rows:
        raise ValueError("Нет данных")
    return pd.DataFrame.from_records(rows)


def fit_and_score(frame: pd.DataFrame, as_of: date, *, iterations=None):
    # Функция для обучения моделей
    train, test, future = split_data(frame, as_of)
    if len(train) < 2 or len(test) < 2:
        raise ValueError("Слишком мало данных")
    features = feature_generation(train)

    params = {
        "iterations": iterations or int(os.getenv("MODEL_ITERATIONS", "1000")),
        "depth": 6,
        "learning_rate": 0.05,
        "loss_function": "RMSE",
        "random_seed": int(os.getenv("RANDOM_SEED", "42")),
        "thread_count": int(os.getenv("MODEL_THREADS", "4")),
        "has_time": True,
        "allow_writing_files": False,
        "verbose": 100,
    }
    hashprint = hashlib.sha256(
        pd.util.hash_pandas_object(features, index=False).values.tobytes()
        + train["price_rub"].to_numpy(dtype="float64").tobytes()
        + json.dumps(params, sort_keys=True).encode()
    ).hexdigest()[:12]
    model_name = f"catboost-{hashprint}"

    model = CatBoostRegressor(**params)
    model.fit(features, train["price_rub"], cat_features=CATEGORICAL_TYPES)
    scor = pd.concat([test.assign(sample="test"), future.assign(sample="future")], ignore_index=True)
    pred = np.maximum(1.0, model.predict(feature_generation(scor)))
    actual = scor["price_rub"].to_numpy(dtype=float)
    hist = scor["sample"].eq("test").to_numpy()

    metrics = {
        "r2": float(r2_score(actual[hist], pred[hist])),
        "mape_pct": float(mean_absolute_percentage_error(actual[hist], pred[hist]) * 100),
        "rmse_rub": float(root_mean_squared_error(actual[hist], pred[hist])),
        "mae_rub": float(mean_absolute_error(actual[hist], pred[hist])),
    }
    output = scor[["price_id", "loading_date", "loading_city_gcid", "unloading_city_gcid", "body_type", "sample"]].copy()
    output["actual_price_rub"] = np.where(hist, actual, np.nan)
    output["predicted_price_rub"] = pred
    output["abs_error_rub"] = np.where(hist, np.abs(actual - pred), np.nan)
    output["ape_pct"] = np.where(hist, np.abs(actual - pred) / actual * 100, np.nan)
    output["model_name"] = model_name
    summary = {
        "model_name": model_name,
        "train_count": len(train),
        "test_count": len(test),
        "future_count": len(future),
        "metrics": metrics,
        "parameters": params,
        "feature_names": list(features.columns),
    }
    return output, summary


def save_result(connection, output: pd.DataFrame, summary: dict, as_of: date):
    # Сохранение результатов
    train_start, test_start = (pd.Timestamp(as_of) - pd.DateOffset(months=3)).date(), as_of - timedelta(weeks=3)
    with connection.transaction():
        connection.execute("DELETE FROM mart.ml_price_predictions WHERE as_of = %s", (as_of,))
        with connection.cursor() as cursor:
            with cursor.copy("""
                    COPY mart.ml_price_predictions (as_of, price_id, loading_date, loading_city_gcid,
                    unloading_city_gcid, body_type, sample, actual_price_rub, predicted_price_rub,
                    abs_error_rub, ape_pct, model_name) FROM STDIN            
            """) as copy:
                for row in output.itertuples(index=False, name=None):
                    copy.write_row((as_of, *(None if pd.isna(value) else value for value in row)))
        connection.execute("DELETE FROM mart.ml_model_runs WHERE as_of = %s", (as_of,))
        connection.execute(
            """
            INSERT INTO mart.ml_model_runs (
                as_of, model_name, train_start, test_start, 
                train_count, test_count, future_count, 
                feature_names, parameters, metrics
            ) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                as_of,
                summary["model_name"],
                train_start,
                test_start,
                summary["train_count"],
                summary["test_count"],
                summary["future_count"],
                Jsonb(summary["feature_names"]),
                Jsonb(summary["parameters"]),
                Jsonb(summary["metrics"]),
            ),
        )


def run_model(connection, as_of: date) -> dict:
    frame = load_dataset(connection, as_of)
    output, summary = fit_and_score(frame, as_of)
    save_result(connection, output, summary, as_of)
    return summary
