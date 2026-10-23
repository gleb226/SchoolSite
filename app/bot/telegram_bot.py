"""
app/bot/telegram_bot.py — Telegram бот Ужгородського ліцею №3
===============================================================
Призначення:
  • 2FA коди для входу на сайт
  • Зручна прив'язка Telegram-акаунту (по номеру телефону, Deep Link в 1 клік, username)
  • Автоматична генерація та миттєва відправка QR-коду для шкільної їдальні
  • Сповіщення батькам: відсутність дитини, нові тести, статус довідки, ГПД
  • Регулярний дайджест для батьків
  • Адмін-статистика (/admin)
"""

import os
import io
import re
import logging
import asyncio
import sqlite3
import hashlib
from datetime import datetime, date

logger = logging.getLogger(__name__)

try:
    from aiogram import Bot, Dispatcher, types, F
    from aiogram.filters import Command
    from aiogram.types import (
        InlineKeyboardMarkup, InlineKeyboardButton,
        ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
        BufferedInputFile
    )
    AIOGRAM_AVAILABLE = True
except ImportError:
    AIOGRAM_AVAILABLE = False
    logger.warning('aiogram not installed - Telegram bot disabled')

_bot = None


def get_bot():
    global _bot
    if _bot is None and AIOGRAM_AVAILABLE:
        token = os.environ.get('BOT_TOKEN', '').strip()
        if token:
            _bot = Bot(token=token)
    return _bot


def make_qr_image_bytes(token: str) -> bytes:
    """Генерує графічне зображення QR-коду високої якості (PNG)."""
    import qrcode
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(token)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color='black', back_color='white').save(buf, 'PNG')
    return buf.getvalue()


def clean_phone_number(phone_raw: str) -> str:
    """Очищає телефон до послідовності цифр."""
    if not phone_raw:
        return ""
    digits = re.sub(r'\D', '', phone_raw)
    # Якщо починається з 380, повертаємо останні 9 або 10 цифр для гнучкого пошуку
    if digits.startswith('380') and len(digits) >= 12:
        return digits[2:]  # 0990571770
    return digits


# ── Публічні функції для виклику з інших модулів ──────────────────────────────

async def send_2fa_code(telegram_id: int, code: str):
    """Відправити 2FA код підтвердження."""
    bot = get_bot()
    if bot:
        try:
            await bot.send_message(
                telegram_id,
                f"🔐 <b>Код підтвердження входу в ліцей №3:</b>\n\n"
                f"<code>{code}</code>\n\n"
                f"Дійсний 10 хвилин. Нікому не передавайте цей код!",
                parse_mode='HTML'
            )
        except Exception as e:
            logger.warning(f"Could not send 2FA code to {telegram_id}: {e}")


def send_notification_sync(telegram_id: int, message: str):
    """Синхронна надійна відправка повідомлення в Telegram через requests/urllib."""
    token = os.environ.get('BOT_TOKEN', '').strip()
    if not token or not telegram_id:
        return False
    try:
        import urllib.request
        import urllib.parse
        import json
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({
            'chat_id': telegram_id,
            'text': message,
            'parse_mode': 'HTML',
            'disable_web_page_preview': False
        }).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        logger.warning(f"Could not send sync tg notification to {telegram_id}: {e}")
        return False

async def send_notification(telegram_id: int, message: str):
    """Відправити довільне сповіщення (HTML) з автоматичним синхронним fallback."""
    # Спочатку пробуємо синхронно у фоновому потоці або через bot
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, send_notification_sync, telegram_id, message)
    except Exception:
        return send_notification_sync(telegram_id, message)


async def send_lunch_qr(telegram_id: int, qr_bytes: bytes,
                        student_name: str, day: str):
    """Надіслати реальне QR-зображення для харчування в Telegram."""
    bot = get_bot()
    if not bot:
        return
    try:
        qr_file = BufferedInputFile(qr_bytes, filename=f'lunch_{day}.png')
        await bot.send_photo(
            telegram_id,
            photo=qr_file,
            caption=(
                f"🍽️ <b>QR-код для харчування в столовій</b>\n\n"
                f"👤 Учень: <b>{student_name}</b>\n"
                f"📅 Дата: <b>{day}</b>\n\n"
                f"Покажіть цей QR-код касиру в шкільній їдальні.\n"
                f"<i>Дійсний тільки сьогодні для отримання гарячого обіду.</i>"
            ),
            parse_mode='HTML'
        )
    except Exception as e:
        logger.warning(f'Could not send lunch QR photo to {telegram_id}: {e}')


async def notify_absence(parent_telegram_id: int, student_name: str,
                         date_str: str, subject: str):
    """Сповіщення батькам про відсутність дитини на уроці."""
    await send_notification(
        parent_telegram_id,
        f"⚠️ <b>Відсутність на уроці</b>\n\n"
        f"Учень: <b>{student_name}</b>\n"
        f"Предмет: {subject}\n"
        f"Дата: {date_str}\n\n"
        f"📓 Деталі — у щоденнику Мрія."
    )


async def notify_new_test(telegram_id: int, student_name: str,
                          test_title: str, subject: str, deadline: str = '', test_url: str = ''):
    """Сповіщення про новий тест/контрольну з дедлайном та прямим лінком."""
    deadline_line = f"\n⏰ <b>Дедлайн:</b> {deadline.replace('T', ' ')}" if deadline else ""
    link_line = f"\n\n👉 <b>Пройти тест:</b> {test_url}" if test_url else "\n\n🌐 Пройти тест — на сайті ліцею."
    await send_notification(
        telegram_id,
        f"📋 <b>Новий тест / контрольна робота!</b>\n\n"
        f"👤 Учень: <b>{student_name}</b>\n"
        f"📚 Предмет: <b>{subject}</b>\n"
        f"📝 Тест: <b>{test_title}</b>"
        f"{deadline_line}"
        f"{link_line}"
    )


async def notify_certificate_ready(telegram_id: int, cert_number: str,
                                   cert_type: str, student_name: str):
    """Сповіщення про готову електронну довідку."""
    await send_notification(
        telegram_id,
        f"📄 <b>Довідка готова</b>\n\n"
        f"Номер: <b>{cert_number}</b>\n"
        f"Учень: {student_name}\n"
        f"Тип: {cert_type}\n\n"
        f"Завантажити — в особистому кабінеті на сайті ліцею."
    )


async def send_parent_digest(telegram_id: int, student_name: str,
                              recent_info: list):
    if not recent_info:
        return
    today_str = date.today().strftime('%d.%m.%Y')
    lines = '\n'.join(f"• {item}" for item in recent_info)
    await send_notification(
        telegram_id,
        f"📰 <b>Дайджест — {today_str}</b>\n"
        f"Учень: <b>{student_name}</b>\n\n"
        f"{lines}\n\n"
        f"📓 Оцінки та відвідуваність — у щоденнику <a href='https://mriia.gov.ua/select'>Мрія</a>."
    )


# ── Допоміжні генератори меню і QR ───────────────────────────────────────────

def build_main_keyboard(is_linked: bool) -> ReplyKeyboardMarkup:
    """Генерує зручне постійне меню бота."""
    if is_linked:
        kb = [
            [KeyboardButton(text="🍽️ Мій QR-код на обід")],
            [KeyboardButton(text="🎒 Забрати з ГПД"), KeyboardButton(text="📑 Мої довідки")],
            [KeyboardButton(text="ℹ️ Мій профіль"), KeyboardButton(text="🌐 Сайт ліцею")]
        ]
    else:
        kb = [
            [KeyboardButton(text="📱 Надіслати номер телефону для підключення", request_contact=True)],
            [KeyboardButton(text="ℹ️ Допомога")]
        ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


async def generate_and_send_qr(bot: Bot, telegram_id: int, user: dict, conn: sqlite3.Connection):
    """Генерує та відправляє свіжий графічний QR-код харчування учню або батькам."""
    today = date.today().isoformat()
    secret = os.environ.get('SECRET_KEY', 'dev-secret')

    def make_token(sid):
        return hashlib.sha256(f"{sid}:{today}:{secret}".encode()).hexdigest()[:32]

    def get_token(sid):
        row = conn.execute(
            'SELECT token, used, used_at FROM lunch_tokens WHERE student_id=? AND date=?',
            (sid, today)
        ).fetchone()
        if row:
            return row['token'], row['used'], row['used_at']
        tok = make_token(sid)
        try:
            conn.execute('INSERT INTO lunch_tokens (student_id,token,date) VALUES (?,?,?)',
                         (sid, tok, today))
            conn.commit()
        except Exception:
            pass
        return tok, 0, None

    if user['role'] == 'student':
        token, used, used_at = get_token(user['id'])
        if used:
            t_str = used_at[11:16] if used_at else ''
            await bot.send_message(telegram_id, f"✅ <b>Сьогоднішній обід уже отримано о {t_str}</b>", parse_mode='HTML')
        else:
            qr_bytes = make_qr_image_bytes(token)
            await send_lunch_qr(telegram_id, qr_bytes, user['full_name'], today)

    elif user['role'] == 'parent':
        children = conn.execute(
            '''SELECT u.id, u.full_name, c.name as class_name
               FROM users u
               JOIN parent_student ps ON u.id=ps.student_id
               LEFT JOIN student_classes sc ON sc.student_id=u.id
               LEFT JOIN classes c ON c.id=sc.class_id
               WHERE ps.parent_id=?''',
            (user['id'],)
        ).fetchall()
        if not children:
            await bot.send_message(telegram_id, "ℹ️ До вашого акаунту поки немає прив'язаних дітей.")
            return

        for ch in children:
            token, used, used_at = get_token(ch['id'])
            if used:
                t_str = used_at[11:16] if used_at else ''
                await bot.send_message(telegram_id, f"✅ <b>{ch['full_name']}</b>: обід уже отримано сьогодні о {t_str}", parse_mode='HTML')
            else:
                qr_bytes = make_qr_image_bytes(token)
                await send_lunch_qr(telegram_id, qr_bytes, ch['full_name'], today)
    else:
        # Для вчителя або адміністратора
        token = make_token(user['id'])
        qr_bytes = make_qr_image_bytes(token)
        await send_lunch_qr(telegram_id, qr_bytes, user['full_name'], today)


# ── Запуск бота ───────────────────────────────────────────────────────────────

async def run_bot():
    if not AIOGRAM_AVAILABLE:
        return
    token = os.environ.get('BOT_TOKEN', '').strip()
    if not token:
        return

    import tempfile
    import atexit
    lock_path = os.path.join(tempfile.gettempdir(), f'schoolsite_bot_{token[:8]}.lock')

    try:
        lock_fd = open(lock_path, 'x')
    except FileExistsError:
        logger.warning('Bot lock file exists — skipping to avoid TelegramConflictError.')
        return

    def _release_lock():
        try:
            lock_fd.close()
            os.remove(lock_path)
        except Exception:
            pass

    atexit.register(_release_lock)

    bot = Bot(token=token)
    dp = Dispatcher()

    def get_db_conn():
        db_path = os.environ.get('DATABASE_PATH', 'school.db')
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        return c

    # ── /start (підтримка Deep Link в 1 клік) ──────────────────────────────────
    @dp.message(Command('start'))
    async def cmd_start(message: types.Message):
        parts = message.text.split(maxsplit=1)
        conn = get_db_conn()

        # 1. Перевірка чи є код у /start link_XXX або /start link:XXX
        if len(parts) > 1 and (parts[1].startswith('link_') or parts[1].startswith('link:')):
            raw_code = parts[1].replace('link_', '').replace('link:', '').strip().upper()
            user = conn.execute(
                "SELECT * FROM users WHERE telegram_username=?", (f'link:{raw_code}',)
            ).fetchone()
            if user:
                conn.execute(
                    'UPDATE users SET telegram_id=?, telegram_username=? WHERE id=?',
                    (message.from_user.id, message.from_user.username or '', user['id'])
                )
                conn.commit()
                role_labels = {
                    'student': 'Учень', 'parent': 'Батько/мати',
                    'teacher': 'Вчитель', 'admin': 'Адміністратор',
                    'superuser': 'Superuser', 'principal': 'Директор'
                }
                role_label = role_labels.get(user['role'], user['role'])

                await message.answer(
                    f"🎉 <b>Ура! Акаунт успішно підключено в 1 клік!</b>\n\n"
                    f"👤 <b>{user['full_name']}</b> ({role_label})\n"
                    f"Тепер усі шкільні сповіщення та сервіси доступні прямо тут.",
                    reply_markup=build_main_keyboard(is_linked=True),
                    parse_mode='HTML'
                )

                # ОДРАЗУ генеруємо та надсилаємо реальний QR-код!
                try:
                    await generate_and_send_qr(bot, message.from_user.id, dict(user), conn)
                except Exception as e:
                    logger.error(f"Error sending instant QR: {e}")

                conn.close()
                return

        # 2. Перевіряємо чи цей telegram_id вже прив'язаний
        user = conn.execute('SELECT * FROM users WHERE telegram_id=?', (message.from_user.id,)).fetchone()
        if user:
            conn.close()
            await message.answer(
                f"👋 Вітаємо знову, <b>{user['full_name']}</b>!\n\n"
                f"Оберіть дію в меню нижче:",
                reply_markup=build_main_keyboard(is_linked=True),
                parse_mode='HTML'
            )
            return

        # 3. Перевірка чи є в базі користувач з таким же @username
        if message.from_user.username:
            user_by_uname = conn.execute(
                'SELECT * FROM users WHERE LOWER(telegram_username) = LOWER(?)',
                (message.from_user.username,)
            ).fetchone()
            if user_by_uname:
                conn.execute(
                    'UPDATE users SET telegram_id=? WHERE id=?',
                    (message.from_user.id, user_by_uname['id'])
                )
                conn.commit()
                await message.answer(
                    f"✅ <b>Акаунт розпізнано за вашим @{message.from_user.username}!</b>\n\n"
                    f"👤 <b>{user_by_uname['full_name']}</b>\n"
                    f"Акаунт успішно зв'язано з Telegram.",
                    reply_markup=build_main_keyboard(is_linked=True),
                    parse_mode='HTML'
                )
                try:
                    await generate_and_send_qr(bot, message.from_user.id, dict(user_by_uname), conn)
                except Exception as e:
                    logger.error(f"Error sending auto QR: {e}")
                conn.close()
                return

        conn.close()

        # 4. Якщо ще не прив'язаний — показуємо зручну кнопку поділитися номером
        welcome_text = (
            "👋 <b>Ласкаво просимо до бота Ужгородського ліцею №3!</b>\n\n"
            "Щоб отримувати <b>QR-код на харчування</b>, сповіщення про уроки та довідки:\n\n"
            "👇 <b>Натисніть кнопку «📱 Надіслати номер телефону» нижче</b>, і бот підключить ваш акаунт за 1 секунду!\n\n"
            "<i>Або натисніть кнопку «Підключити в 1 клік» на сайті ліцею.</i>"
        )
        await message.answer(
            welcome_text,
            reply_markup=build_main_keyboard(is_linked=False),
            parse_mode='HTML'
        )

    # ── Обробка відправки контакту (кнопка внизу) ──────────────────────────────
    @dp.message(F.contact)
    async def contact_handler(message: types.Message):
        if not message.contact or not message.contact.phone_number:
            await message.answer("Не вдалося отримати номер телефону. Спробуйте ще раз.")
            return

        raw_phone = message.contact.phone_number
        digits = clean_phone_number(raw_phone)
        conn = get_db_conn()

        # Шукаємо користувача за телефоном (порівнюємо очищені цифри)
        all_users = conn.execute("SELECT * FROM users WHERE phone IS NOT NULL AND phone != ''").fetchall()
        matched_user = None
        for u in all_users:
            u_digits = clean_phone_number(u['phone'])
            if digits and u_digits and (digits.endswith(u_digits) or u_digits.endswith(digits)):
                matched_user = u
                break

        if matched_user:
            conn.execute(
                'UPDATE users SET telegram_id=?, telegram_username=? WHERE id=?',
                (message.from_user.id, message.from_user.username or '', matched_user['id'])
            )
            conn.commit()

            role_labels = {
                'student': 'Учень', 'parent': 'Батько/мати',
                'teacher': 'Вчитель', 'admin': 'Адміністратор',
                'superuser': 'Superuser', 'principal': 'Директор'
            }
            role_label = role_labels.get(matched_user['role'], matched_user['role'])

            await message.answer(
                f"🎉 <b>Чудово! Акаунт успішно підключено за номером телефону!</b>\n\n"
                f"👤 <b>{matched_user['full_name']}</b> ({role_label})\n"
                f"📱 Номер: {raw_phone}\n\n"
                f"Зараз генеруємо ваш QR-код для шкільної їдальні...",
                reply_markup=build_main_keyboard(is_linked=True),
                parse_mode='HTML'
            )

            # ОДРАЗУ генеруємо та надсилаємо реальний QR-код!
            try:
                await generate_and_send_qr(bot, message.from_user.id, dict(matched_user), conn)
            except Exception as e:
                logger.error(f"Error sending contact QR: {e}")

        else:
            await message.answer(
                f"❌ Користувача з номером <b>{raw_phone}</b> не знайдено в базі ліцею.\n\n"
                f"Будь ласка, переконайтеся що цей номер вказано у вашому профілі на сайті, "
                f"або підключіться через кнопку «Підключити в 1 клік» на сайті ліцею.",
                reply_markup=build_main_keyboard(is_linked=False),
                parse_mode='HTML'
            )

        conn.close()

    # ── /qr та /lunch — відправка реального QR-коду ───────────────────────────
    @dp.message(Command('qr'))
    @dp.message(Command('lunch'))
    @dp.message(F.text == "🍽️ Мій QR-код на обід")
    async def cmd_qr(message: types.Message):
        conn = get_db_conn()
        user = conn.execute('SELECT * FROM users WHERE telegram_id=?', (message.from_user.id,)).fetchone()
        if not user:
            conn.close()
            await message.answer(
                "❌ Ваш Telegram ще не підключено до шкільного акаунту.\n\n"
                "Натисніть кнопку <b>«📱 Надіслати номер телефону»</b> нижче для швидкого підключення!",
                reply_markup=build_main_keyboard(is_linked=False),
                parse_mode='HTML'
            )
            return

        try:
            await generate_and_send_qr(bot, message.from_user.id, dict(user), conn)
        except Exception as e:
            logger.error(f"QR command error: {e}")
            await message.answer("Сталася помилка при формуванні QR-коду. Спробуйте пізніше.")
        finally:
            conn.close()

    # ── /link — текстова сумісність ───────────────────────────────────────────
    @dp.message(Command('link'))
    async def cmd_link(message: types.Message):
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                "Використання: /link [код]\n"
                "Або просто натисніть кнопку «📱 Надіслати номер телефону» внизу!",
                reply_markup=build_main_keyboard(is_linked=False)
            )
            return
        code = parts[1].strip().upper()
        conn = get_db_conn()
        user = conn.execute("SELECT * FROM users WHERE telegram_username=?", (f'link:{code}',)).fetchone()
        if user:
            conn.execute(
                'UPDATE users SET telegram_id=?, telegram_username=? WHERE id=?',
                (message.from_user.id, message.from_user.username or '', user['id'])
            )
            conn.commit()
            await message.answer(
                f"✅ Акаунт успішно прив'язано!\nВітаємо, <b>{user['full_name']}</b>!",
                reply_markup=build_main_keyboard(is_linked=True),
                parse_mode='HTML'
            )
            await generate_and_send_qr(bot, message.from_user.id, dict(user), conn)
        else:
            await message.answer("❌ Невірний код або він вже застарів. Спробуйте підключення за номером телефону.")
        conn.close()

    # ── ГПД (Забрати дитину) ──────────────────────────────────────────────────
    @dp.message(Command('gpd'))
    @dp.message(F.text == "🎒 Забрати з ГПД")
    async def cmd_gpd(message: types.Message):
        conn = get_db_conn()
        user = conn.execute('SELECT * FROM users WHERE telegram_id=?', (message.from_user.id,)).fetchone()
        if not user:
            conn.close()
            await message.answer("Спочатку підключіть акаунт.")
            return

        if user['role'] == 'parent':
            children = conn.execute('''
                SELECT u.id, u.full_name, c.name as class_name
                FROM users u
                JOIN parent_student ps ON ps.student_id = u.id
                LEFT JOIN student_classes sc ON sc.student_id = u.id
                LEFT JOIN classes c ON c.id = sc.class_id
                WHERE ps.parent_id = ?
            ''', (user['id'],)).fetchall()

            if not children:
                conn.close()
                await message.answer("У вас немає прив'язаних дітей.")
                return

            now_time = datetime.now().strftime('%H:%M')
            today_str = date.today().isoformat()

            for ch in children:
                conn.execute('''
                    INSERT INTO gpd_pickups (parent_id, student_id, note, status)
                    VALUES (?, ?, 'Запит з Telegram-бота', 'requested')
                ''', (user['id'], ch['id']))
            conn.commit()

            # Сповіщаємо вчителів
            ch_names = ', '.join(f"{ch['full_name']} ({ch['class_name'] or ''})" for ch in children)
            t_msg = (
                f"🔔 <b>ГПД: Батьки прийшли за дитиною!</b>\n\n"
                f"👤 <b>Учень:</b> {ch_names}\n"
                f"👨‍👩‍👧 <b>Забирає:</b> {user['full_name']}\n"
                f"⏰ <b>Час:</b> {now_time}\n"
                f"📍 <i>Будь ласка, допоможіть зібратися та відпустіть до батьків.</i>"
            )
            teachers = conn.execute("SELECT telegram_id FROM users WHERE role IN ('teacher', 'admin', 'principal') AND telegram_id IS NOT NULL").fetchall()
            for t in teachers:
                if t['telegram_id'] and t['telegram_id'] != message.from_user.id:
                    try:
                        await bot.send_message(t['telegram_id'], t_msg, parse_mode='HTML')
                    except Exception:
                        pass

            conn.close()
            await message.answer(
                f"✅ <b>Запит надіслано черговому вихователю ГПД!</b>\n\n"
                f"Діти: <b>{ch_names}</b>\n"
                f"Очікуйте, дитину вже збирають і відпустять до входу.",
                parse_mode='HTML'
            )

        elif user['role'] in ('teacher', 'admin', 'principal', 'superuser'):
            today_str = date.today().isoformat()
            pickups = conn.execute('''
                SELECT p.pickup_time, p.status, s.full_name as student_name, par.full_name as parent_name
                FROM gpd_pickups p
                JOIN users s ON s.id = p.student_id
                JOIN users par ON par.id = p.parent_id
                WHERE date(p.pickup_time) = ?
                ORDER BY p.pickup_time DESC LIMIT 5
            ''', (today_str,)).fetchall()
            conn.close()

            if not pickups:
                await message.answer("Сьогодні запитів на забирання з ГПД ще не було.")
                return

            text = f"🎒 <b>Запити з ГПД на сьогодні ({today_str}):</b>\n\n"
            for p in pickups:
                t = p['pickup_time'][11:16] if p['pickup_time'] else ''
                status_icon = "✅" if p['status'] == 'completed' else "⏳"
                text += f"{status_icon} <b>{t}</b> — {p['student_name']} (забирає: {p['parent_name']})\n"
            await message.answer(text, parse_mode='HTML')
        else:
            conn.close()
            await message.answer("Ця функція для батьків та вчителів.")

    # ── Довідки ───────────────────────────────────────────────────────────────
    @dp.message(Command('certs'))
    @dp.message(F.text == "📑 Мої довідки")
    async def cmd_certs(message: types.Message):
        conn = get_db_conn()
        user = conn.execute('SELECT * FROM users WHERE telegram_id=?', (message.from_user.id,)).fetchone()
        if not user:
            conn.close()
            await message.answer("Спочатку підключіть акаунт.")
            return

        certs = conn.execute(
            "SELECT * FROM certificate_requests WHERE user_id=? ORDER BY created_at DESC LIMIT 5",
            (user['id'],)
        ).fetchall()
        conn.close()

        if not certs:
            await message.answer(
                "📑 У вас поки немає замовлених довідок.\n\n"
                "Замовити офіційну довідку можна на сайті ліцею в розділі «Електронні довідки»."
            )
            return

        text = "📑 <b>Ваші останні довідки:</b>\n\n"
        status_map = {'approved': '✅ Готова', 'pending': '⏳ На розгляді', 'rejected': '❌ Відхилено'}
        for c in certs:
            st = status_map.get(c['status'], c['status'])
            num = f" (№{c['cert_number']})" if c['cert_number'] else ""
            text += f"• <b>{c['student_name']}</b>{num}\n  Статус: {st}\n"
        text += "\n🌐 Завантажити або замовити нову можна на сайті ліцею."
        await message.answer(text, parse_mode='HTML')

    # ── Профіль ───────────────────────────────────────────────────────────────
    @dp.message(Command('status'))
    @dp.message(F.text == "ℹ️ Мій профіль")
    async def cmd_profile(message: types.Message):
        conn = get_db_conn()
        user = conn.execute('SELECT * FROM users WHERE telegram_id=?', (message.from_user.id,)).fetchone()
        if not user:
            conn.close()
            await message.answer(
                "Акаунт не підключено.\nНатисніть «📱 Надіслати номер телефону» нижче.",
                reply_markup=build_main_keyboard(is_linked=False)
            )
            return

        role_labels = {
            'student': 'Учень', 'parent': 'Батько/мати',
            'teacher': 'Вчитель', 'admin': 'Адміністратор',
            'superuser': 'Superuser', 'principal': 'Директор'
        }
        text = (
            f"👤 <b>{user['full_name']}</b>\n"
            f"Роль: <b>{role_labels.get(user['role'], user['role'])}</b>\n"
            f"Логін: <code>{user['username']}</code>\n"
            f"Телефон: {user['phone'] or 'не вказано'}\n\n"
            f"🌐 Сайт ліцею: http://localhost:5000"
        )
        conn.close()
        await message.answer(text, reply_markup=build_main_keyboard(is_linked=True), parse_mode='HTML')

    @dp.message(F.text == "🌐 Сайт ліцею")
    async def cmd_site(message: types.Message):
        await message.answer("🌐 <b>Ужгородський ліцей №3:</b>\nhttp://localhost:5000", parse_mode='HTML')

    @dp.message(Command('admin'))
    async def cmd_admin(message: types.Message):
        admin_id = os.environ.get('ADMIN_TELEGRAM_ID', '').strip()
        if not admin_id or str(message.from_user.id) != admin_id:
            await message.answer("Ця команда доступна лише адміністрації.")
            return
        try:
            conn = get_db_conn()
            total = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
            pending = conn.execute('SELECT COUNT(*) FROM users WHERE is_approved=0').fetchone()[0]
            certs = conn.execute("SELECT COUNT(*) FROM certificate_requests WHERE status='pending'").fetchone()[0]
            conn.close()
            await message.answer(
                f"<b>📊 Адмін-панель</b>\n\nКористувачів: {total}\nОчікують схвалення: {pending}\nДовідок на розгляді: {certs}",
                parse_mode='HTML'
            )
        except Exception as e:
            await message.answer(f"Помилка: {e}")

    @dp.message(Command('help'))
    @dp.message(F.text == "ℹ️ Допомога")
    async def cmd_help(message: types.Message):
        await message.answer(
            "<b>Довідка бота Ужгородського ліцею №3:</b>\n\n"
            "🍽️ /qr або /lunch — Отримати свіжий QR-код для шкільної їдальні\n"
            "🎒 /gpd — Запит «Забрати дитину з ГПД» черговому вчителю\n"
            "📑 /certs — Статус електронних довідок\n"
            "ℹ️ /status — Ваш профіль\n"
            "⚡ /start — Головне меню та оновлення підключення",
            parse_mode='HTML'
        )

    # ── Текстовий ввід номеру телефону користувачем ──────────────────────────
    @dp.message(F.text)
    async def generic_text_handler(message: types.Message):
        text = message.text.strip()
        # Перевірка чи це схоже на номер телефону (від 9 до 13 цифр)
        cleaned = clean_phone_number(text)
        if len(cleaned) >= 9 and len(cleaned) <= 12 and (cleaned.startswith('0') or cleaned.startswith('380')):
            conn = get_db_conn()
            all_users = conn.execute("SELECT * FROM users WHERE phone IS NOT NULL AND phone != ''").fetchall()
            matched = None
            for u in all_users:
                u_dig = clean_phone_number(u['phone'])
                if u_dig and (cleaned.endswith(u_dig) or u_dig.endswith(cleaned)):
                    matched = u
                    break

            if matched:
                conn.execute(
                    'UPDATE users SET telegram_id=?, telegram_username=? WHERE id=?',
                    (message.from_user.id, message.from_user.username or '', matched['id'])
                )
                conn.commit()
                await message.answer(
                    f"✅ <b>Знайдено! Акаунт успішно підключено:</b>\n\n"
                    f"👤 <b>{matched['full_name']}</b> ({matched['role']})\n\n"
                    f"Надсилаємо ваш QR-код для їдальні...",
                    reply_markup=build_main_keyboard(is_linked=True),
                    parse_mode='HTML'
                )
                try:
                    await generate_and_send_qr(bot, message.from_user.id, dict(matched), conn)
                except Exception as e:
                    logger.error(f"Text match QR error: {e}")
                conn.close()
                return
            conn.close()

        # Якщо просто текст і не прив'язаний
        conn = get_db_conn()
        user = conn.execute('SELECT * FROM users WHERE telegram_id=?', (message.from_user.id,)).fetchone()
        conn.close()

        if not user:
            await message.answer(
                "Щоб підключити акаунт, натисніть кнопку «📱 Надіслати номер телефону» нижче або надішліть свій номер телефону повідомленням.",
                reply_markup=build_main_keyboard(is_linked=False)
            )
        else:
            await message.answer(
                "Скористайтеся кнопками внизу екрана для вибору дії.",
                reply_markup=build_main_keyboard(is_linked=True)
            )

    # ── Запуск polling ────────────────────────────────────────────────────────

async def _deadline_reminder_worker(bot: Bot):
    """Періодично перевіряє активні тести та надсилає нагадування учням за 24 год та за 2 год до дедлайну."""
    while True:
        try:
            await asyncio.sleep(60)  # check every minute
            conn = get_db_conn()
            now_dt = datetime.now()
            now_str = now_dt.strftime('%Y-%m-%dT%H:%M')
            
            # Select tests with deadline in future that have not expired
            tests = conn.execute(
                """SELECT t.id, t.title, t.deadline, t.class_id, s.name as subject_name
                   FROM tests t
                   LEFT JOIN subjects s ON s.id = t.subject_id
                   WHERE t.is_active=1 AND t.deadline IS NOT NULL AND t.deadline != ''"""
            ).fetchall()

            for t in tests:
                try:
                    d_clean = t['deadline'].replace(' ', 'T')[:16]
                    d_dt = datetime.strptime(d_clean, '%Y-%m-%dT%H:%M')
                except Exception:
                    continue

                diff_seconds = (d_dt - now_dt).total_seconds()
                if diff_seconds <= 0:
                    continue

                # Remind within [23h50m .. 24h00m] or [1h50m .. 2h00m]
                is_24h = (82800 <= diff_seconds <= 86400)
                is_2h = (6600 <= diff_seconds <= 7200)

                if is_24h or is_2h:
                    rem_label = "залишилось менше 24 годин" if is_24h else "залишилось лише 2 години!"
                    # Find students in this class who have not finished the test
                    students = conn.execute(
                        """SELECT u.telegram_id, u.full_name
                           FROM users u
                           JOIN student_classes sc ON sc.student_id = u.id
                           WHERE sc.class_id = ? AND u.telegram_id IS NOT NULL
                             AND u.id NOT IN (
                                 SELECT student_id FROM test_attempts WHERE test_id = ? AND status = 'finished'
                             )""",
                        (t['class_id'], t['id'])
                    ).fetchall()

                    for st in students:
                        msg = (
                            f"⏰ <b>Нагадування про тест!</b>\n\n"
                            f"Привіт, <b>{st['full_name']}</b>! Нагадуємо зробити тест: <b>«{t['title']}»</b> ({t['subject_name']}).\n\n"
                            f"⏳ <b>До завершення:</b> {rem_label}\n"
                            f"Дедлайн: <b>{d_clean.replace('T', ' ')}</b>\n\n"
                            f"Не зволікай, заходь на сайт і проходь тестування!"
                        )
                        try:
                            await send_notification(st['telegram_id'], msg)
                        except Exception:
                            pass
            conn.close()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Deadline reminder loop error: {e}")

    logger.info('Telegram bot starting...')
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as _e:
        logger.debug(f'delete_webhook skipped: {_e}')
    reminder_task = asyncio.create_task(_deadline_reminder_worker(bot))
    try:
        await dp.start_polling(bot, handle_signals=False)
    finally:
        reminder_task.cancel()
        _release_lock()
