# Reproducibility report

Mode: fresh LLM calls (--no-cache), 3 runs, 728 rows each.

## Graded columns (route, week_of, cost_per_tonne_km, vs_own_history, vs_similar_routes, flagged, matched_note_id)

- run 1: sha256 `f484b24bd2e53374447002351e15d13fed5de8c4eb2006b07d44b321dcc64bd1`
- run 2: sha256 `f484b24bd2e53374447002351e15d13fed5de8c4eb2006b07d44b321dcc64bd1`
- run 3: sha256 `f484b24bd2e53374447002351e15d13fed5de8c4eb2006b07d44b321dcc64bd1`

**IDENTICAL** across all runs.

## LLM usage per run

| run | calls | input tokens | output tokens | est. cost (USD) | fallbacks to template |
|---|---|---|---|---|---|
| 1 | 25 | 17433 | 1924 | 0.001885 | 2 |
| 2 | 25 | 17433 | 1968 | 0.001898 | 2 |
| 3 | 26 | 18319 | 2059 | 0.001992 | 1 |

## `reason` wording: 11 of 728 rows differ between runs

Verdict, cited note and numbers are fixed before the LLM is called, so only wording can vary.

### Chennai-Bangalore, week of 2025-03-03
- run 1: The rise is explained by note N001, which records heavy flooding on the Chennai-Bangalore highway from 2025-02-24 to 2025-03-08, causing longer detours and higher trip costs; the observed increase is consistent with that event.
- run 2: The rise is explained by note N001, which records heavy flooding on the Chennai-Bangalore highway from 2025-02-24 to 2025-03-08 that forced longer detours and higher trip costs; the observed increase is consistent with that event.
- run 3: The rise is explained by note N001, which records heavy flooding on the Chennai-Bangalore highway from 2025-02-24 to 2025-03-08, causing longer detours and higher trip costs; the observed increase is consistent with that event.

### Delhi-Jaipur, week of 2024-11-18
- run 1: No note justifies the rise; the rise is flagged for human review. The only checked note, N007, refers to improved road conditions and does not mention freight costs, so it does not explain the cost increase.
- run 2: No note justifies the rise; the rise is flagged for human review. The only checked note, N007, refers to improved road conditions and does not mention freight costs, so it does not explain the cost increase.
- run 3: No note justifies the rise; N007 does not mention freight costs, only road condition improvements, so it does not explain the 27.6% increase. The case is flagged for human review.

### Mumbai-Delhi, week of 2025-05-12
- run 1: The rise is explained by note N003, which notes a nationwide diesel price increase starting 2025-05-05 that raises transport costs by roughly 5-7% on all routes; this is consistent with the observed +11.6% rise for Mumbai-Delhi in week 2025-05-12.
- run 2: The rise is explained by note N003, which states diesel prices rose nationwide from 2025-05-05, increasing transport costs by roughly 5-7% on all routes; this is consistent with the observed +11.6% increase for Mumbai-Delhi.
- run 3: The rise is explained by note N003, which notes a nationwide diesel price increase starting 2025-05-05 that raises transport costs by roughly 5-7% on all routes; this is consistent with the observed +11.6% rise for Mumbai-Delhi in week 2025-05-12.

### Mumbai-Delhi, week of 2025-08-18
- run 1: No note justifies the rise; N003 applies to all routes but would have increased similar routes too, and N006 states no cost change, so neither explains this route pulling away from similar routes; the anomaly is flagged for human review.
- run 2: No note justifies the rise; N003 does not explain the pull away from similar routes, and N006 states no cost change, so the increase is flagged for human review.
- run 3: No note justifies the rise; N003 is in effect but it would have increased similar routes too, and N006 states no cost change, so neither explains the +10.7% pull away; the anomaly is flagged for human review.

### Mumbai-Delhi, week of 2025-09-22
- run 1: No note justifies the rise; N003 does not explain the pull away from similar routes, and N006 states no cost change, so the increase is flagged for human review.
- run 2: No note justifies the rise; N003 applies to all routes and would have increased similar routes too, so it cannot explain this route pulling away, and N006 states no cost change, so it does not account for the 11.9% increase; the anomaly is flagged for human review.
- run 3: No note justifies the rise; N003 and N006 both do not explain the Mumbai-Delhi cost pulling away from similar routes, so the increase is flagged for human review.

### Mumbai-Delhi, week of 2025-12-22
- run 1: No note justifies the rise; N003 and N010 both do not explain the Mumbai-Delhi route pulling away from similar routes, so the increase is flagged for human review.
- run 2: No note justifies the rise; N003 and N010 do not apply because N003 would have increased all routes, including similar ones, so it cannot explain this route pulling away, and N010 states no cost change, so it cannot explain the 11.3% increase. The anomaly is flagged for human review.
- run 3: No note justifies the rise; N003 and N010 both do not explain the 11.3% pull away from similar routes, so the increase is flagged for human review.

### Mumbai-Pune, week of 2025-10-20
- run 1: No note justifies the rise; the increase is flagged for human review. Note N003, while covering all routes, states it raised similar routes too, so it cannot explain this route pulling away. Note N010's window starts after the week in question and indicates no cost change. Note N006's window ends before the week and also reports no cost impact.
- run 2: No note justifies the rise; the increase is flagged for human review. Note N003, while indicating a nationwide diesel price rise, applies to all routes and would have raised similar routes too, so it cannot explain this route pulling away. Note N010's window starts 2025-10-27, after the week in question, and states no cost change. Note N006's window ends 2025-09-30, before the week, and also reports no cost change.
- run 3: No note justifies the rise; N003 applies to all routes but would have increased similar routes too, so it cannot explain this route pulling away; N010's window starts after the week and says no cost change; N006's window ends before the week and also says no cost change. The spike is flagged for human review.

### Mumbai-Pune, week of 2025-10-27
- run 1: The closest note (N003, 2025-05-05) does not justify this rise: it applies to all routes, so it raised similar routes' costs too and cannot explain this route pulling away from them. No genuine justification found; flagged for review.
- run 2: The closest note (N003, 2025-05-05) does not justify this rise: it applies to all routes, so it raised similar routes' costs too and cannot explain this route pulling away from them. No genuine justification found; flagged for review.
- run 3: No note justifies the rise; it is flagged for human review. N003 does not apply because it explains a 5-7% increase across all routes, yet similar routes also rose, so it cannot explain this route pulling away. N010 and N006 both state no cost change, and N006's window ends before this week, so neither explains the 28.3% increase.

### Mumbai-Pune, week of 2025-11-24
- run 1: No note justifies the rise; N003 and N010 do not apply because N003 would have increased all routes equally, so similar routes would also have risen, and N010 states no cost change, so neither explains the 23% pull away. The anomaly is flagged for human review.
- run 2: No note justifies the rise; N003 would raise all routes but also similar routes, so it cannot explain this route pulling away, and N010 states no cost change, so it does not account for the 23% increase; the anomaly is flagged for human review.
- run 3: No note justifies the rise; N003 applies to all routes but would have increased similar routes too, so it cannot explain this route pulling away, and N010 states no cost change, so it does not account for the 23% increase; the anomaly is flagged for human review.

### Mumbai-Pune, week of 2025-12-01
- run 1: No note justifies the rise; N003 would raise all routes but cannot explain this route pulling away, and N010 states no cost change, so the 37.4% increase is flagged for human review.
- run 2: No note justifies the rise; N003 would raise all routes but cannot explain this route pulling away, and N010 states no cost change, so the 37.4% increase is flagged for human review.
- run 3: No note justifies the rise; N003 and N010 do not apply because N003 would have increased all routes equally, so similar routes would also have risen, and N010 states no cost change, so neither explains the 37.4% pull away. The anomaly is flagged for human review.

### Mumbai-Pune, week of 2025-12-22
- run 1: No note justifies the rise; N003 and N010 do not apply because N003 would have increased all routes, including similar ones, so it cannot explain the pull away, and N010 states no cost change, so it cannot explain the 38.9% increase. The anomaly is flagged for human review.
- run 2: No note justifies the rise; N003 would raise all routes but also similar routes, so it cannot explain this route pulling away, and N010 states no cost change, so it does not account for the 38.9% increase. The anomaly is flagged for human review.
- run 3: No note justifies the rise; N003 applies to all routes and would have increased similar routes too, so it cannot explain this route pulling away, and N010 states no cost change, so it does not account for the 38.9% increase. The anomaly is flagged for human review.
