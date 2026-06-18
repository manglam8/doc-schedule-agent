from datetime import datetime, timezone
from typing import Any
import json

from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey,
    UniqueConstraint, Enum as SAEnum, Text, event
)
from sqlalchemy.orm import DeclarativeBase, relationship
import enum


class Base(DeclarativeBase):
    pass


class AppointmentStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


class Doctor(Base):
    __tablename__ = "doctors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    specialty = Column(String(100), nullable=False, index=True)
    # JSON string: {"Mon": ["09:00", "17:00"], "Tue": ["09:00", "17:00"], ...}
    weekly_schedule = Column(Text, nullable=False)

    appointments = relationship("Appointment", back_populates="doctor", cascade="all, delete-orphan")

    def get_schedule(self) -> dict[str, list[str]]:
        return json.loads(self.weekly_schedule)

    def __repr__(self) -> str:
        return f"<Doctor id={self.id} name={self.name!r} specialty={self.specialty!r}>"


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False)
    patient_name = Column(String(150), nullable=False)
    patient_phone = Column(String(30), nullable=False)
    # Always stored as UTC; enforced by application layer
    start_time = Column(DateTime(timezone=True), nullable=False)
    status = Column(
        SAEnum(AppointmentStatus, name="appointment_status"),
        nullable=False,
        default=AppointmentStatus.CONFIRMED,
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    doctor = relationship("Doctor", back_populates="appointments")

    __table_args__ = (
        # Database-level guard against double-bookings
        UniqueConstraint("doctor_id", "start_time", name="uq_doctor_slot"),
    )

    def __repr__(self) -> str:
        return (
            f"<Appointment id={self.id} doctor_id={self.doctor_id} "
            f"patient={self.patient_name!r} start={self.start_time!r} status={self.status}>"
        )
