import jwt
import bcrypt
import secrets
import string
from datetime import datetime, timedelta, timezone
from flask import current_app, request, redirect, url_for, g
from functools import wraps
import logging

logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False


def generate_jwt(user_id: int, role: str) -> str:
    secret = current_app.config['SECRET_KEY']
    algo = current_app.config.get('JWT_ALGORITHM', 'HS256')
    hours = current_app.config.get('JWT_EXPIRY_HOURS', 24)
    payload = {
        'user_id': user_id,
        'role': role,
        'iat': datetime.now(timezone.utc),
        'exp': datetime.now(timezone.utc) + timedelta(hours=hours)
    }
    return jwt.encode(payload, secret, algorithm=algo)


def set_auth_cookie(response, token: str):
    response.set_cookie(
        'auth_token', token,
        httponly=True, secure=False, samesite='Lax', max_age=86400
    )
    return response


def clear_auth_cookie(response):
    response.delete_cookie('auth_token')
    return response


def generate_2fa_code() -> str:
    return ''.join(secrets.choice(string.digits) for _ in range(6))


def validate_password(password: str):
    if len(password) < 8:
        return False, 'Пароль повинен містити мінімум 8 символів'
    return True, ''


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not g.get('current_user'):
            return redirect(url_for('auth.login', next=request.url))
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = g.get('current_user')
            if not user:
                return redirect(url_for('auth.login'))
            if user['role'] not in roles:
                from flask import abort
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator


def approved_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = g.get('current_user')
        if not user:
            return redirect(url_for('auth.login'))
        if user.get('is_approved'):
            return f(*args, **kwargs)
        from flask import render_template
        return render_template('auth/pending.html'), 403
    return decorated

