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
    redis_url: str = "redis://redis:6379/0"

    indeed_email: str = ""
    indeed_password: str = ""
    resume_path: str = ""          # absolute local path to PDF resume for upload

    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""

    # ------------------------------------------------------------------ #
    # Tunable pipeline behaviour                                           #
    # ------------------------------------------------------------------ #
    human_review_threshold: int = 85
    auto_apply_threshold: int = 75
    max_auto_applies_per_day: int = 20
    max_human_review_per_run: int = 5

    fetch_interval_hours: float = 6
    job_max_age_hours: int = 48
    zero_result_alert_threshold: int = 3

    min_salary: int = 100_000

    playwright_headless: bool = True


settings = Settings()
