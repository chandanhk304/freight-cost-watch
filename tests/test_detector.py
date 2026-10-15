"""Stage 3 tests: signal rules on synthetic data, then stages 1-3 together on the real file."""
import hashlib

import pandas as pd
import pytest

from config import (
    HISTORY_SPIKE_PCT,
    MIN_HISTORY_WEEKS_FOR_SIGNAL,
    MIN_WEEKS_FOR_USUAL_PEER_GAP,
    PEER_GAP_EXCESS_PP,
    SHIPMENTS_CSV,
)
from src.detector import detect_anomalies
from src.loader import load_shipments
from src.metrics import compute_weekly_metrics
from src.pipeline import analyse_route_weeks


# --- Unit tests on synthetic weekly metrics ------------------------------------------------

def _synthetic(route_costs: dict, route_type="Short", weeks=20):
    """Build stage-2 metrics from explicit per-route weekly costs."""
    rows = []
    start = pd.Timestamp("2024-01-01")
    for route, costs in route_costs.items():
        for i, cost in enumerate(costs[:weeks]):
            rows.append({"route": route, "route_type": route_type,
                         "week_of": start + pd.Timedelta(weeks=i),
                         "total_cost_inr": cost, "total_tonne_km": 1.0, "n_shipments": 1})
    from src.metrics import add_history_baseline, add_peer_baseline
    weekly = pd.DataFrame(rows).assign(cost_per_tonne_km=lambda d: d["total_cost_inr"])
    weekly = weekly.sort_values(["route", "week_of"], ignore_index=True)
    return detect_anomalies(add_peer_baseline(add_history_baseline(weekly)))


def _flags(result, route):
    return result[result["route"] == route].sort_values("week_of")["is_anomaly"].tolist()


def test_flat_routes_produce_no_anomalies():
    result = _synthetic({"A": [3.0] * 20, "B": [3.3] * 20})
    assert not result["is_anomaly"].any()


def test_single_week_spike_fires_history_signal():
    a = [3.0] * 20
    a[12] = 3.0 * (1 + (HISTORY_SPIKE_PCT + 5) / 100)
    result = _synthetic({"A": a, "B": [3.0] * 20})
    flagged = result[result["is_anomaly"]]
    assert list(flagged["route"]) == ["A"]
    assert flagged["history_spike"].all()


def test_spike_below_threshold_is_ignored():
    a = [3.0] * 20
    a[12] = 3.0 * (1 + (HISTORY_SPIKE_PCT - 2) / 100)
    result = _synthetic({"A": a, "B": [3.0] * 20})
    assert not result["is_anomaly"].any()


def test_no_history_signal_before_minimum_weeks():
    a = [3.0] * 20
    a[MIN_HISTORY_WEEKS_FOR_SIGNAL - 1] = 4.5  # +50%, but only MIN-1 prior weeks
    result = _synthetic({"A": a, "B": [3.0] * 20})
    assert not result["history_spike"].any()


def test_falling_peer_does_not_flag_the_steady_route():
    """B collapses, so A's gap to B widens, but A's own cost is flat: not A's anomaly."""
    b = [3.0] * 12 + [2.0] * 8
    result = _synthetic({"A": [3.0] * 20, "B": b})
    assert not result.loc[result["route"] == "A", "is_anomaly"].any()


def test_gradual_creep_is_caught_by_peer_divergence():
    """A creeps 2%/week: each week is too small for the history signal, but A drifts far
    from its peer, which the history baseline (it creeps along with A) cannot see."""
    a = [3.0] * 10 + [3.0 * 1.02 ** k for k in range(1, 11)]
    result = _synthetic({"A": a, "B": [3.0] * 20})
    route_a = result[result["route"] == "A"]
    assert not route_a["history_spike"].any()
    assert route_a["peer_divergence"].any()


def test_structural_peer_gap_is_not_an_anomaly():
    """A always costs 15% more than B: a large raw gap that is normal for this route."""
    result = _synthetic({"A": [3.45] * 20, "B": [3.0] * 20})
    assert (result.loc[result["route"] == "A", "vs_similar_routes_pct"].round(1) == 15.0).all()
    assert not result["is_anomaly"].any()


def test_usual_gap_needs_minimum_weeks():
    result = _synthetic({"A": [3.0] * 20, "B": [3.0] * 20})
    first_weeks = result.sort_values("week_of").groupby("route").head(MIN_WEEKS_FOR_USUAL_PEER_GAP)
    assert first_weeks["usual_peer_gap_pct"].isna().all()


# --- Stages 1-3 connected, on the real data -----------------------------------------------

@pytest.fixture(scope="module")
def analysis():
    return analyse_route_weeks(SHIPMENTS_CSV)


def _row(analysis, route, week):
    match = analysis[(analysis["route"] == route) & (analysis["week_of"] == pd.Timestamp(week))]
    assert len(match) == 1
    return match.iloc[0]


def test_pipeline_keeps_every_route_week_and_graded_numbers(analysis):
    """Stage 3 adds columns only: the stage-2 numbers must come through unchanged."""
    metrics = compute_weekly_metrics(load_shipments(SHIPMENTS_CSV))
    graded = ["route", "week_of", "cost_per_tonne_km", "vs_own_history_pct", "vs_similar_routes_pct"]
    key = ["route", "week_of"]
    pd.testing.assert_frame_equal(
        analysis[graded].sort_values(key, ignore_index=True),
        metrics[graded].sort_values(key, ignore_index=True),
    )


@pytest.mark.parametrize("route, week", [
    ("Delhi-Jaipur", "2024-11-11"),
    ("Ahmedabad-Mumbai", "2025-01-20"),
    ("Mumbai-Pune", "2025-09-15"),
])
def test_every_sample_output_row_is_an_anomaly(analysis, route, week):
    """All three sample rows are flagged or justified, so each must reach the note check."""
    assert _row(analysis, route, week)["is_anomaly"]


def test_calm_period_has_no_anomalies(analysis):
    """Jan-Oct 2024: no note describes a cost-raising event, so nothing should fire."""
    calm = analysis[analysis["week_of"] < pd.Timestamp("2024-11-01")]
    assert not calm["is_anomaly"].any(), calm[calm["is_anomaly"]][["route", "week_of"]]


def test_first_week_of_each_route_is_never_an_anomaly(analysis):
    first = analysis[analysis["week_of"] == analysis["week_of"].min()]
    assert not first["is_anomaly"].any()


def test_pipeline_is_deterministic():
    digest = lambda: hashlib.sha256(
        analyse_route_weeks(SHIPMENTS_CSV).to_csv(index=False).encode()
    ).hexdigest()
    assert digest() == digest()
