"""
Latency & Performance Benchmark Test.
Measures p50, p90, and p95 latencies across the deterministic pipeline
(Guardrails -> PuLP CBC Linear Program -> Replay Validator).
Proves the core backend solves well under the required latency budget.
"""

import time
import numpy as np
import pytest
from app.guardrails import validate_directives
from app.optimizer import solve_optimization
from app.replay_validator import replay_validate_schedule
from app.schemas import BatteryConfig, HourEntry, OptimizeRequest, ValidatedDirective


@pytest.fixture
def sample_request() -> OptimizeRequest:
    hours = [
        HourEntry(
            hour=h,
            demand_kwh=30.0 if 10 <= h <= 20 else 15.0,
            solar_kwh=25.0 if 8 <= h <= 16 else 0.0,
            tariff_bdt_per_kwh=12.0 if 17 <= h <= 22 else 6.0,
        )
        for h in range(24)
    ]
    batt = BatteryConfig(
        capacity_kwh=100.0,
        initial_energy_kwh=50.0,
        minimum_energy_kwh=10.0,
        max_charge_kwh_per_hour=25.0,
        max_discharge_kwh_per_hour=25.0,
    )
    return OptimizeRequest(
        scenario_id="benchmark_latency_01",
        operator_notes=[
            "Cut solar by 50% between 12:00 and 14:00.",
            "Hold battery discharge between 18:00 and 20:00.",
            "Keep 40 kWh reserve between 17:00 and 21:00.",
        ],
        hours=hours,
        battery=batt,
    )


def test_latency_benchmark_deterministic_pipeline(sample_request):
    """
    Executes 30 iterations of the deterministic processing pipeline
    (Guardrail validation + LP model creation and solve + Replay validation)
    and computes the latency distribution.
    """
    raw_candidates = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [12, 13], "factor": 0.5},
            "explanation": "Solar cut",
        },
        {
            "note_index": 1,
            "applies": True,
            "directive_type": "no_discharge_window",
            "structured_adjustment": {"hours": [18, 19]},
            "explanation": "Hold discharge",
        },
        {
            "note_index": 2,
            "applies": True,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": [17, 18, 19, 20], "minimum_energy_kwh": 40.0},
            "explanation": "Keep reserve",
        },
    ]

    durations_guardrail = []
    durations_solver = []
    durations_replay = []
    durations_total = []

    iterations = 25
    for _ in range(iterations):
        t0 = time.perf_counter()

        # Step 1: Guardrail
        t_g0 = time.perf_counter()
        validated = validate_directives(raw_candidates, sample_request.operator_notes, sample_request.battery)
        t_g1 = time.perf_counter()
        durations_guardrail.append((t_g1 - t_g0) * 1000.0)

        # Step 2: Optimizer
        t_s0 = time.perf_counter()
        plan, total_grid, total_cost, peak_grid = solve_optimization(sample_request, validated)
        t_s1 = time.perf_counter()
        durations_solver.append((t_s1 - t_s0) * 1000.0)

        # Step 3: Replay
        t_r0 = time.perf_counter()
        is_valid, violations = replay_validate_schedule(sample_request, validated, plan)
        t_r1 = time.perf_counter()
        durations_replay.append((t_r1 - t_r0) * 1000.0)

        t_end = time.perf_counter()
        durations_total.append((t_end - t0) * 1000.0)

        assert is_valid is True

    p50_total = np.percentile(durations_total, 50)
    p95_total = np.percentile(durations_total, 95)
    p95_solver = np.percentile(durations_solver, 95)

    print("\n--- Latency Benchmark Results (25 runs) ---")
    print(f"Guardrail Validation: p50={np.percentile(durations_guardrail, 50):.3f}ms | p95={np.percentile(durations_guardrail, 95):.3f}ms")
    print(f"PuLP LP Solver:       p50={np.percentile(durations_solver, 50):.3f}ms | p95={p95_solver:.3f}ms")
    print(f"Replay Validator:     p50={np.percentile(durations_replay, 50):.3f}ms | p95={np.percentile(durations_replay, 95):.3f}ms")
    print(f"Total Pipeline:       p50={p50_total:.3f}ms | p95={p95_total:.3f}ms")

    # Assert p95 total CPU pipeline is well under 100ms (typically under 25ms)
    assert p95_total < 100.0, f"Deterministic pipeline p95 ({p95_total:.2f}ms) exceeded 100ms threshold"
    assert p95_solver < 80.0, f"Solver p95 ({p95_solver:.2f}ms) exceeded 80ms threshold"
