"""
Tests running against organizer-provided Public Sample Cases JSON.
Scans public_samples/ directory for scenario JSON files, submits them via TestClient,
and verifies schema adherence and physical invariants.
Skips gracefully if no public samples are present.
"""

import json
import os
from glob import glob
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import OptimizeResponse

PUBLIC_SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "public_samples")


def get_public_sample_files():
    """Discovers all JSON sample files in public_samples/."""
    pattern = os.path.join(PUBLIC_SAMPLES_DIR, "*.json")
    return sorted(glob(pattern))


@pytest.fixture
def test_client():
    return TestClient(app)


def test_public_samples_execution(test_client):
    """
    Executes all scenarios found in public_samples/*.json.
    If no sample JSON files are found, skips gracefully.
    """
    sample_files = get_public_sample_files()
    if not sample_files:
        pytest.skip("No public sample cases found in public_samples/ yet. Place organizer sample JSONs there to test.")

    for filepath in sample_files:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Handle both single scenario dict or list of scenarios
        scenarios = data if isinstance(data, list) else [data]

        for scenario in scenarios:
            scenario_id = scenario.get("scenario_id", "unknown")
            response = test_client.post("/optimize-energy", json=scenario)

            assert response.status_code == 200, (
                f"Public sample scenario '{scenario_id}' failed with status {response.status_code}: {response.text}"
            )

            # Validate response schema
            resp_json = response.json()
            validated_resp = OptimizeResponse(**resp_json)

            assert validated_resp.scenario_id == scenario_id
            assert len(validated_resp.hourly_plan) == 24
            assert len(validated_resp.directive_interpretation) == len(scenario.get("operator_notes", []))
            assert validated_resp.total_cost_bdt > 0
            assert validated_resp.total_grid_kwh >= 0
