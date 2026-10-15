"""Stage 5 (verdicts), stage 6 (explanations) and the final output, end to end."""
import csv
import json
from datetime import date

import pandas as pd
import pytest

import run
from config import FLAG_JUSTIFIED, FLAG_NORMAL, FLAG_YES, OUTPUT_COLUMNS, SHIPMENTS_CSV
from src.explainer import Explainer, build_facts, template_reason, validate_reason
from src.llm import LLMClient
from src.notes import Note
from src.pipeline import run_stages_1_to_5
from src.verdict import decide_verdict, note_explains
from tests.fakes import FakeGroq


@pytest.fixture(scope="module")
def stage5():
    results, notes = run_stages_1_to_5(SHIPMENTS_CSV)
    return results, {n.note_id: n for n in notes}


def _row(results, route, week):
    match = results[(results["route"] == route) & (results["week_of"] == pd.Timestamp(week))]
    assert len(match) == 1
    return match.iloc[0]


def _facts(stage5, route, week):
    results, notes_by_id = stage5
    row = _row(results, route, week)
    return build_facts(row, row["verdict_obj"], notes_by_id)


# --- Stage 5: verdict rules on synthetic inputs -------------------------------------------

def _note(routes, magnitude=None):
    return Note("N999", date(2025, 1, 1), "x", "x", tuple(routes), date(2025, 1, 1), None,
                "increase", magnitude, "x")


def _anomaly(history_pct, peer_divergence, route="A-B"):
    return pd.Series({"route": route, "vs_own_history_pct": history_pct, "peer_divergence": peer_divergence})


def test_route_specific_note_explains_any_rise():
    assert note_explains(_note(["A-B"]), _anomaly(40.0, True))[0]


def test_all_routes_note_cannot_explain_pulling_away_from_peers():
    explains, why = note_explains(_note(["ALL"], (5, 7)), _anomaly(6.0, True))
    assert not explains and "similar routes" in why


def test_all_routes_note_explains_a_rise_of_its_stated_size():
    assert note_explains(_note(["ALL"], (5, 7)), _anomaly(11.6, False))[0]


def test_all_routes_note_rejects_a_rise_far_beyond_its_stated_size():
    explains, why = note_explains(_note(["ALL"], (5, 7)), _anomaly(25.0, False))
    assert not explains and "larger than" in why


# --- Stage 5: verdicts on the real data ----------------------------------------------------

# The three rows the case-study authors labelled in sample_output_format_v2.csv.
@pytest.mark.parametrize("route, week, flagged, note", [
    ("Delhi-Jaipur", "2024-11-11", FLAG_YES, ""),
    ("Ahmedabad-Mumbai", "2025-01-20", FLAG_JUSTIFIED, "N002"),
    ("Mumbai-Pune", "2025-09-15", FLAG_YES, ""),
])
def test_sample_output_verdicts(stage5, route, week, flagged, note):
    row = _row(stage5[0], route, week)
    assert (row["flagged"], row["matched_note_id"]) == (flagged, note)


# Traps derived from the notes' text (not from the detector's output).
@pytest.mark.parametrize("route, week, flagged, note", [
    ("Chennai-Bangalore", "2025-02-24", FLAG_JUSTIFIED, "N001"),  # inside the flood window
    ("Chennai-Bangalore", "2025-03-03", FLAG_JUSTIFIED, "N001"),  # week contains Mar 8
    ("Chennai-Bangalore", "2025-03-10", FLAG_YES, ""),            # flood window over
    ("Chennai-Bangalore", "2025-03-17", FLAG_YES, ""),            # N009: back to normal
    ("Mumbai-Delhi", "2025-05-12", FLAG_JUSTIFIED, "N003"),       # diesel, size consistent
    ("Delhi-Jaipur", "2024-11-18", FLAG_YES, ""),                 # N007 is not a cost reason
])
def test_trap_verdicts(stage5, route, week, flagged, note):
    row = _row(stage5[0], route, week)
    assert (row["flagged"], row["matched_note_id"]) == (flagged, note)


def test_creep_is_never_justified_by_the_all_routes_diesel_note(stage5):
    creep = stage5[0][(stage5[0]["route"] == "Mumbai-Pune") & stage5[0]["is_anomaly"]]
    assert len(creep) == 11
    assert (creep["flagged"] == FLAG_YES).all()


def test_every_justified_verdict_passes_the_grader_audit(stage5):
    """The brief's spot-check, run on every justified row: the cited note must apply to that
    route, its window must cover that week, and it must describe a cost increase."""
    results, notes_by_id = stage5
    justified = results[results["flagged"] == FLAG_JUSTIFIED]
    assert len(justified) > 0
    for _, row in justified.iterrows():
        note = notes_by_id[row["matched_note_id"]]
        assert note.applies_to_route(row["route"]), row["route"]
        assert note.overlaps_week(row["week_of"].date()), row["week_of"]
        assert note.cost_effect == "increase"
        assert row["matched_note_id"] in row["eligible_note_ids"].split(";")


def test_non_anomalies_are_never_flagged_or_cited(stage5):
    normal = stage5[0][~stage5[0]["is_anomaly"]]
    assert (normal["flagged"] == FLAG_NORMAL).all()
    assert (normal["matched_note_id"] == "").all()


# --- Stage 6: validator regression tests (real replies seen during review) ------------------

@pytest.mark.parametrize("route, week, reply, problem", [
    ("Chennai-Bangalore", "2025-03-10",
     "No note justifies the rise; both checked notes (N009 and N001) do not cover the week of "
     "2025-03-10, and neither indicates a cost increase. The rise is flagged for human review.",
     "N001 shows no cost increase"),
    ("Mumbai-Pune", "2025-10-27",
     "No note justifies the rise; N003, N010, and N006 all either apply to all routes (so cannot "
     "explain this route pulling away) or describe no cost increase, and their windows do not cover "
     "the week of 2025-10-27, so the spike is flagged for human review.",
     "N003 shows no cost increase"),
    ("Delhi-Jaipur", "2024-11-11",
     "No note justifies the rise; the only checked note (N007) refers to road resurfacing and "
     "explicitly states it does not affect freight costs, so it does not explain the 35.5% increase. "
     "This case is flagged for human review.",
     "N007 states no cost change"),
    ("Delhi-Jaipur", "2024-11-11",
     "No note justifies the rise; it is flagged for human review. The only checked note, N007, ends "
     "at 2024-05-20 and states improved road conditions, not a cost increase.",
     "an end date"),
    ("Chennai-Bangalore", "2025-03-10",
     "No note justifies the rise; N012 covers it. Flagged for review.", "not in the facts"),
    ("Delhi-Jaipur", "2024-11-11",
     "No note justifies the rise; a 48.2% jump. Flagged for review.", "number 48.2"),
    ("Delhi-Jaipur", "2024-11-11",
     "No note justifies the rise; it was likely due to fuel prices. Flagged for review.", "fuel"),
    ("Delhi-Jaipur", "2024-11-11",
     "No note covers this week for Delhi-Jaipur.", "does not say it is flagged"),
    ("Ahmedabad-Mumbai", "2025-01-20",
     "The rise is explained by a festival surcharge.", "does not name the matched note"),
])
def test_validator_rejects_known_bad_replies(stage5, route, week, reply, problem):
    assert problem in (validate_reason(reply, _facts(stage5, route, week)) or "")


@pytest.mark.parametrize("route, week, reply", [
    ("Chennai-Bangalore", "2025-03-17",
     "No note justifies the rise; N009 says costs were unchanged and N001's window ended before "
     "this week, so the spike is flagged for human review."),
    ("Mumbai-Pune", "2025-09-15",
     "No note justifies the rise; N003 applies to all routes and would have increased similar routes "
     "too, so it cannot explain this route pulling away, and N006 states no cost change, so it does "
     "not account for the 23.6% increase. The anomaly is flagged for human review."),
    ("Chennai-Bangalore", "2025-02-24",
     "The rise is explained by note N001, which states that heavy flooding on the Chennai-Bangalore "
     "highway from 2025-02-24 to 2025-03-08 forced longer detours and higher trip costs."),
])
def test_validator_accepts_faithful_replies(stage5, route, week, reply):
    assert validate_reason(reply, _facts(stage5, route, week)) is None


def test_every_template_reason_passes_the_validator(stage5):
    """The fallback must itself be safe, or falling back would not protect anything."""
    results, notes_by_id = stage5
    for _, row in results[results["is_anomaly"]].iterrows():
        facts = build_facts(row, row["verdict_obj"], notes_by_id)
        assert validate_reason(template_reason(facts), facts) is None, (row["route"], row["week_of"])


# --- Stage 6: explainer behaviour with a fake LLM -------------------------------------------

def test_bad_reply_is_retried_then_falls_back_to_template(stage5, tmp_path):
    facts = _facts(stage5, "Delhi-Jaipur", "2024-11-11")
    fake = FakeGroq(["No note. Due to fuel. Flagged for review."] * 2)
    explainer = Explainer(LLMClient(groq_client=fake), tmp_path / "cache.json")
    reason, source = explainer.explain(facts)
    assert source == "template_fallback" and reason == template_reason(facts)
    assert len(fake.calls) == 2
    assert "rejected" in fake.calls[1]["messages"][1]["content"]


def test_cache_hit_makes_no_llm_call(stage5, tmp_path):
    facts = _facts(stage5, "Ahmedabad-Mumbai", "2025-01-20")
    good = "The rise is explained by note N002, a festival-week surcharge from 2025-01-20 to 2025-01-26."
    first = Explainer(LLMClient(groq_client=FakeGroq([good])), tmp_path / "cache.json")
    assert first.explain(facts) == (good, "llm")
    first.save_cache()

    fake = FakeGroq([])
    second = Explainer(LLMClient(groq_client=fake), tmp_path / "cache.json")
    assert second.explain(facts) == (good, "cache")
    assert fake.calls == []


def test_llm_outage_falls_back_without_crashing(stage5, tmp_path):
    facts = _facts(stage5, "Mumbai-Pune", "2025-09-15")
    fake = FakeGroq([], fail_with=ConnectionError("network down"))
    reason, source = Explainer(LLMClient(groq_client=fake), tmp_path / "c.json").explain(facts)
    assert source == "template_fallback" and "flagged" in reason


def test_normal_rows_never_call_the_llm(stage5, tmp_path):
    results, notes_by_id = stage5
    row = results[~results["is_anomaly"]].iloc[0]
    fake = FakeGroq([])
    reason, source = Explainer(LLMClient(groq_client=fake), tmp_path / "c.json").explain(
        build_facts(row, row["verdict_obj"], notes_by_id))
    assert source == "template" and fake.calls == []


# --- Full run: output contract and the LLM's inability to move verdicts --------------------

@pytest.fixture(scope="module")
def template_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("no_llm")
    run.main(["--no-llm", "--out-dir", str(out)])
    return out


def test_output_matches_the_contract(template_run):
    path = template_run / "output.csv"
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == OUTPUT_COLUMNS
    assert all(len(r) == len(OUTPUT_COLUMNS) for r in rows)   # strict parse: every comma quoted
    assert len(rows) == 1 + 728
    assert path.read_text(encoding="utf-8").isascii()

    out = pd.read_csv(path, keep_default_na=False, dtype=str)
    assert set(out["flagged"]) == {FLAG_YES, FLAG_JUSTIFIED, FLAG_NORMAL}
    assert out["cost_per_tonne_km"].str.fullmatch(r"\d+\.\d{2}").all()
    assert out["vs_similar_routes"].str.fullmatch(r"[+-]\d+\.\d% vs similar-length routes this week").all()
    history = out["vs_own_history"]
    assert (history.str.fullmatch(r"[+-]\d+\.\d% vs this route's past average")
            | (history == "N/A (no prior weeks)")).all()


def test_sample_rows_appear_exactly_in_the_output(template_run):
    out = pd.read_csv(template_run / "output.csv", keep_default_na=False, dtype=str).set_index(["route", "week_of"])
    expected = {
        ("Delhi-Jaipur", "2024-11-11"): ("4.17", "+35.5% vs this route's past average",
                                         "+21.0% vs similar-length routes this week", "Yes", ""),
        ("Ahmedabad-Mumbai", "2025-01-20"): ("3.29", "+29.5% vs this route's past average",
                                             "+22.5% vs similar-length routes this week", "No (justified)", "N002"),
        ("Mumbai-Pune", "2025-09-15"): ("3.98", "+9.2% vs this route's past average",
                                        "+23.6% vs similar-length routes this week", "Yes", ""),
    }
    cols = ["cost_per_tonne_km", "vs_own_history", "vs_similar_routes", "flagged", "matched_note_id"]
    for key, values in expected.items():
        assert tuple(out.loc[key, cols]) == values


def test_llm_cannot_change_any_graded_column(template_run):
    """The committed LLM run (outputs/output.csv) and a no-LLM run differ only in `reason`."""
    llm_output = run.OUTPUT_DIR / "output.csv"
    if not llm_output.exists():
        pytest.skip("no LLM run on disk yet")
    graded = [c for c in OUTPUT_COLUMNS if c != "reason"]
    with_llm = pd.read_csv(llm_output, keep_default_na=False, dtype=str)[graded]
    without = pd.read_csv(template_run / "output.csv", keep_default_na=False, dtype=str)[graded]
    pd.testing.assert_frame_equal(with_llm, without)


def test_run_log_reports_usage_and_inputs(template_run):
    log = json.loads((template_run / "run_log.json").read_text(encoding="utf-8"))
    assert log["rows"] == 728
    assert set(log["inputs_sha256"]) == {"shipments", "notes", "notes_structured"}
    assert log["llm_usage_this_run"]["calls"] == 0
    assert log["reason_sources"]["template"] == 728
