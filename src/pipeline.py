"""Wires the stages together."""
import hashlib
import json
from pathlib import Path

import pandas as pd

from config import NOTES_CSV, NOTES_STRUCTURED_JSON, SHIPMENTS_CSV
from src.detector import detect_anomalies
from src.explainer import Explainer, build_facts
from src.loader import load_shipments
from src.metrics import compute_weekly_metrics
from src.notes import Note, load_notes
from src.retriever import NoteRetriever, RetrievalResult
from src.verdict import attach_verdicts


def analyse_route_weeks(shipments_csv: Path = SHIPMENTS_CSV) -> pd.DataFrame:
    """Stages 1-3: one row per route-week with graded metrics and anomaly signals."""
    shipments = load_shipments(shipments_csv)
    metrics = compute_weekly_metrics(shipments)
    return detect_anomalies(metrics)


def attach_retrieval(analysis: pd.DataFrame, retriever: NoteRetriever) -> pd.DataFrame:
    """Stage 4: retrieve notes for anomalies only; normal weeks need no explanation."""
    out = analysis.copy()
    out["eligible_note_ids"] = ""
    out["nearest_rejected_note_id"] = ""
    out["nearby_rejected_note_ids"] = ""
    out["retrieval_trace"] = ""

    for idx in out.index[out["is_anomaly"]]:
        result = retriever.retrieve(out.at[idx, "route"], out.at[idx, "week_of"].date())
        out.at[idx, "eligible_note_ids"] = ";".join(r.note.note_id for r in result.eligible)
        nearby = [r.note.note_id for r in result.nearby_relevant_rejected()]
        out.at[idx, "nearest_rejected_note_id"] = nearby[0] if nearby else ""
        out.at[idx, "nearby_rejected_note_ids"] = ";".join(nearby)
        out.at[idx, "retrieval_trace"] = _trace_json(result)
    return out


def _trace_json(result: RetrievalResult) -> str:
    """Full ranked list with reasons: what was considered, and why each note did or didn't count."""
    return json.dumps({
        "query": result.query,
        "ranked": [
            {"note_id": r.note.note_id, "distance": r.distance, "eligible": r.eligible,
             "rejected_because": list(r.rejection_reasons)}
            for r in result.ranked
        ],
    })


def load_project_notes(known_routes: set[str]) -> list[Note]:
    return load_notes(NOTES_CSV, NOTES_STRUCTURED_JSON, known_routes)


def run_stages_1_to_4(shipments_csv: Path = SHIPMENTS_CSV) -> pd.DataFrame:
    analysis = analyse_route_weeks(shipments_csv)
    notes = load_project_notes(set(analysis["route"]))
    return attach_retrieval(analysis, NoteRetriever(notes))


def run_stages_1_to_5(shipments_csv: Path = SHIPMENTS_CSV) -> tuple[pd.DataFrame, list[Note]]:
    analysis = run_stages_1_to_4(shipments_csv)
    notes = load_project_notes(set(analysis["route"]))
    return attach_verdicts(analysis, notes), notes


def attach_explanations(results: pd.DataFrame, notes: list[Note], explainer: Explainer) -> pd.DataFrame:
    """Stage 6: one reason per row (LLM for anomalies, template for normal weeks)."""
    notes_by_id = {n.note_id: n for n in notes}
    out = results.copy()
    reasons, sources = [], []
    for _, row in out.iterrows():
        reason, source = explainer.explain(build_facts(row, row["verdict_obj"], notes_by_id))
        reasons.append(reason)
        sources.append(source)
    out["reason"] = reasons
    out["reason_source"] = sources
    return out


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
