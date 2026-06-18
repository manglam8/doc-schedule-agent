"""
Unit tests for tools.py — focused on slot computation and booking conflict handling.

All tests use an in-memory SQLite DB (see conftest.py).
No LLM calls are made.
"""
import json
from datetime import datetime, timezone

import pytest

from models import Appointment, AppointmentStatus


# ---------------------------------------------------------------------------
# Helpers to invoke tools without the LangChain wrapper overhead
# ---------------------------------------------------------------------------

def _invoke_availability(doctor_id: int, date: str) -> dict:
    from tools import check_doctor_availability
    raw = check_doctor_availability.invoke({"doctor_id": doctor_id, "date": date})
    return json.loads(raw)


def _invoke_book(doctor_id: int, name: str, phone: str, date: str, time: str) -> dict:
    from tools import book_appointment_slot
    raw = book_appointment_slot.invoke({
        "doctor_id": doctor_id,
        "patient_name": name,
        "patient_phone": phone,
        "date": date,
        "time": time,
    })
    return json.loads(raw)


def _invoke_specialty(specialty: str) -> dict:
    from tools import get_doctors_by_specialty
    raw = get_doctors_by_specialty.invoke({"specialty": specialty})
    return json.loads(raw)


# ---------------------------------------------------------------------------
# get_doctors_by_specialty
# ---------------------------------------------------------------------------

class TestGetDoctorsBySpecialty:
    def test_exact_match(self, seeded_db):
        result = _invoke_specialty("Cardiologist")
        assert result["found"] is True
        assert len(result["doctors"]) == 1
        assert result["doctors"][0]["name"] == "Dr. Sarah Chen"

    def test_partial_case_insensitive_match(self, seeded_db):
        result = _invoke_specialty("derma")
        assert result["found"] is True
        assert result["doctors"][0]["specialty"] == "Dermatologist"

    def test_no_match(self, seeded_db):
        result = _invoke_specialty("Neurosurgeon")
        assert result["found"] is False
        assert result["doctors"] == []


# ---------------------------------------------------------------------------
# check_doctor_availability
# ---------------------------------------------------------------------------

class TestCheckDoctorAvailability:
    def test_full_day_no_bookings(self, seeded_db):
        # Dr. Chen (id=1) works Mon–Fri 09:00–17:00
        # 2026-06-22 is a Monday
        result = _invoke_availability(1, "2026-06-22")
        assert result["available"] is True
        assert "09:00" in result["open_slots"]
        assert "16:30" in result["open_slots"]
        # 30-min slots from 09:00 to 16:30 = 16 slots
        assert len(result["open_slots"]) == 16

    def test_day_off_returns_message(self, seeded_db):
        # Dr. Adams (id=2) only works Tue & Thu; 2026-06-22 is Mon
        result = _invoke_availability(2, "2026-06-22")
        assert result["available"] is False
        assert "does not work" in result["message"]

    def test_booked_slot_excluded(self, seeded_db):
        # Pre-book 09:00 for Dr. Chen on 2026-06-22
        appt = Appointment(
            doctor_id=1,
            patient_name="Alice",
            patient_phone="555-0001",
            start_time=datetime(2026, 6, 22, 9, 0, tzinfo=timezone.utc),
            status=AppointmentStatus.CONFIRMED,
        )
        seeded_db.add(appt)
        seeded_db.commit()

        result = _invoke_availability(1, "2026-06-22")
        assert "09:00" not in result["open_slots"]
        assert "09:00" in result["booked_slots"]

    def test_cancelled_slot_is_available(self, seeded_db):
        # A CANCELLED appointment must not block the slot
        appt = Appointment(
            doctor_id=1,
            patient_name="Bob",
            patient_phone="555-0002",
            start_time=datetime(2026, 6, 22, 10, 0, tzinfo=timezone.utc),
            status=AppointmentStatus.CANCELLED,
        )
        seeded_db.add(appt)
        seeded_db.commit()

        result = _invoke_availability(1, "2026-06-22")
        assert "10:00" in result["open_slots"]

    def test_invalid_date_format(self, seeded_db):
        result = _invoke_availability(1, "22-06-2026")
        assert "error" in result

    def test_nonexistent_doctor(self, seeded_db):
        result = _invoke_availability(999, "2026-06-22")
        assert "error" in result


# ---------------------------------------------------------------------------
# book_appointment_slot
# ---------------------------------------------------------------------------

class TestBookAppointmentSlot:
    def test_successful_booking(self, seeded_db):
        result = _invoke_book(1, "Jane Doe", "555-1234", "2026-06-22", "09:00")
        assert result["success"] is True
        assert result["appointment_id"] is not None
        assert result["status"] == "CONFIRMED"

    def test_double_booking_returns_alternatives(self, seeded_db):
        # First booking succeeds
        r1 = _invoke_book(1, "Jane Doe", "555-1234", "2026-06-22", "09:00")
        assert r1["success"] is True

        # Second booking on the same slot must fail gracefully
        r2 = _invoke_book(1, "John Smith", "555-5678", "2026-06-22", "09:00")
        assert r2["success"] is False
        assert "alternative_slots" in r2
        # Agent must propose alternatives (09:30, 10:00, …)
        assert len(r2["alternative_slots"]) > 0
        assert "09:00" not in r2["alternative_slots"]

    def test_booking_outside_working_hours(self, seeded_db):
        # Dr. Chen starts at 09:00; 08:00 is outside hours
        result = _invoke_book(1, "Jane Doe", "555-1234", "2026-06-22", "08:00")
        assert result["success"] is False
        assert "working hours" in result["error"]

    def test_booking_on_day_off(self, seeded_db):
        # Dr. Adams only works Tue/Thu; 2026-06-22 is Monday
        result = _invoke_book(2, "Jane Doe", "555-1234", "2026-06-22", "10:00")
        assert result["success"] is False
        assert "does not work" in result["error"]

    def test_invalid_time_boundary(self, seeded_db):
        # :15 is not a valid 30-min boundary
        result = _invoke_book(1, "Jane Doe", "555-1234", "2026-06-22", "09:15")
        assert result["success"] is False

    def test_booking_nonexistent_doctor(self, seeded_db):
        result = _invoke_book(999, "Jane Doe", "555-1234", "2026-06-22", "09:00")
        assert result["success"] is False

    def test_multiple_patients_different_slots(self, seeded_db):
        r1 = _invoke_book(1, "Alice", "555-0001", "2026-06-22", "09:00")
        r2 = _invoke_book(1, "Bob",   "555-0002", "2026-06-22", "09:30")
        assert r1["success"] is True
        assert r2["success"] is True

    def test_gp_booking_on_working_day(self, seeded_db):
        # Dr. Patel (id=3) works Mon/Wed/Fri; 2026-06-22 is Monday
        result = _invoke_book(3, "Carol", "555-9999", "2026-06-22", "08:00")
        assert result["success"] is True
        assert result["doctor_name"] == "Dr. Priya Patel"
