# Evaluation report

**Overall: ALL HARD CHECKS PASS**


## 1. Graded numbers

- **PASS** sample row Delhi-Jaipur 2024-11-11: ('4.17', '+35.5', '+21.0')
- **PASS** sample row Ahmedabad-Mumbai 2025-01-20: ('3.29', '+29.5', '+22.5')
- **PASS** sample row Mumbai-Pune 2025-09-15: ('3.98', '+9.2', '+23.6')
- **PASS** independent recomputation of every route-week: 728/728 rows agree to 1e-9
- Partial history: 49 rows (weeks 2-8 of each route) average 1-7 prior weeks; 7 first-week rows have none (`N/A (no prior weeks)`).

## 2. Gold labels

| route | week | expected | got | source | result |
|---|---|---|---|---|---|
| Delhi-Jaipur | 2024-11-11 | Yes | Yes | sample | ok |
| Ahmedabad-Mumbai | 2025-01-20 | No (justified) N002 | No (justified) N002 | sample | ok |
| Mumbai-Pune | 2025-09-15 | Yes | Yes | sample | ok |
| Chennai-Bangalore | 2025-02-24 | No (justified) N001 | No (justified) N001 | notes | ok |
| Chennai-Bangalore | 2025-03-03 | No (justified) N001 | No (justified) N001 | notes | ok |
| Chennai-Bangalore | 2025-03-10 | Yes | Yes | notes | ok |
| Chennai-Bangalore | 2025-03-17 | Yes | Yes | notes | ok |
| Delhi-Jaipur | 2024-11-18 | Yes | Yes | notes | ok |
| Mumbai-Pune | 2025-12-01 | Yes | Yes | notes | ok |
| Ahmedabad-Mumbai | 2025-01-27 | No | No | notes | ok |
| Mumbai-Delhi | 2024-07-29 | No | No | notes | ok |
| Delhi-Jaipur | 2024-05-20 | No | No | notes | ok |
| Delhi-Chennai | 2024-03-11 | No | No | notes | ok |
| Kolkata-Bhubaneswar | 2025-05-12 | No or No (justified) N003 | No | notes | ok |
| Chennai-Bangalore | 2025-05-05 | No or No (justified) N003 | No | notes | ok |
| Mumbai-Delhi | 2025-05-12 | No (justified) N003 | No (justified) N003 | judgment | ok |

- **PASS** gold-label agreement: 16/16
- Labels: 3 come from the case-study authors' sample rows; the rest were written from the note text and the raw weekly costs; 1 is marked as a judgment call. They were written after the detector existed, so they test the verdict logic, not the detector's thresholds (see section 7).

## 3. Grader audit of every justified row

- **PASS** cited note applies to the route, covers the week, describes a rise, and is named in the reason: 4/4 justified rows
- **PASS** no note cited on unjustified rows
- Ahmedabad-Mumbai 2025-01-20 -> N002 (2025-01-20, Ahmedabad-Mumbai, in effect 2025-01-20 to 2025-01-26): "A regional festival week saw a temporary surcharge applied by transporters on the Ahmedabad-Mumbai corridor due to high demand and limited truck availability."
- Chennai-Bangalore 2025-02-24 -> N001 (2025-02-24, Chennai-Bangalore, in effect 2025-02-24 to 2025-03-08): "Heavy flooding on the Chennai-Bangalore highway disrupted normal truck movement from Feb 24 to Mar 8, forcing longer detours and higher trip costs. Roads were passable again by Mar 9."
- Chennai-Bangalore 2025-03-03 -> N001 (2025-02-24, Chennai-Bangalore, in effect 2025-02-24 to 2025-03-08): "Heavy flooding on the Chennai-Bangalore highway disrupted normal truck movement from Feb 24 to Mar 8, forcing longer detours and higher trip costs. Roads were passable again by Mar 9."
- Mumbai-Delhi 2025-05-12 -> N003 (2025-05-05, All Routes, in effect 2025-05-05 to open-ended): "Diesel prices rose nationwide starting this week, pushing up transportation costs across all routes by roughly 5-7%."

## 4. Explanations

- **PASS** every anomaly reason re-validated against its facts: 22/22 pass
- **PASS** output is plain ASCII
- Reason sources in the run of record: 21 LLM-written, 1 template fallback(s) after failed validation, 706 normal-week templates.
  - rejected: Delhi-Jaipur 2024-11-11: rejected llm reply, attempt 1 (says N007 states no cost change, but it does not mention costs): No note justifies the rise; the only checked note (N007) refers 
  - rejected: Mumbai-Pune 2025-10-06: rejected llm reply, attempt 1 (says N003's window does not cover the week, but that is not why it was rejected): No note justifies the rise; N003, N006, and N01
  - rejected: Mumbai-Pune 2025-10-06: rejected llm reply, attempt 2 (says N003's window does not cover the week, but that is not why it was rejected): No note justifies the rise; N003 applies to all
- Validator checks: note ids limited to the facts; matched note named; every number present in the facts; cause words backed by a note; dates called starts/ends must be real window starts/ends; per-note claims (window / no cost increase / 'states no change') must match why that note was rejected. Known limit: it is pattern-based, so a fluent misattribution that uses none of these patterns can still pass (one seen in review: 'N003 states it raised similar routes too').

## 5. LLM note extraction vs hand-reviewed annotations

- Valid records: 9/10 (invalid ones failed the verbatim-evidence check)
- Field accuracy: routes 10/10, effective_from 8/10, effective_to 8/10, cost_effect 7/10, magnitude_pct 10/10
- Errors that would have widened what a note can justify: {'N002': ['window ends later (open-ended vs 2025-01-26)'], 'N006': ['window ends later (open-ended vs 2025-09-30)']}
- Verdicts use the reviewed annotations, never the raw extraction.

## 6. Reproducibility

- **PASS** graded columns identical across 3 fresh-LLM runs: sha256 f484b24bd2e53374... (details: outputs/repro/repro_report.md)

## 7. Threshold sensitivity

| history % | peer-gap pts | anomalies | in calm Jan-Oct 2024 | expected-anomaly gold rows caught |
|---|---|---|---|---|
| 8 | 10 | 32 | 3 | 10/10 |
| 8 | 12 | 23 | 1 | 10/10 |
| 8 | 14 | 17 | 1 | 10/10 |
| 10 | 10 | 31 | 2 | 10/10 |
| 10 (chosen) | 12 | 22 | 0 | 10/10 |
| 10 | 14 | 15 | 0 | 9/10 |
| 12 | 10 | 30 | 2 | 9/10 |
| 12 | 12 | 21 | 0 | 9/10 |
| 12 | 14 | 14 | 0 | 8/10 |
| 15 | 10 | 30 | 2 | 9/10 |
| 15 | 12 | 21 | 0 | 9/10 |
| 15 | 14 | 14 | 0 | 8/10 |

- Both thresholds sit in narrow working ranges. History: above 11.6% the diesel week Mumbai-Delhi 2025-05-12 (+11.6%, the judgment-call gold row) stops being an anomaly; below ~10% calm 2024 starts producing false alarms. Peer gap: at 10 points calm 2024 produces false alarms, at 14 the sample row Mumbai-Pune 2025-09-15 (13.2 points above its usual gap) is missed.

## 8. Cost of the run of record

- Model `openai/gpt-oss-20b` (temperature 0.0, seed 42, reasoning effort low)
- 24 calls, 16578 input tokens, 1922 output tokens, estimated $0.00182 at $0.075/$0.3 per million tokens
- Mode: {'use_cache': False, 'use_llm': True, 'model': 'openai/gpt-oss-20b'}
