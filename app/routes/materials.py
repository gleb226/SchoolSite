"""
app/routes/materials.py — Educational materials blueprint
==========================================================
Routes:
  GET  /materials/              — list teacher's materials
  GET  /materials/create        — create form
  POST /materials/create        — save new material
  GET  /materials/<id>          — view material (rendered Markdown)
  GET  /materials/<id>/edit     — edit form
  POST /materials/<id>/edit     — save edits
  POST /materials/<id>/delete   — delete
  POST /materials/api/generate  — AI generate Markdown content
  POST /materials/api/ocr       — OCR handwritten photo → Markdown
  POST /materials/api/format    — AI format raw text → clean Markdown
"""

import json
import logging
import base64
import re
from datetime import datetime

from flask import (Blueprint, g, request, jsonify, render_template,
                   redirect, url_for, flash)

from app.database import get_db
from app.security import login_required, role_required, sanitize_input, validate_csrf, generate_csrf_token
from app.llm import llm_generate, llm_ocr

logger = logging.getLogger(__name__)

materials_bp = Blueprint('materials', __name__)

# ── Prompt templates ───────────────────────────────────────────────────────────

_GENERATE_PROMPT = """Ти — досвідчений методист українського ліцею.
Створи навчальний матеріал у форматі Markdown для вчителя.

Предмет: {subject}
Клас: {class_level}
Тема / запит: {topic}
Тип матеріалу: {material_type}

Вимоги до оформлення:
1. Використовуй Markdown без зайвих емодзі.
2. Структура: заголовки (# ##), **жирний** для термінів, *курсив* для прикладів.
3. ==підсвічування== для ключових визначень (EasyMDE підтримує).
4. Формули в $LaTeX$ (inline) або $$...$$ (block).
5. Таблиці для порівнянь, нумеровані списки для кроків, - для переліків.
6. Жодних зайвих смайликів, але можна ✓ × для тестів/перевірок.
7. Максимально чітко і корисно для школяра та вчителя.
8. Обсяг: {length}.

Поверни ТІЛЬКИ Markdown, без пояснень."""

_FORMAT_PROMPT = """Перетвори наступний сирий текст у чистий навчальний Markdown.

Правила:
- Зберігай весь зміст, але правильно структуруй заголовками (# ##)
- **Жирний** для термінів і визначень
- ==підсвічування== для найважливіших понять
- Формули → $LaTeX$
- Без зайвих смайликів
- Таблиці де є порівняння
- Виправ орфографічні помилки

Сирий текст:
{raw_text}

Поверни ТІЛЬКИ готовий Markdown."""

_OCR_TO_MD_PROMPT = """Текст розпізнано з рукописного фото.
Перетвори його у чистий навчальний Markdown:
- Виправ помилки OCR
- Додай структуру (заголовки, списки)
- Формули → $LaTeX$
- Зберігай весь зміст

Розпізнаний текст:
{ocr_text}

Поверни ТІЛЬКИ Markdown."""

MATERIAL_TYPES = [
    ('lecture',    'Конспект лекції'),
    ('summary',    'Шпаргалка / підсумок теми'),
    ('practice',   'Практичні вправи'),
    ('homework',   'Домашнє завдання'),
    ('test_prep',  'Підготовка до контрольної'),
    ('reference',  'Довідковий матеріал'),
    ('glossary',   'Глосарій термінів'),
]

LENGTHS = [
    ('short',  'Коротко (1–2 стор.)'),
    ('medium', 'Середньо (3–5 стор.)'),
    ('long',   'Розгорнуто (5–10 стор.)'),
]

LENGTH_HINTS = {
    'short':  'Орієнтовно 300–600 слів.',
    'medium': 'Орієнтовно 700–1500 слів.',
    'long':   'Орієнтовно 1500–3000 слів.',
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_material_or_404(material_id: int, check_owner: bool = True):
    db   = get_db()
    user = g.current_user
    mat  = db.execute(
        'SELECT m.*, u.full_name as teacher_name, c.name as class_name '
        'FROM materials m '
        'LEFT JOIN users u ON m.teacher_id = u.id '
        'LEFT JOIN classes c ON m.class_id = c.id '
        'WHERE m.id = ?',
        (material_id,)
    ).fetchone()
    if not mat:
        return None
    if check_owner and user['role'] not in ('admin', 'principal', 'vice_principal'):
        if mat['teacher_id'] != user['id']:
            return None
    return dict(mat)


# ── Routes ─────────────────────────────────────────────────────────────────────

@materials_bp.route('/')
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def list_materials():
    db   = get_db()
    user = g.current_user
    if user['role'] in ('admin', 'principal', 'vice_principal'):
        mats = db.execute(
            'SELECT m.*, u.full_name as teacher_name, c.name as class_name '
            'FROM materials m '
            'LEFT JOIN users u ON m.teacher_id = u.id '
            'LEFT JOIN classes c ON m.class_id = c.id '
            'ORDER BY m.updated_at DESC'
        ).fetchall()
    else:
        mats = db.execute(
            'SELECT m.*, u.full_name as teacher_name, c.name as class_name '
            'FROM materials m '
            'LEFT JOIN users u ON m.teacher_id = u.id '
            'LEFT JOIN classes c ON m.class_id = c.id '
            'WHERE m.teacher_id = ? '
            'ORDER BY m.updated_at DESC',
            (user['id'],)
        ).fetchall()
    return render_template('materials/list.html', materials=[dict(m) for m in mats])


@materials_bp.route('/create', methods=['GET', 'POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def create_material():
    db         = get_db()
    user       = g.current_user
    csrf_token = generate_csrf_token()
    classes    = [dict(r) for r in db.execute('SELECT * FROM classes ORDER BY name').fetchall()]
    subjects   = [dict(r) for r in db.execute('SELECT * FROM subjects ORDER BY name').fetchall()]

    if request.method == 'POST':
        if not validate_csrf(request.form.get('csrf_token', '')):
            flash('Невірний CSRF токен', 'danger')
            return redirect(url_for('materials.create_material'))

        title       = sanitize_input(request.form.get('title', ''), 200)
        subject     = sanitize_input(request.form.get('subject', ''), 100)
        class_level = sanitize_input(request.form.get('class_level', ''), 20)
        class_id    = request.form.get('class_id', None, type=int)
        content_md  = request.form.get('content_md', '')
        is_published= 1 if request.form.get('is_published') else 0

        if not title or not content_md.strip():
            flash("Вкажіть назву та вміст матеріалу", 'danger')
            return render_template('materials/create.html',
                                   csrf_token=csrf_token, classes=classes,
                                   subjects=subjects, material_types=MATERIAL_TYPES,
                                   lengths=LENGTHS)

        db.execute(
            'INSERT INTO materials (title, subject, class_level, class_id, content_md, '
            'teacher_id, is_published, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
            (title, subject, class_level, class_id, content_md,
             user['id'], is_published,
             datetime.utcnow().isoformat(), datetime.utcnow().isoformat())
        )
        db.commit()
        flash('Матеріал збережено ✓', 'success')
        return redirect(url_for('materials.list_materials'))

    return render_template('materials/create.html',
                           csrf_token=csrf_token, classes=classes,
                           subjects=subjects, material_types=MATERIAL_TYPES,
                           lengths=LENGTHS)


@materials_bp.route('/<int:material_id>')
@login_required
def view_material(material_id: int):
    user = g.current_user
    # Students/parents can view published materials
    check = user['role'] not in ('student', 'parent', 'class_president', 'school_president')
    mat = _get_material_or_404(material_id, check_owner=check)
    if not mat:
        flash('Матеріал не знайдено або доступ заборонено', 'danger')
        return redirect(url_for('materials.list_materials'))
    if user['role'] in ('student', 'parent') and not mat['is_published']:
        flash('Матеріал ще не опубліковано', 'warning')
        return redirect(url_for('dashboard.dashboard'))
    return render_template('materials/view.html', mat=mat)


@materials_bp.route('/<int:material_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def edit_material(material_id: int):
    db         = get_db()
    csrf_token = generate_csrf_token()
    mat        = _get_material_or_404(material_id)
    classes    = [dict(r) for r in db.execute('SELECT * FROM classes ORDER BY name').fetchall()]
    subjects   = [dict(r) for r in db.execute('SELECT * FROM subjects ORDER BY name').fetchall()]

    if not mat:
        flash('Матеріал не знайдено', 'danger')
        return redirect(url_for('materials.list_materials'))

    if request.method == 'POST':
        if not validate_csrf(request.form.get('csrf_token', '')):
            flash('Невірний CSRF токен', 'danger')
            return redirect(request.url)

        title       = sanitize_input(request.form.get('title', ''), 200)
        subject     = sanitize_input(request.form.get('subject', ''), 100)
        class_level = sanitize_input(request.form.get('class_level', ''), 20)
        class_id    = request.form.get('class_id', None, type=int)
        content_md  = request.form.get('content_md', '')
        is_published= 1 if request.form.get('is_published') else 0

        db.execute(
            'UPDATE materials SET title=?, subject=?, class_level=?, class_id=?, '
            'content_md=?, is_published=?, updated_at=? WHERE id=?',
            (title, subject, class_level, class_id, content_md,
             is_published, datetime.utcnow().isoformat(), material_id)
        )
        db.commit()
        flash('Зміни збережено ✓', 'success')
        return redirect(url_for('materials.view_material', material_id=material_id))

    return render_template('materials/create.html',
                           csrf_token=csrf_token, mat=mat,
                           classes=classes, subjects=subjects,
                           material_types=MATERIAL_TYPES, lengths=LENGTHS)


@materials_bp.route('/<int:material_id>/delete', methods=['POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def delete_material(material_id: int):
    db  = get_db()
    mat = _get_material_or_404(material_id)
    if not mat:
        flash('Матеріал не знайдено', 'danger')
        return redirect(url_for('materials.list_materials'))
    db.execute('DELETE FROM materials WHERE id=?', (material_id,))
    db.commit()
    flash('Матеріал видалено', 'success')
    return redirect(url_for('materials.list_materials'))


# ── API endpoints ──────────────────────────────────────────────────────────────

@materials_bp.route('/api/generate', methods=['POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def api_generate():
    """AI generates Markdown content from topic/type/class."""
    data        = request.get_json() or {}
    subject     = sanitize_input(data.get('subject', 'Загальна'), 100)
    class_level = sanitize_input(data.get('class_level', ''), 20)
    topic       = sanitize_input(data.get('topic', ''), 500)
    mat_type    = sanitize_input(data.get('material_type', 'lecture'), 30)
    length      = sanitize_input(data.get('length', 'medium'), 10)

    if not topic:
        return jsonify({'error': 'Вкажіть тему матеріалу'}), 400

    type_label = dict(MATERIAL_TYPES).get(mat_type, mat_type)
    length_hint = LENGTH_HINTS.get(length, '')

    prompt = _GENERATE_PROMPT.format(
        subject=subject,
        class_level=class_level or 'не вказано',
        topic=topic,
        material_type=type_label,
        length=length_hint
    )
    try:
        md = llm_generate(prompt, task='materials')
        # Strip any accidental code fences at top level
        md = re.sub(r'^```(?:markdown)?\s*\n', '', md, flags=re.IGNORECASE)
        md = re.sub(r'\n```\s*$', '', md)
        return jsonify({'success': True, 'content': md.strip()})
    except Exception as ex:
        logger.error(f'Materials generate error: {ex}')
        return jsonify({'error': str(ex)}), 500


@materials_bp.route('/api/format', methods=['POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def api_format():
    """AI formats raw pasted text into clean Markdown."""
    data     = request.get_json() or {}
    raw_text = data.get('text', '').strip()
    if not raw_text:
        return jsonify({'error': 'Вставте текст для форматування'}), 400
    if len(raw_text) > 30_000:
        return jsonify({'error': 'Текст занадто великий (максимум 30 000 символів)'}), 400

    prompt = _FORMAT_PROMPT.format(raw_text=raw_text[:20_000])
    try:
        md = llm_generate(prompt, task='materials')
        md = re.sub(r'^```(?:markdown)?\s*\n', '', md, flags=re.IGNORECASE)
        md = re.sub(r'\n```\s*$', '', md)
        return jsonify({'success': True, 'content': md.strip()})
    except Exception as ex:
        logger.error(f'Materials format error: {ex}')
        return jsonify({'error': str(ex)}), 500


@materials_bp.route('/api/ocr', methods=['POST'])
@login_required
@role_required('teacher', 'admin', 'principal', 'vice_principal')
def api_ocr():
    """OCR handwritten/printed photo → clean Markdown."""
    data      = request.get_json() or {}
    image_b64_full = data.get('image', '')  # data:image/jpeg;base64,...

    if not image_b64_full:
        return jsonify({'error': 'Немає зображення'}), 400

    # Strip data URL prefix
    if ',' in image_b64_full:
        image_b64 = image_b64_full.split(',', 1)[1]
    else:
        image_b64 = image_b64_full

    # Validate size (~5MB base64 → ~3.75MB binary)
    if len(image_b64) > 7_000_000:
        return jsonify({'error': 'Зображення занадто велике (максимум 5 МБ)'}), 400

    try:
        # Step 1: OCR → raw text
        raw_text = llm_ocr(image_b64, hint='Рукописний навчальний матеріал')

        if not raw_text.strip():
            return jsonify({'error': 'Не вдалося розпізнати текст на фото'}), 400

        # Step 2: Format raw OCR → clean Markdown
        prompt = _OCR_TO_MD_PROMPT.format(ocr_text=raw_text[:15_000])
        md = llm_generate(prompt, task='materials')
        md = re.sub(r'^```(?:markdown)?\s*\n', '', md, flags=re.IGNORECASE)
        md = re.sub(r'\n```\s*$', '', md)

        return jsonify({'success': True, 'content': md.strip(), 'raw_ocr': raw_text[:2000]})
    except Exception as ex:
        logger.error(f'Materials OCR error: {ex}')
        return jsonify({'error': str(ex)}), 500
