from datetime import date, timedelta

import numpy as np
import pandas as pd


CATEGORICAL_TYPES = [
    "loading_city_gcid", "unloading_city_gcid", "departure_point_region", "destination_region",
    "body_type", "weight_category", "volume_category",
]
NUMERIC_TYPES = [
    "distance", "total_weight", "total_volume", "departure_point_latitude", "departure_point_longitude",
    "destination_latitude", "destination_longitude",
]


def split_data(data: pd.DataFrame, as_of: date) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    # Функция разделения датасета на обучающую, тестовую и future
    train_start, test_start = (pd.Timestamp(as_of) - pd.DateOffset(months=3)).date(), as_of - timedelta(weeks=3)

    dates = pd.to_datetime(data["loading_date"])
    # Маска для отбора валидных данных
    valid_date = (
        data["is_fact"] & data["price_type"].isin([1, 2, 3, 4, 5, 9])
        & ~data["complex"] & ~data["outlier_mark"] & data["price_rub"].gt(0)
    )

    train = valid_date & dates.ge(pd.Timestamp(train_start)) & dates.lt(pd.Timestamp(test_start))
    test = valid_date & dates.ge(pd.Timestamp(test_start)) & dates.le(pd.Timestamp(as_of))
    future = dates.gt(pd.Timestamp(as_of)) & ~data["complex"]
    return tuple(data.loc[mask].sort_values(["loading_date", "price_id"]).copy() for mask in (train, test, future))


def feature_generation(data: pd.DataFrame) -> pd.DataFrame:
    # Функция генерации признаков

    attr = data[NUMERIC_TYPES].astype(float).copy()
    for column in CATEGORICAL_TYPES:
        attr[column] = data[column].fillna("Не указан").astype(str)

    # Извлечение и кодировка дат
    dates = pd.to_datetime(data["loading_date"])
    attr["year"] = dates.dt.year
    attr["month"] = dates.dt.month
    attr["weekday"] = dates.dt.dayofweek
    attr["day_of_year"] = dates.dt.dayofyear
    for name, values, period in (
        ("month", dates.dt.month - 1, 12),
        ("weekday", dates.dt.dayofweek, 7),
        ("day_of_year", dates.dt.dayofyear - 1, 365.25),
    ):
        attr[f"{name}_sin"] = np.sin(2 * np.pi * values / period)
        attr[f"{name}_cos"] = np.cos(2 * np.pi * values / period)
    return attr
