"""
Deterministic Guardrail Validator.
Zero LLM calls. Pure Python verification and sanitization of raw candidate directives.
Defends the optimizer against malformed, out-of-bounds, or hallucinated LLM outputs.
Isolates failures per note: bad notes are safely coerced to no_op while valid notes are preserved.
"""

import math
from typing import Any, Dict, List, Optional, Set
from app.logging_utils import logger
from app.schemas import BatteryConfig, DirectiveType, ValidatedDirective

VALID_DIRECTIVE_TYPES: Set[str] = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}


def _coerce_to_noop(
    note_index: int,
    reason: str,
    original_text: str = "",
    prior_explanation: str = "",
) -> ValidatedDirective:
    """Helper to safely coerce a candidate directive into an inactive no_op."""
    logger.warning(f"Guardrail coerced note {note_index} to no_op: {reason}")
    explanation = f"Sanitized to no_op: {reason}"
    if prior_explanation:
        explanation = f"{explanation} (Original: {prior_explanation})"
    return ValidatedDirective(
        note_index=note_index,
        directive_type="no_op",
        applies=False,
        hours=[],
        factor=None,
        minimum_energy_kwh=None,
        max_grid_kwh=None,
        explanation=explanation,
        original_text=original_text,
    )


def _sanitize_hours(raw_hours: Any) -> Optional[List[int]]:
    """
    Extracts, filters to [0, 23], deduplicates, and sorts ascending.
    Returns None if raw_hours is invalid or results in an empty list.
    """
    if not isinstance(raw_hours, list) or len(raw_hours) == 0:
        return None

    cleaned: Set[int] = set()
    for item in raw_hours:
        try:
            # Reject booleans (isinstance(True, int) is True in Python)
            if isinstance(item, bool):
                continue
            val = int(item)
            if 0 <= val <= 23:
                cleaned.add(val)
        except (ValueError, TypeError):
            continue

    if not cleaned:
        return None

    return sorted(list(cleaned))


def _is_finite_number(val: Any) -> bool:
    """Checks if a value is a finite float or int (not bool, NaN, or Inf)."""
    if isinstance(val, bool) or val is None:
        return False
    try:
        f = float(val)
        return not (math.isnan(f) or math.isinf(f))
    except (ValueError, TypeError):
        return False


def validate_directives(
    raw_candidates: List[Dict[str, Any]],
    operator_notes: List[str],
    battery: BatteryConfig,
) -> List[ValidatedDirective]:
    """
    Validates and sanitizes raw candidate directives from the LLM interpreter.
    Guarantees that every returned ValidatedDirective conforms strictly to domain
    constraints and mathematical bounds.

    Args:
        raw_candidates: Untrusted list of candidate directive dicts from LLM output.
        operator_notes: Original natural-language strings (length 1-3).
        battery: Battery configuration containing capacity and rate limits.

    Returns:
        List of ValidatedDirective objects, exactly 1:1 with operator_notes order (0..N-1).
    """
    num_notes = len(operator_notes)
    if num_notes == 0:
        return []

    # 1. Deduplicate / index mapping: ensure exactly one candidate per note_index 0..N-1
    candidate_map: Dict[int, Dict[str, Any]] = {}
    if isinstance(raw_candidates, list):
        for candidate in raw_candidates:
            if not isinstance(candidate, dict):
                continue
            idx = candidate.get("note_index")
            if isinstance(idx, int) and 0 <= idx < num_notes:
                if idx not in candidate_map:
                    candidate_map[idx] = candidate
                else:
                    logger.warning(f"Guardrail dropped duplicate entry for note_index {idx}")

    validated_results: List[ValidatedDirective] = []

    # Process each note in sequential order 0..num_notes-1
    for i in range(num_notes):
        orig_text = operator_notes[i] if i < len(operator_notes) else ""
        candidate = candidate_map.get(i)

        if candidate is None:
            validated_results.append(
                _coerce_to_noop(
                    note_index=i,
                    reason="Missing candidate entry from LLM output",
                    original_text=orig_text,
                )
            )
            continue

        raw_type = candidate.get("directive_type")
        raw_applies = candidate.get("applies")
        raw_adj = candidate.get("structured_adjustment")
        raw_explanation = str(candidate.get("explanation", "") or "").strip()

        # 2. directive_type check
        if not isinstance(raw_type, str) or raw_type not in VALID_DIRECTIVE_TYPES:
            validated_results.append(
                _coerce_to_noop(
                    note_index=i,
                    reason=f"Unsupported directive_type '{raw_type}'",
                    original_text=orig_text,
                    prior_explanation=raw_explanation,
                )
            )
            continue

        directive_type: DirectiveType = raw_type  # type: ignore

        # 3. applies & adjustment shape semantics
        if directive_type == "no_op":
            # Coerce applies to False and structured_adjustment to None
            validated_results.append(
                ValidatedDirective(
                    note_index=i,
                    directive_type="no_op",
                    applies=False,
                    hours=[],
                    factor=None,
                    minimum_energy_kwh=None,
                    max_grid_kwh=None,
                    explanation=raw_explanation or "Operational notice does not modify schedule.",
                    original_text=orig_text,
                )
            )
            continue

        # For all 5 real directive types:
        if raw_adj is None or not isinstance(raw_adj, dict):
            validated_results.append(
                _coerce_to_noop(
                    note_index=i,
                    reason=f"Directive {directive_type} missing structured_adjustment object",
                    original_text=orig_text,
                    prior_explanation=raw_explanation,
                )
            )
            continue

        # 4. Hours validation: every real directive requires a non-empty list of hours in [0, 23]
        clean_hours = _sanitize_hours(raw_adj.get("hours"))
        if clean_hours is None:
            validated_results.append(
                _coerce_to_noop(
                    note_index=i,
                    reason=f"Directive {directive_type} has empty or invalid 'hours' array",
                    original_text=orig_text,
                    prior_explanation=raw_explanation,
                )
            )
            continue

        # 5-7. Specific parameter validation per directive type
        if directive_type == "solar_reduction":
            raw_factor = raw_adj.get("factor")
            if not _is_finite_number(raw_factor):
                validated_results.append(
                    _coerce_to_noop(
                        note_index=i,
                        reason="solar_reduction factor must be a finite number",
                        original_text=orig_text,
                        prior_explanation=raw_explanation,
                    )
                )
                continue

            factor = float(raw_factor)
            # Design choice: clamp small floating noise [-0.01, 1.01] to [0.0, 1.0];
            # reject anything beyond that as an untrusted hallucination.
            if -0.01 <= factor <= 1.01:
                factor = max(0.0, min(1.0, factor))
            else:
                validated_results.append(
                    _coerce_to_noop(
                        note_index=i,
                        reason=f"solar_reduction factor {factor} out of range [0, 1]",
                        original_text=orig_text,
                        prior_explanation=raw_explanation,
                    )
                )
                continue

            validated_results.append(
                ValidatedDirective(
                    note_index=i,
                    directive_type="solar_reduction",
                    applies=True,
                    hours=clean_hours,
                    factor=factor,
                    explanation=raw_explanation or f"Solar reduced to factor {factor} in hours {clean_hours}",
                    original_text=orig_text,
                )
            )

        elif directive_type == "minimum_battery_reserve":
            raw_reserve = raw_adj.get("minimum_energy_kwh")
            if not _is_finite_number(raw_reserve):
                validated_results.append(
                    _coerce_to_noop(
                        note_index=i,
                        reason="minimum_battery_reserve requires finite minimum_energy_kwh",
                        original_text=orig_text,
                        prior_explanation=raw_explanation,
                    )
                )
                continue

            reserve = float(raw_reserve)
            if reserve < 0.0 or reserve > battery.capacity_kwh:
                validated_results.append(
                    _coerce_to_noop(
                        note_index=i,
                        reason=f"minimum_energy_kwh {reserve} exceeds battery capacity {battery.capacity_kwh} or is negative",
                        original_text=orig_text,
                        prior_explanation=raw_explanation,
                    )
                )
                continue

            validated_results.append(
                ValidatedDirective(
                    note_index=i,
                    directive_type="minimum_battery_reserve",
                    applies=True,
                    hours=clean_hours,
                    minimum_energy_kwh=reserve,
                    explanation=raw_explanation or f"Battery reserve set to {reserve} kWh in hours {clean_hours}",
                    original_text=orig_text,
                )
            )

        elif directive_type == "no_charge_window":
            validated_results.append(
                ValidatedDirective(
                    note_index=i,
                    directive_type="no_charge_window",
                    applies=True,
                    hours=clean_hours,
                    explanation=raw_explanation or f"Battery charging forbidden in hours {clean_hours}",
                    original_text=orig_text,
                )
            )

        elif directive_type == "no_discharge_window":
            validated_results.append(
                ValidatedDirective(
                    note_index=i,
                    directive_type="no_discharge_window",
                    applies=True,
                    hours=clean_hours,
                    explanation=raw_explanation or f"Battery discharging forbidden in hours {clean_hours}",
                    original_text=orig_text,
                )
            )

        elif directive_type == "max_grid_window":
            raw_grid_cap = raw_adj.get("max_grid_kwh")
            if not _is_finite_number(raw_grid_cap):
                validated_results.append(
                    _coerce_to_noop(
                        note_index=i,
                        reason="max_grid_window requires finite max_grid_kwh",
                        original_text=orig_text,
                        prior_explanation=raw_explanation,
                    )
                )
                continue

            grid_cap = float(raw_grid_cap)
            if grid_cap < 0.0:
                validated_results.append(
                    _coerce_to_noop(
                        note_index=i,
                        reason=f"max_grid_kwh {grid_cap} cannot be negative",
                        original_text=orig_text,
                        prior_explanation=raw_explanation,
                    )
                )
                continue

            validated_results.append(
                ValidatedDirective(
                    note_index=i,
                    directive_type="max_grid_window",
                    applies=True,
                    hours=clean_hours,
                    max_grid_kwh=grid_cap,
                    explanation=raw_explanation or f"Grid import capped at {grid_cap} kWh in hours {clean_hours}",
                    original_text=orig_text,
                )
            )

    return validated_results
