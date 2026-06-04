# Botk2-IA — Pendientes para el lanzamiento 🚀
_Última actualización: 3 de junio de 2026_

---

## 💲 Planes y precios

| Plan | Precio USD/mes | Para quién |
|---|---|---|
| **Starter** | USD 29 | 1 profesional, bot WhatsApp incluido |
| **Pro** | USD 60 | Hasta 3 profesionales, historial clínico, estadísticas |
| **Clínica** | USD 90 | Profesionales ilimitados, multi-especialidad, todo incluido |

> **Diferencial clave:** WhatsApp bot incluido en todos los planes. La competencia (Medicloud) cobra ~$150.000 ARS/mes aparte por esto.

---

## 🔴 CRÍTICO — Sin esto la app no funciona en producción

1. **Subir código a GitHub**
   - `git init`, `git add .`, `git commit`, `git push`
   - Crear repo en github.com/TU_USUARIO/botk2-ia

2. **Deploy en Railway**
   - Crear proyecto en railway.app desde el repo de GitHub
   - Agregar plugin PostgreSQL (Railway inyecta `DATABASE_URL` automático)
   - Ver guía completa en `DEPLOY_RAILWAY.md`

3. **Variables de entorno en Railway**
   - `SECRET_KEY` → generá con: `python -c "import secrets; print(secrets.token_hex(32))"`
   - `ADMIN_PASSWORD_HASH` → SHA-256 de tu contraseña admin
   - `APP_URL` → la URL pública que te da Railway (ej: https://botk2-ia.railway.app)

---

## 🟠 IMPORTANTE — Para poder cobrar

4. **MercadoPago (Argentina y LATAM)**
   - Crear cuenta en mercadopago.com.ar/developers
   - Obtener `MP_ACCESS_TOKEN` de producción (empieza con APP_USR-)
   - Agregar como variable de entorno en Railway
   - Configurar webhook IPN apuntando a `APP_URL/webhook/mercadopago`

5. **Stripe (Internacional)**
   - Obtener `STRIPE_SECRET_KEY` y `STRIPE_WEBHOOK_SECRET` desde dashboard.stripe.com
   - Agregar como variables de entorno en Railway
   - Configurar webhook en Stripe apuntando a `APP_URL/webhook/stripe`

6. **Probar flujo de pago completo**
   - Registrar una clínica de prueba
   - Hacer un pago de prueba con MercadoPago sandbox
   - Verificar que el plan se actualiza en el panel admin

---

## 🟡 PARA QUE EL BOT FUNCIONE DE VERDAD

7. **WhatsApp — Twilio o Meta Business API**
   - Opción A (más fácil): Twilio → agregar `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`
   - Opción B (más barato largo plazo): Meta WhatsApp Business API
   - Configurar el webhook en Twilio/Meta apuntando a `APP_URL/webhook/whatsapp/{clinic_id}`

---

## 🟢 PARA VENDER MEJOR

8. **Almacenamiento de imágenes/radiografías**
   - Integrar Cloudinary o AWS S3
   - Permite subir radiografías reales desde el dashboard de la clínica
   - El demo ya tiene la UI lista, solo falta conectar el backend

9. **Dominio propio**
   - Comprar dominio (ej: botk2-ia.com o app.botk2ia.com)
   - Configurar en Railway → Settings → Custom Domain

10. **Landing page en vivo**
    - El `index.html` ya está listo
    - Deployarlo en Netlify o conectarlo al mismo Railway

---

## ✅ YA ESTÁ HECHO

- [x] Backend completo (FastAPI): login, dashboard, pacientes, turnos, profesionales
- [x] Bot de WhatsApp con máquina de estados
- [x] Panel admin para gestionar clínicas y planes
- [x] Sistema de pagos codificado (MercadoPago + Stripe)
- [x] Demo clínica completo con historial médico, radiografías, obra social
- [x] Demo profesional con historial expandible, recetas, imágenes
- [x] Estadísticas: por profesional, especialidad, obra social, presentismo/ausentismo
- [x] Filtros de agenda funcionando en todas las vistas
- [x] Navegación entre páginas del demo arreglada
- [x] Landing page y sitio web listo
