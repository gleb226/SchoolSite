"""app/routes/lunch.py — Шкільне харчування з QR-кодами

Флоу:
  1. Вчитель зранку заходить на /lunch/morning-roll?class_id=X
  2. Відмічає хто є / кого немає → POST зберігає в lunch_attendance
  3. Присутнім автоматично генерується lunch_token, бот надсилає QR-зображення
  4. Учень/батько показує QR в столовій
  5. Касир сканує на /lunch/canteen → POST /lunch/validate → токен відмічається used=1
"""
import os
import io
import hashlib
import logging
import asyncio
from datetime import date, datetime

from flask import (Blueprint, g, request, jsonify, render_template,
                   redirect, url_for, flash, Response, abort)

from app.database import get_db
from app.auth import login_required, role_required

logger = logging.getLogger(__name__)
lunch_bp = Blueprint('lunch', __name__)


# ── Утиліти ───────────────────────────────────────────────────────────────────

def _make_token(student_id: int, day: str) -> str:
    secret = os.environ.get('SECRET_KEY', 'dev-secret')
    return hashlib.sha256(f"{student_id}:{day}:{secret}".encode()).hexdigest()[:32]


def _qr_png(data: str) -> bytes:
    import qrcode
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color='black', back_color='white').save(buf, 'PNG')
    return buf.getvalue()


def ensure_lunch_token(student_id: int, day: str = None) -> str | None:
    """
    Повертає токен для учня якщо він відмічений присутнім (lunch_attendance).
    Якщо відмітки ще немає взагалі — повертає токен (вчитель ще не відмічав).
    Якщо відмічений відсутнім — повертає None.
    """
    if day is None:
        day = date.today().isoformat()
    db = get_db()

    # Перевіряємо чи є запис у lunch_attendance
    att = db.execute(
        'SELECT present FROM lunch_attendance WHERE student_id=? AND date=?',
        (student_id, day)
    ).fetchone()

    # Якщо вчитель явно відмітив як відсутній — токен не видаємо
    if att is not None and not att['present']:
        return None

    token = _make_token(student_id, day)
    if not db.execute('SELECT 1 FROM lunch_tokens WHERE student_id=? AND date=?',
                      (student_id, day)).fetchone():
        try:
            db.execute('INSERT INTO lunch_tokens (student_id,token,date) VALUES (?,?,?)',
                       (student_id, token, day))
            db.commit()
        except Exception:
            pass
    return token


def _send_qr_to_student(student_id: int, day: str, token: str):
    """Надсилаємо QR-зображення учню та його батькам в Telegram (якщо прив'язані)."""
    try:
        from app.bot.telegram_bot import send_lunch_qr
        db = get_db()
        student = db.execute(
            'SELECT full_name, telegram_id FROM users WHERE id=?', (student_id,)
        ).fetchone()
        parents = db.execute(
            '''SELECT u.telegram_id FROM users u
               JOIN parent_student ps ON u.id=ps.parent_id
               WHERE ps.student_id=? AND u.telegram_id IS NOT NULL''',
            (student_id,)
        ).fetchall()

        recipients = []
        if student and student['telegram_id']:
            recipients.append(student['telegram_id'])
        for p in parents:
            if p['telegram_id']:
                recipients.append(p['telegram_id'])

        if not recipients:
            return

        qr_bytes = _qr_png(token)
        student_name = student['full_name'] if student else ''

        # Запускаємо в окремому потоці щоб не блокувати Flask
        # і не конфліктувати з event loop бота
        import threading

        def _send_in_thread():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                for tg_id in recipients:
                    try:
                        loop.run_until_complete(
                            send_lunch_qr(tg_id, qr_bytes, student_name, day)
                        )
                    except Exception as e:
                        logger.warning(f'Could not send QR to {tg_id}: {e}')
            finally:
                loop.close()

        t = threading.Thread(target=_send_in_thread, daemon=True)
        t.start()

    except Exception as e:
        logger.warning(f'Could not send QR to student {student_id}: {e}')


# ── Маршрути ──────────────────────────────────────────────────────────────────

@lunch_bp.route('/morning-roll', methods=['GET', 'POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal', 'superuser')
def morning_roll():
    """Ранкова відмітка класу на харчування."""
    user = g.current_user
    db = get_db()
    today = date.today().isoformat()

    # Вчитель бачить свій клас за замовчуванням
    classes = db.execute('SELECT * FROM classes ORDER BY name').fetchall()
    class_id = request.args.get('class_id', type=int)
    if not class_id and classes:
        # Якщо це класний керівник — підставляємо його клас
        my_class = db.execute(
            'SELECT id FROM classes WHERE class_teacher_id=?', (user['id'],)
        ).fetchone()
        class_id = my_class['id'] if my_class else classes[0]['id']

    if request.method == 'POST':
        from app.security import validate_csrf
        if not validate_csrf(request.form.get('csrf_token', '')):
            flash('Недійсний CSRF токен', 'danger')
            return redirect(url_for('lunch.morning_roll', class_id=class_id))

        class_id = request.form.get('class_id', type=int)
        roll_date = request.form.get('date', today)

        students = db.execute(
            """SELECT u.id FROM users u
               JOIN student_classes sc ON u.id=sc.student_id
               WHERE sc.class_id=? AND u.role='student' AND u.is_approved=1""",
            (class_id,)
        ).fetchall()

        saved = 0
        notified = 0
        for s in students:
            sid = s['id']
            present = 1 if request.form.get(f'present_{sid}') else 0
            # INSERT OR REPLACE — дозволяє перевідмічати
            db.execute(
                '''INSERT INTO lunch_attendance (student_id, class_id, date, present, marked_by)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(student_id, date) DO UPDATE SET
                       present=excluded.present,
                       marked_by=excluded.marked_by,
                       marked_at=CURRENT_TIMESTAMP''',
                (sid, class_id, roll_date, present, user['id'])
            )
            saved += 1

            # Якщо присутній — генеруємо токен та надсилаємо QR
            if present:
                token = ensure_lunch_token(sid, roll_date)
                if token:
                    _send_qr_to_student(sid, roll_date, token)
                    notified += 1
            else:
                # Видаляємо токен якщо є (учень відсутній)
                db.execute(
                    'DELETE FROM lunch_tokens WHERE student_id=? AND date=? AND used=0',
                    (sid, roll_date)
                )

        db.commit()
        flash(f'Відмітку збережено ({saved} учнів). QR надіслано: {notified}.', 'success')
        return redirect(url_for('lunch.morning_roll', class_id=class_id))

    # GET — завантажуємо учнів та поточний стан відмітки
    students_raw = db.execute(
        """SELECT u.id, u.full_name
           FROM users u JOIN student_classes sc ON u.id=sc.student_id
           WHERE sc.class_id=? AND u.role='student' AND u.is_approved=1
           ORDER BY u.full_name""",
        (class_id,)
    ).fetchall() if class_id else []

    # Поточний стан lunch_attendance для сьогодні
    att_map = {}
    if class_id:
        for row in db.execute(
            'SELECT student_id, present FROM lunch_attendance WHERE class_id=? AND date=?',
            (class_id, today)
        ).fetchall():
            att_map[row['student_id']] = row['present']

    # Чи є токен / чи вже скористався
    token_map = {}
    if class_id:
        for row in db.execute(
            '''SELECT lt.student_id, lt.used, lt.used_at
               FROM lunch_tokens lt
               JOIN student_classes sc ON sc.student_id=lt.student_id
               WHERE sc.class_id=? AND lt.date=?''',
            (class_id, today)
        ).fetchall():
            token_map[row['student_id']] = {'used': row['used'], 'used_at': row['used_at']}

    students = []
    for s in students_raw:
        sid = s['id']
        # Якщо відмітки немає — за замовчуванням вважаємо присутнім (checkbox checked)
        present = att_map.get(sid, 1)
        tok = token_map.get(sid)
        students.append({
            'id': sid,
            'full_name': s['full_name'],
            'present': present,
            'marked': sid in att_map,
            'token_used': tok['used'] if tok else False,
            'used_at': tok['used_at'] if tok else None,
        })

    already_marked = bool(att_map)

    from app.security import generate_csrf_token
    return render_template(
        'lunch/morning_roll.html',
        classes=classes,
        class_id=class_id,
        students=students,
        today=today,
        already_marked=already_marked,
        csrf_token=generate_csrf_token(),
    )


@lunch_bp.route('/qr/<int:student_id>.png')
@login_required
def get_qr_png(student_id: int):
    user = g.current_user
    if user['role'] == 'student' and user['id'] != student_id:
        abort(403)
    if user['role'] == 'parent':
        db = get_db()
        if not db.execute('SELECT 1 FROM parent_student WHERE parent_id=? AND student_id=?',
                          (user['id'], student_id)).fetchone():
            abort(403)
    today = date.today().isoformat()
    token = ensure_lunch_token(student_id, today)
    if token is None:
        # Відмічений відсутнім — повертаємо прозору 1x1 PNG
        abort(403)
    return Response(_qr_png(token), mimetype='image/png')


@lunch_bp.route('/my')
@login_required
def my_qr():
    user = g.current_user
    db = get_db()
    today = date.today().isoformat()
    students = []
    if user['role'] == 'student':
        token = ensure_lunch_token(user['id'], today)
        rec = db.execute('SELECT used,used_at FROM lunch_tokens WHERE student_id=? AND date=?',
                         (user['id'], today)).fetchone()
        att = db.execute('SELECT present FROM lunch_attendance WHERE student_id=? AND date=?',
                         (user['id'], today)).fetchone()
        students = [{
            'id': user['id'],
            'full_name': user['full_name'],
            'token': token,
            'used': rec and rec['used'],
            'used_at': rec['used_at'] if rec else None,
            'absent': att is not None and not att['present'],
            'not_marked': att is None,
        }]
    elif user['role'] == 'parent':
        children = db.execute(
            'SELECT u.id,u.full_name FROM users u JOIN parent_student ps ON u.id=ps.student_id '
            'WHERE ps.parent_id=? AND u.is_approved=1', (user['id'],)).fetchall()
        for c in children:
            token = ensure_lunch_token(c['id'], today)
            rec = db.execute('SELECT used,used_at FROM lunch_tokens WHERE student_id=? AND date=?',
                             (c['id'], today)).fetchone()
            att = db.execute('SELECT present FROM lunch_attendance WHERE student_id=? AND date=?',
                             (c['id'], today)).fetchone()
            students.append({
                'id': c['id'],
                'full_name': c['full_name'],
                'token': token,
                'used': rec and rec['used'],
                'used_at': rec['used_at'] if rec else None,
                'absent': att is not None and not att['present'],
                'not_marked': att is None,
            })
    else:
        flash('Ця сторінка для учнів та батьків.', 'warning')
        return redirect(url_for('dashboard.dashboard'))
    return render_template('lunch/my_qr.html', students=students, today=today)


@lunch_bp.route('/send-qr-tg/<int:student_id>', methods=['POST'])
@login_required
def send_qr_to_telegram(student_id: int):
    user = g.current_user
    db = get_db()
    today = date.today().isoformat()

    # Перевірка прав доступу
    if user['role'] == 'student' and user['id'] != student_id:
        abort(403)
    if user['role'] == 'parent':
        if not db.execute('SELECT 1 FROM parent_student WHERE parent_id=? AND student_id=?',
                          (user['id'], student_id)).fetchone():
            abort(403)

    if not user.get('telegram_id'):
        flash('Спочатку підключіть Telegram у розділі "Telegram" вашого кабінету.', 'warning')
        return redirect(url_for('lunch.my_qr'))

    token = ensure_lunch_token(student_id, today)
    if not token:
        flash('На жаль, токен на сьогодні недоступний.', 'warning')
        return redirect(url_for('lunch.my_qr'))

    student = db.execute('SELECT full_name FROM users WHERE id=?', (student_id,)).fetchone()
    student_name = student['full_name'] if student else 'Учень'

    qr_bytes = _qr_png(token)

    import threading
    def _send_async():
        import asyncio
        from app.bot.telegram_bot import send_lunch_qr
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(send_lunch_qr(user['telegram_id'], qr_bytes, student_name, today))
        except Exception as e:
            logger.warning(f"Failed to send QR to tg: {e}")
        finally:
            loop.close()

    threading.Thread(target=_send_async, daemon=True).start()
    flash('✅ QR-код успішно надіслано у ваш Telegram! Перевірте повідомлення від бота.', 'success')
    return redirect(url_for('lunch.my_qr'))


@lunch_bp.route('/canteen')
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal', 'superuser')
def canteen():
    today = date.today().isoformat()
    db = get_db()
    fed = db.execute('''
        SELECT lt.used_at, u.full_name, c.name as class_name
        FROM lunch_tokens lt JOIN users u ON u.id=lt.student_id
        LEFT JOIN student_classes sc ON sc.student_id=lt.student_id
        LEFT JOIN classes c ON c.id=sc.class_id
        WHERE lt.date=? AND lt.used=1 ORDER BY lt.used_at DESC
    ''', (today,)).fetchall()

    # Учні з ранковою відміткою "присутній" але ще не пообідали
    pending = db.execute('''
        SELECT u.full_name, c.name as class_name,
               la.present,
               CASE WHEN lt.id IS NOT NULL THEN 1 ELSE 0 END as has_token,
               lt.used
        FROM lunch_attendance la
        JOIN users u ON u.id=la.student_id
        LEFT JOIN student_classes sc ON sc.student_id=la.student_id
        LEFT JOIN classes c ON c.id=sc.class_id
        LEFT JOIN lunch_tokens lt ON lt.student_id=la.student_id AND lt.date=la.date
        WHERE la.date=? AND la.present=1
        ORDER BY c.name, u.full_name
    ''', (today,)).fetchall()

    # Кількість класів що вже відмічені
    classes_marked = db.execute(
        'SELECT COUNT(DISTINCT class_id) as n FROM lunch_attendance WHERE date=?', (today,)
    ).fetchone()['n']

    total_classes = db.execute('SELECT COUNT(*) as n FROM classes').fetchone()['n']
    total_present = db.execute(
        'SELECT COUNT(*) as n FROM lunch_attendance WHERE date=? AND present=1', (today,)
    ).fetchone()['n']
    total_issued = db.execute('SELECT COUNT(*) as n FROM lunch_tokens WHERE date=?', (today,)).fetchone()['n']

    return render_template('lunch/canteen.html', today=today,
                           fed_today=fed, fed_count=len(fed),
                           total_issued=total_issued,
                           pending=pending,
                           total_present=total_present,
                           classes_marked=classes_marked,
                           total_classes=total_classes)


@lunch_bp.route('/validate', methods=['POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal', 'superuser')
def validate_token():
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or request.form.get('token', '')).strip()
    if not token:
        return jsonify({'ok': False, 'error': 'Порожній код'}), 400
    db = get_db()
    today = date.today().isoformat()
    rec = db.execute('''
        SELECT lt.*, u.full_name, c.name as class_name
        FROM lunch_tokens lt JOIN users u ON u.id=lt.student_id
        LEFT JOIN student_classes sc ON sc.student_id=lt.student_id
        LEFT JOIN classes c ON c.id=sc.class_id
        WHERE lt.token=? AND lt.date=?
    ''', (token, today)).fetchone()
    if not rec:
        return jsonify({'ok': False, 'error': '❌ Невірний QR або він не на сьогодні'}), 404
    if rec['used']:
        t = rec['used_at'][11:16] if rec['used_at'] else '?'
        return jsonify({'ok': False, 'already': True,
                        'error': f'⚠️ Вже отримав обід о {t}',
                        'name': rec['full_name'], 'class': rec['class_name'] or ''}), 409
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db.execute('UPDATE lunch_tokens SET used=1,used_at=? WHERE token=? AND date=?',
               (now, token, today))
    db.commit()
    return jsonify({'ok': True, 'name': rec['full_name'],
                    'class': rec['class_name'] or '', 'time': now[11:16],
                    'message': '✅ Обід підтверджено!'})


@lunch_bp.route('/report')
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal', 'superuser')
def report():
    db = get_db()
    day = request.args.get('date', date.today().isoformat())
    rows = db.execute('''
        SELECT lt.used,lt.used_at,u.full_name,c.name as class_name
        FROM lunch_tokens lt JOIN users u ON u.id=lt.student_id
        LEFT JOIN student_classes sc ON sc.student_id=lt.student_id
        LEFT JOIN classes c ON c.id=sc.class_id
        WHERE lt.date=? ORDER BY c.name,u.full_name
    ''', (day,)).fetchall()
    fed = sum(1 for r in rows if r['used'])
    return render_template('lunch/report.html', rows=rows, day=day, fed=fed, total=len(rows))


@lunch_bp.route('/mark-fed/<int:student_id>', methods=['POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal', 'superuser')
def mark_fed(student_id: int):
    today = date.today().isoformat()
    db = get_db()
    token = ensure_lunch_token(student_id, today)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db.execute('UPDATE lunch_tokens SET used=1, used_at=? WHERE student_id=? AND date=?',
               (now, student_id, today))
    db.commit()
    flash('✅ Обід видано та зараховано!', 'success')
    return redirect(url_for('lunch.canteen'))


@lunch_bp.route('/test-send-qr', methods=['POST'])
@login_required
def test_send_qr():
    user = g.current_user
    today = date.today().isoformat()
    if not user.get('telegram_id'):
        flash('Спочатку підключіть Telegram у розділі "Telegram" вашого кабінету.', 'warning')
        return redirect(url_for('dashboard.link_telegram'))
    token = ensure_lunch_token(user['id'], today)
    qr_bytes = _qr_png(token)
    import threading
    def _send():
        import asyncio
        from app.bot.telegram_bot import send_lunch_qr
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(send_lunch_qr(user['telegram_id'], qr_bytes, user['full_name'], today))
        except Exception as e:
            logger.warning(f"Test QR send failed: {e}")
        finally:
            loop.close()
    threading.Thread(target=_send, daemon=True).start()
    flash('🚀 Тестовий QR-код надіслано у ваш Telegram! Перевірте чат бота @MedelnBot.', 'success')
    return redirect(request.referrer or url_for('lunch.my_qr'))
