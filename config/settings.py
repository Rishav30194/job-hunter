from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Required — app fails loudly on startup if any of these are missing  #
    # ------------------------------------------------------------------ #
    database_url: str
    anthropic_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str

    # ------------------------------------------------------------------ #
    # Optional — enabled only when the relevant phase is active           #
    # ------------------------------------------------------------------ #
    rapidapi_key: str = ""         # JSearch (Google for Jobs) — Phase 9; skipped if empty

    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""

    # ------------------------------------------------------------------ #
    # Tunable pipeline behaviour                                           #
    # ------------------------------------------------------------------ #
    high_match_threshold: int = 85   # score at which Telegram alert fires
    auto_apply_threshold: int = 75   # minimum score to enter apply queue
    max_queued_per_day: int = 20     # daily cap on new queued_apply jobs

    fetch_interval_hours: float = 6
    job_max_age_hours: int = 48
    zero_result_alert_threshold: int = 3

    cooldown_days: int = 30            # how long a rejecting company is skipped
    cooldown_min_rejections: int = 4   # rejections required before cooldown kicks in
                                       # (tier-1/2 companies are always exempt)

    min_salary: int = 100_000


settings = Settings()
