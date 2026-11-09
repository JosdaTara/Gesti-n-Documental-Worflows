from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.core.config import settings

ES_POSTGRES = settings.DATABASE_URL.startswith("postgresql")

if ES_POSTGRES:
    # prepare_threshold=None: el pooler de Supabase (modo transacción, puerto 6543) no admite
    # sentencias preparadas, que psycopg 3 usa por defecto a partir de la 5.ª ejecución.
    # Pool pequeño: el plan gratuito de Supabase limita las conexiones simultáneas.
    # connect_timeout: si la cadena es incorrecta (p. ej. la conexión directa IPv6 de Supabase,
    # inalcanzable desde Render) falla en 10 s con un mensaje claro en vez de colgarse minutos.
    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=5,
                           pool_recycle=300, connect_args={"prepare_threshold": None, "connect_timeout": 10})
else:
    # SQLite (desarrollo y pruebas): el scheduler usa otro hilo.
    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True,
                           connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
