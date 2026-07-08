"""
Botk2-IA — Sistema de automatización para clínicas y negocios
=================================================================
Correr con:  uvicorn main:app --reload --port 8000
Panel:       http://localhost:8000
"""

from contextlib import asynccontextmanager
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
import reminders as reminders_module
from apscheduler.schedulers.background import BackgroundScheduler

# ── Inicialización ────────────────────────────────────────────────────────────
database.Base.metadata.create_all(bind=database.engine)

# Migración: agregar columnas nuevas si no existen (IF NOT EXISTS evita errores en PostgreSQL)
from sqlalchemy import text as _sql_text
def _run_migration():
    migrations = [
        "ALTER TABLE clinics ADD COLUMN IF NOT EXISTS wa_phone_id VARCHAR(100) DEFAULT ''",
        "ALTER TABLE clinics ADD COLUMN IF NOT EXISTS wa_token TEXT DEFAULT ''",
        "ALTER TABLE clinics ADD COLUMN IF NOT EXISTS onboarding_done BOOLEAN DEFAULT FALSE",
        "ALTER TABLE professionals ADD COLUMN IF NOT EXISTS work_days VARCHAR(20) DEFAULT '0,1,2,3,4'",
        "ALTER TABLE professionals ADD COLUMN IF NOT EXISTS work_start VARCHAR(10) DEFAULT '09:00'",
        "ALTER TABLE professionals ADD COLUMN IF NOT EXISTS work_end VARCHAR(10) DEFAULT '18:00'",
    ]
    with database.engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(_sql_text(sql))
                conn.commit()
            except Exception as e:
                conn.rollback()
                print(f"[Migration] skipped: {e}")
_run_migration()

_scheduler = BackgroundScheduler(timezone="America/Argentina/Buenos_Aires")

@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Recordatorio diario a las 9:00 AM hora Argentina
    _scheduler.add_job(reminders_module.send_reminders, "cron", hour=9, minute=0, id="daily_reminders")
    _scheduler.start()
    print("[Scheduler] Recordatorios automáticos activos (9:00 AM diario)")
    yield
    _scheduler.shutdown(wait=False)

app = FastAPI(title="Botk2-IA", version="1.0.0", lifespan=_lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")



# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK (Railway / uptime monitoring)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "Botk2-IA"}


@app.get("/privacy", response_class=HTMLResponse)
def privacy_policy(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})


# ═══════════════════════════════════════════════════════════════════════════════
# LANDING & AUTH
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
def landing(request: Request, db: Session = Depends(database.get_db)):
    clinic = auth_module.get_current_clinic_optional(request, db)
    if clinic:
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("landing.html", {"request": request})

@app.get("/home", response_class=HTMLResponse)
def landing_public(request: Request):
    """Landing page pública — siempre muestra la landing aunque esté logueado."""
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
    response = RedirectResponse("/onboarding", status_code=302)
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
# ONBOARDING
# ═══════════════════════════════════════════════════════════════════════════════

def _get_onboarding_step(clinic, professionals):
    """Determina en qué paso del onboarding está la clínica."""
    if not (clinic.phone or clinic.address):
        return 1
    if not professionals:
        return 2
    if not (clinic.wa_phone_id and clinic.wa_token):
        return 3
    return 4


@app.get("/onboarding", response_class=HTMLResponse)
def onboarding_page(
    request: Request,
    step: int = 0,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    if clinic.onboarding_done:
        return RedirectResponse("/dashboard", status_code=302)
    professionals = db.query(models.Professional).filter_by(clinic_id=clinic.id, active=True).all()
    current_step = step if step else _get_onboarding_step(clinic, professionals)
    webhook_url = str(request.base_url).rstrip("/") + f"/webhook/whatsapp/{clinic.id}"
    return templates.TemplateResponse("onboarding.html", {
        "request": request,
        "clinic": clinic,
        "step": current_step,
        "professionals": professionals,
        "webhook_url": webhook_url,
    })


@app.post("/onboarding/step1")
def onboarding_step1(
    name: str = Form(...),
    phone: str = Form(""),
    address: str = Form(""),
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    clinic.name = name
    clinic.phone = phone
    clinic.address = address
    db.commit()
    return RedirectResponse("/onboarding?step=2", status_code=302)


@app.post("/onboarding/step2")
def onboarding_step2(
    prof_name: str = Form(...),
    specialty: str = Form(""),
    color: str = Form("#3B82F6"),
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    # Borrar el profesional por defecto si existe y no tiene turnos
    default_prof = db.query(models.Professional).filter_by(
        clinic_id=clinic.id, name=f"Dr. {clinic.name.split()[0]}"
    ).first()
    if default_prof and not default_prof.appointments:
        db.delete(default_prof)
    prof = models.Professional(clinic_id=clinic.id, name=prof_name, specialty=specialty, color=color)
    db.add(prof)
    db.commit()
    return RedirectResponse("/onboarding?step=3", status_code=302)


@app.post("/onboarding/step3")
def onboarding_step3(
    wa_phone_id: str = Form(""),
    wa_token: str = Form(""),
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    clinic.wa_phone_id = wa_phone_id.strip()
    clinic.wa_token = wa_token.strip()
    db.commit()
    return RedirectResponse("/onboarding?step=4", status_code=302)


@app.post("/onboarding/complete")
def onboarding_complete(
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    clinic.onboarding_done = True
    db.commit()
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/onboarding/skip")
def onboarding_skip(
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    clinic.onboarding_done = True
    db.commit()
    return RedirectResponse("/dashboard", status_code=302)


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

    # Para el modal de nuevo turno en dashboard
    all_patients = db.query(models.Patient).filter_by(clinic_id=clinic.id, active=True).order_by(models.Patient.name).all()
    all_professionals = db.query(models.Professional).filter_by(clinic_id=clinic.id, active=True).order_by(models.Professional.name).all()
    total_professionals = len(all_professionals)

    # Label de fecha para el header
    DAYS_ES = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
    MONTHS_ES = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]
    today_dt = date.today()
    today_label = f"{DAYS_ES[today_dt.weekday()].capitalize()} {today_dt.day} de {MONTHS_ES[today_dt.month - 1]}, {today_dt.year}"

    # Checklist de configuración
    setup_steps = [
        {"label": "Cuenta creada", "done": True},
        {"label": "Datos de la clínica", "done": bool(clinic.phone or clinic.address)},
        {"label": "Primer profesional", "done": total_professionals > 0},
        {"label": "WhatsApp conectado", "done": bool(clinic.wa_phone_id and clinic.wa_token)},
    ]
    setup_done = all(s["done"] for s in setup_steps)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "clinic": clinic,
        "today": today,
        "today_label": today_label,
        "todays_appointments": todays_appointments,
        "tomorrow_appointments": tomorrow_appointments,
        "total_patients": total_patients,
        "total_today": total_today,
        "total_professionals": total_professionals,
        "pending_today": pending_today,
        "confirmed_today": confirmed_today,
        "recent_patients": recent_patients,
        "week_dates": week_dates,
        "week_counts": week_counts,
        "all_patients": all_patients,
        "all_professionals": all_professionals,
        "setup_steps": setup_steps,
        "setup_done": setup_done,
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
    professional_filter: str = "",
    specialty_filter: str = "",
    view: str = "day",
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    if not date_filter:
        date_filter = date.today().isoformat()

    base_date = date.fromisoformat(date_filter)
    query = db.query(models.Appointment).filter_by(clinic_id=clinic.id)

    # Rango de fechas según vista
    if view == "week":
        week_start = base_date - timedelta(days=base_date.weekday())
        week_end   = week_start + timedelta(days=6)
        query = query.filter(
            models.Appointment.date >= week_start.isoformat(),
            models.Appointment.date <= week_end.isoformat(),
        )
        range_label = f"{week_start.strftime('%d/%m')} – {week_end.strftime('%d/%m/%Y')}"
    elif view == "month":
        import calendar as _cal
        last_day = _cal.monthrange(base_date.year, base_date.month)[1]
        month_start = base_date.replace(day=1)
        month_end   = base_date.replace(day=last_day)
        query = query.filter(
            models.Appointment.date >= month_start.isoformat(),
            models.Appointment.date <= month_end.isoformat(),
        )
        MESES = ["","Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
        range_label = f"{MESES[base_date.month]} {base_date.year}"
    else:
        query = query.filter(models.Appointment.date == date_filter)
        range_label = date_filter

    if status_filter:
        query = query.filter(models.Appointment.status == status_filter)
    if professional_filter:
        query = query.filter(models.Appointment.professional_id == int(professional_filter))

    appointments = query.order_by(models.Appointment.date, models.Appointment.time).all()

    if specialty_filter:
        appointments = [a for a in appointments if a.professional and a.professional.specialty == specialty_filter]

    # Agrupar por fecha (para uso en vistas)
    from collections import defaultdict
    grouped = defaultdict(list)
    for a in appointments:
        grouped[str(a.date)].append(a)

    DIAS_CORTO = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"]
    MESES_CORTO = ["","Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]

    # Vista SEMANA: 7 columnas con los días
    if view == "week":
        week_days = []
        for i in range(7):
            d = week_start + timedelta(days=i)
            d_str = d.isoformat()
            week_days.append({
                "date": d_str,
                "label": DIAS_CORTO[d.weekday()],
                "num": d.day,
                "appts": grouped.get(d_str, []),
                "is_today": d == date.today(),
            })
    else:
        week_days = []

    # Vista MES: grilla de calendario empezando en domingo
    if view == "month":
        import calendar as _cal2
        last_day_num = _cal2.monthrange(base_date.year, base_date.month)[1]
        first_day = base_date.replace(day=1)
        first_offset = (first_day.weekday() + 1) % 7  # Dom=0...Sáb=6
        all_cells = [None] * first_offset
        for day_num in range(1, last_day_num + 1):
            d = date(base_date.year, base_date.month, day_num)
            d_str = d.isoformat()
            all_cells.append({
                "day": day_num,
                "date": d_str,
                "appts": grouped.get(d_str, []),
                "is_today": d == date.today(),
            })
        while len(all_cells) % 7 != 0:
            all_cells.append(None)
        calendar_weeks = [all_cells[i:i+7] for i in range(0, len(all_cells), 7)]
    else:
        calendar_weeks = []

    patients      = db.query(models.Patient).filter_by(clinic_id=clinic.id, active=True).order_by(models.Patient.name).all()
    professionals = db.query(models.Professional).filter_by(clinic_id=clinic.id, active=True).order_by(models.Professional.name).all()
    specialties   = sorted(set(p.specialty for p in professionals if p.specialty))

    return templates.TemplateResponse("appointments.html", {
        "request": request,
        "clinic": clinic,
        "appointments": appointments,
        "week_days": week_days,
        "calendar_weeks": calendar_weeks,
        "patients": patients,
        "professionals": professionals,
        "specialties": specialties,
        "date_filter": date_filter,
        "status_filter": status_filter,
        "professional_filter": professional_filter,
        "specialty_filter": specialty_filter,
        "view": view,
        "range_label": range_label,
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
    redirect_to: str = Form(""),
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    if not _check_appointment_limit(clinic, db):
        return RedirectResponse("/pricing?limit=appointments", status_code=302)

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
    if redirect_to and redirect_to.startswith("/"):
        return RedirectResponse(redirect_to, status_code=302)
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
    if not _check_professional_limit(clinic, db):
        return RedirectResponse("/pricing?limit=professionals", status_code=302)

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


@app.post("/professionals/{prof_id}/schedule")
def professional_schedule(
    prof_id: int,
    work_days: str = Form(""),
    work_start: str = Form("09:00"),
    work_end: str = Form("18:00"),
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    prof = db.query(models.Professional).filter_by(id=prof_id, clinic_id=clinic.id).first()
    if prof:
        prof.work_days  = work_days
        prof.work_start = work_start
        prof.work_end   = work_end
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


@app.post("/reminders/send-all")
def send_all_reminders(
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    """Envía todos los recordatorios pendientes para mañana."""
    sent = reminders_module.send_reminders()
    return RedirectResponse(f"/reminders?sent={sent}", status_code=302)


@app.post("/reminders/{appt_id}/send")
def send_reminder(
    appt_id: int,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    """Envía recordatorio individual por WhatsApp."""
    import os as _osr
    import httpx as _httpx
    appt = db.query(models.Appointment).filter_by(id=appt_id, clinic_id=clinic.id).first()
    if appt and appt.patient and appt.patient.phone:
        wa_token    = clinic.wa_token    or _osr.environ.get("WHATSAPP_TOKEN", "")
        wa_phone_id = clinic.wa_phone_id or _osr.environ.get("WHATSAPP_PHONE_ID", "")
        if wa_token and wa_phone_id:
            prof_name = appt.professional.name if appt.professional else "el profesional"
            msg = (
                f"👋 Hola {appt.patient.name}, te recordamos tu turno en *{clinic.name}*:\n\n"
                f"📅 Mañana, {appt.date}\n"
                f"⏰ {appt.time} hs\n"
                f"👨‍⚕️ {prof_name}\n\n"
                f"Si necesitás cancelar o reprogramar, respondé este mensaje."
            )
            phone = reminders_module._normalizar_phone(appt.patient.phone)
            try:
                _httpx.post(
                    f"https://graph.facebook.com/v21.0/{wa_phone_id}/messages",
                    headers={"Authorization": f"Bearer {wa_token}", "Content-Type": "application/json"},
                    json={"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": msg}},
                    timeout=10,
                )
            except Exception:
                pass
        appt.reminder_sent = True
        db.commit()
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
    webhook_url = str(request.base_url).rstrip("/") + f"/webhook/whatsapp/{clinic.id}"
    # Credenciales: per-clinic primero, fallback a env vars
    wa_phone_id = clinic.wa_phone_id or _os2.environ.get("WHATSAPP_PHONE_ID", "")
    wa_token = clinic.wa_token or _os2.environ.get("WHATSAPP_TOKEN", "")
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
    wa_phone_id: str = Form(""),
    wa_token: str = Form(""),
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    clinic.name     = name
    clinic.phone    = phone
    clinic.address  = address
    clinic.whatsapp = whatsapp
    if wa_phone_id.strip():
        clinic.wa_phone_id = wa_phone_id.strip()
    # Only update token if it's a real token (not the placeholder text)
    if wa_token.strip() and wa_token.strip() != "configurado":
        clinic.wa_token = wa_token.strip()
    db.commit()
    return RedirectResponse("/settings?saved=1", status_code=302)


# ═══════════════════════════════════════════════════════════════════════════════
# ESTADÍSTICAS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/statistics", response_class=HTMLResponse)
def statistics_page(
    request: Request,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    from calendar import month_abbr
    all_appts = db.query(models.Appointment).filter_by(clinic_id=clinic.id).all()
    total = len(all_appts)
    completed = sum(1 for a in all_appts if a.status == "completed")
    cancelled = sum(1 for a in all_appts if a.status == "cancelled")
    pending_confirmed = total - completed - cancelled
    presentismo = round(completed * 100 / total) if total else 0
    ausentismo  = round(cancelled * 100 / total) if total else 0
    total_patients = db.query(models.Patient).filter_by(clinic_id=clinic.id, active=True).count()

    # Turnos por mes (últimos 4 meses)
    today = date.today()
    months_data = []
    for i in range(3, -1, -1):
        m = (today.month - i - 1) % 12 + 1
        y = today.year - ((today.month - i - 1) // 12)
        def appt_month(a, m=m, y=y):
            try:
                d = a.date if isinstance(a.date, date) else date.fromisoformat(str(a.date))
                return d.month == m and d.year == y
            except Exception:
                return False
        cnt  = sum(1 for a in all_appts if appt_month(a))
        comp = sum(1 for a in all_appts if appt_month(a) and a.status == "completed")
        months_data.append({"label": month_abbr[m], "total": cnt, "completed": comp})

    # Rendimiento por profesional
    professionals = db.query(models.Professional).filter_by(clinic_id=clinic.id, active=True).all()
    prof_stats = []
    for p in professionals:
        p_appts = [a for a in all_appts if a.professional_id == p.id]
        p_total = len(p_appts)
        p_comp  = sum(1 for a in p_appts if a.status == "completed")
        if p_total:
            prof_stats.append({
                "prof": p,
                "total": p_total,
                "completed": p_comp,
                "pct": round(p_comp * 100 / p_total),
            })
    prof_stats.sort(key=lambda x: x["pct"], reverse=True)

    # Turnos por especialidad
    from collections import Counter
    spec_counts = Counter()
    spec_colors = {}
    for a in all_appts:
        if a.professional and a.professional.specialty:
            sp = a.professional.specialty
            spec_counts[sp] += 1
            spec_colors[sp] = a.professional.color
    spec_total = sum(spec_counts.values()) or 1
    specialty_stats = [
        {"name": sp, "count": cnt, "pct": round(cnt * 100 / spec_total), "color": spec_colors.get(sp, "#3B82F6")}
        for sp, cnt in spec_counts.most_common()
    ]

    # Pacientes por obra social
    patients_all = db.query(models.Patient).filter_by(clinic_id=clinic.id, active=True).all()
    ins_counts = Counter()
    for p in patients_all:
        ins = (p.insurance or "").strip() or "Particular"
        ins_counts[ins] += 1
    ins_total = sum(ins_counts.values()) or 1
    INS_COLORS = ["#8B5CF6","#3B82F6","#06B6D4","#10B981","#F59E0B","#6B7280","#EF4444","#EC4899","#14B8A6","#F97316"]
    insurance_stats = [
        {"name": ins, "count": cnt, "pct": round(cnt * 100 / ins_total), "color": INS_COLORS[i % len(INS_COLORS)]}
        for i, (ins, cnt) in enumerate(ins_counts.most_common())
    ]

    return templates.TemplateResponse("statistics.html", {
        "request": request,
        "clinic": clinic,
        "total": total,
        "completed": completed,
        "cancelled": cancelled,
        "pending_confirmed": pending_confirmed,
        "presentismo": presentismo,
        "ausentismo": ausentismo,
        "total_patients": total_patients,
        "months_data": months_data,
        "prof_stats": prof_stats,
        "specialty_stats": specialty_stats,
        "insurance_stats": insurance_stats,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# BOT WHATSAPP (panel + simulador)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/bot", response_class=HTMLResponse)
def bot_page(
    request: Request,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    import os as _os3
    wa_token = _os3.environ.get("WHATSAPP_TOKEN", "")
    wa_phone_id = _os3.environ.get("WHATSAPP_PHONE_ID", "")
    # Stats del bot: turnos creados via bot (los que tienen source='bot' si existe, si no 0)
    all_appts = db.query(models.Appointment).filter_by(clinic_id=clinic.id).all()
    bot_appts = [a for a in all_appts if getattr(a, 'source', None) == 'bot']
    return templates.TemplateResponse("bot.html", {
        "request": request,
        "clinic": clinic,
        "bot_active": bool(wa_token and wa_phone_id),
        "wa_phone_id": wa_phone_id,
        "bot_appts": len(bot_appts),
        "total_appts": len(all_appts),
    })


@app.post("/bot/chat")
async def bot_chat(
    request: Request,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    data = await request.json()
    message = data.get("message", "")
    phone = f"sim_{clinic.id}_preview"
    reply = chatbot.process_message(phone, message, db, clinic.id)
    return JSONResponse({"reply": reply})


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

    # Usar credenciales por clínica, con fallback a env vars globales
    wa_token    = clinic_obj.wa_token    or _os.environ.get("WHATSAPP_TOKEN", "")
    wa_phone_id = clinic_obj.wa_phone_id or _os.environ.get("WHATSAPP_PHONE_ID", "")
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

# Límites por plan (None = ilimitado)
PLAN_LIMITS = {
    "free":    {"professionals": 1, "monthly_appointments": 30},
    "starter": {"professionals": 1, "monthly_appointments": 100},
    "pro":     {"professionals": 5, "monthly_appointments": None},
    "clinica": {"professionals": None, "monthly_appointments": None},
}

def _check_professional_limit(clinic, db) -> bool:
    plan = clinic.plan or "free"
    limit = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])["professionals"]
    if limit is None:
        return True
    count = db.query(models.Professional).filter_by(clinic_id=clinic.id, active=True).count()
    return count < limit

def _check_appointment_limit(clinic, db) -> bool:
    plan = clinic.plan or "free"
    limit = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])["monthly_appointments"]
    if limit is None:
        return True
    from datetime import date as _date
    today = _date.today()
    month_start = today.replace(day=1).isoformat()
    count = db.query(models.Appointment).filter(
        models.Appointment.clinic_id == clinic.id,
        models.Appointment.date >= month_start,
        models.Appointment.status != "cancelled",
    ).count()
    return count < limit


@app.get("/pricing", response_class=HTMLResponse)
def pricing_page(
    request: Request,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    return templates.TemplateResponse("pricing.html", {
        "request": request,
        "clinic":  clinic,
        "plans":   pay_module.PLANS,
    })


@app.get("/pricing", response_class=HTMLResponse)
def pricing_page(
    request: Request,
    db: Session = Depends(database.get_db),
    clinic: models.Clinic = Depends(auth_module.get_current_clinic),
):
    """Página de planes y precios."""
    return templates.TemplateResponse("pricing.html", {
        "request": request,
        "clinic":  clinic,
        "plans":   pay_module.PLANS,
    })


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
        if clinic and result.get("plan") in VALID_PLANS:
            clinic.plan = result["plan"]
            db.commit()
            print(f"[MP Webhook] Clínica {clinic.id} → plan {result['plan']}")

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
