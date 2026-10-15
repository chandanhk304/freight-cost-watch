"""Stage 5: rule-based verdicts. The LLM never decides whether a row is justified.

A note justifies an anomaly only if it passed retrieval eligibility (right route, window covers
the week, describes a cost increase) AND it can explain this kind of rise:

- A note about this specific route explains any rise on it in its window.
- A note about all routes raises the peers' costs too, so it cannot explain the route pulling
  away from its peers. It can explain a rise vs the route's own history, but only if that
  rise is no bigger than the note's stated size plus normal noise.
"""
from dataclasses import dataclass, field

import pandas as pd

from config import (
    ALL_ROUTES_MAGNITUDE_TOLERANCE_PP,
    FLAG_JUSTIFIED,
    FLAG_NORMAL,
    FLAG_YES,
)
from src.notes import Note


@dataclass(frozen=True)
class ConsideredNote:
    note_id: str
    explains: bool
    why: str


@dataclass(frozen=True)
class Verdict:
    flagged: str
    matched_note_id: str | None
    considered: tuple[ConsideredNote, ...] = field(default_factory=tuple)


def note_explains(note: Note, row: pd.Series) -> tuple[bool, str]:
    """Can this eligible note explain this anomaly? Returns (decision, plain-English why)."""
    if row["route"] in note.routes:
        return True, f"it describes a cost-raising event on {row['route']} during this week"

    if row["peer_divergence"]:
        return False, ("it applies to all routes, so it raised similar routes' costs too and "
                       "cannot explain this route pulling away from them")

    if note.magnitude_pct is not None:
        low, high = note.magnitude_pct
        limit = high + ALL_ROUTES_MAGNITUDE_TOLERANCE_PP
        rise = row["vs_own_history_pct"]
        if rise > limit:
            return False, (f"the rise of {rise:+.1f}% is larger than the note's stated "
                           f"{low:g}-{high:g}% even allowing for normal weekly variation")
        return True, (f"it describes a {low:g}-{high:g}% rise on all routes from "
                      f"{note.effective_from.isoformat()}, consistent with this week's {rise:+.1f}%")

    return True, "it describes a cost-raising event on all routes during this week"


def decide_verdict(row: pd.Series, notes_by_id: dict[str, Note]) -> Verdict:
    if not row["is_anomaly"]:
        return Verdict(flagged=FLAG_NORMAL, matched_note_id=None)

    considered = []
    eligible_ids = [i for i in row["eligible_note_ids"].split(";") if i]
    # Route-specific notes first: they are the stronger evidence for a route-level rise.
    ordered = sorted(eligible_ids, key=lambda i: row["route"] not in notes_by_id[i].routes)
    for note_id in ordered:
        explains, why = note_explains(notes_by_id[note_id], row)
        considered.append(ConsideredNote(note_id, explains, why))
        if explains:
            return Verdict(FLAG_JUSTIFIED, note_id, tuple(considered))

    return Verdict(FLAG_YES, None, tuple(considered))


def attach_verdicts(analysis: pd.DataFrame, notes: list[Note]) -> pd.DataFrame:
    notes_by_id = {n.note_id: n for n in notes}
    out = analysis.copy()
    verdicts = [decide_verdict(row, notes_by_id) for _, row in out.iterrows()]
    out["flagged"] = [v.flagged for v in verdicts]
    out["matched_note_id"] = [v.matched_note_id or "" for v in verdicts]
    out["verdict_obj"] = verdicts
    return out
