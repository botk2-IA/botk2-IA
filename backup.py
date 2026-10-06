"""
Botk2-IA — Copias de seguridad
==============================
Exporta todas las tablas a un único archivo .json.gz y permite restaurarlo.

Uso manual:
    python backup.py export  respaldo.json.gz
    python backup.py restore respaldo.json.gz     (solo sobre una base vacía)

Copia automática diaria (opcional): si están configuradas las variables
BACKUP_S3_BUCKET, BACKUP_S3_ACCESS_KEY y BACKUP_S3_SECRET_KEY, la app sube una
copia por día a ese bucket (S3, Cloudflare R2, Backblaze B2, etc.).
BACKUP_S3_ENDPOINT es necesaria para proveedores que no sean AWS.

El archivo contiene datos de salud y credenciales: guardarlo siempre en un
lugar privado y cifrado.
"""

import gzip
import json
import os
import sys
from datetime import datetime, date

from sqlalchemy import DateTime, text

import database
import models  # noqa: F401 — registra las tablas en Base.metadata

S3_BUCKET     = os.environ.get("BACKUP_S3_BUCKET", "")
S3_ACCESS_KEY = os.environ.get("BACKUP_S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.environ.get("BACKUP_S3_SECRET_KEY", "")
S3_ENDPOINT   = os.environ.get("BACKUP_S3_ENDPOINT", "")
S3_REGION     = os.environ.get("BACKUP_S3_REGION", "auto")


def _tables():
    return database.Base.metadata.sorted_tables  # ordenadas por dependencias (FK)


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def backup_filename() -> str:
    return f"botk2ia-backup-{datetime.utcnow().strftime('%Y%m%d-%H%M')}.json.gz"


def export_bytes() -> bytes:
    """Devuelve la base completa como JSON comprimido."""
    data = {"created_at": datetime.utcnow().isoformat(), "tables": {}}
    with database.engine.connect() as conn:
        for table in _tables():
            rows = conn.execute(table.select()).mappings().all()
            data["tables"][table.name] = [dict(r) for r in rows]
    raw = json.dumps(data, default=_json_default, ensure_ascii=False).encode("utf-8")
    return gzip.compress(raw)


def restore_bytes(content: bytes) -> dict:
    """Carga un respaldo en una base vacía. Devuelve la cantidad de filas por tabla."""
    data = json.loads(gzip.decompress(content).decode("utf-8"))
    database.Base.metadata.create_all(bind=database.engine)
    counts = {}
    with database.engine.begin() as conn:
        for table in _tables():
            if conn.execute(table.select().limit(1)).first() is not None:
                raise RuntimeError(
                    f"La tabla '{table.name}' ya tiene datos. Restaurá solo sobre una base vacía."
                )
        for table in _tables():
            rows = data["tables"].get(table.name, [])
            date_cols = [c.name for c in table.columns if isinstance(c.type, DateTime)]
            known = {c.name for c in table.columns}
            clean = []
            for row in rows:
                row = {k: v for k, v in row.items() if k in known}
                for col in date_cols:
                    if isinstance(row.get(col), str):
                        row[col] = datetime.fromisoformat(row[col])
                clean.append(row)
            if clean:
                conn.execute(table.insert(), clean)
            counts[table.name] = len(clean)

        # PostgreSQL: dejar los contadores de id después del último valor restaurado
        if database.engine.dialect.name == "postgresql":
            for table in _tables():
                if "id" in table.columns:
                    conn.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {table.name}), 1))"
                    ))
    return counts


def s3_configured() -> bool:
    return bool(S3_BUCKET and S3_ACCESS_KEY and S3_SECRET_KEY)


def upload_to_s3() -> bool:
    """Sube una copia al bucket configurado."""
    import boto3  # type: ignore
    client = boto3.client(
        "s3",
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        endpoint_url=S3_ENDPOINT or None,
        region_name=S3_REGION,
    )
    key = f"backups/{backup_filename()}"
    client.put_object(Bucket=S3_BUCKET, Key=key, Body=export_bytes(), ContentType="application/gzip")
    print(f"[Backup] Copia subida: {key}")
    return True


def scheduled_backup() -> None:
    """Tarea diaria del scheduler. No hace nada si no hay bucket configurado."""
    if not s3_configured():
        return
    try:
        upload_to_s3()
    except Exception as e:
        print(f"[Backup] ERROR en la copia automática: {e}")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("export", "restore"):
        print(__doc__)
        sys.exit(1)
    action, path = sys.argv[1], sys.argv[2]
    if action == "export":
        with open(path, "wb") as f:
            f.write(export_bytes())
        print(f"Respaldo guardado en {path}")
    else:
        with open(path, "rb") as f:
            result = restore_bytes(f.read())
        print("Restaurado:", ", ".join(f"{t}={n}" for t, n in result.items()))
