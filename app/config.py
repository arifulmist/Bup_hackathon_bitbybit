"""
Configuration loader for GridWise Energy Optimizer.
Loads all settings from environment variables with sensible defaults.
No secrets are hard-coded.
"""

import os
from dotenv import load_dotenv

# Load local .env if present
load_dotenv()

LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai").strip().lower()
LLM_API_KEY: str = os.getenv("LLM_API_KEY", "").strip()
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "").strip()
LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini").strip()

PORT: int = int(os.getenv("PORT", "8000"))
REQUEST_TIMEOUT_SECONDS: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "25.0"))
MAX_LLM_RETRIES: int = int(os.getenv("MAX_LLM_RETRIES", "1"))

# Optional Supabase config for enhanced dashboard & telemetry
SUPABASE_URL: str = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", "").strip()

# Internal flags
REPLAY_STRICT_FAIL: bool = os.getenv("REPLAY_STRICT_FAIL", "false").strip().lower() in ("true", "1", "yes")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").strip().upper()
