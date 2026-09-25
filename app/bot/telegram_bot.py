import os
import logging
import asyncio
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    from aiogram import Bot, Dispatcher, types
    from aiogram.filters import Command
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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


async def send_2fa_code(telegram_id: int, code: str):
    bot = get_bot()
    if bot:
        await bot.send_message(
            telegram_id,
            f"🔐 Ваш код підтвердження для входу в Ужгородський ліцей №3:\n\n"
            f"<b>{code}</b>\n\n"
            f"Код дійсний 10 хвилин. Нікому не повідомляйте цей код!",
            parse_mode='HTML'
        )


async def send_notification(telegram_id: int, message: str):
    bot = get_bot()
    if bot:
        try:
            await bot.send_message(telegram_id, message, parse_mode='HTML')
        except Exception as e:
            logger.warning(f'Could not send notification to {telegram_id}: {e}')


async def run_bot():
    if not AIOGRAM_AVAILABLE:
        return
    token = os.environ.get('BOT_TOKEN', '').strip()
    if not token:
        return

    bot = Bot(token=token)
    dp = Dispatcher()

    @dp.message(Command('start'))
    async def cmd_start(message: types.Message):
        keyboard = [
            [InlineKeyboardButton(text='Оцінки', callback_data='grades')],
            [InlineKeyboardButton(text='Відвідуваність', callback_data='attendance')],
            [InlineKeyboardButton(text='Відкрити сайт', url='http://localhost:5000')],
        ]

        kb = InlineKeyboardMarkup(inline_keyboard=keyboard)
        welcome_text = (
            f"Вітаємо в боті Ужгородського ліцею №3!\n\n"
            f"Доступні команди:\n"
            f"/link <код> - Прив'язати Telegram до облікового запису\n"
            f"/grades - Останні оцінки\n"
            f"/attendance - Статистика відвідуваності\n"
            f"/help - Допомога\n"
        )
        await message.answer(welcome_text, reply_markup=kb, parse_mode='HTML')

    @dp.message(Command('admin'))
    async def cmd_admin(message: types.Message):
        admin_id = os.environ.get('ADMIN_TELEGRAM_ID', '513546547').strip()
        if str(message.from_user.id) != admin_id:
            await message.answer("Ця команда доступна лише адміністрації.")
            return
        try:
            import sqlite3
            db_path = os.environ.get('DATABASE_PATH', 'school.db')
            conn = sqlite3.connect(db_path)
            total = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
            pending = conn.execute('SELECT COUNT(*) FROM users WHERE is_approved=0').fetchone()[0]
            students = conn.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0]
            teachers = conn.execute("SELECT COUNT(*) FROM users WHERE role='teacher'").fetchone()[0]
            conn.close()
            text = (
                f"<b>Панель адміністратора:</b>\n\n"
                f"Всього користувачів: <b>{total}</b>\n"
                f"Очікують схвалення: <b>{pending}</b>\n"
                f"Учнів: <b>{students}</b>\n"
                f"Вчителів: <b>{teachers}</b>\n\n"
                f"🌐 Адмінка сайту: http://localhost:5000/admin\n"
                f"Перший директор: http://localhost:5000/setup-principal"
            )
            await message.answer(text, parse_mode='HTML')
        except Exception as e:
            logger.error(f'Admin command error: {e}')
            await message.answer("Помилка отримання статистики.")

    @dp.message(Command('link'))
    async def cmd_link(message: types.Message):
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Використання: /link <код>\nКод можна отримати в особистому кабінеті на сайті.")
            return
        code = parts[1].strip()
        # Find user with this link code (stored in telegram_username temporarily)
        try:
            import sqlite3
            db_path = os.environ.get('DATABASE_PATH', 'school.db')
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            user = conn.execute('SELECT * FROM users WHERE telegram_username=?', (f'link:{code}',)).fetchone()
            if user:
                conn.execute('UPDATE users SET telegram_id=?, telegram_username=? WHERE id=?',
                             (message.from_user.id, message.from_user.username or '', user['id']))
                conn.commit()
                await message.answer(f"Акаунт успішно прив'язано!\nВітаємо, {user['full_name']}!")
            else:
                await message.answer("Невірний код. Перевірте код в особистому кабінеті.")
            conn.close()
        except Exception as e:
            logger.error(f'Link error: {e}')
            await message.answer("Помилка. Спробуйте пізніше.")

    @dp.message(Command('grades'))
    async def cmd_grades(message: types.Message):
        try:
            import sqlite3
            db_path = os.environ.get('DATABASE_PATH', 'school.db')
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            user = conn.execute('SELECT * FROM users WHERE telegram_id=?', (message.from_user.id,)).fetchone()
            if not user:
                await message.answer("Акаунт не прив'язано. Використайте /link <код>")
                conn.close()
                return

            if user['role'] == 'student':
                student_id = user['id']
            elif user['role'] == 'parent':
                child = conn.execute('SELECT student_id FROM parent_student WHERE parent_id=?', (user['id'],)).fetchone()
                if not child:
                    await message.answer("У вас немає прив'язаних дітей.")
                    conn.close()
                    return
                student_id = child['student_id']
            else:
                await message.answer("Ця команда доступна лише для учнів та батьків.")
                conn.close()
                return

            grades = conn.execute(
                """SELECT g.grade, g.date, g.comment, s.name as subject
                   FROM grades g JOIN subjects s ON g.subject_id=s.id
                   WHERE g.student_id=? ORDER BY g.date DESC LIMIT 5""",
                (student_id,)
            ).fetchall()
            conn.close()

            if not grades:
                await message.answer("Оцінок поки немає.")
                return

            text = "<b>Останні оцінки:</b>\n\n"
            for g in grades:
                text += f"• {g['subject']}: <b>{g['grade']}</b> ({g['date']})"
                if g['comment']:
                    text += f" - {g['comment']}"
                text += "\n"
            await message.answer(text, parse_mode='HTML')
        except Exception as e:
            logger.error(f'Grades error: {e}')
            await message.answer("Помилка отримання оцінок.")

    @dp.message(Command('attendance'))
    async def cmd_attendance(message: types.Message):
        try:
            import sqlite3
            db_path = os.environ.get('DATABASE_PATH', 'school.db')
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            user = conn.execute('SELECT * FROM users WHERE telegram_id=?', (message.from_user.id,)).fetchone()
            if not user:
                await message.answer("Акаунт не прив'язано.")
                conn.close()
                return

            student_id = user['id'] if user['role'] == 'student' else None
            if user['role'] == 'parent':
                child = conn.execute('SELECT student_id FROM parent_student WHERE parent_id=?', (user['id'],)).fetchone()
                student_id = child['student_id'] if child else None

            if not student_id:
                await message.answer("Дані недоступні.")
                conn.close()
                return

            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM attendance WHERE student_id=? GROUP BY status",
                (student_id,)
            ).fetchall()
            conn.close()
            summary = {r['status']: r['cnt'] for r in rows}
            text = (
                f"<b>Відвідуваність:</b>\n\n"
                f"Присутній: {summary.get('present', 0)}\n"
                f"Відсутній: {summary.get('absent', 0)}\n"
                f"⏰ Запізнення: {summary.get('late', 0)}\n"
                f"Поважна причина: {summary.get('excused', 0)}"
            )
            await message.answer(text, parse_mode='HTML')
        except Exception as e:
            logger.error(f'Attendance error: {e}')
            await message.answer("Помилка отримання відвідуваності.")

    @dp.message(Command('help'))
    async def cmd_help(message: types.Message):
        await message.answer(
            "<b>Команди бота:</b>\n\n"
            "/start - Головне меню\n"
            "/link <код> - Прив'язати Telegram акаунт\n"
            "/grades - Останні 5 оцінок\n"
            "/attendance - Статистика відвідуваності\n"
            "/help - Ця довідка\n\n"
            "🌐 Сайт ліцею: http://localhost:5000",
            parse_mode='HTML'
        )

    @dp.callback_query()
    async def handle_callback(callback: types.CallbackQuery):
        if callback.data == 'grades':
            await cmd_grades(callback.message)
        elif callback.data == 'attendance':
            await cmd_attendance(callback.message)
        await callback.answer()

    logger.info('Telegram bot starting...')
    await dp.start_polling(bot)
