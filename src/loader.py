"""Stage 1: load shipment records, validate them, and derive route and week keys."""
from pathlib import Path

import pandas as pd

from config import VALID_ROUTE_TYPES

REQUIRED_COLUMNS = [
    "shipment_id", "origin", "destination", "route_type", "material",
    "quantity_tonnes", "distance_km", "freight_cost_inr", "shipment_date", "transporter",
]
NUMERIC_COLUMNS = ["quantity_tonnes", "distance_km", "freight_cost_inr"]


class DataValidationError(ValueError):
    """Raised when the input file breaks an assumption the pipeline relies on."""


def week_start(dates: pd.Series) -> pd.Series:
    """Map each date to the Monday of its Monday-Sunday week (the brief's `week_of`)."""
    return dates - pd.to_timedelta(dates.dt.weekday, unit="D")


def load_shipments(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    validate_shipments(df)

    df = df.copy()
    for col in ["origin", "destination", "route_type"]:
        df[col] = df[col].str.strip()
    df["shipment_date"] = pd.to_datetime(df["shipment_date"], format="%Y-%m-%d")
    df["route"] = df["origin"] + "-" + df["destination"]
    df["week_of"] = week_start(df["shipment_date"])
    df["tonne_km"] = df["quantity_tonnes"] * df["distance_km"]
    return df


def validate_shipments(df: pd.DataFrame) -> None:
    """Fail loudly instead of silently producing wrong costs."""
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataValidationError(f"Missing columns: {missing}")

    nulls = df[REQUIRED_COLUMNS].isna().sum()
    if nulls.any():
        raise DataValidationError(f"Null values found: {nulls[nulls > 0].to_dict()}")

    if df["shipment_id"].duplicated().any():
        dupes = df.loc[df["shipment_id"].duplicated(), "shipment_id"].tolist()
        raise DataValidationError(f"Duplicate shipment_id values: {dupes[:5]}")

    for col in NUMERIC_COLUMNS:
        values = pd.to_numeric(df[col], errors="coerce")
        if values.isna().any():
            raise DataValidationError(f"Non-numeric values in {col}")
        if (values <= 0).any():
            raise DataValidationError(f"Non-positive values in {col}")

    dates = pd.to_datetime(df["shipment_date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any():
        bad = df.loc[dates.isna(), "shipment_id"].tolist()
        raise DataValidationError(f"Unparseable shipment_date for: {bad[:5]}")

    bad_types = set(df["route_type"].str.strip()) - set(VALID_ROUTE_TYPES)
    if bad_types:
        raise DataValidationError(f"Unknown route_type values: {sorted(bad_types)}")

    # A route must carry a single route_type, otherwise the peer grouping is ambiguous.
    route = df["origin"].str.strip() + "-" + df["destination"].str.strip()
    types_per_route = df.assign(route=route).groupby("route")["route_type"].nunique()
    if (types_per_route > 1).any():
        raise DataValidationError(
            f"Routes with more than one route_type: {types_per_route[types_per_route > 1].index.tolist()}"
        )
