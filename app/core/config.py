from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@localhost:5432/gd"
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    SUPABASE_BUCKET: str = "documentos"
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.8-flash"
    # Segundo modelo con la misma clave: más liviano y casi siempre disponible cuando el
    # principal responde "high demand" (503) o se demora (504). Vacío = desactivado.
    GEMINI_FALLBACK_MODEL: str = "gemini-3.5-flash-lite"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    OLLAMA_BASE_URL: str = ""          # p. ej. http://localhost:11434 (vacío = desactivado)
    OLLAMA_MODEL: str = "llama3.1"
    LLM_PROVIDERS: str = "gemini,gemini_lite,groq,ollama"   # orden del plan B
    CHAT_HISTORY_TURNS: int = 10       # mensajes previos que recuerda el chatbot
    EMBEDDING_MODEL: str = "gemini-embedding-001"
    EMBEDDING_DIM: int = 768
    JWT_SECRET: str = "change-me"
    JWT_EXPIRE_MINUTES: int = 480
    APP_ENV: str = "dev"
    ALLOWED_ORIGINS: str = "http://localhost:5500,http://localhost:8000"
    INDEXER_CRON_MINUTES: int = 5
    CONFIDENCE_THRESHOLD: float = 0.85
    # Módulo de visión
    VISION_PROVIDERS: str = "gemini,gemini_lite,groq,ollama"
    GROQ_VISION_MODEL: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    OLLAMA_VISION_MODEL: str = "llama3.2-vision"
    # local: solo modelos locales | anonimizado: se tapan datos personales antes de enviar
    # a servicios externos (requiere Tesseract) | externo: se envía la imagen tal cual
    # (solo con autorización registrada del usuario)
    VISION_PRIVACY_MODE: str = "anonimizado"
    VISION_MAX_MB: int = 10
    # Flujo procesar_factura (reglas de control de compras)
    MONTO_APROBACION: float = 5_000_000      # COP: desde este total la factura requiere aprobación
    MONTO_ATIPICO_FACTOR: float = 3.0        # total > factor × promedio del proveedor
    # Seguridad
    ADMIN_PASSWORD: str = ""                 # contraseña inicial del admin (obligatoria en producción)
    # Motor de automatización
    SCHEDULER_ENABLED: bool = True
    RETRY_ATTEMPTS: int = 3
    RETRY_WAIT_SECONDS: float = 1.0
    CRON_SECRET: str = ""   # token para disparar el indexador desde un cron externo

settings = Settings()
