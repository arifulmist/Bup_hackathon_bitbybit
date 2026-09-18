"""
Pydantic v2 schemas for GridWise Energy Optimizer.
Defines exact API request and response contracts, plus internal models.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------
# Supported Directive Types
# ---------------------------------------------------------
DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
]

BatteryActionType = Literal["charge", "discharge", "idle"]


# ---------------------------------------------------------
# Request Models
# ---------------------------------------------------------
class HourEntry(BaseModel):
    """Energy profile for a single hour."""

    model_config = ConfigDict(extra="forbid")

    hour: int = Field(..., ge=0, le=23, description="Hour of the day (0-23)")
    demand_kwh: float = Field(..., ge=0, description="Demand in kWh")
    solar_kwh: float = Field(..., ge=0, description="Available solar generation in kWh")
    tariff_bdt_per_kwh: float = Field(..., ge=0, description="Grid tariff in BDT per kWh")


class BatteryConfig(BaseModel):
    """Battery specifications and operational limits."""

    model_config = ConfigDict(extra="forbid")

    capacity_kwh: float = Field(..., gt=0, description="Total battery storage capacity in kWh")
    initial_energy_kwh: float = Field(..., ge=0, description="Initial battery energy at start of hour 0")
    minimum_energy_kwh: float = Field(..., ge=0, description="Minimum reserve energy allowed in kWh")
    max_charge_kwh_per_hour: float = Field(..., ge=0, description="Max charging rate in kWh/hour")
    max_discharge_kwh_per_hour: float = Field(..., ge=0, description="Max discharging rate in kWh/hour")

    @model_validator(mode="after")
    def validate_battery_limits(self) -> "BatteryConfig":
        if self.minimum_energy_kwh > self.capacity_kwh:
            raise ValueError("minimum_energy_kwh cannot exceed capacity_kwh")
        if self.initial_energy_kwh > self.capacity_kwh:
            raise ValueError("initial_energy_kwh cannot exceed capacity_kwh")
        if self.initial_energy_kwh < self.minimum_energy_kwh:
            raise ValueError("initial_energy_kwh cannot be less than minimum_energy_kwh")
        return self


class OptimizeRequest(BaseModel):
    """Contract for POST /optimize-energy request."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(..., min_length=1, description="Unique scenario identifier")
    operator_notes: List[str] = Field(..., description="1 to 3 non-empty natural language operator notes")
    hours: List[HourEntry] = Field(..., description="Exactly 24 hour entries covering hours 0 to 23")
    battery: BatteryConfig = Field(..., description="Battery configuration")

    @field_validator("operator_notes")
    @classmethod
    def validate_operator_notes(cls, v: List[str]) -> List[str]:
        if not (1 <= len(v) <= 3):
            raise ValueError("operator_notes must contain between 1 and 3 entries")
        for i, note in enumerate(v):
            if not isinstance(note, str) or not note.strip():
                raise ValueError(f"operator_note at index {i} must be a non-empty string")
        return v

    @field_validator("hours")
    @classmethod
    def validate_hours(cls, v: List[HourEntry]) -> List[HourEntry]:
        if len(v) != 24:
            raise ValueError(f"hours must have exactly 24 entries, received {len(v)}")
        hours_present = [entry.hour for entry in v]
        if set(hours_present) != set(range(24)):
            raise ValueError("hours must contain exactly one entry for each hour from 0 to 23")
        # Ensure hours are sorted 0..23 regardless of input order
        return sorted(v, key=lambda x: x.hour)


# ---------------------------------------------------------
# Response Models
# ---------------------------------------------------------
class DirectiveInterpretation(BaseModel):
    """Interpretation of a single operator note."""

    model_config = ConfigDict(extra="forbid")

    note_index: int = Field(..., ge=0, description="0-indexed position of the operator note")
    applies: bool = Field(..., description="True for real directives, false ONLY for no_op")
    directive_type: DirectiveType = Field(..., description="One of the 6 canonical directive types")
    structured_adjustment: Optional[Dict[str, Any]] = Field(
        ..., description="Specific parameter adjustments dict, or null for no_op"
    )
    explanation: str = Field(..., description="Short human-readable rationale")

    @model_validator(mode="after")
    def validate_applies_and_adjustment(self) -> "DirectiveInterpretation":
        if self.directive_type == "no_op":
            if self.applies is not False:
                raise ValueError("no_op directive must have applies=False")
            if self.structured_adjustment is not None:
                raise ValueError("no_op directive must have structured_adjustment=null")
        else:
            if self.applies is not True:
                raise ValueError(f"Directive type {self.directive_type} must have applies=True")
            if self.structured_adjustment is None:
                raise ValueError(f"Directive type {self.directive_type} requires non-null structured_adjustment")
        return self


class HourlyPlanEntry(BaseModel):
    """Energy dispatch schedule for a single hour."""

    model_config = ConfigDict(extra="forbid")

    hour: int = Field(..., ge=0, le=23, description="Hour index (0-23)")
    grid_kwh: float = Field(..., ge=0, description="Grid import energy in kWh (>= 0)")
    solar_used_kwh: float = Field(..., ge=0, description="Solar energy directly consumed in kWh (>= 0)")
    battery_action: BatteryActionType = Field(..., description="Action taken by battery: charge, discharge, or idle")
    battery_kwh: float = Field(..., ge=0, description="Energy charged/discharged in kWh (must be 0 for idle)")
    battery_energy_after_kwh: float = Field(
        ..., ge=0, description="State of charge in battery after this hour's action in kWh"
    )

    @model_validator(mode="after")
    def validate_battery_action_consistency(self) -> "HourlyPlanEntry":
        if self.battery_action == "idle":
            if abs(self.battery_kwh) > 1e-4:
                raise ValueError("battery_kwh must be 0 when battery_action is idle")
        return self


class OptimizeResponse(BaseModel):
    """Contract for POST /optimize-energy response (HTTP 200)."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(..., description="Echoes the request scenario_id")
    directive_interpretation: List[DirectiveInterpretation] = Field(
        ..., description="List of interpretations matching operator_notes order (0..N-1)"
    )
    hourly_plan: List[HourlyPlanEntry] = Field(
        ..., description="Exactly 24 hourly dispatch entries covering hours 0 to 23"
    )
    total_grid_kwh: float = Field(..., ge=0, description="Sum of grid_kwh across all 24 hours")
    total_cost_bdt: float = Field(..., ge=0, description="Total monetary cost in BDT across all 24 hours")
    peak_grid_kwh: float = Field(..., ge=0, description="Peak single-hour grid import in kWh")
    plan_summary: str = Field(..., description="Executive human-readable summary of the plan")

    @field_validator("hourly_plan")
    @classmethod
    def validate_hourly_plan_length(cls, v: List[HourlyPlanEntry]) -> List[HourlyPlanEntry]:
        if len(v) != 24:
            raise ValueError(f"hourly_plan must contain exactly 24 entries, received {len(v)}")
        hours_present = [entry.hour for entry in v]
        if hours_present != list(range(24)):
            raise ValueError("hourly_plan entries must be in sequential order from hour 0 to 23")
        return v


# ---------------------------------------------------------
# Internal Validated Directive Representation
# ---------------------------------------------------------
@dataclass
class ValidatedDirective:
    """
    Internal, fully sanitized directive ready for direct consumption by the LP solver.
    Decoupled from untrusted LLM JSON output.
    """

    note_index: int
    directive_type: DirectiveType
    applies: bool
    hours: List[int] = field(default_factory=list)
    factor: Optional[float] = None
    minimum_energy_kwh: Optional[float] = None
    max_grid_kwh: Optional[float] = None
    explanation: str = ""
    original_text: str = ""

    def to_api_representation(self) -> Dict[str, Any]:
        """Converts internal validated directive to the exact API response dict shape."""
        structured_adj: Optional[Dict[str, Any]] = None
        if self.applies and self.directive_type != "no_op":
            if self.directive_type == "solar_reduction":
                structured_adj = {"hours": self.hours, "factor": self.factor}
            elif self.directive_type == "minimum_battery_reserve":
                structured_adj = {"hours": self.hours, "minimum_energy_kwh": self.minimum_energy_kwh}
            elif self.directive_type == "no_charge_window":
                structured_adj = {"hours": self.hours}
            elif self.directive_type == "no_discharge_window":
                structured_adj = {"hours": self.hours}
            elif self.directive_type == "max_grid_window":
                structured_adj = {"hours": self.hours, "max_grid_kwh": self.max_grid_kwh}

        return {
            "note_index": self.note_index,
            "applies": self.applies,
            "directive_type": self.directive_type,
            "structured_adjustment": structured_adj,
            "explanation": self.explanation,
        }
