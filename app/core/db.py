from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.core.config import settings

ES_POSTGRES = settings.DATABASE_URL.startswith("postgresql")

if ES_POSTGRES:
    # prepare_threshold=None: el pooler de Supabase (modo transacción, puerto 6543) no admite
    # sentencias preparadas, que psycopg 3 usa por defecto a partir de la 5.ª ejecución.
    # Pool pequeño: el plan gratuito de Supabase limita las conexiones simultáneas.
    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=5,
                           pool_recycle=300, connect_args={"prepare_threshold": None})
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
