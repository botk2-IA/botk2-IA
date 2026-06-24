"""
Botk2-IA - Modulo de Pagos
"""
import os
from typing import Optional

APP_URL = os.environ.get("APP_URL", "http://localhost:8000")
MP_ACCESS_TOKEN     = os.environ.get("MP_ACCESS_TOKEN", "")
STRIPE_SECRET_KEY   = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

PLANS = {
    "starter": {"name": "Starter",  "price_usd": 29.0,  "price_cents": 2900},
    "pro":     {"name": "Pro",      "price_usd": 49.0,  "price_cents": 4900},
    "clinica": {"name": "Clinica",  "price_usd": 89.0,  "price_cents": 8900},
}


def create_mp_preference(plan: str, clinic_id: int, clinic_email: str) -> Optional[str]:
    if not MP_ACCESS_TOKEN:
        return None
    try:
        import mercadopago  # type: ignore
        sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
        plan_info = PLANS[plan]
        preference_data = {
            "items": [{
                "title":       f"Botk2-IA - Plan {plan_info['name']}",
                "description": "Automatizacion WhatsApp para clinicas",
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
            return result["response"].get("init_point") or result["response"].get("sandbox_init_point")
    except Exception as e:
        print(f"[MercadoPago] Error creando preferencia: {e}")
    return None


def verify_mp_webhook(data: dict) -> Optional[dict]:
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


def create_stripe_session(plan: str, clinic_id: int, clinic_email: str) -> Optional[str]:
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
                        "name":        f"Botk2-IA - Plan {plan_info['name']}",
                        "description": "Automatizacion WhatsApp para clinicas",
                    },
                    "unit_amount": plan_info["price_cents"],
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
        print(f"[Stripe] Error creando sesion: {e}")
    return None


def verify_stripe_webhook(payload: bytes, sig_header: str) -> Optional[dict]:
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
                    "plan":      session["metadata"].get("plan"),
                    "status":    "paid",
                }
    except Exception as e:
        print(f"[Stripe] Error verificando webhook: {e}")
    return None
