#!/usr/bin/env python3
"""Single entry point - starts Flask + Telegram bot"""
import os
import sys
import threading
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def start_telegram_bot():
    bot_token = os.environ.get('BOT_TOKEN', '').strip()
    if not bot_token:
        logger.warning('BOT_TOKEN not set - Telegram bot disabled')
        return
    try:
        import asyncio
        from app.bot.telegram_bot import run_bot
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_bot())
    except Exception as e:
        logger.error(f'Telegram bot error: {e}')


def main():
    try:
        import flask
    except ImportError:
        logger.info('Installing dependencies...')
        os.system(f'{sys.executable} -m pip install -r requirements.txt')

    from app import create_app
    from app.database import init_db, seed_data

    app = create_app()

    with app.app_context():
        init_db()
        seed_data()

    bot_thread = threading.Thread(target=start_telegram_bot, daemon=True)
    bot_thread.start()

    port = int(os.environ.get('PORT', 5000))
    logger.info(f'Starting Flask on http://localhost:{port}')
    logger.info('Create the first principal at /setup-principal, then approve users from the admin panel.')
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
