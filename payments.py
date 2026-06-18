"""
Botk2-IA — Módulo de Pagos
============================
Maneja suscripciones con dos procesadores:
  - MercadoPago  → Argentina y LATAM
  - Stripe       → Internacional (tarjeta de crédito global)

Configuración via variables de entorno (.env / Railway):
  MP_ACCESS_TOKEN      → Token de MercadoPago (producción o sandbox)
  STRIPE_SECRET_KEY    → sk_live_... o sk_test_...
  STRIPE_WEBHOOK_SECRET → whsec_... (para verificar webhooks de Stripe)
  APP_URL              → URL pública de la app (ej: https://app.botk2-ia.com)
"""

import os
import json
import hashlib
import hmac
from typing import Optional

# ── Configuración ─────────────────────────────────────────────────────────────

APP_URL = os.environ.get("APP_URL", "http://localhost:8000")

MP_ACCESS_TOKEN     = os.environ.get("MP_ACCESS_TOKEN", "")
STRIPE_SECRET_KEY   = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# Precios por plan (en USD — MercadoPago los convierte a moneda local)
PLANS = {
    "starter": {"name": "Starter",  "price_usd": 29.0,  "price_cents": 2900},
    "pro":     {"name": "Pro",      "price_usd": 49.0,  "price_cents": 4900},
    "clinica": {"name": "Clínica",  "price_usd": 89.0,  "price_cents": 8900},
}


# ── MercadoPago ───────────────────────────────────────────────────────────────

def create_mp_preference(plan: str, clinic_id: int, clinic_email: str) -> Optional[str]:
    """
    Crea una preferencia de pago en MercadoPago.
    Devuelve la URL de checkout (init_point) o None si falla.

    Requiere: pip install mercadopago
    Docs: https://www.mercadopago.com.ar/developers/es/docs/checkout-pro
    """
    if not MP_ACCESS_TOKEN:
        return None

    try:
        import mercadopago  # type: ignore
        sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

        plan_info = PLANS[plan]
        preference_data = {
            "items": [{
                "title":       f"Botk2-IA — Plan {plan_info['name']}",
                "description": "Automatización con WhatsApp para tu clínica o negocio",
                "quantity":    1,
                "unit_price":  plan_info["price_usd"],
                "currency_id": "USD",
            }],
            "payer": {"email": clinic_email},
            "back_urls": {
                "success": f"{APP_URL}/payment/success?plan={plan}&method=mercadopago",
                "failure": f"{APP_URL}/payment/cancel?reason=failure",
                "pending": f"{APP_URL}/payment/success?plan={plan}&method=mercadopago&status=pending",
            },
            "auto_return":          "approved",
            "external_reference":   f"{clinic_id}:{plan}",
            "notification_url":     f"{APP_URL}/webhook/mercadopago",
            "statement_descriptor": "BOTK2-IA",
        }

        result = sdk.preference().create(preference_data)
        if result["status"] == 201:
            # Producción: init_point / Sandbox: sandbox_init_point
            return result["response"].get("init_point") or result["response"].get("sandbox_init_point")
    except Exception as e:
        print(f"[MercadoPago] Error creando preferencia: {e}")

    return None


def verify_mp_webhook(data: dict) -> Optional[dict]:
    """
    Verifica y parsea una notificación IPN de MercadoPago.
    Devuelve dict con clinic_id y status si es un pago aprobado.
    """
    if not MP_ACCESS_TOKEN:
        return None
    try:
        import mercadopago  # type: ignore
        sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

        topic = data.get("topic") or data.get("type")
        resource_id = data.get("id") or (data.get("data") or {}).get("id")

        if topic not in ("payment", "merchant_order"):
            return None

        if topic == "payment":
            result = sdk.payment().get(resource_id)
            payment = result["response"]
            if payment.get("status") == "approved":
                ref = payment.get("external_reference", "")
                parts = ref.split(":")
                clinic_id = int(parts[0])
                plan = parts[1] if len(parts) > 1 else ""
                return {
                    "clinic_id":  clinic_id,
                    "plan":       plan,
                    "status":     "approved",
                    "payment_id": str(resource_id),
                }
    except Exception as e:
        print(f"[MercadoPago] Error verificando webhook: {e}")

    return None


# ── Stripe ────────────────────────────────────────────────────────────────────

def create_stripe_session(plan: str, clinic_id: int, clinic_email: str) -> Optional[str]:
    """
    Crea una sesión de Stripe Checkout.
    Devuelve la URL de pago o None si falla.

    Requiere: pip install stripe
    Docs: https://stripe.com/docs/checkout/quickstart
    """
    if not STRIPE_SECRET_KEY:
        return None

    try:
        import stripe  # type: ignore
        stripe.api_key = STRIPE_SECRET_KEY

        plan_info = PLANS[plan]
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency":     "usd",
                    "product_data": {
                        "name":        f"Botk2-IA — Plan {plan_info['name']}",
                        "description": "Automatización con WhatsApp para clínicas y negocios",
                    },
                    "unit_amount": plan_info["price_cents"],  # en centavos
                },
                "quantity": 1,
            }],
            mode="payment",
            customer_email=clinic_email,
            client_reference_id=str(clinic_id),
            success_url=f"{APP_URL}/payment/success?plan={plan}&method=stripe&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{APP_URL}/payment/cancel?reason=cancelled",
            metadata={"clinic_id": str(clinic_id), "plan": plan},
        )
        return session.url
    except Exception as e:
        print(f"[Stripe] Error creando sesión: {e}")

    return None


def verify_stripe_webhook(payload: bytes, sig_header: str) -> Optional[dict]:
    """
    Verifica la firma del webhook de Stripe y extrae datos del pago.
    Devuelve dict con clinic_id y plan si el pago fue exitoso.
    """
    if not STRIPE_SECRET_KEY or not STRIPE_WEBHOOK_SECRET:
        return None

    try:
        import stripe  # type: ignore
        stripe.api_key = STRIPE_SECRET_KEY

        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)

        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            if session.get("payment_status") == "paid":
                return {
                    "clinic_id": int(session["client_reference_id"]),
    