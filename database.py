"""
Botk2-IA — Configuración de base de datos
==========================================
- LOCAL:      SQLite  (automático si no hay DATABASE_URL)
- PRODUCCIÓN: PostgreSQL en Railway (via variable de entorno DATABASE_URL)
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "")

if DATABASE_URL:
    # Railway / Heroku entregan "postgres://..." pero SQLAlchemy necesita "postgresql://"
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
else:
    # Desarrollo local con SQLite
    _DB_PATH = os.environ.get("DB_PATH", "./botk2ia.db")
    engine = create_engine(
        f"sqlite:///{_DB_PATH}",
        connect_args={"check_same_thread": False},
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
