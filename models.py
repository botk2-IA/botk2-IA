"""
Botk2-IA — Modelos de base de datos (SQLAlchemy)
"""

from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class Clinic(Base):
    __tablename__ = "clinics"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(200), nullable=False)
    email      = Column(String(200), unique=True, index=True, nullable=False)
    password   = Column(String(200), nullable=False)
    phone      = Column(String(50), default="")
    address    = Column(String(300), default="")
    whatsapp   = Column(String(50), default="")
    plan       = Column(String(50), default="free")   # free | starter | pro | clinica
    active     = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    patients      = relationship("Patient",      back_populates="clinic")
    professionals = relationship("Professional", back_populates="clinic")
    appointments  = relationship("Appointment",  back_populates="clinic")


class Patient(Base):
    __tablename__ = "patients"

    id          = Column(Integer, primary_key=True, index=True)
    clinic_id   = Column(Integer, ForeignKey("clinics.id"), nullable=False)
    name        = Column(String(200), nullable=False)
    phone       = Column(String(50), default="")
    email       = Column(String(200), default="")
    dni         = Column(String(30), default="")
    birth_date  = Column(String(20), default="")
    insurance   = Column(String(100), default="")   # obra social / prepaga
    notes       = Column(Text, default="")
    active      = Column(Boolean, default=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    clinic       = relationship("Clinic",      back_populates="patients")
    appointments = relationship("Appointment", back_populates="patient")


class Professional(Base):
    __tablename__ = "professionals"

    id        = Column(Integer, primary_key=True, index=True)
    clinic_id = Column(Integer, ForeignKey("clinics.id"), nullable=False)
    name      = Column(String(200), nullable=False)
    specialty = Column(String(100), default="")
    color     = Column(String(20), default="#3B82F6")
    active    = Column(Boolean, default=True)

    clinic       = relationship("Clinic",      back_populates="professionals")
    appointments = relationship("Appointment", back_populates="professional")


class Appointment(Base):
    __tablename__ = "appointments"

    id              = Column(Integer, primary_key=True, index=True)
    clinic_id       = Column(Integer, ForeignKey("clinics.id"), nullable=False)
    patient_id      = Column(Integer, ForeignKey("patients.id"), nullable=False)
    professional_id = Column(Integer, ForeignKey("professionals.id"), nullable=True)
    date            = Column(String(20), nullable=False)   # YYYY-MM-DD
    time            = Column(String(10), nullable=False)   # HH:MM
    duration_min    = Column(Integer, default=30)
    reason          = Column(String(300), default="")
    notes           = Column(Text, default="")
    status          = Column(String(30), default="pending")  # pending|confirmed|completed|cancelled
    reminder_sent   = Column(Boolean, default=False)
    # Historia clínica
    diagnostico     = Column(Text, default="")
    observaciones   = Column(Text, default="")
    receta          = Column(Text, default="")
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    clinic       = relationship("Clinic",       back_populates="appointments")
    patient      = relationship("Patient",      back_populates="appointments")
    professional = relationship("Professional", back_populates="appointments")
