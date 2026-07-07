"""
Botk2-IA — Recordatorios automáticos por WhatsApp
Se ejecuta diariamente para notificar turnos del día siguiente.
"""

import os
import httpx
from datetime import date, timedelta
from sqlalchemy.orm import Session

import models
import database


DAYS_ES   = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
MONTHS_ES = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto",
             "septiembre","octubre","noviembre","diciembre"]


def _fecha_legible(fecha_str: str) -> str:
    d = date.fromisoformat(fecha_str)
    return f"{DAYS_ES[d.weekday()]} {d.day} de {MONTHS_ES[d.month - 1]}"


def _normalizar_phone(phone: str) -> str:
    """Convierte el teléfono al formato internacional argentino sin +."""
    phone = phone.replace("+", "").replace(" ", "").replace("-", "").strip()
    # Si ya empieza con 549 o 54 → ok
    if phone.startswith("549"):
        return phone
    if phone.startswith("54"):
        return "549" + phone[2:].lstrip("0")
    # Número local argentino: 011... o celular 15...
    return "549" + phone.lstrip("0")


def send_reminders() -> int:
    """
    Busca todos los turnos de mañana con reminder_sent=False y envía WhatsApp.
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
            clinic     = appt.clinic
            patient    = appt.patient
            professional = appt.professional

            if not patient or not patient.phone:
                continue

            # Credenciales por clínica con fallback a env vars globales
            wa_token    = clinic.wa_token    or os.environ.get("WHATSAPP_TOKEN", "")
            wa_phone_id = clinic.wa_phone_id or os.environ.get("WHATSAPP_PHONE_ID", "")

            if not wa_token or not wa_phone_id:
                print(f"[Reminder] Clínica {clinic.id} sin WhatsApp configurado, skip.")
                continue

            prof_name = professional.name if professional else "el profesional"
            msg = (
                f"👋 Hola {patient.name}, te recordamos tu turno en *{clinic.name}*:\n\n"
                f"📅 Mañana, {_fecha_legible(appt.date)}\n"
                f"⏰ {appt.time} hs\n"
                f"👨‍⚕️ {prof_name}\n\n"
                f"Si necesitás cancelar o reprogramar, respondé este mensaje."
            )

            phone = _normalizar_phone(patient.phone)
            url     = f"https://graph.facebook.com/v21.0/{wa_phone_id}/messages"
            headers = {
                "Authorization": f"Bearer {wa_token}",
                "Content-Type":  "application/json",
            }
            payload = {
                "messaging_product": "whatsapp",
                "to":   phone,
                "type": "text",
                "text": {"body": msg},
            }

            try:
                r = httpx.post(url, headers=headers, json=payload, timeout=10)
                if r.status_code == 200:
                    appt.reminder_sent = True
                    sent += 1
                    print(f"[Reminder] ✓ {patient.name} ({phone}) – {appt.date} {appt.time}")
                else:
                    print(f"[Reminder] ✗ {patient.name}: HTTP {r.status_code} – {r.text[:200]}")
            except Exception as e:
                print(f"[Reminder] ✗ Error enviando a {patient.name}: {e}")

        db.commit()
        print(f"[Reminder] Listo: {sent}/{len(appointments)} enviados para {tomorrow}")
        return sent

    finally:
        db.close()
