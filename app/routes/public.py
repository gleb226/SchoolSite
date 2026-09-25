from flask import Blueprint, render_template, abort
from app.models import NewsModel
from app.database import get_db

public_bp = Blueprint('public', __name__)

BELL_SCHEDULE = [
    (1, '08:00', '08:45'),
    (2, '08:55', '09:40'),
    (3, '09:50', '10:35'),
    (4, '10:55', '11:40'),
    (5, '11:50', '12:35'),
    (6, '12:45', '13:30'),
    (7, '13:40', '14:25'),
    (8, '14:35', '15:20'),
]


@public_bp.route('/')
def index():
    db = get_db()
    news = NewsModel.get_published(limit=5)
    stats = {
        'students': db.execute("SELECT COUNT(*) as c FROM users WHERE role='student' AND is_approved=1").fetchone()['c'],
        'teachers': db.execute("SELECT COUNT(*) as c FROM users WHERE role='teacher' AND is_approved=1").fetchone()['c'],
        'classes': db.execute("SELECT COUNT(*) as c FROM classes").fetchone()['c'],
    }
    return render_template('index.html', news=news, stats=stats)


@public_bp.route('/about')
def about():
    return render_template('about.html')


@public_bp.route('/teachers')
def teachers():
    db = get_db()
    rows = db.execute(
        """SELECT u.full_name, u.email, tp.subject_specialty, tp.bio, tp.experience_years, tp.photo_url
           FROM users u
           LEFT JOIN teacher_profiles tp ON u.id = tp.user_id
           WHERE u.role = 'teacher' AND u.is_approved = 1
           ORDER BY u.full_name"""
    ).fetchall()
    teachers_list = [dict(r) for r in rows]
    return render_template('teachers.html', teachers=teachers_list)


@public_bp.route('/news')
def news():
    items = NewsModel.get_published(limit=20)
    return render_template('news.html', news_list=items)


@public_bp.route('/news/<int:news_id>')
def news_detail(news_id):
    item = NewsModel.get_by_id(news_id)
    if not item or not item['is_published']:
        abort(404)
    return render_template('news_detail.html', item=item)


@public_bp.route('/contacts')
def contacts():
    return render_template('contacts.html')


@public_bp.route('/schedule')
def schedule():
    return render_template('schedule.html', bell_schedule=BELL_SCHEDULE)


@public_bp.route('/enrollment')
def enrollment():
    return render_template('enrollment.html')


@public_bp.errorhandler(403)
def forbidden(e):
    return render_template('errors/403.html'), 403


@public_bp.errorhandler(404)
def not_found(e):
    return render_template('errors/404.html'), 404
