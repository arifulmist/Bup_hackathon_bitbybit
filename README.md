# GridWise Energy Optimizer

**BUP CSE Fest 2026 — Smart Campus Energy Optimization Challenge**  
*(LLM-Assisted Operator Directive Interpretation & Cost-Optimal Dispatch)*

---

## 🌐 Live Deployment & Service Endpoints

The GridWise service is deployed live on Render in the Singapore region:

- **Live Web Command Center:** [https://gridwise-optimizer-jdrh.onrender.com](https://gridwise-optimizer-jdrh.onrender.com)
- **Health Check Probe:** [https://gridwise-optimizer-jdrh.onrender.com/health](https://gridwise-optimizer-jdrh.onrender.com/health)
- **Interactive Swagger / OpenAPI Specs:** [https://gridwise-optimizer-jdrh.onrender.com/docs](https://gridwise-optimizer-jdrh.onrender.com/docs)
- **API Optimization Endpoint:** `POST https://gridwise-optimizer-jdrh.onrender.com/optimize-energy`
- **GitHub Repository:** [https://github.com/arifulmist/Bup_hackathon_bitbybit.git](https://github.com/arifulmist/Bup_hackathon_bitbybit.git)

---

## 1. Problem & Solution Summary

GridWise is an enterprise-grade energy optimization platform designed for smart university campuses. It balances hourly electricity demands across 24-hour dispatch horizons while interpreting free-form natural language directives submitted by grid operators. 

Operator notes—ranging from cloudy weather alerts and auditorium reserve buffers to transformer charging halts or contract grid import limits—are interpreted by an advanced generative Large Language Model into structured candidate adjustments. Every candidate directive is vetted by a zero-trust deterministic Python guardrail validator, solved to mathematical global optimality via Linear Programming (LP), and verified by an independent replay verification engine before response delivery.

```
Energy Scenario (24h) + Operator Notes (1-3)
                   │
                   ▼
       ┌───────────────────────┐
       │    LLM Interpreter    │  (Generative LLM with strict JSON mode)
       └───────────┬───────────┘
                   │  Raw candidate directives
                   ▼
       ┌───────────────────────┐
       │  Guardrail Validator  │  (Zero-trust deterministic verification & bounds coercion)
       └───────────┬───────────┘
                   │  Sanitized ValidatedDirectives
                   ▼
       ┌───────────────────────┐
       │   Math LP Optimizer   │  (PuLP + COIN-OR CBC Continuous Linear Programming)
       └───────────┬───────────┘
                   │  24-hour cost-minimized hourly dispatch plan
                   ▼
       ┌───────────────────────┐
       │   Replay Validator    │  (Independent constraint & energy balance verification)
       └───────────┬───────────┘
                   │
                   ▼
             API Response          (Exact challenge contract: directives + hourly plan + totals)
```

---

## 2. Operator Notes & Supported Directives Specification

Each optimization scenario contains **1 to 3 operator notes**. Notes can affect the current schedule or serve as realistic distractors that must be safely ignored (`no_op`). Hidden test cases may express the same directive using varied wording or colloquial expressions.

### 2.1 All 6 Supported Canonical Directive Types

GridWise natively recognizes, parses, validates, and mathematically models all 6 canonical directive types defined in Section 4.1 of the challenge specification:

| # | Directive Type | Semantic Meaning | Required `structured_adjustment` Schema | Canonical Phrasing Example |
|:---:|:---|:---|:---|:---|
| **1** | `solar_reduction` | Reduce usable solar generation during specific hours due to cloud cover, haze, or panel maintenance. | `{"hours": [int, ...], "factor": float}`<br>*(factor $\in [0.0, 1.0]$ representing fraction remaining)* | *"Solar output will drop to about 20% from 1 PM to 3 PM."* $\rightarrow$ `hours: [13, 14], factor: 0.2` |
| **2** | `minimum_battery_reserve` | Maintain battery stored energy at or above a required safety threshold during specified hours. | `{"hours": [int, ...], "minimum_energy_kwh": float}`<br>*(bounded by $0.0 \le \text{min} \le \text{capacity}$)* | *"Keep at least 35 kWh in reserve from 6 PM until 9 PM."* $\rightarrow$ `hours: [18, 19, 20], minimum_energy_kwh: 35.0` |
| **3** | `no_charge_window` | Battery charging is completely unavailable or prohibited during specific hours. | `{"hours": [int, ...]}` | *"Do not charge the battery between 2 PM and 4 PM."* $\rightarrow$ `hours: [14, 15]` |
| **4** | `no_discharge_window` | Battery discharging is completely unavailable or prohibited during specific hours. | `{"hours": [int, ...]}` | *"Hold battery discharging between 7 AM and 9 AM."* $\rightarrow$ `hours: [7, 8]` |
| **5** | `max_grid_window` | Grid import may not exceed a stated maximum power cap during specific hours. | `{"hours": [int, ...], "max_grid_kwh": float}`<br>*(bounded by $\ge 0.0$)* | *"Grid import capped at 20 kWh from 18:00 to 22:00."* $\rightarrow$ `hours: [18, 19, 20, 21], max_grid_kwh: 20.0` |
| **6** | `no_op` | Realistic distractor note that does not affect the current 24-hour energy dispatch schedule. | `null`<br>*(with `applies: false`)* | *"The cafeteria menu changes tomorrow."* / *"Campus looks beautiful today!"* $\rightarrow$ `null` |

### 2.2 Hour Window Interpretation Rules
- **Start-Inclusive / End-Exclusive**: Windows specified as *"from 1 PM to 3 PM"* or *"between 13:00 and 15:00"* cover hour indices `[13, 14]` (the 13:00-14:00 and 14:00-15:00 dispatch blocks).
- **Until / Through Semantics**: *"until 9 PM"* denotes ending at the start of hour 21 (`[18, 19, 20]`).
- **Reduction vs. Remaining Fraction**: *"drop by 80%"* correctly calculates the remaining factor as $1.0 - 0.80 = 0.20$.

---

## 3. Web Command Center & Visual Features

The application serves a state-of-the-art **BUP Smart Campus Energy Command Center** at `/` referencing the official visual identity of [Bangladesh University of Professionals (BUP)](https://bup.edu.bd/):

### 🏢 BUP Official Visual Identity
- **University Color Palette**: Deep Navy (`#0B1B3D` / `#253B80`), Sky Blue (`#179BD7`), Sustainable Green (`#01803D` / `#10B981`), and Gold (`#FFB606`).
- **Official Branding**: Embedded BUP emblem, university seal, and motto *"Excellence Through Knowledge"*.
- **Live Campus Telemetry**: Real-time Dhaka Weather Widget (`⛅ Dhaka 28°C Partly Cloudy`) and dynamic digital clock.

### 🌐 Interactive 3D WebGL Campus Digital Twin (Three.js)
- **Isometric 3D Campus Models**:
  - Central Academic Complex, Multi-purpose Hall, and Faculty Blocks with illuminated cyan windows.
  - Rooftop Photovoltaic Solar Array with metallic framing and sunlight shimmer.
  - BESS Battery Storage Container with glowing State-of-Charge LED bars.
  - High-Voltage Grid Substation with steel transmission tower.
- **Dynamic Energy Particle Streams**: 60 animated glowing light particles traveling along 3 distinct energy corridors in real-time:
  1. Gold particles: Solar Rooftop $\rightarrow$ Battery Storage
  2. Green particles: Battery Storage $\rightarrow$ Central Campus Load
  3. Blue particles: Grid Substation $\rightarrow$ Central Campus Load
- **OrbitControls & Camera Presets**: Interactive mouse rotation, scroll zoom, and quick camera views (`Isometric`, `Solar Rooftop`, `Battery BESS`).

### ⚡ Animated Colorful SVG Energy Flow Diagram
- High-fidelity vector schematic connecting **Solar Panels (12.4 MW)** $\rightarrow$ **Battery BESS (1.5 MWh / 62% SoC)** $\rightarrow$ **Campus Load (68.4 MW)** $\leftarrow$ **Grid Substation (38.2 MW)**.
- Real-time animated pulse conduits indicating active energy direction.
- Invariant balance indicator: `Grid + SolarUsed ± Battery == Demand`.

### 🎛️ Supported Directives Gallery & Quick Injector
- Dedicated visual cards for **all 6 canonical directive types** with schema specifications and authentic challenge examples.
- Single-click **"⚡ Load into Note"** buttons to immediately inject directives into the active prompt.
- **One-Click Presets**:
  - *Solar + Reserve*: `solar_reduction` + `minimum_battery_reserve` + `no_op` distractor.
  - *Charge/Discharge Inhibit*: `no_charge_window` + `no_discharge_window`.
  - *Max Grid Window*: `max_grid_window` capping peak grid import.
  - *Distractor Test*: Submits 3 distractor sentences to verify zero-false-positive `no_op` filtering.
  - *Test Multi-Directive Scenario*: Tests compound operational directives simultaneously.

### 📊 Comprehensive Metrics, Charts & Data Table
- **5 High-Level KPI Cards**: Total Demand, Solar Generation, Battery SoC (with neutrality check), Grid Import, and Total Cost.
- **Dual Visualizations**:
  - 24-Hour Dispatch Curve Chart (Chart.js area curves for demand, solar, grid, and battery SoC).
  - Energy Source Breakdown Donut Chart (Proportional supply mix with centered total).
- **24-Hour Schedule Table**: Hour-by-hour dispatch decisions, color-coded battery action badges (`▲ charge`, `▼ discharge`, `— idle`), hourly cost in BDT, and invariant verification checkmarks (`✓`).
- **Data Export & Inspection**:
  - **Export CSV**: One-click download of the complete 24-hour schedule.
  - **View JSON**: Modal dialog displaying the exact challenge JSON contract response with a "Copy JSON" button.
- **Audit Checklist**: Exhaustive validation checklist confirming all 6 canonical directive types.

---

## 4. Architecture & Pipeline Breakdown

Every request to `POST /optimize-energy` passes through these discrete, non-overlapping pipeline stages:

1. **Request Schema Validation** (`app/schemas.py`):  
   Enforces strict Pydantic v2 schemas: exactly 24 sequential hourly entries covering hours 0 to 23, 1 to 3 non-empty operator notes, physical battery limits ($\text{initial} \le \text{capacity}$, $\text{minimum} \le \text{capacity}$), and rejection of unrecognized attributes (`extra="forbid"`). Structurally invalid requests return a clean HTTP 400 response without leaking internal stack traces.

2. **LLM Operator-Note Interpretation** (`app/llm_interpreter.py`):  
   Calls a generative LLM once per request with all notes batched together to preserve $0..N-1$ indexing and minimize latency. Uses JSON mode and a specialized domain prompt covering all 6 directive types, start-inclusive/end-exclusive hour windows, solar fraction remaining math, and few-shot examples. In case of provider timeouts, 429/5xx errors, or malformed outputs, it fails safely to deterministic `no_op` candidates.

3. **Deterministic Guardrail Validation** (`app/guardrails.py`):  
   Pure deterministic Python (zero LLM calls). Enforces 1:1 note index mapping, validates supported directive types, sanitizes and sorts `hours` into unique ascending integers within $[0, 23]$, clamps minor floating noise on solar factors ($[-0.01, 1.01] \rightarrow [0.0, 1.0]$) while rejecting gross errors, validates non-negative grid caps, and ensures battery reserve targets never exceed physical capacity. Coerces non-conforming directives to `no_op` while isolating failures per note.

4. **Mathematical Optimization Engine** (`app/optimizer.py`):  
   Constructs a 24-hour Continuous Linear Program solved via PuLP and the COIN-OR CBC solver. Applies pre-computed effective solar generation, dynamic reserve floors, and rate limits. Guarantees hourly energy balance ($\text{grid} + \text{solar} + \text{discharge} = \text{demand} + \text{charge}$), battery state transitions ($E_h = E_{h-1} + \text{charge} - \text{discharge}$), and end-of-day battery neutrality ($E_{23} = E_{\text{initial}}$). A $10^{-6}$ BDT throughput regularization mathematically guarantees that simultaneous charging and discharging never occurs in the same hour.

5. **Independent Replay Validation** (`app/replay_validator.py`):  
   Independent from-scratch re-check verifying that all physical bounds, energy balance tolerances ($\pm 0.01\text{ kWh}$), battery transitions, and active directive restrictions are strictly respected in the final schedule.

6. **Executive Summary & Serialization** (`app/main.py`):  
   Recalculates totals directly from the validated `hourly_plan` and formats the final `OptimizeResponse`.

---

## 5. Model & Provider Disclosure (LLM Mandate)

- **Provider**: Hosted OpenAI-compatible API (`https://router.bynara.id/v1` or `https://api.openai.com/v1`)
- **Default Model**: `agnes-2.5-flash` / `gpt-4o-mini`
- **Confirmation of Mandate Compliance**: **A generative Large Language Model directly and actively parses and interprets the `operator_notes` to produce the candidate `directive_interpretation` structured array.** The LLM is NOT used solely for generating `plan_summary` or cosmetic text; nor is keyword/regex matching used as the interpretation mechanism. The LLM interpretation output is subsequently validated and sanitized by deterministic guardrails before entering the mathematical optimizer.

---

## 6. Required Environment Variables

Configuration is loaded from environment variables (or a local `.env` file). No secrets are hardcoded:

| Variable Name | Required | Default | Description |
|---|---|---|---|
| `LLM_PROVIDER` | Optional | `openai` | Name of LLM provider (`openai`, `bynara`, etc.) |
| `LLM_API_KEY` | **Yes** | — | API key for the generative language model provider |
| `LLM_BASE_URL` | Optional | *(empty)* | Custom base URL for OpenAI-compatible proxies/routers |
| `LLM_MODEL` | Optional | `gpt-4o-mini` | Model name (e.g. `agnes-2.5-flash`, `gpt-4o-mini`) |
| `PORT` | Optional | `8000` | Port for the HTTP API server to listen on |
| `REQUEST_TIMEOUT_SECONDS` | Optional | `25.0` | Timeout per request (leaves headroom under 30s hard limit) |
| `MAX_LLM_RETRIES` | Optional | `1` | Retry count for LLM calls before falling back to safe `no_op` |
| `SUPABASE_URL` | Optional | — | URL for optional Supabase persistence/telemetry |
| `SUPABASE_ANON_KEY` | Optional | — | Anon publishable key for optional database telemetry |

---

## 7. Local Quickstart (Zero-Assistance Guide)

### Step 1: Clone Repository & Enter Directory
```bash
git clone https://github.com/arifulmist/Bup_hackathon_bitbybit.git
cd Bup_hackathon_bitbybit
```

### Step 2: Create & Activate Virtual Environment
```bash
python -m venv .venv
# On Linux/macOS:
source .venv/bin/activate
# On Windows (PowerShell):
.venv\Scripts\Activate.ps1
```

### Step 3: Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 4: Configure Environment
```bash
cp .env.example .env
```
Edit `.env`:
```ini
LLM_PROVIDER=openai
LLM_API_KEY=your_real_api_key_here
LLM_BASE_URL=https://router.bynara.id/v1
LLM_MODEL=agnes-2.5-flash
PORT=8000
```

### Step 5: Start the API Service
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Step 6: Verify Health Endpoint
```bash
curl http://localhost:8000/health
```
Expected output:
```json
{"status":"ok"}
```

---

## 8. Working `curl` Example (`POST /optimize-energy`)

Execute this command to run a full 24-hour optimization scenario:

```bash
curl -X POST https://gridwise-optimizer-jdrh.onrender.com/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "campus_scenario_sample_01",
    "operator_notes": [
      "Solar output will drop to about 20% from 1 PM to 3 PM.",
      "Keep at least 35 kWh in reserve from 6 PM until 9 PM.",
      "The cafeteria menu changes tomorrow."
    ],
    "hours": [
      {"hour": 0, "demand_kwh": 18.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 6.0},
      {"hour": 1, "demand_kwh": 16.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 6.0},
      {"hour": 2, "demand_kwh": 15.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 6.0},
      {"hour": 3, "demand_kwh": 15.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 6.0},
      {"hour": 4, "demand_kwh": 16.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 6.0},
      {"hour": 5, "demand_kwh": 19.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 6.0},
      {"hour": 6, "demand_kwh": 22.0, "solar_kwh": 2.0, "tariff_bdt_per_kwh": 6.0},
      {"hour": 7, "demand_kwh": 28.0, "solar_kwh": 8.0, "tariff_bdt_per_kwh": 8.0},
      {"hour": 8, "demand_kwh": 35.0, "solar_kwh": 18.0, "tariff_bdt_per_kwh": 8.0},
      {"hour": 9, "demand_kwh": 42.0, "solar_kwh": 25.0, "tariff_bdt_per_kwh": 8.0},
      {"hour": 10, "demand_kwh": 48.0, "solar_kwh": 32.0, "tariff_bdt_per_kwh": 8.0},
      {"hour": 11, "demand_kwh": 50.0, "solar_kwh": 38.0, "tariff_bdt_per_kwh": 8.0},
      {"hour": 12, "demand_kwh": 52.0, "solar_kwh": 40.0, "tariff_bdt_per_kwh": 8.0},
      {"hour": 13, "demand_kwh": 49.0, "solar_kwh": 38.0, "tariff_bdt_per_kwh": 8.0},
      {"hour": 14, "demand_kwh": 46.0, "solar_kwh": 32.0, "tariff_bdt_per_kwh": 8.0},
      {"hour": 15, "demand_kwh": 42.0, "solar_kwh": 24.0, "tariff_bdt_per_kwh": 8.0},
      {"hour": 16, "demand_kwh": 38.0, "solar_kwh": 12.0, "tariff_bdt_per_kwh": 8.0},
      {"hour": 17, "demand_kwh": 45.0, "solar_kwh": 4.0, "tariff_bdt_per_kwh": 12.0},
      {"hour": 18, "demand_kwh": 55.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 12.0},
      {"hour": 19, "demand_kwh": 52.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 12.0},
      {"hour": 20, "demand_kwh": 46.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 12.0},
      {"hour": 21, "demand_kwh": 38.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 12.0},
      {"hour": 22, "demand_kwh": 30.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 12.0},
      {"hour": 23, "demand_kwh": 22.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 6.0}
    ],
    "battery": {
      "capacity_kwh": 100.0,
      "initial_energy_kwh": 50.0,
      "minimum_energy_kwh": 10.0,
      "max_charge_kwh_per_hour": 25.0,
      "max_discharge_kwh_per_hour": 25.0
    }
  }'
```

---

## 9. Automated Testing & Verification

To run the complete automated test suite (40+ unit, integration, contract, and benchmark latency tests):
```bash
pytest -v
```

All 40 tests consistently pass in ~23 seconds with 100% verification across:
- `test_api_contract.py`: Pydantic v2 schemas and exact contract fields.
- `test_guardrails.py`: Deterministic bounds checking, clamping, and error isolation.
- `test_llm_interpreter.py`: Live generative model integration and failure fallbacks.
- `test_optimizer.py`: PuLP CBC optimality, state transitions, neutrality, and throughput regularization.
- `test_benchmark_latency.py`: End-to-end performance under the 30-second budget.

---

## 10. Docker Deployment & Container Image

### Build & Run Locally
```bash
docker build -t gridwise:latest .
docker run -d -p 8000:8000 --env-file .env gridwise:latest
```

### Submitted Fallback Image Reference
```bash
docker pull zawadrafid/gridwise:latest
```

---

## 11. Security & Hygiene Assurance

- **Zero Hardcoded Secrets**: No API keys, passwords, or tokens are committed to this repository. `.env` is verified in `.gitignore`.
- **Log Masking**: `app/logging_utils.py` contains regex sanitizers redacting Bearer tokens and OpenAI-style keys (`sk-...`) from all logs.
- **Non-Root Container**: The production Dockerfile executes under an unprivileged user (`appuser`).
