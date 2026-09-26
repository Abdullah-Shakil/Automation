import secrets

from fastapi import Request

from app.config import Settings


def ensure_csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def csrf_ok(request: Request, token: object) -> bool:
    expected = request.session.get("csrf") or ""
    if not expected or not isinstance(token, str) or not token:
        return False
    return secrets.compare_digest(expected, token)


def flash(request: Request, message: str, category: str = "info") -> None:
    request.session["flash"] = {"message": message, "category": category}


def pop_flash(request: Request):
    return request.session.pop("flash", None)


def credentials_ok(settings: Settings, username: str, password: str) -> bool:
    user_ok = secrets.compare_digest(username.encode(), settings.admin_username.encode())
    pass_ok = secrets.compare_digest(password.encode(), settings.admin_password.encode())
    return user_ok and pass_ok
