"""Run the full pipeline: shipments -> output.csv, plus a run log with token usage and cost.

  python run.py              use cached LLM replies where available (repeatable, free)
  python run.py --no-cache   force fresh LLM calls for every anomaly (for the repro check)
  python run.py --no-llm     template explanations only; no key or network needed
"""
import argparse
import json
from pathlib import Path

from config import (
    LLM_MODEL,
    NOTES_CSV,
    NOTES_STRUCTURED_JSON,
    OUTPUT_DIR,
    SHIPMENTS_CSV,
)
from src.explainer import Explainer
from src.llm import LLMClient
from src.output import to_output_frame, write_csv
from src.pipeline import attach_explanations, file_sha256, run_stages_1_to_5

LLM_CACHE = OUTPUT_DIR / "llm_cache.json"


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-cache", action="store_true", help="ignore cached LLM replies")
    parser.add_argument("--no-llm", action="store_true", help="use template explanations only")
    parser.add_argument("--out-dir", type=Path, default=OUTPUT_DIR, help="where to write outputs")
    args = parser.parse_args(argv)

    results, notes = run_stages_1_to_5()

    llm = LLMClient()
    explainer = Explainer(llm, LLM_CACHE, use_cache=not args.no_cache, use_llm=not args.no_llm)
    results = attach_explanations(results, notes, explainer)
    if not args.no_llm:
        explainer.save_cache()

    out_dir = args.out_dir
    output = to_output_frame(results)
    write_csv(output, out_dir / "output.csv")
    write_csv(output[output["flagged"] != "No"], out_dir / "output_anomalies_only.csv")
    results.drop(columns=["verdict_obj"]).to_csv(out_dir / "route_week_analysis.csv", index=False,
                                                 date_format="%Y-%m-%d")

    log = {
        "inputs_sha256": {"shipments": file_sha256(SHIPMENTS_CSV), "notes": file_sha256(NOTES_CSV),
                          "notes_structured": file_sha256(NOTES_STRUCTURED_JSON)},
        "mode": {"use_cache": not args.no_cache, "use_llm": not args.no_llm, "model": LLM_MODEL},
        "rows": len(output),
        "verdicts": output["flagged"].value_counts().to_dict(),
        "reason_sources": explainer.sources,
        "llm_usage_this_run": llm.usage.as_dict(),
        "fallbacks": explainer.fallbacks,
    }
    (out_dir / "run_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")

    print(f"{len(output)} rows -> {out_dir / 'output.csv'}")
    print(f"verdicts: {log['verdicts']}")
    print(f"reasons:  {explainer.sources}")
    u = llm.usage
    print(f"LLM: {u.calls} calls, {u.input_tokens} in / {u.output_tokens} out tokens, "
          f"est. ${u.cost_usd():.6f}, {len(explainer.fallbacks)} fallbacks")
    return log


if __name__ == "__main__":
    main()
