"""Write output.csv in the exact contract format (columns, order, values)."""
import csv
from pathlib import Path

import pandas as pd

from config import FLAG_JUSTIFIED, FLAG_NORMAL, FLAG_YES, OUTPUT_COLUMNS
from src.explainer import format_history, format_peers


def to_output_frame(results: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({
        "route": results["route"],
        "week_of": results["week_of"].dt.strftime("%Y-%m-%d"),
        "cost_per_tonne_km": results["cost_per_tonne_km"].round(2).map(lambda v: f"{v:.2f}"),
        "vs_own_history": results.apply(format_history, axis=1),
        "vs_similar_routes": results.apply(format_peers, axis=1),
        "flagged": results["flagged"],
        "matched_note_id": results["matched_note_id"].fillna(""),
        "reason": results["reason"],
    })[OUTPUT_COLUMNS]
    _check_contract(out)
    return out.sort_values(["route", "week_of"], ignore_index=True)


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # QUOTE_MINIMAL quotes any field containing a comma, unlike the provided sample file.
    frame.to_csv(path, index=False, quoting=csv.QUOTE_MINIMAL, lineterminator="\n", encoding="utf-8")


def _check_contract(out: pd.DataFrame) -> None:
    assert list(out.columns) == OUTPUT_COLUMNS
    assert set(out["flagged"]) <= {FLAG_YES, FLAG_JUSTIFIED, FLAG_NORMAL}
    justified = out["flagged"] == FLAG_JUSTIFIED
    assert (out.loc[justified, "matched_note_id"] != "").all(), "justified rows must cite a note"
    assert (out.loc[~justified, "matched_note_id"] == "").all(), "only justified rows may cite a note"
    assert out["reason"].str.len().gt(0).all()
