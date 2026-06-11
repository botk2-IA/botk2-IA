"""
Botk2-IA — Sistema de automatización para clínicas y negocios
=================================================================
Correr con:  uvicorn main:app --reload --port 8000
Panel:       http://localhost:8000
"""

from fastapi import FastAPI, Depends, HTTPException, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import bot as chatbot
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from datetime import date, datetime, timedelta
from typing import Optional
import models
import database
import auth as auth_module

# ── Inicialización ────────────────────────────────────────────────────────────
database.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="Botk2-IA", version="1.0.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")



# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK (Railway / uptime monitoring)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "Botk2-IA"}


# ═══════════════════════════════════════════════════════════════════════════════
# LANDING & AUTH
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
def landing(request: Request, db: Session = Depends(database.get_db)):
    clinic = auth_module.get_current_clinic_optional(request, db)
    if clinic:
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("landing.html", {"request": request})


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "error": None})


@app.post("/register", response_class=HTMLResponse)
def register(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    phone: str = Form(""),
    address: str = Form(""),
    db: Session = Depends(database.get_db),
):
    existing = db.query(models.Clinic).filter(models.Clinic.email == email).first()
    if existing:
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "Ya existe una cuenta con ese email."
        })

    clinic = models.Clinic(
        name=name,
        email=email,
        password=auth_module.hash_password(password),
        phone=phone,
        address=address,
    )
    db.add(clinic)
    db.commit()
    db.refresh(clinic)

    # Crear un profesional por defecto
    prof = models.Professional(clinic_id=clinic.id, name=f"Dr. {name.split()[0]}", specialty="General")
    db.add(prof)
    db.commit()

    token = auth_module.create_access_token({"sub": str(clinic.id)})
    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie("access_token", token, httponly=True, max_age=60*60*24*7)
    return response


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(database.get_db),
):
    clinic = db.query(models.Clinic).filter(models.Clinic.email == email).first()
    if not clinic or not auth_module.verify_password(password, clinic.password):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Email o contraseña incorrectos."
        })

    token = auth_module.create_access_token({"sub": str(clinic.id)})
    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie("access_token", token, httponly=True, max_age=60*60*24*7)
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie("access_token")
    return response


# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    # Turnos de hoy
    todays_appointments = (
        db.query(models.Appointment)
        .filter(
            models.Appointment.clinic_id == clinic.id,
            models.Appointment.date == today,
        )
        .order_by(models.Appointment.time)
        .all()
    )

    # Turnos de mañana
    tomorrow_appointments = (
        db.query(models.Appointment)
        .filter(
            models.Appointment.clinic_id == clinic.id,
            models.Appointment.date == tomorrow,
            models.Appointment.status != "cancelled",
        )
        .order_by(models.Appointment.time)
        .all()
    )

    # Stats
    total_patients     = db.query(models.Patient).filter_by(clinic_id=clinic.id, active=True).count()
    total_today        = len(todays_appointments)
    pending_today      = sum(1 for a in todays_appointments if a.status == "pending")
    confirmed_today    = sum(1 for a in todays_appointments if a.status == "confirmed")

    # Últimos pacientes registrados
    recent_patients = (
        db.query(models.Patient)
        .filter_by(clinic_id=clinic.id, active=True)
        .order_by(models.Patient.created_at.desc())
        .limit(5)
        .all()
    )

    # Próximos 7 días de actividad
    week_dates = [(date.today() + timedelta(days=i)).isoformat() for i in range(7)]
    week_counts = []
    for d in week_dates:
        count = db.query(models.Appointment).filter(
            models.Appointment.clinic_id == clinic.id,
            models.Appointment.date == d,
            models.Appointment.status != "cancelled",
        ).count()
        week_counts.append(count)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "clinic": clinic,
        "today": today,
        "todays_appointments": todays_appointments,
        "tomorrow_appointments": tomorrow_appointments,
        "total_patients": total_patients,
        "total_today": total_today,
        "pending_today": pending_today,
        "confirmed_today": confirmed_today,
        "recent_patients": recent_patients,
        "week_dates": week_dates,
        "week_counts": week_counts,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# PACIENTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/patients", response_class=HTMLResponse)
def patients_list(
    request: Request,
    search: str = "",
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    query = db.query(models.Patient).filter_by(clinic_id=clinic.id, active=True)
    if search:
        query = query.filter(
            models.Patient.name.ilike(f"%{search}%") |
            models.Patient.phone.ilike(f"%{search}%") |
            models.Patient.email.ilike(f"%{search}%")
        )
    patients = query.order_by(models.Patient.name).all()

    # Agregar conteo de turnos por paciente
    for p in patients:
        p.appointment_count = db.query(models.Appointment).filter_by(patient_id=p.id).count()

    return templates.TemplateResponse("patients.html", {
        "request": request,
        "clinic": clinic,
        "patients": patients,
        "search": search,
    })


@app.post("/patients/new")
def patient_create(
    request: Request,
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    dni: str = Form(""),
    birth_date: str = Form(""),
    insurance: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    patient = models.Patient(
        clinic_id=clinic.id,
        name=name, phone=phone, email=email,
        dni=dni, birth_date=birth_date, insurance=insurance, notes=notes,
    )
    db.add(patient)
    db.commit()
    return RedirectResponse("/patients", status_code=302)


@app.post("/patients/{patient_id}/edit")
def patient_edit(
    patient_id: int,
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    dni: str = Form(""),
    birth_date: str = Form(""),
    insurance: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    patient = db.query(models.Patient).filter_by(id=patient_id, clinic_id=clinic.id).first()
    if not patient:
        raise HTTPException(404)
    patient.name = name
    patient.phone = phone
    patient.email = email
    patient.dni = dni
    patient.birth_date = birth_date
    patient.insurance = insurance
    patient.notes = notes
    db.commit()
    return RedirectResponse("/patients", status_code=302)


@app.post("/patients/{patient_id}/delete")
def patient_delete(
    patient_id: int,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    patient = db.query(models.Patient).filter_by(id=patient_id, clinic_id=clinic.id).first()
    if patient:
        patient.active = False
        db.commit()
    return RedirectResponse("/patients", status_code=302)


@app.get("/patients/{patient_id}", response_class=HTMLResponse)
def patient_detail(
    patient_id: int,
    request: Request,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    patient = db.query(models.Patient).filter_by(id=patient_id, clinic_id=clinic.id).first()
    if not patient:
        raise HTTPException(404)
    appointments = (
        db.query(models.Appointment)
        .filter_by(patient_id=patient_id)
        .order_by(models.Appointment.date.desc(), models.Appointment.time.desc())
        .all()
    )
    return templates.TemplateResponse("patient_detail.html", {
        "request": request,
        "clinic": clinic,
        "patient": patient,
        "appointments": appointments,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# TURNOS / APPOINTMENTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/appointments", response_class=HTMLResponse)
def appointments_list(
    request: Request,
    date_filter: str = "",
    status_filter: str = "",
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    if not date_filter:
        date_filter = date.today().isoformat()

    query = db.query(models.Appointment).filter_by(clinic_id=clinic.id)

    if date_filter:
        query = query.filter(models.Appointment.date == date_filter)
    if status_filter:
        query = query.filter(models.Appointment.status == status_filter)

    appointments = query.order_by(models.Appointment.time).all()
    patients      = db.query(models.Patient).filter_by(clinic_id=clinic.id, active=True).order_by(models.Patient.name).all()
    professionals = db.query(models.Professional).filter_by(clinic_id=clinic.id, active=True).all()

    return templates.TemplateResponse("appointments.html", {
        "request": request,
        "clinic": clinic,
        "appointments": appointments,
        "patients": patients,
        "professionals": professionals,
        "date_filter": date_filter,
        "status_filter": status_filter,
        "today": date.today().isoformat(),
    })


@app.post("/appointments/new")
def appointment_create(
    patient_id: int = Form(...),
    professional_id: int = Form(None),
    date_str: str = Form(..., alias="date"),
    time_str: str = Form(..., alias="time"),
    duration_min: int = Form(30),
    reason: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    appointment = models.Appointment(
        clinic_id=clinic.id,
        patient_id=patient_id,
        professional_id=professional_id or None,
        date=date_str,
        time=time_str,
        duration_min=duration_min,
        reason=reason,
        notes=notes,
        status="pending",
    )
    db.add(appointment)
    db.commit()
    return RedirectResponse(f"/appointments?date_filter={date_str}", status_code=302)


@app.post("/appointments/{appt_id}/status")
def appointment_update_status(
    appt_id: int,
    new_status: str = Form(...),
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    appt = db.query(models.Appointment).filter_by(id=appt_id, clinic_id=clinic.id).first()
    if appt:
        appt.status = new_status
        db.commit()
    return RedirectResponse(f"/appointments?date_filter={appt.date}", status_code=302)


@app.post("/appointments/{appt_id}/delete")
def appointment_delete(
    appt_id: int,
    date_back: str = Form(""),
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    appt = db.query(models.Appointment).filter_by(id=appt_id, clinic_id=clinic.id).first()
    if appt:
        date_back = appt.date
        db.delete(appt)
        db.commit()
    return RedirectResponse(f"/appointments?date_filter={date_back}", status_code=302)


# ═══════════════════════════════════════════════════════════════════════════════
# PROFESIONALES
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/professionals", response_class=HTMLResponse)
def professionals_list(
    request: Request,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    professionals = db.query(models.Professional).filter_by(clinic_id=clinic.id).all()
    return templates.TemplateResponse("professionals.html", {
        "request": request,
        "clinic": clinic,
        "professionals": professionals,
    })


@app.post("/professionals/new")
def professional_create(
    name: str = Form(...),
    specialty: str = Form(""),
    color: str = Form("#3B82F6"),
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    prof = models.Professional(
        clinic_id=clinic.id,
        name=name,
        specialty=specialty,
        color=color,
    )
    db.add(prof)
    db.commit()
    return RedirectResponse("/professionals", status_code=302)


@app.post("/professionals/{prof_id}/delete")
def professional_delete(
    prof_id: int,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    prof = db.query(models.Professional).filter_by(id=prof_id, clinic_id=clinic.id).first()
    if prof:
        prof.active = False
        db.commit()
    return RedirectResponse("/professionals", status_code=302)


# ═══════════════════════════════════════════════════════════════════════════════
# RECORDATORIOS (WhatsApp scaffold)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/reminders", response_class=HTMLResponse)
def reminders_page(
    request: Request,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    pending_reminders = (
        db.query(models.Appointment)
        .filter(
            models.Appointment.clinic_id == clinic.id,
            models.Appointment.date == tomorrow,
            models.Appointment.status != "cancelled",
            models.Appointment.reminder_sent == False,
        )
        .order_by(models.Appointment.time)
        .all()
    )
    sent_reminders = (
        db.query(models.Appointment)
        .filter(
            models.Appointment.clinic_id == clinic.id,
            models.Appointment.reminder_sent == True,
        )
        .order_by(models.Appointment.date.desc())
        .limit(20)
        .all()
    )
    return templates.TemplateResponse("reminders.html", {
        "request": request,
        "clinic": clinic,
        "pending_reminders": pending_reminders,
        "sent_reminders": sent_reminders,
        "tomorrow": tomorrow,
    })


@app.post("/reminders/{appt_id}/send")
def send_reminder(
    appt_id: int,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    """
    Aquí iría la integración real con WhatsApp Business API / Twilio.
    Por ahora marcamos como enviado (simulación).
    """
    appt = db.query(models.Appointment).filter_by(id=appt_id, clinic_id=clinic.id).first()
    if appt:
        appt.reminder_sent = True
        db.commit()
        # TODO: Integrar con Twilio / WhatsApp Business API
        # message = f"Hola {appt.patient.name}! Le recordamos su turno el {appt.date} a las {appt.time}. — {clinic.name}"
        # twilio_client.messages.create(to=f"whatsapp:+54{appt.patient.phone}", body=message, ...)
    return RedirectResponse("/reminders", status_code=302)


@app.post("/reminders/send-all")
def send_all_reminders(
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    appointments = (
        db.query(models.Appointment)
        .filter(
            models.Appointment.clinic_id == clinic.id,
            models.Appointment.date == tomorrow,
            models.Appointment.status != "cancelled",
            models.Appointment.reminder_sent == False,
        )
        .all()
    )
    for appt in appointments:
        appt.reminder_sent = True
    db.commit()
    return RedirectResponse("/reminders", status_code=302)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    saved: str = "",
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    import os as _os2
    wa_phone_id = _os2.environ.get("WHATSAPP_PHONE_ID", "")
    wa_token = _os2.environ.get("WHATSAPP_TOKEN", "")
    webhook_url = str(request.base_url).rstrip("/") + "/webhook"
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "clinic": clinic,
        "saved": bool(saved),
        "wa_phone_id": wa_phone_id,
        "wa_token_set": bool(wa_token),
        "webhook_url": webhook_url,
    })


@app.post("/settings")
def settings_save(
    name: str = Form(...),
    phone: str = Form(""),
    address: str = Form(""),
    whatsapp: str = Form(""),
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    clinic.name     = name
    clinic.phone    = phone
    clinic.address  = address
    clinic.whatsapp = whatsapp
    db.commit()
    return RedirectResponse("/settings?saved=1", status_code=302)


# ═══════════════════════════════════════════════════════════════════════════════
# API JSON (para integraciones externas)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/appointments/{date_str}")
def api_appointments_by_date(
    date_str: str,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    appointments = (
        db.query(models.Appointment)
        .filter_by(clinic_id=clinic.id)
        .filter(models.Appointment.date == date_str)
        .order_by(models.Appointment.time)
        .all()
    )
    return [
        {
            "id": a.id,
            "patient": a.patient.name if a.patient else "",
            "phone": a.patient.phone if a.patient else "",
            "time": a.time,
            "duration": a.duration_min,
            "reason": a.reason,
            "status": a.status,
            "professional": a.professional.name if a.professional else "",
        }
        for a in appointments
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# CHATBOT — WEBHOOK WHATSAPP (Twilio / Meta)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/webhook/whatsapp/{clinic_id}")
async def whatsapp_webhook_verify(clinic_id: int, request: Request):
    """Verificación del webhook de Meta/WhatsApp."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    return Response(content="Forbidden", status_code=403)

@app.post("/webhook/whatsapp/{clinic_id}")
async def whatsapp_webhook(
    clinic_id: int,
    request: Request,
    db: Session = Depends(database.get_db),
):
    """
    Webhook que recibe mensajes de WhatsApp vía Meta Cloud API.
    """
    import httpx, os as _os
    data = await request.json()
    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]["value"]
        messages = changes.get("messages")
        if not messages:
            return {"status": "ok"}
        msg = messages[0]
        phone = msg["from"]
        body = msg.get("text", {}).get("body", "").strip()
    except (KeyError, IndexError):
        return {"status": "ok"}

    clinic_obj = db.query(models.Clinic).filter_by(id=clinic_id, active=True).first()
    if not clinic_obj:
        return {"status": "ok"}

    reply = chatbot.process_message(phone, body, db, clinic_id)

    # Enviar respuesta via Meta API
    wa_token = _os.environ.get("WHATSAPP_TOKEN", "")
    wa_phone_id = _os.environ.get("WHATSAPP_PHONE_ID", "")
    if wa_token and wa_phone_id:
        url = f"https://graph.facebook.com/v21.0/{wa_phone_id}/messages"
        headers = {"Authorization": f"Bearer {wa_token}", "Content-Type": "application/json"}
        payload = {"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": reply}}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, headers=headers, timeout=10)
                print(f"[WhatsApp] Enviando a {phone} | Status: {resp.status_code} | Body: {resp.text}")
        except Exception as e:
            print(f"[WhatsApp] Error enviando mensaje: {e}")

    return {"status": "ok"}


# ═══════════════════════════════════════════════════════════════════════════════
# SIMULADOR DE CHAT (desarrollo)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/chat-sim", response_class=HTMLResponse)
def chat_sim_page(
    request: Request,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    return templates.TemplateResponse("chat_sim.html", {
        "request": request,
        "clinic": clinic,
    })


@app.post("/chat-sim/send")
async def chat_sim_send(
    request: Request,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    data    = await request.json()
    message = data.get("message", "")
    phone   = f"sim_{clinic.id}_test"
    reply   = chatbot.process_message(phone, message, db, clinic.id)
    return JSONResponse({"reply": reply})


# ═══════════════════════════════════════════════════════════════════════════════
# PANEL DE ADMINISTRADOR — Botk2-IA
# Acceso: /admin  |  Auth separada por contraseña de entorno
# ═══════════════════════════════════════════════════════════════════════════════
import os
import hashlib

WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "botk2ia_webhook_secret")

ADMIN_PASSWORD_HASH = os.environ.get(
    "ADMIN_PASSWORD_HASH",
    # Hash por defecto: "botk2admin2024" — cambiarlo en producción via env var
    hashlib.sha256("botk2admin2024".encode()).hexdigest()
)

def _check_admin(request: Request) -> bool:
    """Verifica si la cookie de admin es válida."""
    return request.cookies.get("admin_token") == ADMIN_PASSWORD_HASH


def _require_admin(request: Request):
    if not _check_admin(request):
        raise HTTPException(status_code=302, headers={"Location": "/admin/login"})


def _admin_rows(db: Session, q: str = ""):
    """Devuelve lista enriquecida de clínicas con sus estadísticas."""
    query = db.query(models.Clinic)
    if q:
        query = query.filter(
            models.Clinic.name.ilike(f"%{q}%") |
            models.Clinic.email.ilike(f"%{q}%")
        )
    clinics = query.order_by(models.Clinic.created_at.desc()).all()

    rows = []
    for c in clinics:
        rows.append({
            "clinic": c,
            "patients":      db.query(models.Patient).filter_by(clinic_id=c.id, active=True).count(),
            "appointments":  db.query(models.Appointment).filter_by(clinic_id=c.id).count(),
            "professionals": db.query(models.Professional).filter_by(clinic_id=c.id, active=True).count(),
        })
    return rows


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    if _check_admin(request):
        return RedirectResponse("/admin", status_code=302)
    return templates.TemplateResponse("admin_login.html", {"request": request, "error": None})


@app.post("/admin/login", response_class=HTMLResponse)
def admin_login(request: Request, password: str = Form(...)):
    hashed = hashlib.sha256(password.encode()).hexdigest()
    if hashed != ADMIN_PASSWORD_HASH:
        return templates.TemplateResponse("admin_login.html", {
            "request": request,
            "error": "Contraseña incorrecta."
        })
    response = RedirectResponse("/admin", status_code=302)
    response.set_cookie("admin_token", hashed, httponly=True, max_age=60*60*8)  # 8 horas
    return response


@app.get("/admin/logout")
def admin_logout():
    response = RedirectResponse("/admin/login", status_code=302)
    response.delete_cookie("admin_token")
    return response


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    q: str = "",
    db: Session = Depends(database.get_db),
):
    if not _check_admin(request):
        return RedirectResponse("/admin/login", status_code=302)

    all_clinics = db.query(models.Clinic).all()

    stats = {
        "total_clinics":   len(all_clinics),
        "active_clinics":  sum(1 for c in all_clinics if c.active),
        "total_patients":  db.query(models.Patient).filter_by(active=True).count(),
        "total_appointments": db.query(models.Appointment).count(),
        "paid_clinics":    sum(1 for c in all_clinics if c.plan not in ("free", None)),
        "by_plan": {
            "free":    sum(1 for c in all_clinics if c.plan == "free"),
            "starter": sum(1 for c in all_clinics if c.plan == "starter"),
            "pro":     sum(1 for c in all_clinics if c.plan == "pro"),
            "clinica": sum(1 for c in all_clinics if c.plan == "clinica"),
        },
    }

    rows         = _admin_rows(db, q)
    recent_rows  = _admin_rows(db)[:5]  # últimas 5 sin filtro

    return templates.TemplateResponse("admin_dashboard.html", {
        "request":       request,
        "clinics":       rows,
        "recent_clinics": recent_rows,
        "stats":         stats,
        "today":         date.today().strftime("%d/%m/%Y"),
        "q":             q,
    })


@app.post("/admin/clinics/{clinic_id}/plan")
def admin_change_plan(
    clinic_id: int,
    plan: str = Form(...),
    request: Request = None,
    db: Session = Depends(database.get_db),
):
    if not _check_admin(request):
        return RedirectResponse("/admin/login", status_code=302)
    clinic = db.query(models.Clinic).filter_by(id=clinic_id).first()
    if clinic and plan in ("free", "starter", "pro", "clinica"):
        clinic.plan = plan
        db.commit()
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/clinics/{clinic_id}/toggle")
def admin_toggle_clinic(
    clinic_id: int,
    request: Request,
    db: Session = Depends(database.get_db),
):
    if not _check_admin(request):
        return RedirectResponse("/admin/login", status_code=302)
    clinic = db.query(models.Clinic).filter_by(id=clinic_id).first()
    if clinic:
        clinic.active = not clinic.active
        db.commit()
    return RedirectResponse("/admin", status_code=302)


@app.get("/admin/login-as/{clinic_id}")
def admin_login_as(
    clinic_id: int,
    request: Request,
    db: Session = Depends(database.get_db),
):
    """Permite al admin ingresar como cualquier clínica para ver su dashboard."""
    if not _check_admin(request):
        return RedirectResponse("/admin/login", status_code=302)
    clinic = db.query(models.Clinic).filter_by(id=clinic_id).first()
    if not clinic:
        return RedirectResponse("/admin", status_code=302)
    token = auth_module.create_access_token({"sub": str(clinic.id)})
    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie("access_token", token, httponly=True, max_age=60*60*2)  # 2 horas
    return response


# ═══════════════════════════════════════════════════════════════════════════════
# PAGOS — MercadoPago + Stripe
# ═══════════════════════════════════════════════════════════════════════════════
import payments as pay_module

VALID_PLANS = {"starter", "pro", "clinica"}


@app.get("/subscribe/{plan}", response_class=HTMLResponse)
def subscribe_page(
    plan: str,
    request: Request,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    """Página de selección de método de pago."""
    if plan not in VALID_PLANS:
        return RedirectResponse("/dashboard", status_code=302)

    plan_info = pay_module.PLANS[plan]
    return templates.TemplateResponse("subscribe.html", {
        "request":   request,
        "clinic":    clinic,
        "plan":      plan,
        "plan_info": plan_info,
    })


@app.post("/subscribe/{plan}/mercadopago")
def subscribe_mercadopago(
    plan: str,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    """Redirige a MercadoPago Checkout Pro."""
    if plan not in VALID_PLANS:
        return RedirectResponse("/dashboard", status_code=302)

    checkout_url = pay_module.create_mp_preference(plan, clinic.id, clinic.email)

    if not checkout_url:
        # MP no configurado aún — en demo vamos directo al éxito
        return RedirectResponse(
            f"/payment/success?plan={plan}&method=mercadopago&demo=1",
            status_code=302,
        )
    return RedirectResponse(checkout_url, status_code=302)


@app.post("/subscribe/{plan}/stripe")
def subscribe_stripe(
    plan: str,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    """Redirige a Stripe Checkout."""
    if plan not in VALID_PLANS:
        return RedirectResponse("/dashboard", status_code=302)

    checkout_url = pay_module.create_stripe_session(plan, clinic.id, clinic.email)

    if not checkout_url:
        # Stripe no configurado aún — en demo vamos directo al éxito
        return RedirectResponse(
            f"/payment/success?plan={plan}&method=stripe&demo=1",
            status_code=302,
        )
    return RedirectResponse(checkout_url, status_code=302)


@app.get("/payment/success", response_class=HTMLResponse)
def payment_success(
    request: Request,
    plan: str = "",
    method: str = "",
    status: str = "approved",
    demo: str = "",
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    """Página de éxito — actualiza el plan de la clínica."""
    if plan in VALID_PLANS and status != "pending":
        clinic.plan = plan
        db.commit()

    plan_info = pay_module.PLANS.get(plan, {"name": plan.capitalize()})
    return templates.TemplateResponse("payment_success.html", {
        "request":   request,
        "clinic":    clinic,
        "plan":      plan,
        "plan_name": plan_info["name"],
        "method":    method,
        "status":    status,
        "is_demo":   bool(demo),
    })


@app.get("/payment/cancel", response_class=HTMLResponse)
def payment_cancel(
    request: Request,
    reason: str = "",
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    """Página de pago cancelado."""
    return templates.TemplateResponse("payment_cancel.html", {
        "request": request,
        "clinic":  clinic,
        "reason":  reason,
    })


# ── Webhooks de pago (llamados por MercadoPago / Stripe) ──────────────────────

@app.post("/webhook/mercadopago")
async def webhook_mercadopago(request: Request, db: Session = Depends(database.get_db)):
    """
    Recibe notificaciones IPN de MercadoPago y actualiza el plan de la clínica.
    Configurar en el dashboard de MercadoPago → Notificaciones IPN.
    """
    try:
        data = await request.json()
    except Exception:
        data = dict(await request.form())

    result = pay_module.verify_mp_webhook(data)
    if result and result.get("status") == "approved":
        clinic = db.query(models.Clinic).filter_by(id=result["clinic_id"]).first()
        if clinic:
            # El plan lo guardamos en la preferencia external_reference
            # Acá se puede guardar el plan en el metadata de la preferencia
            # Por ahora confiamos en el query param enviado
            db.commit()

    return JSONResponse({"ok": True})


@app.post("/webhook/stripe")
async def webhook_stripe(request: Request, db: Session = Depends(database.get_db)):
    """
    Recibe eventos de Stripe y actualiza el plan de la clínica.
    Configurar en el dashboard de Stripe → Developers → Webhooks.
    Evento requerido: checkout.session.completed
    """
    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    result = pay_module.verify_stripe_webhook(payload, sig_header)
    if result and result.get("status") == "paid":
        clinic = db.query(models.Clinic).filter_by(id=result["clinic_id"]).first()
        if clinic and result.get("plan") in VALID_PLANS:
            clinic.plan = result["plan"]
            db.commit()

    return JSONResponse({"ok": True})
