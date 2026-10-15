"""Stage 3: decide which route-weeks are rising in a way that looks out of the ordinary.

Two signals, both rule-based so the same input always gives the same candidates:

1. history_spike    - cost jumped vs the route's own trailing average.
2. peer_divergence  - the route pulled away from its peers by much more than its usual gap,
                      while its own cost is also rising (so a falling peer cannot trigger it).

A row is an anomaly if either signal fires. Whether a note justifies it is decided later.
"""
import pandas as pd

from config import (
    HISTORY_SPIKE_PCT,
    MIN_HISTORY_WEEKS_FOR_SIGNAL,
    MIN_WEEKS_FOR_USUAL_PEER_GAP,
    PEER_GAP_EXCESS_PP,
)


def add_usual_peer_gap(metrics: pd.DataFrame) -> pd.DataFrame:
    """Median of the route's vs_similar_routes_pct over all strictly prior weeks."""
    out = metrics.sort_values(["route", "week_of"]).copy()
    out["usual_peer_gap_pct"] = out.groupby("route")["vs_similar_routes_pct"].transform(
        lambda gaps: gaps.shift(1).expanding(min_periods=MIN_WEEKS_FOR_USUAL_PEER_GAP).median()
    )
    out["peer_gap_excess_pp"] = out["vs_similar_routes_pct"] - out["usual_peer_gap_pct"]
    return out


def detect_anomalies(metrics: pd.DataFrame) -> pd.DataFrame:
    """Add signal columns and `is_anomaly`. Graded metric columns are left untouched."""
    out = add_usual_peer_gap(metrics)

    enough_history = out["history_weeks_used"] >= MIN_HISTORY_WEEKS_FOR_SIGNAL
    rising = out["vs_own_history_pct"] > 0

    # NaN comparisons evaluate to False, so rows without a baseline never fire.
    out["history_spike"] = enough_history & (out["vs_own_history_pct"] >= HISTORY_SPIKE_PCT)
    out["peer_divergence"] = rising & (out["peer_gap_excess_pp"] >= PEER_GAP_EXCESS_PP)
    out["is_anomaly"] = out["history_spike"] | out["peer_divergence"]
    out["anomaly_signals"] = out.apply(_describe_signals, axis=1)

    return out.sort_values(["week_of", "route"], ignore_index=True)


def _describe_signals(row: pd.Series) -> str:
    signals = []
    if row["history_spike"]:
        signals.append("history_spike")
    if row["peer_divergence"]:
        signals.append("peer_divergence")
    return "+".join(signals)
