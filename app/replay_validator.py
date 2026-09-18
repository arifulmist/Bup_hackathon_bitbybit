"""
Independent Replay Validator.
Re-checks the generated 24-hour dispatch schedule against the original request
and sanitized directives. Serves as an independent secondary safety net to verify
that every directive was actually obeyed and all physical bounds hold.
"""

from typing import List, Optional, Tuple
from app.config import REPLAY_STRICT_FAIL
from app.logging_utils import logger
from app.schemas import HourlyPlanEntry, OptimizeRequest, ValidatedDirective

TOLERANCE = 0.01  # Challenge absolute tolerance for energy and cost


class ReplayValidationError(Exception):
    """Raised when an optimized schedule fails independent validation."""
    pass


def replay_validate_schedule(
    request: OptimizeRequest,
    validated_directives: List[ValidatedDirective],
    hourly_plan: List[HourlyPlanEntry],
) -> Tuple[bool, List[str]]:
    """
    Independently walks the 24-hour hourly_plan to verify physical constraints,
    energy balance, battery bounds, and directive compliance.

    Args:
        request: Original validated OptimizeRequest.
        validated_directives: Fully sanitized directives from guardrails layer.
        hourly_plan: 24 entries produced by the optimizer.

    Returns:
        Tuple of (is_valid, list_of_violations)
    """
    violations: List[str] = []
    battery = request.battery
    hours_data = request.hours

    if len(hourly_plan) != 24:
        violations.append(f"Hourly plan must contain exactly 24 entries, found {len(hourly_plan)}")
        return False, violations

    # 1. Independent parameter reconstruction
    effective_solar = [h.solar_kwh for h in hours_data]
    effective_min_reserve = [battery.minimum_energy_kwh for _ in range(24)]
    effective_grid_cap: List[Optional[float]] = [None for _ in range(24)]
    no_charge_hours: set[int] = set()
    no_discharge_hours: set[int] = set()

    for directive in validated_directives:
        if not directive.applies or directive.directive_type == "no_op":
            continue

        if directive.directive_type == "solar_reduction":
            factor = directive.factor if directive.factor is not None else 1.0
            for h in directive.hours:
                if 0 <= h < 24:
                    effective_solar[h] = hours_data[h].solar_kwh * factor

        elif directive.directive_type == "minimum_battery_reserve":
            reserve = (
                directive.minimum_energy_kwh
                if directive.minimum_energy_kwh is not None
                else battery.minimum_energy_kwh
            )
            for h in directive.hours:
                if 0 <= h < 24:
                    effective_min_reserve[h] = max(effective_min_reserve[h], reserve)

        elif directive.directive_type == "no_charge_window":
            for h in directive.hours:
                if 0 <= h < 24:
                    no_charge_hours.add(h)

        elif directive.directive_type == "no_discharge_window":
            for h in directive.hours:
                if 0 <= h < 24:
                    no_discharge_hours.add(h)

        elif directive.directive_type == "max_grid_window":
            cap = directive.max_grid_kwh if directive.max_grid_kwh is not None else float("inf")
            for h in directive.hours:
                if 0 <= h < 24:
                    if effective_grid_cap[h] is None:
                        effective_grid_cap[h] = cap
                    else:
                        effective_grid_cap[h] = min(effective_grid_cap[h], cap)

    # 2. Walk hour-by-hour checking all invariants
    e_prev = battery.initial_energy_kwh

    for h, entry in enumerate(hourly_plan):
        if entry.hour != h:
            violations.append(f"Hour mismatch at index {h}: plan specifies hour {entry.hour}")

        demand = hours_data[h].demand_kwh
        eff_solar = effective_solar[h]

        # Solar usage check
        if entry.solar_used_kwh > eff_solar + TOLERANCE:
            violations.append(
                f"Hour {h}: solar_used ({entry.solar_used_kwh:.3f} kWh) exceeds effective solar ({eff_solar:.3f} kWh)"
            )

        # Rate limits & action checks
        charge_kwh = 0.0
        discharge_kwh = 0.0

        if entry.battery_action == "charge":
            charge_kwh = entry.battery_kwh
            if charge_kwh > battery.max_charge_kwh_per_hour + TOLERANCE:
                violations.append(
                    f"Hour {h}: charge ({charge_kwh:.3f} kWh) exceeds max charge rate ({battery.max_charge_kwh_per_hour} kWh)"
                )
            if h in no_charge_hours:
                violations.append(f"Hour {h}: battery charged during active no_charge_window")

        elif entry.battery_action == "discharge":
            discharge_kwh = entry.battery_kwh
            if discharge_kwh > battery.max_discharge_kwh_per_hour + TOLERANCE:
                violations.append(
                    f"Hour {h}: discharge ({discharge_kwh:.3f} kWh) exceeds max discharge rate ({battery.max_discharge_kwh_per_hour} kWh)"
                )
            if h in no_discharge_hours:
                violations.append(f"Hour {h}: battery discharged during active no_discharge_window")

        elif entry.battery_action == "idle":
            if abs(entry.battery_kwh) > 1e-4:
                violations.append(f"Hour {h}: battery is idle but battery_kwh is non-zero ({entry.battery_kwh})")
        else:
            violations.append(f"Hour {h}: invalid battery_action '{entry.battery_action}'")

        # Battery transition check
        e_expected = e_prev + charge_kwh - discharge_kwh
        if abs(entry.battery_energy_after_kwh - e_expected) > TOLERANCE:
            violations.append(
                f"Hour {h}: battery state transition error: expected {e_expected:.3f} kWh, found {entry.battery_energy_after_kwh:.3f} kWh"
            )

        # Battery bounds check
        if entry.battery_energy_after_kwh < effective_min_reserve[h] - TOLERANCE:
            violations.append(
                f"Hour {h}: battery energy ({entry.battery_energy_after_kwh:.3f} kWh) below required reserve ({effective_min_reserve[h]:.3f} kWh)"
            )
        if entry.battery_energy_after_kwh > battery.capacity_kwh + TOLERANCE:
            violations.append(
                f"Hour {h}: battery energy ({entry.battery_energy_after_kwh:.3f} kWh) exceeds capacity ({battery.capacity_kwh:.3f} kWh)"
            )

        # Grid cap check
        if effective_grid_cap[h] is not None:
            if entry.grid_kwh > effective_grid_cap[h] + TOLERANCE:
                violations.append(
                    f"Hour {h}: grid import ({entry.grid_kwh:.3f} kWh) exceeds cap ({effective_grid_cap[h]:.3f} kWh)"
                )

        # Energy balance check: grid + solar_used + discharge = demand + charge
        generation = entry.grid_kwh + entry.solar_used_kwh + discharge_kwh
        consumption = demand + charge_kwh
        if abs(generation - consumption) > TOLERANCE:
            violations.append(
                f"Hour {h}: energy imbalance: generation ({generation:.3f} kWh) != consumption ({consumption:.3f} kWh)"
            )

        e_prev = entry.battery_energy_after_kwh

    # End-of-day Neutrality
    final_energy = hourly_plan[23].battery_energy_after_kwh
    if abs(final_energy - battery.initial_energy_kwh) > TOLERANCE:
        violations.append(
            f"End-of-day neutrality violated: final energy {final_energy:.3f} kWh != initial {battery.initial_energy_kwh:.3f} kWh"
        )

    is_valid = len(violations) == 0

    if not is_valid:
        logger.error(f"Replay validator detected {len(violations)} rule violations in scenario {request.scenario_id}:")
        for v in violations:
            logger.error(f"  - {v}")
        if REPLAY_STRICT_FAIL:
            raise ReplayValidationError(f"Replay validation failed with {len(violations)} violations")
    else:
        logger.info(f"Replay validator passed successfully for scenario {request.scenario_id}")

    return is_valid, violations
