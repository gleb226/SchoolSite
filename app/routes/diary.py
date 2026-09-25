from flask import Blueprint, render_template, request, redirect, url_for, flash, g, abort
from app.auth import login_required, approved_required, role_required
from app.database import get_db
from app.security import generate_csrf_token, validate_csrf, sanitize_input
from datetime import date
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)
diary_bp = Blueprint('diary', __name__)


@diary_bp.route('/grades')
@login_required
@approved_required
def grades():
    user = g.current_user
    db = get_db()
    subject_id = request.args.get('subject_id', type=int)
    grade_type = request.args.get('grade_type', '')

    q = """SELECT g.*, s.name as subject_name, u.full_name as teacher_name
           FROM grades g
           JOIN subjects s ON g.subject_id = s.id
           JOIN users u ON g.teacher_id = u.id
           WHERE g.student_id = ?"""
    params = [user['id']]
    if subject_id:
        q += ' AND g.subject_id = ?'
        params.append(subject_id)
    if grade_type:
        q += ' AND g.grade_type = ?'
        params.append(grade_type)
    q += ' ORDER BY g.date DESC'

    grades_list = [dict(r) for r in db.execute(q, params).fetchall()]
    grades_by_subject = defaultdict(list)
    grades_by_date = defaultdict(list)
    for grade in grades_list:
        grades_by_subject[grade['subject_name']].append(grade)
        grades_by_date[grade['date']].append(grade)

    subjects = [dict(r) for r in db.execute('SELECT * FROM subjects ORDER BY name').fetchall()]
    return render_template('diary/grades.html', grades=grades_list, subjects=subjects,
                           grades_by_subject=dict(grades_by_subject),
                           grades_by_date=dict(grades_by_date),
                           selected_subject=subject_id, selected_type=grade_type)


@diary_bp.route('/attendance')
@login_required
@approved_required
def attendance():
    user = g.current_user
    db = get_db()
    rows = db.execute(
        """SELECT a.*, s.name as subject_name
           FROM attendance a
           JOIN subjects s ON a.subject_id = s.id
           WHERE a.student_id = ? ORDER BY a.date DESC""",
        (user['id'],)
    ).fetchall()
    summary = {'present': 0, 'absent': 0, 'late': 0, 'excused': 0}
    for r in rows:
        summary[r['status']] = summary.get(r['status'], 0) + 1
    return render_template('diary/attendance.html',
                           attendance=[dict(r) for r in rows], summary=summary)


@diary_bp.route('/add-grade', methods=['GET', 'POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def add_grade():
    user = g.current_user
    db = get_db()
    csrf_token = generate_csrf_token()
    error = None
    success = None

    if request.method == 'POST':
        if not validate_csrf(request.form.get('csrf_token', '')):
            error = 'Недійсний CSRF токен'
        else:
            action = request.form.get('action', 'single')
            class_id = request.form.get('class_id', type=int)
            subject_id = request.form.get('subject_id', type=int)
            grade_type = sanitize_input(request.form.get('grade_type', 'current'))
            comment = sanitize_input(request.form.get('comment', ''), 200)
            grade_date = request.form.get('date', date.today().isoformat())
            lesson_number = request.form.get('lesson_number', type=int)
            lesson_topic = sanitize_input(request.form.get('lesson_topic', ''), 200)

            if action == 'grid':
                students = db.execute(
                    """SELECT u.id FROM users u
                       JOIN student_classes sc ON u.id=sc.student_id
                       WHERE sc.class_id=? AND u.role='student' AND u.is_approved=1""",
                    (class_id,)
                ).fetchall() if class_id else []
                saved = 0
                if not class_id or not subject_id or not grade_date:
                    error = "Оберіть клас, предмет і дату"
                else:
                    for student in students:
                        student_id = student['id']
                        raw_grade = request.form.get(f'grade_{student_id}', '').strip()
                        raw_comment = sanitize_input(request.form.get(f'comment_{student_id}', ''), 200)
                        if not raw_grade:
                            continue
                        try:
                            grade_val = int(raw_grade)
                        except ValueError:
                            error = 'Оцінки мають бути числами від 1 до 12'
                            break
                        if not (1 <= grade_val <= 12):
                            error = 'Оцінки мають бути від 1 до 12'
                            break
                        db.execute(
                            """INSERT INTO grades
                               (student_id,subject_id,teacher_id,grade,grade_type,comment,lesson_number,lesson_topic,date)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            (student_id, subject_id, user['id'], grade_val, grade_type,
                             raw_comment or comment, lesson_number, lesson_topic, grade_date)
                        )
                        saved += 1
                    if not error:
                        db.commit()
                        success = f'Збережено оцінок: {saved}'
            else:
                student_id = request.form.get('student_id', type=int)
                grade_val = request.form.get('grade', type=int)

                if not all([student_id, subject_id, grade_val]):
                    error = "Заповніть всі обов'язкові поля"
                elif not (1 <= grade_val <= 12):
                    error = 'Оцінка має бути від 1 до 12'
                else:
                    db.execute(
                        """INSERT INTO grades
                           (student_id,subject_id,teacher_id,grade,grade_type,comment,lesson_number,lesson_topic,date)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (student_id, subject_id, user['id'], grade_val, grade_type, comment,
                         lesson_number, lesson_topic, grade_date)
                    )
                    db.commit()
                    success = 'Оцінку додано успішно'

    classes = [dict(r) for r in db.execute('SELECT * FROM classes ORDER BY name').fetchall()]
    subjects = [dict(r) for r in db.execute('SELECT * FROM subjects ORDER BY name').fetchall()]
    students = [dict(r) for r in db.execute(
        "SELECT * FROM users WHERE role='student' AND is_approved=1 ORDER BY full_name"
    ).fetchall()]
    selected_class = request.args.get('class_id', type=int) or (classes[0]['id'] if classes else None)
    selected_subject = request.args.get('subject_id', type=int) or (subjects[0]['id'] if subjects else None)
    selected_date = request.args.get('date', date.today().isoformat())
    class_students = [dict(r) for r in db.execute(
        """SELECT u.* FROM users u
           JOIN student_classes sc ON u.id=sc.student_id
           WHERE sc.class_id=? AND u.role='student' AND u.is_approved=1
           ORDER BY u.full_name""",
        (selected_class,)
    ).fetchall()] if selected_class else []
    existing_grades = [dict(r) for r in db.execute(
        """SELECT g.*, u.full_name as student_name
           FROM grades g
           JOIN users u ON g.student_id=u.id
           WHERE g.subject_id=? AND g.date=? AND g.student_id IN (
               SELECT student_id FROM student_classes WHERE class_id=?
           )
           ORDER BY u.full_name, g.lesson_number, g.created_at""",
        (selected_subject, selected_date, selected_class)
    ).fetchall()] if selected_class and selected_subject else []
    return render_template('diary/add_grade.html',
                           classes=classes, subjects=subjects, students=students,
                           class_students=class_students,
                           existing_grades=existing_grades,
                           selected_class=selected_class,
                           selected_subject=selected_subject,
                           selected_date=selected_date,
                           csrf_token=csrf_token, error=error, success=success)


@diary_bp.route('/mark-attendance', methods=['GET', 'POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def mark_attendance():
    user = g.current_user
    db = get_db()
    csrf_token = generate_csrf_token()
    error = None
    success = None

    if request.method == 'POST':
        if not validate_csrf(request.form.get('csrf_token', '')):
            error = 'Недійсний CSRF токен'
        else:
            student_id = request.form.get('student_id', type=int)
            class_id = request.form.get('class_id', type=int)
            subject_id = request.form.get('subject_id', type=int)
            att_date = request.form.get('date', date.today().isoformat())
            status = request.form.get('status', 'present')
            note = sanitize_input(request.form.get('note', ''), 200)
            if status not in ('present', 'absent', 'late', 'excused'):
                error = 'Невірний статус'
            elif not all([student_id, class_id, subject_id]):
                error = "Заповніть всі обов'язкові поля"
            else:
                db.execute(
                    'INSERT OR REPLACE INTO attendance (student_id,class_id,subject_id,date,status,note) VALUES (?,?,?,?,?,?)',
                    (student_id, class_id, subject_id, att_date, status, note)
                )
                db.commit()
                success = 'Відвідуваність відмічено'

    classes = [dict(r) for r in db.execute('SELECT * FROM classes ORDER BY name').fetchall()]
    subjects = [dict(r) for r in db.execute('SELECT * FROM subjects ORDER BY name').fetchall()]
    students = [dict(r) for r in db.execute(
        "SELECT * FROM users WHERE role='student' AND is_approved=1 ORDER BY full_name"
    ).fetchall()]
    return render_template('diary/mark_attendance.html',
                           classes=classes, subjects=subjects, students=students,
                           csrf_token=csrf_token, error=error, success=success)
