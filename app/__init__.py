from flask import Flask, g, request, session
import os
import jwt
import logging
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

logger = logging.getLogger(__name__)


def create_app():
    if load_dotenv:
        load_dotenv()

    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates'),
        static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')
    )

    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-prod')
    app.config['DATABASE'] = os.environ.get('DATABASE_PATH', 'school.db')
    app.config['JWT_ALGORITHM'] = 'HS256'
    app.config['JWT_EXPIRY_HOURS'] = 24

    @app.after_request
    def set_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "frame-src 'self' https://www.google.com;"
        )
        return response

    @app.before_request
    def load_current_user():
        g.current_user = None
        token = request.cookies.get('auth_token')
        if token:
            try:
                payload = jwt.decode(
                    token,
                    app.config['SECRET_KEY'],
                    algorithms=[app.config['JWT_ALGORITHM']]
                )
                from app.database import get_db
                db = get_db()
                user = db.execute(
                    'SELECT * FROM users WHERE id = ? AND is_active = 1',
                    (payload['user_id'],)
                ).fetchone()
                if user:
                    g.current_user = dict(user)
            except Exception:
                pass

    @app.context_processor
    def inject_user():
        return {'current_user': g.get('current_user')}

    from app.routes.public import public_bp
    from app.routes.api import api_bp
    from app.routes.admin import admin_bp
    from app.routes.diary import diary_bp
    from app.routes.auth_routes import auth_bp
    from app.routes.tests_routes import tests_bp
    from app.routes.voting_routes import voting_bp
    from app.routes.dashboard_routes import dashboard_bp
    from app.routes.session_routes import session_bp
    from app.routes.materials import materials_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(diary_bp, url_prefix='/diary')
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(tests_bp, url_prefix='/tests')
    app.register_blueprint(voting_bp, url_prefix='/voting')
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(session_bp, url_prefix='/session')
    app.register_blueprint(materials_bp, url_prefix='/materials')

    return app
