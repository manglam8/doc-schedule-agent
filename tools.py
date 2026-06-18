"""
LLM-callable tools for the Doctor Scheduling Agent.

All DB access is isolated here — no SQL leaks into agent.py.
Each tool returns a plain string so the LLM can parse it naturally.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone, date as date_type
from typing import Annotated

from langchain_core.tools import tool
from sqlalchemy.exc import IntegrityError

from config import get_settings
from database import SessionLocal
from models import Appointment, AppointmentStatus, Doctor

logger = logging.getLogger(__name__)
settings = get_settings()

# Day abbreviation used by schedule JSON → Python weekday index
_DAY_TO_WEEKDAY = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(date_str: str) -> date_type:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"Invalid date format '{date_str}'. Expected YYYY-MM-DD.")


def _slot_to_utc(date_str: str, time_str: str) -> datetime:
    """Combine date + HH:MM into a UTC-aware datetime (clinic runs in UTC)."""
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    return dt.replace(tzinfo=timezone.utc)


def _generate_slots(start_hour: int, start_min: int, end_hour: int, end_min: int) -> list[str]:
    """Generate HH:MM slot strings every 30 minutes in [start, end)."""
    slots: list[str] = []
    current = timedelta(hours=start_hour, minutes=start_min)
    end = timedelta(hours=end_hour, minutes=end_min)
    while current < end:
        total_minutes = int(current.total_seconds() // 60)
        h, m = divmod(total_minutes, 60)
        slots.append(f"{h:02d}:{m:02d}")
        current += timedelta(minutes=30)
    return slots


def _day_abbrev(d: date_type) -> str:
    return list(_DAY_TO_WEEKDAY.keys())[d.weekday()]


# ---------------------------------------------------------------------------
# Tool 1 — find doctors
# ---------------------------------------------------------------------------

@tool
def get_doctors_by_specialty(specialty: str) -> str:
    """
    Return a JSON list of doctors matching the given medical specialty.

    Use this whenever the user mentions a health concern or explicitly names
    a specialty (e.g. 'skin rash' → Dermatologist, 'heart checkup' → Cardiologist).

    Args:
        specialty: A medical specialty string (e.g. 'Cardiologist', 'Dermatologist',
                   'General Practitioner'). Partial, case-insensitive match is supported.
    """
    db = SessionLocal()
    try:
        doctors = (
            db.query(Doctor)
            .filter(Doctor.specialty.ilike(f"%{specialty}%"))
            .all()
        )
        if not doctors:
            return json.dumps({
                "found": False,
                "message": f"No doctors found for specialty '{specialty}'.",
                "doctors": [],
            })

        result = []
        for doc in doctors:
            schedule = doc.get_schedule()
            working_days = list(schedule.keys())
            result.append({
                "doctor_id": doc.id,
                "name": doc.name,
                "specialty": doc.specialty,
                "working_days": working_days,
            })

        return json.dumps({"found": True, "doctors": result})
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Tool 2 — check availability
# ---------------------------------------------------------------------------

@tool
def check_doctor_availability(doctor_id: int, date: str) -> str:
    """
    Return the list of open 30-minute appointment slots for a doctor on a specific date.

    IMPORTANT: Always call this before booking. Never guess or invent slots.

    Args:
        doctor_id: The integer ID of the doctor (from get_doctors_by_specialty).
        date: The date to check, formatted as YYYY-MM-DD.
    """
    db = SessionLocal()
    try:
        doctor = db.get(Doctor, doctor_id)
        if not doctor:
            return json.dumps({"error": f"Doctor with id={doctor_id} not found."})

        target_date = _parse_date(date)
        day_abbrev = _day_abbrev(target_date)
        schedule = doctor.get_schedule()

        if day_abbrev not in schedule:
            return json.dumps({
                "available": False,
                "doctor_name": doctor.name,
                "date": date,
                "day": day_abbrev,
                "message": (
                    f"Dr. {doctor.name} does not work on {day_abbrev}s. "
                    f"Working days: {', '.join(schedule.keys())}."
                ),
                "slots": [],
            })

        hours = schedule[day_abbrev]
        start_h, start_m = int(hours[0][:2]), int(hours[0][3:])
        end_h, end_m = int(hours[1][:2]), int(hours[1][3:])
        all_slots = _generate_slots(start_h, start_m, end_h, end_m)

        # Fetch already-booked slots for that day
        day_start = datetime(_parse_date(date).year, _parse_date(date).month,
                             _parse_date(date).day, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)

        booked = (
            db.query(Appointment.start_time)
            .filter(
                Appointment.doctor_id == doctor_id,
                Appointment.start_time >= day_start,
                Appointment.start_time < day_end,
                Appointment.status != AppointmentStatus.CANCELLED,
            )
            .all()
        )
        booked_times = {row[0].strftime("%H:%M") for row in booked}

        available = [s for s in all_slots if s not in booked_times]

        return json.dumps({
            "available": bool(available),
            "doctor_id": doctor_id,
            "doctor_name": doctor.name,
            "date": date,
            "day": day_abbrev,
            "open_slots": available,
            "booked_slots": sorted(booked_times),
        })
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Tool 3 — book appointment
# ---------------------------------------------------------------------------

@tool
def book_appointment_slot(
    doctor_id: int,
    patient_name: str,
    patient_phone: str,
    date: str,
    time: str,
) -> str:
    """
    Attempt to book a 30-minute appointment slot for a patient.

    You MUST have called check_doctor_availability first to confirm the slot is open.
    If the slot is already taken (race condition), this tool will return an error
    with alternative suggestions — relay them to the user and offer to rebook.

    Args:
        doctor_id: Integer ID of the doctor.
        patient_name: Full name of the patient.
        patient_phone: Contact phone number of the patient.
        date: Appointment date as YYYY-MM-DD.
        time: Appointment time as HH:MM (24-hour, on a 30-minute boundary).
    """
    db = SessionLocal()
    try:
        # --- Validate inputs before hitting the DB ---
        target_date = _parse_date(date)
        # Validate time format and boundary
        try:
            dt = datetime.strptime(time, "%H:%M")
        except ValueError:
            return json.dumps({"success": False, "error": f"Invalid time format '{time}'. Use HH:MM."})

        if dt.minute not in (0, 30):
            return json.dumps({
                "success": False,
                "error": f"Time '{time}' is not on a 30-minute boundary. Use :00 or :30.",
            })

        doctor = db.get(Doctor, doctor_id)
        if not doctor:
            return json.dumps({"success": False, "error": f"Doctor id={doctor_id} not found."})

        # Verify the doctor works that day
        day_abbrev = _day_abbrev(target_date)
        schedule = doctor.get_schedule()
        if day_abbrev not in schedule:
            return json.dumps({
                "success": False,
                "error": (
                    f"Dr. {doctor.name} does not work on {day_abbrev}s. "
                    f"Working days: {', '.join(schedule.keys())}."
                ),
            })

        # Verify the requested time is within working hours
        hours = schedule[day_abbrev]
        start_h, start_m = int(hours[0][:2]), int(hours[0][3:])
        end_h, end_m = int(hours[1][:2]), int(hours[1][3:])
        all_slots = _generate_slots(start_h, start_m, end_h, end_m)
        if time not in all_slots:
            return json.dumps({
                "success": False,
                "error": f"'{time}' is outside Dr. {doctor.name}'s working hours on {day_abbrev}.",
                "valid_slots": all_slots,
            })

        start_time_utc = _slot_to_utc(date, time)

        appointment = Appointment(
            doctor_id=doctor_id,
            patient_name=patient_name.strip(),
            patient_phone=patient_phone.strip(),
            start_time=start_time_utc,
            status=AppointmentStatus.CONFIRMED,
        )
        db.add(appointment)
        db.commit()
        db.refresh(appointment)

        return json.dumps({
            "success": True,
            "appointment_id": appointment.id,
            "doctor_name": doctor.name,
            "specialty": doctor.specialty,
            "patient_name": patient_name,
            "date": date,
            "time": time,
            "status": appointment.status.value,
            "message": (
                f"Appointment confirmed! {patient_name} is booked with {doctor.name} "
                f"({doctor.specialty}) on {date} at {time} UTC."
            ),
        })

    except IntegrityError:
        # Double-booking caught at DB level (unique constraint violation)
        db.rollback()
        # Fetch alternative slots to offer the user
        try:
            alt_data = json.loads(check_doctor_availability.invoke({
                "doctor_id": doctor_id, "date": date
            }))
            alternatives = alt_data.get("open_slots", [])
        except Exception:
            alternatives = []

        return json.dumps({
            "success": False,
            "error": (
                f"The slot {date} at {time} with Dr. {doctor.name if doctor else doctor_id} "
                "was just booked by someone else."
            ),
            "alternative_slots": alternatives,
            "suggestion": (
                "Please choose one of the alternative slots listed above, "
                "or pick a different date."
            ),
        })
    except ValueError as exc:
        db.rollback()
        return json.dumps({"success": False, "error": str(exc)})
    except Exception as exc:
        db.rollback()
        logger.exception("Unexpected error in book_appointment_slot")
        return json.dumps({"success": False, "error": f"Unexpected error: {exc}"})
    finally:
        db.close()


# Exported list for agent registration
ALL_TOOLS = [get_doctors_by_specialty, check_doctor_availability, book_appointment_slot]
