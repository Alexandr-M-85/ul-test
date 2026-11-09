from datetime import date

import numpy as np
import pandas as pd

from ml.features import feature_generation, split_data
from ml.train import fit_and_score

AS_OF = date(2026, 10, 26)


def features_ignore_targets(df):
    # Безопасность этапа генерации признаков
    original = feature_generation(df)
    df["price_rub"] *= 100
    for column in ["price_with_vat", "price_group_id", "avg_price", "min_km_price", "profit"]:
        df[column] = 123456
    pd.testing.assert_frame_equal(original, feature_generation(df))
    assert not {"price_rub", "outlier_mark", "price_type", "price_id"} & set(original.columns)
    for name in ["month", "weekday", "day_of_year"]:
        np.testing.assert_allclose(original[f"{name}_sin"] ** 2 + original[f"{name}_cos"] ** 2, 1)
    df.loc[0, "loading_date"] = pd.Timestamp("2026-12-31")
    df.loc[1, "loading_date"] = pd.Timestamp("2027-01-01")
    result = feature_generation(df)
    assert abs(result.loc[0, "day_of_year_sin"] - result.loc[1, "day_of_year_sin"]) < 0.03


def test_split_filters(df):
    # Корректность логики временного разделения данных и проверка аномалий
    train, test, future = split_data(df, AS_OF)
    assert train["price_id"].tolist() == [2, 3, 4, 5]
    assert test["price_id"].tolist() == [6, 7]
    assert future["price_id"].tolist() == [8, 9]
    df.loc[2, "outlier_mark"] = True
    df.loc[3, "price_type"] = 6
    df.loc[7, ["outlier_mark", "is_fact"]] = [True, False]
    train, _, future = split_data(df, AS_OF)
    assert train["price_id"].tolist() == [2, 5]
    assert future["price_id"].tolist() == [8, 9]


def test_targets_isolation(df):
    predictions, summary = fit_and_score(df, AS_OF, iterations=20)
    df.loc[df["loading_date"] >= "2026-10-05", "price_rub"] *= 100
    again, changed = fit_and_score(df, AS_OF, iterations=20)
    assert summary["model_name"] == changed["model_name"]
    np.testing.assert_array_equal(predictions["predicted_price_rub"], again["predicted_price_rub"])
    assert summary["metrics"] != changed["metrics"]
    future_cols = ["actual_price_rub", "abs_error_rub", "ape_pct"]
    assert predictions.loc[predictions["sample"] == "future", future_cols].isna().all().all()

