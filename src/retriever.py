"""Stage 4: find the notes that could explain an anomaly.

Two layers with different jobs:
- Vector search (Chroma + MiniLM embeddings) ranks every note by semantic similarity to the
  anomaly. It decides nothing on its own. Measured on this corpus, it is a weak signal: it
  latches onto city names (N005, about Mumbai-Delhi, outranks the correct N002 for
  Ahmedabad-Mumbai) and no query phrasing ranked the right note first for every route.
- Hard eligibility rules (route, date window, cost effect) on the reviewed note structure
  decide which notes may be used. Rejected notes are kept with their reasons, and the most
  relevant one (same route, then all-routes, closest in time) is surfaced so explanations can
  say why a nearby note does not count. Notes about other routes are never surfaced.
"""
import itertools
from dataclasses import dataclass, field
from datetime import date, timedelta

import chromadb
from chromadb.config import Settings

from config import NEARBY_NOTE_MAX_DAYS
from src.notes import Note

COLLECTION_NAME = "context_notes"
# EphemeralClient shares state within a process, so every index needs a unique name.
# id(self) is not enough: Python reuses ids after garbage collection.
_collection_ids = itertools.count()


@dataclass(frozen=True)
class RankedNote:
    note: Note
    distance: float                 # cosine distance, lower = more similar
    rejection_reasons: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        return not self.rejection_reasons


@dataclass(frozen=True)
class RetrievalResult:
    route: str
    week_of: date
    query: str
    ranked: tuple[RankedNote, ...] = field(default_factory=tuple)

    @property
    def eligible(self) -> list[RankedNote]:
        return [r for r in self.ranked if r.eligible]

    @property
    def rejected(self) -> list[RankedNote]:
        return [r for r in self.ranked if not r.eligible]

    def nearby_relevant_rejected(self) -> list[RankedNote]:
        """Rejected notes a reader would expect to see discussed for this row: they must apply
        to this route (or all routes) and sit within NEARBY_NOTE_MAX_DAYS of the week. Ordered by
        closeness in time, then route-specific before all-routes, then semantic distance."""
        relevant = [
            r for r in self.rejected
            if r.note.routes
            and r.note.applies_to_route(self.route)
            and days_between_week_and_window(r.note, self.week_of) <= NEARBY_NOTE_MAX_DAYS
        ]
        return sorted(relevant, key=lambda r: (
            days_between_week_and_window(r.note, self.week_of),
            0 if self.route in r.note.routes else 1,
            r.distance,
            r.note.note_id,
        ))

    def nearest_relevant_rejected(self) -> RankedNote | None:
        nearby = self.nearby_relevant_rejected()
        return nearby[0] if nearby else None


def days_between_week_and_window(note: Note, week_of: date) -> int:
    """0 if the note's window overlaps the week, else the gap in days."""
    if note.overlaps_week(week_of):
        return 0
    week_end = week_of + timedelta(days=6)
    if note.effective_from > week_end:
        return (note.effective_from - week_end).days
    return (week_of - note.effective_to).days


def rejection_reasons(note: Note, route: str, week_of: date) -> tuple[str, ...]:
    """Why a note cannot justify a cost rise on this route in this week (empty = eligible)."""
    reasons = []
    if not note.routes:
        reasons.append("applies to no route in this dataset")
    elif not note.applies_to_route(route):
        reasons.append(f"applies to {', '.join(note.routes)}, not {route}")
    if not note.overlaps_week(week_of):
        reasons.append(f"window {note.window_text()} does not cover week of {week_of.isoformat()}")
    if note.cost_effect != "increase":
        reasons.append(COST_EFFECT_REASONS[note.cost_effect])
    return tuple(reasons)


# Precise wording matters: "says costs did not change" and "does not mention costs" are
# different claims, and explanations must not blur them.
COST_EFFECT_REASONS = {
    "no_change": "it says costs were not affected, so it describes no cost increase",
    "not_stated": "it does not mention freight costs at all, so it describes no cost increase",
    "decrease": "it describes conditions that lower costs, so it describes no cost increase",
}


class NoteRetriever:
    """In-memory Chroma index, rebuilt each run from the notes: no hidden persisted state."""

    def __init__(self, notes: list[Note]):
        self._notes = {n.note_id: n for n in notes}
        client = chromadb.EphemeralClient(settings=Settings(anonymized_telemetry=False))
        name = f"{COLLECTION_NAME}_{next(_collection_ids)}"
        self._collection = client.create_collection(name=name, metadata={"hnsw:space": "cosine"})
        self._collection.add(
            ids=[n.note_id for n in notes],
            documents=[n.text for n in notes],
            metadatas=[{"applies_to": n.applies_to, "note_date": n.note_date.isoformat()} for n in notes],
        )

    @staticmethod
    def build_query(route: str, week_of: date) -> str:
        origin, destination = route.split("-", 1)
        return (f"Reason for higher freight costs on the {route} route "
                f"({origin} to {destination}) in the week of {week_of:%d %B %Y}")

    def retrieve(self, route: str, week_of: date) -> RetrievalResult:
        query = self.build_query(route, week_of)
        hits = self._collection.query(query_texts=[query], n_results=len(self._notes))
        ranked = []
        for note_id, distance in zip(hits["ids"][0], hits["distances"][0]):
            note = self._notes[note_id]
            ranked.append(RankedNote(
                note=note,
                # Rounded so float noise in the embedding runtime cannot reorder ties.
                distance=round(float(distance), 4),
                rejection_reasons=rejection_reasons(note, route, week_of),
            ))
        ranked.sort(key=lambda r: (r.distance, r.note.note_id))
        return RetrievalResult(route=route, week_of=week_of, query=query, ranked=tuple(ranked))
