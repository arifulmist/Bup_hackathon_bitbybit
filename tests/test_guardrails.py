"""
Unit and fuzz tests for deterministic guardrail validator.
Zero LLM calls. Tests every boundary, malformed input, deduplication, and safe coercion.
"""

import pytest
from app.guardrails import validate_directives
from app.schemas import BatteryConfig


@pytest.fixture
def battery() -> BatteryConfig:
    return BatteryConfig(
        capacity_kwh=100.0,
        initial_energy_kwh=50.0,
        minimum_energy_kwh=10.0,
        max_charge_kwh_per_hour=25.0,
        max_discharge_kwh_per_hour=25.0,
    )


def test_valid_passthrough_all_5_real_directives(battery):
    """Verifies that well-formed directives for all 5 operational types pass with applies=True."""
    raw_candidates = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [12, 13], "factor": 0.3},
            "explanation": "Solar cut",
        },
        {
            "note_index": 1,
            "applies": True,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": [18, 19], "minimum_energy_kwh": 35.0},
            "explanation": "Keep reserve",
        },
        {
            "note_index": 2,
            "applies": True,
            "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": [14, 15]},
            "explanation": "No charge",
        },
    ]
    notes = ["Note 0", "Note 1", "Note 2"]
    validated = validate_directives(raw_candidates, notes, battery)

    assert len(validated) == 3
    assert validated[0].directive_type == "solar_reduction"
    assert validated[0].factor == 0.3
    assert validated[0].hours == [12, 13]
    assert validated[0].applies is True

    assert validated[1].directive_type == "minimum_battery_reserve"
    assert validated[1].minimum_energy_kwh == 35.0
    assert validated[1].hours == [18, 19]
    assert validated[1].applies is True

    assert validated[2].directive_type == "no_charge_window"
    assert validated[2].hours == [14, 15]
    assert validated[2].applies is True


def test_no_discharge_and_max_grid_window(battery):
    """Verifies no_discharge_window and max_grid_window directives."""
    raw = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "no_discharge_window",
            "structured_adjustment": {"hours": [7, 8]},
            "explanation": "Hold discharge",
        },
        {
            "note_index": 1,
            "applies": True,
            "directive_type": "max_grid_window",
            "structured_adjustment": {"hours": [18, 19, 20], "max_grid_kwh": 40.0},
            "explanation": "Cap grid import",
        },
    ]
    notes = ["n0", "n1"]
    val = validate_directives(raw, notes, battery)
    assert len(val) == 2
    assert val[0].directive_type == "no_discharge_window"
    assert val[0].hours == [7, 8]
    assert val[1].directive_type == "max_grid_window"
    assert val[1].max_grid_kwh == 40.0
    assert val[1].hours == [18, 19, 20]


def test_hours_sorting_and_deduplication(battery):
    """Confirms unsorted, duplicate, and out-of-range hours are sanitized strictly to unique [0, 23]."""
    raw = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": [15, 12, 15, 99, -5, 13, 12]},
            "explanation": "Messy hours",
        }
    ]
    notes = ["Messy hours note"]
    val = validate_directives(raw, notes, battery)
    assert len(val) == 1
    assert val[0].directive_type == "no_charge_window"
    assert val[0].hours == [12, 13, 15]


def test_empty_hours_coerces_to_noop(battery):
    """Confirms that if an hours array becomes empty after filtering out-of-range values, it coerces to no_op."""
    raw = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": [25, 99, -1]},
            "explanation": "All invalid hours",
        }
    ]
    val = validate_directives(raw, ["Invalid hours note"], battery)
    assert len(val) == 1
    assert val[0].directive_type == "no_op"
    assert val[0].applies is False


def test_solar_factor_clamping_and_rejection(battery):
    """Confirms factor clamping for tiny floating noise and rejection for gross out-of-bounds values."""
    raw = [
        # Slightly above 1.0 -> clamped to 1.0
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [12], "factor": 1.005},
        },
        # Grossly out of bounds (e.g. 80.0 instead of 0.8) -> coerced to no_op
        {
            "note_index": 1,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [13], "factor": 80.0},
        },
        # Negative factor -> coerced to no_op
        {
            "note_index": 2,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [14], "factor": -0.5},
        },
    ]
    notes = ["n0", "n1", "n2"]
    val = validate_directives(raw, notes, battery)
    assert val[0].directive_type == "solar_reduction"
    assert val[0].factor == 1.0

    assert val[1].directive_type == "no_op"
    assert val[1].applies is False

    assert val[2].directive_type == "no_op"
    assert val[2].applies is False


def test_battery_reserve_bounds_enforcement(battery):
    """Confirms minimum_battery_reserve cannot exceed capacity_kwh or be negative."""
    raw = [
        # Exceeds battery capacity of 100 kWh
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": [18], "minimum_energy_kwh": 120.0},
        },
        # Negative reserve
        {
            "note_index": 1,
            "applies": True,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": [19], "minimum_energy_kwh": -10.0},
        },
    ]
    val = validate_directives(raw, ["n0", "n1"], battery)
    assert val[0].directive_type == "no_op"
    assert val[1].directive_type == "no_op"


def test_bogus_directive_type_coercion(battery):
    """Confirms invented or unsupported directive types are safely coerced to no_op."""
    raw = [
        {
            "note_index": 0,
            "directive_type": "increase_solar_output",
            "structured_adjustment": {"hours": [12], "factor": 2.0},
        }
    ]
    val = validate_directives(raw, ["Invented directive"], battery)
    assert len(val) == 1
    assert val[0].directive_type == "no_op"
    assert val[0].applies is False


def test_deduplication_and_missing_index_backfill(battery):
    """Confirms duplicate note_index keeps first entry, and missing note_index is backfilled as no_op."""
    raw = [
        # Note 0 first occurrence
        {
            "note_index": 0,
            "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": [14]},
        },
        # Note 0 duplicate (should be dropped)
        {
            "note_index": 0,
            "directive_type": "no_discharge_window",
            "structured_adjustment": {"hours": [15]},
        },
        # Note 2 is provided, but Note 1 is missing entirely!
        {
            "note_index": 2,
            "directive_type": "no_discharge_window",
            "structured_adjustment": {"hours": [8]},
        },
    ]
    notes = ["First note", "Second note", "Third note"]
    val = validate_directives(raw, notes, battery)

    assert len(val) == 3
    # Index 0 preserved
    assert val[0].note_index == 0
    assert val[0].directive_type == "no_charge_window"

    # Index 1 backfilled as no_op
    assert val[1].note_index == 1
    assert val[1].directive_type == "no_op"
    assert val[1].applies is False

    # Index 2 preserved
    assert val[2].note_index == 2
    assert val[2].directive_type == "no_discharge_window"


def test_fuzz_garbage_inputs_never_raise(battery):
    """Fuzzes validate_directives with arbitrary non-conforming types and structures."""
    garbage_inputs = [
        None,
        {},
        "some random string",
        [1, 2, 3],
        [{"note_index": "not_an_int"}],
        [{"note_index": None, "directive_type": None}],
        [{"note_index": 0, "structured_adjustment": "not_a_dict", "directive_type": "solar_reduction"}],
        [{"note_index": 0, "directive_type": "solar_reduction", "structured_adjustment": {"factor": float("nan"), "hours": [1]}}],
        [{"note_index": 0, "directive_type": "solar_reduction", "structured_adjustment": {"factor": float("inf"), "hours": [1]}}],
        [{"note_index": 0, "directive_type": "max_grid_window", "structured_adjustment": {"max_grid_kwh": "infinite", "hours": []}}],
    ]
    for garbage in garbage_inputs:
        # Should never raise, always returning safe ValidatedDirective list
        result = validate_directives(garbage if isinstance(garbage, list) else [garbage], ["Note 0"], battery)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].note_index == 0
        # If it was garbage, it must be safe no_op
        assert result[0].directive_type in ("no_op", "solar_reduction", "max_grid_window")
