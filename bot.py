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
  REPROGRAMAR_LISTA → Eligiendo turno a reprogramar (después sigue por FECHA/HORA/CONFIRMAR)
  REPETIR_CANTIDAD → Eligiendo cuántas semanas repetir el turno recién sacado
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

SLOT_OPCIONES       = (15, 20, 30, 45, 60)   # duraciones de turno permitidas (minutos)
SLOT_DEFAULT        = 30
DIAS_VENTANA_DEFAULT = 14                    # con cuántos días de anticipación se puede reservar
DIAS_VENTANA_MAX    = 90
FECHAS_POR_PAGINA   = 8
MAX_SEMANAS_REPETIR = 8


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
        "fecha_offset": 0,            # página actual de la lista de fechas
        "reprogramar_appt_id": None,  # turno que se está moviendo (None = turno nuevo)
        "ultimo_turno": None,         # último turno sacado, para ofrecer repetirlo
    }


def _reset(phone: str):
    _sessions[phone] = _new_session()


# ── Helpers de disponibilidad ────────────────────────────────────────────────

def _to_mins(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":")[:2])
    return h * 60 + m


def slot_minutes(prof) -> int:
    """Duración de cada turno del profesional (15, 20, 30, 45 o 60 minutos)."""
    value = getattr(prof, "slot_minutes", None) or SLOT_DEFAULT
    return value if value in SLOT_OPCIONES else SLOT_DEFAULT


def dias_ventana(clinic) -> int:
    """Con cuántos días de anticipación ofrece turnos el bot de esta clínica."""
    value = getattr(clinic, "bot_days_ahead", None) or DIAS_VENTANA_DEFAULT
    return max(1, min(int(value), DIAS_VENTANA_MAX))


def _intervalos_ocupados(db: Session, clinic_id: int, professional_id: int, fecha: str,
                         exclude_appt_id: int = None) -> list[tuple[int, int]]:
    """Intervalos (inicio, fin) en minutos de los turnos activos del profesional ese día."""
    turnos = db.query(models.Appointment).filter(
        models.Appointment.clinic_id == clinic_id,
        models.Appointment.professional_id == professional_id,
        models.Appointment.date == fecha,
        models.Appointment.status.in_(["pending", "confirmed"]),
    ).all()
    ocupados = []
    for a in turnos:
        if exclude_appt_id and a.id == exclude_appt_id:
            continue
        try:
            inicio = _to_mins(a.time)
        except (ValueError, AttributeError):
            continue
        ocupados.append((inicio, inicio + (a.duration_min or SLOT_DEFAULT)))
    return ocupados


def slot_ocupado(db: Session, clinic_id: int, professional_id: int, fecha: str, hora: str,
                 duracion: int, exclude_appt_id: int = None) -> bool:
    """True si el profesional ya tiene un turno que se pisa con ese horario."""
    inicio = _to_mins(hora)
    return any(
        inicio < fin and ini < inicio + duracion
        for ini, fin in _intervalos_ocupados(db, clinic_id, professional_id, fecha, exclude_appt_id)
    )


def _get_horas_disponibles(db: Session, clinic_id: int, professional_id: int, fecha: str,
                           exclude_appt_id: int = None) -> list[str]:
    """Devuelve lista de horas disponibles según el horario y la duración de turno del profesional."""
    prof = db.query(models.Professional).filter_by(id=professional_id).first()
    if not prof:
        return []

    # Verificar que el día de la semana esté habilitado (0=Lun, 6=Dom)
    fecha_date = date.fromisoformat(fecha)
    dia_semana = str(fecha_date.weekday())
    dias_habilitados = (prof.work_days or "0,1,2,3,4").split(",")
    if dia_semana not in dias_habilitados:
        return []

    ocupados = _intervalos_ocupados(db, clinic_id, professional_id, fecha, exclude_appt_id)

    # Generar grilla según horario del profesional y la duración de sus turnos
    start_mins = _to_mins(prof.work_start or "09:00")
    end_mins   = _to_mins(prof.work_end   or "18:00")
    paso = slot_minutes(prof)

    horas = []
    cur = start_mins
    while cur + paso <= end_mins:
        if not any(cur < fin and ini < cur + paso for ini, fin in ocupados):
            horas.append(f"{cur // 60:02d}:{cur % 60:02d}")
        cur += paso
    return horas


def _get_fechas_disponibles(db: Session, clinic_id: int, professional_id: int, dias: int = DIAS_VENTANA_DEFAULT,
                            exclude_appt_id: int = None) -> list[str]:
    """Devuelve días con al menos un horario libre en los próximos N días."""
    disponibles = []
    hoy = date.today()
    for i in range(1, dias + 1):
        d = (hoy + timedelta(days=i)).isoformat()
        horas = _get_horas_disponibles(db, clinic_id, professional_id, d, exclude_appt_id)
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

    # Respuesta al recordatorio: SI confirma, NO cancela — solo en estado INICIO
    if s["state"] == "INICIO" and text_lower in ["si", "sí", "yes", "confirmo", "confirmar", "no", "no puedo", "no voy"]:
        clean = phone.replace("whatsapp:", "").replace("+", "").strip()
        patient_rem = db.query(models.Patient).filter(
            models.Patient.clinic_id == clinic_id,
            models.Patient.phone.contains(clean[-8:]),
        ).first()
        if patient_rem:
            from datetime import timedelta as _td, date as _dt2
            tomorrow = (_dt2.today() + _td(days=1)).isoformat()
            appt_rem = db.query(models.Appointment).filter(
                models.Appointment.patient_id == patient_rem.id,
                models.Appointment.date == tomorrow,
                models.Appointment.status.in_(["pending", "confirmed"]),
            ).order_by(models.Appointment.time).first()
            if appt_rem:
                if text_lower in ["si", "sí", "yes", "confirmo", "confirmar"]:
                    appt_rem.status = "confirmed"
                    db.commit()
                    return (
                        f"✅ ¡Perfecto! Tu turno del {_fecha_legible(appt_rem.date)} "
                        f"a las {appt_rem.time} hs está confirmado.\n\n"
                        f"Te esperamos en *{clinic_name}*. 🏥"
                    )
                else:
                    appt_rem.status = "cancelled"
                    db.commit()
                    return (
                        f"❌ Turno del {_fecha_legible(appt_rem.date)} a las {appt_rem.time} hs cancelado.\n\n"
                        f"Cuando quieras sacar otro turno, escribí *hola*. 😊"
                    )

    # Atajo: información y precios cargados por la clínica (no cambia el paso en el que está)
    if text_lower in ["info", "información", "informacion", "precio", "precios", "valor", "valores", "cuanto sale", "cuánto sale"]:
        return _info_text(db, clinic) + "\n\nEscribí *hola* para volver al menú."

    # Atajo: repetir el turno recién sacado las próximas semanas
    if text_lower in ["repetir", "repetir turno", "varias sesiones"] and s.get("ultimo_turno") and s["state"] == "INICIO":
        s["state"] = "REPETIR_CANTIDAD"
        u = s["ultimo_turno"]
        dia = _fecha_legible(u["fecha"]).split()[0].lower()
        return (
            f"🔁 Puedo reservarte el mismo horario las próximas semanas:\n"
            f"todos los *{dia}* a las *{u['hora']} hs* con *{u['professional_name']}*.\n\n"
            f"¿Cuántas semanas más querés reservar? Respondé con un número del 1 al {MAX_SEMANAS_REPETIR}."
        )

    # Atajo directo: "reprogramar" va directamente a la lista de turnos
    if text_lower in ["reprogramar", "quiero reprogramar", "cambiar turno"] and s["state"] not in ["REPROGRAMAR_LISTA"]:
        _reset(phone)
        s = _get_session(phone)
        s["clinic_id"] = clinic_id
        s["state"] = "REPROGRAMAR_LISTA"
        return _mostrar_turnos_paciente(phone, db, clinic_id, s, solo_ver=False, reprogramar=True)

    if text_lower in ["cancelar", "quiero cancelar", "cancelar turno"] and s["state"] not in ["CANCELAR_LISTA", "CANCELAR_CONFIRMAR"]:
        _reset(phone)
        s = _get_session(phone)
        s["clinic_id"] = clinic_id
        s["state"] = "CANCELAR_LISTA"
        return _mostrar_turnos_paciente(phone, db, clinic_id, s)

    # ── ESTADO: INICIO ────────────────────────────────────────────────────────
    if s["state"] == "INICIO":
        s["state"] = "MENU"
        saludo = (getattr(clinic, "bot_welcome", "") or "").strip() if clinic else ""
        if not saludo:
            saludo = f"👋 ¡Hola! Soy el asistente de *{clinic_name}*."
        return (
            f"{saludo}\n\n"
            f"¿En qué te puedo ayudar?\n\n"
            f"{_opciones_menu(db, clinic)}\n\n"
            f"Respondé con el número de la opción 👆"
        )

    # ── ESTADO: MENÚ PRINCIPAL ────────────────────────────────────────────────
    if s["state"] == "MENU":
        if text == "1":
            return _iniciar_eleccion_profesional(db, s, clinic_id)

        elif text == "2":
            s["state"] = "CANCELAR_LISTA"
            return _mostrar_turnos_paciente(phone, db, clinic_id, s)

        elif text == "3":
            return _mostrar_turnos_paciente(phone, db, clinic_id, s, solo_ver=True)

        elif text == "4":
            s["state"] = "REPROGRAMAR_LISTA"
            return _mostrar_turnos_paciente(phone, db, clinic_id, s, solo_ver=False, reprogramar=True)

        elif text == "5" and _tiene_info(db, clinic):
            return _info_text(db, clinic) + "\n\nRespondé con otra opción del menú o escribí *hola* para verlo de nuevo."

        else:
            return (
                "No entendí 😅 Por favor elegí una opción:\n\n"
                + _opciones_menu(db, clinic)
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
        s["fecha_offset"] = 0
        return _mostrar_fechas(db, s, clinic_id)

    # ── ESTADO: ELEGIR FECHA ──────────────────────────────────────────────────
    if s["state"] == "FECHA_LISTA":
        if text_lower in ["más", "mas", "+", "ver más", "ver mas", "siguiente"]:
            s["fecha_offset"] = s.get("fecha_offset", 0) + FECHAS_POR_PAGINA
            return _mostrar_fechas(db, s, clinic_id)
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

        # Reprogramación: el paciente ya se conoce por el turno original
        if s.get("reprogramar_appt_id"):
            s["state"] = "CONFIRMAR"
            return _mostrar_confirmacion(db, s)

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
            return _mostrar_confirmacion(db, s)
        else:
            s["state"] = "NOMBRE"
            return "📝 Para registrarte, ¿cuál es tu nombre completo?"

    # ── ESTADO: PEDIR NOMBRE (paciente nuevo) ─────────────────────────────────
    if s["state"] == "NOMBRE":
        if len(text) < 3:
            return "Por favor ingresá tu nombre completo 😊"
        s["patient_name"] = text.title()
        s["state"] = "CONFIRMAR"
        return _mostrar_confirmacion(db, s)

    # ── ESTADO: CONFIRMAR TURNO ───────────────────────────────────────────────
    if s["state"] == "CONFIRMAR":
        if text_lower in ["si", "sí", "s", "1", "confirmar", "ok", "dale", "sí confirmo"]:
            reprogramar_id = s.get("reprogramar_appt_id")

            # Verificar que el slot sigue disponible
            horas_libres = _get_horas_disponibles(db, clinic_id, s["professional_id"], s["fecha"], reprogramar_id)
            if s["hora"] not in horas_libres:
                s["state"] = "HORA_LISTA"
                return (
                    "⚠️ Lo siento, ese horario se acaba de ocupar. Por favor elegí otro:\n\n"
                    + _mostrar_horas(db, s, clinic_id)
                )

            prof_obj = db.query(models.Professional).filter_by(id=s["professional_id"]).first()
            duracion = slot_minutes(prof_obj) if prof_obj else SLOT_DEFAULT
            nota = (getattr(clinic, "bot_confirm_note", "") or "").strip() if clinic else ""
            nota_txt = f"\n\n{nota}" if nota else ""

            # Reprogramación: se mueve el turno existente (recién ahora se libera el horario anterior)
            if reprogramar_id:
                appt = db.query(models.Appointment).filter_by(id=reprogramar_id, clinic_id=clinic_id).first()
                if not appt or appt.status not in ("pending", "confirmed"):
                    _reset(phone)
                    return "No encontré el turno a reprogramar. Escribí *hola* para empezar de nuevo."
                appt.professional_id = s["professional_id"]
                appt.date = s["fecha"]
                appt.time = s["hora"]
                appt.duration_min = duracion
                appt.status = "confirmed"
                appt.reminder_sent = False
                db.commit()
                detalle = _resumen_turno(db, s)
                _reset(phone)
                return (
                    f"✅ *¡Turno reprogramado!*\n\n"
                    f"{detalle}\n"
                    f"🏥 {clinic_name}"
                    f"{nota_txt}\n\n"
                    f"Te vamos a mandar un recordatorio el día anterior."
                )

            # Crear o encontrar paciente
            patient = _find_or_create_patient(db, clinic_id, phone, s["patient_name"])
            s["patient_id"] = patient.id

            # Crear el turno
            appt = models.Appointment(
                clinic_id=clinic_id,
                patient_id=patient.id,
                professional_id=s["professional_id"],
                date=s["fecha"],
                time=s["hora"],
                duration_min=duracion,
                reason="Turno solicitado por WhatsApp",
                status="confirmed",
            )
            db.add(appt)
            db.commit()

            nombre = s["patient_name"]
            detalle = _resumen_turno(db, s, con_paciente=False)
            ultimo = {
                "patient_id": patient.id,
                "professional_id": s["professional_id"],
                "professional_name": s["professional_name"],
                "fecha": s["fecha"],
                "hora": s["hora"],
            }

            _reset(phone)
            _sessions[phone]["clinic_id"] = clinic_id
            _sessions[phone]["ultimo_turno"] = ultimo

            return (
                f"✅ *¡Turno confirmado, {nombre}!*\n\n"
                f"{detalle}\n"
                f"🏥 {clinic_name}"
                f"{nota_txt}\n\n"
                f"Te vamos a mandar un recordatorio el día anterior.\n"
                f"Si necesitás cancelar, escribí *cancelar* en cualquier momento.\n\n"
                f"🔁 ¿Son varias sesiones? Escribí *repetir* para reservar este mismo día y horario las próximas semanas."
            )

        elif text_lower in ["no", "n", "2", "cambiar", "volver"]:
            s["state"] = "FECHA_LISTA"
            s["hora"] = None
            s["fecha_offset"] = 0
            return _mostrar_fechas(db, s, clinic_id)

        else:
            return (
                f"¿Confirmamos el turno?\n\n"
                f"{_resumen_turno(db, s)}\n\n"
                f"Respondé *SI* para confirmar o *NO* para elegir otra fecha."
            )

    # ── ESTADO: REPETIR TURNO (sesiones) ──────────────────────────────────────
    if s["state"] == "REPETIR_CANTIDAD":
        u = s.get("ultimo_turno")
        if text_lower in ["no", "n", "ninguna", "cancelar"] or not u:
            _reset(phone)
            return "De acuerdo, no se reservó ningún turno adicional. Escribí *hola* si necesitás algo más."
        if not text.isdigit() or not (1 <= int(text) <= MAX_SEMANAS_REPETIR):
            return f"Por favor respondé con un número del 1 al {MAX_SEMANAS_REPETIR}, o *no* para dejarlo así."

        prof_obj = db.query(models.Professional).filter_by(id=u["professional_id"]).first()
        duracion = slot_minutes(prof_obj) if prof_obj else SLOT_DEFAULT
        base = date.fromisoformat(u["fecha"])
        reservados, no_disponibles = [], []
        for semana in range(1, int(text) + 1):
            fecha = (base + timedelta(days=7 * semana)).isoformat()
            if u["hora"] in _get_horas_disponibles(db, clinic_id, u["professional_id"], fecha):
                db.add(models.Appointment(
                    clinic_id=clinic_id,
                    patient_id=u["patient_id"],
                    professional_id=u["professional_id"],
                    date=fecha,
                    time=u["hora"],
                    duration_min=duracion,
                    reason="Turno solicitado por WhatsApp (sesión repetida)",
                    status="confirmed",
                ))
                db.commit()
                reservados.append(fecha)
            else:
                no_disponibles.append(fecha)
        _reset(phone)

        msg = ""
        if reservados:
            msg += f"✅ *Sesiones reservadas* a las {u['hora']} hs con {u['professional_name']}:\n"
            msg += "".join(f"📅 {_fecha_legible(f)}\n" for f in reservados)
        if no_disponibles:
            msg += ("\n" if msg else "") + "⚠️ Estas fechas no tenían ese horario libre y *no* se reservaron:\n"
            msg += "".join(f"▫️ {_fecha_legible(f)}\n" for f in no_disponibles)
            msg += "Para esas fechas escribí *hola* y elegí otro horario.\n"
        msg += "\nPodés ver todos tus turnos escribiendo *hola* y eligiendo la opción 3."
        return msg

    # ── ESTADO: REPROGRAMAR_LISTA ─────────────────────────────────────────────
    if s["state"] == "REPROGRAMAR_LISTA":
        appt_id = (s.get("opciones") or {}).get(text, {}).get("appt_id")
        if not appt_id:
            return "Por favor elegí un número válido 👆 o escribí *hola* para volver al menú."
        appt = db.query(models.Appointment).filter_by(id=appt_id, clinic_id=clinic_id).first()
        if not appt or appt.status not in ("pending", "confirmed"):
            _reset(phone)
            return "No encontré ese turno. Escribí *hola* para empezar de nuevo."

        # El turno original NO se cancela: se mueve recién cuando el paciente confirma el nuevo horario
        s["reprogramar_appt_id"] = appt.id
        s["patient_id"] = appt.patient_id
        s["patient_name"] = appt.patient.name if appt.patient else ""
        s["fecha_offset"] = 0
        prof = appt.professional
        if prof and prof.active:
            s["professional_id"] = prof.id
            s["professional_name"] = prof.name
            s["specialty_name"] = prof.specialty or "General"
            s["state"] = "FECHA_LISTA"
            return _mostrar_fechas(db, s, clinic_id)
        # Turno sin profesional asignado (o ya no atiende): elegirlo primero
        return (
            f"🔄 *Turno a reprogramar:*\n{_detalle_turno(appt)}\n\n"
            + _iniciar_eleccion_profesional(db, s, clinic_id)
        )

    # ── ESTADO: CANCELAR TURNO ────────────────────────────────────────────────
    if s["state"] == "CANCELAR_LISTA":
        if text_lower in ["0", "volver", "no", "ninguno"]:
            _reset(phone)
            s = _get_session(phone)
            s["clinic_id"] = clinic_id
            s["state"] = "MENU"
            return "De acuerdo, no se canceló ningún turno. ¿En qué más te puedo ayudar?\n\n" + _opciones_menu(db, clinic)

        opcion = s["opciones"].get(text)
        if not opcion:
            return f"Por favor elegí un número válido o escribí *0* para volver."

        appt = db.query(models.Appointment).filter_by(id=opcion["appt_id"], clinic_id=clinic_id).first()
        if appt:
            s["state"] = "CANCELAR_CONFIRMAR"
            s["cancelar_appt_id"] = appt.id
            return (
                f"⚠️ ¿Confirmás que querés cancelar este turno?\n\n"
                f"{_detalle_turno(appt)}\n\n"
                f"Respondé *SI* para cancelar ✅\n"
                f"Respondé *NO* para volver ❌"
            )
        return "No encontré ese turno. Escribí *hola* para empezar de nuevo."

    # ── ESTADO: CONFIRMAR CANCELACIÓN ─────────────────────────────────────────
    if s["state"] == "CANCELAR_CONFIRMAR":
        if text_lower in ["si", "sí", "yes", "confirmar", "confirmo"]:
            appt = db.query(models.Appointment).filter_by(id=s.get("cancelar_appt_id"), clinic_id=clinic_id).first()
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
            _reset(phone)
            return "No encontré el turno. Escribí *hola* para empezar de nuevo."
        else:
            s["state"] = "CANCELAR_LISTA"
            return _mostrar_turnos_paciente(phone, db, clinic_id, s)

    # Fallback
    _reset(phone)
    return (
        "No entendí tu mensaje 😅\n"
        "Escribí *hola* para empezar de nuevo."
    )


# ── Helpers de presentación ───────────────────────────────────────────────────

def _profesionales_con_precio(db: Session, clinic) -> list:
    if not clinic:
        return []
    profesionales = db.query(models.Professional).filter_by(clinic_id=clinic.id, active=True).all()
    return [p for p in profesionales if (getattr(p, "price", "") or "").strip()]


def _tiene_info(db: Session, clinic) -> bool:
    """True si la clínica cargó información o precios para mostrar en el bot."""
    if not clinic:
        return False
    return bool((getattr(clinic, "bot_info", "") or "").strip() or _profesionales_con_precio(db, clinic))


def _opciones_menu(db: Session, clinic) -> str:
    opciones = (
        "1️⃣ Sacar un turno\n"
        "2️⃣ Cancelar un turno\n"
        "3️⃣ Ver mis turnos\n"
        "4️⃣ Reprogramar un turno"
    )
    if _tiene_info(db, clinic):
        opciones += "\n5️⃣ Información y precios"
    return opciones


def _info_text(db: Session, clinic) -> str:
    """Información personalizada por la clínica: texto libre, precios por profesional y contacto."""
    if not clinic:
        return "No hay información cargada por el momento."
    partes = [f"ℹ️ *{clinic.name}*"]
    info = (getattr(clinic, "bot_info", "") or "").strip()
    if info:
        partes.append(info)
    con_precio = _profesionales_con_precio(db, clinic)
    if con_precio:
        lineas = "💲 *Valor de la consulta:*\n"
        for p in con_precio:
            esp = f" ({p.specialty})" if p.specialty else ""
            lineas += f"• {p.name}{esp}: {p.price.strip()}\n"
        partes.append(lineas.rstrip())
    contacto = ""
    if (clinic.address or "").strip():
        contacto += f"📍 {clinic.address.strip()}\n"
    if (clinic.phone or "").strip():
        contacto += f"📞 {clinic.phone.strip()}\n"
    if contacto:
        partes.append(contacto.rstrip())
    if len(partes) == 1:
        return "Por el momento no hay información cargada. Llamanos al teléfono de la clínica."
    return "\n\n".join(partes)


def _detalle_turno(appt) -> str:
    """Fecha, hora, profesional y especialidad de un turno existente."""
    prof = appt.professional
    return (
        f"📅 {_fecha_legible(appt.date)}\n"
        f"⏰ {appt.time} hs\n"
        f"👨‍⚕️ {prof.name if prof else 'Profesional a confirmar'}\n"
        f"🩺 {(prof.specialty if prof and prof.specialty else 'General')}"
    )


def _iniciar_eleccion_profesional(db: Session, s: dict, clinic_id: int) -> str:
    """Arranca el flujo especialidad → profesional → fecha."""
    profesionales = db.query(models.Professional).filter_by(
        clinic_id=clinic_id, active=True
    ).all()
    if not profesionales:
        s["state"] = "MENU"
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
        s["fecha_offset"] = 0
        return _mostrar_fechas(db, s, clinic_id)

    # Más de uno → el paciente elige
    s["state"] = "PROF_LISTA"
    s["opciones"] = {}
    esp = s.get("specialty_name", "")
    msg = f"👨‍⚕️ ¿Con qué profesional de *{esp}* querés el turno?\n\n"
    for i, p in enumerate(profesionales, 1):
        s["opciones"][str(i)] = {"id": p.id, "name": p.name}
        precio = (getattr(p, "price", "") or "").strip()
        msg += f"{i}️⃣ {p.name}" + (f" — {precio}" if precio else "") + "\n"
    msg += "\nRespondé con el número."
    return msg


def _mostrar_fechas(db: Session, s: dict, clinic_id: int) -> str:
    clinic = db.query(models.Clinic).filter_by(id=clinic_id).first()
    dias = dias_ventana(clinic)
    reprogramar_id = s.get("reprogramar_appt_id")
    fechas = _get_fechas_disponibles(db, clinic_id, s["professional_id"], dias, reprogramar_id)
    esp = s.get("specialty_name") or ""
    prof = s.get("professional_name") or "el profesional"
    if not fechas:
        s["state"] = "MENU"
        s["reprogramar_appt_id"] = None
        aviso = "Tu turno actual sigue como estaba.\n" if reprogramar_id else ""
        return (
            f"😕 No hay turnos disponibles con *{prof}* ({esp}) en los próximos {dias} días.\n"
            f"{aviso}"
            f"Llamanos para coordinar una fecha especial."
        )

    # Paginado: se muestran de a FECHAS_POR_PAGINA; "más" avanza y al final vuelve al principio
    offset = s.get("fecha_offset", 0)
    if offset >= len(fechas):
        offset = 0
    s["fecha_offset"] = offset
    pagina = fechas[offset:offset + FECHAS_POR_PAGINA]

    msg = ""
    if reprogramar_id:
        appt = db.query(models.Appointment).filter_by(id=reprogramar_id, clinic_id=clinic_id).first()
        if appt:
            msg += f"🔄 *Turno a reprogramar:*\n{_detalle_turno(appt)}\n\n"
        msg += f"📅 Elegí la nueva fecha con *{prof}* ({esp}):\n\n"
    else:
        msg += f"📅 Fechas disponibles con *{prof}* ({esp}):\n\n"

    s["opciones"] = {}
    for i, fecha in enumerate(pagina, 1):
        s["opciones"][str(i)] = {"fecha": fecha}
        msg += f"{i}️⃣ {_fecha_legible(fecha)}\n"
    msg += "\nElegí el número de la fecha que preferís 👆"
    if len(fechas) > FECHAS_POR_PAGINA:
        if offset + FECHAS_POR_PAGINA < len(fechas):
            msg += "\nEscribí *más* para ver fechas posteriores."
        else:
            msg += "\nEscribí *más* para volver a las primeras fechas."
    return msg


def _mostrar_horas(db: Session, s: dict, clinic_id: int) -> str:
    horas = _get_horas_disponibles(db, clinic_id, s["professional_id"], s["fecha"], s.get("reprogramar_appt_id"))
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


def _mostrar_confirmacion(db: Session, s: dict) -> str:
    if s.get("reprogramar_appt_id"):
        appt = db.query(models.Appointment).filter_by(id=s["reprogramar_appt_id"]).first()
        antes = f"*Turno actual:*\n{_detalle_turno(appt)}\n\n" if appt else ""
        return (
            f"Por favor confirmá el cambio de turno:\n\n"
            f"{antes}"
            f"*Nuevo turno:*\n{_resumen_turno(db, s, con_paciente=False)}\n\n"
            f"Respondé *SI* para confirmar el cambio ✅\n"
            f"Respondé *NO* para elegir otra fecha ❌"
        )
    return (
        f"Por favor confirmá tu turno:\n\n"
        f"{_resumen_turno(db, s)}\n\n"
        f"Respondé *SI* para confirmar ✅\n"
        f"Respondé *NO* para elegir otra fecha ❌"
    )


def _resumen_turno(db: Session, s: dict, con_paciente: bool = True) -> str:
    """Fecha, hora, profesional, especialidad y (si la clínica lo cargó) valor de la consulta."""
    lineas = []
    if con_paciente and s.get("patient_name"):
        lineas.append(f"👤 *{s['patient_name']}*")
    lineas.append(f"📅 {_fecha_legible(s['fecha'])}")
    lineas.append(f"⏰ {s['hora']} hs")
    lineas.append(f"👨‍⚕️ {s['professional_name']}")
    if s.get("specialty_name"):
        lineas.append(f"🩺 {s['specialty_name']}")
    prof = db.query(models.Professional).filter_by(id=s["professional_id"]).first()
    precio = (getattr(prof, "price", "") or "").strip() if prof else ""
    if precio:
        lineas.append(f"💲 Consulta: {precio}")
    return "\n".join(lineas)


def _mostrar_turnos_paciente(phone: str, db: Session, clinic_id: int, s: dict, solo_ver: bool = False, reprogramar: bool = False) -> str:
    clean = phone.replace("whatsapp:", "").replace("+", "").strip()
    patient = db.query(models.Patient).filter(
        models.Patient.clinic_id == clinic_id,
        models.Patient.phone.contains(clean[-8:]),
    ).first()

    accion = "reprogramar" if reprogramar else "cancelar"

    if not patient:
        s["state"] = "MENU"
        if solo_ver:
            return "📋 No encontré turnos registrados con tu número.\n\nEscribí *1* para sacar un turno."
        else:
            return "📋 No encontré turnos activos con tu número.\n\nEscribí *1* para sacar un turno."

    hoy = date.today().isoformat()
    turnos = db.query(models.Appointment).filter(
        models.Appointment.patient_id == patient.id,
        models.Appointment.clinic_id == clinic_id,
        models.Appointment.date >= hoy,
        models.Appointment.status.in_(["pending", "confirmed"]),
    ).order_by(models.Appointment.date, models.Appointment.time).all()

    if not turnos:
        s["state"] = "MENU"
        if solo_ver:
            return "📋 No tenés turnos próximos agendados.\n\nEscribí *1* para sacar uno."
        else:
            return f"📋 No tenés turnos activos para {accion}.\n\nEscribí *1* para sacar un turno."

    if solo_ver:
        msg = f"📋 *Tus próximos turnos, {patient.name}:*\n\n"
        for t in turnos:
            msg += _detalle_turno(t) + "\n\n"
        msg += "Escribí *hola* si necesitás algo más."
        s["state"] = "INICIO"
        return msg
    else:
        s["opciones"] = {}
        msg = f"¿Cuál turno querés {accion}?\n\n"
        for i, t in enumerate(turnos, 1):
            prof = t.professional.name if t.professional else "Profesional a confirmar"
            esp = t.professional.specialty if t.professional and t.professional.specialty else "General"
            s["opciones"][str(i)] = {"appt_id": t.id}
            msg += f"🔵 *{i}.* {_fecha_legible(t.date)} · {t.time} hs\n      {prof} · {esp}\n"
        msg += f"\n🔵 *0.* Volver\n\nElegí el número del turno a {accion}."
        return msg
