import os
import json
import logging
import time
import urllib.request
import urllib.error
import urllib.parse
import socket
import re
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependencies are installed by start.py
    load_dotenv = None

if load_dotenv:
    load_dotenv()

logger = logging.getLogger(__name__)

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash").strip() or "gemini-3.6-flash"
GEMINI_FALLBACK_MODELS = [
    model.strip()
    for model in os.environ.get("GEMINI_FALLBACK_MODELS", "gemini-3.7-flash,gemini-3.8-flash,gemini-flash-latest").split(",")
    if model.strip()
]
GEMINI_TIMEOUT_SECONDS = int(os.environ.get("GEMINI_TIMEOUT_SECONDS", "90"))
GEMINI_RETRIES = int(os.environ.get("GEMINI_RETRIES", "2"))
GEMINI_BATCH_SIZE = max(1, min(50, int(os.environ.get("GEMINI_BATCH_SIZE", "20"))))

CURRICULUM_TOPICS = {
    "Математика": [
        "Квадратні рівняння та теорема Вієта",
        "Лінійні рівняння та системи рівнянь",
        "Дробово-раціональні вирази",
        "Функції: лінійна, квадратична, їх графіки",
        "Арифметична та геометрична прогресії",
        "Тригонометричні функції та тотожності",
        "Похідна функції та її застосування",
        "Теорема Піфагора та синусів/косинусів",
        "Площі та периметри геометричних фігур",
        "Вектори на площині та у просторі"
    ],
    "Українська мова": [
        "Правопис префіксів та суфіксів",
        "Ненаголошені голосні е, и, о",
        "Складне речення: складносурядне, складнопідрядне",
        "Відокремлені члени речення",
        "Пряма мова та діалог",
        "Вживання м'якого знака та апострофа",
        "Спрощення в групах приголосних",
        "Частини мови та морфологічний розбір"
    ],
    "Історія України": [
        "Київська Русь за перших князів",
        "Козацька доба та Богдан Хмельницький",
        "Українська революція 1917–1921 років",
        "Україна в роки Другої світової війни",
        "Відновлення незалежності України у 1991 році",
        "Сучасна історія та боротьба за суверенітет"
    ],
    "Фізика": [
        "Закони динаміки Ньютона",
        "Закон збереження енергії та імпульсу",
        "Закон Ома та послідовне/паралельне з'єднання",
        "Теплові явища та питома теплоємність",
        "Оптика: заломлення та лінзи",
        "Будова атома та радіоактивність"
    ],
    "Англійська мова": [
        "Present Simple vs Present Continuous",
        "Past Simple and Past Continuous",
        "Present Perfect and Past Simple",
        "Future Tenses (will, going to)",
        "Conditionals (Type 0, 1, 2)",
        "Passive Voice in English",
        "Modal Verbs (must, should, can, have to)"
    ],
    "Інформатика": [
        "Основи Python: змінні, розгалуження, типи даних",
        "Списки, словники та цикли в Python",
        "Бази даних та базові SQL-запити",
        "Основи HTML та CSS для створення сайтів",
        "Алгоритми пошуку та сортування",
        "Кібербезпека та безпека в інтернеті"
    ],
    "Біологія": [
        "Будова клітини та органели",
        "Фотосинтез та клітинне дихання",
        "Основи генетики: закони Менделя",
        "Кровоносна та дихальна системи людини",
        "Екосистеми та колообіг речовин"
    ],
    "Хімія": [
        "Періодичний закон та електронна будова атома",
        "Класи неорганічних сполук: оксиди, кислоти, основи, солі",
        "Розчини та розрахунок масової частки",
        "Окисно-відновні реакції",
        "Вуглеводні: алкани, алкени, алкіни"
    ]
}


def get_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        key = os.environ.get("GOOGLE_API_KEY", "").strip()
    return key


def _validate_api_key(key: str) -> None:
    """Raises ValueError with a clear message if the key looks invalid."""
    if not key:
        raise ValueError(
            "GEMINI_API_KEY не налаштовано. "
            "Додайте API ключ до .env файлу: GEMINI_API_KEY=ваш_ключ"
        )


def _extract_gemini_text(result: dict[str, Any]) -> str:
    candidates = result.get("candidates", [])
    for candidate in candidates:
        finish_reason = candidate.get("finishReason", "")
        parts = candidate.get("content", {}).get("parts", [])
        text = "".join(str(part.get("text", "")) for part in parts).strip()
        if text:
            if finish_reason == "MAX_TOKENS":
                # Response was cut off mid-generation — signal this so clean_json_response
                # can attempt repair rather than raising a cryptic JSONDecodeError.
                logger.warning(
                    f"Gemini response truncated (MAX_TOKENS). Raw length={len(text)}. "
                    "Will attempt JSON repair."
                )
                raise RuntimeError(
                    "__TRUNCATED__:" + text
                )
            return text
    prompt_feedback = result.get("promptFeedback") or {}
    finish_reason = candidates[0].get("finishReason") if candidates else ""
    raise RuntimeError(f"Gemini API returned no text. finish_reason={finish_reason}, feedback={prompt_feedback}")


def call_gemini(prompt: str, json_mode: bool = True) -> str:
    from app.llm import llm_generate
    try:
        # ai.py handles json repair on its own in clean_json_response, so we just return the string
        return llm_generate(prompt, task='test_generation' if json_mode else 'default', json_mode=json_mode)
    except Exception as e:
        raise RuntimeError(str(e))


def clean_json_response(raw_text: str) -> dict:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    # Fast path — valid JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try trimming to the last complete {...} block first
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    # Attempt to repair truncated JSON by closing open brackets/braces.
    # This handles MAX_TOKENS cut-offs where the response ends mid-structure.
    if start != -1:
        fragment = text[start:]
        repaired = _repair_truncated_json(fragment)
        if repaired is not None:
            logger.warning(
                f"Repaired truncated JSON. Original length={len(text)}, "
                f"repaired questions count={len(repaired.get('questions', []))}"
            )
            return repaired

    # Nothing worked — re-raise with context for easier debugging
    logger.error(f"clean_json_response failed. First 200 chars: {text[:200]!r}")
    raise json.JSONDecodeError("Cannot parse or repair Gemini JSON response", text, 0)


def _repair_truncated_json(fragment: str) -> dict | None:
    """
    Best-effort repair of JSON truncated mid-stream (e.g. MAX_TOKENS).
    Removes the incomplete last item and closes any open arrays/objects.
    Returns a dict on success, None if repair is impossible.
    """
    # Walk the string tracking open bracket depth; find the last *complete* value
    # by peeling from the end until we get a parseable string.
    # Strategy: truncate at the last comma that's at the top-level questions array,
    # then close all open structures.
    depth = 0
    in_string = False
    escape = False
    last_safe_comma_pos = -1  # position of last comma at depth==2 (inside questions array)

    for i, ch in enumerate(fragment):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in ('{', '['):
            depth += 1
        elif ch in ('}', ']'):
            depth -= 1
        elif ch == ',' and depth == 2:
            # depth==2 means we're inside the top-level object > questions array
            last_safe_comma_pos = i

    if last_safe_comma_pos == -1:
        return None  # can't find a safe truncation point

    truncated = fragment[:last_safe_comma_pos]

    # Count unclosed brackets/braces
    depth2 = 0
    in_string2 = False
    escape2 = False
    stack = []
    for ch in truncated:
        if escape2:
            escape2 = False
            continue
        if ch == '\\' and in_string2:
            escape2 = True
            continue
        if ch == '"' and not escape2:
            in_string2 = not in_string2
            continue
        if in_string2:
            continue
        if ch in ('{', '['):
            stack.append(ch)
        elif ch == '}':
            if stack and stack[-1] == '{':
                stack.pop()
        elif ch == ']':
            if stack and stack[-1] == '[':
                stack.pop()

    # Close open structures in reverse order
    closing = ""
    for ch in reversed(stack):
        closing += '}' if ch == '{' else ']'

    candidate = truncated + closing
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _coerce_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def normalize_generated_test(payload: dict, topic: str, question_count: int, practice: bool = False) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Gemini returned invalid JSON structure.")

    count = _coerce_int(question_count, 5, 1, 200)
    title_prefix = "Тренувальний тест" if practice else "Контрольна робота"
    title = str(payload.get("title") or f"{title_prefix}: {topic}").strip()[:200]
    description = str(payload.get("description") or f"Тестові завдання з теми: {topic}").strip()[:500]
    time_limit = _coerce_int(payload.get("time_limit_minutes"), max(10, count * (2 if practice else 3)), 1, 9999)

    normalized_questions = []
    for raw_q in payload.get("questions", []):
        if not isinstance(raw_q, dict):
            continue

        question_text = str(raw_q.get("question_text") or "").strip()
        if not question_text:
            continue

        raw_options = raw_q.get("options", [])
        if not isinstance(raw_options, list):
            raw_options = []

        raw_type = str(raw_q.get("question_type") or "single").strip().lower()
        q_type = raw_type if raw_type in ("single", "multiple", "text") else "single"
        options = []
        correct_count = 0
        for raw_opt in raw_options[:4]:
            if not isinstance(raw_opt, dict):
                continue
            opt_text = str(raw_opt.get("text") or raw_opt.get("option_text") or "").strip()
            if not opt_text:
                continue
            is_correct = bool(raw_opt.get("is_correct"))
            if is_correct:
                correct_count += 1
            options.append({"text": opt_text[:300], "is_correct": is_correct})

        if q_type == "text":
            options = []
        else:
            if len(options) < 2:
                continue
            if correct_count == 0:
                options[0]["is_correct"] = True
                correct_count = 1
            if q_type == "single" and correct_count > 1:
                first_correct = False
                for opt in options:
                    if opt["is_correct"] and not first_correct:
                        first_correct = True
                    elif opt["is_correct"]:
                        opt["is_correct"] = False

        normalized_questions.append({
            "question_text": question_text[:500],
            "question_type": q_type,
            "points": _coerce_int(raw_q.get("points"), 1, 1, 12),
            "options": options,
            "explanation": str(raw_q.get("explanation") or "").strip()[:1000],
        })

        if len(normalized_questions) >= count:
            break

    if not normalized_questions:
        raise ValueError("Gemini did not return usable questions.")

    return {
        "title": title,
        "description": description,
        "topic": topic,
        "time_limit_minutes": time_limit,
        "questions": normalized_questions,
    }


def _generate_test_ai_once(subject: str, topic: str, class_level: str = "", question_count: int = 5,
                           difficulty: str = "середній", question_mode: str = "auto",
                           batch_note: str = "") -> dict:
    """Generates a teacher test with selectable question mix."""
    count = max(1, min(200, int(question_count)))
    class_str = f"для {class_level} класу" if class_level else "для ліцею"
    mode_map = {
        "tests_only": "тільки single та multiple, без текстових відповідей",
        "mixed": "змішай single, multiple і text",
        "nmt": "НМТ-рівень: переважно single/multiple, але додай складніші текстові задачі з короткою відповіддю",
        "auto": "обери типи за складністю: базовий - single; середній - single і multiple; поглиблений/НМТ - single, multiple і text",
    }
    mode_text = mode_map.get(question_mode, mode_map["auto"])
    
    prompt = f"""Ти — висококваліфікований вчитель українського ліцею.
Склади навчальний тестовий контроль з предмету "{subject}", тема: "{topic}", {class_str}.
Рівень складності: {difficulty}.
Кількість питань: {count}.
Режим типів завдань: {mode_text}.
{batch_note}

Вимоги:
1. Питання мають відповідати чинній навчальній програмі МОН України.
2. Формулювання чіткі, українською мовою.
3. Використовуй типи question_type:
   - "single": 4 варіанти, рівно один правильний.
   - "multiple": 4 варіанти, два або більше правильних.
   - "text": задача або відкрите питання без options; правильну відповідь/хід розв'язання опиши в explanation.
4. Для складності "НМТ" або "поглиблений" додай комплексні задачі, роботу з твердженнями, відповідність або коротку числову/текстову відповідь.
5. Обов'язково вкажи коротке педагогічне пояснення (explanation).

Відповідь поверни ВИКЛЮЧНО у валідному JSON наступної структури:
{{
  "title": "Назва тесту (наприклад: Контрольна робота: {topic})",
  "description": "Короткий опис цілей тестування та теми",
  "time_limit_minutes": {max(10, count * 3)},
  "questions": [
    {{
      "question_text": "Текст запитання",
      "question_type": "single",
      "points": 1,
      "options": [
        {{"text": "Варіант А", "is_correct": true}},
        {{"text": "Варіант Б", "is_correct": false}},
        {{"text": "Варіант В", "is_correct": false}},
        {{"text": "Варіант Г", "is_correct": false}}
      ],
      "explanation": "Чітке пояснення правила або формули"
    }},
    {{
      "question_text": "Питання з кількома правильними відповідями",
      "question_type": "multiple",
      "points": 2,
      "options": [
        {{"text": "Варіант А", "is_correct": true}},
        {{"text": "Варіант Б", "is_correct": false}},
        {{"text": "Варіант В", "is_correct": true}},
        {{"text": "Варіант Г", "is_correct": false}}
      ],
      "explanation": "Пояснення"
    }},
    {{
      "question_text": "Текстове завдання або задача з короткою відповіддю",
      "question_type": "text",
      "points": 3,
      "options": [],
      "explanation": "Еталонна відповідь і короткий розв'язок"
    }}
  ]
}}"""

    raw_output = call_gemini(prompt, json_mode=True)
    return normalize_generated_test(clean_json_response(raw_output), topic, count, practice=False)


def generate_test_ai(subject: str, topic: str, class_level: str = "", question_count: int = 5,
                     difficulty: str = "середній", question_mode: str = "auto") -> dict:
    """Generates exactly the requested number of questions, batching large requests."""
    target_count = _coerce_int(question_count, 5, 1, 200)
    all_questions = []
    seen_questions = set()
    generated = None
    attempts = 0
    max_attempts = (target_count + GEMINI_BATCH_SIZE - 1) // GEMINI_BATCH_SIZE + 4

    while len(all_questions) < target_count and attempts < max_attempts:
        attempts += 1
        remaining = target_count - len(all_questions)
        batch_count = min(GEMINI_BATCH_SIZE, remaining)
        batch_note = (
            f"Це частина {attempts} великого тесту. Згенеруй рівно {batch_count} нових питань. "
            "Не повторюй попередні формулювання."
        )
        if attempts > 1:
            time.sleep(4)  # pause between batches to avoid rate limiting
        generated = _generate_test_ai_once(
            subject, topic, class_level, batch_count, difficulty, question_mode, batch_note
        )

        for question in generated["questions"]:
            key = question["question_text"].strip().lower()
            if key in seen_questions:
                continue
            seen_questions.add(key)
            all_questions.append(question)
            if len(all_questions) >= target_count:
                break

    if not generated:
        raise RuntimeError("Gemini did not return a generated test.")

    if len(all_questions) < target_count:
        raise RuntimeError(
            f"Gemini generated only {len(all_questions)} usable questions out of {target_count}. "
            "Спробуйте меншу кількість або повторіть генерацію."
        )

    generated["questions"] = all_questions[:target_count]
    generated["time_limit_minutes"] = max(10, target_count * 3)
    return generated


def generate_practice_test_ai(subject: str, topic: str, question_count: int = 5) -> dict:
    """Generates an interactive training/practice test for students with mixed question types."""
    count = max(1, min(50, int(question_count)))

    prompt = f"""Ти — турботливий репетитор та вчитель.
Склади самостійний ТРЕНУВАЛЬНИЙ тест для учня з предмету "{subject}", тема: "{topic}".
Кількість завдань: {count}.

Мета тесту: допомогти учню потренуватися, закріпити знання з теми та зрозуміти типові помилки.
Використовуй РІЗНІ типи завдань: "single" (4 варіанти, один правильний), "multiple" (4 варіанти, 2+ правильних), "text" (відкрите питання без варіантів).
Приблизно 60% single, 30% multiple, 10% text — але адаптуй до змісту теми.
До кожного питання обов'язково надай детальне пояснення правильної відповіді.

Поверни результат ВИКЛЮЧНО у форматі JSON:
{{
  "title": "Тренувальний тест: {topic}",
  "description": "Практичні завдання для самоперевірки та закріплення знань",
  "topic": "{topic}",
  "time_limit_minutes": {max(10, count * 2)},
  "questions": [
    {{
      "question_text": "Текст завдання з одним правильним варіантом",
      "question_type": "single",
      "points": 1,
      "options": [
        {{"text": "Варіант 1", "is_correct": true}},
        {{"text": "Варіант 2", "is_correct": false}},
        {{"text": "Варіант 3", "is_correct": false}},
        {{"text": "Варіант 4", "is_correct": false}}
      ],
      "explanation": "Пояснення: розв'язок або правило, яке пояснює правильну відповідь."
    }},
    {{
      "question_text": "Питання з кількома правильними відповідями",
      "question_type": "multiple",
      "points": 2,
      "options": [
        {{"text": "Варіант А", "is_correct": true}},
        {{"text": "Варіант Б", "is_correct": false}},
        {{"text": "Варіант В", "is_correct": true}},
        {{"text": "Варіант Г", "is_correct": false}}
      ],
      "explanation": "Пояснення до питання з кількома відповідями."
    }},
    {{
      "question_text": "Відкрите завдання: розв'яжи або дай коротку відповідь",
      "question_type": "text",
      "points": 2,
      "options": [],
      "explanation": "Еталонна відповідь і короткий розв'язок."
    }}
  ]
}}"""

    raw_output = call_gemini(prompt, json_mode=True)
    return normalize_generated_test(clean_json_response(raw_output), topic, count, practice=True)



def grade_text_answer(question_text: str, correct_answer: str, student_answer: str, max_points: int) -> dict:
    """Ask AI to grade a student's text answer. Returns score, comment, confidence."""
    if not student_answer or not student_answer.strip():
        return {"score": 0, "max_points": max_points, "comment": "Відповідь не надана.", "confidence": "high"}

    prompt = f"""Ти — досвідчений шкільний вчитель. Перевір відповідь учня на завдання.

Завдання: {question_text}
Еталонна відповідь / розв'язок: {correct_answer or '(не вказано, оцінюй за суттю)'}
Відповідь учня: {student_answer}
Максимальний бал: {max_points}

Оціни відповідь учня за шкалою від 0 до {max_points}.
Враховуй: правильність суті, повноту, допустимі помилки в оформленні.

Поверни ТІЛЬКИ JSON:
{{"score": <число від 0 до {max_points}>, "comment": "<коротке пояснення до 120 символів>", "confidence": "<high|medium|low>"}}"""

    try:
        raw = call_gemini(prompt, json_mode=True)
        data = clean_json_response(raw)
        score = _coerce_int(data.get("score"), 0, 0, max_points)
        comment = str(data.get("comment") or "").strip()[:200]
        confidence = str(data.get("confidence") or "medium").strip().lower()
        if confidence not in ("high", "medium", "low"):
            confidence = "medium"
        return {"score": score, "max_points": max_points, "comment": comment, "confidence": confidence}
    except Exception as e:
        logger.warning(f"AI grading failed: {e}")
        return {"score": 0, "max_points": max_points,
                "comment": f"AI не зміг перевірити автоматично: {str(e)[:100]}", "confidence": "low"}


# ══════════════════════════════════════════════════════════════════════════════
# IMAGE GENERATION PIPELINE FOR QUESTIONS
#
# Flow: 1. Classify question (needs image?)
#        2. Google Custom Search → find real photo/diagram
#        3. Validate image (accessible + relevant)
#        4. If no good image found → Gemini generates SVG code (for graphs,
#           geometry, charts) or returns None (no image needed)
# ══════════════════════════════════════════════════════════════════════════════

GOOGLE_CSE_KEY = os.environ.get("GOOGLE_CSE_KEY", "").strip()
GOOGLE_CSE_CX  = os.environ.get("GOOGLE_CSE_CX", "").strip()

# Topics/keywords that benefit from visual aids
_VISUAL_KEYWORDS = (
    # Geometry
    'трикутник', 'коло', 'квадрат', 'прямокутник', 'паралелограм', 'ромб',
    'трапеція', 'куб', 'циліндр', 'конус', 'куля', 'піраміда', 'призма',
    'геометр', 'фігур', 'кут', 'вектор', 'координат',
    # Graphs / functions
    'графік', 'функці', 'парабол', 'гіпербол', 'синус', 'косинус',
    'показников', 'логарифм', 'похідн', 'інтеграл',
    # Physics
    'схем', 'ланцюг', 'коливан', 'хвил', 'оптик', 'лінз', 'призм',
    'сила', 'вектор', 'траєктор',
    # Chemistry
    'молекул', 'структур', 'формул', 'реакц', 'таблиц менделєєв',
    # Biology / geography
    'клітин', 'орган', 'карт', 'діаграм', 'графік',
)

# Question types that almost never need images
_NO_IMAGE_TYPES = ('правопис', 'граматик', 'орфограф', 'відмінювання',
                   'синонім', 'антонім', 'наголос')


def _question_needs_image(question_text: str, subject: str) -> bool:
    """Heuristic: does this question benefit from a visual?"""
    qt = question_text.lower()
    # Explicit negative signals
    for kw in _NO_IMAGE_TYPES:
        if kw in qt:
            return False
    # Explicit positive signals
    for kw in _VISUAL_KEYWORDS:
        if kw in qt:
            return True
    # Subject-level default
    return subject.lower() in ('математика', 'фізика', 'хімія', 'географія', 'біологія')


def _google_image_search(query: str, num: int = 3) -> list[str]:
    """
    Search Google Custom Search API for image URLs.
    Returns list of image URLs (may be empty if API not configured).
    """
    if not GOOGLE_CSE_KEY or not GOOGLE_CSE_CX:
        return []
    try:
        params = urllib.parse.urlencode({
            'key': GOOGLE_CSE_KEY,
            'cx': GOOGLE_CSE_CX,
            'q': query,
            'searchType': 'image',
            'num': num,
            'safe': 'active',
            'imgSize': 'medium',
            'imgType': 'clipart',   # prefer clean educational diagrams
        })
        url = f'https://www.googleapis.com/customsearch/v1?{params}'
        req = urllib.request.Request(url, headers={'User-Agent': 'SchoolSite/1.0'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        return [item['link'] for item in data.get('items', [])]
    except Exception as ex:
        logger.debug(f'Google CSE search failed: {ex}')
        return []


def _validate_image_url(url: str) -> bool:
    """Quick HEAD check — is the image reachable and reasonably sized?"""
    try:
        req = urllib.request.Request(url, method='HEAD',
                                     headers={'User-Agent': 'SchoolSite/1.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            ct = resp.headers.get('Content-Type', '')
            cl = int(resp.headers.get('Content-Length', '0') or '0')
            return ct.startswith('image/') and (cl == 0 or cl < 5_000_000)
    except Exception:
        return False


# ── SVG generation via Gemini ──────────────────────────────────────────────

_SVG_PROMPT = """Ти — генератор навчальних SVG-зображень для шкільних тестів.
Для наступного питання згенеруй компактне SVG-зображення 400x300 пікселів.

Питання: {question}
Предмет: {subject}

Правила:
1. Поверни ТІЛЬКИ валідний SVG-код, починаючи з <svg і закінчуючи </svg>.
2. Без пояснень, без markdown, без ```svg.
3. viewBox="0 0 400 300", xmlns="http://www.w3.org/2000/svg".
4. Чистий білий фон (#fff або white).
5. Чіткі лінії, підписи латиницею/цифрами (шрифт Arial або sans-serif, розмір 12-14px).
6. Для графіків функцій: намалюй систему координат, вісь X та Y, підписи, та криву/пряму.
7. Для геометрії: намалюй фігуру з підписами сторін і кутів.
8. Для фізики: намалюй схему або діаграму.
9. Якщо питання — чисто текстове (без діаграм) — поверни порожній рядок "".

Відповідь (тільки SVG або порожній рядок):"""


def _generate_svg_for_question(question_text: str, subject: str) -> str | None:
    """
    Ask Gemini to generate an SVG diagram for a question.
    Returns SVG string (starting with <svg) or None.
    """
    prompt = _SVG_PROMPT.format(question=question_text[:300], subject=subject)
    try:
        raw = call_gemini(prompt, json_mode=False)
        if not raw or not raw.strip():
            return None
        # Extract SVG block
        svg_match = re.search(r'<svg[\s\S]*?</svg>', raw, re.IGNORECASE)
        if svg_match:
            svg = svg_match.group(0)
            # Basic safety: remove script tags
            svg = re.sub(r'<script[\s\S]*?</script>', '', svg, flags=re.IGNORECASE)
            svg = re.sub(r'\son\w+\s*=\s*["\'][^"\']*["\']', '', svg)
            if len(svg) > 200:  # sanity check
                return svg
        return None
    except Exception as ex:
        logger.debug(f'SVG generation failed: {ex}')
        return None


def _svg_to_data_url(svg: str) -> str:
    """Convert SVG string to a data: URL for use in <img src="">."""
    import base64
    encoded = base64.b64encode(svg.encode('utf-8')).decode('ascii')
    return f'data:image/svg+xml;base64,{encoded}'


# ── Main public function ────────────────────────────────────────────────────

def generate_question_image(question_text: str, subject: str,
                             search_query: str | None = None) -> str | None:
    """
    Full pipeline: search → validate → SVG fallback.

    Returns:
        str  — image URL (https://… or data:image/svg+xml;base64,…)
        None — no image appropriate for this question
    """
    if not _question_needs_image(question_text, subject):
        return None

    # 1. Try Google Image Search
    query = search_query or f'{subject} {question_text[:80]} схема навчальний'
    urls = _google_image_search(query)
    for url in urls:
        if _validate_image_url(url):
            logger.info(f'Found image via Google CSE: {url[:60]}')
            return url

    # 2. Fallback — generate SVG with Gemini
    logger.info(f'No image found via Google, generating SVG for: {question_text[:60]}')
    svg = _generate_svg_for_question(question_text, subject)
    if svg:
        return _svg_to_data_url(svg)

    return None


def enrich_questions_with_images(questions: list[dict], subject: str,
                                  max_images: int = 5) -> list[dict]:
    """
    Add image_url to questions that benefit from visual aids.
    Processes up to max_images questions to avoid slow generation.
    """
    import urllib.parse  # ensure imported in scope
    enriched = 0
    for q in questions:
        if enriched >= max_images:
            break
        if q.get('image_url'):  # already has image
            continue
        img = generate_question_image(q.get('question_text', ''), subject)
        if img:
            q['image_url'] = img
            enriched += 1
    return questions
