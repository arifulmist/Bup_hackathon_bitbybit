"""
Unit tests for the Linear Programming Optimization Engine.
Verifies LP correctness, energy balance, rate limits, end-of-day neutrality,
and every active directive constraint across 24 hours.
"""

import pytest
from app.optimizer import solve_optimization
from app.schemas import BatteryConfig, HourEntry, OptimizeRequest, ValidatedDirective


def make_test_request(
    battery_override: dict = None,
    solar_override: dict = None,
    demand_override: dict = None,
    tariff_override: dict = None,
) -> OptimizeRequest:
    """Helper to build a realistic 24-hour test request."""
    batt_data = {
        "capacity_kwh": 100.0,
        "initial_energy_kwh": 50.0,
        "minimum_energy_kwh": 10.0,
        "max_charge_kwh_per_hour": 25.0,
        "max_discharge_kwh_per_hour": 25.0,
    }
    if battery_override:
        batt_data.update(battery_override)

    hours = []
    for h in range(24):
        # Base daily demand profile: higher in afternoon/evening
        demand = 30.0 if 10 <= h <= 20 else 15.0
        if demand_override and h in demand_override:
            demand = demand_override[h]

        # Base solar: active 07:00 to 17:00 peaking at noon
        solar = 0.0
        if 8 <= h <= 16:
            solar = 25.0 - abs(h - 12) * 3.0
        if solar_override and h in solar_override:
            solar = solar_override[h]

        # Tariff: peak 17:00 to 22:00, off-peak at night
        tariff = 12.0 if 17 <= h <= 22 else (5.0 if h <= 6 or h == 23 else 8.0)
        if tariff_override and h in tariff_override:
            tariff = tariff_override[h]

        hours.append(
            HourEntry(
                hour=h,
                demand_kwh=float(demand),
                solar_kwh=float(solar),
                tariff_bdt_per_kwh=float(tariff),
            )
        )

    return OptimizeRequest(
        scenario_id="test_opt_scenario",
        operator_notes=["Routine optimization"],
        hours=hours,
        battery=BatteryConfig(**batt_data),
    )


def assert_schedule_invariants(request: OptimizeRequest, plan: list, directives: list):
    """
    Independent invariant verification: walks through the 24-hour plan
    and checks energy balance, battery state transition, rate limits, and bounds.
    """
    battery = request.battery
    e_prev = battery.initial_energy_kwh

    # Map directives
    solar_factors = {h: 1.0 for h in range(24)}
    min_reserves = {h: battery.minimum_energy_kwh for h in range(24)}
    grid_caps = {h: float("inf") for h in range(24)}
    no_charge = set()
    no_discharge = set()

    for d in directives:
        if not d.applies or d.directive_type == "no_op":
            continue
        if d.directive_type == "solar_reduction":
            for h in d.hours:
                solar_factors[h] = d.factor
        elif d.directive_type == "minimum_battery_reserve":
            for h in d.hours:
                min_reserves[h] = max(min_reserves[h], d.minimum_energy_kwh)
        elif d.directive_type == "no_charge_window":
            no_charge.update(d.hours)
        elif d.directive_type == "no_discharge_window":
            no_discharge.update(d.hours)
        elif d.directive_type == "max_grid_window":
            for h in d.hours:
                grid_caps[h] = min(grid_caps[h], d.max_grid_kwh)

    for h, entry in enumerate(plan):
        hour_data = request.hours[h]
        effective_solar = hour_data.solar_kwh * solar_factors[h]

        # 1. Non-negativity
        assert entry.grid_kwh >= -1e-5
        assert entry.solar_used_kwh >= -1e-5
        assert entry.battery_kwh >= -1e-5

        # 2. Solar bound
        assert entry.solar_used_kwh <= effective_solar + 1e-4

        # 3. Rate limits
        if entry.battery_action == "charge":
            assert entry.battery_kwh <= battery.max_charge_kwh_per_hour + 1e-4
            assert h not in no_charge
            e_expected = e_prev + entry.battery_kwh
        elif entry.battery_action == "discharge":
            assert entry.battery_kwh <= battery.max_discharge_kwh_per_hour + 1e-4
            assert h not in no_discharge
            e_expected = e_prev - entry.battery_kwh
        else:
            assert entry.battery_kwh == 0.0
            e_expected = e_prev

        # 4. Battery transition
        assert abs(entry.battery_energy_after_kwh - e_expected) < 0.01

        # 5. Battery bounds
        assert entry.battery_energy_after_kwh >= min_reserves[h] - 0.01
        assert entry.battery_energy_after_kwh <= battery.capacity_kwh + 0.01

        # 6. Grid cap
        assert entry.grid_kwh <= grid_caps[h] + 0.01

        # 7. Energy balance: grid + solar_used + discharge = demand + charge
        charge_val = entry.battery_kwh if entry.battery_action == "charge" else 0.0
        discharge_val = entry.battery_kwh if entry.battery_action == "discharge" else 0.0
        generation = entry.grid_kwh + entry.solar_used_kwh + discharge_val
        consumption = hour_data.demand_kwh + charge_val
        assert abs(generation - consumption) < 0.01, f"Hour {h}: gen={generation}, cons={consumption}"

        e_prev = entry.battery_energy_after_kwh

    # 8. End-of-day neutrality
    assert abs(plan[23].battery_energy_after_kwh - battery.initial_energy_kwh) < 0.01


def test_baseline_scenario_solves_optimal():
    """Confirms a baseline scenario without directives satisfies all rules."""
    req = make_test_request()
    plan, total_grid, total_cost, peak_grid = solve_optimization(req, [])

    assert len(plan) == 24
    assert total_grid > 0
    assert total_cost > 0
    assert peak_grid > 0
    assert_schedule_invariants(req, plan, [])


def test_solar_reduction_directive():
    """Confirms solar_reduction limits solar usage during specified hours."""
    req = make_test_request()
    # 80% reduction (factor 0.20) during hours 12 and 13
    directive = ValidatedDirective(
        note_index=0,
        directive_type="solar_reduction",
        applies=True,
        hours=[12, 13],
        factor=0.20,
        explanation="80% solar reduction",
    )
    plan, _, _, _ = solve_optimization(req, [directive])

    assert_schedule_invariants(req, plan, [directive])
    for h in [12, 13]:
        max_allowed = req.hours[h].solar_kwh * 0.20
        assert plan[h].solar_used_kwh <= max_allowed + 1e-4


def test_minimum_battery_reserve_directive():
    """Confirms minimum_battery_reserve raises the battery floor during target hours."""
    req = make_test_request()
    # Normal min reserve is 10 kWh; raise to 60 kWh between 18:00 and 21:00
    directive = ValidatedDirective(
        note_index=0,
        directive_type="minimum_battery_reserve",
        applies=True,
        hours=[18, 19, 20],
        minimum_energy_kwh=60.0,
        explanation="Keep 60 kWh reserve",
    )
    plan, _, _, _ = solve_optimization(req, [directive])

    assert_schedule_invariants(req, plan, [directive])
    for h in [18, 19, 20]:
        assert plan[h].battery_energy_after_kwh >= 60.0 - 0.01


def test_no_charge_window_directive():
    """Confirms no_charge_window forces battery charging to 0 in specified hours."""
    req = make_test_request()
    # Forbid charging between 10:00 and 14:00 (when solar would normally charge battery)
    directive = ValidatedDirective(
        note_index=0,
        directive_type="no_charge_window",
        applies=True,
        hours=[10, 11, 12, 13],
        explanation="No charging allowed",
    )
    plan, _, _, _ = solve_optimization(req, [directive])

    assert_schedule_invariants(req, plan, [directive])
    for h in [10, 11, 12, 13]:
        assert plan[h].battery_action != "charge"
        if plan[h].battery_action == "idle":
            assert plan[h].battery_kwh == 0.0


def test_no_discharge_window_directive():
    """Confirms no_discharge_window forces battery discharging to 0 in specified hours."""
    req = make_test_request()
    # Forbid discharging during high-tariff evening hours 17..20
    directive = ValidatedDirective(
        note_index=0,
        directive_type="no_discharge_window",
        applies=True,
        hours=[17, 18, 19, 20],
        explanation="No discharge window",
    )
    plan, _, _, _ = solve_optimization(req, [directive])

    assert_schedule_invariants(req, plan, [directive])
    for h in [17, 18, 19, 20]:
        assert plan[h].battery_action != "discharge"


def test_max_grid_window_directive():
    """Confirms max_grid_window strictly caps grid import in specified hours."""
    req = make_test_request()
    # Demand during hours 14..16 is 30 kWh; cap grid at 10 kWh
    directive = ValidatedDirective(
        note_index=0,
        directive_type="max_grid_window",
        applies=True,
        hours=[14, 15],
        max_grid_kwh=10.0,
        explanation="Cap grid import to 10 kWh",
    )
    plan, _, _, _ = solve_optimization(req, [directive])

    assert_schedule_invariants(req, plan, [directive])
    for h in [14, 15]:
        assert plan[h].grid_kwh <= 10.0 + 0.01
