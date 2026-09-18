"""
LLM Operator-Note Interpreter.
Translates natural-language operator directives into structured candidate directive dictionaries.
Uses a hosted generative LLM with strict JSON-mode enforcement, prompt-engineered domain rules,
and safe fallback to no_op upon any provider or format failure.
"""

import json
import time
from typing import Any, Dict, List, Optional
from openai import OpenAI

from app.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    MAX_LLM_RETRIES,
    REQUEST_TIMEOUT_SECONDS,
)
from app.logging_utils import logger

# -----------------------------------------------------------------------------
# System Prompt: Canonical Directive Interpretation Knowledge Base
# -----------------------------------------------------------------------------
INTERPRETER_SYSTEM_PROMPT = """You are the expert energy operator directive interpreter for the GridWise Energy Management System.
Your job is to read natural language operator notes for a 24-hour campus energy scheduling period (hours 0 to 23) and output a machine-checkable structured interpretation for EVERY note.

SUPPORTED DIRECTIVE TYPES (EXACTLY THESE SIX - NO OTHERS EVER):
1. solar_reduction
   - Meaning: Reduces usable solar in specified hours due to weather, dust, shading, maintenance, or curtailment.
   - structured_adjustment: {"hours": [int, ...], "factor": float}
   - CRITICAL FACTOR RULE: "factor" is the fraction of solar that REMAINS, NOT the reduction amount!
     * "80% reduction in solar" -> factor = 0.20
     * "solar will drop to 20%" -> factor = 0.20
     * "cut solar by 30%" -> factor = 0.70
     * "roughly one-fifth of normal solar output" -> factor = 0.20
     * "solar generation reduced by half" -> factor = 0.50

2. minimum_battery_reserve
   - Meaning: Battery stored energy must stay at or above a minimum reserve in specified hours (for emergencies, backup, or campus events).
   - structured_adjustment: {"hours": [int, ...], "minimum_energy_kwh": float}

3. no_charge_window
   - Meaning: Battery must NOT charge during specified hours.
   - structured_adjustment: {"hours": [int, ...]}

4. no_discharge_window
   - Meaning: Battery must NOT discharge during specified hours.
   - structured_adjustment: {"hours": [int, ...]}

5. max_grid_window
   - Meaning: Grid import is capped at a maximum kWh limit in specified hours (for peak shaving, transformer limits, or contract demand limits).
   - structured_adjustment: {"hours": [int, ...], "max_grid_kwh": float}

6. no_op
   - Meaning: Note does not mandate an operational constraint on today's 24-hour dispatch schedule. This includes irrelevant notes, informational notices, future-day statements, or general comments.
   - structured_adjustment: null
   - applies: false

HOUR-WINDOW CONVENTION:
- Start hour inclusive, end hour exclusive: [start, end)
- "1 PM to 3 PM" (13:00 to 15:00) -> hours [13, 14] (NOT 15!)
- "9 AM to 12 PM" (09:00 to 12:00) -> hours [9, 10, 11]
- "from 18:00 to 21:00" -> hours [18, 19, 20]
- "between 2 PM and 4 PM" -> hours [14, 15]
- Every "hours" array must contain unique ascending integers between 0 and 23.

STRICT APPLIES SEMANTICS:
- For "no_op": applies MUST be false, structured_adjustment MUST be null.
- For all other 5 directive types: applies MUST be true, structured_adjustment MUST be an object matching the required shape.

OUTPUT FORMAT REQUIREMENTS:
Output ONLY a single JSON object with the following key:
{
  "directives": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
      "explanation": "Brief human readable explanation"
    }
  ]
}

FEW-SHOT EXAMPLES:
Example Input:
[
  {"note_index": 0, "text": "Cloud cover expected between 1 PM and 3 PM, reduce solar output by 75%."},
  {"note_index": 1, "text": "Keep at least 40 kWh in the battery bank from 6 PM to 9 PM for the evening auditorium event."},
  {"note_index": 2, "text": "The cafeteria will be serving tea at 4 PM."}
]
Example Output:
{
  "directives": [
    {"note_index": 0, "applies": true, "directive_type": "solar_reduction", "structured_adjustment": {"hours": [13, 14], "factor": 0.25}, "explanation": "Solar output reduced by 75% (factor 0.25) during hours 13 and 14."},
    {"note_index": 1, "applies": true, "directive_type": "minimum_battery_reserve", "structured_adjustment": {"hours": [18, 19, 20], "minimum_energy_kwh": 40.0}, "explanation": "Maintain minimum 40 kWh battery reserve during hours 18, 19, and 20."},
    {"note_index": 2, "applies": false, "directive_type": "no_op", "structured_adjustment": null, "explanation": "Cafeteria social notice does not affect energy dispatch."}
  ]
}

Example Input 2:
[
  {"note_index": 0, "text": "Do not charge the battery between 2 PM and 5 PM due to high transformer temperature."},
  {"note_index": 1, "text": "Hold battery discharge between 7 AM and 9 AM."},
  {"note_index": 2, "text": "Grid import must stay below 35 kWh from 18:00 to 22:00."}
]
Example Output 2:
{
  "directives": [
    {"note_index": 0, "applies": true, "directive_type": "no_charge_window", "structured_adjustment": {"hours": [14, 15, 16]}, "explanation": "Charging prohibited between 14:00 and 17:00."},
    {"note_index": 1, "applies": true, "directive_type": "no_discharge_window", "structured_adjustment": {"hours": [7, 8]}, "explanation": "Discharging prohibited between 07:00 and 09:00."},
    {"note_index": 2, "applies": true, "directive_type": "max_grid_window", "structured_adjustment": {"hours": [18, 19, 20, 21], "max_grid_kwh": 35.0}, "explanation": "Grid import capped at 35 kWh from 18:00 to 22:00."}
  ]
}
"""


def get_llm_client() -> OpenAI:
    """Instantiates OpenAI client configured with environment settings."""
    kwargs: Dict[str, Any] = {"api_key": LLM_API_KEY or "dummy-key"}
    if LLM_BASE_URL:
        kwargs["base_url"] = LLM_BASE_URL
    return OpenAI(**kwargs)


def _safe_no_op_fallback(operator_notes: List[str], reason: str) -> List[Dict[str, Any]]:
    """
    Constructs a deterministic, safe no_op candidate directive for every input note.
    Ensures the service never crashes or halts optimization if the LLM call fails.
    """
    return [
        {
            "note_index": i,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": f"Interpretation fallback ({reason}): safely defaulted to no_op",
        }
        for i in range(len(operator_notes))
    ]


def interpret_notes(
    operator_notes: List[str],
    client: Optional[OpenAI] = None,
    model: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Interprets 1 to 3 operator notes using a generative LLM in JSON mode.
    Sends all notes in a single prompt to optimize latency and guarantee 1:1 index alignment.

    Args:
        operator_notes: List of 1-3 natural-language operator instructions.
        client: Optional OpenAI client instance (allows mocking for tests).
        model: Optional model override.

    Returns:
        List of raw candidate directive dicts matching note_index order 0..N-1.
    """
    if not operator_notes:
        return []

    active_model = model or LLM_MODEL
    active_client = client or get_llm_client()

    user_payload = [
        {"note_index": i, "text": note}
        for i, note in enumerate(operator_notes)
    ]
    user_message = json.dumps(user_payload, indent=2)

    logger.info(f"LLM interpreter invoking model '{active_model}' for {len(operator_notes)} notes")

    max_attempts = 1 + max(0, MAX_LLM_RETRIES)
    timeout_budget = min(REQUEST_TIMEOUT_SECONDS, 20.0)

    for attempt in range(1, max_attempts + 1):
        t0 = time.perf_counter()
        try:
            # We use response_format={"type": "json_object"} to strictly enforce valid JSON output
            # from modern OpenAI/compatible models, minimizing parsing failures while avoiding
            # provider-specific strict-schema schema compilation bugs.
            response = active_client.chat.completions.create(
                model=active_model,
                messages=[
                    {"role": "system", "content": INTERPRETER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                timeout=timeout_budget,
            )
            elapsed = time.perf_counter() - t0
            raw_content = response.choices[0].message.content or "{}"
            logger.info(f"LLM call succeeded in {elapsed:.2f}s (attempt {attempt})")

            parsed = json.loads(raw_content)
            candidates: List[Dict[str, Any]] = parsed.get("directives", [])

            if isinstance(candidates, list) and len(candidates) > 0:
                return candidates

            logger.warning("LLM response did not contain a non-empty 'directives' list")

        except Exception as exc:
            elapsed = time.perf_counter() - t0
            logger.warning(
                f"LLM interpretation attempt {attempt}/{max_attempts} failed in {elapsed:.2f}s: {type(exc).__name__}"
            )
            if attempt < max_attempts:
                time.sleep(0.5)

    # Safe failure path: Default all notes to no_op
    logger.error("All LLM interpretation attempts failed; falling back to safe no_op directives")
    return _safe_no_op_fallback(operator_notes, reason="LLM provider error or parse failure")
