"""
test_session_routes.py — Маршрути для «Сесій тесту».

Сесія — це спільне проходження тесту в режимі реального часу на уроці.
Вчитель відкриває сесію → отримує 6-символний join-код + QR-код.
Учні сканують QR або вводять код → приєднуються → одразу починають тест.
Вчитель бачить список учнів у реальному часі (polling кожні 5 с).
"""

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, g, abort, jsonify
)
from app.auth import login_required, approved_required, role_required
from app.database import get_db
from app.security import generate_csrf_token, validate_csrf
import random
import logging

logger = logging.getLogger(__name__)
session_bp = Blueprint('session', __name__)


# ── Утиліти ──────────────────────────────────────────────────────────────────

def _gen_join_code(length: int = 6) -> str:
    """Генерує унікальний join-код: великі літери + цифри, зручний для введення."""
    # виключаємо схожі символи: 0/O, 1/I/L
    chars = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
    db = get_db()
    for _ in range(20):
        code = ''.join(random.choices(chars, k=length))
        exists = db.execute(
            "SELECT 1 FROM test_sessions WHERE join_code=? AND status != 'closed'",
            (code,)
        ).fetchone()
        if not exists:
            return code
    # fallback — додаємо timestamp-суфікс
    import time
    return ''.join(random.choices(chars, k=4)) + str(int(time.time()) % 100)


def _qr_url(join_url: str) -> str:
    """Повертає URL картинки QR-коду через безкоштовний публічний API."""
    from urllib.parse import quote
    encoded = quote(join_url, safe='')
    return f"https://api.qrserver.com/v1/create-qr-code/?size=260x260&data={encoded}"


def _get_session_or_404(db, session_id: int):
    row = db.execute('SELECT * FROM test_sessions WHERE id=?', (session_id,)).fetchone()
    if not row:
        abort(404)
    return dict(row)


def _teacher_owns_session(user, sess: dict) -> bool:
    if user['role'] in ('admin', 'principal', 'vice_principal'):
        return True
    return user['role'] == 'teacher' and int(sess['teacher_id']) == int(user['id'])


# ── Вчитель: створити сесію ───────────────────────────────────────────────────

@session_bp.route('/create/<int:test_id>', methods=['POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def create_session(test_id):
    """Відкриває нову сесію для вказаного тесту."""
    if not validate_csrf(request.form.get('csrf_token', '')):
        abort(403)
    user = g.current_user
    db = get_db()

    test = db.execute('SELECT * FROM tests WHERE id=?', (test_id,)).fetchone()
    if not test:
        abort(404)

    # Закриваємо попередню відкриту сесію цього тесту (якщо є)
    db.execute(
        "UPDATE test_sessions SET status='closed', closed_at=CURRENT_TIMESTAMP "
        "WHERE test_id=? AND teacher_id=? AND status!='closed'",
        (test_id, user['id'])
    )

    join_code = _gen_join_code()
    db.execute(
        "INSERT INTO test_sessions (test_id, teacher_id, join_code, status) VALUES (?,?,?,'waiting')",
        (test_id, user['id'], join_code)
    )
    db.commit()

    sess_id = db.execute(
        "SELECT id FROM test_sessions WHERE join_code=?", (join_code,)
    ).fetchone()['id']

    return redirect(url_for('session.teacher_panel', session_id=sess_id))


# ── Вчитель: панель ───────────────────────────────────────────────────────────

@session_bp.route('/panel/<int:session_id>')
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def teacher_panel(session_id):
    """Панель вчителя для відкритої сесії."""
    user = g.current_user
    db = get_db()

    sess = _get_session_or_404(db, session_id)
    if not _teacher_owns_session(user, sess):
        abort(403)

    test = db.execute(
        'SELECT t.*, s.name as subject_name, cl.name as class_name '
        'FROM tests t '
        'LEFT JOIN subjects s ON t.subject_id=s.id '
        'LEFT JOIN classes cl ON t.class_id=cl.id '
        'WHERE t.id=?',
        (sess['test_id'],)
    ).fetchone()
    if not test:
        abort(404)

    join_url = request.host_url.rstrip('/') + url_for('session.join_page', code=sess['join_code'])
    qr_img_url = _qr_url(join_url)

    participants = _load_participants(db, session_id)

    return render_template(
        'tests/session_panel.html',
        session=sess,
        test=dict(test),
        join_url=join_url,
        qr_img_url=qr_img_url,
        participants=participants,
        csrf_token=generate_csrf_token(),
    )


# ── Вчитель: запустити / закрити сесію ────────────────────────────────────────

@session_bp.route('/start/<int:session_id>', methods=['POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def start_session(session_id):
    """Змінює статус на 'active' — учні, які вже приєдналися, бачать кнопку «Почати»."""
    if not validate_csrf(request.form.get('csrf_token', '')):
        abort(403)
    user = g.current_user
    db = get_db()
    sess = _get_session_or_404(db, session_id)
    if not _teacher_owns_session(user, sess):
        abort(403)
    if sess['status'] == 'waiting':
        db.execute(
            "UPDATE test_sessions SET status='active', started_at=CURRENT_TIMESTAMP WHERE id=?",
            (session_id,)
        )
        db.commit()
    return redirect(url_for('session.teacher_panel', session_id=session_id))


@session_bp.route('/close/<int:session_id>', methods=['POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def close_session(session_id):
    """Закриває сесію."""
    if not validate_csrf(request.form.get('csrf_token', '')):
        abort(403)
    user = g.current_user
    db = get_db()
    sess = _get_session_or_404(db, session_id)
    if not _teacher_owns_session(user, sess):
        abort(403)
    db.execute(
        "UPDATE test_sessions SET status='closed', closed_at=CURRENT_TIMESTAMP WHERE id=?",
        (session_id,)
    )
    db.commit()
    return redirect(url_for('tests.results', test_id=sess['test_id']))


# ── Polling: список учасників (AJAX кожні 5 сек) ─────────────────────────────

@session_bp.route('/api/<int:session_id>/set-variant', methods=['POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def set_variant(session_id):
    """Вчитель вручну призначає/змінює варіант учню в сесії."""
    user = g.current_user
    db = get_db()
    sess = _get_session_or_404(db, session_id)
    if not _teacher_owns_session(user, sess):
        abort(403)

    data = request.get_json() or {}
    student_id = data.get('student_id')
    variant_number = data.get('variant_number')

    if not student_id or not variant_number:
        return jsonify({'error': 'student_id та variant_number обов\'язкові'}), 400

    try:
        variant_number = int(variant_number)
    except (TypeError, ValueError):
        return jsonify({'error': 'variant_number має бути числом'}), 400

    # Get all variants for this test
    test = db.execute('SELECT * FROM tests WHERE id=?', (sess['test_id'],)).fetchone()
    if not test or not test['variant_group_id']:
        return jsonify({'error': 'Цей тест не має варіантів'}), 400

    variants = db.execute(
        'SELECT id FROM tests WHERE variant_group_id=? AND is_active=1 ORDER BY id',
        (test['variant_group_id'],)
    ).fetchall()

    if variant_number < 1 or variant_number > len(variants):
        return jsonify({'error': f'Варіант {variant_number} не існує (є {len(variants)} варіанти)'}), 400

    # Update session participant
    db.execute(
        'UPDATE test_session_participants SET variant_number=? WHERE session_id=? AND student_id=?',
        (variant_number, session_id, student_id)
    )
    db.commit()
    return jsonify({'success': True, 'variant_number': variant_number})


@session_bp.route('/api/status/<int:session_id>')
def poll_status(session_id):
    """
    Повертає JSON зі статусом сесії та поточними учасниками.
    Викликається фронтендом (вчитель і учні) кожні 4–5 секунд.
    """
    user = g.current_user
    db = get_db()
    sess = db.execute('SELECT * FROM test_sessions WHERE id=?', (session_id,)).fetchone()
    if not sess:
        return jsonify({'error': 'not found'}), 404
    sess = dict(sess)

    # Перевірка доступу: зареєстрований учень або гість з flask_session
    from flask import session as flask_session
    if user and user['role'] == 'student':
        part = db.execute(
            'SELECT 1 FROM test_session_participants WHERE session_id=? AND student_id=?',
            (session_id, user['id'])
        ).fetchone()
        if not part:
            return jsonify({'error': 'forbidden'}), 403
    elif not user:
        # Гість — перевіряємо flask_session
        if flask_session.get('guest_session_id') != session_id:
            return jsonify({'error': 'forbidden'}), 403
    # Вчитель/адмін — дозволяємо без перевірки

    participants = _load_participants(db, session_id)
    return jsonify({
        'status': sess['status'],
        'participants': participants,
        'count': len(participants),
    })


# ── Учень: публічна сторінка приєднання ──────────────────────────────────────

@session_bp.route('/join', methods=['GET'])
def join_page():
    """
    Сторінка приєднання. Підтримує:
    - ?code=ABC123  — pre-fill коду з QR
    - без параметра — просто форма введення
    Доступна без авторизації (щоб QR сканувалось просто).
    """
    code = request.args.get('code', '').strip().upper()
    error = None

    # Якщо форма надіслана (POST через redirect від join_submit)
    if request.args.get('error'):
        error = request.args.get('error')

    return render_template('tests/session_join.html', code=code, error=error)


@session_bp.route('/join', methods=['POST'])
def join_submit():
    """Учень вводить код → приєднується до сесії. Гість вводить ім'я."""
    from flask import session as flask_session

    code = request.form.get('code', '').strip().upper()
    if not code:
        return redirect(url_for('session.join_page', error='Введіть код приєднання'))

    db = get_db()
    sess = db.execute(
        "SELECT * FROM test_sessions WHERE join_code=? AND status IN ('waiting','active')",
        (code,)
    ).fetchone()

    if not sess:
        return redirect(url_for('session.join_page',
                                error='Код не знайдено або сесія вже закрита. Перевірте код.'))
    sess = dict(sess)

    user = g.current_user

    if user:
        # Авторизований — тільки учні
        if user['role'] != 'student':
            return redirect(url_for('session.join_page',
                                    error='Сесії доступні тільки для учнів'))
        student_id = user['id']
        guest_name = None
    else:
        # Гість — потрібне ім'я
        guest_name = request.form.get('guest_name', '').strip()
        if not guest_name:
            return redirect(url_for('session.join_page', code=code,
                                    error="Введіть ваше ім'я та прізвище"))
        # Зберігаємо ім'я гостя в Flask session
        flask_session['guest_name'] = guest_name
        flask_session['guest_session_id'] = sess['id']
        student_id = None  # гість без user_id

    # Для авторизованих — зберігаємо в participants
    if student_id:
        existing = db.execute(
            'SELECT * FROM test_session_participants WHERE session_id=? AND student_id=?',
            (sess['id'], student_id)
        ).fetchone()
        if not existing:
            db.execute(
                'INSERT OR IGNORE INTO test_session_participants (session_id, student_id) VALUES (?,?)',
                (sess['id'], student_id)
            )
            db.commit()
        return redirect(url_for('session.student_waiting', session_id=sess['id']))
    else:
        # Гість — перенаправляємо одразу на очікування без запису в учасники
        # (запис відбудеться через окремий гостьовий шлях)
        return redirect(url_for('session.student_waiting', session_id=sess['id']))


# ── Учень: сторінка очікування ────────────────────────────────────────────────

@session_bp.route('/waiting/<int:session_id>')
def student_waiting(session_id):
    """Учень або гість бачить: «Очікуємо вчителя» або «Тест розпочато»."""
    from flask import session as flask_session
    user = g.current_user
    db = get_db()

    sess = db.execute('SELECT * FROM test_sessions WHERE id=?', (session_id,)).fetchone()
    if not sess:
        abort(404)
    sess = dict(sess)

    # Перевірка доступу: авторизований учень або гість з іменем у flask_session
    guest_name = None
    if user:
        if user['role'] != 'student':
            abort(403)
        part = db.execute(
            'SELECT * FROM test_session_participants WHERE session_id=? AND student_id=?',
            (session_id, user['id'])
        ).fetchone()
        if not part:
            return redirect(url_for('session.join_page', error='Спочатку приєднайтесь до сесії'))
    else:
        # Гість
        guest_name = flask_session.get('guest_name')
        if not guest_name or flask_session.get('guest_session_id') != session_id:
            return redirect(url_for('session.join_page',
                                    error='Введіть код і ваше ім\'я для приєднання'))

    if sess['status'] == 'closed':
        if user and part and dict(part).get('attempt_id'):
            return redirect(url_for('tests.results', test_id=sess['test_id']))
        return redirect(url_for('tests.list_tests'))

    test = db.execute('SELECT * FROM tests WHERE id=?', (sess['test_id'],)).fetchone()

    count = db.execute(
        'SELECT COUNT(*) as c FROM test_session_participants WHERE session_id=?',
        (session_id,)
    ).fetchone()['c']

    return render_template(
        'tests/session_waiting.html',
        session=sess,
        test=dict(test) if test else {},
        participant_count=count,
        guest_name=guest_name,
    )


# ── Учень: старт тесту з сесії ───────────────────────────────────────────────

@session_bp.route('/begin/<int:session_id>')
def begin_test(session_id):
    """
    Учень/гість натискає «Почати тест».
    Гості перенаправляються до take_test, але без збереження результату в БД учнів.
    """
    from flask import session as flask_session
    user = g.current_user
    db = get_db()

    sess = db.execute('SELECT * FROM test_sessions WHERE id=?', (session_id,)).fetchone()
    if not sess:
        abort(404)
    sess = dict(sess)

    if sess['status'] != 'active':
        return redirect(url_for('session.student_waiting', session_id=session_id))

    if user:
        if user['role'] != 'student':
            abort(403)
        part = db.execute(
            'SELECT * FROM test_session_participants WHERE session_id=? AND student_id=?',
            (session_id, user['id'])
        ).fetchone()
        if not part:
            abort(403)
        return redirect(url_for('tests.take_test', test_id=sess['test_id']))
    else:
        # Гість — переходить до тесту, але take_test побачить guest_name з flask_session
        guest_name = flask_session.get('guest_name')
        if not guest_name or flask_session.get('guest_session_id') != session_id:
            return redirect(url_for('session.join_page'))
        flask_session['guest_test_id'] = sess['test_id']
        return redirect(url_for('tests.take_test', test_id=sess['test_id']))


# ── Допоміжна функція завантаження учасників ─────────────────────────────────

def _load_participants(db, session_id: int) -> list:
    rows = db.execute(
        """SELECT
               tsp.id, tsp.joined_at, tsp.attempt_id,
               tsp.anticheat_disabled,
               tsp.variant_number,
               u.full_name, u.id as student_id,
               ta.score, ta.max_score, ta.status as attempt_status,
               ta.finished_at,
               COALESCE(ta.exit_count, 0) as exit_count,
               COALESCE(ta.exit_duration_seconds, 0.0) as exit_duration_seconds
           FROM test_session_participants tsp
           JOIN users u ON tsp.student_id = u.id
           LEFT JOIN test_attempts ta ON tsp.attempt_id = ta.id
           WHERE tsp.session_id = ?
           ORDER BY tsp.joined_at""",
        (session_id,)
    ).fetchall()
    return [dict(r) for r in rows]
