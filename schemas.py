from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator
import re


# ---------------------------------------------------------------------------
# Doctor schemas
# ---------------------------------------------------------------------------

class DoctorBase(BaseModel):
    name: str
    specialty: str


class DoctorOut(DoctorBase):
    id: int
    weekly_schedule: dict[str, list[str]]

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Appointment schemas
# ---------------------------------------------------------------------------

class AppointmentCreate(BaseModel):
    doctor_id: int = Field(..., gt=0)
    patient_name: str = Field(..., min_length=2, max_length=150)
    patient_phone: str = Field(..., min_length=7, max_length=30)
    date: str = Field(..., description="ISO date string YYYY-MM-DD")
    time: str = Field(..., description="HH:MM (24-hour)")

    @field_validator("date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("date must be in YYYY-MM-DD format")
        return v

    @field_validator("time")
    @classmethod
    def validate_time(cls, v: str) -> str:
        if not re.match(r"^\d{2}:\d{2}$", v):
            raise ValueError("time must be in HH:MM format")
        h, m = int(v[:2]), int(v[3:])
        if not (0 <= h <= 23 and m in (0, 30)):
            raise ValueError("time must be on a 30-minute boundary (minutes 00 or 30)")
        return v

    @field_validator("patient_phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        digits = re.sub(r"\D", "", v)
        if len(digits) < 7:
            raise ValueError("phone number must have at least 7 digits")
        return v


class AppointmentOut(BaseModel):
    id: int
    doctor_id: int
    patient_name: str
    patient_phone: str
    start_time: datetime
    status: str

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Chat API schemas
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    thread_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    thread_id: str
    response: str
    tool_calls_made: list[str] = []
