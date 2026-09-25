from flask import Blueprint, render_template, request, redirect, url_for, g, abort, flash
from app.auth import login_required, role_required
from app.database import get_db
from app.models import UserModel, NewsModel
from app.security import generate_csrf_token, validate_csrf, sanitize_input
import logging

logger = logging.getLogger(__name__)
admin_bp = Blueprint('admin', __name__)

ADMIN_ROLES = ('admin', 'principal', 'vice_principal')


@admin_bp.before_request
@login_required
def check_admin():
    user = g.get('current_user')
    if not user or user['role'] not in ADMIN_ROLES:
        abort(403)


@admin_bp.route('/')
def admin_dashboard():
    db = get_db()
    stats = {
        'total_users': db.execute('SELECT COUNT(*) as c FROM users').fetchone()['c'],
        'pending_users': db.execute('SELECT COUNT(*) as c FROM users WHERE is_approved=0').fetchone()['c'],
        'students': db.execute("SELECT COUNT(*) as c FROM users WHERE role='student'").fetchone()['c'],
        'teachers': db.execute("SELECT COUNT(*) as c FROM users WHERE role='teacher'").fetchone()['c'],
        'classes': db.execute('SELECT COUNT(*) as c FROM classes').fetchone()['c'],
        'news': db.execute('SELECT COUNT(*) as c FROM news').fetchone()['c'],
        'tests': db.execute('SELECT COUNT(*) as c FROM tests').fetchone()['c'],
        'grades': db.execute('SELECT COUNT(*) as c FROM grades').fetchone()['c'],
    }
    pending = [dict(r) for r in db.execute(
        "SELECT * FROM users WHERE is_approved=0 ORDER BY created_at DESC LIMIT 10"
    ).fetchall()]
    return render_template('dashboard/admin.html', stats=stats, pending=pending)


@admin_bp.route('/users')
def users():
    db = get_db()
    role_filter = request.args.get('role', '')
    q = 'SELECT * FROM users WHERE 1=1'
    params = []
    if role_filter:
        q += ' AND role = ?'
        params.append(role_filter)
    q += ' ORDER BY created_at DESC'
    user_list = [dict(r) for r in db.execute(q, params).fetchall()]
    return render_template('admin/users.html', users=user_list, role_filter=role_filter)


@admin_bp.route('/users/<int:user_id>', methods=['GET', 'POST'])
def edit_user(user_id):
    db = get_db()
    csrf_token = generate_csrf_token()
    user_obj = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    if not user_obj:
        abort(404)
    user_obj = dict(user_obj)
    error = None
    success = None

    if request.method == 'POST':
        action = request.form.get('action')
        if not validate_csrf(request.form.get('csrf_token', '')):
            error = 'Недійсний CSRF токен'
        elif action == 'approve':
            db.execute('UPDATE users SET is_approved=1 WHERE id=?', (user_id,))
            db.commit()
            success = 'Користувача підтверджено'
            user_obj['is_approved'] = 1
        elif action == 'reject':
            db.execute('UPDATE users SET is_approved=0, is_active=0 WHERE id=?', (user_id,))
            db.commit()
            success = 'Користувача відхилено'
        elif action == 'update_role':
            new_role = request.form.get('role')
            allowed = ('student','parent','teacher','vice_principal','principal','class_president','school_president','admin')
            if new_role in allowed:
                db.execute('UPDATE users SET role=? WHERE id=?', (new_role, user_id))
                db.commit()
                success = 'Роль оновлено'
                user_obj['role'] = new_role
        elif action == 'assign_class':
            class_id = request.form.get('class_id', type=int)
            if class_id:
                db.execute('INSERT OR IGNORE INTO student_classes (student_id,class_id) VALUES (?,?)',
                           (user_id, class_id))
                db.commit()
                success = 'Клас призначено'
        elif action == 'link_student':
            student_id = request.form.get('student_id', type=int)
            if student_id:
                db.execute('INSERT OR IGNORE INTO parent_student (parent_id,student_id) VALUES (?,?)',
                           (user_id, student_id))
                db.commit()
                success = 'Учня успішно прив\'язано до батьків'
        elif action == 'unlink_student':
            student_id = request.form.get('student_id', type=int)
            if student_id:
                db.execute('DELETE FROM parent_student WHERE parent_id=? AND student_id=?',
                           (user_id, student_id))
                db.commit()
                success = 'Зв\'язок видалено'
        elif action == 'link_parent':
            parent_id = request.form.get('parent_id', type=int)
            if parent_id:
                db.execute('INSERT OR IGNORE INTO parent_student (parent_id,student_id) VALUES (?,?)',
                           (parent_id, user_id))
                db.commit()
                success = 'Батьків успішно прив\'язано до учня'
        elif action == 'set_2fa':
            enabled = request.form.get('two_fa_enabled') == '1'
            db.execute('UPDATE users SET two_fa_enabled=? WHERE id=?', (1 if enabled else 0, user_id))
            db.commit()
            success = '2FA оновлено'
            user_obj['two_fa_enabled'] = 1 if enabled else 0

    classes = [dict(r) for r in db.execute('SELECT * FROM classes ORDER BY name').fetchall()]
    students = [dict(r) for r in db.execute("SELECT * FROM users WHERE role='student' AND is_approved=1 ORDER BY full_name").fetchall()]
    parents = [dict(r) for r in db.execute("SELECT * FROM users WHERE role='parent' AND is_approved=1 ORDER BY full_name").fetchall()]
    
    linked_students = [dict(r) for r in db.execute(
        "SELECT u.* FROM users u JOIN parent_student ps ON u.id=ps.student_id WHERE ps.parent_id=?", (user_id,)
    ).fetchall()]
    linked_parents = [dict(r) for r in db.execute(
        "SELECT u.* FROM users u JOIN parent_student ps ON u.id=ps.parent_id WHERE ps.student_id=?", (user_id,)
    ).fetchall()]

    return render_template('admin/edit_user.html', user_obj=user_obj, classes=classes,
                           students=students, parents=parents,
                           linked_students=linked_students, linked_parents=linked_parents,
                           csrf_token=csrf_token, error=error, success=success)


@admin_bp.route('/users/<int:user_id>/approve', methods=['POST'])
def approve_user(user_id):
    if not validate_csrf(request.form.get('csrf_token', '')):
        abort(403)
    db = get_db()
    db.execute('UPDATE users SET is_approved=1 WHERE id=?', (user_id,))
    db.commit()
    return redirect(url_for('admin.users'))


@admin_bp.route('/classes', methods=['GET', 'POST'])
def classes():
    db = get_db()
    csrf_token = generate_csrf_token()
    error = None
    if request.method == 'POST':
        if not validate_csrf(request.form.get('csrf_token', '')):
            error = 'Недійсний CSRF токен'
        else:
            action = request.form.get('action')
            if action == 'create':
                name = sanitize_input(request.form.get('name', ''), 20)
                year = request.form.get('year', type=int)
                teacher_id = request.form.get('teacher_id', type=int)
                if name and year:
                    db.execute('INSERT INTO classes (name,year,class_teacher_id) VALUES (?,?,?)',
                               (name, year, teacher_id))
                    db.commit()
            elif action == 'delete':
                class_id = request.form.get('class_id', type=int)
                if class_id:
                    db.execute('DELETE FROM classes WHERE id=?', (class_id,))
                    db.commit()
    classes_list = [dict(r) for r in db.execute(
        """SELECT cl.*, u.full_name as teacher_name,
           (SELECT COUNT(*) FROM student_classes sc WHERE sc.class_id=cl.id) as student_count
           FROM classes cl LEFT JOIN users u ON cl.class_teacher_id=u.id
           ORDER BY cl.name"""
    ).fetchall()]
    teachers = [dict(r) for r in db.execute(
        "SELECT * FROM users WHERE role='teacher' AND is_approved=1 ORDER BY full_name"
    ).fetchall()]
    return render_template('admin/classes.html', classes=classes_list, teachers=teachers,
                           csrf_token=csrf_token, error=error)


@admin_bp.route('/news', methods=['GET', 'POST'])
def manage_news():
    db = get_db()
    csrf_token = generate_csrf_token()
    error = None
    if request.method == 'POST':
        if not validate_csrf(request.form.get('csrf_token', '')):
            error = 'Недійсний CSRF токен'
        else:
            action = request.form.get('action')
            if action == 'create':
                title = sanitize_input(request.form.get('title', ''), 200)
                content = request.form.get('content', '')
                publish = request.form.get('publish') == '1'
                if title and content:
                    db.execute(
                        'INSERT INTO news (title,content,author_id,is_published) VALUES (?,?,?,?)',
                        (title, content, g.current_user['id'], 1 if publish else 0)
                    )
                    db.commit()
            elif action == 'publish':
                news_id = request.form.get('news_id', type=int)
                db.execute('UPDATE news SET is_published=1 WHERE id=?', (news_id,))
                db.commit()
            elif action == 'unpublish':
                news_id = request.form.get('news_id', type=int)
                db.execute('UPDATE news SET is_published=0 WHERE id=?', (news_id,))
                db.commit()
            elif action == 'delete':
                news_id = request.form.get('news_id', type=int)
                db.execute('DELETE FROM news WHERE id=?', (news_id,))
                db.commit()
    news_list = NewsModel.get_all()
    return render_template('admin/news.html', news_list=news_list, csrf_token=csrf_token, error=error)


@admin_bp.route('/subjects', methods=['GET', 'POST'])
def subjects():
    db = get_db()
    csrf_token = generate_csrf_token()
    error = None
    if request.method == 'POST':
        if not validate_csrf(request.form.get('csrf_token', '')):
            error = 'Недійсний CSRF токен'
        else:
            action = request.form.get('action')
            if action == 'create':
                name = sanitize_input(request.form.get('name', ''), 100)
                if name:
                    db.execute('INSERT INTO subjects (name) VALUES (?)', (name,))
                    db.commit()
            elif action == 'delete':
                subj_id = request.form.get('subject_id', type=int)
                if subj_id:
                    db.execute('DELETE FROM subjects WHERE id=?', (subj_id,))
                    db.commit()
    subjects_list = [dict(r) for r in db.execute('SELECT * FROM subjects ORDER BY name').fetchall()]
    return render_template('admin/subjects.html', subjects=subjects_list, csrf_token=csrf_token, error=error)
