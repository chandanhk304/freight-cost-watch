"""Central configuration. Every tunable value lives here so behaviour is auditable in one place."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "outputs"

SHIPMENTS_CSV = DATA_DIR / "shipment_records.csv"
NOTES_CSV = DATA_DIR / "context_notes.csv"
# Hand-reviewed structured facts per note: the only note data verdicts are allowed to use.
NOTES_STRUCTURED_JSON = ROOT / "annotations" / "notes_structured.json"

# Output contract: column names and order from data/sample_output_format_v2.csv.
OUTPUT_COLUMNS = [
    "route", "week_of", "cost_per_tonne_km", "vs_own_history", "vs_similar_routes",
    "flagged", "matched_note_id", "reason",
]

# Brief: trailing 8-week rolling average of the route's own cost, strictly before the current week.
HISTORY_WINDOW_WEEKS = 8

VALID_ROUTE_TYPES = ("Short", "Medium", "Long")

# --- Stage 3: anomaly detection -----------------------------------------------------------
# Thresholds are ~3x the week-to-week noise measured over the calm period (Jan-Oct 2024, no
# notes describe cost events): vs_own_history sd = 2.9%, peer-gap excess sd = 3.8 points.

# Signal 1: this week vs the route's own trailing average.
HISTORY_SPIKE_PCT = 10.0
# Ignore signal 1 until enough prior weeks exist for the average to mean something.
MIN_HISTORY_WEEKS_FOR_SIGNAL = 4

# Signal 2: how far the route has pulled away from its peers, relative to its *usual* gap.
# Raw vs_similar_routes is biased per route (Mumbai-Pune normally sits ~10% above its peer,
# Delhi-Jaipur ~9% below), so the signal is the change in the gap, not the gap itself.
PEER_GAP_EXCESS_PP = 12.0
# The usual gap is the median over all prior weeks: robust to one-off spikes and slow
# enough not to absorb a gradual creep. Needs a minimum number of weeks to be meaningful.
MIN_WEEKS_FOR_USUAL_PEER_GAP = 8

# --- Notes and retrieval -----------------------------------------------------------------
ALL_ROUTES = "ALL"
COST_EFFECTS = ("increase", "no_change", "decrease", "not_stated")
# A rejected note is only worth mentioning in an explanation if its window is this close to the week.
NEARBY_NOTE_MAX_DAYS = 28

# --- Stage 5: verdicts ---------------------------------------------------------------------
# An all-routes note with a stated size (diesel: 5-7%) only explains a rise up to that size
# plus normal weekly noise (~2x the calm-period sd of 2.9%).
ALL_ROUTES_MAGNITUDE_TOLERANCE_PP = 6.0

FLAG_YES = "Yes"
FLAG_JUSTIFIED = "No (justified)"
FLAG_NORMAL = "No"

# --- LLM (Groq)--------------------------------------------------------------------------
# Open-weight (Apache 2.0) model served on Groq's free tier.
LLM_MODEL = "openai/gpt-oss-20b"
LLM_TEMPERATURE = 0.0
LLM_SEED = 42
# gpt-oss is a reasoning model; hidden reasoning is billed as output tokens, so keep it short.
LLM_REASONING_EFFORT = "low"
# USD per million tokens, from console.groq.com/docs/models (checked 2026-10-15).
LLM_PRICE_INPUT_PER_M = 0.075
LLM_PRICE_OUTPUT_PER_M = 0.30
