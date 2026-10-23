"""
WSGI entry point for gunicorn / Render deployment.
Usage: gunicorn wsgi:app
"""
import os
import threading
import logging
from dotenv import load_dotenv

load_dotenv()

from app import create_app
from app.database import init_db, seed_data

logger = logging.getLogger(__name__)

app = create_app()

with app.app_context():
    try:
        init_db()
        if os.environ.get('SEED_DEMO_DATA', 'false').lower() == 'true':
            seed_data()
    except Exception as e:
        logger.warning(f"DB init: {e}")

# Запуск Telegram-бота у фоновому потоці, якщо задано BOT_TOKEN
def _run_bot_background():
    bot_token = os.environ.get('BOT_TOKEN', '').strip()
    if not bot_token:
        return
    import asyncio
    from app.bot.telegram_bot import run_bot
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_bot())
    except Exception as e:
        logger.error(f"Telegram bot background error: {e}")

if os.environ.get('BOT_TOKEN', '').strip():
    t = threading.Thread(target=_run_bot_background, daemon=True)
    t.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
