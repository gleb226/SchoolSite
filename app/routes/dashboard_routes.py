from flask import Blueprint, render_template, redirect, url_for, g, session, flash, abort, make_response, request
from app.auth import login_required, approved_required, generate_jwt, set_auth_cookie, hash_password, validate_password
from app.database import get_db
from app.models import UserModel, GradeModel, AttendanceModel
from app.security import generate_csrf_token, validate_csrf, sanitize_input
import os

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/dashboard')
@login_required
def dashboard():
    user = g.current_user
    role = user['role']
    if role in ('superuser', 'admin', 'principal', 'vice_principal'):
        return redirect(url_for('dashboard.superuser_dashboard'))
    elif role == 'student':
        return redirect(url_for('dashboard.student_dashboard'))
    elif role == 'parent':
        return redirect(url_for('dashboard.parent_dashboard'))
    elif role == 'teacher':
        return redirect(url_for('dashboard.teacher_dashboard'))
    else:
        return redirect(url_for('dashboard.student_dashboard'))


@dashboard_bp.route('/setup-superuser', methods=['GET', 'POST'])
def setup_superuser():
    """Створення superuser акаунту. Доступно тільки якщо немає жодного superuser."""
    db = get_db()
    csrf_token = generate_csrf_token()
    setup_secret = os.environ.get('DEV_SETUP_TOKEN', '').strip()

    has_superuser = db.execute(
        "SELECT 1 FROM users WHERE role='superuser' AND is_active=1 LIMIT 1"
    ).fetchone()
    error = None
    success = None

    if has_superuser:
        # Вже є — показуємо форму входу
        flash('Superuser вже існує. Увійдіть у свій акаунт.', 'info')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        if not validate_csrf(request.form.get('csrf_token', '')):
            error = 'Недійсний CSRF токен'
        elif setup_secret and request.form.get('setup_token', '').strip() != setup_secret:
            error = 'Невірний код розробника'
        else:
            full_name = sanitize_input(request.form.get('full_name', ''), 100)
            username  = sanitize_input(request.form.get('username', ''), 50)
            email     = sanitize_input(request.form.get('email', ''), 100)
            phone     = sanitize_input(request.form.get('phone', ''), 20)
            password  = request.form.get('password', '')

            ok, msg = validate_password(password)
            if not ok:
                error = msg
            elif not full_name or not username or not email:
                error = "Заповніть ПІБ, логін та email"
            elif db.execute('SELECT id FROM users WHERE username=? OR email=?',
                            (username, email)).fetchone():
                error = 'Користувач із таким логіном або email вже існує'
            else:
                db.execute(
                    '''INSERT INTO users
                       (username, email, password_hash, role, full_name, phone, is_approved, is_active)
                       VALUES (?,?,?,?,?,?,1,1)''',
                    (username, email, hash_password(password), 'superuser', full_name, phone)
                )
                db.commit()
                success = f'Superuser "{username}" створено! Тепер увійдіть.'

    return render_template('auth/setup_superuser.html',
                           csrf_token=csrf_token,
                           setup_token_required=bool(setup_secret),
                           error=error,
                           success=success)


@dashboard_bp.route('/setup-principal', methods=['GET', 'POST'])
def setup_principal():
    db = get_db()
    csrf_token = generate_csrf_token()
    setup_secret = os.environ.get('DEV_SETUP_TOKEN', '').strip()
    has_leadership = db.execute(
        "SELECT 1 FROM users WHERE role='principal' AND is_active=1 LIMIT 1"
    ).fetchone()
    error = None
    success = None

    if has_leadership:
        flash('Керівника вже створено. Увійдіть під директором, щоб керувати користувачами.', 'info')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        if not validate_csrf(request.form.get('csrf_token', '')):
            error = 'Недійсний CSRF токен'
        elif setup_secret and request.form.get('setup_token', '').strip() != setup_secret:
            error = 'Невірний код розробника'
        else:
            full_name = sanitize_input(request.form.get('full_name', ''), 100)
            username = sanitize_input(request.form.get('username', ''), 50)
            email = sanitize_input(request.form.get('email', ''), 100)
            phone = sanitize_input(request.form.get('phone', ''), 20)
            password = request.form.get('password', '')
            role = 'principal'
            ok, msg = validate_password(password)
            if not ok:
                error = msg
            elif not full_name or not username or not email:
                error = "Заповніть ПІБ, логін, email і пароль"
            elif db.execute('SELECT id FROM users WHERE username=? OR email=?', (username, email)).fetchone():
                error = 'Користувач із таким логіном або email вже існує'
            else:
                db.execute(
                    'INSERT INTO users (username,email,password_hash,role,full_name,phone,is_approved,is_active) VALUES (?,?,?,?,?,?,1,1)',
                    (username, email, hash_password(password), role, full_name, phone)
                )
                db.commit()
                success = 'Директора створено. Тепер увійдіть під цим акаунтом і підтверджуйте користувачів.'

    return render_template('auth/setup_principal.html',
                           csrf_token=csrf_token,
                           setup_token_required=bool(setup_secret),
                           error=error,
                           success=success)


@dashboard_bp.route('/dashboard/student')
@login_required
@approved_required
def student_dashboard():
    user = g.current_user
    db = get_db()
    
    # Grades for current student
    grades = db.execute(
        """SELECT g.*, s.name as subject_name FROM grades g
           JOIN subjects s ON g.subject_id = s.id
           WHERE g.student_id = ? ORDER BY g.date DESC LIMIT 10""",
        (user['id'],)
    ).fetchall()
    attendance = db.execute(
        """SELECT a.*, s.name as subject_name FROM attendance a
           JOIN subjects s ON a.subject_id = s.id
           WHERE a.student_id = ? ORDER BY a.date DESC LIMIT 10""",
        (user['id'],)
    ).fetchall()
    tests = db.execute(
        """SELECT t.* FROM tests t
           JOIN student_classes sc ON t.class_id = sc.class_id
           WHERE sc.student_id = ? AND t.is_active = 1""",
        (user['id'],)
    ).fetchall()

    return render_template('dashboard/student.html',
                           grades=[dict(g) for g in grades],
                           attendance=[dict(a) for a in attendance],
                           tests=[dict(t) for t in tests])


@dashboard_bp.route('/dashboard/teacher')
@login_required
@approved_required
def teacher_dashboard():
    user = g.current_user
    if user['role'] not in ('teacher', 'admin', 'principal', 'vice_principal'):
        return redirect(url_for('dashboard.student_dashboard'))
    db = get_db()
    
    if user['role'] in ('admin', 'principal', 'vice_principal'):
        classes = db.execute('SELECT * FROM classes ORDER BY name').fetchall()
        recent_grades = db.execute(
            """SELECT g.*, s.name as subject_name, u.full_name as student_name
               FROM grades g
               JOIN subjects s ON g.subject_id = s.id
               JOIN users u ON g.student_id = u.id
               ORDER BY g.created_at DESC LIMIT 10"""
        ).fetchall()
        tests = db.execute('SELECT * FROM tests ORDER BY created_at DESC').fetchall()
    else:
        classes = db.execute(
            'SELECT * FROM classes WHERE class_teacher_id = ? ORDER BY name',
            (user['id'],)
        ).fetchall()
        recent_grades = db.execute(
            """SELECT g.*, s.name as subject_name, u.full_name as student_name
               FROM grades g
               JOIN subjects s ON g.subject_id = s.id
               JOIN users u ON g.student_id = u.id
               WHERE g.teacher_id = ? ORDER BY g.created_at DESC LIMIT 10""",
            (user['id'],)
        ).fetchall()
        tests = db.execute('SELECT * FROM tests WHERE teacher_id = ? ORDER BY created_at DESC', (user['id'],)).fetchall()

    return render_template('dashboard/teacher.html',
                           classes=[dict(c) for c in classes],
                           recent_grades=[dict(g) for g in recent_grades],
                           tests=[dict(t) for t in tests])


@dashboard_bp.route('/dashboard/parent')
@login_required
@approved_required
def parent_dashboard():
    user = g.current_user
    db = get_db()
    children = db.execute(
        """SELECT u.* FROM users u JOIN parent_student ps ON u.id = ps.student_id
           WHERE ps.parent_id = ?""",
        (user['id'],)
    ).fetchall()

    children_data = []
    for child in children:
        child = dict(child)
        grades = db.execute(
            """SELECT g.*, s.name as subject_name FROM grades g
               JOIN subjects s ON g.subject_id = s.id
               WHERE g.student_id = ? ORDER BY g.date DESC LIMIT 5""",
            (child['id'],)
        ).fetchall()
        child['recent_grades'] = [dict(g) for g in grades]
        children_data.append(child)

    # ГПД показуємо тільки якщо хоч одна дитина — в класі 1-5
    gpd_children = db.execute(
        """SELECT u.id FROM users u
           JOIN parent_student ps ON u.id = ps.student_id
           JOIN student_classes sc ON sc.student_id = u.id
           JOIN classes c ON c.id = sc.class_id
           WHERE ps.parent_id = ?""",
        (user['id'],)
    ).fetchall()

    def _is_junior(class_name: str) -> bool:
        """Клас вважається молодшим (ГПД) якщо номер 1–5."""
        if not class_name:
            return False
        import re
        m = re.match(r'^(\d+)', class_name.strip())
        return bool(m and int(m.group(1)) <= 5)

    # Отримуємо назви класів де вчаться діти
    classes_of_children = db.execute(
        """SELECT c.name FROM classes c
           JOIN student_classes sc ON c.id = sc.class_id
           JOIN parent_student ps ON ps.student_id = sc.student_id
           WHERE ps.parent_id = ?""",
        (user['id'],)
    ).fetchall()
    has_gpd_children = any(_is_junior(r['name']) for r in classes_of_children)

    return render_template('dashboard/parent.html',
                           children=children_data,
                           has_gpd_children=has_gpd_children)



@dashboard_bp.route('/dashboard/superuser')
@login_required
def superuser_dashboard():
    user = g.current_user
    if user['role'] not in ('superuser', 'admin', 'principal', 'vice_principal'):
        from flask import abort
        abort(403)
    db = get_db()

    # ── Статистика (як адмін) ──────────────────────────────────────────────
    stats = {
        'total_users':   db.execute('SELECT COUNT(*) as c FROM users').fetchone()['c'],
        'pending_users': db.execute('SELECT COUNT(*) as c FROM users WHERE is_approved=0').fetchone()['c'],
        'students':      db.execute("SELECT COUNT(*) as c FROM users WHERE role='student'").fetchone()['c'],
        'teachers':      db.execute("SELECT COUNT(*) as c FROM users WHERE role='teacher'").fetchone()['c'],
        'classes':       db.execute('SELECT COUNT(*) as c FROM classes').fetchone()['c'],
        'tests':         db.execute('SELECT COUNT(*) as c FROM tests').fetchone()['c'],
        'grades':        db.execute('SELECT COUNT(*) as c FROM grades').fetchone()['c'],
    }

    # ── Заявки на підтвердження ────────────────────────────────────────────
    pending = [dict(r) for r in db.execute(
        "SELECT * FROM users WHERE is_approved=0 ORDER BY created_at DESC LIMIT 10"
    ).fetchall()]

    # ── Данні як вчитель ──────────────────────────────────────────────────
    classes = [dict(r) for r in db.execute('SELECT * FROM classes ORDER BY name').fetchall()]
    recent_grades = [dict(r) for r in db.execute(
        """SELECT g.*, s.name as subject_name, u.full_name as student_name
           FROM grades g
           JOIN subjects s ON g.subject_id=s.id
           JOIN users u ON g.student_id=u.id
           ORDER BY g.created_at DESC LIMIT 10"""
    ).fetchall()]
    tests = [dict(r) for r in db.execute(
        'SELECT * FROM tests ORDER BY created_at DESC LIMIT 10'
    ).fetchall()]

    # ── QR харчування (як учень) ───────────────────────────────────────────
    from datetime import date
    from app.routes.lunch import ensure_lunch_token
    today = date.today().isoformat()
    lunch_token = ensure_lunch_token(user['id'], today)
    lunch_rec = db.execute(
        'SELECT used, used_at FROM lunch_tokens WHERE student_id=? AND date=?',
        (user['id'], today)
    ).fetchone()

    # ── Харчування статистика (як касир) ─────────────────────────────────
    lunch_stats = {
        'fed_today': db.execute(
            'SELECT COUNT(*) as c FROM lunch_tokens WHERE date=? AND used=1', (today,)
        ).fetchone()['c'],
        'total_issued': db.execute(
            'SELECT COUNT(*) as c FROM lunch_tokens WHERE date=?', (today,)
        ).fetchone()['c'],
    }

    # ── Діти (як батько) ──────────────────────────────────────────────────
    children = [dict(r) for r in db.execute(
        """SELECT u.*, c.name as class_name
           FROM users u
           JOIN parent_student ps ON u.id=ps.student_id
           JOIN student_classes sc ON sc.student_id=u.id
           JOIN classes c ON c.id=sc.class_id
           WHERE ps.parent_id=?""",
        (user['id'],)
    ).fetchall()]

    return render_template(
        'dashboard/superuser.html',
        stats=stats,
        pending=pending,
        classes=classes,
        recent_grades=recent_grades,
        tests=tests,
        lunch_token=lunch_token,
        lunch_used=lunch_rec and lunch_rec['used'],
        lunch_used_at=lunch_rec['used_at'] if lunch_rec else None,
        lunch_stats=lunch_stats,
        children=children,
        today=today,
    )


@dashboard_bp.route('/link-telegram', methods=['GET', 'POST'])
@login_required
def link_telegram():
    """Сторінка швидкої та зручної прив'язки Telegram."""
    import secrets
    user = g.current_user
    db = get_db()
    bot_username = os.environ.get('BOT_USERNAME', 'MedelnBot').lstrip('@')

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'unlink':
            db.execute("UPDATE users SET telegram_id=NULL, telegram_username=NULL WHERE id=?", (user['id'],))
            db.commit()
            flash('Telegram успішно відв\'язано. Тепер ви можете підключити інший акаунт.', 'info')
            return redirect(url_for('dashboard.link_telegram'))

        # Збереження username або телефону
        tg_username_input = request.form.get('telegram_username', '').strip().lstrip('@')
        phone_input = request.form.get('phone', '').strip()

        updates = []
        params = []
        if tg_username_input:
            updates.append("telegram_username = ?")
            params.append(tg_username_input)
        if phone_input:
            updates.append("phone = ?")
            params.append(phone_input)

        if updates:
            params.append(user['id'])
            db.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
            db.commit()
            flash('Дані збережено! Тепер бот зможе знайти ваш акаунт автоматично.', 'success')
            return redirect(url_for('dashboard.link_telegram'))

    # Отримуємо свіжі дані користувача
    fresh_user = db.execute("SELECT * FROM users WHERE id=?", (user['id'],)).fetchone()
    already_linked = bool(fresh_user['telegram_id'])
    tg_username = fresh_user['telegram_username'] or ''

    # Генеруємо або беремо існуючий код для швидкого переходу
    code = None
    if tg_username and tg_username.startswith('link:'):
        code = tg_username[5:]
    else:
        code = secrets.token_hex(4).upper()
        if not already_linked:
            db.execute("UPDATE users SET telegram_username=? WHERE id=?", (f'link:{code}', user['id']))
            db.commit()

    tg_direct_link = f"https://t.me/{bot_username}?start=link_{code}"

    return render_template('auth/link_telegram.html',
                           already_linked=already_linked,
                           tg_username=tg_username if not tg_username.startswith('link:') else '',
                           phone=fresh_user['phone'] or '',
                           code=code,
                           bot_username=bot_username,
                           tg_direct_link=tg_direct_link)
