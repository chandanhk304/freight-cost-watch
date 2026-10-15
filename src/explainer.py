"""Stage 6: plain-English `reason` for each row.

The verdict and note are already decided (stage 5). The LLM only words the explanation, from a
small facts object, and every reply is checked before use:
  - it may only mention note ids present in the facts, and must name the matched note;
  - every number it writes must appear in the facts;
  - cause words (flood, diesel, festival...) must come from a note in the facts;
  - it must state the verdict that was decided.
Anything that fails, or any LLM outage, falls back to a deterministic template. Replies are
cached by prompt hash so reruns cost nothing and repeat exactly; --no-cache forces fresh calls.
"""
import hashlib
import json
import re
from pathlib import Path

import pandas as pd

from config import FLAG_JUSTIFIED, FLAG_NORMAL, FLAG_YES, LLM_MODEL
from src.llm import LLMClient, LLMUnavailable
from src.notes import Note
from src.verdict import Verdict

PROMPT_VERSION = "v4"
MAX_REASON_CHARS = 450
# Rejected notes near the week that the explanation may discuss (e.g. a flood window that just ended).
MAX_NEARBY_NOTES_IN_FACTS = 2
OPEN_ENDED = "no end date (still in effect)"
# A reply that fails validation gets one more attempt, told why, before the template is used.
MAX_RETRIES = 1

# A date the reply calls an end must be a real window end; one it calls a start, a real start.
_END_WORDS = ("end", "ends", "ended", "until", "through", "to")
_DATE_ROLE = re.compile(
    r"\b(ends?|ended|until|through|to|starts?|started|from|since)\b\D{0,12}?(\d{4}-\d{2}-\d{2})",
    flags=re.IGNORECASE,
)

CAUSE_WORDS = ("fuel", "diesel", "flood", "festival", "toll", "weather", "monsoon", "surcharge",
               "maintenance", "strike", "detour", "demand", "holiday", "rain", "mandate")
FLAG_WORDS = ("flag", "review", "unexplained", "no genuine", "not justified", "no justification")

SYSTEM_PROMPT = """You write the `reason` column of a freight-cost monitoring report.
You are given FACTS as JSON. The verdict is already decided; you only explain it.

Rules:
- 1 to 3 plain-English sentences, at most 400 characters, no line breaks, no markdown.
- If verdict is "No (justified)": say the rise is explained, name matched_note.note_id, and
  reflect what that note says and its dates. Use matched_note.why_it_applies for how it fits;
  if the observed rise differs from a size the note states, say so honestly ("consistent with",
  not "matches").
- If verdict is "Yes": say no note justifies the rise and that it is flagged for human review.
  You may mention the most informative checked note(s) from checked_notes and why each does
  not apply, e.g. a window that ended before this week.
- Use only information in FACTS. Do not invent causes, dates, numbers or note ids.
- Mention only note ids that appear in FACTS.
Reply with the reason text only."""


def build_facts(row: pd.Series, verdict: Verdict, notes_by_id: dict[str, Note]) -> dict:
    def note_facts(note_id: str) -> dict:
        n = notes_by_id[note_id]
        return {"note_id": n.note_id, "note_date": n.note_date.isoformat(),
                "applies_to": n.applies_to, "window_start": n.effective_from.isoformat(),
                "window_end": n.effective_to.isoformat() if n.effective_to else OPEN_ENDED,
                "cost_effect": n.cost_effect, "text": n.text}

    checked = [note_facts(c.note_id) | {"why_it_does_not_apply": c.why}
               for c in verdict.considered if not c.explains]
    nearby = [i for i in (row.get("nearby_rejected_note_ids") or "").split(";") if i]
    for note_id in nearby[:MAX_NEARBY_NOTES_IN_FACTS]:
        if note_id not in {c["note_id"] for c in checked}:
            checked.append(note_facts(note_id) | {"why_it_does_not_apply": _retrieval_reasons(row, note_id)})

    matched = None
    if verdict.matched_note_id:
        why = next(c.why for c in verdict.considered if c.note_id == verdict.matched_note_id)
        matched = note_facts(verdict.matched_note_id) | {"why_it_applies": why}

    return {
        "route": row["route"],
        "week_of": row["week_of"].strftime("%Y-%m-%d"),
        "cost_per_tonne_km": round(row["cost_per_tonne_km"], 2),
        "vs_own_history": format_history(row),
        "vs_similar_routes": format_peers(row),
        "signals": _describe_signals(row),
        "verdict": verdict.flagged,
        "matched_note": matched,
        "checked_notes": checked,
    }


def template_reason(facts: dict) -> str:
    verdict = facts["verdict"]
    if verdict == FLAG_NORMAL:
        return "Within the normal range for this route's history and similar routes; no review needed."
    if verdict == FLAG_JUSTIFIED:
        m = facts["matched_note"]
        return (f"Matches note {m['note_id']} dated {m['note_date']} "
                f"(in effect {m['window_start']} to {m['window_end']}): "
                f"\"{_short(m['text'])}\" The cost rise has a clear explanation.")
    checked = facts["checked_notes"]
    if not checked:
        return ("No matching note found for this route or date range. "
                "Cost rise looks unexplained and worth a human review.")
    c = checked[0]
    return (f"The closest note ({c['note_id']}, {c['note_date']}) does not justify this rise: "
            f"{c['why_it_does_not_apply']}. No genuine justification found; flagged for review.")


# The model likes typographic punctuation (U+2011 non-breaking hyphen in route names, curly
# quotes). It renders like ASCII but breaks matching, grep and Windows consoles.
_ASCII_PUNCTUATION = str.maketrans({
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2212": "-",
    "\u2013": " - ", "\u2014": " - ",  # dashes between clauses: keep the words apart
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u00a0": " ", "\u202f": " ", "\u2009": " ", "\u2026": "...",
})


def normalise_reply(text: str) -> str:
    return " ".join(text.translate(_ASCII_PUNCTUATION).split())


def validate_reason(text: str, facts: dict) -> str | None:
    """Return a problem description, or None if the LLM reason is safe to use."""
    if not text or "\n" in text.strip() or len(text) > MAX_REASON_CHARS:
        return "empty, multi-line or too long"

    allowed_ids = {c["note_id"] for c in facts["checked_notes"]}
    if facts["matched_note"]:
        allowed_ids.add(facts["matched_note"]["note_id"])
    mentioned = set(re.findall(r"\bN\d{3}\b", text))
    if mentioned - allowed_ids:
        return f"mentions note ids not in the facts: {sorted(mentioned - allowed_ids)}"

    facts_text = json.dumps(facts)
    for number in re.findall(r"\d+(?:\.\d+)?", text):
        if number not in facts_text:
            return f"number {number} does not appear in the facts"

    starts = {n["window_start"] for n in _all_notes(facts)} | {facts["week_of"]}
    ends = {n["window_end"] for n in _all_notes(facts)}
    for word, day in _DATE_ROLE.findall(text):
        if word.lower() in _END_WORDS and day not in ends:
            return f"calls {day} an end date, but no note window ends then"
        if word.lower() not in _END_WORDS and day not in starts:
            return f"calls {day} a start date, but no note window starts then"

    claim_problem = _check_claims_about_notes(text, facts)
    if claim_problem:
        return claim_problem

    note_texts = " ".join(
        [c["text"] + " " + c["why_it_does_not_apply"] for c in facts["checked_notes"]]
        + ([facts["matched_note"]["text"]] if facts["matched_note"] else [])
    ).lower()
    for word in CAUSE_WORDS:
        if word in text.lower() and word not in note_texts:
            return f"cause word '{word}' is not supported by any note in the facts"

    lowered = text.lower()
    if facts["verdict"] == FLAG_JUSTIFIED:
        if facts["matched_note"]["note_id"] not in mentioned:
            return "justified reason does not name the matched note"
        if any(w in lowered for w in ("flagged", "unexplained", "no genuine")):
            return "justified reason reads as flagged"
    elif facts["verdict"] == FLAG_YES:
        if not any(w in lowered for w in FLAG_WORDS):
            return "flagged reason does not say it is flagged / unexplained"
    return None


class Explainer:
    def __init__(self, llm: LLMClient, cache_path: Path, use_cache: bool = True, use_llm: bool = True):
        self.llm = llm
        self.cache_path = cache_path
        self.use_cache = use_cache
        self.use_llm = use_llm
        self.cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
        self.sources = {"llm": 0, "cache": 0, "template": 0, "template_fallback": 0}
        self.fallbacks: list[dict] = []
        self._llm_down = False

    def explain(self, facts: dict) -> tuple[str, str]:
        """Returns (reason, source). Normal rows never call the LLM."""
        if facts["verdict"] == FLAG_NORMAL or not self.use_llm:
            self.sources["template"] += 1
            return template_reason(facts), "template"

        user = "FACTS:\n" + json.dumps(facts, indent=1)
        problem = None
        for attempt in range(1 + MAX_RETRIES):
            prompt = user if problem is None else (
                f"{user}\n\nYour previous reply was rejected: {problem}. Write it again, following the rules.")
            reply, source = self._get_reply(prompt, facts)
            if reply is None:
                break
            reply = normalise_reply(reply)
            problem = validate_reason(reply, facts)
            if problem is None:
                self.sources[source] += 1
                return reply, source
            self._record_fallback(facts, f"rejected {source} reply, attempt {attempt + 1} ({problem}): {reply}")

        self.sources["template_fallback"] += 1
        return template_reason(facts), "template_fallback"

    def _get_reply(self, user: str, facts: dict) -> tuple[str | None, str | None]:
        """Cached reply if allowed, else a fresh LLM call (stored in the cache for next time)."""
        key = hashlib.sha256(f"{LLM_MODEL}|{PROMPT_VERSION}|{SYSTEM_PROMPT}|{user}".encode()).hexdigest()
        if self.use_cache and key in self.cache:
            return self.cache[key], "cache"
        if not self.use_llm or self._llm_down:
            return None, None
        try:
            reply = self.llm.complete("explanation", SYSTEM_PROMPT, user).strip()
        except LLMUnavailable as exc:
            self._llm_down = "not set" in str(exc)  # no key: stop trying; transient: try next row
            self._record_fallback(facts, f"LLM unavailable: {exc}")
            return None, None
        self.cache[key] = reply
        return reply, "llm"

    def save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.cache, indent=1, sort_keys=True), encoding="utf-8")

    def _record_fallback(self, facts: dict, why: str) -> None:
        self.fallbacks.append({"route": facts["route"], "week_of": facts["week_of"], "why": why})


# --- formatting shared with the output writer ---------------------------------------------

def format_history(row: pd.Series) -> str:
    if pd.isna(row["vs_own_history_pct"]):
        return "N/A (no prior weeks)"
    return f"{row['vs_own_history_pct']:+.1f}% vs this route's past average"


def format_peers(row: pd.Series) -> str:
    return f"{row['vs_similar_routes_pct']:+.1f}% vs similar-length routes this week"


def _describe_signals(row: pd.Series) -> list[str]:
    signals = []
    if row["history_spike"]:
        signals.append("sharp rise vs this route's own recent average")
    if row["peer_divergence"]:
        signals.append(f"pulled away from similar routes: usually {row['usual_peer_gap_pct']:+.1f}% "
                       f"vs them, now {row['vs_similar_routes_pct']:+.1f}%")
    return signals


_WINDOW_CLAIM = re.compile(r"\b(window|windows|cover|covers|outside|ended|expired|not yet|before this week|"
                           r"after this week|before the week|after the week)\b", re.IGNORECASE)
_NO_COST_CLAIM = re.compile(r"\b(no|not|neither|nor|without)\b[^.;]{0,40}?\b(cost|rate)s? (increase|rise|change)",
                            re.IGNORECASE)
_STATED_CLAIM = re.compile(r"\b(states?|stated|says|reports?|explicitly|confirms?)\b[^.;]{0,30}?"
                           r"\b(no|not|unaffected|unchanged)\b", re.IGNORECASE)
_PLURAL = re.compile(r"\b(both|neither|none|all|their|they|these|those)\b", re.IGNORECASE)


def _check_claims_about_notes(text: str, facts: dict) -> str | None:
    """Check what each clause says about the notes it refers to.

    Clauses are split on sentence ends, semicolons and ', and'. A clause refers to the note ids
    it names; with no id it refers to the previous clause's notes; with a plural word (both,
    neither, their...) it refers to every note named in the sentence. Heuristic by design: it
    targets the mistakes seen in review, and an over-cautious rejection only costs a template.
    """
    notes = {n["note_id"]: n for n in _all_notes(facts)}
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        sentence_ids = re.findall(r"\bN\d{3}\b", sentence)
        previous: list[str] = []
        for clause in re.split(r";|,\s*(?:and|but|while|so)\b|\s+and\s+(?=N\d{3}\b)", sentence):
            ids = re.findall(r"\bN\d{3}\b", clause) or previous
            if _PLURAL.search(clause):
                ids = sentence_ids
            previous = ids
            for note_id in ids:
                note = notes.get(note_id)
                if note is None or note is facts["matched_note"]:
                    continue
                why = note["why_it_does_not_apply"]
                if _WINDOW_CLAIM.search(clause) and "window" not in why:
                    return f"says {note_id}'s window does not cover the week, but that is not why it was rejected"
                if _NO_COST_CLAIM.search(clause) and note["cost_effect"] == "increase":
                    return f"says {note_id} shows no cost increase, but it describes one"
                if _STATED_CLAIM.search(clause) and note["cost_effect"] == "not_stated":
                    return f"says {note_id} states no cost change, but it does not mention costs"
    return None


def _all_notes(facts: dict) -> list[dict]:
    return facts["checked_notes"] + ([facts["matched_note"]] if facts["matched_note"] else [])


def _retrieval_reasons(row: pd.Series, note_id: str) -> str:
    trace = json.loads(row["retrieval_trace"])
    entry = next(r for r in trace["ranked"] if r["note_id"] == note_id)
    return "; ".join(entry["rejected_because"])


def _short(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else text[: limit - 3].rsplit(" ", 1)[0] + "..."
