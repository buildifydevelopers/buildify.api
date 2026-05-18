from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # LLM provider selection: auto | gemini | cerebras
    llm_provider: str = "auto"

    # Gemini API
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # Cerebras API
    cerebras_api_key: str = ""
    cerebras_model: str = "llama3.1-8b"
    cerebras_min_interval_seconds: float = 2.5

    # Authentication
    api_auth_token: str = ""

    # Database (using PB_DATABASE_URL to avoid system DATABASE_URL conflicts)
    pb_database_url: str = "sqlite+aiosqlite:///./project_builder.db"

    # CORS
    cors_origins: str = '["*"]'

    # Rate Limiting
    rate_limit_per_minute: int = 10

    # Cache
    cache_ttl_seconds: int = 3600  # 1 hour

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
