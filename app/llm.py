"""
app/llm.py — Unified multi-provider LLM client
================================================
Provider chain (failover order):
  1. Google Gemini  (GEMINI_API_KEY_1 / _2 / _3)
  2. OpenRouter     (OPENROUTER_API_KEY_1 / _2 / _3)
  3. Groq           (GROQ_API_KEY_1 / _2 / _3)

When ALL providers are exhausted → sends Telegram alert to ADMIN_TELEGRAM_ID.

Usage:
    from app.llm import llm_generate, llm_generate_json

    text = llm_generate("Поясни закон Ньютона")
    data = llm_generate_json("Поверни JSON: {\"key\": ...}")

Task routing:
    "test_generation"  → Gemini only (best Ukrainian, structured JSON)
    "ocr"              → Gemini Vision only (image → text)
    "grading"          → Gemini only (short, fast)
    "materials"        → Gemini first, then OpenRouter/Groq fallback
    "default"          → Gemini first, then OpenRouter/Groq fallback
"""

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

logger = logging.getLogger(__name__)

# ── Read all API keys from environment ────────────────────────────────────────

def _keys(prefix: str) -> list[str]:
    """Read KEY_1, KEY_2, KEY_3 for a given prefix. Skip empty."""
    result = []
    # Also check without suffix for backward compat
    base = os.environ.get(prefix, '').strip()
    if base:
        result.append(base)
    for i in range(1, 6):
        v = os.environ.get(f'{prefix}_{i}', '').strip()
        if v and v not in result:
            result.append(v)
    return result


GEMINI_KEYS       = _keys('GEMINI_API_KEY')
OPENROUTER_KEYS   = _keys('OPENROUTER_API_KEY')
GROQ_KEYS         = _keys('GROQ_API_KEY')

GEMINI_MODEL      = os.environ.get('GEMINI_MODEL', 'gemini-3.6-flash').strip()
GEMINI_FALLBACKS  = [m.strip() for m in
                     os.environ.get('GEMINI_FALLBACK_MODELS', 'gemini-3.8-flash,gemini-flash-latest').split(',')
                     if m.strip()]

# Best free/cheap OpenRouter models (in priority order)
OPENROUTER_MODELS = [
    m.strip() for m in os.environ.get(
        'OPENROUTER_MODELS',
        'google/gemini-1.5-flash,'
        'meta-llama/llama-3.1-8b-instruct:free,'
        'mistralai/mistral-7b-instruct:free'
    ).split(',') if m.strip()
]

# Best free Groq models
GROQ_MODELS = [
    m.strip() for m in os.environ.get(
        'GROQ_MODELS',
        'llama-3.3-70b-versatile,'
        'llama-3.1-70b-versatile,'
        'mixtral-8x7b-32768,'
        'gemma2-9b-it'
    ).split(',') if m.strip()
]

TIMEOUT    = int(os.environ.get('LLM_TIMEOUT_SECONDS', '90'))
RETRIES    = int(os.environ.get('LLM_RETRIES', '2'))
BOT_TOKEN  = os.environ.get('BOT_TOKEN', '').strip()
ADMIN_TG   = os.environ.get('ADMIN_TELEGRAM_ID', '').strip()

# ── Telegram alert ─────────────────────────────────────────────────────────────

def _tg_alert(message: str) -> None:
    """Send a Telegram message to the admin. Fire-and-forget."""
    if not BOT_TOKEN or not ADMIN_TG:
        return
    try:
        payload = json.dumps({
            'chat_id': ADMIN_TG,
            'text': f'🚨 SchoolSite LLM: {message}',
            'parse_mode': 'HTML'
        }).encode()
        req = urllib.request.Request(
            f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        urllib.request.urlopen(req, timeout=8)
    except Exception as ex:
        logger.warning(f'TG alert failed: {ex}')


# ── Provider implementations ───────────────────────────────────────────────────

def _call_gemini(prompt: str, api_key: str, model: str,
                 json_mode: bool = False, image_b64: str | None = None) -> str:
    """Single Gemini API call. Raises RuntimeError on failure."""
    parts: list[dict] = [{'text': prompt}]
    if image_b64:
        parts.append({'inline_data': {'mime_type': 'image/jpeg', 'data': image_b64}})

    payload: dict[str, Any] = {
        'contents': [{'parts': parts}],
        'generationConfig': {'temperature': 0.4}
    }
    if json_mode:
        payload['generationConfig']['responseMimeType'] = 'application/json'

    url = (f'https://generativelanguage.googleapis.com/v1beta/models/'
           f'{model}:generateContent?key={api_key}')
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        result = json.loads(resp.read().decode())

    candidates = result.get('candidates', [])
    for cand in candidates:
        finish_reason = cand.get('finishReason', '')
        text = ''.join(p.get('text', '') for p in cand.get('content', {}).get('parts', [])).strip()
        if text:
            if finish_reason == 'MAX_TOKENS':
                logger.warning(f'Gemini {model}: MAX_TOKENS truncation, len={len(text)}')
            return text
    raise RuntimeError(f'Gemini {model}: no text in response')


def _call_openrouter(prompt: str, api_key: str, model: str,
                     json_mode: bool = False) -> str:
    """Single OpenRouter API call (OpenAI-compatible)."""
    messages = [{'role': 'user', 'content': prompt}]
    payload: dict[str, Any] = {
        'model': model,
        'messages': messages,
        'temperature': 0.4,
        'max_tokens': 8192,
    }
    if json_mode:
        payload['response_format'] = {'type': 'json_object'}

    req = urllib.request.Request(
        'https://openrouter.ai/api/v1/chat/completions',
        data=json.dumps(payload).encode(),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
            'HTTP-Referer': 'https://schoolsite.onrender.com',
            'X-Title': 'SchoolSite',
        },
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        result = json.loads(resp.read().decode())

    choices = result.get('choices', [])
    if not choices:
        raise RuntimeError(f'OpenRouter {model}: no choices in response')
    content = choices[0].get('message', {}).get('content', '').strip()
    if not content:
        raise RuntimeError(f'OpenRouter {model}: empty content')
    return content


def _call_groq(prompt: str, api_key: str, model: str,
               json_mode: bool = False) -> str:
    """Single Groq API call (OpenAI-compatible)."""
    messages = [{'role': 'user', 'content': prompt}]
    payload: dict[str, Any] = {
        'model': model,
        'messages': messages,
        'temperature': 0.4,
        'max_tokens': 8192,
    }
    if json_mode:
        payload['response_format'] = {'type': 'json_object'}

    req = urllib.request.Request(
        'https://api.groq.com/openai/v1/chat/completions',
        data=json.dumps(payload).encode(),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        result = json.loads(resp.read().decode())

    choices = result.get('choices', [])
    if not choices:
        raise RuntimeError(f'Groq {model}: no choices in response')
    content = choices[0].get('message', {}).get('content', '').strip()
    if not content:
        raise RuntimeError(f'Groq {model}: empty content')
    return content


# ── Failover engine ────────────────────────────────────────────────────────────

class _ProviderSlot:
    __slots__ = ('name', 'fn', 'key', 'model')

    def __init__(self, name: str, fn, key: str, model: str):
        self.name  = name
        self.fn    = fn
        self.key   = key
        self.model = model

    def __str__(self):
        return f'{self.name}/{self.model[:30]}'


def _build_slots(task: str = 'default') -> list[_ProviderSlot]:
    """Build ordered list of provider slots based on task type."""
    slots: list[_ProviderSlot] = []

    # Gemini slots — best for Ukrainian language, JSON, OCR
    gemini_models = [GEMINI_MODEL] + [m for m in GEMINI_FALLBACKS if m != GEMINI_MODEL]
    for key in GEMINI_KEYS:
        for model in gemini_models:
            slots.append(_ProviderSlot('Gemini', _call_gemini, key, model))

    # If task is OCR → Gemini only (Vision required)
    if task in ('ocr',):
        return slots  # Gemini only

    # OpenRouter slots
    for key in OPENROUTER_KEYS:
        for model in OPENROUTER_MODELS:
            slots.append(_ProviderSlot('OpenRouter', _call_openrouter, key, model))

    # Groq slots
    for key in GROQ_KEYS:
        for model in GROQ_MODELS:
            slots.append(_ProviderSlot('Groq', _call_groq, key, model))

    return slots


def _is_retryable_error(ex: Exception) -> bool:
    """True if the error is transient (rate limit / 503) and we should try next slot."""
    msg = str(ex).lower()
    if isinstance(ex, urllib.error.HTTPError):
        return ex.code in (429, 500, 502, 503, 529)
    return any(k in msg for k in ('429', '503', '502', 'rate limit', 'quota',
                                   'overloaded', 'unavailable', 'timeout', 'timed out'))


def llm_generate(prompt: str, task: str = 'default',
                 json_mode: bool = False,
                 image_b64: str | None = None) -> str:
    """
    Main entry point. Tries all provider slots in order until one succeeds.

    Args:
        prompt:    The text prompt.
        task:      Hint for slot ordering: 'test_generation', 'materials',
                   'ocr', 'grading', 'default'.
        json_mode: Request JSON output from the provider.
        image_b64: Base64-encoded image for vision tasks (Gemini only).

    Returns:
        str — raw text response from the first successful provider.

    Raises:
        RuntimeError — if ALL providers are exhausted (Telegram alert sent).
    """
    slots = _build_slots(task)
    if not slots:
        raise RuntimeError('No LLM API keys configured. Add at least GEMINI_API_KEY to .env')

    errors: list[str] = []
    for slot in slots:
        for attempt in range(RETRIES + 1):
            try:
                kwargs: dict[str, Any] = {'json_mode': json_mode}
                if slot.name == 'Gemini' and image_b64:
                    kwargs['image_b64'] = image_b64
                result = slot.fn(prompt, slot.key, slot.model, **kwargs)
                if result:
                    if errors:  # recovered after failures
                        logger.info(f'LLM succeeded on {slot} after {len(errors)} failures')
                    return result
            except urllib.error.HTTPError as ex:
                body = ex.read().decode('utf-8', errors='ignore')[:200] if hasattr(ex, 'read') else ''
                err  = f'{slot} HTTP {ex.code}: {body}'
                logger.warning(f'LLM {err} (attempt {attempt+1})')
                errors.append(err)
                if ex.code in (401, 403):
                    break  # Bad key — skip all attempts for this slot
                if ex.code == 429:
                    retry_after = int(ex.headers.get('Retry-After', '0') or '0')
                    wait = min(retry_after or 15, 30)
                    if attempt < RETRIES:
                        time.sleep(wait)
                        continue
                break  # Try next slot
            except (TimeoutError, socket.timeout) as ex:
                err = f'{slot} timeout: {ex}'
                logger.warning(f'LLM {err} (attempt {attempt+1})')
                errors.append(err)
                if attempt < RETRIES:
                    time.sleep(2 ** attempt)
                    continue
                break
            except urllib.error.URLError as ex:
                err = f'{slot} URLError: {ex}'
                logger.warning(err)
                errors.append(err)
                break
            except RuntimeError as ex:
                err = f'{slot}: {ex}'
                logger.warning(err)
                errors.append(err)
                break
            except Exception as ex:
                err = f'{slot} unexpected: {ex}'
                logger.error(err)
                errors.append(err)
                break

    # All providers failed
    summary = f'Всі AI-провайдери недоступні! Перевірте API ключі.\n{chr(10).join(errors[-6:])}'
    logger.error(summary)
    _tg_alert(summary)
    raise RuntimeError(
        'AI тимчасово недоступний — усі провайдери вичерпано. '
        'Адміністратора сповіщено через Telegram.'
    )


def llm_generate_json(prompt: str, task: str = 'default') -> dict:
    """
    Like llm_generate but parses the result as JSON.
    Strips markdown code fences, repairs truncated JSON.
    """
    raw = llm_generate(prompt, task=task, json_mode=True)
    return _parse_json_response(raw)


def llm_ocr(image_b64: str, hint: str = '') -> str:
    """
    Extract text from a base64-encoded image using Gemini Vision.
    Falls back to text-only providers with a description request.
    """
    prompt = (
        'Розпізнай весь текст на зображенні. '
        'Поверни тільки розпізнаний текст, зберігаючи структуру (абзаци, списки). '
        'Якщо є математичні формули — запиши їх у LaTeX ($...$). '
        + (f'Контекст: {hint}' if hint else '')
    )
    return llm_generate(prompt, task='ocr', image_b64=image_b64)


# ── JSON repair helpers ────────────────────────────────────────────────────────

def _parse_json_response(raw: str) -> dict:
    text = raw.strip()
    # Strip markdown fences
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*```$', '', text)
    text = text.strip()

    # Fast path
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Trim to last complete {...} block
    start, end = text.find('{'), text.rfind('}')
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    # Attempt truncation repair
    if start != -1:
        repaired = _repair_truncated_json(text[start:])
        if repaired is not None:
            return repaired

    logger.error(f'JSON parse failed. First 200: {text[:200]!r}')
    raise json.JSONDecodeError('Cannot parse LLM JSON response', text, 0)


def _repair_truncated_json(fragment: str) -> dict | None:
    depth, in_string, escape = 0, False, False
    last_comma = -1
    for i, ch in enumerate(fragment):
        if escape:        escape = False; continue
        if ch == '\\' and in_string: escape = True; continue
        if ch == '"':     in_string = not in_string; continue
        if in_string:     continue
        if ch in ('{', '['): depth += 1
        elif ch in ('}', ']'): depth -= 1
        elif ch == ',' and depth == 2: last_comma = i
    if last_comma == -1:
        return None
    truncated = fragment[:last_comma]
    stack = []
    in_string = escape = False
    for ch in truncated:
        if escape:        escape = False; continue
        if ch == '\\' and in_string: escape = True; continue
        if ch == '"':     in_string = not in_string; continue
        if in_string:     continue
        if ch in ('{', '['): stack.append(ch)
        elif ch == '}' and stack and stack[-1] == '{': stack.pop()
        elif ch == ']' and stack and stack[-1] == '[': stack.pop()
    closing = ''.join('}' if c == '{' else ']' for c in reversed(stack))
    try:
        return json.loads(truncated + closing)
    except json.JSONDecodeError:
        return None


# ── Backward-compat shim (used by existing ai.py code) ───────────────────────
# ai.py calls call_gemini() directly; we keep that working but also allow
# new code to use llm_generate() for multi-provider support.

def get_provider_status() -> list[dict]:
    """Returns a list of configured providers and key counts — for admin UI."""
    return [
        {'name': 'Gemini',      'keys': len(GEMINI_KEYS),     'models': [GEMINI_MODEL] + GEMINI_FALLBACKS},
        {'name': 'OpenRouter',  'keys': len(OPENROUTER_KEYS), 'models': OPENROUTER_MODELS[:3]},
        {'name': 'Groq',        'keys': len(GROQ_KEYS),       'models': GROQ_MODELS[:3]},
    ]
