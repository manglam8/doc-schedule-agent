"""
Shared pytest fixtures.

Uses an in-memory SQLite database so tests are fully isolated and fast.
"""
import json
import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Doctor, Appointment, AppointmentStatus


TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture(scope="function")
def engine():
    _engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=_engine)
    yield _engine
    Base.metadata.drop_all(bind=_engine)
    _engine.dispose()


@pytest.fixture(scope="function")
def db(engine, monkeypatch):
    """
    Yield a real SQLAlchemy Session backed by the in-memory DB,
    and patch tools.SessionLocal so that tool functions use this DB.
    """
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestSession()

    # tools.py imports SessionLocal directly, so patch it there
    import tools
    monkeypatch.setattr(tools, "SessionLocal", TestSession)

    yield session
    session.close()


@pytest.fixture(scope="function")
def seeded_db(db):
    """DB session pre-populated with the three clinic doctors."""
    doctors = [
        Doctor(
            name="Dr. Sarah Chen",
            specialty="Cardiologist",
            weekly_schedule=json.dumps({
                "Mon": ["09:00", "17:00"],
                "Tue": ["09:00", "17:00"],
                "Wed": ["09:00", "17:00"],
                "Thu": ["09:00", "17:00"],
                "Fri": ["09:00", "17:00"],
            }),
        ),
        Doctor(
            name="Dr. Marcus Adams",
            specialty="Dermatologist",
            weekly_schedule=json.dumps({
                "Tue": ["10:00", "16:00"],
                "Thu": ["10:00", "16:00"],
            }),
        ),
        Doctor(
            name="Dr. Priya Patel",
            specialty="General Practitioner",
            weekly_schedule=json.dumps({
                "Mon": ["08:00", "15:00"],
                "Wed": ["08:00", "15:00"],
                "Fri": ["08:00", "15:00"],
            }),
        ),
    ]
    db.add_all(doctors)
    db.commit()
    return db
