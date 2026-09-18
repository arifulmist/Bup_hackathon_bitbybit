"""
GridWise Energy Optimizer - Main Application.
Provides endpoints:
- GET /health: Health readiness check.
- POST /optimize-energy: 24-hour cost-optimal schedule with LLM operator directive interpretation.
"""

import asyncio
import time
from typing import Any, Dict, List
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import PORT, REQUEST_TIMEOUT_SECONDS
from app.guardrails import validate_directives
from app.llm_interpreter import interpret_notes
from app.logging_utils import logger
from app.optimizer import solve_optimization
from app.replay_validator import replay_validate_schedule
from app.schemas import (
    DirectiveInterpretation,
    OptimizeRequest,
    OptimizeResponse,
    ValidatedDirective,
)

app = FastAPI(
    title="GridWise Energy Optimizer",
    version="1.0.0",
    description="LLM-Assisted Operator Directive Interpretation & 24-Hour Dispatch Optimization",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Catches Pydantic validation errors and converts them into a clean HTTP 400 response.
    Never exposes internal Python traces.
    """
    errors = exc.errors()
    clean_messages = []
    for err in errors:
        loc = " -> ".join(str(l) for l in err.get("loc", []))
        msg = err.get("msg", "invalid")
        clean_messages.append(f"{loc}: {msg}")

    detail_msg = "; ".join(clean_messages)
    logger.warning(f"Validation error on {request.url.path}: {detail_msg}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": "invalid_request", "detail": detail_msg},
    )


@app.get("/health")
async def health_check():
    """Health check endpoint. Unconditionally returns HTTP 200 within 60s of process boot."""
    return {"status": "ok"}


@app.get("/")
async def serve_dashboard():
    """Serves the elegant single-page operator dashboard."""
    import os
    from fastapi.responses import FileResponse, HTMLResponse
    html_path = os.path.join(os.path.dirname(__file__), "..", "Frontend", "index.html")
    if os.path.exists(html_path):
        return FileResponse(html_path)
    return HTMLResponse("<h1>GridWise API Online</h1><p>Visit /docs for API documentation.</p>")



def _build_plan_summary(
    scenario_id: str,
    validated_directives: List[ValidatedDirective],
    total_grid_kwh: float,
    total_cost_bdt: float,
    peak_grid_kwh: float,
) -> str:
    """Constructs a deterministic, informative executive summary of the dispatch schedule."""
    active = [d for d in validated_directives if d.applies and d.directive_type != "no_op"]
    summary_parts = [
        f"Scenario '{scenario_id}' optimized successfully.",
        f"Total grid import: {total_grid_kwh:.2f} kWh (Peak: {peak_grid_kwh:.2f} kWh) at a total cost of {total_cost_bdt:.2f} BDT.",
    ]
    if active:
        applied_names = [f"{d.directive_type} (hours {d.hours})" for d in active]
        summary_parts.append(f"Applied {len(active)} operator directive(s): {'; '.join(applied_names)}.")
    else:
        summary_parts.append("No active operational constraints modified baseline dispatch.")

    return " ".join(summary_parts)


async def _run_pipeline(request_data: OptimizeRequest) -> OptimizeResponse:
    """Internal execution pipeline running LLM interpretation, guardrails, and LP solve."""
    # 1. LLM Interpretation of operator notes
    # Run in thread pool to avoid blocking the asyncio event loop during network I/O
    raw_candidates = await asyncio.to_thread(
        interpret_notes, request_data.operator_notes
    )

    # 2. Deterministic Guardrail Validation
    validated_directives = validate_directives(
        raw_candidates, request_data.operator_notes, request_data.battery
    )

    # 3. Mathematical Optimization (PuLP LP Solver)
    hourly_plan, total_grid_kwh, total_cost_bdt, peak_grid_kwh = await asyncio.to_thread(
        solve_optimization, request_data, validated_directives
    )

    # 4. Independent Replay Validation
    replay_validate_schedule(request_data, validated_directives, hourly_plan)

    # 5. Plan Summary Generation
    plan_summary = _build_plan_summary(
        request_data.scenario_id,
        validated_directives,
        total_grid_kwh,
        total_cost_bdt,
        peak_grid_kwh,
    )

    # 6. Format Response
    api_interpretations = [
        DirectiveInterpretation(**vd.to_api_representation())
        for vd in validated_directives
    ]

    return OptimizeResponse(
        scenario_id=request_data.scenario_id,
        directive_interpretation=api_interpretations,
        hourly_plan=hourly_plan,
        total_grid_kwh=total_grid_kwh,
        total_cost_bdt=total_cost_bdt,
        peak_grid_kwh=peak_grid_kwh,
        plan_summary=plan_summary,
    )


@app.post("/optimize-energy", response_model=OptimizeResponse)
async def optimize_energy(request: OptimizeRequest):
    """
    Main energy optimization endpoint.
    Processes 24-hour scenario + operator notes, interpreting directives and producing
    a cost-minimized hourly dispatch plan.
    """
    t_start = time.perf_counter()
    scenario_id = request.scenario_id
    logger.info(f"Received /optimize-energy request for scenario_id='{scenario_id}' with {len(request.operator_notes)} notes")

    try:
        # Enforce timeout budget
        response = await asyncio.wait_for(
            _run_pipeline(request),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        elapsed = time.perf_counter() - t_start
        logger.info(f"Scenario '{scenario_id}' successfully optimized in {elapsed:.2f}s")
        return response

    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - t_start
        logger.error(f"Scenario '{scenario_id}' exceeded timeout budget of {REQUEST_TIMEOUT_SECONDS}s (took {elapsed:.2f}s)")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "request_timeout", "message": "Optimization exceeded execution time limit"},
        )

    except Exception as exc:
        elapsed = time.perf_counter() - t_start
        # Controlled 500 error: never leak traceback or internal secrets to client
        logger.error(f"Internal error processing scenario '{scenario_id}' in {elapsed:.2f}s: {type(exc).__name__}: {str(exc)}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error", "message": "A controlled internal error occurred during optimization"},
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, reload=True)
