"""Stage 0 (structured notes, LLM extraction) and stage 4 (retrieval) tests."""
import copy
import json
from datetime import date

import pandas as pd
import pytest

from config import NOTES_CSV, NOTES_STRUCTURED_JSON, NEARBY_NOTE_MAX_DAYS, SHIPMENTS_CSV
from src.llm import LLMClient, LLMUnavailable
from src.note_extraction import compare_to_reviewed, extract_note
from src.notes import NoteValidationError, load_notes, load_raw_notes, validate_structured_note
from src.pipeline import run_stages_1_to_4
from tests.fakes import FakeGroq
from src.retriever import (
    NoteRetriever,
    RankedNote,
    RetrievalResult,
    days_between_week_and_window,
    rejection_reasons,
)

ROUTES = {"Mumbai-Pune", "Delhi-Jaipur", "Chennai-Bangalore", "Kolkata-Bhubaneswar",
          "Ahmedabad-Mumbai", "Mumbai-Delhi", "Delhi-Chennai"}
REVIEWED = json.loads(NOTES_STRUCTURED_JSON.read_text(encoding="utf-8"))
RAW_TEXT = load_raw_notes(NOTES_CSV).set_index("note_id")["note"].to_dict()


@pytest.fixture(scope="module")
def notes():
    return {n.note_id: n for n in load_notes(NOTES_CSV, NOTES_STRUCTURED_JSON, ROUTES)}


@pytest.fixture(scope="module")
def retriever(notes):
    return NoteRetriever(list(notes.values()))


@pytest.fixture(scope="module")
def stage4():
    return run_stages_1_to_4(SHIPMENTS_CSV)


# --- Stage 0: reviewed structure ----------------------------------------------------------

def test_reviewed_notes_load_and_cover_every_raw_note(notes):
    assert sorted(notes) == sorted(RAW_TEXT)


def test_only_three_notes_can_ever_justify_a_rise(notes):
    """Decoys (no_change / not_stated / no routes) must be structurally unable to justify."""
    can_justify = {nid for nid, n in notes.items() if n.cost_effect == "increase" and n.routes}
    assert can_justify == {"N001", "N002", "N003"}


@pytest.mark.parametrize("mutate, message", [
    (lambda s: s.update(evidence="costs rose sharply due to flooding"), "verbatim"),
    (lambda s: s.update(routes=["Pune-Goa"]), "unknown routes"),
    (lambda s: s.update(cost_effect="spike"), "cost_effect"),
    (lambda s: s.update(effective_to="2025-01-01"), "before"),
    (lambda s: s.update(effective_from="Feb 24"), "bad date"),
    (lambda s: s.pop("evidence"), "missing"),
])
def test_validation_rejects_bad_structure(mutate, message):
    record = copy.deepcopy(next(s for s in REVIEWED if s["note_id"] == "N001"))
    mutate(record)
    with pytest.raises(NoteValidationError, match=message):
        validate_structured_note(record, RAW_TEXT["N001"], ROUTES)


def test_week_overlap_edges(notes):
    n001 = notes["N001"]  # flood window 2025-02-24 (Mon) to 2025-03-08 (Sat)
    assert n001.overlaps_week(date(2025, 2, 24))
    assert n001.overlaps_week(date(2025, 3, 3))       # Mon 3 - Sun 9 contains Mar 8
    assert not n001.overlaps_week(date(2025, 3, 10))  # starts two days after the window ends
    assert not n001.overlaps_week(date(2025, 2, 17))
    n002 = notes["N002"]  # one festival week
    assert n002.overlaps_week(date(2025, 1, 20))
    assert not n002.overlaps_week(date(2025, 1, 27))
    assert notes["N003"].overlaps_week(date(2025, 12, 22))  # open-ended


def test_days_between_week_and_window(notes):
    assert days_between_week_and_window(notes["N001"], date(2025, 3, 3)) == 0
    assert days_between_week_and_window(notes["N001"], date(2025, 3, 10)) == 2   # ended Mar 8
    assert days_between_week_and_window(notes["N009"], date(2025, 3, 3)) == 8    # starts Mar 17, week ends Mar 9


# --- Stage 4: eligibility rules ------------------------------------------------------------

def test_eligibility_invariant_holds_for_every_route_week_and_note(notes, stage4):
    """Exhaustive: 728 route-weeks x 10 notes. A note is eligible only if it applies to the
    route, its window overlaps the week, and it describes a cost increase."""
    for route, week in stage4[["route", "week_of"]].itertuples(index=False):
        for note in notes.values():
            eligible = not rejection_reasons(note, route, week.date())
            expected = (bool(note.routes) and note.applies_to_route(route)
                        and note.overlaps_week(week.date()) and note.cost_effect == "increase")
            assert eligible == expected, (route, week, note.note_id)


@pytest.mark.parametrize("route, week, expected_eligible", [
    ("Ahmedabad-Mumbai", "2025-01-20", ["N002"]),
    ("Ahmedabad-Mumbai", "2025-01-27", []),              # festival week is over
    ("Chennai-Bangalore", "2025-02-24", ["N001"]),
    ("Chennai-Bangalore", "2025-03-03", ["N001"]),
    ("Chennai-Bangalore", "2025-03-10", []),             # flood window ended Mar 8
    ("Chennai-Bangalore", "2025-03-17", []),             # N009 says back to normal
    ("Delhi-Jaipur", "2024-11-11", []),                  # N007 is not a cost reason
    ("Mumbai-Delhi", "2024-07-29", []),                  # N005: costs not affected
    ("Kolkata-Bhubaneswar", "2025-05-12", ["N003"]),     # N008 no_change, diesel applies
    ("Mumbai-Pune", "2025-09-15", ["N003"]),             # N006 no_change; diesel passes filters
])
def test_expected_eligible_notes(retriever, route, week, expected_eligible):
    result = retriever.retrieve(route, date.fromisoformat(week))
    assert sorted(r.note.note_id for r in result.eligible) == expected_eligible


def test_retrieval_ranks_every_note_with_reasons(retriever):
    result = retriever.retrieve("Chennai-Bangalore", date(2025, 3, 10))
    assert len(result.ranked) == 10
    reasons = {r.note.note_id: r.rejection_reasons for r in result.ranked}
    assert any("does not cover" in x for x in reasons["N001"])
    assert any("no cost increase" in x for x in reasons["N009"])
    assert any("not Chennai-Bangalore" in x for x in reasons["N002"])
    assert reasons["N004"][0] == "applies to no route in this dataset"


def test_retrieval_is_deterministic(notes):
    a = NoteRetriever(list(notes.values())).retrieve("Mumbai-Pune", date(2025, 9, 15))
    b = NoteRetriever(list(notes.values())).retrieve("Mumbai-Pune", date(2025, 9, 15))
    assert [(r.note.note_id, r.distance) for r in a.ranked] == [(r.note.note_id, r.distance) for r in b.ranked]


@pytest.mark.parametrize("route, week, expected", [
    ("Mumbai-Pune", "2025-09-15", "N006"),        # the note the sample output discusses
    ("Chennai-Bangalore", "2025-03-17", "N009"),
    ("Delhi-Jaipur", "2024-11-11", "N007"),
    ("Mumbai-Delhi", "2025-05-12", None),         # nothing relevant within 4 weeks
])
def test_nearest_relevant_rejected_note(retriever, route, week, expected):
    nearest = retriever.retrieve(route, date.fromisoformat(week)).nearest_relevant_rejected()
    assert (nearest.note.note_id if nearest else None) == expected


def test_nearest_rejected_never_names_another_route_or_a_distant_note(stage4, notes):
    """Exhaustive over all route-weeks. Built without embeddings (equal distances) because the
    rule under test is pure metadata logic; this keeps the test fast."""
    for route, week in stage4[["route", "week_of"]].itertuples(index=False):
        ranked = tuple(RankedNote(n, 0.5, rejection_reasons(n, route, week.date())) for n in notes.values())
        nearest = RetrievalResult(route, week.date(), "", ranked).nearest_relevant_rejected()
        if nearest:
            assert nearest.note.applies_to_route(route)
            assert days_between_week_and_window(nearest.note, week.date()) <= NEARBY_NOTE_MAX_DAYS


# --- Stages 1-4 connected ------------------------------------------------------------------

def test_retrieval_runs_for_anomalies_only(stage4):
    normal = stage4[~stage4["is_anomaly"]]
    assert (normal["retrieval_trace"] == "").all()
    anomalies = stage4[stage4["is_anomaly"]]
    assert (anomalies["retrieval_trace"] != "").all()
    assert len(json.loads(anomalies["retrieval_trace"].iloc[0])["ranked"]) == 10


def test_stage4_does_not_change_earlier_stages(stage4):
    from src.pipeline import analyse_route_weeks
    before = analyse_route_weeks(SHIPMENTS_CSV)
    pd.testing.assert_frame_equal(stage4[before.columns], before)


# --- Stage 0: LLM extraction, with a stand-in client (no network) -------------------------

def _raw(note_id):
    return load_raw_notes(NOTES_CSV).set_index("note_id").loc[note_id].to_dict() | {"note_id": note_id}


def test_extraction_accepts_a_faithful_reply_and_logs_tokens():
    gold = next(s for s in REVIEWED if s["note_id"] == "N001")
    llm = LLMClient(groq_client=FakeGroq([json.dumps({k: gold[k] for k in gold if k != "annotation_note"})]))
    record = extract_note(llm, _raw("N001"), ROUTES)
    assert record["validation_error"] is None
    assert llm.usage.calls == 1 and llm.usage.input_tokens == 300 and llm.usage.output_tokens == 60


def test_extraction_flags_invented_evidence():
    reply = {"routes": ["Chennai-Bangalore"], "effective_from": "2025-02-24", "effective_to": "2025-03-08",
             "cost_effect": "increase", "magnitude_pct": None, "evidence": "costs doubled due to floods"}
    record = extract_note(LLMClient(groq_client=FakeGroq([json.dumps(reply)])), _raw("N001"), ROUTES)
    assert "verbatim" in record["validation_error"]


def test_extraction_flags_non_json_reply():
    record = extract_note(LLMClient(groq_client=FakeGroq(["not json"])), _raw("N001"), ROUTES)
    assert record["validation_error"]


def test_comparison_scores_fields_and_highlights_dangerous_errors():
    extracted = [{k: v for k, v in s.items() if k != "annotation_note"} | {"validation_error": None}
                 for s in REVIEWED]
    wrong = next(e for e in extracted if e["note_id"] == "N009")
    wrong["cost_effect"] = "increase"  # the hallucination that would cause a fabricated verdict
    report = compare_to_reviewed(extracted, REVIEWED)
    assert report["field_accuracy"]["cost_effect"] == "9/10"
    assert report["field_accuracy"]["routes"] == "10/10"
    assert list(report["permissive_errors"]) == ["N009"]


def test_missing_key_raises_llm_unavailable(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr("src.llm.load_dotenv", lambda *a, **k: None)
    with pytest.raises(LLMUnavailable):
        LLMClient().complete("test", "system", "user")
