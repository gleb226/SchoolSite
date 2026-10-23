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
from app.security import sanitize_input, validate_csrf, generate_csrf_token
from app.auth import login_required, role_required
from app.llm import llm_generate, llm_ocr

logger = logging.getLogger(__name__)

materials_bp = Blueprint('materials', __name__)

# ── Prompt templates ───────────────────────────────────────────────────────────

_GENERATE_PROMPT = """Ти — провідний вчитель та методист українського ліцею.
Напиши ПОВНИЙ, САМОДОСТАТНІЙ НАВЧАЛЬНИЙ МАТЕРІАЛ у форматі Markdown для вчителя та учнів.

Предмет: {subject}
Клас: {class_level}
Тема: {topic}
Формат: {material_type}

КАТЕГОРИЧНІ ВИМОГИ (ПОРУШЕННЯ НЕ ДОПУСКАЄТЬСЯ):
1. ПОВНИЙ ТЕКСТ, А НЕ ШАБЛОН ЧИ ПЛАН УРОКУ!
   - КАТЕГОРИЧНО ЗАБОРОНЕНО створювати порожні каркаси типу:
     «I. Мета уроку
      II. Основні поняття
      III. Етапи уроку:
      IV. Домашнє завдання:»
     з двокрапками і пустими рядками під ними!
   - Це має бути розгорнута, глибока навчальна стаття/конспект, де кожен пункт ПОВНІСТЮ розписаний і пояснений (мінімум 3-5 абзаців на кожен розділ).

2. ЖОДНИХ ОБРИВІВ ТА ПУСТИХ ДВОКРАПОК:
   - Якщо ставиш двокрапку в кінці речення («Основні етапи:», «Розглянемо властивості:», «Висновки:», «пройшла кілька визначальних етапів:») — ти ЗОБОВ'ЯЗАНИЙ ОДРАЗУ наступним рядком надати повний текст (маркований список, розгорнуті пункти або пояснення).
   - Для літератури та історії («етапи світогляду», «періоди творчості»): ОБОВ'ЯЗКОВО розпиши кожен етап (назва, роки, ключові події, ідеї, твори)!
   - Заборонено залишати двокрапку без тексту або переходити після неї до наступного заголовка!

3. ПРАКТИЧНІ ЗАДАЧІ ТА ПРИКЛАДИ:
   - Для кожної наведеної задачі чи прикладу ОБОВ'ЯЗКОВО повністю розпиши:
     **Умова:** детальний текст задачі.
     **Розв'язання:** покрокові дії, розрахунки, математичні чи хімічні формули з коментарями.
     **Відповідь:** кінцевий числовий результат або висновок.
   - Категорично заборонено залишати рядок «Розв'язання:» без самого розв'язання!

4. ТАБЛИЦІ:
   - Якщо порівнюєш поняття чи процеси (наприклад, світлову та темнову фази), ОБОВ'ЯЗКОВО побудуй повну заповнену Markdown-таблицю (| Параметр | Фаза 1 | Фаза 2 |).

5. ФОРМУЛИ:
   - Усі хімічні та математичні формули пиши у форматі LaTeX: $inline$ або $$block$$.
   - Наприклад: $$6CO_2 + 6H_2O \\xrightarrow{{світло, хлорофіл}} C_6H_{{12}}O_6 + 6O_2$$.

6. ДОМАШНЄ ЗАВДАННЯ:
   - Якщо є розділ «Домашнє завдання» — надай 3 конкретні практичні вправи або запитання, а не порожній рядок.

Обсяг: {length}.
{image_instruction}

Поверни ВИКЛЮЧНО готовий Markdown-текст без вступних слів і привітань."""

_FORMAT_PROMPT = """Перетвори наступний сирий текст у чистий навчальний Markdown.

Правила:
- Зберігай весь зміст, але правильно структуруй заголовками (# ##)
- **Жирний** для термінів і визначень
- ==підсвічування== для найважливіших понять
- Формули → $LaTeX$
- Без зайвих смайликів
- Якщо в тексті є таблиця чи порівняння — сформуй чітку Markdown-таблицю (| ... | ... |)
- Виправ орфографічні помилки

Сирий текст:
{raw_text}

Поверни ТІЛЬКИ готовий Markdown."""

_OCR_TO_MD_PROMPT = """Текст розпізнано з рукописного конспекту або зошита.
Перетвори його у структурований, охайний навчальний Markdown:
- ЗБЕРЕЖИ АБСОЛЮТНО ВСІ ПУНКТИ ТА ПІДПУНКТИ:
  * Не викидай жодного підпункту, цифри, скорочення чи пояснення!
  * Збережи всі рівні ієрархії: Римські цифри (I, II, III...), підпункти (1, 2, 3...), дефіси, примітки у дужках (наприклад, скорочення 'ат.' розгорни у 'атома', 'ел.' у 'електронних', але зміст залиш повністю!).
  * Не узагальнюй і не стискай конспект — перенеси кожен рядок і підпункт.
- Хімічні формули та позначення (p+, e-, n0, вищі оксиди, заряди ядра, електронні конфігурації тощо) та математичні формули оформи коректно (за потреби у $LaTeX$).
- Виправ дрібні описки розпізнавання OCR.

Розпізнаний текст:
{ocr_text}

Поверни ТІЛЬКИ повний Markdown із усіма збереженими пунктами."""

MATERIAL_TYPES = [
    ('lesson_notes', 'Конспект уроку'),
    ('summary',      'Шпаргалка / підсумок теми'),
    ('practice',     'Практичні вправи та задачі'),
    ('homework',     'Домашнє завдання'),
    ('test_prep',    'Підготовка до самостійної / контрольної'),
    ('reference',    'Довідковий матеріал'),
    ('glossary',     'Глосарій термінів'),
]

LENGTHS = [
    ('short',  'Коротко (до 1–2 стор.)'),
    ('medium', 'Стандартний урок (2–3 стор.)'),
    ('long',   'Поглиблений матеріал (3–5 стор.)'),
]

LENGTH_HINTS = {
    'short':  'Орієнтовно 250–500 слів. Концентрована суть уроку.',
    'medium': 'Орієнтовно 600–1200 слів. Повноцінний конспект шкільного уроку з поясненнями та 2-3 прикладами.',
    'long':   'Орієнтовно 1200–2200 слів. Детальний конспект з теорією, розбором типових завдань та практикою.',
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
            'INSERT INTO materials (title, subject, class_level, class_id, content_md, image_urls, teacher_id, is_published, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (title, subject, class_level, class_id, content_md, request.form.get('material_images', ''),
             user['id'], is_published,
             datetime.utcnow().isoformat(), datetime.utcnow().isoformat())
        )
        db.commit()
        flash('Матеріл збережено', 'success')
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
            'content_md=?, image_urls=?, is_published=?, updated_at=? WHERE id=?',
            (title, subject, class_level, class_id, content_md,
             request.form.get('material_images', ''),
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
@role_required('teacher', 'class_teacher', 'psychologist', 'librarian', 'educator', 'admin', 'principal', 'vice_principal')
def api_generate():
    """AI generates Markdown content from topic/type/class with optional illustration."""
    data        = request.get_json() or {}
    subject     = sanitize_input(data.get('subject', 'Загальна'), 100)
    class_level = sanitize_input(data.get('class_level', ''), 20)
    topic       = sanitize_input(data.get('topic', ''), 500)
    mat_type    = sanitize_input(data.get('material_type', 'lecture'), 30)
    length      = sanitize_input(data.get('length', 'medium'), 10)
    generate_images = bool(data.get('generate_images', False))

    if not topic:
        return jsonify({'error': 'Вкажіть тему матеріалу'}), 400

    type_label = dict(MATERIAL_TYPES).get(mat_type, mat_type)
    length_hint = LENGTH_HINTS.get(length, '')

    if generate_images:
        image_instruction = (
            "Де це доречно (після ключового пояснення або визначення — "
            "орієнтовно після кожного другого розділу ##), вставляй рядок "
            "{{IMAGE}} як окремий абзац. Це місце де буде підставлено ілюстрацію. "
            "Не більше 3 таких місць."
        )
    else:
        image_instruction = ""

    prompt = _GENERATE_PROMPT.format(
        subject=subject,
        class_level=class_level or 'не вказано',
        topic=topic,
        material_type=type_label,
        length=length_hint,
        image_instruction=image_instruction,
    )
    try:
        md = llm_generate(prompt, task='materials')
        # Strip any accidental code fences at top level
        md = re.sub(r'^```(?:markdown)?\s*\n', '', md, flags=re.IGNORECASE)
        md = re.sub(r'\n```\s*$', '', md)
        md = md.strip()

        # ── Per-section empty fill-in ──────────────────────────────────────────
        def _detect_empty_sections(text: str) -> list[tuple[int, str, int]]:
            """
            Find lines that announce content but have nothing after them.
            Returns list of (line_index, title, blank_count).

            Two signals:
              • Line ends with ':' without immediate bullet list/content
              • Section-like line or problem without content
            """
            lines = text.splitlines()
            result = []
            i = 0
            while i < len(lines):
                s = lines[i].strip()
                if not s:
                    i += 1
                    continue
                # Count consecutive blank lines immediately after this line
                j = i + 1
                blank_count = 0
                while j < len(lines) and not lines[j].strip():
                    blank_count += 1
                    j += 1

                ends_with_colon = s.endswith(':')
                is_header = s.startswith('#') or bool(re.match(r'^\d+[\.\)]\s+\S', s))
                is_bold_title = bool(re.match(r'^\*{1,2}[^*]{2,}\*{0,2}:?\s*$', s))
                is_problem = bool(re.match(r'^Задача\s+\d+', s, re.IGNORECASE))
                is_section_like = is_header or is_bold_title or is_problem

                # Check if next non-empty line is a bullet item or next header/colon/end
                next_is_bullet = False
                next_is_header_or_end = False
                if j >= len(lines):
                    next_is_header_or_end = True
                else:
                    nj_s = lines[j].strip()
                    next_is_bullet = bool(re.match(r'^(?:[-*•+]|\d+[\.\)])\s+', nj_s))
                    next_is_header_or_end = (
                        nj_s.startswith('#')
                        or bool(re.match(r'^\d+[\.\)]\s+\S', nj_s))
                        or bool(re.match(r'^Задача\s+\d+', nj_s, re.IGNORECASE))
                        or (nj_s.endswith(':') and len(nj_s) < 100)
                    )

                # Build human-readable title
                title = re.sub(r'^#+\s*', '', s)
                title = re.sub(r'^\d+[\.\)]\s*', '', title)
                title = re.sub(r'\*+', '', title).rstrip(':').strip()

                if not title or len(title) < 2:
                    i += 1
                    continue

                # Empty colon intro (e.g. "...визначальних етапів:" or "Розв'язання:")
                is_empty_colon = ends_with_colon and not next_is_bullet and (next_is_header_or_end or blank_count >= 1)
                is_empty_section = is_section_like and (blank_count >= 2 or next_is_header_or_end)
                # Also catch colon-ending lines where next content is a new section (no actual list followed)
                if ends_with_colon and not next_is_bullet and not is_empty_colon:
                    # If the colon line is NOT a section header but looks like an intro phrase,
                    # and the very next non-empty line is another section header — mark it too
                    if j < len(lines):
                        nj_s2 = lines[j].strip()
                        if nj_s2.startswith('#') or bool(re.match(r'^Задача\s+\d+', nj_s2, re.IGNORECASE)):
                            is_empty_colon = True

                if is_empty_colon or is_empty_section:
                    result.append((i, title, s))

                i += 1
            return result

        empty_secs = _detect_empty_sections(md)
        if empty_secs:
            logger.info(
                f'Empty sections detected ({len(empty_secs)}): '
                f'{[t for _, t, _ in empty_secs]}'
            )

            # Fill each detected empty section cleanly and directly (up to 10)
            lines = md.splitlines()
            for line_idx, sec_title, full_line in reversed(empty_secs[:10]):
                ctx_start = max(0, line_idx - 5)
                context_lines = '\n'.join(lines[ctx_start:line_idx + 1]).strip()
                sec_lower = sec_title.lower()
                is_sol = any(k in sec_lower for k in ('розв', 'рішенн', 'відповід', 'задач', 'приклад'))
                is_stages = any(k in sec_lower for k in ('етап', 'період', 'фаз', 'процес', 'ознак', 'властив', 'пункт', 'значенн', 'мета', 'понять', 'виснов'))

                if is_sol:
                    fill_q = (
                        f"Предмет: {subject}. Тема: «{topic}».\n"
                        f"Ось умова завдання/задачі:\n{context_lines}\n\n"
                        f"Напиши ПОВНЕ, покрокове розв'язання цієї задачі з поясненням, формулами та кінцевою відповіддю. "
                        f"Без вступних слів, одразу зміст розв'язання."
                    )
                elif is_stages:
                    fill_q = (
                        f"Предмет: {subject}. Тема: «{topic}».\n"
                        f"Контекст уроку/теми:\n{context_lines}\n\n"
                        f"Напиши розгорнутий зміст (маркований список з 3–5 пунктів або повноцінні абзаци з описом) "
                        f"для: «{full_line}».\n"
                        f"Опиши детально кожен етап, період або властивість з фактами та сутністю. "
                        f"Без вступних фраз від штучного інтелекту, одразу конкретний зміст пунктів."
                    )
                else:
                    fill_q = (
                        f"Предмет: {subject}. Тема: «{topic}».\n"
                        f"Контекст:\n{context_lines}\n\n"
                        f"Напиши 3–5 змістовних речень або маркований список конкретного навчального змісту для «{sec_title}». "
                        f"Без вступних фраз від AI, тільки сам навчальний зміст."
                    )

                try:
                    content = llm_generate(fill_q, task='materials').strip()
                    content = re.sub(r'^```[a-z]*\s*', '', content, flags=re.IGNORECASE)
                    content = re.sub(r'\s*```$', '', content).strip()
                    content = re.sub(
                        r'^(?:#+\s*)?(?:Розв\'язання|Відповідь|' + re.escape(sec_title) + r')[:\s]*\n?',
                        '',
                        content,
                        flags=re.IGNORECASE
                    ).strip()
                    if content:
                        lines.insert(line_idx + 1, content)
                        logger.info(f"Filled '{sec_title}' ({len(content)} chars)")
                except Exception as fe:
                    logger.warning(f"Fill '{sec_title}' failed: {fe}")

            md = '\n'.join(lines)
        # ── End per-section fill-in ────────────────────────────────────────────

        # Collapse excess empty lines (e.g. 4-10 consecutive blanks -> 2)
        md = re.sub(r'\n{3,}', '\n\n', md).strip()

        img_url = None

        if generate_images:
            try:
                from app.ai import generate_material_illustration

                # If AI didn't place any {{IMAGE}} placeholders, inject them
                # automatically after every 2nd ## heading (max 3 total)
                if '{{IMAGE}}' not in md:
                    heading_positions = [m.start() for m in re.finditer(r'\n## ', md)]
                    insert_after = heading_positions[1::2][:3]
                    offset = 0
                    for pos in insert_after:
                        line_end = md.find('\n', pos + offset + 1)
                        if line_end == -1:
                            line_end = len(md)
                        insert_pos = line_end + offset
                        md = md[:insert_pos] + '\n\n{{IMAGE}}' + md[insert_pos:]
                        offset += len('\n\n{{IMAGE}}')

                # Replace each {{IMAGE}} with a real fetched image URL (max 3)
                replaced_count = 0

                def _replace_img(match):
                    nonlocal replaced_count, img_url
                    if replaced_count >= 3:
                        return ''
                    try:
                        url = generate_material_illustration(subject, topic)
                    except Exception:
                        url = None
                    if url:
                        replaced_count += 1
                        if img_url is None:
                            img_url = url
                        return f'\n![Ілюстрація: {topic}]({url})\n'
                    return ''

                md = re.sub(r'\{\{IMAGE\}\}', _replace_img, md)

            except Exception as img_err:
                logger.warning(f"Material illustration skipped: {img_err}")
                md = re.sub(r'\{\{IMAGE\}\}', '', md)

        return jsonify({'success': True, 'content': md.strip(), 'image_url': img_url})
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
