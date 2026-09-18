"""
Optimization Engine for GridWise Energy Optimizer.
Constructs and solves a 24-hour cost-minimization Linear Program using PuLP (CBC solver).
Guarantees provably optimal, deterministic dispatch schedules honoring all battery physics
and validated operator directives.
"""

from typing import Any, Dict, List, Optional, Tuple
import pulp

from app.logging_utils import logger
from app.schemas import (
    HourlyPlanEntry,
    OptimizeRequest,
    ValidatedDirective,
)


class OptimizationError(Exception):
    """Raised when the optimization problem cannot be solved to optimality."""
    pass


def _clean_val(val: Optional[float], eps: float = 1e-5) -> float:
    """Clamps small solver numerical noise to exactly 0.0."""
    if val is None or abs(val) < eps:
        return 0.0
    return round(float(val), 6)


def solve_optimization(
    request: OptimizeRequest,
    validated_directives: List[ValidatedDirective],
) -> Tuple[List[HourlyPlanEntry], float, float, float]:
    """
    Constructs and solves the 24-hour energy dispatch Linear Program.

    Args:
        request: Validated OptimizeRequest containing 24 hours and battery specs.
        validated_directives: Sanitized operator directives from guardrails layer.

    Returns:
        Tuple of (hourly_plan, total_grid_kwh, total_cost_bdt, peak_grid_kwh)
    """
    hours_data = request.hours
    battery = request.battery

    # -------------------------------------------------------------------------
    # 1. Pre-calculate effective parameter profiles across all 24 hours
    # -------------------------------------------------------------------------
    effective_solar: List[float] = [h.solar_kwh for h in hours_data]
    effective_min_reserve: List[float] = [battery.minimum_energy_kwh for _ in range(24)]
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
            reserve = directive.minimum_energy_kwh if directive.minimum_energy_kwh is not None else battery.minimum_energy_kwh
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

    # -------------------------------------------------------------------------
    # 2. Build Linear Programming Model
    # -------------------------------------------------------------------------
    prob = pulp.LpProblem(f"GridWise_{request.scenario_id}", pulp.LpMinimize)

    # Decision variables for each hour h = 0..23
    grid_vars: List[pulp.LpVariable] = []
    solar_vars: List[pulp.LpVariable] = []
    charge_vars: List[pulp.LpVariable] = []
    discharge_vars: List[pulp.LpVariable] = []
    energy_vars: List[pulp.LpVariable] = []

    for h in range(24):
        # Grid import variable (non-negative, capped if max_grid_window active)
        upper_grid = effective_grid_cap[h]
        grid_vars.append(
            pulp.LpVariable(
                f"grid_{h}",
                lowBound=0.0,
                upBound=upper_grid,
                cat=pulp.LpContinuous,
            )
        )

        # Solar used variable (bounded by effective solar availability)
        solar_vars.append(
            pulp.LpVariable(
                f"solar_{h}",
                lowBound=0.0,
                upBound=max(0.0, effective_solar[h]),
                cat=pulp.LpContinuous,
            )
        )

        # Battery charge amount (bounded by max rate, or 0 if no_charge_window active)
        charge_cap = 0.0 if h in no_charge_hours else battery.max_charge_kwh_per_hour
        charge_vars.append(
            pulp.LpVariable(
                f"charge_{h}",
                lowBound=0.0,
                upBound=charge_cap,
                cat=pulp.LpContinuous,
            )
        )

        # Battery discharge amount (bounded by max rate, or 0 if no_discharge_window active)
        discharge_cap = 0.0 if h in no_discharge_hours else battery.max_discharge_kwh_per_hour
        discharge_vars.append(
            pulp.LpVariable(
                f"discharge_{h}",
                lowBound=0.0,
                upBound=discharge_cap,
                cat=pulp.LpContinuous,
            )
        )

        # Battery state of charge after hour h's action
        min_reserve = effective_min_reserve[h]
        energy_vars.append(
            pulp.LpVariable(
                f"energy_{h}",
                lowBound=min_reserve,
                upBound=battery.capacity_kwh,
                cat=pulp.LpContinuous,
            )
        )

    # -------------------------------------------------------------------------
    # 3. Add Constraints
    # -------------------------------------------------------------------------
    for h in range(24):
        entry = hours_data[h]

        # Hourly Energy Balance:
        # grid_kwh + solar_used_kwh + discharge_kwh = demand_kwh + charge_kwh
        prob += (
            grid_vars[h] + solar_vars[h] + discharge_vars[h]
            == entry.demand_kwh + charge_vars[h],
            f"EnergyBalance_{h}",
        )

        # Battery State of Charge Transition:
        # E_after = E_before + charge - discharge
        if h == 0:
            prob += (
                energy_vars[0]
                == battery.initial_energy_kwh + charge_vars[0] - discharge_vars[0],
                f"BatteryTransition_0",
            )
        else:
            prob += (
                energy_vars[h]
                == energy_vars[h - 1] + charge_vars[h] - discharge_vars[h],
                f"BatteryTransition_{h}",
            )

    # End-of-day Neutrality constraint:
    # Hour 23 ending energy must equal initial energy
    prob += (
        energy_vars[23] == battery.initial_energy_kwh,
        "EndOfDayNeutrality",
    )

    # -------------------------------------------------------------------------
    # 4. Objective Function
    # -------------------------------------------------------------------------
    # Minimize total electricity cost.
    # We also add an imperceptible penalty (1e-6) on battery throughput (charge + discharge).
    # This provably eliminates simultaneous charge and discharge in the same hour without
    # needing integer/binary variables, keeping solve time well under 10 milliseconds.
    cost_terms = [
        grid_vars[h] * hours_data[h].tariff_bdt_per_kwh for h in range(24)
    ]
    throughput_penalty = [
        1e-6 * (charge_vars[h] + discharge_vars[h]) for h in range(24)
    ]
    prob += pulp.lpSum(cost_terms + throughput_penalty), "TotalCost"

    # -------------------------------------------------------------------------
    # 5. Solve the LP using CBC
    # -------------------------------------------------------------------------
    solver = pulp.PULP_CBC_CMD(msg=False)
    status = prob.solve(solver)

    if pulp.LpStatus[status] != "Optimal":
        logger.error(f"Optimization problem solved with non-optimal status: {pulp.LpStatus[status]}")
        raise OptimizationError(f"Optimization failed with solver status: {pulp.LpStatus[status]}")

    # -------------------------------------------------------------------------
    # 6. Extract results & build HourlyPlanEntry list
    # -------------------------------------------------------------------------
    hourly_plan: List[HourlyPlanEntry] = []

    for h in range(24):
        raw_grid = _clean_val(pulp.value(grid_vars[h]))
        raw_solar = _clean_val(pulp.value(solar_vars[h]))
        raw_charge = _clean_val(pulp.value(charge_vars[h]))
        raw_discharge = _clean_val(pulp.value(discharge_vars[h]))
        raw_energy = _clean_val(pulp.value(energy_vars[h]))

        # Derive mutually exclusive battery action
        if raw_charge > 1e-4:
            action = "charge"
            kwh = raw_charge
        elif raw_discharge > 1e-4:
            action = "discharge"
            kwh = raw_discharge
        else:
            action = "idle"
            kwh = 0.0

        hourly_plan.append(
            HourlyPlanEntry(
                hour=h,
                grid_kwh=raw_grid,
                solar_used_kwh=raw_solar,
                battery_action=action,
                battery_kwh=kwh,
                battery_energy_after_kwh=raw_energy,
            )
        )

    # -------------------------------------------------------------------------
    # 7. Recalculate summary metrics directly from final hourly_plan
    # -------------------------------------------------------------------------
    total_grid = sum(entry.grid_kwh for entry in hourly_plan)
    total_cost = sum(
        entry.grid_kwh * hours_data[h].tariff_bdt_per_kwh
        for h, entry in enumerate(hourly_plan)
    )
    peak_grid = max(entry.grid_kwh for entry in hourly_plan)

    total_grid = round(total_grid, 4)
    total_cost = round(total_cost, 4)
    peak_grid = round(peak_grid, 4)

    return hourly_plan, total_grid, total_cost, peak_grid
