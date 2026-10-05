"""Auth request/response schemas (Pydantic v2).

Responses NEVER include password hashes, OAuth tokens or internal security
fields — only the public user projection.
"""
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

# Minimum password policy. Kept reasonable for a web MVP: length is the
# dominant factor, plus a light character-class requirement.
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


class UserPublic(BaseModel):
    """Public user projection — safe to return to the browser."""

    id: str
    email: str
    name: str
    provider: str
    email_verified: bool = False


class AuthStatus(BaseModel):
    authenticated: bool
    user: Optional[UserPublic] = None


class WsTicketResponse(BaseModel):
    """Short-lived credential for the cross-origin live-interview socket."""

    ticket: str
    expires_in: int


class RegisterRequest(BaseModel):
    name: str = Field(default="", max_length=200)
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("password")
    @classmethod
    def _password_policy(cls, v: str) -> str:
        if len(v) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
        if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one letter and one number.")
        return v

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        return (v or "").strip()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("password")
    @classmethod
    def _password_policy(cls, v: str) -> str:
        if len(v) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
        if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one letter and one number.")
        return v


class MessageResponse(BaseModel):
    """Generic, enumeration-resistant response."""

    message: str
    ok: bool = True


class GoogleCallbackResult(BaseModel):
    """Internal result of a Google OAuth exchange."""

    provider: Literal["google"] = "google"
    subject: str
    email: str
    email_verified: bool
    name: str = ""
