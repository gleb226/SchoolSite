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
    if role == 'student':
        return redirect(url_for('dashboard.student_dashboard'))
    elif role == 'parent':
        return redirect(url_for('dashboard.parent_dashboard'))
    elif role == 'teacher':
        return redirect(url_for('dashboard.teacher_dashboard'))
    elif role in ('admin', 'principal', 'vice_principal'):
        return redirect(url_for('admin.admin_dashboard'))
    else:
        return redirect(url_for('dashboard.student_dashboard'))


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
    return render_template('dashboard/parent.html', children=children_data)

