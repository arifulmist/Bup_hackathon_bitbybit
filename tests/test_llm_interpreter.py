"""
Unit and integration tests for LLM Operator-Note Interpreter.
Tests mock responses, error fallbacks, index mapping, and live API connectivity.
"""

from unittest.mock import MagicMock
import pytest
from app.config import LLM_API_KEY
from app.llm_interpreter import interpret_notes, INTERPRETER_SYSTEM_PROMPT


def test_system_prompt_integrity():
    """Verifies that the system prompt contains all 6 canonical directive definitions."""
    for directive in [
        "solar_reduction",
        "minimum_battery_reserve",
        "no_charge_window",
        "no_discharge_window",
        "max_grid_window",
        "no_op",
    ]:
        assert directive in INTERPRETER_SYSTEM_PROMPT
    assert "start hour inclusive, end hour exclusive" in INTERPRETER_SYSTEM_PROMPT.lower()
    assert "factor" in INTERPRETER_SYSTEM_PROMPT


def test_interpret_notes_mock_success_3_notes():
    """Confirms interpret_notes maps a 3-note input correctly from mock LLM output."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = """
    {
      "directives": [
        {
          "note_index": 0,
          "applies": true,
          "directive_type": "solar_reduction",
          "structured_adjustment": {"hours": [13, 14], "factor": 0.25},
          "explanation": "Solar reduced by 75%"
        },
        {
          "note_index": 1,
          "applies": true,
          "directive_type": "minimum_battery_reserve",
          "structured_adjustment": {"hours": [18, 19, 20], "minimum_energy_kwh": 35.0},
          "explanation": "Reserve 35 kWh for evening"
        },
        {
          "note_index": 2,
          "applies": false,
          "directive_type": "no_op",
          "structured_adjustment": null,
          "explanation": "General notice"
        }
      ]
    }
    """
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_response

    notes = [
        "Rooftop solar down 75% between 1 PM and 3 PM",
        "Maintain 35 kWh battery storage between 6 PM and 9 PM",
        "Campus sports event at 5 PM",
    ]
    results = interpret_notes(notes, client=mock_client)

    assert len(results) == 3
    assert results[0]["note_index"] == 0
    assert results[0]["directive_type"] == "solar_reduction"
    assert results[0]["structured_adjustment"]["factor"] == 0.25

    assert results[1]["note_index"] == 1
    assert results[1]["directive_type"] == "minimum_battery_reserve"

    assert results[2]["note_index"] == 2
    assert results[2]["directive_type"] == "no_op"
    assert results[2]["applies"] is False


def test_interpret_notes_mock_malformed_json_fallback():
    """Confirms that unparseable non-JSON output safely degrades to no_op fallback without raising."""
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "INVALID_NON_JSON_CONTENT <<< >>>"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_response

    notes = ["Reduce solar at noon", "Reserve 20 kWh at 6 PM"]
    results = interpret_notes(notes, client=mock_client)

    assert len(results) == 2
    for i, res in enumerate(results):
        assert res["note_index"] == i
        assert res["directive_type"] == "no_op"
        assert res["applies"] is False
        assert res["structured_adjustment"] is None
        assert "fallback" in res["explanation"].lower()


def test_interpret_notes_mock_exception_fallback():
    """Confirms that an unexpected network/timeout exception triggers retry and safe fallback."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = TimeoutError("Request timed out")

    notes = ["Charge battery at night"]
    results = interpret_notes(notes, client=mock_client)

    assert len(results) == 1
    assert results[0]["note_index"] == 0
    assert results[0]["directive_type"] == "no_op"
    assert results[0]["applies"] is False
    assert results[0]["structured_adjustment"] is None


@pytest.mark.skipif(not LLM_API_KEY, reason="Live LLM tests require configured LLM_API_KEY")
def test_interpret_notes_live_call():
    """Performs an actual end-to-end call against the configured LLM API provider."""
    notes = [
        "Solar generation will drop by 80% between 1 PM and 3 PM due to haze.",
        "Keep at least 25 kWh in the battery from 6 PM to 9 PM.",
    ]
    results = interpret_notes(notes)
    assert len(results) == 2
    # Verify first directive
    assert results[0]["note_index"] == 0
    assert results[0]["directive_type"] == "solar_reduction"
    assert results[0]["applies"] is True
    # Factor should be 0.20 (80% drop -> 20% remaining)
    assert results[0]["structured_adjustment"]["factor"] == pytest.approx(0.2, abs=0.05)
    assert results[0]["structured_adjustment"]["hours"] == [13, 14]

    # Verify second directive
    assert results[1]["note_index"] == 1
    assert results[1]["directive_type"] == "minimum_battery_reserve"
    assert results[1]["applies"] is True
    assert results[1]["structured_adjustment"]["minimum_energy_kwh"] == pytest.approx(25.0)
    assert results[1]["structured_adjustment"]["hours"] == [18, 19, 20]
