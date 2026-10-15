"""Stage 0: context notes as structured, validated facts.

Verdicts never read free text. They use the reviewed fields below, and every field is tied
back to the source note by an `evidence` quote that must appear verbatim in the note.
"""
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from config import ALL_ROUTES, COST_EFFECTS


class NoteValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Note:
    note_id: str
    note_date: date
    applies_to: str
    text: str
    routes: tuple[str, ...]
    effective_from: date
    effective_to: date | None  # None = open-ended
    cost_effect: str
    magnitude_pct: tuple[float, float] | None
    evidence: str

    def applies_to_route(self, route: str) -> bool:
        return ALL_ROUTES in self.routes or route in self.routes

    def overlaps_week(self, week_of: date) -> bool:
        week_end = week_of + timedelta(days=6)
        starts_in_time = self.effective_from <= week_end
        not_over_yet = self.effective_to is None or self.effective_to >= week_of
        return starts_in_time and not_over_yet

    def window_text(self) -> str:
        end = self.effective_to.isoformat() if self.effective_to else "open-ended"
        return f"{self.effective_from.isoformat()} to {end}"


def load_raw_notes(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str)


def load_notes(notes_csv: Path, structured_json: Path, known_routes: set[str]) -> list[Note]:
    """Join the raw notes with their reviewed structure, validating both sides."""
    raw = load_raw_notes(notes_csv).set_index("note_id")
    structured = json.loads(Path(structured_json).read_text(encoding="utf-8"))

    structured_ids = [s["note_id"] for s in structured]
    if sorted(structured_ids) != sorted(raw.index):
        raise NoteValidationError(
            f"Structured notes {sorted(structured_ids)} do not match raw notes {sorted(raw.index)}"
        )

    notes = []
    for s in structured:
        source = raw.loc[s["note_id"]]
        validate_structured_note(s, source["note"], known_routes)
        notes.append(Note(
            note_id=s["note_id"],
            note_date=date.fromisoformat(source["date"]),
            applies_to=source["applies_to"],
            text=source["note"],
            routes=tuple(s["routes"]),
            effective_from=date.fromisoformat(s["effective_from"]),
            effective_to=date.fromisoformat(s["effective_to"]) if s["effective_to"] else None,
            cost_effect=s["cost_effect"],
            magnitude_pct=tuple(s["magnitude_pct"]) if s["magnitude_pct"] else None,
            evidence=s["evidence"],
        ))
    return sorted(notes, key=lambda n: n.note_id)


def validate_structured_note(s: dict, note_text: str, known_routes: set[str]) -> None:
    """Shared by the reviewed file and LLM extraction, so both meet the same bar."""
    nid = s.get("note_id", "?")
    required = {"note_id", "routes", "effective_from", "effective_to", "cost_effect",
                "magnitude_pct", "evidence"}
    missing = required - s.keys()
    if missing:
        raise NoteValidationError(f"{nid}: missing fields {sorted(missing)}")

    unknown = set(s["routes"]) - known_routes - {ALL_ROUTES}
    if unknown:
        raise NoteValidationError(f"{nid}: unknown routes {sorted(unknown)}")

    if s["cost_effect"] not in COST_EFFECTS:
        raise NoteValidationError(f"{nid}: cost_effect must be one of {COST_EFFECTS}")

    try:
        start = date.fromisoformat(s["effective_from"])
        end = date.fromisoformat(s["effective_to"]) if s["effective_to"] else None
    except (TypeError, ValueError) as exc:
        raise NoteValidationError(f"{nid}: bad date ({exc})") from exc
    if end is not None and end < start:
        raise NoteValidationError(f"{nid}: effective_to is before effective_from")

    magnitude = s["magnitude_pct"]
    if magnitude is not None and (len(magnitude) != 2 or magnitude[0] > magnitude[1]):
        raise NoteValidationError(f"{nid}: magnitude_pct must be [low, high]")

    # The anti-hallucination anchor: the claimed evidence must be quoted from the note itself.
    if not s["evidence"] or s["evidence"] not in note_text:
        raise NoteValidationError(f"{nid}: evidence is not a verbatim quote from the note")
