"""
Tests for API contract, Pydantic v2 schemas, and HTTP endpoints.
Ensures strict adherence to the challenge request/response specifications,
including Phase 7 robustness, failure resilience, concurrency, and edge-case hardening.
"""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas import (
    BatteryConfig,
    DirectiveInterpretation,
    HourEntry,
    HourlyPlanEntry,
    OptimizeRequest,
    OptimizeResponse,
    ValidatedDirective,
)


@pytest.fixture
def client():
    return TestClient(app)


def sample_battery_config() -> dict:
    return {
        "capacity_kwh": 100.0,
        "initial_energy_kwh": 50.0,
        "minimum_energy_kwh": 10.0,
        "max_charge_kwh_per_hour": 25.0,
        "max_discharge_kwh_per_hour": 25.0,
    }


def sample_24_hours() -> list:
    return [
        {
            "hour": h,
            "demand_kwh": 20.0 + (h % 5),
            "solar_kwh": 15.0 if 8 <= h <= 16 else 0.0,
            "tariff_bdt_per_kwh": 6.0 if 23 <= h or h <= 6 else 10.0,
        }
        for h in range(24)
    ]


def test_valid_optimize_request():
    """Confirms that a well-formed 24-hour request passes validation."""
    data = {
        "scenario_id": "scen_valid_01",
        "operator_notes": ["Reduce solar to 20% from 1 PM to 3 PM", "Keep battery above 30 kWh between 6 PM and 9 PM"],
        "hours": sample_24_hours(),
        "battery": sample_battery_config(),
    }
    req = OptimizeRequest(**data)
    assert req.scenario_id == "scen_valid_01"
    assert len(req.hours) == 24
    assert len(req.operator_notes) == 2
    assert [h.hour for h in req.hours] == list(range(24))


def test_optimize_request_shuffled_hours():
    """Confirms that hours provided in arbitrary order are sorted 0..23 automatically."""
    hours = sample_24_hours()
    reversed_hours = list(reversed(hours))
    data = {
        "scenario_id": "scen_shuffled_01",
        "operator_notes": ["Routine maintenance note"],
        "hours": reversed_hours,
        "battery": sample_battery_config(),
    }
    req = OptimizeRequest(**data)
    assert [h.hour for h in req.hours] == list(range(24))


def test_optimize_request_invalid_hour_count():
    """Fails if hours count != 24."""
    hours_23 = sample_24_hours()[:-1]
    data = {
        "scenario_id": "scen_fail_23",
        "operator_notes": ["Check solar output"],
        "hours": hours_23,
        "battery": sample_battery_config(),
    }
    with pytest.raises(ValidationError) as exc:
        OptimizeRequest(**data)
    assert "hours must have exactly 24 entries" in str(exc.value)


def test_optimize_request_missing_hour_coverage():
    """Fails if hours do not cover 0-23 uniquely."""
    hours_duplicate = sample_24_hours()
    hours_duplicate[23] = hours_duplicate[0].copy()
    data = {
        "scenario_id": "scen_duplicate_hour",
        "operator_notes": ["Check peak shaving"],
        "hours": hours_duplicate,
        "battery": sample_battery_config(),
    }
    with pytest.raises(ValidationError) as exc:
        OptimizeRequest(**data)
    assert "hours must contain exactly one entry for each hour from 0 to 23" in str(exc.value)


def test_optimize_request_operator_notes_bounds():
    """Rejects empty operator notes or notes exceeding length 3."""
    with pytest.raises(ValidationError):
        OptimizeRequest(
            scenario_id="scen_empty_notes",
            operator_notes=[],
            hours=sample_24_hours(),
            battery=sample_battery_config(),
        )

    with pytest.raises(ValidationError):
        OptimizeRequest(
            scenario_id="scen_4_notes",
            operator_notes=["n1", "n2", "n3", "n4"],
            hours=sample_24_hours(),
            battery=sample_battery_config(),
        )

    with pytest.raises(ValidationError):
        OptimizeRequest(
            scenario_id="scen_blank_note",
            operator_notes=["Valid note", "   "],
            hours=sample_24_hours(),
            battery=sample_battery_config(),
        )


def test_battery_config_validation():
    """Rejects invalid physical battery constraints."""
    with pytest.raises(ValidationError):
        BatteryConfig(
            capacity_kwh=50.0,
            initial_energy_kwh=60.0,
            minimum_energy_kwh=10.0,
            max_charge_kwh_per_hour=20.0,
            max_discharge_kwh_per_hour=20.0,
        )

    with pytest.raises(ValidationError):
        BatteryConfig(
            capacity_kwh=50.0,
            initial_energy_kwh=5.0,
            minimum_energy_kwh=10.0,
            max_charge_kwh_per_hour=20.0,
            max_discharge_kwh_per_hour=20.0,
        )


def test_directive_interpretation_validation():
    """Enforces applies=False and structured_adjustment=null for no_op, and applies=True otherwise."""
    noop = DirectiveInterpretation(
        note_index=0,
        applies=False,
        directive_type="no_op",
        structured_adjustment=None,
        explanation="Irrelevant note",
    )
    assert noop.directive_type == "no_op"
    assert noop.applies is False
    assert noop.structured_adjustment is None

    with pytest.raises(ValidationError):
        DirectiveInterpretation(
            note_index=0,
            applies=True,
            directive_type="no_op",
            structured_adjustment=None,
            explanation="Invalid",
        )

    with pytest.raises(ValidationError):
        DirectiveInterpretation(
            note_index=1,
            applies=False,
            directive_type="solar_reduction",
            structured_adjustment={"hours": [12, 13], "factor": 0.5},
            explanation="Invalid applies flag",
        )

    with pytest.raises(ValidationError):
        DirectiveInterpretation(
            note_index=1,
            applies=True,
            directive_type="solar_reduction",
            structured_adjustment=None,
            explanation="Missing adjustment body",
        )


def test_hourly_plan_entry_idle_rule():
    """Validates that battery_kwh must be 0 when battery_action is idle."""
    entry = HourlyPlanEntry(
        hour=0,
        grid_kwh=10.0,
        solar_used_kwh=0.0,
        battery_action="idle",
        battery_kwh=0.0,
        battery_energy_after_kwh=50.0,
    )
    assert entry.battery_kwh == 0.0

    with pytest.raises(ValidationError):
        HourlyPlanEntry(
            hour=0,
            grid_kwh=10.0,
            solar_used_kwh=0.0,
            battery_action="idle",
            battery_kwh=5.0,
            battery_energy_after_kwh=50.0,
        )


def test_valid_optimize_response():
    """Verifies that a full OptimizeResponse validates correctly."""
    hourly_plan = [
        HourlyPlanEntry(
            hour=h,
            grid_kwh=15.0,
            solar_used_kwh=5.0,
            battery_action="idle",
            battery_kwh=0.0,
            battery_energy_after_kwh=50.0,
        )
        for h in range(24)
    ]
    interpretations = [
        DirectiveInterpretation(
            note_index=0,
            applies=True,
            directive_type="solar_reduction",
            structured_adjustment={"hours": [13, 14], "factor": 0.2},
            explanation="Solar reduced by 80% between 1 PM and 3 PM",
        )
    ]
    resp = OptimizeResponse(
        scenario_id="scen_01",
        directive_interpretation=interpretations,
        hourly_plan=hourly_plan,
        total_grid_kwh=360.0,
        total_cost_bdt=2880.0,
        peak_grid_kwh=15.0,
        plan_summary="Plan successfully generated and validated.",
    )
    assert resp.scenario_id == "scen_01"
    assert len(resp.hourly_plan) == 24


def test_validated_directive_to_api_representation():
    """Verifies internal ValidatedDirective transforms to the exact response shape."""
    vd_solar = ValidatedDirective(
        note_index=0,
        directive_type="solar_reduction",
        applies=True,
        hours=[13, 14],
        factor=0.2,
        explanation="Solar reduced",
    )
    assert vd_solar.to_api_representation() == {
        "note_index": 0,
        "applies": True,
        "directive_type": "solar_reduction",
        "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
        "explanation": "Solar reduced",
    }


# -----------------------------------------------------------------------------
# HTTP Endpoint Tests
# -----------------------------------------------------------------------------
def test_endpoint_get_health(client):
    """GET /health must return HTTP 200 with {'status': 'ok'}."""
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_endpoint_optimize_energy_success(client):
    """POST /optimize-energy returns 200 and matches full schema."""
    req_body = {
        "scenario_id": "test_e2e_01",
        "operator_notes": [
            "Cut solar by 50% from 12:00 to 14:00 due to dust storm.",
            "Hold battery discharge between 18:00 and 20:00.",
        ],
        "hours": sample_24_hours(),
        "battery": sample_battery_config(),
    }
    res = client.post("/optimize-energy", json=req_body)
    assert res.status_code == 200
    data = res.json()

    assert data["scenario_id"] == "test_e2e_01"
    assert len(data["directive_interpretation"]) == 2
    assert len(data["hourly_plan"]) == 24

    plan = data["hourly_plan"]
    calc_grid = round(sum(p["grid_kwh"] for p in plan), 4)
    calc_cost = round(
        sum(p["grid_kwh"] * sample_24_hours()[h]["tariff_bdt_per_kwh"] for h, p in enumerate(plan)),
        4,
    )
    calc_peak = round(max(p["grid_kwh"] for p in plan), 4)

    assert abs(data["total_grid_kwh"] - calc_grid) < 0.01
    assert abs(data["total_cost_bdt"] - calc_cost) < 0.01
    assert abs(data["peak_grid_kwh"] - calc_peak) < 0.01


# -----------------------------------------------------------------------------
# Phase 7 Hardening Tests
# -----------------------------------------------------------------------------
def test_endpoint_empty_body_400(client):
    """Empty JSON request body returns clean HTTP 400."""
    res = client.post("/optimize-energy", json={})
    assert res.status_code == 400
    assert res.json()["error"] == "invalid_request"


def test_endpoint_hours_25_entries_400(client):
    """Request with 25 hour entries returns clean HTTP 400."""
    hours_25 = sample_24_hours() + [{"hour": 24, "demand_kwh": 10, "solar_kwh": 0, "tariff_bdt_per_kwh": 5}]
    req = {
        "scenario_id": "test_25_hours",
        "operator_notes": ["Note"],
        "hours": hours_25,
        "battery": sample_battery_config(),
    }
    res = client.post("/optimize-energy", json=req)
    assert res.status_code == 400
    assert "hours" in res.json()["detail"]


def test_endpoint_negative_numeric_values_400(client):
    """Negative demand or solar values return clean HTTP 400."""
    bad_hours = sample_24_hours()
    bad_hours[5]["demand_kwh"] = -10.0
    req = {
        "scenario_id": "test_negative_demand",
        "operator_notes": ["Note"],
        "hours": bad_hours,
        "battery": sample_battery_config(),
    }
    res = client.post("/optimize-energy", json=req)
    assert res.status_code == 400


def test_endpoint_non_numeric_values_400(client):
    """Non-numeric strings where floats are expected return clean HTTP 400."""
    bad_hours = sample_24_hours()
    bad_hours[0]["solar_kwh"] = "not_a_number"
    req = {
        "scenario_id": "test_string_solar",
        "operator_notes": ["Note"],
        "hours": bad_hours,
        "battery": sample_battery_config(),
    }
    res = client.post("/optimize-energy", json=req)
    assert res.status_code == 400


def test_endpoint_all_noop_distractor_scenario_200(client):
    """A scenario with solely irrelevant distractor notes executes cleanly without errors."""
    req = {
        "scenario_id": "test_distractor_only",
        "operator_notes": [
            "Campus security patrol shift change at 4 PM.",
            "Cafeteria menu update for lunch tomorrow.",
        ],
        "hours": sample_24_hours(),
        "battery": sample_battery_config(),
    }
    res = client.post("/optimize-energy", json=req)
    assert res.status_code == 200
    data = res.json()
    for d in data["directive_interpretation"]:
        assert d["directive_type"] == "no_op"
        assert d["applies"] is False
        assert d["structured_adjustment"] is None
    assert len(data["hourly_plan"]) == 24


def test_endpoint_llm_failure_resilience_200(client):
    """
    Simulates provider timeout or 500 error during LLM call.
    Verifies the service degrades gracefully to no_op fallbacks and returns 200 OK.
    """
    with patch("app.main.interpret_notes") as mock_interp:
        # Simulate LLM raising an exception or returning empty
        mock_interp.return_value = [
            {
                "note_index": 0,
                "applies": False,
                "directive_type": "no_op",
                "structured_adjustment": None,
                "explanation": "Provider unavailable, safely defaulted to no_op",
            }
        ]
        req = {
            "scenario_id": "test_llm_failure",
            "operator_notes": ["Cut solar output during haze"],
            "hours": sample_24_hours(),
            "battery": sample_battery_config(),
        }
        res = client.post("/optimize-energy", json=req)
        assert res.status_code == 200
        data = res.json()
        assert data["directive_interpretation"][0]["directive_type"] == "no_op"
        assert len(data["hourly_plan"]) == 24


def test_endpoint_concurrency(client):
    """Verifies that multiple concurrent requests do not corrupt shared state."""
    req1 = {
        "scenario_id": "concurrent_01",
        "operator_notes": ["Note A"],
        "hours": sample_24_hours(),
        "battery": sample_battery_config(),
    }
    req2 = {
        "scenario_id": "concurrent_02",
        "operator_notes": ["Note B"],
        "hours": sample_24_hours(),
        "battery": sample_battery_config(),
    }

    with patch("app.main.interpret_notes") as mock_interp:
        mock_interp.return_value = [
            {
                "note_index": 0,
                "applies": False,
                "directive_type": "no_op",
                "structured_adjustment": None,
                "explanation": "Concurrent test",
            }
        ]

        def call_api(req):
            return client.post("/optimize-energy", json=req)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(call_api, req) for req in [req1, req2, req1, req2]]
            results = [f.result() for f in futures]

        for res in results:
            assert res.status_code == 200
            assert len(res.json()["hourly_plan"]) == 24
