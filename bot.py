"""
Botk2-IA — Motor del Chatbot de WhatsApp
============================================
Maneja conversaciones con pacientes por WhatsApp para sacar, cancelar
y consultar turnos de forma totalmente automática.

Funciona como una "máquina de estados": cada número de teléfono tiene
su propio estado de conversación guardado en memoria.

Estados:
  INICIO           → Saludo y menú principal
  MENU             → Esperando que elija 1, 2 o 3
  ESPECIALIDAD_LISTA → Eligiendo especialidad (Odontología, Kinesiología, etc.)
  PROF_LISTA       → Eligiendo profesional (solo si hay más de uno en la especialidad)
  FECHA_LISTA      → Eligiendo fecha disponible
  HORA_LISTA       → Eligiendo hora disponible
  CONFIRMAR        → Confirmando el turno
  CANCELAR_LISTA   → Eligiendo turno a cancelar
  NOMBRE           → Pidiendo nombre (paciente nuevo)
"""

from datetime import date, timedelta, datetime
from sqlalchemy.orm import Session
import models

# ── Sesiones en memoria ──────────────────────────────────────────────────────
# Clave: número de teléfono (ej: "5491112345678")
# Valor: dict con estado y datos temporales de la conversación
_sessions: dict[str, dict] = {}

TIMEOUT_MINUTOS = 30  # sesión expira si no hay actividad


def _get_session(phone: str) -> dict:
    now = datetime.now()
    s = _sessions.get(phone)
    if s is None:
        s = _new_session()
        _sessions[phone] = s
    else:
        # Expirar sesión inactiva
        if (now - s["last_activity"]).seconds > TIMEOUT_MINUTOS * 60:
            s = _new_session()
            _sessions[phone] = s
    s["last_activity"] = now
    return s


def _new_session() -> dict:
    return {
        "state": "INICIO",
        "last_activity": datetime.now(),
        "clinic_id": None,
        "patient_id": None,
        "patient_name": None,
        "specialty_name": None,     # especialidad elegida (Odontología, etc.)
        "professional_id": None,
        "professional_name": None,
        "fecha": None,
        "hora": None,
        "opciones": {},   # mapeo número → valor para los menús
    }


def _reset(phone: str):
    _sessions[phone] = _new_session()


# ── Helpers de disponibilidad ────────────────────────────────────────────────

def _get_horas_disponibles(db: Session, clinic_id: int, professional_id: int, fecha: str) -> list[str]:
    """Devuelve lista de horas disponibles según el horario configurado del profesional."""
    prof = db.query(models.Professional).filter_by(id=professional_id).first()
    if not prof:
        return []

    # Verificar que el día de la semana esté habilitado (0=Lun, 6=Dom)
    fecha_date = date.fromisoformat(fecha)
    dia_semana = str(fecha_date.weekday())
    dias_habilitados = (prof.work_days or "0,1,2,3,4").split(",")
    if dia_semana not in dias_habilitados:
        return []

    # Horas ocupadas ese día
    ocupados = set(
        a.time for a in db.query(models.Appointment).filter(
            models.Appointment.clinic_id == clinic_id,
            models.Appointment.professional_id == professional_id,
            models.Appointment.date == fecha,
            models.Appointment.status.in_(["pending", "confirmed"]),
        ).all()
    )

    # Generar grilla según horario del profesional, cada 30 minutos
    work_start = prof.work_start or "09:00"
    work_end   = prof.work_end   or "18:00"
    sh, sm = map(int, work_start.split(":"))
    eh, em = map(int, work_end.split(":"))
    start_mins = sh * 60 + sm
    end_mins   = eh * 60 + em

    horas = []
    cur = start_mins
    while cur < end_mins:
        slot = f"{cur // 60:02d}:{cur % 60:02d}"
        if slot not in ocupados:
            horas.append(slot)
        cur += 30
    return horas


def _get_fechas_disponibles(db: Session, clinic_id: int, professional_id: int, dias: int = 7) -> list[str]:
    """Devuelve días con al menos un horario libre en los próximos N días."""
    disponibles = []
    hoy = date.today()
    for i in range(1, dias + 1):
        d = (hoy + timedelta(days=i)).isoformat()
        horas = _get_horas_disponibles(db, clinic_id, professional_id, d)
        if horas:
            disponibles.append(d)
    return disponibles


def _fecha_legible(fecha_iso: str) -> str:
    """'2025-01-27' → 'Lunes 27/01'"""
    dias = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    d = date.fromisoformat(fecha_iso)
    nombre_dia = dias[d.weekday()]
    return f"{nombre_dia} {d.day:02d}/{d.month:02d}"


def _find_or_create_patient(db: Session, clinic_id: int, phone: str, name: str) -> models.Patient:
    """Busca el paciente por teléfono, si no existe lo crea."""
    # Normalizar teléfono
    clean = phone.replace("whatsapp:", "").replace("+", "").strip()
    patient = db.query(models.Patient).filter(
        models.Patient.clinic_id == clinic_id,
        models.Patient.phone.contains(clean[-8:]),  # últimos 8 dígitos
    ).first()
    if not patient:
        patient = models.Patient(
            clinic_id=clinic_id,
            name=name,
            phone=phone.replace("whatsapp:", ""),
            active=True,
        )
        db.add(patient)
        db.commit()
        db.refresh(patient)
    return patient


# ── Procesador principal ─────────────────────────────────────────────────────

def process_message(phone: str, text: str, db: Session, clinic_id: int) -> str:
    """
    Recibe un mensaje del paciente y devuelve la respuesta del bot.

    Args:
        phone:     número de teléfono del paciente (ej: "whatsapp:+5491112345678")
        text:      texto del mensaje
        db:        sesión de base de datos
        clinic_id: ID de la clínica

    Returns:
        Texto de la respuesta a enviar por WhatsApp
    """
    # Normalizar entrada
    text = text.strip()
    text_lower = text.lower()
    s = _get_session(phone)
    s["clinic_id"] = clinic_id

    clinic = db.query(models.Clinic).filter_by(id=clinic_id).first()
    clinic_name = clinic.name if clinic else "la clínica"

    # Palabras clave para reiniciar siempre
    if text_lower in ["hola", "hi", "buenas", "buenos días", "buen dia", "inicio", "menu", "menú", "0", "cancelar todo", "empezar"]:
        _reset(phone)
        s = _get_session(phone)
        s["clinic_id"] = clinic_id

    # ── ESTADO: INICIO ────────────────────────────────────────────────────────
    if s["state"] == "INICIO":
        s["state"] = "MENU"
        return (
            f"👋 ¡Hola! Soy el asistente de *{clinic_name}*.\n\n"
            f"¿En qué te puedo ayudar?\n\n"
            f"1️⃣ Sacar un turno\n"
            f"2️⃣ Cancelar un turno\n"
            f"3️⃣ Ver mis turnos\n\n"
            f"Respondé con el número de la opción 👆"
        )

    # ── ESTADO: MENÚ PRINCIPAL ────────────────────────────────────────────────
    if s["state"] == "MENU":
        if text == "1":
            # Obtener especialidades distintas de los profesionales activos
            profesionales = db.query(models.Professional).filter_by(
                clinic_id=clinic_id, active=True
            ).all()
            if not profesionales:
                return "😕 No hay profesionales disponibles por ahora. Llamanos al teléfono de la clínica."

            # Agrupar por especialidad (conservar orden de aparición)
            especialidades = []
            seen = set()
            for p in profesionales:
                esp = p.specialty or "General"
                if esp not in seen:
                    especialidades.append(esp)
                    seen.add(esp)

            # Si solo hay una especialidad, saltear ese paso
            if len(especialidades) == 1:
                s["specialty_name"] = especialidades[0]
                return _resolver_profesionales(db, s, clinic_id, profesionales)

            # Mostrar lista de especialidades
            s["state"] = "ESPECIALIDAD_LISTA"
            s["opciones"] = {}
            msg = "🏥 ¿Qué especialidad necesitás?\n\n"
            for i, esp in enumerate(especialidades, 1):
                s["opciones"][str(i)] = {"specialty": esp}
                msg += f"{i}️⃣ {esp}\n"
            msg += "\nRespondé con el número de la especialidad 👆"
            return msg

        elif text == "2":
            s["state"] = "CANCELAR_LISTA"
            return _mostrar_turnos_paciente(phone, db, clinic_id, s)

        elif text == "3":
            return _mostrar_turnos_paciente(phone, db, clinic_id, s, solo_ver=True)

        else:
            return (
                "No entendí 😅 Por favor elegí una opción:\n\n"
                "1️⃣ Sacar un turno\n"
                "2️⃣ Cancelar un turno\n"
                "3️⃣ Ver mis turnos"
            )

    # ── ESTADO: ELEGIR ESPECIALIDAD ───────────────────────────────────────────
    if s["state"] == "ESPECIALIDAD_LISTA":
        opcion = s["opciones"].get(text)
        if not opcion:
            return f"Por favor elegí un número válido del 1 al {len(s['opciones'])} 👆"
        s["specialty_name"] = opcion["specialty"]
        # Filtrar profesionales de esa especialidad
        profesionales = db.query(models.Professional).filter_by(
            clinic_id=clinic_id, active=True
        ).filter(
            models.Professional.specialty == s["specialty_name"]
        ).all()
        if not profesionales:
            # Fallback si 'General' u otro match raro
            profesionales = db.query(models.Professional).filter_by(
                clinic_id=clinic_id, active=True
            ).all()
        return _resolver_profesionales(db, s, clinic_id, profesionales)

    # ── ESTADO: ELEGIR PROFESIONAL ────────────────────────────────────────────
    if s["state"] == "PROF_LISTA":
        opcion = s["opciones"].get(text)
        if not opcion:
            return f"Por favor elegí un número válido del 1 al {len(s['opciones'])} 👆"
        s["professional_id"] = opcion["id"]
        s["professional_name"] = opcion["name"]
        s["state"] = "FECHA_LISTA"
        return _mostrar_fechas(db, s, clinic_id)

    # ── ESTADO: ELEGIR FECHA ──────────────────────────────────────────────────
    if s["state"] == "FECHA_LISTA":
        opcion = s["opciones"].get(text)
        if not opcion:
            return f"Por favor elegí un número válido del 1 al {len(s['opciones'])} 👆"
        s["fecha"] = opcion["fecha"]
        s["state"] = "HORA_LISTA"
        return _mostrar_horas(db, s, clinic_id)

    # ── ESTADO: ELEGIR HORA ───────────────────────────────────────────────────
    if s["state"] == "HORA_LISTA":
        opcion = s["opciones"].get(text)
        if not opcion:
            return f"Por favor elegí un número válido del 1 al {len(s['opciones'])} 👆"
        s["hora"] = opcion["hora"]

        # Verificar si el paciente ya existe
        clean = phone.replace("whatsapp:", "").replace("+", "").strip()
        patient = db.query(models.Patient).filter(
            models.Patient.clinic_id == clinic_id,
            models.Patient.phone.contains(clean[-8:]),
        ).first()

        if patient:
            s["patient_id"] = patient.id
            s["patient_name"] = patient.name
            s["state"] = "CONFIRMAR"
            return _mostrar_confirmacion(s)
        else:
            s["state"] = "NOMBRE"
            return "📝 Para registrarte, ¿cuál es tu nombre completo?"

    # ── ESTADO: PEDIR NOMBRE (paciente nuevo) ─────────────────────────────────
    if s["state"] == "NOMBRE":
        if len(text) < 3:
            return "Por favor ingresá tu nombre completo 😊"
        s["patient_name"] = text.title()
        s["state"] = "CONFIRMAR"
        return _mostrar_confirmacion(s)

    # ── ESTADO: CONFIRMAR TURNO ───────────────────────────────────────────────
    if s["state"] == "CONFIRMAR":
        if text_lower in ["si", "sí", "s", "1", "confirmar", "ok", "dale", "sí confirmo"]:
            # Crear o encontrar paciente
            patient = _find_or_create_patient(db, clinic_id, phone, s["patient_name"])
            s["patient_id"] = patient.id

            # Verificar que el slot sigue disponible
            horas_libres = _get_horas_disponibles(db, clinic_id, s["professional_id"], s["fecha"])
            if s["hora"] not in horas_libres:
                s["state"] = "HORA_LISTA"
                return (
                    "⚠️ Lo siento, ese horario se acaba de ocupar. Por favor elegí otro:\n\n"
                    + _mostrar_horas(db, s, clinic_id)
                )

            # Crear el turno
            appt = models.Appointment(
                clinic_id=clinic_id,
                patient_id=patient.id,
                professional_id=s["professional_id"],
                date=s["fecha"],
                time=s["hora"],
                duration_min=30,
                reason="Turno solicitado por WhatsApp",
                status="confirmed",
            )
            db.add(appt)
            db.commit()

            fecha_leg = _fecha_legible(s["fecha"])
            nombre = s["patient_name"]
            prof = s["professional_name"]

            _reset(phone)

            return (
                f"✅ *¡Turno confirmado, {nombre}!*\n\n"
                f"📅 {fecha_leg}\n"
                f"⏰ {s['hora']} hs\n"
                f"👨‍⚕️ {prof}\n"
                f"🏥 {clinic_name}\n\n"
                f"Te vamos a mandar un recordatorio el día anterior.\n"
                f"Si necesitás cancelar, escribí *cancelar* en cualquier momento."
            )

        elif text_lower in ["no", "n", "2", "cambiar", "volver"]:
            s["state"] = "FECHA_LISTA"
            s["hora"] = None
            return _mostrar_fechas(db, s, clinic_id)

        else:
            return (
                f"¿Confirmamos el turno?\n\n"
                f"{_resumen_turno(s)}\n\n"
                f"Respondé *SI* para confirmar o *NO* para elegir otra fecha."
            )

    # ── ESTADO: CANCELAR TURNO ────────────────────────────────────────────────
    if s["state"] == "CANCELAR_LISTA":
        if text_lower in ["0", "volver", "no", "ninguno"]:
            _reset(phone)
            s = _get_session(phone)
            s["clinic_id"] = clinic_id
            s["state"] = "MENU"
            return "De acuerdo, no se canceló ningún turno. ¿En qué más te puedo ayudar?\n\n1️⃣ Sacar un turno\n2️⃣ Cancelar un turno\n3️⃣ Ver mis turnos"

        opcion = s["opciones"].get(text)
        if not opcion:
            return f"Por favor elegí un número válido o escribí *0* para volver."

        appt = db.query(models.Appointment).filter_by(id=opcion["appt_id"]).first()
        if appt:
            appt.status = "cancelled"
            db.commit()
            fecha_leg = _fecha_legible(appt.date)
            _reset(phone)
            return (
                f"✅ Turno cancelado correctamente.\n\n"
                f"📅 {fecha_leg} · ⏰ {appt.time} hs\n\n"
                f"Si querés sacar un nuevo turno, escribí *hola* cuando quieras."
            )
        return "No encontré ese turno. Escribí *hola* para empezar de nuevo."

    # Fallback
    _reset(phone)
    return (
        "No entendí tu mensaje 😅\n"
        "Escribí *hola* para empezar de nuevo."
    )


# ── Helpers de presentación ───────────────────────────────────────────────────

def _resolver_profesionales(db: Session, s: dict, clinic_id: int, profesionales: list) -> str:
    """
    Dado un listado de profesionales (ya filtrados por especialidad):
    - Si solo hay 1 → asignarlo automáticamente y ir a fechas
    - Si hay más de 1 → mostrar la lista para que el paciente elija
    """
    if not profesionales:
        s["state"] = "MENU"
        return "😕 No hay profesionales disponibles para esa especialidad. Llamanos para coordinar."

    if len(profesionales) == 1:
        s["professional_id"] = profesionales[0].id
        s["professional_name"] = profesionales[0].name
        s["state"] = "FECHA_LISTA"
        return _mostrar_fechas(db, s, clinic_id)

    # Más de uno → el paciente elige
    s["state"] = "PROF_LISTA"
    s["opciones"] = {}
    esp = s.get("specialty_name", "")
    msg = f"👨‍⚕️ ¿Con qué profesional de *{esp}* querés el turno?\n\n"
    for i, p in enumerate(profesionales, 1):
        s["opciones"][str(i)] = {"id": p.id, "name": p.name}
        msg += f"{i}️⃣ {p.name}\n"
    msg += "\nRespondé con el número."
    return msg


def _mostrar_fechas(db: Session, s: dict, clinic_id: int) -> str:
    fechas = _get_fechas_disponibles(db, clinic_id, s["professional_id"])
    esp = s.get("specialty_name") or ""
    prof = s.get("professional_name") or "el profesional"
    if not fechas:
        s["state"] = "MENU"
        return (
            f"😕 No hay turnos disponibles con *{prof}* ({esp}) en los próximos 7 días.\n"
            f"Llamanos para coordinar una fecha especial."
        )
    s["opciones"] = {}
    msg = f"📅 Fechas disponibles con *{prof}* ({esp}):\n\n"
    for i, fecha in enumerate(fechas, 1):
        s["opciones"][str(i)] = {"fecha": fecha}
        msg += f"{i}️⃣ {_fecha_legible(fecha)}\n"
    msg += "\nElegí el número de la fecha que preferís 👆"
    return msg


def _mostrar_horas(db: Session, s: dict, clinic_id: int) -> str:
    horas = _get_horas_disponibles(db, clinic_id, s["professional_id"], s["fecha"])
    if not horas:
        s["state"] = "FECHA_LISTA"
        return (
            f"😕 No quedan horarios libres para {_fecha_legible(s['fecha'])}.\n"
            f"Por favor elegí otra fecha:"
        ) + "\n\n" + _mostrar_fechas(db, s, clinic_id)

    s["opciones"] = {}
    msg = f"⏰ Horarios disponibles el *{_fecha_legible(s['fecha'])}*:\n\n"
    for i, hora in enumerate(horas, 1):
        s["opciones"][str(i)] = {"hora": hora}
        msg += f"🔵 *{i}.* {hora} hs\n"
    msg += "\nElegí el número del horario que preferís 👆"
    return msg


def _mostrar_confirmacion(s: dict) -> str:
    return (
        f"Por favor confirmá tu turno:\n\n"
        f"{_resumen_turno(s)}\n\n"
        f"Respondé *SI* para confirmar ✅\n"
        f"Respondé *NO* para elegir otra fecha ❌"
    )


def _resumen_turno(s: dict) -> str:
    esp = s.get("specialty_name")
    prof_line = f"👨‍⚕️ {s['professional_name']}"
    if esp:
        prof_line += f" ({esp})"
    return (
        f"👤 *{s['patient_name']}*\n"
        f"{prof_line}\n"
        f"📅 {_fecha_legible(s['fecha'])}\n"
        f"⏰ {s['hora']} hs"
    )


def _mostrar_turnos_paciente(phone: str, db: Session, clinic_id: int, s: dict, solo_ver: bool = False) -> str:
    clean = phone.replace("whatsapp:", "").replace("+", "").strip()
    patient = db.query(models.Patient).filter(
        models.Patient.clinic_id == clinic_id,
        models.Patient.phone.contains(clean[-8:]),
    ).first()

    if not patient:
        if solo_ver:
            return "📋 No encontré turnos registrados con tu número.\n\nEscribí *1* para sacar un turno."
        else:
            return "📋 No encontré turnos activos con tu número.\n\nEscribí *1* para sacar un turno."

    hoy = date.today().isoformat()
    turnos = db.query(models.Appointment).filter(
        models.Appointment.patient_id == patient.id,
        models.Appointment.date >= hoy,
        models.Appointment.status.in_(["pending", "confirmed"]),
    ).order_by(models.Appointment.date, models.Appointment.time).all()

    if not turnos:
        if solo_ver:
            return "📋 No tenés turnos próximos agendados.\n\nEscribí *1* para sacar uno."
        else:
            return "📋 No tenés turnos activos para cancelar.\n\nEscribí *1* para sacar un turno."

    if solo_ver:
        msg = f"📋 *Tus próximos turnos, {patient.name}:*\n\n"
        for t in turnos:
            prof = t.professional.name if t.professional else "Profesional"
            msg += f"📅 {_fecha_legible(t.date)} · ⏰ {t.time} hs\n"
            msg += f"   👨‍⚕️ {prof}\n\n"
        msg += "Escribí *hola* si necesitás algo más."
        s["state"] = "INICIO"
        return msg
    else:
        s["opciones"] = {}
        msg = f"¿Cuál turno querés cancelar?\n\n"
        for i, t in enumerate(turnos, 1):
            prof = t.professional.name if t.professional else "Profesional"
            s["opciones"][str(i)] = {"appt_id": t.id}
            msg += f"🔵 *{i}.* {_fecha_legible(t.date)} · {t.time} hs · {prof}\n"
        msg += "\n🔵 *0.* Volver sin cancelar\n\nElegí el número del turno a cancelar."
        return msg
