# Cómo deployar Botk2-IA en Railway

## 1. Crear cuenta en Railway
Ve a https://railway.app y registrate con tu cuenta de GitHub.

## 2. Crear repositorio en GitHub
```bash
git init
git add .
git commit -m "Initial commit — Botk2-IA"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/botk2-ia.git
git push -u origin main
```

## 3. Crear proyecto en Railway
1. En el dashboard de Railway → "New Project"
2. Elegí "Deploy from GitHub repo"
3. Seleccioná tu repositorio `botk2-ia`
4. Railway detecta el `Procfile` automáticamente ✅

## 4. Agregar base de datos PostgreSQL
1. En tu proyecto de Railway → "+ New" → "Database" → "PostgreSQL"
2. Railway automáticamente inyecta la variable `DATABASE_URL` en tu app ✅

## 5. Configurar variables de entorno
En Railway → tu servicio → "Variables", agregá:

| Variable            | Valor                                      |
|---------------------|--------------------------------------------|
| `SECRET_KEY`        | (generá con: `python -c "import secrets; print(secrets.token_hex(32))"`) |
| `ADMIN_PASSWORD_HASH` | (SHA-256 de tu contraseña admin) |

Para generar el hash de la contraseña admin:
```python
import hashlib
print(hashlib.sha256(b"tu_password_aqui").hexdigest())
```

## 6. Deploy automático
Cada vez que hagas `git push`, Railway re-deploya automáticamente. ✅

## 7. Dominio personalizado (opcional)
En Railway → tu servicio → "Settings" → "Domains" → "Custom Domain"
Podés conectar `app.botk2-ia.com` o similar.

## URLs importantes después del deploy
- Panel de clínica: `https://tu-app.railway.app/dashboard`
- Panel admin: `https://tu-app.railway.app/admin`
- Health check: `https://tu-app.railway.app/health`
- Webhook WhatsApp: `https://tu-app.railway.app/webhook/whatsapp/{clinic_id}`

## Notas
- La DB se migra automáticamente al iniciar (SQLAlchemy crea las tablas)
- Para cargar datos de demo en Railway, corré: `python seed.py` desde la consola de Railway
