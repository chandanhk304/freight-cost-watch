"""Evaluation scorecard: how we know the output is right before submitting.

  python eval.py     reads outputs/ from the last run and writes outputs/eval_report.md

Sections (each PASS/FAIL where a hard pass condition exists):
  1. Graded numbers   - sample rows exact; every row recomputed by an independent implementation
  2. Gold labels      - verdict + cited note on a labelled set (annotations/gold_labels.csv)
  3. Grader audit     - every justified row: cited note applies to that route, window and a rise
  4. Explanations     - every written reason re-checked against its facts; sources and fallbacks
  5. Note extraction  - LLM-structured notes vs the hand-reviewed annotations
  6. Reproducibility  - graded-column hashes from repro_check.py
  7. Threshold sensitivity - does the result depend on the exact thresholds?
  8. Cost             - LLM calls, tokens and cost for the run of record
Exits non-zero if any hard check fails.
"""
import csv
import json
import statistics
from datetime import date, timedelta

import pandas as pd

import src.detector as detector
from config import (
    FLAG_JUSTIFIED,
    FLAG_YES,
    OUTPUT_DIR,
    PEER_GAP_EXCESS_PP,
    HISTORY_SPIKE_PCT,
    ROOT,
    SHIPMENTS_CSV,
)
from src.explainer import build_facts, validate_reason
from src.loader import load_shipments
from src.metrics import compute_weekly_metrics
from src.pipeline import run_stages_1_to_5

GOLD_CSV = ROOT / "annotations" / "gold_labels.csv"
REPORT = OUTPUT_DIR / "eval_report.md"

# (route, week_of, cost_per_tonne_km, vs_own_history %, vs_similar_routes %) from the sample file.
SAMPLE_NUMBERS = [
    ("Delhi-Jaipur", "2024-11-11", "4.17", "+35.5", "+21.0"),
    ("Ahmedabad-Mumbai", "2025-01-20", "3.29", "+29.5", "+22.5"),
    ("Mumbai-Pune", "2025-09-15", "3.98", "+9.2", "+23.6"),
]


def independent_metrics(path) -> dict:
    """Plain-Python recomputation of the graded numbers, sharing no code with src/."""
    totals, route_type = {}, {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            d = date.fromisoformat(r["shipment_date"])
            week = d - timedelta(days=d.weekday())
            route = f"{r['origin']}-{r['destination']}"
            route_type[route] = r["route_type"]
            cost, tkm = totals.get((route, week), (0.0, 0.0))
            totals[(route, week)] = (cost + float(r["freight_cost_inr"]),
                                     tkm + float(r["quantity_tonnes"]) * float(r["distance_km"]))
    cpt = {k: c / t for k, (c, t) in totals.items()}

    results = {}
    for (route, week), value in cpt.items():
        prior = [cpt[(route, week - timedelta(weeks=i))] for i in range(1, 9)
                 if (route, week - timedelta(weeks=i)) in cpt]
        peers = [v for (r, w), v in cpt.items()
                 if w == week and r != route and route_type[r] == route_type[route]]
        hist = (value / statistics.mean(prior) - 1) * 100 if prior else None
        peer = (value / statistics.mean(peers) - 1) * 100 if peers else None
        results[(route, week)] = (value, hist, peer)
    return results


class Scorecard:
    def __init__(self):
        self.lines: list[str] = ["# Evaluation report", ""]
        self.failures: list[str] = []

    def section(self, title: str) -> None:
        self.lines += ["", f"## {title}", ""]

    def check(self, name: str, passed: bool, detail: str = "") -> None:
        self.lines.append(f"- **{'PASS' if passed else 'FAIL'}** {name}" + (f": {detail}" if detail else ""))
        if not passed:
            self.failures.append(name)

    def add(self, *lines: str) -> None:
        self.lines += list(lines)


def main() -> None:
    output = pd.read_csv(OUTPUT_DIR / "output.csv", keep_default_na=False, dtype=str)
    run_log = json.loads((OUTPUT_DIR / "run_log.json").read_text(encoding="utf-8"))
    results, notes = run_stages_1_to_5()
    notes_by_id = {n.note_id: n for n in notes}
    out_idx = output.set_index(["route", "week_of"])
    card = Scorecard()

    # 1. Graded numbers -------------------------------------------------------------------
    card.section("1. Graded numbers")
    for route, week, cost, hist, peer in SAMPLE_NUMBERS:
        row = out_idx.loc[(route, week)]
        got = (row["cost_per_tonne_km"], row["vs_own_history"].split("%")[0], row["vs_similar_routes"].split("%")[0])
        card.check(f"sample row {route} {week}", got == (cost, hist, peer), f"{got}")

    expected = independent_metrics(SHIPMENTS_CSV)
    metrics = compute_weekly_metrics(load_shipments(SHIPMENTS_CSV))
    mismatches = 0
    for _, r in metrics.iterrows():
        value, hist, peer = expected[(r["route"], r["week_of"].date())]
        same_hist = (hist is None and pd.isna(r["vs_own_history_pct"])) or (
            hist is not None and abs(r["vs_own_history_pct"] - hist) < 1e-9)
        if abs(r["cost_per_tonne_km"] - value) > 1e-9 or abs(r["vs_similar_routes_pct"] - peer) > 1e-9 or not same_hist:
            mismatches += 1
    card.check("independent recomputation of every route-week", mismatches == 0,
               f"{len(metrics) - mismatches}/{len(metrics)} rows agree to 1e-9")
    early = metrics[metrics["history_weeks_used"].between(1, 7)]
    card.add(f"- Partial history: {len(early)} rows (weeks 2-8 of each route) average 1-7 prior weeks; "
             f"{int(metrics['vs_own_history_pct'].isna().sum())} first-week rows have none (`N/A (no prior weeks)`).")

    # 2. Gold labels ----------------------------------------------------------------------
    card.section("2. Gold labels")
    gold = pd.read_csv(GOLD_CSV, keep_default_na=False, dtype=str)
    card.add("| route | week | expected | got | source | result |", "|---|---|---|---|---|---|")
    correct = 0
    for _, g in gold.iterrows():
        row = out_idx.loc[(g["route"], g["week_of"])]
        options = list(zip(g["expected_flagged"].split("|"), g["expected_note_id"].split("|")
                           if "|" in g["expected_note_id"] else [g["expected_note_id"]] * 2))
        ok = (row["flagged"], row["matched_note_id"]) in options
        correct += ok
        got = f"{row['flagged']} {row['matched_note_id']}".strip()
        want = " or ".join(f"{f} {n}".strip() for f, n in dict.fromkeys(options))
        card.add(f"| {g['route']} | {g['week_of']} | {want} | {got} | {g['source']} | {'ok' if ok else 'WRONG'} |")
    card.add("")
    card.check("gold-label agreement", correct == len(gold), f"{correct}/{len(gold)}")
    card.add("- Labels: 3 come from the case-study authors' sample rows; the rest were written from the note "
             "text and the raw weekly costs; 1 is marked as a judgment call. They were written after the "
             "detector existed, so they test the verdict logic, not the detector's thresholds (see section 7).")

    # 3. Grader audit ---------------------------------------------------------------------
    card.section("3. Grader audit of every justified row")
    justified = output[output["flagged"] == FLAG_JUSTIFIED]
    violations = []
    for _, row in justified.iterrows():
        note = notes_by_id.get(row["matched_note_id"])
        week = date.fromisoformat(row["week_of"])
        if not (note and note.applies_to_route(row["route"]) and note.overlaps_week(week) and note.cost_effect == "increase"):
            violations.append(f"{row['route']} {row['week_of']} -> {row['matched_note_id']}")
        elif row["matched_note_id"] not in row["reason"]:
            violations.append(f"{row['route']} {row['week_of']}: reason does not name {row['matched_note_id']}")
    card.check("cited note applies to the route, covers the week, describes a rise, and is named in the reason",
               not violations, f"{len(justified) - len(violations)}/{len(justified)} justified rows" +
               (f"; violations: {violations}" if violations else ""))
    others = output[output["flagged"] != FLAG_JUSTIFIED]
    card.check("no note cited on unjustified rows", (others["matched_note_id"] == "").all())
    for _, row in justified.iterrows():
        n = notes_by_id[row["matched_note_id"]]
        card.add(f"- {row['route']} {row['week_of']} -> {n.note_id} ({n.note_date}, {n.applies_to}, "
                 f"in effect {n.window_text()}): \"{n.text}\"")

    # 4. Explanations ---------------------------------------------------------------------
    card.section("4. Explanations")
    problems = []
    for _, r in results[results["is_anomaly"]].iterrows():
        written = out_idx.loc[(r["route"], r["week_of"].strftime("%Y-%m-%d")), "reason"]
        problem = validate_reason(written, build_facts(r, r["verdict_obj"], notes_by_id))
        if problem:
            problems.append(f"{r['route']} {r['week_of']:%Y-%m-%d}: {problem}")
    card.check("every anomaly reason re-validated against its facts", not problems,
               f"{int(results['is_anomaly'].sum()) - len(problems)}/{int(results['is_anomaly'].sum())} pass")
    card.check("output is plain ASCII", (OUTPUT_DIR / "output.csv").read_text(encoding="utf-8").isascii())
    src = run_log["reason_sources"]
    card.add(f"- Reason sources in the run of record: {src['llm'] + src['cache']} LLM-written, "
             f"{src['template_fallback']} template fallback(s) after failed validation, {src['template']} normal-week templates.")
    for f in run_log["fallbacks"]:
        card.add(f"  - rejected: {f['route']} {f['week_of']}: {f['why'][:160]}")
    card.add("- Validator checks: note ids limited to the facts; matched note named; every number present in the "
             "facts; cause words backed by a note; dates called starts/ends must be real window starts/ends; "
             "per-note claims (window / no cost increase / 'states no change') must match why that note was "
             "rejected. Known limit: it is pattern-based, so a fluent misattribution that uses none of these "
             "patterns can still pass (one seen in review: 'N003 states it raised similar routes too').")

    # 5. Note extraction ------------------------------------------------------------------
    card.section("5. LLM note extraction vs hand-reviewed annotations")
    extraction_path = OUTPUT_DIR / "notes_extraction_report.json"
    if extraction_path.exists():
        ex = json.loads(extraction_path.read_text(encoding="utf-8"))
        card.add(f"- Valid records: {ex['valid_records']}/{ex['notes']} (invalid ones failed the verbatim-evidence check)")
        card.add("- Field accuracy: " + ", ".join(f"{k} {v}" for k, v in ex["field_accuracy"].items()))
        card.add(f"- Errors that would have widened what a note can justify: {ex['permissive_errors'] or 'none'}")
        card.add("- Verdicts use the reviewed annotations, never the raw extraction.")
    else:
        card.add("- Not run (needs `python extract_notes.py` with a Groq key).")

    # 6. Reproducibility ------------------------------------------------------------------
    card.section("6. Reproducibility")
    hashes_path = OUTPUT_DIR / "repro" / "graded_hashes.json"
    if hashes_path.exists():
        hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
        card.check(f"graded columns identical across {len(hashes)} fresh-LLM runs", len(set(hashes)) == 1,
                   f"sha256 {hashes[0][:16]}... (details: outputs/repro/repro_report.md)")
    else:
        card.add("- Not run (needs `python repro_check.py`).")

    # 7. Threshold sensitivity ------------------------------------------------------------
    card.section("7. Threshold sensitivity")
    key_rows = {(g["route"], g["week_of"]) for _, g in gold.iterrows() if FLAG_YES in g["expected_flagged"].split("|")
                or FLAG_JUSTIFIED == g["expected_flagged"]}
    card.add("| history % | peer-gap pts | anomalies | in calm Jan-Oct 2024 | expected-anomaly gold rows caught |",
             "|---|---|---|---|---|")
    saved = detector.HISTORY_SPIKE_PCT, detector.PEER_GAP_EXCESS_PP
    try:
        for h in (8.0, 10.0, 12.0, 15.0):
            for p in (10.0, 12.0, 14.0):
                detector.HISTORY_SPIKE_PCT, detector.PEER_GAP_EXCESS_PP = h, p
                a = detector.detect_anomalies(metrics)
                hits = a[a["is_anomaly"]]
                caught = {(r, w.strftime("%Y-%m-%d")) for r, w in zip(hits["route"], hits["week_of"])} & key_rows
                calm = int((hits["week_of"] < pd.Timestamp("2024-11-01")).sum())
                mark = " (chosen)" if (h, p) == (HISTORY_SPIKE_PCT, PEER_GAP_EXCESS_PP) else ""
                card.add(f"| {h:g}{mark} | {p:g} | {len(hits)} | {calm} | {len(caught)}/{len(key_rows)} |")
    finally:
        detector.HISTORY_SPIKE_PCT, detector.PEER_GAP_EXCESS_PP = saved
    card.add("", "- Both thresholds sit in narrow working ranges. History: above 11.6% the diesel week "
             "Mumbai-Delhi 2025-05-12 (+11.6%, the judgment-call gold row) stops being an anomaly; below ~10% "
             "calm 2024 starts producing false alarms. Peer gap: at 10 points calm 2024 produces false alarms, "
             "at 14 the sample row Mumbai-Pune 2025-09-15 (13.2 points above its usual gap) is missed.")

    # 8. Cost ------------------------------------------------------------------------------
    card.section("8. Cost of the run of record")
    u = run_log["llm_usage_this_run"]
    card.add(f"- Model `{u['model']}` (temperature {u['temperature']}, seed {u['seed']}, reasoning effort "
             f"{u['reasoning_effort']})",
             f"- {u['calls']} calls, {u['input_tokens']} input tokens, {u['output_tokens']} output tokens, "
             f"estimated ${u['estimated_cost_usd']} at ${u['price_usd_per_million']['input']}/"
             f"${u['price_usd_per_million']['output']} per million tokens",
             f"- Mode: {run_log['mode']}")

    card.lines[1:1] = ["", f"**Overall: {'ALL HARD CHECKS PASS' if not card.failures else 'FAILURES: ' + ', '.join(card.failures)}**"]
    REPORT.write_text("\n".join(card.lines) + "\n", encoding="utf-8")
    print(f"report -> {REPORT}")
    print("ALL HARD CHECKS PASS" if not card.failures else f"FAILED: {card.failures}")
    if card.failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
