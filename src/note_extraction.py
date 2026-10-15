"""Stage 0 (AI part): have the LLM turn each free-text note into structured facts, then score
the result against the hand-reviewed annotations. The reviewed file stays the source of truth;
this shows how note structuring would scale beyond what a person can label.
"""
import json

from config import ALL_ROUTES, COST_EFFECTS
from src.llm import LLMClient
from src.notes import NoteValidationError, validate_structured_note

SYSTEM_PROMPT = f"""You convert logistics notes into structured JSON facts. Use only what the note says.

Return a JSON object with exactly these keys:
- "routes": list of route names from the provided list that the note applies to. Use ["{ALL_ROUTES}"]
  if it applies to every route. Use [] if the text says the affected routes are not in the dataset.
- "effective_from": ISO date (YYYY-MM-DD) when the described condition starts.
- "effective_to": ISO date when it ends, or null if no end is stated. Interpret "this week" as the
  Monday-Sunday week of the note date, "this quarter" as the calendar quarter of the note date,
  and "about a week" as the 7 days starting on the note date.
- "cost_effect": one of {list(COST_EFFECTS)}. "increase" only if the note says freight costs or
  rates went up. Use "no_change" if it says costs were unaffected, stable, normal, or absorbed.
  Use "not_stated" if it does not talk about costs.
- "magnitude_pct": [low, high] percentage if a size of the cost change is stated, else null.
- "evidence": an exact, verbatim substring of the note that supports cost_effect. Copy it character
  for character; do not paraphrase."""

FIELDS = ["routes", "effective_from", "effective_to", "cost_effect", "magnitude_pct"]


def build_user_prompt(note_id: str, note_date: str, applies_to: str, text: str, known_routes: list[str]) -> str:
    return (f"Routes in the dataset: {', '.join(sorted(known_routes))}\n"
            f"Note id: {note_id}\nNote date: {note_date}\napplies_to column: {applies_to}\n"
            f"Note text: {text}")


def extract_note(llm: LLMClient, raw: dict, known_routes: set[str]) -> dict:
    """One LLM call per note. Returns the parsed record plus any validation error."""
    reply = llm.complete(
        purpose="note_extraction",
        system=SYSTEM_PROMPT,
        user=build_user_prompt(raw["note_id"], raw["date"], raw["applies_to"], raw["note"], sorted(known_routes)),
        json_mode=True,
    )
    record = {"note_id": raw["note_id"]}
    try:
        record.update({k: v for k, v in json.loads(reply).items() if k != "note_id"})
        validate_structured_note(record, raw["note"], known_routes)
        record["validation_error"] = None
    except (json.JSONDecodeError, NoteValidationError, TypeError, AttributeError) as exc:
        record["validation_error"] = str(exc)
    return record


def compare_to_reviewed(extracted: list[dict], reviewed: list[dict]) -> dict:
    """Field-by-field agreement between LLM extraction and the reviewed annotations."""
    reviewed_by_id = {r["note_id"]: r for r in reviewed}
    rows, correct = [], {f: 0 for f in FIELDS}
    for e in sorted(extracted, key=lambda r: r["note_id"]):
        gold = reviewed_by_id[e["note_id"]]
        mismatches = {}
        for f in FIELDS:
            got, want = _normalise(f, e.get(f)), _normalise(f, gold[f])
            if got == want:
                correct[f] += 1
            else:
                mismatches[f] = {"llm": e.get(f), "reviewed": gold[f]}
        rows.append({"note_id": e["note_id"], "valid": e.get("validation_error") is None,
                     "validation_error": e.get("validation_error"), "mismatches": mismatches,
                     "permissive_errors": _permissive_errors(e, gold)})
    n = len(extracted)
    return {
        "notes": n,
        "valid_records": sum(r["valid"] for r in rows),
        "field_accuracy": {f: f"{correct[f]}/{n}" for f in FIELDS},
        # Errors that would let a note justify rows the reviewed version would not: these are
        # the ones that cause fabricated verdicts. Other mismatches only make the system stricter.
        "permissive_errors": {r["note_id"]: r["permissive_errors"] for r in rows if r["permissive_errors"]},
        "per_note": rows,
    }


def _permissive_errors(llm: dict, gold: dict) -> list[str]:
    errors = []
    if llm.get("cost_effect") == "increase" and gold["cost_effect"] != "increase":
        errors.append("claims a cost increase the note does not state")
    llm_routes, gold_routes = set(llm.get("routes") or []), set(gold["routes"])
    if llm_routes - gold_routes and "ALL" not in gold_routes:
        errors.append(f"adds routes {sorted(llm_routes - gold_routes)}")
    try:
        if llm.get("effective_from") and llm["effective_from"] < gold["effective_from"]:
            errors.append(f"window starts earlier ({llm['effective_from']} vs {gold['effective_from']})")
        if gold["effective_to"] and (llm.get("effective_to") is None or llm["effective_to"] > gold["effective_to"]):
            errors.append(f"window ends later ({llm.get('effective_to') or 'open-ended'} vs {gold['effective_to']})")
    except TypeError:
        errors.append("unparseable window")
    return errors


def _normalise(field_name: str, value):
    if field_name == "routes":
        return sorted(value or [])
    if field_name == "magnitude_pct":
        return [float(v) for v in value] if value else None
    return value
