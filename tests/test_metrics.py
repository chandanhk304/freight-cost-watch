"""Stage 1-2 tests: weekly grouping and the two graded baselines."""

import pandas as pd
import pytest

from config import SHIPMENTS_CSV
from eval import independent_metrics
from src.loader import DataValidationError, load_shipments, validate_shipments, week_start
from src.metrics import compute_weekly_metrics


@pytest.fixture(scope="module")
def shipments():
    return load_shipments(SHIPMENTS_CSV)


@pytest.fixture(scope="module")
def metrics(shipments):
    return compute_weekly_metrics(shipments)


def _row(metrics, route, week):
    match = metrics[(metrics["route"] == route) & (metrics["week_of"] == pd.Timestamp(week))]
    assert len(match) == 1, f"expected one row for {route} {week}"
    return match.iloc[0]


# --- Stage 1: loading and week bucketing -------------------------------------------------

def test_week_of_is_monday_of_monday_to_sunday_week():
    dates = pd.Series(pd.to_datetime([
        "2024-01-01",  # Monday -> itself
        "2024-01-07",  # Sunday -> previous Monday
        "2024-12-31",  # Tuesday, crosses year boundary -> 2024-12-30
        "2025-01-05",  # Sunday after the year boundary -> 2024-12-30
    ]))
    expected = pd.to_datetime(["2024-01-01", "2024-01-01", "2024-12-30", "2024-12-30"])
    assert list(week_start(dates)) == list(expected)


def test_loader_derives_route_and_week(shipments):
    assert (shipments["week_of"].dt.weekday == 0).all()
    assert set(shipments["route"]) == {
        "Mumbai-Pune", "Delhi-Jaipur", "Chennai-Bangalore", "Kolkata-Bhubaneswar",
        "Ahmedabad-Mumbai", "Mumbai-Delhi", "Delhi-Chennai",
    }


@pytest.mark.parametrize("column, bad_value", [
    ("quantity_tonnes", 0),
    ("distance_km", -5),
    ("freight_cost_inr", "abc"),
    ("shipment_date", "2024-13-45"),
    ("route_type", "Ultra"),
])
def test_validation_rejects_bad_values(column, bad_value):
    df = pd.read_csv(SHIPMENTS_CSV).head(20)
    df[column] = df[column].astype(object)
    df.loc[0, column] = bad_value
    with pytest.raises(DataValidationError):
        validate_shipments(df)


# --- Stage 2: graded numbers reproduce the sample output exactly ------------------------

# The only figures the case-study authors computed themselves, copied from
# data/sample_output_format_v2.csv: (route, week_of, cost_per_tonne_km, vs_own_history %, vs_similar_routes %).
SAMPLE_ROWS = [
    ("Delhi-Jaipur", "2024-11-11", 4.17, 35.5, 21.0),
    ("Ahmedabad-Mumbai", "2025-01-20", 3.29, 29.5, 22.5),
    ("Mumbai-Pune", "2025-09-15", 3.98, 9.2, 23.6),
]


@pytest.mark.parametrize("route, week, cost, vs_history, vs_peers", SAMPLE_ROWS)
def test_reproduces_sample_output_numbers(metrics, route, week, cost, vs_history, vs_peers):
    row = _row(metrics, route, week)
    assert round(row["cost_per_tonne_km"], 2) == cost
    assert round(row["vs_own_history_pct"], 1) == vs_history
    assert round(row["vs_similar_routes_pct"], 1) == vs_peers


# --- Stage 2: baseline definitions -------------------------------------------------------

def test_history_has_no_look_ahead(shipments):
    """Inflating the current week and every later week must not move this week's baseline."""
    week = pd.Timestamp("2025-03-03")
    before = _row(compute_weekly_metrics(shipments), "Chennai-Bangalore", week)

    tampered = shipments.copy()
    future = (tampered["route"] == "Chennai-Bangalore") & (tampered["week_of"] >= week)
    tampered.loc[future, "freight_cost_inr"] *= 10
    after = _row(compute_weekly_metrics(tampered), "Chennai-Bangalore", week)

    assert after["history_avg"] == pytest.approx(before["history_avg"])
    assert after["cost_per_tonne_km"] == pytest.approx(before["cost_per_tonne_km"] * 10)


def test_history_uses_only_available_prior_weeks_early_on(metrics):
    first = pd.Timestamp("2024-01-01")
    for weeks_in, expected_used in [(0, 0), (1, 1), (3, 3), (8, 8), (20, 8)]:
        week = first + pd.Timedelta(weeks=weeks_in)
        rows = metrics[metrics["week_of"] == week]
        assert (rows["history_weeks_used"] == expected_used).all()
    assert metrics.loc[metrics["week_of"] == first, "history_avg"].isna().all()


def test_peer_average_excludes_the_route_itself(metrics):
    row = _row(metrics, "Ahmedabad-Mumbai", "2025-01-20")
    peers = metrics[
        (metrics["week_of"] == pd.Timestamp("2025-01-20"))
        & (metrics["route_type"] == "Medium")
        & (metrics["route"] != "Ahmedabad-Mumbai")
    ]
    assert row["n_peer_routes"] == 2
    assert row["peer_avg"] == pytest.approx(peers["cost_per_tonne_km"].mean())


def test_every_route_has_a_peer(metrics):
    # Short and Long have two routes each, so every route has exactly one or two peers.
    assert metrics["peer_avg"].notna().all()
    assert set(metrics["n_peer_routes"]) == {1, 2}


# --- Stage 2: independent re-implementation cross-check ---------------------------------

def test_matches_independent_implementation_on_every_row(metrics):
    expected = independent_metrics(SHIPMENTS_CSV)
    assert len(metrics) == len(expected)

    for _, row in metrics.iterrows():
        value, hist, peer = expected[(row["route"], row["week_of"].date())]
        assert row["cost_per_tonne_km"] == pytest.approx(value, rel=1e-9)
        assert row["vs_similar_routes_pct"] == pytest.approx(peer, rel=1e-9)
        if hist is None:
            assert pd.isna(row["vs_own_history_pct"])
        else:
            assert row["vs_own_history_pct"] == pytest.approx(hist, rel=1e-9)
