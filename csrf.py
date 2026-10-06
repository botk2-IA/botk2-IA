"""
Botk2-IA — Protección CSRF
==========================
Cada navegador recibe un token aleatorio en una cookie. Todo POST tiene que
traer ese mismo token, en el campo oculto `csrf_token` del formulario o en el
header `X-CSRF-Token` (fetch). Un sitio externo no puede leer la cookie, así
que no puede armar un pedido válido en nombre del usuario.

Los webhooks (/webhook/...) quedan afuera: se validan por firma del proveedor.
"""

import hmac
import re
import secrets
from urllib.parse import parse_qs

from markupsafe import Markup
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse

import auth

COOKIE_NAME = "csrf_token"
FIELD_NAME  = "csrf_token"
HEADER_NAME = "x-csrf-token"

SAFE_METHODS    = {"GET", "HEAD", "OPTIONS"}
EXEMPT_PREFIXES = ("/webhook/",)

_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")

_ERROR_HTML = """<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"/><title>Sesión vencida — Botk2-IA</title></head>
<body style="font-family:Arial,sans-serif;text-align:center;padding:60px 20px;color:#334155;">
<h2>La página quedó desactualizada</h2>
<p>Por seguridad no procesamos el pedido. Volvé atrás, recargá la página e intentá de nuevo.</p>
<p><a href="javascript:history.back()" style="color:#2563eb;">← Volver</a></p>
</body></html>"""


async def _token_from_form(request) -> str:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/x-www-form-urlencoded"):
        body = await request.body()
        values = parse_qs(body.decode("utf-8", errors="replace")).get(FIELD_NAME, [])
        return values[0] if values else ""
    if content_type.startswith("multipart/form-data"):
        await request.body()  # deja el cuerpo en caché para que la ruta lo pueda volver a leer
        form = await request.form()
        value = form.get(FIELD_NAME, "")
        return value if isinstance(value, str) else ""
    return ""


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        token = request.cookies.get(COOKIE_NAME, "")
        is_new = not _TOKEN_RE.match(token)
        if is_new:
            token = secrets.token_hex(32)
        request.state.csrf_token = token

        if request.method not in SAFE_METHODS and not request.url.path.startswith(EXEMPT_PREFIXES):
            sent = request.headers.get(HEADER_NAME) or await _token_from_form(request)
            if is_new or not sent or not hmac.compare_digest(sent.encode(), token.encode()):
                return HTMLResponse(_ERROR_HTML, status_code=403)

        response = await call_next(request)
        if is_new:
            response.set_cookie(COOKIE_NAME, token, httponly=True, max_age=60*60*24*30,
                                secure=auth.COOKIE_SECURE, samesite="lax")
        return response


def csrf_token(request) -> str:
    return getattr(request.state, "csrf_token", "")


def csrf_input(request) -> Markup:
    """Campo oculto para poner dentro de cada <form method="post">."""
    return Markup(f'<input type="hidden" name="{FIELD_NAME}" value="{csrf_token(request)}"/>')
