from flask import Blueprint, render_template, request, redirect, url_for, make_response, flash, session, g
from app.auth import check_password, generate_jwt, set_auth_cookie, clear_auth_cookie, hash_password, validate_password, generate_2fa_code
from app.models import UserModel
from app.database import get_db
from app.security import rate_limit, generate_csrf_token, validate_csrf, sanitize_input
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)
auth_bp = Blueprint('auth', __name__)

ROLE_LABELS = {
    'student': 'Учень',
    'parent': 'Батько/Мати',
    'teacher': 'Вчитель',
    'vice_principal': 'Заступник директора',
    'principal': 'Директор',
    'class_president': 'Президент класу',
    'school_president': 'Президент школи',
    'admin': 'Адміністратор',
}


@auth_bp.route('/login', methods=['GET', 'POST'])
@rate_limit(max_calls=30, period_seconds=60)
def login():
    if g.current_user:
        return redirect(url_for('dashboard.dashboard'))
    csrf_token = generate_csrf_token()
    error = None
    if request.method == 'POST':
        if not validate_csrf(request.form.get('csrf_token', '')):
            error = 'Недійсний CSRF токен'
        else:
            username = sanitize_input(request.form.get('username', ''))
            password = request.form.get('password', '')
            user = UserModel.get_by_username(username)
            if not user or not check_password(password, user['password_hash']):
                error = 'Невірний логін або пароль'
            elif not user['is_active']:
                error = 'Акаунт деактивовано'
            elif not user['is_approved']:
                error = 'Ваш акаунт очікує підтвердження адміністратором'
            if not error:
                if user['two_fa_enabled'] and user.get('telegram_id'):
                    code = generate_2fa_code()
                    db = get_db()
                    expires = (datetime.now() + timedelta(minutes=10)).isoformat()
                    db.execute('INSERT INTO tg_2fa_codes (user_id,code,expires_at) VALUES (?,?,?)',
                               (user['id'], code, expires))
                    db.commit()
                    try:
                        from app.bot.telegram_bot import send_2fa_code
                        import asyncio
                        asyncio.run(send_2fa_code(user['telegram_id'], code))
                    except Exception as e:
                        logger.warning(f'Could not send 2FA: {e}')
                    session['pending_2fa_user'] = user['id']
                    return redirect(url_for('auth.verify_2fa'))
                token = generate_jwt(user['id'], user['role'])
                resp = make_response(redirect(url_for('dashboard.dashboard')))
                set_auth_cookie(resp, token)
                return resp
    return render_template('auth/login.html', error=error, csrf_token=csrf_token, roles=ROLE_LABELS)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if g.current_user:
        return redirect(url_for('dashboard.dashboard'))
    csrf_token = generate_csrf_token()
    error = None
    if request.method == 'POST':
        if not validate_csrf(request.form.get('csrf_token', '')):
            error = 'Недійсний CSRF токен'
        else:
            full_name = sanitize_input(request.form.get('full_name', ''), 100)
            username = sanitize_input(request.form.get('username', ''), 50)
            email = sanitize_input(request.form.get('email', ''), 100)
            phone = sanitize_input(request.form.get('phone', ''), 20)
            password = request.form.get('password', '')
            confirm = request.form.get('confirm_password', '')
            role = 'student'
            ok, msg = validate_password(password)
            if not ok:
                error = msg
            elif password != confirm:
                error = 'Паролі не співпадають'
            elif not full_name or not username or not email:
                error = "Заповніть всі обов'язкові поля"
            else:
                db = get_db()
                existing = db.execute('SELECT id FROM users WHERE username=? OR email=?', (username, email)).fetchone()
                if existing:
                    error = 'Користувач з таким логіном або email вже існує'
                else:
                    from app.auth import hash_password as hp
                    ph = hp(password)
                    new_user_id = UserModel.create(username, email, ph, role, full_name, phone)
                    
                    # Notify admin on Telegram if configured
                    import os
                    admin_tg = os.environ.get('ADMIN_TELEGRAM_ID', '513546547').strip()
                    if admin_tg:
                        try:
                            from app.bot.telegram_bot import send_notification
                            import asyncio
                            notif_msg = (
                                f"<b>Нова реєстрація учня!</b>\n\n"
                                f"<b>ПІБ:</b> {full_name}\n"
                                f"<b>Логін:</b> {username}\n"
                                f"<b>Email:</b> {email}\n"
                                f"<b>Телефон:</b> {phone or 'не вказано'}\n\n"
                                f"<i>Зайдіть в адмін-панель для підтвердження.</i>"
                            )
                            asyncio.run(send_notification(int(admin_tg), notif_msg))
                        except Exception as ex:
                            logger.warning(f"Could not send registration tg notification: {ex}")

                    return render_template('auth/register_success.html')
    return render_template('auth/register.html', error=error, csrf_token=csrf_token)


@auth_bp.route('/verify-2fa', methods=['GET', 'POST'])
def verify_2fa():
    if 'pending_2fa_user' not in session:
        return redirect(url_for('auth.login'))
    csrf_token = generate_csrf_token()
    error = None
    if request.method == 'POST':
        if not validate_csrf(request.form.get('csrf_token', '')):
            error = 'Недійсний CSRF токен'
        else:
            code = sanitize_input(request.form.get('code', ''), 10)
            user_id = session['pending_2fa_user']
            db = get_db()
            row = db.execute(
                "SELECT * FROM tg_2fa_codes WHERE user_id=? AND code=? AND used=0 AND expires_at > CURRENT_TIMESTAMP",
                (user_id, code)
            ).fetchone()
            if not row:
                error = 'Невірний або прострочений код'
            else:
                db.execute('UPDATE tg_2fa_codes SET used=1 WHERE id=?', (row['id'],))
                db.commit()
                session.pop('pending_2fa_user', None)
                user = UserModel.get_by_id(user_id)
                token = generate_jwt(user['id'], user['role'])
                resp = make_response(redirect(url_for('dashboard.dashboard')))
                set_auth_cookie(resp, token)
                return resp
    return render_template('auth/verify_2fa.html', error=error, csrf_token=csrf_token)


@auth_bp.route('/logout')
def logout():
    session.clear()
    resp = make_response(redirect(url_for('public.index')))
    clear_auth_cookie(resp)
    return resp
