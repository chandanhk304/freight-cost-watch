"""Stage 2: weekly cost per tonne-km per route, and the two comparison baselines.

All values are kept at full precision; rounding happens only when writing output.
"""
import pandas as pd

from config import HISTORY_WINDOW_WEEKS


def weekly_route_costs(shipments: pd.DataFrame) -> pd.DataFrame:
    """One row per (route, week): total cost / total tonne-km, i.e. a tonne-km-weighted average.

    This matches the brief's formula applied to the week's totals and reproduces the
    sample output exactly; averaging per-shipment ratios does not.
    """
    weekly = (
        shipments.groupby(["route", "route_type", "week_of"], as_index=False)
        .agg(
            total_cost_inr=("freight_cost_inr", "sum"),
            total_tonne_km=("tonne_km", "sum"),
            n_shipments=("shipment_id", "count"),
        )
    )
    weekly["cost_per_tonne_km"] = weekly["total_cost_inr"] / weekly["total_tonne_km"]
    return weekly.sort_values(["route", "week_of"], ignore_index=True)


def add_history_baseline(weekly: pd.DataFrame, window_weeks: int = HISTORY_WINDOW_WEEKS) -> pd.DataFrame:
    """Trailing average of the route's own cost over the previous `window_weeks` calendar weeks.

    The window is [week - window_weeks, week): the current week is excluded, so there is no
    look-ahead. Early weeks use whatever prior weeks exist (`history_weeks_used` records how
    many); the first week of each route has no history and gets NaN.
    """
    window = pd.Timedelta(weeks=window_weeks)
    parts = []
    for _, route_df in weekly.groupby("route", sort=True):
        series = route_df.set_index("week_of")["cost_per_tonne_km"].sort_index()
        rolling = series.rolling(window, closed="left")
        parts.append(
            pd.DataFrame({
                "route": route_df["route"].iloc[0],
                "week_of": series.index,
                "history_avg": rolling.mean().to_numpy(),
                "history_weeks_used": rolling.count().fillna(0).astype(int).to_numpy(),
            })
        )
    history = pd.concat(parts, ignore_index=True)

    out = weekly.merge(history, on=["route", "week_of"], how="left", validate="one_to_one")
    out["vs_own_history_pct"] = (out["cost_per_tonne_km"] / out["history_avg"] - 1) * 100
    return out


def add_peer_baseline(weekly: pd.DataFrame) -> pd.DataFrame:
    """Average cost of the *other* routes with the same route_type in the same week.

    Each peer route counts once (simple mean of route-level weekly costs), matching the sample
    output; pooling peer tonne-km would weight busy routes more heavily and does not match.
    """
    out = weekly.copy()
    group = out.groupby(["route_type", "week_of"])["cost_per_tonne_km"]
    group_sum = group.transform("sum")
    group_count = group.transform("count")

    out["n_peer_routes"] = group_count - 1
    peer_sum = group_sum - out["cost_per_tonne_km"]
    out["peer_avg"] = (peer_sum / out["n_peer_routes"]).where(out["n_peer_routes"] > 0)
    out["vs_similar_routes_pct"] = (out["cost_per_tonne_km"] / out["peer_avg"] - 1) * 100
    return out


def compute_weekly_metrics(shipments: pd.DataFrame, window_weeks: int = HISTORY_WINDOW_WEEKS) -> pd.DataFrame:
    weekly = weekly_route_costs(shipments)
    weekly = add_history_baseline(weekly, window_weeks)
    weekly = add_peer_baseline(weekly)
    return weekly.sort_values(["week_of", "route"], ignore_index=True)
