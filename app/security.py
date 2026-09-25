import secrets
import time
from collections import defaultdict
from functools import wraps
from flask import request, session, abort, jsonify
import logging

logger = logging.getLogger(__name__)
_rate_limit_store = defaultdict(list)


def rate_limit(max_calls: int, period_seconds: int, methods: tuple = ('POST',)):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if request.method not in methods:
                return f(*args, **kwargs)
            ip = request.remote_addr or '127.0.0.1'
            now = time.time()
            key = f'{f.__name__}:{ip}'
            calls = _rate_limit_store[key]
            calls[:] = [t for t in calls if now - t < period_seconds]
            if len(calls) >= max_calls:
                logger.warning(f'Rate limit exceeded for {ip} on {f.__name__}')
                if request.is_json or request.headers.get('Accept') == 'application/json':
                    return jsonify({'error': 'Забагато спроб. Спробуйте пізніше.'}), 429
                from flask import render_template
                return render_template('auth/login.html',
                                       error='Забагато спроб входу. Зачекайте кілька хвилин перед наступною спробою.',
                                       csrf_token=session.get('csrf_token', '')), 429
            calls.append(now)
            return f(*args, **kwargs)
        return decorated
    return decorator


def generate_csrf_token() -> str:
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']


def validate_csrf(token: str) -> bool:
    return secrets.compare_digest(session.get('csrf_token', ''), token)


def csrf_protect(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
            if not validate_csrf(token or ''):
                abort(403)
        return f(*args, **kwargs)
    return decorated


def sanitize_input(text: str, max_length: int = 1000) -> str:
    if not text:
        return ''
    return str(text).strip()[:max_length]
