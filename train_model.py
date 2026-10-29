
import sqlite3
from pathlib import Path
from datetime import date

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder
from sklearn.pipeline import Pipeline
from sklearn.ensemble import RandomForestClassifier
import joblib

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "freshfirst.db"
MODEL_PATH = BASE_DIR / "food_waste_model.pkl"


def train_model():
    with sqlite3.connect(DB_PATH) as conn:
        data = pd.read_sql_query("""
            SELECT
                category,
                quantity,
                price,
                purchase_date,
                expiry_date,
                status
            FROM food_items
            WHERE status IN ('available', 'wasted')
        """, conn)

    if len(data) < 20:
        print("Not enough data to train a useful model yet.")
        print(f"Current records: {len(data)}")
        print("Collect at least 20 records to begin testing.")
        return

    if data["status"].nunique() < 2:
        print("Data must include both available and wasted items.")
        return

    data["purchase_date"] = pd.to_datetime(
        data["purchase_date"], errors="coerce"
    )
    data["expiry_date"] = pd.to_datetime(
        data["expiry_date"], errors="coerce"
    )

    data["shelf_life_days"] = (
        data["expiry_date"] - data["purchase_date"]
    ).dt.days

    data = data.dropna(
        subset=["category", "quantity", "price", "shelf_life_days"]
    )

    if len(data) < 20 or data["status"].nunique() < 2:
        print("Not enough valid data for training.")
        return

    X = data[
        ["category", "quantity", "price", "shelf_life_days"]
    ]
    y = data["status"].map({
        "available": 0,
        "wasted": 1
    })

    preprocessing = ColumnTransformer([
        (
            "category",
            OneHotEncoder(handle_unknown="ignore"),
            ["category"]
        )
    ], remainder="passthrough")

    model = Pipeline([
        ("preprocessing", preprocessing),
        (
            "classifier",
            RandomForestClassifier(
                n_estimators=100,
                random_state=42,
                class_weight="balanced"
            )
        )
    ])

    model.fit(X, y)
    joblib.dump(model, MODEL_PATH)

    print("Model trained successfully!")
    print(f"Saved model to: {MODEL_PATH}")
    print(f"Training records: {len(data)}")
    print(
        "Note: This is an initial model. More real records "
        "are needed to evaluate prediction quality."
    )


if __name__ == "__main__":
    train_model()
