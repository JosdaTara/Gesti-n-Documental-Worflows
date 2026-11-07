import os

# Debe ejecutarse antes de importar la app: BD SQLite aislada, sin IA real, sin scheduler
# y sin esperas entre reintentos.
_DB = os.path.join(os.path.dirname(__file__), "test.db")
if os.path.exists(_DB):
    os.remove(_DB)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["GEMINI_API_KEY"] = ""
os.environ["SUPABASE_URL"] = ""
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["RETRY_WAIT_SECONDS"] = "0"
os.environ["RETRY_ATTEMPTS"] = "3"
os.environ["CRON_SECRET"] = "cron-test"
