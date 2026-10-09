"""
Botk2-IA — Cifrado de datos sensibles en la base
================================================
Los campos sensibles (DNI, notas, diagnóstico, observaciones, receta y el
token de WhatsApp de cada clínica) se guardan cifrados con la clave de la
variable de entorno DATA_ENCRYPTION_KEY. La app los cifra al guardar y los
descifra al leer; el resto del código los usa como texto normal.

Generar la clave (una sola vez):
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

IMPORTANTE: si se pierde la clave, los datos cifrados NO se pueden recuperar.
Guardala fuera de Railway también (gestor de contraseñas).

Sin la variable configurada la app sigue funcionando y guarda en texto plano.
"""

import os

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text, text
from sqlalchemy.types import TypeDecorator

PREFIX = "enc:v1:"

_KEY = os.environ.get("DATA_ENCRYPTION_KEY", "").strip()
_fernet = Fernet(_KEY.encode()) if _KEY else None  # una clave mal formada corta el arranque

# Tabla → columnas cifradas
ENCRYPTED_COLUMNS = {
    "clinics":      ["wa_token"],
    "patients":     ["dni", "notes"],
    "appointments": ["notes", "diagnostico", "observaciones", "receta"],
}


def enabled() -> bool:
    return _fernet is not None


def encrypt(value: str) -> str:
    if not _fernet or not value:
        return value
    return PREFIX + _fernet.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: str) -> str:
    if not value or not value.startswith(PREFIX):
        return value  # dato anterior al cifrado, todavía en texto plano
    if not _fernet:
        raise RuntimeError("Hay datos cifrados pero falta DATA_ENCRYPTION_KEY")
    try:
        return _fernet.decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        raise RuntimeError("DATA_ENCRYPTION_KEY no corresponde a los datos cifrados")


class EncryptedText(TypeDecorator):
    """Columna de texto que se cifra al guardar y se descifra al leer."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt(value) if isinstance(value, str) else value

    def process_result_value(self, value, dialect):
        return decrypt(value) if isinstance(value, str) else value


def prepare_database(engine) -> None:
    """
    Se llama al arrancar, después de las migraciones:
    1. Comprueba que los datos ya cifrados se puedan leer con la clave actual.
       Si no, corta el arranque para no mostrar ni pisar datos ilegibles.
    2. Con clave configurada, cifra las filas que todavía están en texto plano.
    """
    with engine.begin() as conn:
        for table, columns in ENCRYPTED_COLUMNS.items():
            for column in columns:
                sample = conn.execute(text(
                    f"SELECT {column} FROM {table} WHERE {column} LIKE :p LIMIT 1"
                ), {"p": PREFIX + "%"}).scalar()
                if sample:
                    decrypt(sample)  # lanza RuntimeError si falta la clave o no coincide

        if not enabled():
            print("[Cifrado] ADVERTENCIA: DATA_ENCRYPTION_KEY no configurada, los datos sensibles se guardan sin cifrar")
            return

        total = 0
        for table, columns in ENCRYPTED_COLUMNS.items():
            for column in columns:
                rows = conn.execute(text(
                    f"SELECT id, {column} FROM {table} "
                    f"WHERE {column} IS NOT NULL AND {column} <> '' AND {column} NOT LIKE :p"
                ), {"p": PREFIX + "%"}).all()
                for row_id, value in rows:
                    conn.execute(
                        text(f"UPDATE {table} SET {column} = :v WHERE id = :id"),
                        {"v": encrypt(value), "id": row_id},
                    )
                total += len(rows)
        if total:
            print(f"[Cifrado] {total} valores existentes cifrados")
