import os
import logging
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, jsonify, g, abort, current_app
)
from app.database import get_db
from app.auth import login_required, role_required

logger = logging.getLogger(__name__)

services_bp = Blueprint('services', __name__)


def _send_tg_notification_sync(telegram_id: int, text: str):
    """Helper to send a Telegram message via bot in background (thread-safe)."""
    try:
        from app.bot.telegram_bot import get_bot
        import asyncio, threading
        bot = get_bot()
        if not bot or not telegram_id:
            return
        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(bot.send_message(telegram_id, text, parse_mode='HTML'))
            except Exception as e:
                logger.warning(f"TG send failed to {telegram_id}: {e}")
            finally:
                loop.close()
        threading.Thread(target=_run, daemon=True).start()
    except Exception as e:
        logger.warning(f"Could not send Telegram notification to {telegram_id}: {e}")


# ── ГРУПА ПОДОВЖЕНОГО ДНЯ (ГПД) ──────────────────────────────────────────────

@services_bp.route('/gpd', methods=['GET'])
@login_required
def gpd_index():
    user = g.current_user
    db = get_db()

    children = []
    if user['role'] == 'parent':
        children = db.execute(
            SELECT u.id, u.full_name, c.name as class_name
            FROM users u
            JOIN parent_student ps ON ps.student_id = u.id
            LEFT JOIN student_classes sc ON sc.student_id = u.id
            LEFT JOIN classes c ON c.id = sc.class_id
            WHERE ps.parent_id = ?
        , (user['id'],)).fetchall()
    if not children and user['role'] in ('superuser', 'admin', 'principal'):
        children = db.execute(
            SELECT u.id, u.full_name, c.name as class_name
            FROM users u
            LEFT JOIN student_classes sc ON sc.student_id = u.id
            LEFT JOIN classes c ON c.id = sc.class_id
            WHERE u.role = 'student' AND u.is_approved = 1
            ORDER BY c.name, u.full_name LIMIT 30
        ).fetchall()
        children = db.execute('''
            SELECT u.id, u.full_name, c.name as class_name
            FROM users u
            JOIN parent_student ps ON ps.student_id = u.id
            LEFT JOIN student_classes sc ON sc.student_id = u.id
            LEFT JOIN classes c ON c.id = sc.class_id
            WHERE ps.parent_id = ?
        ''', (user['id'],)).fetchall()

    today_str = datetime.now().strftime('%Y-%m-%d')
    # Pickups for today
    if user['role'] in ('teacher', 'admin', 'principal', 'superuser', 'vice_principal'):
        pickups = db.execute('''
            SELECT p.id, p.pickup_time, p.status, p.note, p.completed_at,
                   s.full_name as student_name, c.name as class_name,
                   par.full_name as parent_name, par.phone as parent_phone
            FROM gpd_pickups p
            JOIN users s ON s.id = p.student_id
            JOIN users par ON par.id = p.parent_id
            LEFT JOIN student_classes sc ON sc.student_id = s.id
            LEFT JOIN classes c ON c.id = sc.class_id
            WHERE date(p.pickup_time) = ?
            ORDER BY p.pickup_time DESC
        ''', (today_str,)).fetchall()
    else:
        pickups = db.execute('''
            SELECT p.id, p.pickup_time, p.status, p.note, p.completed_at,
                   s.full_name as student_name, c.name as class_name,
                   par.full_name as parent_name
            FROM gpd_pickups p
            JOIN users s ON s.id = p.student_id
            JOIN users par ON par.id = p.parent_id
            LEFT JOIN student_classes sc ON sc.student_id = s.id
            LEFT JOIN classes c ON c.id = sc.class_id
            WHERE p.parent_id = ? AND date(p.pickup_time) = ?
            ORDER BY p.pickup_time DESC
        ''', (user['id'], today_str)).fetchall()

    return render_template('services/gpd.html', children=children, pickups=pickups, today=today_str)


@services_bp.route('/gpd/pickup', methods=['POST'])
@login_required
def gpd_request_pickup():
    user = g.current_user
    if user['role'] not in ('parent', 'admin', 'superuser', 'principal', 'vice_principal'):
        flash('Тільки батьки можуть надсилати запит на забирання дитини з ГПД.', 'danger')
        return redirect(url_for('services.gpd_index'))

    student_id = request.form.get('student_id', type=int)
    note = request.form.get('note', '').strip()

    if not student_id:
        flash('Оберіть дитину.', 'warning')
        return redirect(url_for('services.gpd_index'))

    db = get_db()
    # Verify child belongs to parent
    if user['role'] == 'parent':
        child = db.execute('SELECT student_id FROM parent_student WHERE parent_id=? AND student_id=?',
                           (user['id'], student_id)).fetchone()
        if not child:
            flash('Цей учень не прив\'язаний до вашого акаунту.', 'danger')
            return redirect(url_for('services.gpd_index'))

    student = db.execute('''
        SELECT u.full_name, c.name as class_name
        FROM users u
        LEFT JOIN student_classes sc ON sc.student_id = u.id
        LEFT JOIN classes c ON c.id = sc.class_id
        WHERE u.id = ?
    ''', (student_id,)).fetchone()

    db.execute('''
        INSERT INTO gpd_pickups (parent_id, student_id, note, status)
        VALUES (?, ?, ?, 'requested')
    ''', (user['id'], student_id, note))
    db.commit()

    student_name = student['full_name'] if student else 'Учень'
    class_name = student['class_name'] if (student and student['class_name']) else ''
    now_time = datetime.now().strftime('%H:%M')

    # Notify teachers with telegram_id and school admin
    msg = (
        f"🔔 <b>ГПД: Батьки прийшли за дитиною!</b>\n\n"
        f"👤 <b>Учень:</b> {student_name} ({class_name})\n"
        f"👨‍👩‍👧 <b>Забирає:</b> {user['full_name']}\n"
        f"⏰ <b>Час:</b> {now_time}\n"
    )
    if note:
        msg += f"💬 <b>Примітка:</b> {note}\n"
    msg += "\n📍 <i>Будь ласка, допоможіть дитині зібратися та відпустіть до батьків.</i>"

    # Find teacher telegram IDs
    teachers = db.execute("SELECT telegram_id FROM users WHERE role IN ('teacher', 'admin', 'principal') AND telegram_id IS NOT NULL").fetchall()
    for t in teachers:
        if t['telegram_id']:
            _send_tg_notification_sync(t['telegram_id'], msg)

    admin_tid = os.environ.get('ADMIN_TELEGRAM_ID', '').strip()
    if admin_tid and admin_tid.isdigit():
        _send_tg_notification_sync(int(admin_tid), msg)

    flash('Запит успішно надіслано вчителю та вихователю ГПД!', 'success')
    return redirect(url_for('services.gpd_index'))


@services_bp.route('/gpd/complete/<int:pickup_id>', methods=['POST'])
@login_required
def gpd_complete(pickup_id):
    user = g.current_user
    if user['role'] not in ('teacher', 'admin', 'principal', 'superuser', 'vice_principal'):
        abort(403)

    db = get_db()
    pickup = db.execute('''
        SELECT p.*, par.telegram_id as parent_tg, s.full_name as student_name
        FROM gpd_pickups p
        JOIN users par ON par.id = p.parent_id
        JOIN users s ON s.id = p.student_id
        WHERE p.id = ?
    ''', (pickup_id,)).fetchone()

    if pickup:
        now_dt = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        db.execute('UPDATE gpd_pickups SET status = "completed", completed_at = ? WHERE id = ?', (now_dt, pickup_id))
        db.commit()

        if pickup['parent_tg']:
            _send_tg_notification_sync(
                pickup['parent_tg'],
                f"✅ <b>ГПД:</b> Вчитель відпустив {pickup['student_name']}. Очікуйте!"
            )

    flash('Статус оновлено: дитину відпущено.', 'info')
    return redirect(url_for('services.gpd_index'))


# ── ЕЛЕКТРОННІ ДОВІДКИ ВІД ШКОЛИ ──────────────────────────────────────────────

CERT_TYPES = {
    'study': 'Довідка з місця навчання (загальна)',
    'benefit': 'Довідка для отримання пільг, субсидій та виплат',
    'travel': 'Довідка для виїзду / оформлення закордонного паспорта',
    'grades': 'Довідка про успішність та оцінки',
    'enrollment': 'Довідка про зарахування до закладу освіти',
    'free_meals': 'Довідка для отримання безкоштовного харчування',
}


@services_bp.route('/certificates', methods=['GET'])
@login_required
def certificates_index():
    user = g.current_user
    db = get_db()

    is_staff = user['role'] in ('admin', 'principal', 'teacher', 'superuser', 'vice_principal')

    if is_staff:
        requests_list = db.execute('''
            SELECT cr.*, u.full_name as requester_name, u.role as requester_role
            FROM certificate_requests cr
            JOIN users u ON u.id = cr.user_id
            ORDER BY cr.created_at DESC
        ''').fetchall()
    else:
        requests_list = db.execute('''
            SELECT cr.*, u.full_name as requester_name
            FROM certificate_requests cr
            JOIN users u ON u.id = cr.user_id
            WHERE cr.user_id = ?
            ORDER BY cr.created_at DESC
        ''', (user['id'],)).fetchall()

    children = []
    if user['role'] == 'parent':
        children = db.execute(
            SELECT u.id, u.full_name, c.name as class_name
            FROM users u
            JOIN parent_student ps ON ps.student_id = u.id
            LEFT JOIN student_classes sc ON sc.student_id = u.id
            LEFT JOIN classes c ON c.id = sc.class_id
            WHERE ps.parent_id = ?
        , (user['id'],)).fetchall()
    if not children and user['role'] in ('superuser', 'admin', 'principal'):
        children = db.execute(
            SELECT u.id, u.full_name, c.name as class_name
            FROM users u
            LEFT JOIN student_classes sc ON sc.student_id = u.id
            LEFT JOIN classes c ON c.id = sc.class_id
            WHERE u.role = 'student' AND u.is_approved = 1
            ORDER BY c.name, u.full_name LIMIT 30
        ).fetchall()
        children = db.execute('''
            SELECT u.id, u.full_name, c.name as class_name
            FROM users u
            JOIN parent_student ps ON ps.student_id = u.id
            LEFT JOIN student_classes sc ON sc.student_id = u.id
            LEFT JOIN classes c ON c.id = sc.class_id
            WHERE ps.parent_id = ?
        ''', (user['id'],)).fetchall()

    return render_template(
        'services/certificates.html',
        requests=requests_list,
        cert_types=CERT_TYPES,
        children=children,
        is_staff=is_staff
    )


@services_bp.route('/certificates/request', methods=['POST'])
@login_required
def certificate_create():
    user = g.current_user
    db = get_db()

    cert_type = request.form.get('cert_type', '').strip()
    purpose = request.form.get('purpose', '').strip()
    student_name = request.form.get('student_name', '').strip()
    class_name = request.form.get('class_name', '').strip()

    if not cert_type or cert_type not in CERT_TYPES:
        flash('Оберіть коректний тип довідки.', 'warning')
        return redirect(url_for('services.certificates_index'))

    # If student is logged in, auto-fill student name and class
    if user['role'] == 'student':
        student_name = user['full_name']
        cls = db.execute('''
            SELECT c.name FROM classes c
            JOIN student_classes sc ON sc.class_id = c.id
            WHERE sc.student_id = ?
        ''', (user['id'],)).fetchone()
        class_name = cls['name'] if cls else ''

    if not student_name:
        flash('Вкажіть прізвище та ім\'я учня.', 'warning')
        return redirect(url_for('services.certificates_index'))

    db.execute('''
        INSERT INTO certificate_requests (user_id, student_name, class_name, cert_type, purpose, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
    ''', (user['id'], student_name, class_name, cert_type, purpose))
    db.commit()

    flash('Заяву на отримання електронної довідки успішно подано! Очікуйте підтвердження секретаріату.', 'success')
    return redirect(url_for('services.certificates_index'))


@services_bp.route('/certificates/<int:cert_id>/action', methods=['POST'])
@login_required
def certificate_action(cert_id):
    user = g.current_user
    if user['role'] not in ('admin', 'principal', 'teacher', 'superuser', 'vice_principal'):
        abort(403)

    action = request.form.get('action')
    reason = request.form.get('reason', '').strip()
    db = get_db()

    req = db.execute('''
        SELECT cr.*, u.telegram_id
        FROM certificate_requests cr
        JOIN users u ON u.id = cr.user_id
        WHERE cr.id = ?
    ''', (cert_id,)).fetchone()

    if not req:
        abort(404)

    if action == 'approve':
        now_dt = datetime.now()
        cert_num = f"{now_dt.year}/{now_dt.month:02d}-{cert_id:04d}"
        db.execute('''
            UPDATE certificate_requests
            SET status = 'approved', cert_number = ?, issued_at = ?, issued_by = ?
            WHERE id = ?
        ''', (cert_num, now_dt.strftime('%Y-%m-%d %H:%M:%S'), user['id'], cert_id))
        db.commit()

        if req['telegram_id']:
            _send_tg_notification_sync(
                req['telegram_id'],
                f"📄 <b>Ваша електронна довідка №{cert_num} готова!</b>\n\n"
                f"Учень: {req['student_name']}\n"
                f"Тип: {CERT_TYPES.get(req['cert_type'], req['cert_type'])}\n\n"
                f"Ви можете переглянути та завантажити її в особистому кабінеті ліцею."
            )
        flash(f'Довідку №{cert_num} схвалено та сформовано.', 'success')

    elif action == 'reject':
        db.execute('''
            UPDATE certificate_requests
            SET status = 'rejected', rejection_reason = ?
            WHERE id = ?
        ''', (reason, cert_id))
        db.commit()

        if req['telegram_id']:
            _send_tg_notification_sync(
                req['telegram_id'],
                f"❌ <b>Заяву на довідку відхилено:</b>\n\n"
                f"Причина: {reason or 'Некоректно вказані дані'}"
            )
        flash('Запит на довідку відхилено.', 'info')

    return redirect(url_for('services.certificates_index'))


@services_bp.route('/certificates/<int:cert_id>/view', methods=['GET'])
@login_required
def certificate_view(cert_id):
    user = g.current_user
    db = get_db()

    cert = db.execute('''
        SELECT cr.*, u.full_name as issuer_name
        FROM certificate_requests cr
        LEFT JOIN users u ON u.id = cr.issued_by
        WHERE cr.id = ?
    ''', (cert_id,)).fetchone()

    if not cert:
        abort(404)

    if user['role'] not in ('admin', 'principal', 'teacher', 'superuser', 'vice_principal') and cert['user_id'] != user['id']:
        abort(403)

    cert_dict = dict(cert)
    cert_type_title = CERT_TYPES.get(cert['cert_type'], 'Довідка з місця навчання')

    return render_template(
        'services/certificate_print.html',
        cert=cert_dict,
        cert_type_title=cert_type_title
    )
