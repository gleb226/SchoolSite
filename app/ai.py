import os
import json
import logging
import time
import urllib.request
import urllib.error
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
    key = get_api_key()
    _validate_api_key(key)

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3
        }
    }
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"

    data = json.dumps(payload).encode("utf-8")

    # Build model list: configured primary → configured fallbacks → hardcoded safety net
    # The safety net ensures we always have options even if .env is misconfigured
    SAFETY_NET_MODELS = [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    ]
    models = [GEMINI_MODEL]
    for m in list(GEMINI_FALLBACK_MODELS) + SAFETY_NET_MODELS:
        if m not in models:
            models.append(m)

    last_error = ""
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            for attempt in range(GEMINI_RETRIES + 1):
                try:
                    with urllib.request.urlopen(req, timeout=GEMINI_TIMEOUT_SECONDS) as resp:
                        result = json.loads(resp.read().decode("utf-8"))
                        try:
                            return _extract_gemini_text(result)
                        except RuntimeError as ex:
                            # MAX_TOKENS truncation: pass the raw fragment back so
                            # clean_json_response can attempt repair.
                            if str(ex).startswith("__TRUNCATED__:"):
                                return str(ex)[len("__TRUNCATED__:"):]
                            raise
                except (TimeoutError, socket.timeout) as ex:
                    last_error = str(ex)
                    logger.warning(f"Gemini model {model} timeout attempt {attempt + 1}: {ex}")
                    if attempt < GEMINI_RETRIES:
                        time.sleep(2 ** attempt)
                        continue
                    raise RuntimeError(
                        "Gemini не відповідає (timeout). Спробуйте ще раз або зменшіть кількість питань."
                    )
                except urllib.error.HTTPError as ex:
                    # HTTPError must be before URLError (it's a subclass)
                    err_body = ex.read().decode("utf-8", errors="ignore")
                    last_error = f"HTTP {ex.code}: {err_body[:300]}"
                    logger.warning(f"Gemini model {model} HTTP {ex.code} attempt {attempt + 1}: {err_body[:120]}")
                    if ex.code == 429:
                        retry_after = int(ex.headers.get("Retry-After", "0") or "0")
                        wait = retry_after if retry_after > 0 else 15
                        logger.info(f"Gemini 429 on {model}, attempt {attempt+1}, waiting {wait}s...")
                        if attempt < GEMINI_RETRIES:
                            time.sleep(min(wait, 30))
                            continue
                        # exhausted retries on this model — try next
                        last_error = "429 quota exceeded"
                        break
                    elif ex.code in (500, 502, 503):
                        last_error = f"Service error {ex.code}"
                        if ex.code == 503:
                            time.sleep(5)  # brief pause before trying next model
                        break  # try next model
                    elif ex.code == 404:
                        last_error = f"Model not found 404"
                        break  # try next model
                    elif ex.code in (401, 403):
                        raise RuntimeError(
                            "Невірний або прострочений API ключ. "
                            "Перевірте значення GEMINI_API_KEY у файлі .env."
                        )
                    else:
                        raise RuntimeError(f"Помилка Gemini API (HTTP {ex.code}). Деталі: {err_body[:200]}")
                except urllib.error.URLError as ex:
                    last_error = str(ex)
                    reason_str = str(getattr(ex, 'reason', ex)).lower()
                    logger.warning(f"Gemini model {model} URL error attempt {attempt + 1}: {ex}")
                    is_server_error = any(s in reason_str for s in (
                        '503', '502', '500', 'service unavailable', 'bad gateway', 'internal server'
                    ))
                    if is_server_error:
                        last_error = f"Service Unavailable"
                        break  # try next model
                    if attempt < GEMINI_RETRIES:
                        time.sleep(2 ** attempt)
                        continue
                    raise RuntimeError(
                        "Не вдалося з'єднатися з мережею. "
                        "Перевірте підключення до інтернету."
                    )
        except RuntimeError:
            raise
        except Exception as ex:
            last_error = str(ex)
            logger.warning(f"Gemini unexpected error: {ex}")
            raise RuntimeError(f"Несподівана помилка Gemini: {ex}")

    if '429' in last_error or 'quota' in last_error.lower():
        raise RuntimeError(
            "Перевищено ліміт запитів до Gemini API. "
            "Зачекайте 1–2 хвилини та спробуйте знову. "
            "Безкоштовний план: 15 запитів/хвилину."
        )
    if '503' in last_error or 'unavailable' in last_error.lower() or 'service error' in last_error.lower():
        raise RuntimeError(
            "Сервіс тимчасово недоступний (503). "
            "Зачекайте хвилину та спробуйте знову."
        )
    raise RuntimeError(f"Gemini API не повернув відповідь. {last_error}")


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
