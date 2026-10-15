# Freight Cost Watch

An assistant that tracks freight cost per tonne-km for every route, week by week, flags rises that look out of the ordinary, checks a set of context notes for a genuine reason, and explains each verdict in plain English.

**The core design choice:** rules decide and the LLM writes. Every number, every flag and every "is this note a valid justification?" decision is computed by rules in code. The LLM only words the explanation, and that wording is checked against the facts before it is used. So the LLM cannot invent a justification, and repeat runs give the same verdicts.

## Results at a glance

| | |
|---|---|
| Output | `outputs/output.csv`: 728 rows (7 routes × 104 weeks) in the exact `sample_output_format_v2.csv` columns |
| Verdicts | 706 `No` (normal), **18 `Yes`** (flagged, unexplained), **4 `No (justified)`** |
| Sample rows | All 3 reproduced exactly: numbers, verdict and cited note |
| Grader audit | 4/4 justified rows cite a note that applies to that route, covers that week and describes a cost rise |
| Reproducibility | Graded columns byte-identical across 3 fresh-LLM runs (`outputs/repro/repro_report.md`) |
| Cost of a full run | 24 LLM calls, 16,578 input + 1,922 output tokens, **~$0.0018** (`outputs/run_log.json`) |
| Tests | 102 passing (`pytest`); scorecard `python eval.py`: all hard checks pass |

What it found:

| Route | Weeks | Verdict |
|---|---|---|
| Delhi-Jaipur | 2024-11-11, 11-18 | **Flagged.** +35% spike; the only Delhi-Jaipur note (N007, road resurfacing) gives no cost reason |
| Ahmedabad-Mumbai | 2025-01-20 | Justified by **N002** (festival-week surcharge) |
| Chennai-Bangalore | 2025-02-24, 03-03 | Justified by **N001** (flooding Feb 24 – Mar 8) |
| Chennai-Bangalore | 2025-03-10, 03-17 | **Flagged.** Still ~30% high after the flood window ended; N009 says the route was back to normal |
| Mumbai-Delhi | 2025-05-12 | Justified by **N003** (diesel +5–7% on all routes; observed +11.6%, see the judgment call below) |
| Mumbai-Pune | 2025-09-15 → 12-22 (11 weeks) | **Flagged.** A slow creep: 23–39% above its only peer vs a usual ~10%. Diesel cannot explain it because it raised the peer too |
| Mumbai-Delhi | 2025-08-18, 09-22, 12-22 | **Flagged.** Pulled ~12 points away from its only peer (probably a limitation, see below) |

## Quick start

Python 3.13 on Windows (PowerShell). From the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy example.env .env                # optional: add GROQ_API_KEY for fresh LLM calls

.\.venv\Scripts\python.exe run.py            # full pipeline; replays outputs/llm_cache.json (no key needed)
.\.venv\Scripts\python.exe run.py --no-llm   # template explanations only; no key, no network
.\.venv\Scripts\python.exe run.py --no-cache # fresh LLM calls for every anomaly (needs a key)
.\.venv\Scripts\python.exe -m pytest         # 102 tests, ~35 s
.\.venv\Scripts\python.exe eval.py           # evaluation scorecard -> outputs/eval_report.md
.\.venv\Scripts\python.exe repro_check.py    # 3 fresh-LLM runs, compare outputs (needs a key)
.\.venv\Scripts\python.exe extract_notes.py  # LLM note extraction scored vs hand labels (needs a key)
```

The first run downloads the ~80 MB MiniLM embedding model used by ChromaDB.

### API key: not included, and not needed to reproduce the results

The repository contains no API key. `example.env` is the template: copy it to `.env` and paste a free Groq key from console.groq.com/keys. `.env` is listed in `.gitignore` and is never committed. You can run everything without a key:

| You want to... | Command | Key needed? |
|---|---|---|
| Reproduce the submitted output exactly, explanations included | `run.py` (replays the committed `outputs/llm_cache.json`) | No |
| Confirm verdicts don't depend on the LLM | `run.py --no-llm` (same numbers, flags and cited notes; template explanations) | No |
| Make fresh LLM calls / re-run the reproducibility check | `run.py --no-cache`, `repro_check.py`, `extract_notes.py` | Yes, a free Groq key |

The model is `openai/gpt-oss-20b`: open-weight (Apache 2.0), served on Groq's free tier. The `openai/` prefix names the model's publisher; all calls go to Groq.

## How it works

```
shipment_records.csv
  └─ 1 Load + validate ─ 2 Weekly metrics ─ 3 Anomaly detection ──┐  728 route-weeks, 22 anomalies
                                                                   ▼
context_notes.csv ─ 0 Structured notes ─────────────► 4 Retrieval: Chroma ranks all notes,
                    (annotations/notes_structured.json)    rules decide which are eligible
                                                                   ▼
                                                     5 Verdict rules (no LLM)
                                                                   ▼
                                                     6 Explanation: LLM writes, validator
                                                       checks, template fallback, cache
                                                                   ▼
                                    outputs/output.csv + run_log.json (tokens, cost, input hashes)
```

| Stage | Module | What it does |
|---|---|---|
| 1 | `src/loader.py` | Validates columns, nulls, duplicate ids, positive numbers, dates, route types; derives `route` and `week_of` (the Monday of each Mon–Sun week) |
| 2 | `src/metrics.py` | Cost per tonne-km per route-week = Σ cost / Σ(tonnes × km). **vs own history**: average of the previous 8 calendar weeks, current week excluded. **vs similar routes**: mean of the *other* routes of the same `route_type` that week |
| 3 | `src/detector.py` | Flags a route-week if (a) it is ≥ 10% above its own history (needs ≥ 4 prior weeks), or (b) its gap to its peers is ≥ 12 points wider than its *usual* gap (median of all earlier weeks) while its own cost is rising |
| 0 | `src/notes.py` | Each note as reviewed facts: routes, effective window, cost effect (`increase` / `no_change` / `decrease` / `not_stated`), stated size, and a verbatim evidence quote that must appear in the note |
| 4 | `src/retriever.py` | ChromaDB + MiniLM ranks all 10 notes against the anomaly. A note is **eligible** only if it applies to the route (or all routes), its window overlaps the week, and it describes a cost increase. Rejected notes keep their reasons |
| 5 | `src/verdict.py` | A route-specific eligible note justifies the rise. An all-routes note (diesel) cannot explain a route pulling away from its peers, because it raised them too; it can explain a rise vs history only up to its stated size plus normal noise. Otherwise: flagged |
| 6 | `src/explainer.py` | The LLM gets a small facts object (numbers, verdict, the cited or checked notes) and writes 1–3 sentences. The reply is validated; on failure it gets one retry with the reason it was rejected, then falls back to a fixed template |

### Why these choices

**Weighted weekly cost.** Σ cost / Σ tonne-km reproduces the sample exactly (4.17 / +35.5% / +21.0%). Averaging per-shipment ratios gives 4.16 / +35.2% and does not match. The peer average is a simple mean of route-level values: +22.5% matches, pooling peer tonne-km gives +21.8%.

**Why compare against the usual peer gap, not the raw one.** Routes have structural price levels: Mumbai-Pune normally runs ~10% above its peer, Delhi-Jaipur ~9% below. Flagging on raw `vs_similar_routes` would flag Mumbai-Pune every week. The detector therefore flags a change in the gap, while the output still reports the raw gap exactly as the brief defines it.

**Why two signals.** A slow creep defeats the history comparison: the 8-week average creeps up with the cost. Mumbai-Pune in Sep–Dec 2025 never exceeds +9.2% vs history, but sits 23–39% above its peer. A sudden spike shows up in both signals.

**Thresholds** are about 3× the week-to-week noise measured in the calm period, Jan–Oct 2024, when no note describes a cost event (sd 2.9% for history, 3.8 points for the peer gap). They live in `config.py`.

**Notes are structured once, then frozen.** The facts verdicts depend on are written in prose: N001's window ends Mar 8; N005 says costs were *not* affected; N004's `applies_to` says "All Routes" but its text says the affected routes are *not in this dataset*. Matching on the columns alone would let 5 decoy notes justify rows. Each note was turned into structured fields, hand-reviewed and committed; an evidence quote ties every label to the source text. `extract_notes.py` shows the same extraction done by the LLM, scored against the hand labels (see Verification).

**Where the vector database fits, honestly.** Chroma ranks the notes semantically, and the ranking is recorded in every row's `retrieval_trace`. But on this 10-note corpus it is a weak signal: MiniLM latches onto city names (N005, about Mumbai-Delhi, outranks the correct N002 for Ahmedabad-Mumbai), and no query phrasing tried ranked the right note first for every route. So **rules decide eligibility and the ranking never decides a verdict.** The "closest note" mentioned in explanations is chosen by rule (same route or all routes, within 4 weeks, nearest in time), never a note about another route. Semantic retrieval would matter with thousands of notes or free-text questions; here, removing it would not change any verdict.

**The LLM only words the explanation.** The model is `openai/gpt-oss-20b` on Groq: open-weight (Apache 2.0), temperature 0, seed 42, reasoning effort low. The `openai/` prefix names the model's publisher; all calls go to Groq with a Groq key. Before a reply is used, it must:
- mention only note ids present in the facts, and name the cited note on justified rows;
- contain only numbers that appear in the facts;
- use cause words (flood, diesel, festival...) only if a note in the facts contains them;
- use dates it calls a start or an end only if they are real window starts or ends;
- make per-note claims ("its window doesn't cover the week", "it shows no cost increase", "it states no change") only if they match why that note was rejected;
- state the decided verdict.

Normal weeks (706 rows) never call the LLM.

## Assumptions

- **Output contains every route-week**, with `flagged` = `Yes`, `No (justified)` or `No`. The sample only shows anomalies, so `No` is a third value. `outputs/output_anomalies_only.csv` holds just the 22 flagged and justified rows in case only those are expected.
- **Early weeks use the history available.** Weeks 2–8 of each route average 1–7 prior weeks (49 rows; the count is in `route_week_analysis.csv` as `history_weeks_used`). The first week of each route has no history: `vs_own_history` = `N/A (no prior weeks)` and it is never flagged on history.
- **"This quarter"** in a note means the calendar quarter of the note date. **"Festival week"** means the Monday–Sunday week of the note date. **"About a week"** means 7 days from the note date. **"Starting this week"** with no end means open-ended.
- **A note whose window overlaps any day** of the Mon–Sun week covers that week. So the flood window ending Sat Mar 8 covers the week of Mar 3.
- **The provided `sample_output_format_v2.csv` is not valid CSV.** Row 4's `reason` contains an unquoted comma `(N006, 2025-09-22)`. Our writer quotes such fields, and the output parses strictly.
- **Judgment call:** Mumbai-Delhi 2025-05-12 rose +11.6% vs history; the diesel note states 5–7%. With ~6 points allowed for normal weekly noise (2× calm sd), it is treated as consistent and justified by N003. A stricter reading would flag it.

## Verification: how I convinced myself it was right

`python eval.py` writes `outputs/eval_report.md`. Summary:

1. **Graded numbers.** The 3 sample rows match exactly. A second, plain-Python implementation that shares no code with `src/` agrees on all 728 rows to 1e-9. Tests also check that history never looks ahead (inflating future weeks ×10 leaves a week's baseline unchanged), that a route never counts as its own peer, and Monday bucketing across the year boundary.
2. **Gold labels: 16/16** (`annotations/gold_labels.csv`, each with its basis). 3 come from the sample rows; the rest come from the note text: flood-window edges, decoy notes (N004, N005, N007, N009), the week after the festival, and diesel weeks. 1 is the judgment call above. Caveat: I wrote them after building the detector, so they test verdict logic more than detection.
3. **Grader audit on every justified row** (4/4): the cited note applies to the route, its window covers the week, it describes a rise, and the reason names it. No unjustified row cites a note.
4. **Explanations.** All 22 anomaly reasons re-pass the validator. Reading every explanation during development found 4 hallucinated claims about rejected notes, e.g. "N007 *ends* at 2024-05-20" (it starts then) and "neither N009 nor N001 indicates a cost increase" (N001 does). Each led to a validator rule and is now a regression test. In the run of record, 21 reasons are LLM-written and 1 fell back to the template.
5. **LLM note extraction vs hand labels:** routes 10/10, size 10/10, cost effect 7/10, windows 8/10. Every cost-effect error was conservative (it never claimed a rise that wasn't there). But it left 2 windows open-ended, and N002's would have justified Ahmedabad-Mumbai rises indefinitely. That is why verdicts use the reviewed file, and why a person reviews the extraction.
6. **Threshold sensitivity.** A grid over both thresholds is in the eval report. Both sit in narrow working ranges: see Limitations.

## Reproducibility

`python repro_check.py` runs the full pipeline 3 times with **fresh** LLM calls (`--no-cache`) and compares the outputs:

- The graded columns (`route` … `matched_note_id`) are **byte-identical** in all 3 runs: sha256 `f484b24b…dc64bd1`.
- `reason` wording differed on 11 of 728 rows. Even at temperature 0 with a fixed seed, Groq's output is not fully deterministic. Every variant was read: same verdict, same notes, same numbers.
- Why verdicts cannot move: they are fixed before the LLM is called, and a reply that contradicts them is rejected. `test_llm_cannot_change_any_graded_column` checks that the LLM run and a `--no-llm` run differ only in `reason`.
- `run.py` with no flags replays `outputs/llm_cache.json` and reproduces the run of record byte-for-byte, including `reason`, with zero LLM calls.

## Cost

| | LLM calls | Input tokens | Output tokens | Est. cost |
|---|---|---|---|---|
| Full run (run of record, `--no-cache`) | 24 (22 anomalies + 2 retries) | 16,578 | 1,922 | $0.00182 |
| Repro runs 1 / 2 / 3 | 25 / 25 / 26 | 17,433 / 17,433 / 18,319 | 1,924 / 1,968 / 2,059 | ~$0.0019 each |
| One-off note extraction (`extract_notes.py`) | 10 | 4,776 | 1,472 | $0.00080 |

Prices: $0.075 / $0.30 per million input / output tokens for `openai/gpt-oss-20b` (console.groq.com/docs/models, checked 2026-10-15). The free tier covers all of this. Volume is bounded by design: one call per anomaly, at most one retry, none for normal weeks, and cached replays are free.

## Limitations (known, not hidden)

- **Narrow thresholds.** Peer gap: at 10 points, calm 2024 produces false alarms; at 14, the sample row Mumbai-Pune 2025-09-15 (13.2 points above its usual gap) is missed. History: above 11.6%, the diesel week Mumbai-Delhi 2025-05-12 stops being an anomaly. The chosen values are defensible (≈3× noise) but not robust. With more labelled data I would fit them rather than pick them.
- **Single-peer route types.** Short and Long have two routes each, so "similar routes" is one other route. Delhi-Chennai never shows the diesel step, so Mumbai-Delhi looks ~10 points high against it for the rest of 2025. The 3 late-2025 Mumbai-Delhi flags are probably this artefact, not a real event.
- **The history baseline absorbs spikes.** By Chennai-Bangalore 03-17, the flood weeks inflate the 8-week average, so vs history reads only +13.7%. The peer signal still catches it. The baseline is kept as the brief defines it rather than "cleaned".
- **Gaps in a creep.** Mumbai-Pune 2025-11-10 is not flagged: that week dipped just below its recent average.
- **The explanation validator is pattern-based.** It catches every error found in review, but a fluent misattribution that uses none of its patterns can pass. One seen in a repro run: "N003 *states* it raised similar routes too" (that is our reasoning, not the note's words). Verdicts and citations are unaffected; only the commentary on rejected notes carries this risk.
- **Semantic retrieval is decorative at this scale** (see "Where the vector database fits").

## Project layout

```
config.py                  every threshold, path, model setting and price, with reasoning
run.py                     full pipeline -> outputs/
eval.py                    evaluation scorecard -> outputs/eval_report.md
repro_check.py             3-run reproducibility check -> outputs/repro/
extract_notes.py           LLM note extraction scored vs annotations -> outputs/notes_extraction_report.json
src/                       one module per stage (see "How it works"), plus llm.py, pipeline.py, output.py
annotations/
  notes_structured.json    reviewed note facts (the only note data verdicts use)
  gold_labels.csv          labelled rows for the scorecard
data/                      the three provided files, unmodified
tests/                     102 tests, one file per stage group
outputs/
  output.csv               the deliverable
  output_anomalies_only.csv
  route_week_analysis.csv  every intermediate column, incl. retrieval traces
  run_log.json             token/cost log, reason sources, rejected replies, input hashes
  llm_cache.json           LLM replies by prompt hash (lets anyone replay without a key)
  eval_report.md           the scorecard from eval.py
  repro/                   3 repro runs + repro_report.md (graded hashes, every wording difference)
  notes_llm_extracted.json, notes_extraction_report.json   LLM note extraction and its score
```
