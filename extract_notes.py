"""One-off: run LLM note extraction and score it against annotations/notes_structured.json.

Needs GROQ_API_KEY in .env. Writes outputs/notes_llm_extracted.json and
outputs/notes_extraction_report.json. The pipeline itself never reads these files.
"""
import json

from config import NOTES_CSV, NOTES_STRUCTURED_JSON, OUTPUT_DIR, SHIPMENTS_CSV
from src.llm import LLMClient, LLMUnavailable
from src.loader import load_shipments
from src.note_extraction import compare_to_reviewed, extract_note
from src.notes import load_raw_notes


def main() -> None:
    known_routes = set(load_shipments(SHIPMENTS_CSV)["route"])
    raw_notes = load_raw_notes(NOTES_CSV).to_dict("records")
    reviewed = json.loads(NOTES_STRUCTURED_JSON.read_text(encoding="utf-8"))

    llm = LLMClient()
    try:
        extracted = [extract_note(llm, raw, known_routes) for raw in raw_notes]
    except LLMUnavailable as exc:
        raise SystemExit(f"Cannot run extraction: {exc}")

    report = compare_to_reviewed(extracted, reviewed)
    report["usage"] = llm.usage.as_dict()

    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / "notes_llm_extracted.json").write_text(json.dumps(extracted, indent=2), encoding="utf-8")
    (OUTPUT_DIR / "notes_extraction_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"valid records: {report['valid_records']}/{report['notes']}")
    for f, score in report["field_accuracy"].items():
        print(f"  {f:15} {score}")
    print(f"permissive errors (could cause false justifications): {report['permissive_errors'] or 'none'}")
    print(f"LLM calls: {llm.usage.calls}, tokens in/out: {llm.usage.input_tokens}/{llm.usage.output_tokens}, "
          f"est. cost ${llm.usage.cost_usd():.6f}")


if __name__ == "__main__":
    main()
