from flask import Blueprint, render_template, request, redirect, url_for, g, abort, jsonify
from app.auth import login_required, approved_required, role_required
from app.database import get_db
from app.security import generate_csrf_token, validate_csrf, sanitize_input
from app.ai import CURRICULUM_TOPICS, generate_test_ai, generate_practice_test_ai
from datetime import datetime
import logging
import json
import random

logger = logging.getLogger(__name__)
tests_bp = Blueprint('tests', __name__)


def _student_can_access_test(db, user, test):
    if user['role'] != 'student':
        return False
    if test.get('is_practice'):
        return int(test.get('teacher_id') or 0) == int(user['id'])
    if not test.get('class_id'):
        return True
    row = db.execute(
        'SELECT 1 FROM student_classes WHERE student_id=? AND class_id=?',
        (user['id'], test['class_id'])
    ).fetchone()
    return row is not None


def _staff_can_access_test(user, test):
    if user['role'] in ('admin', 'principal', 'vice_principal'):
        return True
    return user['role'] == 'teacher' and int(test.get('teacher_id') or 0) == int(user['id'])


@tests_bp.route('/')
@login_required
@approved_required
def list_tests():
    user = g.current_user
    db = get_db()
    practice_tests = []
    
    if user['role'] == 'student':
        rows = db.execute(
            """SELECT t.*, s.name as subject_name, cl.name as class_name,
                      (SELECT COUNT(*) FROM test_attempts ta WHERE ta.test_id=t.id AND ta.student_id=? AND ta.status='finished') as attempts_done
               FROM tests t
               JOIN student_classes sc ON t.class_id = sc.class_id
               LEFT JOIN subjects s ON t.subject_id = s.id
               LEFT JOIN classes cl ON t.class_id = cl.id
               WHERE sc.student_id = ? AND t.is_active = 1 AND (t.is_practice = 0 OR t.is_practice IS NULL)
                 AND (t.variant_group_id IS NULL OR t.id = t.variant_group_id)
               ORDER BY t.created_at DESC""",
            (user['id'], user['id'])
        ).fetchall()
            
        practice_tests = [dict(r) for r in db.execute(
            """SELECT t.*, s.name as subject_name,
                      (SELECT COUNT(*) FROM test_attempts ta WHERE ta.test_id=t.id AND ta.student_id=? AND ta.status='finished') as attempts_done,
                      (SELECT MAX(score) FROM test_attempts ta WHERE ta.test_id=t.id AND ta.student_id=?) as best_score
               FROM tests t
               LEFT JOIN subjects s ON t.subject_id = s.id
               WHERE t.is_practice = 1 AND t.is_active = 1 AND t.teacher_id = ?
               ORDER BY t.created_at DESC LIMIT 10""",
            (user['id'], user['id'], user['id'])
        ).fetchall()]
        completed_topics = [dict(r) for r in db.execute(
            """SELECT ct.topic, ct.subject_id, s.name as subject_name, MAX(ct.completed_at) as completed_at
               FROM completed_topics ct
               LEFT JOIN subjects s ON ct.subject_id = s.id
               WHERE ct.student_id = ?
               GROUP BY ct.subject_id, ct.topic
               ORDER BY completed_at DESC""",
            (user['id'],)
        ).fetchall()]
    else:
        completed_topics = []
        if user['role'] in ('admin', 'principal', 'vice_principal'):
            rows = db.execute(
                """SELECT t.*, s.name as subject_name, cl.name as class_name,
                          (CASE WHEN t.variant_group_id IS NOT NULL AND t.id = t.variant_group_id
                                THEN (SELECT COUNT(*) FROM tests t2 WHERE t2.variant_group_id = t.variant_group_id AND t2.is_active=1)
                                ELSE NULL END) as variant_count
                   FROM tests t
                   LEFT JOIN subjects s ON t.subject_id = s.id
                   LEFT JOIN classes cl ON t.class_id = cl.id
                   WHERE t.is_practice = 0
                     AND (t.variant_group_id IS NULL OR t.id = t.variant_group_id)
                   ORDER BY t.created_at DESC"""
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT t.*, s.name as subject_name, cl.name as class_name,
                          (CASE WHEN t.variant_group_id IS NOT NULL AND t.id = t.variant_group_id
                                THEN (SELECT COUNT(*) FROM tests t2 WHERE t2.variant_group_id = t.variant_group_id AND t2.is_active=1)
                                ELSE NULL END) as variant_count
                   FROM tests t
                   LEFT JOIN subjects s ON t.subject_id = s.id
                   LEFT JOIN classes cl ON t.class_id = cl.id
                   WHERE t.teacher_id = ? AND t.is_practice = 0
                     AND (t.variant_group_id IS NULL OR t.id = t.variant_group_id)
                   ORDER BY t.created_at DESC""",
                (user['id'],)
            ).fetchall()

    subjects = [dict(r) for r in db.execute('SELECT * FROM subjects ORDER BY name').fetchall()]
    return render_template('tests/list.html',
                           tests=[dict(r) for r in rows],
                           practice_tests=practice_tests,
                           completed_topics=completed_topics,
                           curriculum_topics=CURRICULUM_TOPICS,
                           subjects=subjects,
                           csrf_token=generate_csrf_token())



@tests_bp.route('/preview/<int:test_id>')
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def preview_test(test_id):
    """Teacher preview — shows the test without anticheat, no attempt recorded."""
    user = g.current_user
    db = get_db()
    test = db.execute('SELECT * FROM tests WHERE id=?', (test_id,)).fetchone()
    if not test:
        abort(404)
    test = dict(test)
    if not _staff_can_access_test(user, test):
        abort(403)

    questions = db.execute(
        'SELECT * FROM questions WHERE test_id=? ORDER BY order_num', (test_id,)
    ).fetchall()
    questions_data = []
    for q in questions:
        q = dict(q)
        if q['question_type'] in ('single', 'multiple'):
            opts = db.execute('SELECT id, option_text FROM answer_options WHERE question_id=?', (q['id'],)).fetchall()
            q['options'] = [dict(o) for o in opts]
        else:
            q['options'] = []
        questions_data.append(q)

    return render_template('tests/preview.html', test=test, questions=questions_data)


@tests_bp.route('/take/<int:test_id>')
def take_test(test_id):
    from flask import session as flask_session
    user = g.current_user

    # Guest support: allow users who joined via session with a name in flask_session
    guest_name = None
    is_guest = False
    if not user:
        guest_name = flask_session.get('guest_name')
        guest_test_id = flask_session.get('guest_test_id')
        if guest_name and guest_test_id == test_id:
            is_guest = True
        else:
            # Not logged in and not a valid guest — redirect to login
            return redirect(url_for('auth.login', next=request.url))

    is_staff_preview = (not is_guest) and user is not None and user['role'] in ('admin', 'principal', 'vice_principal', 'teacher')
    db = get_db()

    if is_guest:
        # Guest: load any active test
        test = db.execute('SELECT * FROM tests WHERE id=? AND is_active=1', (test_id,)).fetchone()
    elif is_staff_preview:
        test = db.execute('SELECT * FROM tests WHERE id=?', (test_id,)).fetchone()
    else:
        if user['role'] != 'student':
            abort(403)
        test = db.execute('SELECT * FROM tests WHERE id=? AND is_active=1', (test_id,)).fetchone()

    if not test:
        abort(404)
    test = dict(test)

    # Guests bypass class/approval checks — they joined via session link
    if not is_guest and not is_staff_preview and not _student_can_access_test(db, user, test):
        abort(403)

    # For guests: create a lightweight pseudo-attempt (no student_id row in DB)
    # Render take.html with guest_name; answers won't be graded/stored
    if is_guest:
        questions = db.execute(
            'SELECT * FROM questions WHERE test_id=? ORDER BY order_num', (test_id,)
        ).fetchall()
        questions_data = [dict(q) for q in questions]
        for q in questions_data:
            if q['question_type'] in ('single', 'multiple'):
                opts = db.execute('SELECT id, option_text FROM answer_options WHERE question_id=?', (q['id'],)).fetchall()
                q['options'] = [dict(o) for o in opts]
                random.shuffle(q['options'])
            else:
                q['options'] = []
        random.shuffle(questions_data)
        return render_template('tests/take.html', test=test, questions=questions_data,
                               attempt={'id': 0, 'question_order': None, 'variant_number': None},
                               is_staff_preview=False, guest_name=guest_name)

    if not is_staff_preview:
        # Check attempts — students only.
        # If the student is joining via an active session, allow a fresh attempt
        # regardless of previous attempts (each session = new attempt allowed).
        via_session = db.execute(
            """SELECT 1 FROM test_session_participants tsp
               JOIN test_sessions ts ON tsp.session_id = ts.id
               WHERE tsp.student_id = ? AND ts.test_id = ? AND ts.status IN ('waiting','active')
               LIMIT 1""",
            (user['id'], test_id)
        ).fetchone()

        if not via_session:
            # Not in an active session — apply the normal attempt limit
            done = db.execute(
                "SELECT COUNT(*) as c FROM test_attempts WHERE test_id=? AND student_id=? AND status IN ('finished','disqualified')",
                (test_id, user['id'])
            ).fetchone()['c']
            if done >= test['max_attempts']:
                return render_template('tests/already_done.html', test=test)

    # Create or resume attempt.
    # If the student is in an active session, always create a NEW attempt so that
    # each session run is tracked independently (even if the student already has
    # a previous finished/in_progress attempt for this test).
    if not is_staff_preview and user['role'] == 'student':
        via_session_row = db.execute(
            """SELECT ts.id as session_id FROM test_session_participants tsp
               JOIN test_sessions ts ON tsp.session_id = ts.id
               WHERE tsp.student_id = ? AND ts.test_id = ? AND ts.status IN ('waiting','active')
               LIMIT 1""",
            (user['id'], test_id)
        ).fetchone()
    else:
        via_session_row = None

    if via_session_row:
        # Session mode: look for an in_progress attempt that is linked to THIS session
        session_id_for_attempt = via_session_row['session_id']
        attempt = db.execute(
            """SELECT ta.* FROM test_attempts ta
               JOIN test_session_participants tsp ON tsp.attempt_id = ta.id
               WHERE ta.test_id=? AND ta.student_id=? AND ta.status='in_progress'
                 AND tsp.session_id=?""",
            (test_id, user['id'], session_id_for_attempt)
        ).fetchone()
        if not attempt:
            # Create a brand-new attempt for this session
            db.execute('INSERT INTO test_attempts (test_id, student_id) VALUES (?,?)', (test_id, user['id']))
            db.commit()
            attempt = db.execute(
                "SELECT * FROM test_attempts WHERE test_id=? AND student_id=? AND status='in_progress' ORDER BY id DESC LIMIT 1",
                (test_id, user['id'])
            ).fetchone()
    else:
        # Normal mode: resume existing in_progress attempt or create new
        attempt = db.execute(
            "SELECT * FROM test_attempts WHERE test_id=? AND student_id=? AND status='in_progress'",
            (test_id, user['id'])
        ).fetchone()
        if not attempt:
            db.execute('INSERT INTO test_attempts (test_id, student_id) VALUES (?,?)', (test_id, user['id']))
            db.commit()
            attempt = db.execute(
                "SELECT * FROM test_attempts WHERE test_id=? AND student_id=? AND status='in_progress'",
                (test_id, user['id'])
            ).fetchone()
    attempt = dict(attempt)

    # Link attempt_id into session participants if student joined via a session
    if not is_staff_preview and user['role'] == 'student':
        db.execute(
            """UPDATE test_session_participants
               SET attempt_id = ?
               WHERE student_id = ? AND session_id IN (
                   SELECT id FROM test_sessions
                   WHERE test_id = ? AND status IN ('waiting','active')
               ) AND (attempt_id IS NULL OR attempt_id != ?)""",
            (attempt['id'], user['id'], test_id, attempt['id'])
        )
        db.commit()
    questions = db.execute(
        'SELECT * FROM questions WHERE test_id=? ORDER BY order_num', (test_id,)
    ).fetchall()
    questions_data = [dict(q) for q in questions]

    # Load options for each question
    for q in questions_data:
        if q['question_type'] in ('single', 'multiple'):
            opts = db.execute('SELECT id, option_text FROM answer_options WHERE question_id=?', (q['id'],)).fetchall()
            q['options'] = [dict(o) for o in opts]
        else:
            q['options'] = []

    # Apply shuffle if enabled
    if test.get('shuffle_questions') or test.get('shuffle_options'):
        saved_order = attempt.get('question_order')
        if saved_order:
            # Restore previously saved order so the student always sees the same arrangement
            order_data = json.loads(saved_order)
            qmap = {q['id']: q for q in questions_data}
            ordered_questions = []
            for entry in order_data:
                q = qmap.get(entry['qid'])
                if q:
                    if 'opt_order' in entry:
                        omap = {o['id']: o for o in q['options']}
                        q['options'] = [omap[oid] for oid in entry['opt_order'] if oid in omap]
                    ordered_questions.append(q)
            questions_data = ordered_questions
        else:
            # First visit — create and persist a shuffled order
            if test.get('shuffle_questions'):
                random.shuffle(questions_data)
            order_data = []
            for q in questions_data:
                entry = {'qid': q['id']}
                if test.get('shuffle_options') and q['options']:
                    random.shuffle(q['options'])
                    entry['opt_order'] = [o['id'] for o in q['options']]
                order_data.append(entry)
            db.execute('UPDATE test_attempts SET question_order=? WHERE id=?',
                       (json.dumps(order_data), attempt['id']))
            db.commit()

    # Variant assignment: if this test belongs to a variant group, assign silently.
    # NO redirect — we load the correct variant's questions directly.
    # Assignment order:
    #   1. If student joined via a session → assign by join order (1st joined=variant1, 2nd=variant2, ...)
    #   2. Otherwise → assign by join order across all participants of this test
    #   3. If student already has an assignment → keep it
    if not is_staff_preview and user['role'] == 'student' and test.get('variant_group_id'):
        variants = db.execute(
            'SELECT id FROM tests WHERE variant_group_id=? AND is_active=1 ORDER BY id',
            (test['variant_group_id'],)
        ).fetchall()

        if variants and len(variants) > 1:
            num_variants = len(variants)

            # Check if already assigned in session participants
            session_part = db.execute(
                """SELECT tsp.variant_number, tsp.session_id FROM test_session_participants tsp
                   JOIN test_sessions ts ON tsp.session_id = ts.id
                   WHERE tsp.student_id = ? AND ts.test_id = ? AND ts.status IN ('waiting','active')
                   ORDER BY tsp.joined_at ASC LIMIT 1""",
                (user['id'], test_id)
            ).fetchone()

            assigned_variant_id = None

            if session_part and session_part['variant_number']:
                # Already assigned in this session
                assigned_variant_id = variants[session_part['variant_number'] - 1]['id']
            elif session_part:
                # In session but not yet assigned — assign by join order in this session
                join_index = db.execute(
                    """SELECT COUNT(*) as c FROM test_session_participants
                       WHERE session_id = ? AND joined_at <= (
                           SELECT joined_at FROM test_session_participants
                           WHERE session_id = ? AND student_id = ?
                       )""",
                    (session_part['session_id'], session_part['session_id'], user['id'])
                ).fetchone()['c']
                variant_num = ((join_index - 1) % num_variants) + 1  # 1-based
                assigned_variant_id = variants[variant_num - 1]['id']
                # Save variant_number in session participants
                db.execute(
                    'UPDATE test_session_participants SET variant_number=? WHERE session_id=? AND student_id=?',
                    (variant_num, session_part['session_id'], user['id'])
                )
                db.commit()
            else:
                # No active session — check if student already has an attempt for this group
                existing = db.execute(
                    """SELECT ta.test_id, ta.variant_number FROM test_attempts ta
                       JOIN tests t ON ta.test_id = t.id
                       WHERE ta.student_id = ? AND t.variant_group_id = ?
                         AND ta.status IN ('in_progress','finished','disqualified')
                       ORDER BY ta.id ASC LIMIT 1""",
                    (user['id'], test['variant_group_id'])
                ).fetchone()
                if existing:
                    assigned_variant_id = existing['test_id']
                else:
                    # Count how many unique students already assigned in this group
                    assigned_count = db.execute(
                        """SELECT COUNT(DISTINCT ta.student_id) as c FROM test_attempts ta
                           JOIN tests t ON ta.test_id = t.id
                           WHERE t.variant_group_id = ? AND ta.status IN ('in_progress','finished','disqualified')""",
                        (test['variant_group_id'],)
                    ).fetchone()['c']
                    variant_num = (assigned_count % num_variants) + 1
                    assigned_variant_id = variants[variant_num - 1]['id']

            # If the assigned variant differs from current test_id, load that variant's questions
            if assigned_variant_id and assigned_variant_id != test_id:
                variant_test = db.execute('SELECT * FROM tests WHERE id=?', (assigned_variant_id,)).fetchone()
                if variant_test:
                    # Update attempt to point to correct test
                    db.execute('UPDATE test_attempts SET test_id=?, variant_number=? WHERE id=?',
                               (assigned_variant_id, assigned_variant_id, attempt['id']))
                    db.commit()
                    # Reload questions from correct variant
                    test = dict(variant_test)
                    questions = db.execute(
                        'SELECT * FROM questions WHERE test_id=? ORDER BY order_num',
                        (assigned_variant_id,)
                    ).fetchall()
                    questions_data = [dict(q) for q in questions]
                    for q in questions_data:
                        if q['question_type'] in ('single', 'multiple'):
                            opts = db.execute('SELECT id, option_text FROM answer_options WHERE question_id=?', (q['id'],)).fetchall()
                            q['options'] = [dict(o) for o in opts]
                        else:
                            q['options'] = []
            elif assigned_variant_id == test_id:
                # Correct variant — save variant_number in attempt
                db.execute('UPDATE test_attempts SET variant_number=? WHERE id=?',
                           (test_id, attempt['id']))
                db.commit()

    return render_template('tests/take.html', test=test, questions=questions_data, attempt=attempt,
                           is_staff_preview=is_staff_preview)


@tests_bp.route('/create', methods=['GET', 'POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def create_test():
    user = g.current_user
    db = get_db()
    csrf_token = generate_csrf_token()
    error = None

    if request.method == 'POST':
        if not validate_csrf(request.form.get('csrf_token', '')):
            error = 'Недійсний CSRF токен'
        else:
            title = sanitize_input(request.form.get('title', ''), 200)
            description = sanitize_input(request.form.get('description', ''), 500)
            class_id = request.form.get('class_id', type=int)
            subject_id = request.form.get('subject_id', type=int)
            time_limit = request.form.get('time_limit_minutes', 45, type=int)
            max_attempts = request.form.get('max_attempts', 1, type=int)
            topic = sanitize_input(request.form.get('topic', ''), 200)
            shuffle_questions = 1 if request.form.get('shuffle_questions') else 0
            shuffle_options = 1 if request.form.get('shuffle_options') else 0

            # Multi-variant data sent as hidden JSON field by JS
            variants_json = request.form.get('variants_data_json', '')
            num_variants = request.form.get('num_variants', 1, type=int)

            if not title or not class_id or not subject_id:
                error = "Заповніть всі обов'язкові поля"
            else:
                # Parse questions from form
                q_texts = request.form.getlist('question_text[]')
                q_types = request.form.getlist('question_type[]')
                q_points = request.form.getlist('question_points[]')
                q_explanations = request.form.getlist('question_explanation[]')
                q_images = request.form.getlist('q_image[]')  # base64 or URL per question

                def _save_test_with_questions(test_title, questions_list, variant_group_id_val=None):
                    """Save one test + its questions, return new test_id."""
                    db.execute(
                        'INSERT INTO tests (title,description,teacher_id,class_id,subject_id,'
                        'time_limit_minutes,max_attempts,is_active,is_practice,topic,'
                        'shuffle_questions,shuffle_options,variant_group_id) VALUES (?,?,?,?,?,?,?,1,0,?,?,?,?)',
                        (test_title, description, user['id'], class_id, subject_id,
                         time_limit, max_attempts, topic,
                         shuffle_questions, shuffle_options, variant_group_id_val)
                    )
                    db.commit()
                    tid = db.execute('SELECT last_insert_rowid() as id').fetchone()['id']

                    for i, (qt, qtype, qpts) in enumerate(zip(
                        [q['question_text'] for q in questions_list],
                        [q.get('question_type', 'single') for q in questions_list],
                        [q.get('points', 1) for q in questions_list]
                    )):
                        qt = sanitize_input(qt, 500)
                        if not qt:
                            continue
                        try:
                            pts = int(qpts)
                        except (TypeError, ValueError):
                            pts = 1
                        expl = sanitize_input(
                            questions_list[i].get('explanation', '') if isinstance(questions_list[i], dict) else '',
                            1000
                        )
                        img_url = questions_list[i].get('image_url', '') if isinstance(questions_list[i], dict) else ''
                        db.execute(
                            'INSERT INTO questions (test_id,question_text,question_type,points,order_num,explanation,image_url) VALUES (?,?,?,?,?,?,?)',
                            (tid, qt, qtype, pts, i + 1, expl, img_url or None)
                        )
                        db.commit()
                        q_id = db.execute('SELECT last_insert_rowid() as id').fetchone()['id']

                        if qtype in ('single', 'multiple'):
                            for opt in questions_list[i].get('options', []):
                                opt_text = sanitize_input(opt.get('text', ''), 300)
                                if not opt_text:
                                    continue
                                db.execute(
                                    'INSERT INTO answer_options (question_id,option_text,is_correct) VALUES (?,?,?)',
                                    (q_id, opt_text, 1 if opt.get('is_correct') else 0)
                                )
                            db.commit()
                    return tid

                # Check if we have multi-variant JSON data
                if variants_json and num_variants > 1:
                    try:
                        variants_list = json.loads(variants_json)
                    except (json.JSONDecodeError, TypeError):
                        variants_list = None

                    if variants_list and len(variants_list) >= 1:
                        # Save all variants — NO "(Варіант N)" in title
                        # All share the same base title; variant_group_id links them
                        variant_group_id = None
                        for vdata in variants_list[:num_variants]:
                            questions_for_variant = vdata.get('questions', [])
                            tid = _save_test_with_questions(title, questions_for_variant, variant_group_id)
                            if variant_group_id is None:
                                variant_group_id = tid
                                db.execute('UPDATE tests SET variant_group_id=? WHERE id=?', (variant_group_id, tid))
                                db.commit()
                        return redirect(url_for('tests.list_tests'))

                # Single test (or fallback) — save from form fields normally
                db.execute(
                    'INSERT INTO tests (title,description,teacher_id,class_id,subject_id,'
                    'time_limit_minutes,max_attempts,is_active,is_practice,topic,'
                    'shuffle_questions,shuffle_options) VALUES (?,?,?,?,?,?,?,1,0,?,?,?)',
                    (title, description, user['id'], class_id, subject_id,
                     time_limit, max_attempts, topic, shuffle_questions, shuffle_options)
                )
                db.commit()
                test_id = db.execute('SELECT last_insert_rowid() as id').fetchone()['id']

                for i, (qt, qtype, qpts) in enumerate(zip(q_texts, q_types, q_points)):
                    qt = sanitize_input(qt, 500)
                    if not qt:
                        continue
                    try:
                        pts = int(qpts)
                    except (ValueError, TypeError):
                        pts = 1
                    expl = sanitize_input(q_explanations[i] if i < len(q_explanations) else '', 1000)
                    img_url = q_images[i] if i < len(q_images) else ''
                    db.execute(
                        'INSERT INTO questions (test_id,question_text,question_type,points,order_num,explanation,image_url) VALUES (?,?,?,?,?,?,?)',
                        (test_id, qt, qtype, pts, i + 1, expl, img_url or None)
                    )
                    db.commit()
                    q_id = db.execute('SELECT last_insert_rowid() as id').fetchone()['id']

                    if qtype in ('single', 'multiple'):
                        opts = request.form.getlist(f'options_{i}[]')
                        correct = request.form.getlist(f'correct_{i}[]')
                        for j, opt_text in enumerate(opts):
                            opt_text = sanitize_input(opt_text, 300)
                            if not opt_text:
                                continue
                            is_correct = str(j) in correct
                            db.execute('INSERT INTO answer_options (question_id,option_text,is_correct) VALUES (?,?,?)',
                                       (q_id, opt_text, 1 if is_correct else 0))
                        db.commit()

                return redirect(url_for('tests.list_tests'))

    classes = [dict(r) for r in db.execute('SELECT * FROM classes ORDER BY name').fetchall()]
    subjects = [dict(r) for r in db.execute('SELECT * FROM subjects ORDER BY name').fetchall()]
    return render_template('tests/create.html', classes=classes, subjects=subjects,
                           curriculum_topics=CURRICULUM_TOPICS,
                           csrf_token=csrf_token, error=error)


@tests_bp.route('/api/ai-generate', methods=['POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def ai_generate():
    data = request.get_json() or {}
    subject = sanitize_input(data.get('subject', 'Математика'), 100)
    topic = sanitize_input(data.get('topic', ''), 200)
    class_name = sanitize_input(data.get('class_name', ''), 50)
    count = data.get('question_count', 5)
    difficulty = sanitize_input(data.get('difficulty', 'середній'), 50)
    question_mode = sanitize_input(data.get('question_mode', 'mixed'), 50)
    num_variants = max(1, min(30, int(data.get('num_variants', 1) or 1)))
    variant_type = sanitize_input(data.get('variant_type', 'shuffle'), 20)
    generate_images = bool(data.get('generate_images', False))
    # shuffle and numbers are always on — these flags kept for compatibility
    shuffle_questions = True
    shuffle_options = True

    if not topic:
        return jsonify({'error': 'Будь ласка, вкажіть тему тесту'}), 400

    NUMBERS_NOTE = (
        "ВАЖЛИВО: Всі числові дані в задачах (розміри, маси, швидкості, координати, коефіцієнти тощо) "
        "мають бути підібрані так, щоб задачі мали РЕАЛЬНЕ ЦІЛІСНЕ або ПРОСТЕ ДРОБОВЕ РОЗВ'ЯЗАННЯ. "
        "Не використовуй числа, які дають нескінченні дроби або нерозв'язні рівняння. "
        "Перевір кожну задачу: відповідь має бути конкретним числом, яке виходить стандартними методами рівня цієї теми."
    )

    try:
        from app.ai import _generate_test_ai_once, enrich_questions_with_images
        import time as _time

        if variant_type == 'numbers':
            # For math/physics/chemistry: generate with pedagogically correct numbers
            generated = _generate_test_ai_once(
                subject, topic, class_name, count, difficulty, question_mode,
                batch_note=NUMBERS_NOTE
            )
        else:
            generated = generate_test_ai(subject, topic, class_name, count, difficulty, question_mode)

        # Single variant — return data, form handles saving
        if num_variants <= 1:
            if generate_images:
                enrich_questions_with_images(generated['questions'], subject, max_images=5)
            return jsonify({'success': True, 'data': generated})

        # Multi-variant: generate all variants now, return for editor + hidden field on save
        variants_data = [generated]

        if variant_type == 'unique' or (variant_type == 'shuffle' and num_variants >= 2):
            # Fully unique questions per variant
            for i in range(1, num_variants):
                _time.sleep(3)
                v = generate_test_ai(subject, topic, class_name, count, difficulty, question_mode)
                variants_data.append(v)

        elif variant_type == 'numbers':
            # Same task types, different (pedagogically valid) numbers per variant
            for i in range(1, num_variants):
                _time.sleep(3)
                note = (
                    f"Це варіант {i+1} з {num_variants}. "
                    "Збережи ту саму структуру та типи завдань, але ОБОВ'ЯЗКОВО зміни всі числові дані "
                    "(коефіцієнти, розміри, швидкості, маси, координати тощо) на інші. "
                    "Нові числа мають бути підібрані так, щоб задачі ЗАЛИШАЛИСЬ РОЗВ'ЯЗНИМИ — "
                    "відповідь має бути цілим або простим дробовим числом, без нескінченних дробів. "
                    "Не повторюй числа з попередніх варіантів."
                )
                v = _generate_test_ai_once(subject, topic, class_name, count, difficulty, question_mode, batch_note=note)
                variants_data.append(v)

        else:
            # shuffle — same questions for all variants (shuffled per student at runtime)
            variants_data = [generated] * num_variants

        if generate_images:
            # Enrich only the first variant (images are the same for all variants)
            enrich_questions_with_images(variants_data[0]['questions'], subject, max_images=5)
            # Sync images to other variants that share the same question list ref
            for vd in variants_data[1:]:
                for i, q in enumerate(vd.get('questions', [])):
                    if i < len(variants_data[0]['questions']):
                        q['image_url'] = variants_data[0]['questions'][i].get('image_url')

        return jsonify({
            'success': True,
            'data': variants_data[0],
            'variants_data': variants_data,
            'num_variants': num_variants,
            'variant_type': variant_type,
        })

    except Exception as e:
        logger.error(f'AI test generation error: {e}')
        return jsonify({'error': f'Помилка генерації AI: {str(e)}'}), 500


@tests_bp.route('/api/generate-variants', methods=['POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def generate_variants():
    data = request.get_json() or {}
    subject = sanitize_input(data.get('subject', 'Математика'), 100)
    topic = sanitize_input(data.get('topic', ''), 200)
    class_name = sanitize_input(data.get('class_name', ''), 50)
    count = data.get('question_count', 5)
    difficulty = sanitize_input(data.get('difficulty', 'середній'), 50)
    num_variants = max(2, min(30, int(data.get('num_variants', 2))))
    class_id = data.get('class_id')
    subject_id = data.get('subject_id')
    time_limit = data.get('time_limit_minutes', 45)
    max_attempts = data.get('max_attempts', 1)
    variant_type = sanitize_input(data.get('variant_type', 'unique'), 20)  # shuffle | unique | numbers
    question_mode = sanitize_input(data.get('question_mode', 'auto'), 50)

    if not topic:
        return jsonify({'error': 'Вкажіть тему'}), 400

    user = g.current_user
    db = get_db()
    created_ids = []
    variant_group_id = None

    try:
        if variant_type == 'shuffle':
            # Generate ONE test, then create N copies with shuffle enabled
            gen = generate_test_ai(subject, topic, class_name, count, difficulty, question_mode)
            for i in range(num_variants):
                title = f"{gen['title']} (Варіант {i+1})"
                db.execute(
                    '''INSERT INTO tests (title,description,teacher_id,class_id,subject_id,
                       time_limit_minutes,max_attempts,is_active,is_practice,topic,
                       shuffle_questions,shuffle_options,variant_group_id)
                       VALUES (?,?,?,?,?,?,?,1,0,?,1,1,?)''',
                    (title, gen.get('description', ''), user['id'], class_id, subject_id,
                     time_limit, max_attempts, topic, variant_group_id)
                )
                db.commit()
                test_id = db.execute('SELECT last_insert_rowid() as id').fetchone()['id']
                if variant_group_id is None:
                    variant_group_id = test_id
                    db.execute('UPDATE tests SET variant_group_id=? WHERE id=?', (variant_group_id, test_id))
                    db.commit()
                created_ids.append(test_id)

                for j, q in enumerate(gen['questions']):
                    db.execute(
                        'INSERT INTO questions (test_id,question_text,question_type,points,order_num,explanation,image_url) VALUES (?,?,?,?,?,?,?)',
                        (test_id, sanitize_input(q['question_text'], 500), q['question_type'],
                         q.get('points', 1), j + 1, sanitize_input(q.get('explanation', ''), 1000), None)
                    )
                    db.commit()
                    q_id = db.execute('SELECT last_insert_rowid() as id').fetchone()['id']
                    for opt in q.get('options', []):
                        db.execute('INSERT INTO answer_options (question_id,option_text,is_correct) VALUES (?,?,?)',
                                   (q_id, sanitize_input(opt['text'], 300), 1 if opt.get('is_correct') else 0))
                    db.commit()

        elif variant_type == 'numbers':
            # Variant with changed numbers — use a special AI instruction per variant
            from app.ai import call_gemini, clean_json_response, normalize_generated_test
            # First generate a base test, then re-prompt AI to vary the numbers for each variant
            base = generate_test_ai(subject, topic, class_name, count, difficulty, question_mode)
            for i in range(num_variants):
                if i == 0:
                    gen = base
                else:
                    # Ask AI to re-generate same question types but with different numbers
                    import time as _time
                    _time.sleep(3)
                    from app.ai import _generate_test_ai_once
                    batch_note = (
                        f"Це варіант {i+1} з {num_variants}. "
                        "Збережи ту саму структуру та типи завдань, але ОБОВ'ЯЗКОВО замінити всі конкретні числа, "
                        "розміри, дати, координати та вимірювані величини на інші числа так, щоб задачі залишилися "
                        "того ж рівня складності, але відповіді були зовсім інші. Не повторюй числа з попередніх варіантів."
                    )
                    gen = _generate_test_ai_once(subject, topic, class_name, count, difficulty, question_mode, batch_note)

                title = f"{gen['title']} (Варіант {i+1})"
                db.execute(
                    '''INSERT INTO tests (title,description,teacher_id,class_id,subject_id,
                       time_limit_minutes,max_attempts,is_active,is_practice,topic,
                       shuffle_questions,shuffle_options,variant_group_id)
                       VALUES (?,?,?,?,?,?,?,1,0,?,1,1,?)''',
                    (title, gen.get('description', ''), user['id'], class_id, subject_id,
                     time_limit, max_attempts, topic, variant_group_id)
                )
                db.commit()
                test_id = db.execute('SELECT last_insert_rowid() as id').fetchone()['id']
                if variant_group_id is None:
                    variant_group_id = test_id
                    db.execute('UPDATE tests SET variant_group_id=? WHERE id=?', (variant_group_id, test_id))
                    db.commit()
                created_ids.append(test_id)

                for j, q in enumerate(gen['questions']):
                    db.execute(
                        'INSERT INTO questions (test_id,question_text,question_type,points,order_num,explanation,image_url) VALUES (?,?,?,?,?,?,?)',
                        (test_id, sanitize_input(q['question_text'], 500), q['question_type'],
                         q.get('points', 1), j + 1, sanitize_input(q.get('explanation', ''), 1000), None)
                    )
                    db.commit()
                    q_id = db.execute('SELECT last_insert_rowid() as id').fetchone()['id']
                    for opt in q.get('options', []):
                        db.execute('INSERT INTO answer_options (question_id,option_text,is_correct) VALUES (?,?,?)',
                                   (q_id, sanitize_input(opt['text'], 300), 1 if opt.get('is_correct') else 0))
                    db.commit()

        else:
            # unique — generate fully independent tests for each variant (original behaviour)
            for i in range(num_variants):
                gen = generate_test_ai(subject, topic, class_name, count, difficulty, 'auto')
                title = f"{gen['title']} (Варіант {i+1})"
                db.execute(
                    '''INSERT INTO tests (title,description,teacher_id,class_id,subject_id,
                       time_limit_minutes,max_attempts,is_active,is_practice,topic,
                       shuffle_questions,shuffle_options,variant_group_id)
                       VALUES (?,?,?,?,?,?,?,1,0,?,1,1,?)''',
                    (title, gen.get('description', ''), user['id'], class_id, subject_id,
                     time_limit, max_attempts, topic, variant_group_id)
                )
                db.commit()
                test_id = db.execute('SELECT last_insert_rowid() as id').fetchone()['id']
                if variant_group_id is None:
                    variant_group_id = test_id
                    db.execute('UPDATE tests SET variant_group_id=? WHERE id=?', (variant_group_id, test_id))
                    db.commit()
                created_ids.append(test_id)

                for j, q in enumerate(gen['questions']):
                    db.execute(
                        'INSERT INTO questions (test_id,question_text,question_type,points,order_num,explanation,image_url) VALUES (?,?,?,?,?,?,?)',
                        (test_id, sanitize_input(q['question_text'], 500), q['question_type'],
                         q.get('points', 1), j + 1, sanitize_input(q.get('explanation', ''), 1000), None)
                    )
                    db.commit()
                    q_id = db.execute('SELECT last_insert_rowid() as id').fetchone()['id']
                    for opt in q.get('options', []):
                        db.execute('INSERT INTO answer_options (question_id,option_text,is_correct) VALUES (?,?,?)',
                                   (q_id, sanitize_input(opt['text'], 300), 1 if opt.get('is_correct') else 0))
                    db.commit()

        return jsonify({'success': True, 'test_ids': created_ids, 'variant_group_id': variant_group_id})
    except Exception as e:
        logger.error(f'Variant generation error: {e}')
        return jsonify({'error': str(e)}), 500


@tests_bp.route('/practice/generate', methods=['POST'])
@login_required
@approved_required
def practice_generate():
    user = g.current_user
    if user['role'] != 'student':
        return jsonify({'error': 'Доступ дозволено лише учням'}), 403

    data = request.get_json() or {}
    subject_name = sanitize_input(data.get('subject', 'Математика'), 100)
    topic = sanitize_input(data.get('topic', ''), 200)
    count = data.get('question_count', 5)

    if not topic:
        return jsonify({'error': 'Вкажіть тему для тренування'}), 400

    db = get_db()
    subj_row = db.execute('SELECT id FROM subjects WHERE name = ?', (subject_name,)).fetchone()
    subject_id = subj_row['id'] if subj_row else 1

    if user['role'] == 'student':
        completed = db.execute(
            """SELECT 1 FROM completed_topics
               WHERE student_id=? AND subject_id=? AND LOWER(topic)=LOWER(?)
               LIMIT 1""",
            (user['id'], subject_id, topic)
        ).fetchone()
        if not completed:
            return jsonify({'error': 'Тренувальний тест можна створити тільки за вже пройденою темою. Завершіть офіційний тест із цієї теми або попросіть вчителя відкрити тему.'}), 403

    try:
        generated = generate_practice_test_ai(subject_name, topic, count)
        title = generated.get('title', f'Тренувальний тест: {topic}')
        desc = generated.get('description', f'Практичні завдання з теми {topic}')
        time_limit = generated.get('time_limit_minutes', 15)

        db.execute(
            """INSERT INTO tests (title, description, teacher_id, class_id, subject_id,
                                 time_limit_minutes, max_attempts, is_active, is_practice, topic)
              VALUES (?, ?, ?, NULL, ?, ?, 99, 1, 1, ?)""",
            (title, desc, user['id'], subject_id, time_limit, topic)
        )
        db.commit()
        test_id = db.execute('SELECT last_insert_rowid() as id').fetchone()['id']

        questions = generated.get('questions', [])
        for i, q in enumerate(questions):
            q_text = sanitize_input(q.get('question_text', ''), 500)
            q_type = q.get('question_type', 'single')
            pts = q.get('points', 1)
            expl = sanitize_input(q.get('explanation', ''), 1000)

            db.execute(
                'INSERT INTO questions (test_id, question_text, question_type, points, order_num, explanation, image_url) VALUES (?,?,?,?,?,?,?)',
                (test_id, q_text, q_type, pts, i + 1, expl, None)
            )
            db.commit()
            q_id = db.execute('SELECT last_insert_rowid() as id').fetchone()['id']

            for opt in q.get('options', []):
                opt_text = sanitize_input(opt.get('text', ''), 300)
                is_corr = 1 if opt.get('is_correct') else 0
                db.execute(
                    'INSERT INTO answer_options (question_id, option_text, is_correct) VALUES (?,?,?)',
                    (q_id, opt_text, is_corr)
                )
            db.commit()

        return jsonify({'success': True, 'test_id': test_id})
    except Exception as e:
        logger.error(f'Practice test generation error: {e}')
        return jsonify({'error': f'Помилка генерації AI: {str(e)}'}), 500


@tests_bp.route('/results/<int:test_id>')
@login_required
def results(test_id):
    user = g.current_user
    db = get_db()
    test = db.execute('SELECT t.*, s.name as subject_name FROM tests t LEFT JOIN subjects s ON t.subject_id=s.id WHERE t.id=?', (test_id,)).fetchone()
    if not test:
        abort(404)
    test = dict(test)

    if user['role'] == 'student':
        if not _student_can_access_test(db, user, test):
            abort(403)
    elif not _staff_can_access_test(user, test):
        abort(403)
    
    if user['role'] == 'student':
        attempts = db.execute(
            """SELECT ta.*, u.full_name as student_name
               FROM test_attempts ta JOIN users u ON ta.student_id=u.id
               WHERE ta.test_id=? AND ta.student_id=? ORDER BY ta.started_at DESC""",
            (test_id, user['id'])
        ).fetchall()
    else:
        # For variant groups — show best attempt per student across all variants
        test_ids_to_query = [test_id]
        if test.get('variant_group_id'):
            variant_ids = db.execute(
                'SELECT id FROM tests WHERE variant_group_id=?',
                (test['variant_group_id'],)
            ).fetchall()
            test_ids_to_query = [r['id'] for r in variant_ids]

        placeholders = ','.join('?' * len(test_ids_to_query))
        attempts = db.execute(
            f"""SELECT ta.*, u.full_name as student_name,
                       t2.title as variant_title, ta.variant_number
               FROM test_attempts ta
               JOIN users u ON ta.student_id = u.id
               LEFT JOIN tests t2 ON ta.test_id = t2.id
               WHERE ta.test_id IN ({placeholders})
                 AND ta.status IN ('finished','disqualified','in_progress')
               GROUP BY ta.student_id
               HAVING ta.id = MAX(ta.id)
               ORDER BY ta.score DESC""",
            test_ids_to_query
        ).fetchall()

    # If practice test or review, load questions and answers for latest attempt
    questions_review = []
    # Staff can select a specific attempt via ?attempt_id=
    selected_attempt_id = request.args.get('attempt_id', type=int)
    if selected_attempt_id and user['role'] != 'student':
        latest_attempt = next((a for a in attempts if a['id'] == selected_attempt_id), attempts[0] if attempts else None)
    else:
        latest_attempt = attempts[0] if attempts else None

    if latest_attempt:
        questions = db.execute(
            'SELECT * FROM questions WHERE test_id=? ORDER BY order_num', (test_id,)
        ).fetchall()
        for q in questions:
            q_dict = dict(q)
            opts = db.execute('SELECT * FROM answer_options WHERE question_id=?', (q_dict['id'],)).fetchall()
            q_dict['options'] = [dict(o) for o in opts]
            
            # Find student's answer
            student_ans = db.execute(
                'SELECT * FROM student_answers WHERE attempt_id=? AND question_id=?',
                (latest_attempt['id'], q_dict['id'])
            ).fetchone()
            q_dict['student_answer'] = dict(student_ans) if student_ans else None
            questions_review.append(q_dict)

    return render_template('tests/results.html',
                           test=test,
                           attempts=[dict(a) for a in attempts],
                           latest_attempt=dict(latest_attempt) if latest_attempt else None,
                           questions_review=questions_review)


@tests_bp.route('/<int:test_id>/delete', methods=['POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def delete_test(test_id):
    """Видалення тесту вчителем/адміном разом з усіма пов'язаними даними."""
    if not validate_csrf(request.form.get('csrf_token', '')):
        abort(403)

    user = g.current_user
    db = get_db()

    test = db.execute('SELECT * FROM tests WHERE id=?', (test_id,)).fetchone()
    if not test:
        abort(404)
    test = dict(test)

    # Тільки власник тесту або адмін/директор можуть видаляти
    if not _staff_can_access_test(user, test):
        abort(403)

    # Каскадне видалення:
    # 1. Відповіді учнів
    db.execute(
        """DELETE FROM student_answers
           WHERE attempt_id IN (SELECT id FROM test_attempts WHERE test_id=?)""",
        (test_id,)
    )
    # 2. Скидаємо attempt_id у учасників сесій (FK → test_attempts)
    db.execute(
        """UPDATE test_session_participants SET attempt_id=NULL
           WHERE session_id IN (SELECT id FROM test_sessions WHERE test_id=?)""",
        (test_id,)
    )
    # 3. Спроби учнів
    db.execute('DELETE FROM test_attempts WHERE test_id=?', (test_id,))
    # 4. Учасники сесій
    db.execute(
        """DELETE FROM test_session_participants
           WHERE session_id IN (SELECT id FROM test_sessions WHERE test_id=?)""",
        (test_id,)
    )
    # 5. Сесії
    db.execute('DELETE FROM test_sessions WHERE test_id=?', (test_id,))
    # 6. Варіанти відповідей
    db.execute(
        """DELETE FROM answer_options
           WHERE question_id IN (SELECT id FROM questions WHERE test_id=?)""",
        (test_id,)
    )
    # 7. Питання
    db.execute('DELETE FROM questions WHERE test_id=?', (test_id,))
    # 8. Записи пройдених тем що посилаються на цей тест
    db.execute('UPDATE completed_topics SET source_test_id=NULL WHERE source_test_id=?', (test_id,))
    # 8. Сам тест
    db.execute('DELETE FROM tests WHERE id=?', (test_id,))
    db.commit()

    logger.info(f'Test {test_id} "{test["title"]}" deleted by user {user["id"]} ({user["role"]})')
    return redirect(url_for('tests.list_tests'))
