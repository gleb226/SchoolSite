# 🏫 Ужгородський ліцей №3 — SchoolSite

Веб-платформа для ліцею: тести з AI-генерацією, щоденник, голосування, Telegram-бот.

## Стек

- **Backend:** Python 3.11+, Flask 3, SQLite
- **AI:** Google Gemini API (тести, перевірка відповідей)
- **Auth:** JWT cookies + 2FA
- **Telegram:** aiogram 3
- **Hosting:** Render (free tier)

---

## Локальний запуск

```bash
git clone https://github.com/gleb226/SchoolSite
cd SchoolSite

# Створити .env (скопіювати з .env.example)
cp .env.example .env
# Заповнити ключі в .env

pip install -r requirements.txt
python start.py
# → http://localhost:5000
```

Першочерговий крок після запуску — перейти на `/setup-principal` і створити акаунт директора.

---

## Деплой на Render

### 1. Підготовка репозиторію

```bash
git add .
git commit -m "Initial deploy"
git push origin main
```

> ⚠️ `.env` та `*.db` файли **не комітяться** — вони в `.gitignore`.

### 2. Створення сервісу на Render

1. Відкрити [render.com](https://render.com) → **New → Web Service**
2. Підключити репозиторій `gleb226/SchoolSite`
3. Render автоматично підхопить `render.yaml` і налаштує сервіс

### 3. Змінні оточення (Environment Variables)

Задати в Render Dashboard → вкладка **Environment**:

| Змінна | Значення |
|--------|----------|
| `SECRET_KEY` | Довгий випадковий рядок (Render може згенерувати) |
| `GEMINI_API_KEY` | Ключ від [Google AI Studio](https://aistudio.google.com/app/apikey) |
| `GEMINI_MODEL` | `gemini-2.0-flash` |
| `GEMINI_FALLBACK_MODELS` | `gemini-1.5-flash,gemini-1.5-flash-8b` |
| `BOT_TOKEN` | Telegram bot token від @BotFather (можна не задавати) |
| `ADMIN_TELEGRAM_ID` | Твій Telegram user ID |
| `DATABASE_PATH` | `/opt/render/project/src/school.db` |
| `FLASK_ENV` | `production` |
| `FLASK_DEBUG` | `0` |

### 4. Перший запуск

Після деплою:
1. Відкрити `https://your-app.onrender.com/setup-principal`
2. Створити акаунт директора
3. Зайти в `/admin` → затверджувати користувачів

### ⚠️ Важливо про SQLite на Render

Render **free tier не має persistent disk** — при кожному редеплої дані скидаються. Для продакшну:
- Або **Render Disk** (платно, ~$1/міс) — додати в `render.yaml`
- Або мігрувати на **PostgreSQL** (Render надає безкоштовно)

Для тестування та шкільного використання free SQLite достатньо.

---

## Структура проекту

```
SchoolSite/
├── app/
│   ├── __init__.py         # create_app() factory
│   ├── ai.py               # Gemini API інтеграція
│   ├── database.py         # SQLite schema + migrations
│   ├── routes/             # Blueprint routes
│   └── bot/                # Telegram bot
├── templates/              # Jinja2 HTML шаблони
├── static/                 # CSS, JS
├── wsgi.py                 # Gunicorn entry point
├── start.py                # Локальний запуск
├── requirements.txt
├── Procfile
└── render.yaml
```

---

## Функціонал

- 📋 **Тести** — ручне створення або AI-генерація (Gemini)
- 🎲 **Варіанти** — 1–30 варіантів, зміна чисел для мат/фіз/хім
- ∑ **Формули** — KaTeX редактор з повним набором 1–11 клас
- 📷 **Фото** — вчитель може додати зображення до кожного питання
- 📓 **Щоденник** — оцінки, відвідуваність
- 🗳️ **Голосування**
- 🤖 **Telegram-бот** — сповіщення, 2FA
- 🔒 **Античит** — захист від копіювання, скріншотів, перемикання вкладок

---

## Ліцензія

Приватний навчальний проект. Всі права захищені.
