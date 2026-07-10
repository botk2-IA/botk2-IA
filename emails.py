"""
Botk2-IA — Envio de emails via Gmail SMTP
Requiere variables de entorno:
  SMTP_USER = tu-email@gmail.com
  SMTP_PASS = contraseña de aplicacion de Google (16 caracteres)
  SMTP_FROM = (opcional) nombre visible, default: Botk2-IA <SMTP_USER>
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "Botk2-IA")


def _send(to_email: str, subject: str, html: str) -> bool:
    if not SMTP_USER or not SMTP_PASS:
        print(f"[Email] SMTP no configurado, skip ({to_email})")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"{SMTP_FROM_NAME} <{SMTP_USER}>"
        msg["To"]      = to_email
        msg.attach(MIMEText(html, "html", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.ehlo()
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, to_email, msg.as_string())

        print(f"[Email] OK → {to_email} | {subject}")
        return True
    except Exception as e:
        print(f"[Email] ERROR → {to_email}: {e}")
        return False


def send_welcome(clinic_name: str, to_email: str) -> bool:
    subject = f"¡Bienvenido a Botk2-IA, {clinic_name}! 🎉"
    html = f"""
<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0">
  <tr><td align="center" style="padding:40px 20px;">
    <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.08);">

      <!-- Header -->
      <tr><td style="background:#1e40af;padding:36px 40px;">
        <h1 style="margin:0;color:#ffffff;font-size:26px;font-weight:800;letter-spacing:-0.5px;">Botk2-IA</h1>
        <p style="margin:6px 0 0;color:#bfdbfe;font-size:14px;">Automatización para clínicas por WhatsApp</p>
      </td></tr>

      <!-- Body -->
      <tr><td style="padding:40px;">
        <h2 style="margin:0 0 16px;color:#1e293b;font-size:22px;">¡Hola, {clinic_name}! 👋</h2>
        <p style="margin:0 0 16px;color:#475569;font-size:15px;line-height:1.6;">
          Tu cuenta en Botk2-IA está lista. Ya podés empezar a configurar tu bot de WhatsApp
          para que tus pacientes saquen turnos automáticamente.
        </p>

        <!-- Pasos -->
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#eff6ff;border-radius:12px;margin:24px 0;">
          <tr><td style="padding:24px;">
            <p style="margin:0 0 12px;color:#1e40af;font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;">Primeros pasos</p>
            <p style="margin:0 0 8px;color:#1e293b;font-size:14px;">✅ <b>1.</b> Configurá tu WhatsApp Business en <b>Ajustes</b></p>
            <p style="margin:0 0 8px;color:#1e293b;font-size:14px;">✅ <b>2.</b> Agregá tus profesionales y sus horarios</p>
            <p style="margin:0 0 8px;color:#1e293b;font-size:14px;">✅ <b>3.</b> Cargá tus primeros pacientes</p>
            <p style="margin:0;color:#1e293b;font-size:14px;">✅ <b>4.</b> ¡Probá el bot enviando un "hola"!</p>
          </td></tr>
        </table>

        <!-- CTA -->
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr><td align="center" style="padding:8px 0 24px;">
            <a href="https://botk2ia.com/dashboard"
               style="display:inline-block;background:#2563eb;color:#ffffff;text-decoration:none;font-size:15px;font-weight:700;padding:14px 36px;border-radius:10px;">
              Ir al panel →
            </a>
          </td></tr>
        </table>

        <p style="margin:0;color:#94a3b8;font-size:13px;line-height:1.6;">
          ¿Necesitás ayuda con la configuración? Respondé este email o escribinos a
          <a href="mailto:botk2ia@gmail.com" style="color:#2563eb;">botk2ia@gmail.com</a>.
        </p>
      </td></tr>

      <!-- Footer -->
      <tr><td style="background:#f8fafc;padding:20px 40px;border-top:1px solid #e2e8f0;">
        <p style="margin:0;color:#94a3b8;font-size:12px;text-align:center;">
          © 2025 Botk2-IA · Argentina 🇦🇷 ·
          <a href="https://botk2ia.com/privacy" style="color:#94a3b8;">Privacidad</a> ·
          <a href="https://botk2ia.com/terms" style="color:#94a3b8;">Términos</a>
        </p>
      </td></tr>

    </table>
  </td></tr>
</table>
</body>
</html>
"""
    return _send(to_email, subject, html)
