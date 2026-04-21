"""
routers/auth.py — Login, register, and current user endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, EmailStr
from ..database import get_db
from ..models import User, UserRole, AuditEventType, AuditActorType
from ..auth import create_access_token, get_current_user
from ..audit import write_audit

router = APIRouter(prefix="/auth", tags=["auth"])

class Token(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    role:         str

class UserCreate(BaseModel):
    email: EmailStr
    role:  UserRole = UserRole.analyst

class UserOut(BaseModel):
    id:    str
    email: str
    role:  str
    class Config:
        from_attributes = True

@router.post("/login", response_model=Token)
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == form.username))
    user   = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_access_token(user.id, user.role)
    await write_audit(db, AuditEventType.auth_login, AuditActorType.user,
                      {"email": user.email}, actor_id=user.id)
    return Token(access_token=token, token_type="bearer", role=user.role.value)

@router.post("/register", response_model=UserOut, status_code=201)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(email=payload.email, role=payload.role, settings={})
    db.add(user)
    await db.flush()
    await write_audit(db, AuditEventType.auth_login, AuditActorType.system,
                      {"email": user.email, "role": user.role.value})
    return UserOut(id=str(user.id), email=user.email, role=user.role.value)

@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return UserOut(id=str(current_user.id), email=current_user.email, role=current_user.role.value)
