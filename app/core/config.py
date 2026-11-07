from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@localhost:5432/gd"
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    SUPABASE_BUCKET: str = "documentos"
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    EMBEDDING_MODEL: str = "gemini-embedding-001"
    EMBEDDING_DIM: int = 768
    JWT_SECRET: str = "change-me"
    JWT_EXPIRE_MINUTES: int = 480
    APP_ENV: str = "dev"
    ALLOWED_ORIGINS: str = "http://localhost:5500,http://localhost:8000"
    INDEXER_CRON_MINUTES: int = 5
    CONFIDENCE_THRESHOLD: float = 0.85
    # Motor de automatización
    SCHEDULER_ENABLED: bool = True
    RETRY_ATTEMPTS: int = 3
    RETRY_WAIT_SECONDS: float = 1.0
    CRON_SECRET: str = ""   # token para disparar el indexador desde un cron externo

settings = Settings()
