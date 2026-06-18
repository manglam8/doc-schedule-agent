import json
import logging
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

from config import get_settings
from models import Base, Doctor, Appointment, AppointmentStatus

logger = logging.getLogger(__name__)

settings = get_settings()

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},  # required for SQLite + FastAPI
    echo=False,
)


# Enable WAL mode on every new SQLite connection for better concurrency
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, _connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session and guarantees cleanup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables if they don't exist yet."""
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created/verified.")


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

_DOCTORS_SEED = [
    {
        "name": "Dr. Sarah Chen",
        "specialty": "Cardiologist",
        # Mon–Fri 09:00–17:00
        "weekly_schedule": json.dumps({
            "Mon": ["09:00", "17:00"],
            "Tue": ["09:00", "17:00"],
            "Wed": ["09:00", "17:00"],
            "Thu": ["09:00", "17:00"],
            "Fri": ["09:00", "17:00"],
        }),
    },
    {
        "name": "Dr. Marcus Adams",
        "specialty": "Dermatologist",
        # Only Tue & Thu 10:00–16:00
        "weekly_schedule": json.dumps({
            "Tue": ["10:00", "16:00"],
            "Thu": ["10:00", "16:00"],
        }),
    },
    {
        "name": "Dr. Priya Patel",
        "specialty": "General Practitioner",
        # Mon, Wed, Fri 08:00–15:00
        "weekly_schedule": json.dumps({
            "Mon": ["08:00", "15:00"],
            "Wed": ["08:00", "15:00"],
            "Fri": ["08:00", "15:00"],
        }),
    },
]


def seed_db() -> None:
    """Populate the DB with mock doctors if they haven't been inserted yet."""
    db = SessionLocal()
    try:
        if db.query(Doctor).count() > 0:
            logger.info("Seed data already present; skipping.")
            return

        for doc_data in _DOCTORS_SEED:
            db.add(Doctor(**doc_data))

        db.commit()
        logger.info("Seeded %d doctors into the database.", len(_DOCTORS_SEED))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def reset_db() -> None:
    """Drop and recreate all tables (test helper — do NOT call in production)."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
