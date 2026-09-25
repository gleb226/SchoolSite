from flask import Blueprint, request, jsonify, g
from app.database import get_db
from app.auth import login_required, role_required
from app.security import rate_limit, sanitize_input
import logging

logger = logging.getLogger(__name__)
api_bp = Blueprint('api', __name__)


def _student_can_access_test(db, user, test):
    if user['role'] != 'student':
        return False
    if test['is_practice']:
        return int(test['teacher_id'] or 0) == int(user['id'])
    if not test['class_id']:
        return True
    row = db.execute(
        'SELECT 1 FROM student_classes WHERE student_id=? AND class_id=?',
        (user['id'], test['class_id'])
    ).fetchone()
    return row is not None


def _get_accessible_student_test(db, user, test_id):
    test = db.execute(
        'SELECT * FROM tests WHERE id=? AND is_active=1',
        (test_id,)
    ).fetchone()
    if not test:
        return None, ({'error': 'Test not found'}, 404)
    if not _student_can_access_test(db, user, test):
        return None, ({'error': 'Access denied'}, 403)
    return test, None


@api_bp.route('/stats')
def stats():
    db = get_db()
    data = {
        'students': db.execute("SELECT COUNT(*) as c FROM users WHERE role='student' AND is_approved=1").fetchone()['c'],
        'teachers': db.execute("SELECT COUNT(*) as c FROM users WHERE role='teacher' AND is_approved=1").fetchone()['c'],
        'classes': db.execute("SELECT COUNT(*) as c FROM classes").fetchone()['c'],
        'news': db.execute("SELECT COUNT(*) as c FROM news WHERE is_published=1").fetchone()['c'],
    }
    return jsonify(data)


@api_bp.route('/grades/<int:student_id>')
@login_required
def get_grades(student_id):
    user = g.current_user
    # Students can only see their own grades; staff can see all
    if user['role'] == 'student' and user['id'] != student_id:
        return jsonify({'error': 'Доступ заборонено'}), 403
    if user['role'] == 'parent':
        db = get_db()
        child = db.execute('SELECT * FROM parent_student WHERE parent_id=? AND student_id=?',
                           (user['id'], student_id)).fetchone()
        if not child:
            return jsonify({'error': 'Доступ заборонено'}), 403
    db = get_db()
    rows = db.execute(
        """SELECT g.*, s.name as subject_name, u.full_name as teacher_name
           FROM grades g
           JOIN subjects s ON g.subject_id = s.id
           JOIN users u ON g.teacher_id = u.id
           WHERE g.student_id = ? ORDER BY g.date DESC""",
        (student_id,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@api_bp.route('/attendance/<int:student_id>')
@login_required
def get_attendance(student_id):
    user = g.current_user
    if user['role'] == 'student' and user['id'] != student_id:
        return jsonify({'error': 'Доступ заборонено'}), 403
    db = get_db()
    rows = db.execute(
        """SELECT a.*, s.name as subject_name
           FROM attendance a
           JOIN subjects s ON a.subject_id = s.id
           WHERE a.student_id = ? ORDER BY a.date DESC""",
        (student_id,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@api_bp.route('/subjects')
@login_required
def get_subjects():
    db = get_db()
    rows = db.execute('SELECT * FROM subjects ORDER BY name').fetchall()
    return jsonify([dict(r) for r in rows])


@api_bp.route('/classes')
@login_required
def get_classes():
    db = get_db()
    rows = db.execute('SELECT * FROM classes ORDER BY name').fetchall()
    return jsonify([dict(r) for r in rows])


@api_bp.route('/students/by-class/<int:class_id>')
@login_required
def get_students_by_class(class_id):
    user = g.current_user
    if user['role'] not in ('teacher', 'principal', 'vice_principal'):
        return jsonify({'error': 'Доступ заборонено'}), 403
    db = get_db()
    rows = db.execute(
        """SELECT u.id, u.full_name, u.username, u.email
           FROM users u
           JOIN student_classes sc ON u.id = sc.student_id
           WHERE sc.class_id = ? ORDER BY u.full_name""",
        (class_id,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@api_bp.route('/test/<int:test_id>/cheat-event', methods=['POST'])
@login_required
def log_cheat_event(test_id):
    from datetime import datetime
    user = g.current_user
    data = request.get_json() or {}
    attempt_id = data.get('attempt_id')
    reason = sanitize_input(data.get('reason', 'Вихід з вікна тестування'), 200)
    duration_seconds = float(data.get('duration_seconds', 0) or 0)  # seconds the tab was hidden

    db = get_db()
    is_staff = user['role'] in ('admin', 'principal', 'vice_principal', 'teacher')
    if is_staff:
        test = db.execute('SELECT * FROM tests WHERE id=?', (test_id,)).fetchone()
        if not test:
            return jsonify({'error': 'Test not found'}), 404
    else:
        test, access_error = _get_accessible_student_test(db, user, test_id)
        if access_error:
            body, status = access_error
            return jsonify(body), status

    attempt = db.execute(
        'SELECT * FROM test_attempts WHERE id=? AND test_id=? AND student_id=? AND status="in_progress"',
        (attempt_id, test_id, user['id'])
    ).fetchone()

    if not attempt:
        return jsonify({'error': 'Спробу не знайдено'}), 404

    attempt = dict(attempt)

    # Check if anticheat is disabled for this student or globally for session
    anticheat_disabled = False
    # Check per-student disable
    part_row = db.execute(
        """SELECT tsp.anticheat_disabled FROM test_session_participants tsp
           JOIN test_sessions ts ON tsp.session_id = ts.id
           WHERE tsp.student_id=? AND ts.test_id=? AND ts.status != 'closed'
           ORDER BY tsp.joined_at DESC LIMIT 1""",
        (user['id'], test_id)
    ).fetchone()
    if part_row and part_row['anticheat_disabled']:
        anticheat_disabled = True
    # Check global session disable
    if not anticheat_disabled:
        sess_row = db.execute(
            "SELECT anticheat_disabled FROM test_sessions WHERE test_id=? AND status != 'closed' ORDER BY created_at DESC LIMIT 1",
            (test_id,)
        ).fetchone()
        if sess_row and sess_row['anticheat_disabled']:
            anticheat_disabled = True

    current_cheats = (attempt.get('cheat_attempts') or 0) + 1
    current_exit_count = (attempt.get('exit_count') or 0) + 1
    current_exit_duration = (attempt.get('exit_duration_seconds') or 0.0) + duration_seconds

    now_str = datetime.now().strftime('%H:%M:%S')
    log_entry = f"[{now_str}] {reason} | тривалість: {duration_seconds:.1f}с\n"
    cheat_log = (attempt.get('cheat_log') or '') + log_entry

    # Always update stats
    db.execute(
        'UPDATE test_attempts SET cheat_attempts=?, cheat_log=?, exit_count=?, exit_duration_seconds=? WHERE id=?',
        (current_cheats, cheat_log, current_exit_count, current_exit_duration, attempt_id)
    )
    db.commit()

    # If anticheat disabled — just log, never disqualify
    if anticheat_disabled:
        return jsonify({
            'cheat_attempts': current_cheats,
            'disqualified': False,
            'anticheat_disabled': True,
            'message': 'Зафіксовано (античіт вимкнено вчителем)'
        })

    # будь-який вихід = негайна дискваліфікація
    db.execute(
        'UPDATE test_attempts SET is_disqualified=1, status="disqualified", score=0, finished_at=CURRENT_TIMESTAMP WHERE id=?',
        (attempt_id,)
    )
    db.commit()
    return jsonify({
        'cheat_attempts': current_cheats,
        'disqualified': True,
        'message': 'Вашу спробу анульовано (вихід з тесту)!'
    })


@api_bp.route('/test/<int:test_id>/submit', methods=['POST'])
@login_required
def submit_test(test_id):
    user = g.current_user
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Немає даних'}), 400

    attempt_id = data.get('attempt_id')
    answers = data.get('answers', {})
    try:
        client_cheat_count = int(data.get('cheat_attempts', 0) or 0)
    except (TypeError, ValueError):
        client_cheat_count = 0

    db = get_db()
    is_staff = user['role'] in ('admin', 'principal', 'vice_principal', 'teacher')

    if is_staff:
        test = db.execute('SELECT * FROM tests WHERE id=?', (test_id,)).fetchone()
        if not test:
            return jsonify({'error': 'Test not found'}), 404
    else:
        test, access_error = _get_accessible_student_test(db, user, test_id)
        if access_error:
            body, status = access_error
            return jsonify(body), status

    attempt = db.execute(
        'SELECT * FROM test_attempts WHERE id=? AND test_id=? AND student_id=? AND status="in_progress"',
        (attempt_id, test_id, user['id'])
    ).fetchone()
    if not attempt:
        return jsonify({'error': 'Спробу не знайдено'}), 404

    cheat_count = max(client_cheat_count, int(attempt['cheat_attempts'] or 0))
    questions = db.execute('SELECT * FROM questions WHERE test_id=?', (test_id,)).fetchall()
    max_score = sum(dict(q)['points'] for q in questions)
    if cheat_count >= 3:
        db.execute(
            'UPDATE test_attempts SET status="disqualified", is_disqualified=1, score=0, max_score=?, cheat_attempts=?, finished_at=CURRENT_TIMESTAMP WHERE id=?',
            (max_score, cheat_count, attempt_id)
        )
        db.commit()
        return jsonify({'disqualified': True, 'message': 'Вас дискваліфіковано за порушення'})

    # Score answers
    score = 0
    for q in questions:
        q = dict(q)
        user_ans = answers.get(str(q['id']))
        if q['question_type'] == 'single':
            if user_ans:
                opt = db.execute('SELECT * FROM answer_options WHERE id=? AND question_id=? AND is_correct=1',
                                 (user_ans, q['id'])).fetchone()
                if opt:
                    score += q['points']
                db.execute('INSERT INTO student_answers (attempt_id,question_id,selected_option_id) VALUES (?,?,?)',
                           (attempt_id, q['id'], user_ans))
        elif q['question_type'] == 'multiple':
            selected = user_ans if isinstance(user_ans, list) else ([user_ans] if user_ans else [])
            selected_ids = {int(v) for v in selected if str(v).isdigit()}
            correct_ids = {
                int(r['id']) for r in db.execute(
                    'SELECT id FROM answer_options WHERE question_id=? AND is_correct=1',
                    (q['id'],)
                ).fetchall()
            }
            if selected_ids and selected_ids == correct_ids:
                score += q['points']
            for option_id in selected_ids:
                db.execute('INSERT INTO student_answers (attempt_id,question_id,selected_option_id) VALUES (?,?,?)',
                           (attempt_id, q['id'], option_id))
        else:
            if user_ans:
                db.execute('INSERT INTO student_answers (attempt_id,question_id,text_answer) VALUES (?,?,?)',
                           (attempt_id, q['id'], sanitize_input(str(user_ans))))

    db.execute(
        'UPDATE test_attempts SET status="finished", score=?, max_score=?, cheat_attempts=?, finished_at=CURRENT_TIMESTAMP WHERE id=?',
        (score, max_score, cheat_count, attempt_id)
    )

    test = db.execute('SELECT subject_id, is_practice, topic FROM tests WHERE id=?', (test_id,)).fetchone()
    if test and not test['is_practice'] and test['topic']:
        db.execute(
            """INSERT INTO completed_topics (student_id, subject_id, topic, source_test_id)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(student_id, subject_id, topic)
               DO UPDATE SET completed_at=CURRENT_TIMESTAMP, source_test_id=excluded.source_test_id""",
            (user['id'], test['subject_id'], test['topic'], test_id)
        )
    db.commit()

    # Auto AI-grade text answers for practice tests
    if test and test['is_practice']:
        try:
            from app.ai import grade_text_answer
            text_answers = db.execute(
                """SELECT sa.id, sa.text_answer, q.question_text, q.explanation, q.points
                   FROM student_answers sa
                   JOIN questions q ON sa.question_id = q.id
                   WHERE sa.attempt_id = ? AND q.question_type = 'text'
                     AND sa.text_answer IS NOT NULL AND sa.text_answer != ''
                     AND sa.ai_score IS NULL""",
                (attempt_id,)
            ).fetchall()
            ai_text_score = 0.0
            for ans in text_answers:
                ans = dict(ans)
                graded = grade_text_answer(
                    question_text=ans['question_text'],
                    correct_answer=ans.get('explanation') or '',
                    student_answer=ans.get('text_answer') or '',
                    max_points=ans['points']
                )
                db.execute(
                    'UPDATE student_answers SET ai_score=?, ai_comment=? WHERE id=?',
                    (graded['score'], graded['comment'], ans['id'])
                )
                ai_text_score += graded['score']
            if text_answers:
                new_score = score + ai_text_score
                db.execute(
                    'UPDATE test_attempts SET score=? WHERE id=?',
                    (new_score, attempt_id)
                )
                score = new_score
            db.commit()
        except Exception as ai_err:
            logger.warning(f"Auto AI grading failed for attempt {attempt_id}: {ai_err}")
            db.commit()  # commit what we have anyway

    # Notify parents if grade would be low
    pct = (score / max_score * 100) if max_score > 0 else 0
    return jsonify({'score': score, 'max_score': max_score, 'percentage': round(pct, 1)})


@api_bp.route('/voting/<int:voting_id>/vote', methods=['POST'])
@login_required
def cast_vote(voting_id):
    user = g.current_user
    data = request.get_json()
    candidate_id = data.get('candidate_id') if data else None
    if not candidate_id:
        return jsonify({'error': 'Оберіть кандидата'}), 400

    db = get_db()
    existing = db.execute('SELECT * FROM votes WHERE voting_id=? AND voter_id=?',
                          (voting_id, user['id'])).fetchone()
    if existing:
        return jsonify({'error': 'Ви вже проголосували'}), 400

    voting = db.execute('SELECT * FROM votings WHERE id=? AND is_active=1', (voting_id,)).fetchone()
    if not voting:
        return jsonify({'error': 'Голосування не знайдено або неактивне'}), 404

    db.execute('INSERT INTO votes (voting_id,voter_id,candidate_id) VALUES (?,?,?)',
               (voting_id, user['id'], candidate_id))
    db.commit()
    return jsonify({'success': True, 'message': 'Ваш голос зараховано!'})


@api_bp.route('/test/<int:test_id>/grade-text', methods=['POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def ai_grade_text_answers(test_id):
    """AI-grades all ungraded text answers for a specific attempt."""
    from app.ai import grade_text_answer
    data = request.get_json() or {}
    attempt_id = data.get('attempt_id')
    if not attempt_id:
        return jsonify({'error': 'attempt_id required'}), 400

    db = get_db()
    attempt = db.execute('SELECT * FROM test_attempts WHERE id=? AND test_id=?',
                         (attempt_id, test_id)).fetchone()
    if not attempt:
        return jsonify({'error': 'Спробу не знайдено'}), 404

    # Fetch text answers without AI score yet
    answers = db.execute(
        """SELECT sa.*, q.question_text, q.explanation, q.points, q.id as qid
           FROM student_answers sa
           JOIN questions q ON sa.question_id = q.id
           WHERE sa.attempt_id = ? AND q.question_type = 'text' AND sa.text_answer IS NOT NULL AND sa.text_answer != ''""",
        (attempt_id,)
    ).fetchall()

    results = []
    total_ai_score = 0
    for ans in answers:
        ans = dict(ans)
        graded = grade_text_answer(
            question_text=ans['question_text'],
            correct_answer=ans.get('explanation') or '',
            student_answer=ans.get('text_answer') or '',
            max_points=ans['points']
        )
        db.execute(
            'UPDATE student_answers SET ai_score=?, ai_comment=? WHERE id=?',
            (graded['score'], graded['comment'], ans['id'])
        )
        total_ai_score += graded['score']
        results.append({
            'answer_id': ans['id'],
            'question_id': ans['qid'],
            'question_text': ans['question_text'],
            'student_answer': ans['text_answer'],
            'ai_score': graded['score'],
            'max_points': ans['points'],
            'ai_comment': graded['comment'],
            'confidence': graded['confidence']
        })

    # Recalculate total score including AI scores for text questions
    auto_score = db.execute(
        """SELECT COALESCE(SUM(
             CASE WHEN q.question_type = 'text' THEN COALESCE(sa.teacher_score, sa.ai_score, 0)
                  ELSE 0 END
           ), 0) as s
           FROM student_answers sa
           JOIN questions q ON sa.question_id = q.id
           WHERE sa.attempt_id = ?""",
        (attempt_id,)
    ).fetchone()['s']

    # Update attempt score
    current = dict(db.execute('SELECT * FROM test_attempts WHERE id=?', (attempt_id,)).fetchone())
    new_score = (current.get('score') or 0) + auto_score
    # Recalculate from scratch for accuracy
    total_score = db.execute(
        """SELECT COALESCE(SUM(
             CASE
               WHEN q.question_type = 'text' THEN COALESCE(sa.teacher_score, sa.ai_score, 0)
               WHEN sa.selected_option_id IS NOT NULL THEN 0
               ELSE 0
             END
           ), 0) as s
           FROM student_answers sa
           JOIN questions q ON sa.question_id = q.id
           WHERE sa.attempt_id = ?""",
        (attempt_id,)
    ).fetchone()['s']

    db.commit()
    return jsonify({'success': True, 'graded': results, 'total_ai_added': total_ai_score})


@api_bp.route('/test/<int:test_id>/set-score', methods=['POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def set_teacher_score(test_id):
    """Teacher manually sets score for a specific text answer."""
    data = request.get_json() or {}
    answer_id = data.get('answer_id')
    score = data.get('score')
    if answer_id is None or score is None:
        return jsonify({'error': 'answer_id і score обов\'язкові'}), 400

    db = get_db()
    answer = db.execute(
        """SELECT sa.*, q.points, q.question_type, ta.test_id
           FROM student_answers sa
           JOIN questions q ON sa.question_id = q.id
           JOIN test_attempts ta ON sa.attempt_id = ta.id
           WHERE sa.id = ? AND ta.test_id = ?""",
        (answer_id, test_id)
    ).fetchone()
    if not answer:
        return jsonify({'error': 'Відповідь не знайдено'}), 404

    answer = dict(answer)
    max_pts = answer['points']
    try:
        score = float(score)
    except (TypeError, ValueError):
        return jsonify({'error': 'Невірний бал'}), 400
    score = max(0.0, min(float(max_pts), score))

    db.execute('UPDATE student_answers SET teacher_score=? WHERE id=?', (score, answer_id))

    # Recalculate attempt total score
    attempt_id = answer['attempt_id'] if 'attempt_id' in answer else db.execute(
        'SELECT attempt_id FROM student_answers WHERE id=?', (answer_id,)).fetchone()['attempt_id']

    # Sum: auto-scored (single/multiple) + teacher/ai score for text
    rows = db.execute(
        """SELECT q.question_type, q.points, sa.selected_option_id,
                  COALESCE(sa.teacher_score, sa.ai_score) as text_score
           FROM student_answers sa
           JOIN questions q ON sa.question_id = q.id
           WHERE sa.attempt_id = ?""",
        (attempt_id,)
    ).fetchall()

    # For single/multiple we recalculate from answer_options
    total = 0.0
    for row in rows:
        row = dict(row)
        if row['question_type'] == 'text':
            total += float(row['text_score'] or 0)
        # single/multiple scored already in submit_test, don't re-sum here

    # Get the existing non-text score
    existing_non_text = db.execute(
        """SELECT COALESCE(SUM(q.points), 0) as s
           FROM student_answers sa
           JOIN questions q ON sa.question_id = q.id
           JOIN answer_options ao ON sa.selected_option_id = ao.id AND ao.is_correct = 1
           WHERE sa.attempt_id = ? AND q.question_type != 'text'""",
        (attempt_id,)
    ).fetchone()['s']

    new_total = float(existing_non_text) + total
    db.execute('UPDATE test_attempts SET score=? WHERE id=?', (new_total, attempt_id))
    db.commit()

    return jsonify({'success': True, 'new_score': score, 'attempt_total': new_total})


@api_bp.route('/session/<int:session_id>/anticheat', methods=['POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def toggle_anticheat(session_id):
    """Teacher toggles anticheat: globally for session or for specific student."""
    data = request.get_json() or {}
    student_id = data.get('student_id')  # None = global
    disabled = bool(data.get('disabled', False))

    db = get_db()
    sess = db.execute('SELECT * FROM test_sessions WHERE id=?', (session_id,)).fetchone()
    if not sess:
        return jsonify({'error': 'Session not found'}), 404

    user = g.current_user
    if user['role'] == 'teacher' and int(sess['teacher_id']) != int(user['id']):
        return jsonify({'error': 'Forbidden'}), 403

    if student_id:
        db.execute(
            'UPDATE test_session_participants SET anticheat_disabled=? WHERE session_id=? AND student_id=?',
            (1 if disabled else 0, session_id, student_id)
        )
    else:
        db.execute(
            'UPDATE test_sessions SET anticheat_disabled=? WHERE id=?',
            (1 if disabled else 0, session_id)
        )
    db.commit()
    return jsonify({'success': True, 'disabled': disabled,
                    'scope': 'student' if student_id else 'global'})


@api_bp.route('/session/<int:session_id>/restore-student', methods=['POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def restore_student(session_id):
    """Teacher restores a disqualified student — resets status to in_progress."""
    data = request.get_json() or {}
    student_id = data.get('student_id')
    if not student_id:
        return jsonify({'error': 'student_id required'}), 400

    db = get_db()
    sess = db.execute('SELECT * FROM test_sessions WHERE id=?', (session_id,)).fetchone()
    if not sess:
        return jsonify({'error': 'Session not found'}), 404

    user = g.current_user
    if user['role'] == 'teacher' and int(sess['teacher_id']) != int(user['id']):
        return jsonify({'error': 'Forbidden'}), 403

    # Find the disqualified attempt: first try via session_participants (newer),
    # then fall back to direct lookup by student+test (for older data)
    attempt = db.execute(
        """SELECT ta.* FROM test_attempts ta
           JOIN test_session_participants tsp ON tsp.attempt_id = ta.id
           WHERE tsp.session_id = ? AND tsp.student_id = ?
             AND ta.status = 'disqualified'
           ORDER BY ta.started_at DESC LIMIT 1""",
        (session_id, student_id)
    ).fetchone()

    if not attempt:
        # Fallback: find by test_id + student_id directly
        attempt = db.execute(
            """SELECT * FROM test_attempts
               WHERE test_id = ? AND student_id = ? AND status = 'disqualified'
               ORDER BY started_at DESC LIMIT 1""",
            (sess['test_id'], student_id)
        ).fetchone()

    if not attempt:
        return jsonify({'error': 'Дискваліфіковану спробу не знайдено'}), 404

    db.execute(
        """UPDATE test_attempts
           SET status='in_progress', is_disqualified=0,
               finished_at=NULL
           WHERE id=?""",
        (attempt['id'],)
    )
    db.commit()
    return jsonify({'success': True, 'attempt_id': attempt['id']})
