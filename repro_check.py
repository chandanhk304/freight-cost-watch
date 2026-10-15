"""Reproducibility check: run the pipeline 3 times and compare the outputs.

  python repro_check.py              3 runs with fresh LLM calls (--no-cache): the honest test
  python repro_check.py --cached     3 runs replaying the LLM cache

Graded columns (everything except `reason`) must be byte-identical across runs. `reason` may
vary in wording when fresh LLM calls are made; the report lists every row where it did.
Writes outputs/repro/run_{1,2,3}/ and outputs/repro/repro_report.md.
"""
import argparse
import hashlib
import json

import pandas as pd

import run
from config import OUTPUT_COLUMNS, OUTPUT_DIR

REPRO_DIR = OUTPUT_DIR / "repro"
GRADED = [c for c in OUTPUT_COLUMNS if c != "reason"]
RUNS = 3


def digest(frame: pd.DataFrame) -> str:
    return hashlib.sha256(frame.to_csv(index=False).encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cached", action="store_true", help="replay the LLM cache instead of fresh calls")
    args = parser.parse_args()

    outputs, logs = [], []
    for i in range(1, RUNS + 1):
        out_dir = REPRO_DIR / f"run_{i}"
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"--- run {i} ---")
        logs.append(run.main(["--out-dir", str(out_dir)] + ([] if args.cached else ["--no-cache"])))
        outputs.append(pd.read_csv(out_dir / "output.csv", keep_default_na=False, dtype=str))

    graded_hashes = [digest(o[GRADED]) for o in outputs]
    graded_identical = len(set(graded_hashes)) == 1

    reason_diffs = []
    for idx in range(len(outputs[0])):
        reasons = [o.at[idx, "reason"] for o in outputs]
        if len(set(reasons)) > 1:
            reason_diffs.append((outputs[0].at[idx, "route"], outputs[0].at[idx, "week_of"], reasons))

    lines = [
        "# Reproducibility report",
        "",
        f"Mode: {'cached LLM replies' if args.cached else 'fresh LLM calls (--no-cache)'}, {RUNS} runs, "
        f"{len(outputs[0])} rows each.",
        "",
        "## Graded columns (" + ", ".join(GRADED) + ")",
        "",
        *[f"- run {i + 1}: sha256 `{h}`" for i, h in enumerate(graded_hashes)],
        "",
        f"**{'IDENTICAL' if graded_identical else 'DIFFERENT'}** across all runs.",
        "",
        "## LLM usage per run",
        "",
        "| run | calls | input tokens | output tokens | est. cost (USD) | fallbacks to template |",
        "|---|---|---|---|---|---|",
        *[f"| {i + 1} | {l['llm_usage_this_run']['calls']} | {l['llm_usage_this_run']['input_tokens']} | "
          f"{l['llm_usage_this_run']['output_tokens']} | {l['llm_usage_this_run']['estimated_cost_usd']} | "
          f"{l['reason_sources']['template_fallback']} |" for i, l in enumerate(logs)],
        "",
        f"## `reason` wording: {len(reason_diffs)} of {len(outputs[0])} rows differ between runs",
        "",
        "Verdict, cited note and numbers are fixed before the LLM is called, so only wording can vary.",
        "",
    ]
    for route, week, reasons in reason_diffs:
        lines.append(f"### {route}, week of {week}")
        lines += [f"- run {i + 1}: {r}" for i, r in enumerate(reasons)]
        lines.append("")

    report = REPRO_DIR / "repro_report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    (REPRO_DIR / "graded_hashes.json").write_text(json.dumps(graded_hashes, indent=2), encoding="utf-8")

    print(f"\ngraded columns identical across {RUNS} runs: {graded_identical}")
    print(f"rows whose reason wording differs: {len(reason_diffs)}")
    print(f"report -> {report}")
    if not graded_identical:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
