from flask import Blueprint, render_template, request, redirect, url_for, g, abort
from app.auth import login_required, approved_required, role_required
from app.database import get_db
from app.security import generate_csrf_token, validate_csrf, sanitize_input
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)
voting_bp = Blueprint('voting', __name__)


@voting_bp.route('/')
def list_votings():
    db = get_db()
    rows = db.execute(
        'SELECT * FROM votings WHERE is_active=1 ORDER BY start_date DESC'
    ).fetchall()
    return render_template('voting/vote.html', votings=[dict(r) for r in rows])


@voting_bp.route('/<int:voting_id>')
@login_required
@approved_required
def vote(voting_id):
    user = g.current_user
    db = get_db()
    voting = db.execute('SELECT * FROM votings WHERE id=?', (voting_id,)).fetchone()
    if not voting:
        abort(404)
    voting = dict(voting)
    candidates = [dict(r) for r in db.execute(
        'SELECT * FROM voting_candidates WHERE voting_id=? ORDER BY name', (voting_id,)
    ).fetchall()]
    already_voted = db.execute(
        'SELECT * FROM votes WHERE voting_id=? AND voter_id=?', (voting_id, user['id'])
    ).fetchone()
    csrf_token = generate_csrf_token()
    return render_template('voting/vote_detail.html', voting=voting, candidates=candidates,
                           already_voted=already_voted is not None, csrf_token=csrf_token)


@voting_bp.route('/<int:voting_id>/submit', methods=['POST'])
@login_required
@approved_required
def submit_vote(voting_id):
    user = g.current_user
    if not validate_csrf(request.form.get('csrf_token', '')):
        abort(403)
    db = get_db()
    voting = db.execute('SELECT * FROM votings WHERE id=? AND is_active=1', (voting_id,)).fetchone()
    if not voting:
        abort(404)
    existing = db.execute('SELECT * FROM votes WHERE voting_id=? AND voter_id=?',
                          (voting_id, user['id'])).fetchone()
    if existing:
        return redirect(url_for('voting.vote', voting_id=voting_id))
    candidate_id = request.form.get('candidate_id', type=int)
    if not candidate_id:
        return redirect(url_for('voting.vote', voting_id=voting_id))
    db.execute('INSERT INTO votes (voting_id,voter_id,candidate_id) VALUES (?,?,?)',
               (voting_id, user['id'], candidate_id))
    db.commit()
    return redirect(url_for('voting.results', voting_id=voting_id))


@voting_bp.route('/<int:voting_id>/results')
def results(voting_id):
    db = get_db()
    voting = db.execute('SELECT * FROM votings WHERE id=?', (voting_id,)).fetchone()
    if not voting:
        abort(404)
    voting = dict(voting)
    candidates = db.execute(
        """SELECT vc.*, COUNT(v.id) as vote_count
           FROM voting_candidates vc
           LEFT JOIN votes v ON vc.id = v.candidate_id
           WHERE vc.voting_id = ?
           GROUP BY vc.id ORDER BY vote_count DESC""",
        (voting_id,)
    ).fetchall()
    total = db.execute('SELECT COUNT(*) as c FROM votes WHERE voting_id=?', (voting_id,)).fetchone()['c']
    return render_template('voting/results.html', voting=voting,
                           candidates=[dict(c) for c in candidates], total=total)


@voting_bp.route('/create', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'principal', 'vice_principal', 'school_president')
def create_voting():
    user = g.current_user
    db = get_db()
    csrf_token = generate_csrf_token()
    error = None

    if request.method == 'POST':
        if not validate_csrf(request.form.get('csrf_token', '')):
            error = 'Недійсний CSRF токен'
        else:
            title = sanitize_input(request.form.get('title', ''), 200)
            description = sanitize_input(request.form.get('description', ''), 500)
            end_days = request.form.get('end_days', 7, type=int)
            if not title:
                error = 'Введіть назву голосування'
            else:
                start = datetime.now()
                end = start + timedelta(days=end_days)
                db.execute(
                    'INSERT INTO votings (title,description,created_by,start_date,end_date,is_active) VALUES (?,?,?,?,?,1)',
                    (title, description, user['id'], start.isoformat(), end.isoformat())
                )
                db.commit()
                voting_id = db.execute('SELECT last_insert_rowid() as id').fetchone()['id']

                names = request.form.getlist('candidate_name[]')
                classes = request.form.getlist('candidate_class[]')
                descs = request.form.getlist('candidate_desc[]')
                for nm, cl, ds in zip(names, classes, descs):
                    nm = sanitize_input(nm, 100)
                    if nm:
                        db.execute('INSERT INTO voting_candidates (voting_id,name,class_name,description) VALUES (?,?,?,?)',
                                   (voting_id, nm, sanitize_input(cl, 20), sanitize_input(ds, 300)))
                db.commit()
                return redirect(url_for('voting.vote', voting_id=voting_id))

    return render_template('voting/create.html', csrf_token=csrf_token, error=error)
