"""
config.py
All configuration loaded from environment variables / .env file.
Never hardcode secrets — always read from here.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    APP_NAME: str = "DLA - Data Lifecycle Agent"
    VERSION:  str = "0.1.0"
    DEBUG:    bool = False

    # Database
    DATABASE_URL: str

    # Security
    SECRET_KEY: str
    ALGORITHM:  str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Anthropic
    ANTHROPIC_API_KEY: str


    # Cloud pricing
    AWS_ACCESS_KEY_ID:     str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION:            str = "us-east-1"

    # Agent behaviour
    AGENT_STANDDOWN_THRESHOLD:  float = 0.80
    AGENT_MAX_COMPUTE_LOAD_PCT: int   = 70
    AGENT_PEAK_FACTOR_LIMIT:    float = 3.0
    AGENT_TOKENS_PER_CALL:        int   = 700
    BATCH_SIZE_DEFAULT:         int   = 10
    BATCH_SIZE_MIN: int = 5
    BATCH_SIZE_MAX: int = 50
    MIN_CONVERSATION_AGE_DAYS:  int   = 30

    # Dev mode: set true to skip pre-flight standdown check
    # and always proceed to Anthropic API scoring
    SKIP_STANDDOWN_CHECK: bool = False

    # Scoring model
    SCORER_MODEL:          str = "claude-sonnet-4-20250514"
    SCORER_MAX_TOKENS:     int = 1000
    SCORER_CACHE_TTL_DAYS: int = 30   # days before a cached score is considered stale

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
