import os
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from database import get_db
import models

SECRET_KEY = os.environ.get("SECRET_KEY", "clinica-saas-secret-key-cambiar-en-produccion-2024")
ALGORITHM  = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 días

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login", auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_clinic_from_token(token: str, db: Session) -> Optional[models.Clinic]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        clinic_id: int = payload.get("sub")
        if clinic_id is None:
            return None
    except JWTError:
        return None
    return db.query(models.Clinic).filter(models.Clinic.id == int(clinic_id)).first()


def get_current_clinic(
    request: Request,
    db: Session = Depends(get_db)
) -> models.Clinic:
    """Lee el token del cookie o del header Authorization."""
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado",
            headers={"WWW-Authenticate": "Bearer"},
        )

    clinic = get_clinic_from_token(token, db)
    if not clinic or not clinic.active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión inválida o expirada",
        )
    return clinic


def get_current_clinic_optional(
    request: Request,
    db: Session = Depends(get_db)
) -> Optional[models.Clinic]:
    token = request.cookies.get("access_token")
    if not token:
        return None
    return get_clinic_from_token(token, db)
