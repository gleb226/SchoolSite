"""
WSGI entry point for gunicorn / Render deployment.
Usage: gunicorn wsgi:app
"""
import os
from dotenv import load_dotenv

load_dotenv()

from app import create_app
from app.database import init_db, seed_data

app = create_app()

with app.app_context():
    try:
        init_db()
        seed_data()
    except Exception as e:
        print(f"DB init/seed skipped or failed: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
