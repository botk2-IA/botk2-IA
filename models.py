"""
Botk2-IA — Modelos de base de datos (SQLAlchemy)
"""

from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base
from crypto import EncryptedText


class Clinic(Base):
    __tablename__ = "clinics"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(200), nullable=False)
    email      = Column(String(200), unique=True, index=True, nullable=False)
    password   = Column(String(200), nullable=False)
    phone      = Column(String(50), default="")
    address    = Column(String(300), default="")
    whatsapp   = Column(String(50), default="")
    plan             = Column(String(50), default="free")   # free | starter | pro | clinica
    plan_expires_at  = Column(DateTime, nullable=True)      # UTC; None = sin vencimiento (free o asignado por admin)
    active           = Column(Boolean, default=True)
    wa_phone_id      = Column(String(100), default="")   # WhatsApp Phone ID por clínica
    wa_token         = Column(EncryptedText, default="")  # WhatsApp Token por clínica (cifrado)
    onboarding_done  = Column(Boolean, default=False)
    # Personalización del bot de WhatsApp
    bot_welcome      = Column(Text, default="")           # saludo inicial (vacío = saludo estándar)
    bot_info         = Column(Text, default="")           # precios, obras sociales, indicaciones, etc.
    bot_confirm_note = Column(Text, default="")           # texto extra al confirmar un turno
    bot_days_ahead   = Column(Integer, default=14)        # con cuántos días de anticipación ofrece turnos
    created_at       = Column(DateTime(timezone=True), server_default=func.now())

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
    dni         = Column(EncryptedText, default="")   # cifrado
    birth_date  = Column(String(20), default="")
    insurance   = Column(String(100), default="")   # obra social / prepaga
    notes       = Column(EncryptedText, default="")   # cifrado
    active      = Column(Boolean, default=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    clinic       = relationship("Clinic",      back_populates="patients")
    appointments = relationship("Appointment", back_populates="patient")


class Professional(Base):
    __tablename__ = "professionals"

    id         = Column(Integer, primary_key=True, index=True)
    clinic_id  = Column(Integer, ForeignKey("clinics.id"), nullable=False)
    name       = Column(String(200), nullable=False)
    specialty  = Column(String(100), default="")
    color      = Column(String(20), default="#3B82F6")
    active     = Column(Boolean, default=True)
    work_days  = Column(String(20), default="0,1,2,3,4")   # 0=Lun..6=Dom
    work_start = Column(String(10), default="09:00")
    work_end   = Column(String(10), default="18:00")
    slot_minutes = Column(Integer, default=30)          # duración de cada turno: 15, 20, 30, 45 o 60
    price      = Column(String(100), default="")        # valor de la consulta que informa el bot (opcional)

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
    notes           = Column(EncryptedText, default="")   # cifrado
    status          = Column(String(30), default="pending")  # pending|confirmed|completed|cancelled
    reminder_sent   = Column(Boolean, default=False)
    # Historia clínica (cifrada)
    diagnostico     = Column(EncryptedText, default="")
    observaciones   = Column(EncryptedText, default="")
    receta          = Column(EncryptedText, default="")
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    clinic       = relationship("Clinic",       back_populates="appointments")
    patient      = relationship("Patient",      back_populates="appointments")
    professional = relationship("Professional", back_populates="appointments")


class Payment(Base):
    """Pagos acreditados. Cada pago suma un período al plan y se registra una sola vez."""
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_payment_provider_external"),)

    id          = Column(Integer, primary_key=True, index=True)
    clinic_id   = Column(Integer, ForeignKey("clinics.id"), nullable=False)
    provider    = Column(String(30), nullable=False)    # mercadopago | stripe
    external_id = Column(String(200), nullable=False)   # id del pago en el proveedor
    plan        = Column(String(50), nullable=False)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
