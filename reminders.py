"""
Botk2-IA — Recordatorios automaticos por WhatsApp
Se ejecuta diariamente para notificar turnos del dia siguiente.
"""

import os
import httpx
from datetime import date, timedelta
from sqlalchemy.orm import Session

import models
import database


DAYS_ES   = ["lunes","martes","miercoles","jueves","viernes","sabado","domingo"]
MONTHS_ES = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto",
             "septiembre","octubre","noviembre","diciembre"]

# Nombre del template aprobado en Meta Business Manager.
# Si esta vacio, se envian mensajes de texto libre (solo funciona dentro de la
# ventana de 24hs o en cuentas de prueba).
WA_TEMPLATE_NAME = os.environ.get("WA_REMINDER_TEMPLATE", "")
WA_TEMPLATE_LANG = os.environ.get("WA_REMINDER_TEMPLATE_LANG", "es_AR")


def _fecha_legible(fecha_str: str) -> str:
    d = date.fromisoformat(fecha_str)
    return f"{DAYS_ES[d.weekday()]} {d.day} de {MONTHS_ES[d.month - 1]}"


def _normalizar_phone(phone: str) -> str:
    phone = phone.replace("+", "").replace(" ", "").replace("-", "").strip()
    if phone.startswith("549"):
        return phone
    if phone.startswith("54"):
        return "549" + phone[2:].lstrip("0")
    return "549" + phone.lstrip("0")


def _build_payload(phone: str, patient_name: str, clinic_name: str,
                   fecha: str, hora: str, prof_name: str) -> dict:
    """
    Construye el payload de la API de WhatsApp.
    - Si WA_TEMPLATE_NAME esta configurado: usa template aprobado por Meta
      (necesario para mensajes proactivos fuera de la ventana de 24hs).
    - Si no: usa texto libre (solo para pruebas o dentro de ventana activa).

    El template debe estar aprobado con estos parametros en orden:
      {{1}} = nombre del paciente
      {{2}} = nombre de la clinica
      {{3}} = fecha (ej: "manana, viernes 11 de julio")
      {{4}} = hora (ej: "10:30")
      {{5}} = nombre del profesional
    """
    if WA_TEMPLATE_NAME:
        return {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "template",
            "template": {
                "name": WA_TEMPLATE_NAME,
                "language": {"code": WA_TEMPLATE_LANG},
                "components": [{
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": patient_name},
                        {"type": "text", "text": clinic_name},
                        {"type": "text", "text": f"manana, {fecha}"},
                        {"type": "text", "text": hora},
                        {"type": "text", "text": prof_name},
                    ]
                }]
            }
        }
    else:
        msg = (
            f"Hola {patient_name}, te recordamos tu turno en *{clinic_name}*:\n\n"
            f"Manana, {fecha}\n"
            f"{hora} hs\n"
            f"{prof_name}\n\n"
            f"Vas a poder asistir?\n"
            f"Responde SI para confirmar\n"
            f"Responde NO para cancelar"
        )
        return {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "text",
            "text": {"body": msg}
        }


def send_reminders() -> int:
    """
    Busca todos los turnos de manana con reminder_sent=False y envia WhatsApp.
    Retorna la cantidad de recordatorios enviados.
    """
    db: Session = next(database.get_db())
    try:
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        appointments = (
            db.query(models.Appointment)
            .filter(
                models.Appointment.date == tomorrow,
                models.Appointment.status.in_(["pending", "confirmed"]),
                models.Appointment.reminder_sent == False,
            )
            .all()
        )
        sent = 0
        for appt in appointments:
            clinic       = appt.clinic
            patient      = appt.patient
            professional = appt.professional
            if not patient or not patient.phone:
                continue
            wa_token    = clinic.wa_token    or os.environ.get("WHATSAPP_TOKEN", "")
            wa_phone_id = clinic.wa_phone_id or os.environ.get("WHATSAPP_PHONE_ID", "")
            if not wa_token or not wa_phone_id:
                print(f"[Reminder] Clinica {clinic.id} sin WhatsApp configurado, skip.")
                continue
            prof_name = professional.name if professional else "el profesional"
            phone     = _normalizar_phone(patient.phone)
            payload   = _build_payload(
                phone, patient.name, clinic.name,
                _fecha_legible(appt.date), appt.time, prof_name
            )
            url     = f"https://graph.facebook.com/v21.0/{wa_phone_id}/messages"
            headers = {"Authorization": f"Bearer {wa_token}", "Content-Type": "application/json"}
            try:
                r = httpx.post(url, headers=headers, json=payload, timeout=10)
                if r.status_code == 200:
                    appt.reminder_sent = True
                    sent += 1
                    print(f"[Reminder] OK {patient.name} ({phone}) via {'template' if WA_TEMPLATE_NAME else 'text'}")
                else:
                    print(f"[Reminder] ERROR {patient.name}: HTTP {r.status_code} - {r.text[:200]}")
            except Exception as e:
                print(f"[Reminder] ERROR {patient.name}: {e}")
        db.commit()
        print(f"[Reminder] {sent}/{len(appointments)} enviados para {tomorrow}")
        return sent
    finally:
        db.close()


def auto_complete_past_appointments() -> int:
    """
    Marca como completed los turnos con fecha anterior a hoy
    que siguen en estado pending o confirmed.
    Se ejecuta diariamente a las 23:59.
    """
    db: Session = next(database.get_db())
    try:
        today = date.today().isoformat()
        past = (
            db.query(models.Appointment)
            .filter(
                models.Appointment.date < today,
                models.Appointment.status.in_(["pending", "confirmed"]),
            )
            .all()
        )
        for appt in past:
            appt.status = "completed"
        db.commit()
        print(f"[AutoComplete] {len(past)} turnos marcados como completados")
        return len(past)
    finally:
        db.close()
