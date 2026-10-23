import sqlite3
import os
import logging
from flask import g, current_app
from datetime import datetime, timedelta
import bcrypt

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('student','parent','teacher','vice_principal','principal','class_president','school_president','admin','superuser')),
    full_name TEXT NOT NULL,
    phone TEXT,
    telegram_id INTEGER,
    telegram_username TEXT,
    is_approved BOOLEAN DEFAULT 0,
    is_active BOOLEAN DEFAULT 1,
    two_fa_enabled BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    year INTEGER NOT NULL,
    class_teacher_id INTEGER REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS student_classes (
    student_id INTEGER REFERENCES users(id),
    class_id INTEGER REFERENCES classes(id),
    PRIMARY KEY (student_id, class_id)
);
CREATE TABLE IF NOT EXISTS parent_student (
    parent_id INTEGER REFERENCES users(id),
    student_id INTEGER REFERENCES users(id),
    PRIMARY KEY (parent_id, student_id)
);
CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS grades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER REFERENCES users(id),
    subject_id INTEGER REFERENCES subjects(id),
    teacher_id INTEGER REFERENCES users(id),
    grade INTEGER CHECK(grade BETWEEN 1 AND 12),
    grade_type TEXT,
    comment TEXT,
    lesson_number INTEGER,
    lesson_topic TEXT,
    date DATE DEFAULT CURRENT_DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER REFERENCES users(id),
    class_id INTEGER REFERENCES classes(id),
    subject_id INTEGER REFERENCES subjects(id),
    date DATE NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('present','absent','late','excused')),
    note TEXT
);
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    author_id INTEGER REFERENCES users(id),
    is_published BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS teacher_profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    subject_specialty TEXT,
    bio TEXT,
    photo_url TEXT,
    experience_years INTEGER
);
CREATE TABLE IF NOT EXISTS tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    teacher_id INTEGER REFERENCES users(id),
    class_id INTEGER REFERENCES classes(id),
    subject_id INTEGER REFERENCES subjects(id),
    time_limit_minutes INTEGER DEFAULT 45,
    max_attempts INTEGER DEFAULT 1,
    is_active BOOLEAN DEFAULT 0,
    is_practice BOOLEAN DEFAULT 0,
    topic TEXT,
    qr_code TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id INTEGER REFERENCES tests(id),
    question_text TEXT NOT NULL,
    question_type TEXT DEFAULT 'single',
    points INTEGER DEFAULT 1,
    order_num INTEGER,
    explanation TEXT
);
CREATE TABLE IF NOT EXISTS answer_options (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER REFERENCES questions(id),
    option_text TEXT NOT NULL,
    is_correct BOOLEAN DEFAULT 0
);
CREATE TABLE IF NOT EXISTS test_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id INTEGER REFERENCES tests(id),
    student_id INTEGER REFERENCES users(id),
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    score REAL,
    max_score REAL,
    cheat_attempts INTEGER DEFAULT 0,
    cheat_log TEXT,
    is_disqualified BOOLEAN DEFAULT 0,
    status TEXT DEFAULT 'in_progress'
);
CREATE TABLE IF NOT EXISTS completed_topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER REFERENCES users(id),
    subject_id INTEGER REFERENCES subjects(id),
    topic TEXT NOT NULL,
    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source_test_id INTEGER REFERENCES tests(id),
    UNIQUE(student_id, subject_id, topic)
);
CREATE TABLE IF NOT EXISTS student_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER REFERENCES test_attempts(id),
    question_id INTEGER REFERENCES questions(id),
    selected_option_id INTEGER REFERENCES answer_options(id),
    text_answer TEXT
);
CREATE TABLE IF NOT EXISTS votings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    created_by INTEGER REFERENCES users(id),
    start_date TIMESTAMP,
    end_date TIMESTAMP,
    is_active BOOLEAN DEFAULT 0
);
CREATE TABLE IF NOT EXISTS voting_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    voting_id INTEGER REFERENCES votings(id),
    name TEXT NOT NULL,
    description TEXT,
    class_name TEXT
);
CREATE TABLE IF NOT EXISTS votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    voting_id INTEGER REFERENCES votings(id),
    voter_id INTEGER REFERENCES users(id),
    candidate_id INTEGER REFERENCES voting_candidates(id),
    voted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(voting_id, voter_id)
);
CREATE TABLE IF NOT EXISTS tg_2fa_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    code TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    used BOOLEAN DEFAULT 0
);
CREATE TABLE IF NOT EXISTS test_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id INTEGER REFERENCES tests(id) ON DELETE CASCADE,
    teacher_id INTEGER REFERENCES users(id),
    join_code TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'waiting'
        CHECK(status IN ('waiting','active','closed')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    closed_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS test_session_participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES test_sessions(id) ON DELETE CASCADE,
    student_id INTEGER REFERENCES users(id),
    attempt_id INTEGER REFERENCES test_attempts(id),
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(session_id, student_id)
);
CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    subject TEXT,
    class_level TEXT,
    content_md TEXT NOT NULL DEFAULT '',
    teacher_id INTEGER REFERENCES users(id),
    class_id INTEGER REFERENCES classes(id),
    is_published BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS gpd_pickups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER REFERENCES users(id),
    student_id INTEGER REFERENCES users(id),
    pickup_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'requested',
    note TEXT,
    completed_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS certificate_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    student_name TEXT NOT NULL,
    class_name TEXT NOT NULL,
    cert_type TEXT NOT NULL,
    purpose TEXT,
    cert_number TEXT,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    issued_at TIMESTAMP,
    issued_by INTEGER REFERENCES users(id),
    rejection_reason TEXT
);
CREATE TABLE IF NOT EXISTS lunch_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES users(id),
    token TEXT UNIQUE NOT NULL,
    date DATE NOT NULL,
    used INTEGER DEFAULT 0,
    used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(student_id, date)
);
CREATE TABLE IF NOT EXISTS lunch_attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES users(id),
    class_id INTEGER NOT NULL REFERENCES classes(id),
    date DATE NOT NULL,
    present INTEGER DEFAULT 1,
    marked_by INTEGER NOT NULL REFERENCES users(id),
    marked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(student_id, date)
);
"""


def get_db():
    if 'db' not in g:
        db_path = current_app.config.get('DATABASE', 'school.db')
        g.db = sqlite3.connect(db_path, timeout=20.0)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
        try:
            g.db.execute('PRAGMA journal_mode = WAL')
        except sqlite3.OperationalError:
            pass
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    db_path = current_app.config.get('DATABASE', 'school.db')
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    for col, tbl in [
        ('explanation TEXT', 'questions'),
        ('is_practice BOOLEAN DEFAULT 0', 'tests'),
        ('topic TEXT', 'tests'),
        ('cheat_log TEXT', 'test_attempts'),
        ('lesson_number INTEGER', 'grades'),
        ('lesson_topic TEXT', 'grades'),
        ('ai_score REAL', 'student_answers'),
        ('ai_comment TEXT', 'student_answers'),
        ('teacher_score REAL', 'student_answers'),
        ('shuffle_questions BOOLEAN DEFAULT 0', 'tests'),
        ('shuffle_options BOOLEAN DEFAULT 0', 'tests'),
        ('variant_group_id INTEGER', 'tests'),
        ('variant_number INTEGER', 'test_attempts'),
        ('question_order TEXT', 'test_attempts'),
        ('anticheat_disabled BOOLEAN DEFAULT 0', 'test_sessions'),
        ('exit_duration_seconds REAL', 'test_attempts'),
        ('exit_count INTEGER DEFAULT 0', 'test_attempts'),
        ('image_url TEXT', 'questions'),
        ('image_urls TEXT', 'materials'),
        ('deadline TEXT', 'tests'),
        ('guest_name TEXT', 'test_session_participants'),
        ('guest_name TEXT', 'test_attempts'),
    ]:
        try:
            conn.execute(f'ALTER TABLE {tbl} ADD COLUMN {col}')
        except Exception:
            pass

    # lunch_attendance (create if missing — safe to run multiple times)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS lunch_attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL REFERENCES users(id),
            class_id INTEGER NOT NULL REFERENCES classes(id),
            date DATE NOT NULL,
            present INTEGER DEFAULT 1,
            marked_by INTEGER NOT NULL REFERENCES users(id),
            marked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(student_id, date)
        );
    """)

    # test_sessions tables (create if missing — safe to run multiple times)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS test_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER REFERENCES tests(id) ON DELETE CASCADE,
            teacher_id INTEGER REFERENCES users(id),
            join_code TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'waiting'
                CHECK(status IN ('waiting','active','closed')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            closed_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS test_session_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER REFERENCES test_sessions(id) ON DELETE CASCADE,
            student_id INTEGER REFERENCES users(id),
            attempt_id INTEGER REFERENCES test_attempts(id),
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(session_id, student_id)
        );
    """)
    try:
        conn.execute('ALTER TABLE test_session_participants ADD COLUMN anticheat_disabled BOOLEAN DEFAULT 0')
    except Exception:
        pass
    try:
        conn.execute('ALTER TABLE test_session_participants ADD COLUMN variant_number INTEGER')
    except Exception:
        pass

    # Ensure full school subjects list
    all_school_subjects = [
        'Українська мова', 'Українська література', 'Зарубіжна література',
        'Англійська мова', 'Чеська мова',
        'Математика', 'Алгебра', 'Геометрія', 'Інформатика',
        'Історія України', 'Всесвітня історія', 'Громадянська освіта', 'Правознавство',
        'Фізика', 'Астрономія', 'Хімія', 'Біологія', 'Екологія', 'Географія',
        'Природознавство', 'Основи здоров\'я', 'Мистецтво',
        'Трудове навчання', 'Технології', 'Захист України', 'Фізична культура'
    ]
    try:
        conn.execute("DELETE FROM subjects WHERE name IN ('Німецька мова', 'Французька мова')")
        existing_subjs = {row[0] for row in conn.execute('SELECT name FROM subjects').fetchall()}
        new_subjs = [(s,) for s in all_school_subjects if s not in existing_subjs]
        if new_subjs:
            conn.executemany('INSERT INTO subjects (name) VALUES (?)', new_subjs)
    except Exception as e:
        logger.warning(f"Failed to populate subjects: {e}")

    try:
        all_classes_needed = [f"{i}-{l}" for i in range(1, 12) for l in ['А', 'Б']]
        existing_classes = {row[0] for row in conn.execute("SELECT name FROM classes").fetchall()}
        new_classes = [(c, 2025) for c in all_classes_needed if c not in existing_classes]
        if new_classes:
            conn.executemany("INSERT INTO classes (name, year) VALUES (?, ?)", new_classes)
            conn.commit()
    except Exception as e:
        logger.warning(f"Failed to populate classes: {e}")

    try:
        subject_teachers_data = [
            ('Українська мова', 'Мельник Ольга Петрівна', 'melnyk_o', 'teacher_melnyk@school3.uz.ua'),
            ('Українська література', 'Коваленко Наталія Іванівна', 'kovalenko_n', 'teacher_kovalenko@school3.uz.ua'),
            ('Зарубіжна література', 'Шевчук Олена Василівна', 'shevchuk_o', 'teacher_shevchuk@school3.uz.ua'),
            ('Англійська мова', 'Бондар Світлана Михайлівна', 'bondar_s', 'teacher_bondar@school3.uz.ua'),
            ('Чеська мова', 'Новак Ян Карелович', 'novak_y', 'teacher_novak@school3.uz.ua'),
            ('Математика', 'Коваль Ірина Василівна', 'teacher1', 'teacher1@school3.uz.ua'),
            ('Алгебра', 'Петренко Анатолій Сергійович', 'petrenko_a', 'teacher_petrenko@school3.uz.ua'),
            ('Геометрія', 'Лисенко Тетяна Володимирівна', 'lysenko_t', 'teacher_lysenko@school3.uz.ua'),
            ('Інформатика', 'Кравченко Дмитро Олександрович', 'kravchenko_d', 'teacher_kravchenko@school3.uz.ua'),
            ('Історія України', 'Григоренко Віктор Павлович', 'hryhorenko_v', 'teacher_hryhorenko@school3.uz.ua'),
            ('Всесвітня історія', 'Савченко Марина Вікторівна', 'savchenko_m', 'teacher_savchenko@school3.uz.ua'),
            ('Громадянська освіта', 'Ткаченко Андрій Миколайович', 'tkachenko_a', 'teacher_tkachenko@school3.uz.ua'),
            ('Правознавство', 'Мороз Юлія Сергіївна', 'moroz_y', 'teacher_moroz@school3.uz.ua'),
            ('Фізика', 'Бойко Сергій Ярославович', 'boyko_s', 'teacher_boyko@school3.uz.ua'),
            ('Астрономія', 'Павленко Олег Григорович', 'pavlenko_o', 'teacher_pavlenko@school3.uz.ua'),
            ('Хімія', 'Семенюк Лариса Федорівна', 'semenyuk_l', 'teacher_semenyuk@school3.uz.ua'),
            ('Біологія', 'Василенко Надія Романівна', 'vasylenko_n', 'teacher_vasylenko@school3.uz.ua'),
            ('Екологія', 'Марченко Інна Борисівна', 'marchenko_i', 'teacher_marchenko@school3.uz.ua'),
            ('Географія', 'Дудник Віталій Ігорович', 'dudnyk_v', 'teacher_dudnyk@school3.uz.ua'),
            ('Природознавство', 'Кузьменко Оксана Тарасівна', 'kuzmenko_o', 'teacher_kuzmenko@school3.uz.ua'),
            ('Основи здоров\'я', 'Поліщук Роман Степанович', 'polishchuk_r', 'teacher_polishchuk@school3.uz.ua'),
            ('Мистецтво', 'Ковальчук Дарія Леонідівна', 'kovalchuk_d', 'teacher_kovalchuk@school3.uz.ua'),
            ('Трудове навчання', 'Сидоренко Василь Миколайович', 'sydorenko_v', 'teacher_sydorenko@school3.uz.ua'),
            ('Технології', 'Іванов Денис Артемович', 'ivanov_d', 'teacher_ivanov@school3.uz.ua'),
            ('Захист України', 'Тарасенко Валерій Богданович', 'tarasenko_v', 'teacher_tarasenko@school3.uz.ua'),
            ('Фізична культура', 'Захарченко Максим Ілліч', 'zakharchenko_m', 'teacher_zakharchenko@school3.uz.ua'),
        ]
        import bcrypt
        default_pw = os.environ.get('DEFAULT_TEACHER_PASSWORD', 'Teacher2025!')
        t_hash = bcrypt.hashpw(default_pw.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        for subj, full_name, username, email in subject_teachers_data:
            user = conn.execute(
                "SELECT id FROM users WHERE username=? OR email=?", (username, email)
            ).fetchone()
            if not user:
                conn.execute(
                    """INSERT INTO users 
                       (username, email, password_hash, role, full_name, phone, is_approved, is_active)
                       VALUES (?, ?, ?, 'teacher', ?, '+380312000000', 1, 1)""",
                    (username, email, t_hash, full_name)
                )
                uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            else:
                uid = user[0]
            
            tp = conn.execute("SELECT 1 FROM teacher_profiles WHERE user_id=?", (uid,)).fetchone()
            if not tp:
                conn.execute(
                    """INSERT INTO teacher_profiles (user_id, subject_specialty, bio, experience_years)
                       VALUES (?, ?, ?, 10)""",
                    (uid, subj, f"Викладач предмету «{subj}» в Ужгородському ліцеї №3.")
                )
        conn.commit()
    except Exception as e:
        logger.warning(f"Failed to populate teachers: {e}")

    try:
        superuser_pw = os.environ.get('SUPERUSER_PASSWORD', 'Admin2025!Secure')
        pw_hash = bcrypt.hashpw(superuser_pw.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        existing_su = conn.execute(
            "SELECT id FROM users WHERE username IN ('medleb', 'meleb', 'gmedvedev') OR email='gmedvede226@gmail.com'"
        ).fetchone()

        if existing_su:
            conn.execute(
                '''UPDATE users SET 
                    username='medleb',
                    email='gmedvede226@gmail.com',
                    password_hash=?,
                    role='superuser',
                    full_name='Гліб Медведєв',
                    phone='+380990571770',
                    is_approved=1,
                    is_active=1,
                    telegram_id=513546547,
                    telegram_username='medleb7'
                WHERE id=?''',
                (pw_hash, existing_su[0])
            )
            su_id = existing_su[0]
        else:
            conn.execute(
                '''INSERT INTO users 
                    (username, email, password_hash, role, full_name, phone, telegram_id, telegram_username, is_approved, is_active)
                VALUES 
                    ('medleb', 'gmedvede226@gmail.com', ?, 'superuser', 'Гліб Медведєв', '+380990571770', 513546547, 'medleb7', 1, 1)''',
                (pw_hash,)
            )
            su_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        class_11a = conn.execute("SELECT id FROM classes WHERE name='11-А'").fetchone()
        if class_11a and su_id:
            conn.execute(
                "INSERT OR IGNORE INTO student_classes (student_id, class_id) VALUES (?, ?)",
                (su_id, class_11a[0])
            )
        conn.commit()
    except Exception as e:
        logger.warning(f"Failed to ensure superuser in 11-A: {e}")
    conn.commit()
    conn.close()
    logger.info('Database initialized')


def seed_data():
    db_path = current_app.config.get('DATABASE', 'school.db')
    conn = sqlite3.connect(db_path, timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')

    try:
        existing = conn.execute('SELECT COUNT(*) as c FROM users').fetchone()['c']
        if existing > 0:
            conn.close()
            return
    except sqlite3.OperationalError:
        pass

    logger.info('Seeding sample data...')

    def hash_pw(pw):
        return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

    users = [
        ('gmedvedev', 'gmedvede226@gmail.com', hash_pw(os.environ.get('SUPERUSER_PASSWORD', 'gmedvedev123')), 'superuser', 'Гліб Медведєв', '+380990571770', 1, 1),
        ('admin', 'admin@school3.uz.ua', hash_pw('admin123'), 'admin', 'Адміністратор Системи', '+380312000001', 1, 1),
        ('teacher1', 'teacher1@school3.uz.ua', hash_pw('teacher123'), 'teacher', 'Коваль Ірина Василівна', '+380312000002', 1, 1),
        ('student1', 'student1@school3.uz.ua', hash_pw('student123'), 'student', 'Петренко Олексій Іванович', '+380312000003', 1, 1),
        ('student2', 'student2@school3.uz.ua', hash_pw('student123'), 'student', 'Сидоренко Марія Петрівна', '+380312000004', 1, 1),
        ('parent1', 'parent1@school3.uz.ua', hash_pw('parent123'), 'parent', 'Петренко Іван Миколайович', '+380312000005', 1, 1),
    ]
    try:
        conn.executemany(
            'INSERT OR IGNORE INTO users (username,email,password_hash,role,full_name,phone,is_approved,is_active) VALUES (?,?,?,?,?,?,?,?)',
            users
        )
    except sqlite3.IntegrityError:
        pass

    classes = [('10-А', 2025, 2), ('10-Б', 2025, None), ('11-А', 2025, 2)]
    conn.executemany('INSERT OR IGNORE INTO classes (name,year,class_teacher_id) VALUES (?,?,?)', classes)

    conn.execute('INSERT OR IGNORE INTO student_classes VALUES (3,1)')
    conn.execute('INSERT OR IGNORE INTO student_classes VALUES (4,1)')
    conn.execute('INSERT OR IGNORE INTO parent_student VALUES (5,3)')

    subjects = [
        ('Математика',), ('Українська мова',), ('Фізика',), ('Хімія',),
        ('Біологія',), ('Географія',), ('Англійська мова',), ('Історія',), ('Інформатика',)
    ]
    conn.executemany('INSERT INTO subjects (name) VALUES (?)', subjects)

    conn.execute(
        'INSERT INTO teacher_profiles (user_id, subject_specialty, bio, experience_years) VALUES (2,?,?,?)',
        ('Математика', 'Досвідчений вчитель математики з 15-річним стажем роботи в ліцеї.', 15)
    )

    from datetime import date
    today = date.today().isoformat()
    grades = [
        (3, 1, 2, 11, 'current', 'Добре', today),
        (3, 2, 2, 9, 'current', '', today),
        (3, 3, 2, 10, 'current', 'Відмінно', today),
        (3, 7, 2, 8, 'current', '', today),
        (3, 9, 2, 12, 'current', 'Чудово!', today),
        (4, 1, 2, 7, 'current', '', today),
        (4, 2, 2, 10, 'current', '', today),
    ]
    conn.executemany(
        'INSERT INTO grades (student_id,subject_id,teacher_id,grade,grade_type,comment,date) VALUES (?,?,?,?,?,?,?)',
        grades
    )

    attendance = [
        (3, 1, 1, today, 'present', ''),
        (3, 1, 2, today, 'present', ''),
        (3, 1, 3, today, 'late', 'Запізнення'),
        (4, 1, 1, today, 'present', ''),
        (4, 1, 2, today, 'absent', 'Хворіє'),
    ]
    conn.executemany(
        'INSERT INTO attendance (student_id,class_id,subject_id,date,status,note) VALUES (?,?,?,?,?,?)',
        attendance
    )

    conn.execute(
        "INSERT INTO news (title,content,author_id,is_published,created_at) VALUES (?,?,?,1,?)",
        (
            'Початок нового навчального року',
            '<p>Шановні учні, вчителі та батьки!</p><p>Раді вітати вас з початком нового 2025-2026 навчального року в <strong>Ужгородському ліцеї №3</strong>!</p><p>Цього року нас чекає багато цікавого: нові предмети, захоплюючі заходи та нові досягнення. Бажаємо всім плідної праці та чудового навчального року!</p><p>Адміністрація ліцею</p>',
            1,
            datetime.now().isoformat()
        )
    )

    conn.execute(
        'INSERT INTO tests (title,description,teacher_id,class_id,subject_id,time_limit_minutes,max_attempts,is_active) VALUES (?,?,?,?,?,?,?,?)',
        ('Контрольна з математики (тема: квадратні рівняння)', 'Тест перевіряє знання квадратних рівнянь та їх розвязання.', 2, 1, 1, 30, 1, 1)
    )
    conn.execute(
        'INSERT INTO questions (test_id,question_text,question_type,points,order_num) VALUES (1,?,?,?,?)',
        ('Розвяжіть рівняння: x² - 5x + 6 = 0', 'single', 2, 1)
    )
    conn.executemany('INSERT INTO answer_options (question_id,option_text,is_correct) VALUES (?,?,?)', [
        (1, 'x = 2 та x = 3', 1), (1, 'x = -2 та x = -3', 0),
        (1, 'x = 1 та x = 6', 0), (1, 'Немає розвязків', 0),
    ])
    conn.execute(
        'INSERT INTO questions (test_id,question_text,question_type,points,order_num) VALUES (1,?,?,?,?)',
        ('Яка формула дискримінанта квадратного рівняння ax²+bx+c=0?', 'single', 1, 2)
    )
    conn.executemany('INSERT INTO answer_options (question_id,option_text,is_correct) VALUES (?,?,?)', [
        (2, 'D = b² - 4ac', 1), (2, 'D = b² + 4ac', 0),
        (2, 'D = 4ac - b²', 0), (2, 'D = b² - 2ac', 0),
    ])
    conn.execute(
        'INSERT INTO questions (test_id,question_text,question_type,points,order_num) VALUES (1,?,?,?,?)',
        ('Напишіть корені рівняння x² - 1 = 0 через кому:', 'text', 2, 3)
    )

    conn.execute(
        'INSERT INTO votings (title,description,created_by,start_date,end_date,is_active) VALUES (?,?,?,?,?,?)',
        ('Вибори президента учнівського самоврядування', 'Обираємо президента учнівського самоврядування на 2025-2026 навчальний рік.', 1,
         datetime.now().isoformat(), (datetime.now() + timedelta(days=7)).isoformat(), 1)
    )
    candidates = [
        (1, 'Іваненко Андрій', '10-А', 'Хочу покращити шкільне життя для всіх учнів!'),
        (1, 'Мельник Олена', '11-А', 'Буду відстоювати права кожного учня!'),
        (1, 'Бондаренко Тарас', '10-Б', 'Разом ми зробимо наш ліцей кращим!'),
    ]
    conn.executemany('INSERT INTO voting_candidates (voting_id,name,class_name,description) VALUES (?,?,?,?)', candidates)

    try:
        all_classes_needed = [f"{i}-{l}" for i in range(1, 12) for l in ['А', 'Б']]
        existing_classes = {row[0] for row in conn.execute("SELECT name FROM classes").fetchall()}
        new_classes = [(c, 2025) for c in all_classes_needed if c not in existing_classes]
        if new_classes:
            conn.executemany("INSERT INTO classes (name, year) VALUES (?, ?)", new_classes)
            conn.commit()
    except Exception as e:
        logger.warning(f"Failed to populate classes: {e}")

    try:
        subject_teachers_data = [
            ('Українська мова', 'Мельник Ольга Петрівна', 'melnyk_o', 'teacher_melnyk@school3.uz.ua'),
            ('Українська література', 'Коваленко Наталія Іванівна', 'kovalenko_n', 'teacher_kovalenko@school3.uz.ua'),
            ('Зарубіжна література', 'Шевчук Олена Василівна', 'shevchuk_o', 'teacher_shevchuk@school3.uz.ua'),
            ('Англійська мова', 'Бондар Світлана Михайлівна', 'bondar_s', 'teacher_bondar@school3.uz.ua'),
            ('Чеська мова', 'Новак Ян Карелович', 'novak_y', 'teacher_novak@school3.uz.ua'),
            ('Математика', 'Коваль Ірина Василівна', 'teacher1', 'teacher1@school3.uz.ua'),
            ('Алгебра', 'Петренко Анатолій Сергійович', 'petrenko_a', 'teacher_petrenko@school3.uz.ua'),
            ('Геометрія', 'Лисенко Тетяна Володимирівна', 'lysenko_t', 'teacher_lysenko@school3.uz.ua'),
            ('Інформатика', 'Кравченко Дмитро Олександрович', 'kravchenko_d', 'teacher_kravchenko@school3.uz.ua'),
            ('Історія України', 'Григоренко Віктор Павлович', 'hryhorenko_v', 'teacher_hryhorenko@school3.uz.ua'),
            ('Всесвітня історія', 'Савченко Марина Вікторівна', 'savchenko_m', 'teacher_savchenko@school3.uz.ua'),
            ('Громадянська освіта', 'Ткаченко Андрій Миколайович', 'tkachenko_a', 'teacher_tkachenko@school3.uz.ua'),
            ('Правознавство', 'Мороз Юлія Сергіївна', 'moroz_y', 'teacher_moroz@school3.uz.ua'),
            ('Фізика', 'Бойко Сергій Ярославович', 'boyko_s', 'teacher_boyko@school3.uz.ua'),
            ('Астрономія', 'Павленко Олег Григорович', 'pavlenko_o', 'teacher_pavlenko@school3.uz.ua'),
            ('Хімія', 'Семенюк Лариса Федорівна', 'semenyuk_l', 'teacher_semenyuk@school3.uz.ua'),
            ('Біологія', 'Василенко Надія Романівна', 'vasylenko_n', 'teacher_vasylenko@school3.uz.ua'),
            ('Екологія', 'Марченко Інна Борисівна', 'marchenko_i', 'teacher_marchenko@school3.uz.ua'),
            ('Географія', 'Дудник Віталій Ігорович', 'dudnyk_v', 'teacher_dudnyk@school3.uz.ua'),
            ('Природознавство', 'Кузьменко Оксана Тарасівна', 'kuzmenko_o', 'teacher_kuzmenko@school3.uz.ua'),
            ('Основи здоров\'я', 'Поліщук Роман Степанович', 'polishchuk_r', 'teacher_polishchuk@school3.uz.ua'),
            ('Мистецтво', 'Ковальчук Дарія Леонідівна', 'kovalchuk_d', 'teacher_kovalchuk@school3.uz.ua'),
            ('Трудове навчання', 'Сидоренко Василь Миколайович', 'sydorenko_v', 'teacher_sydorenko@school3.uz.ua'),
            ('Технології', 'Іванов Денис Артемович', 'ivanov_d', 'teacher_ivanov@school3.uz.ua'),
            ('Захист України', 'Тарасенко Валерій Богданович', 'tarasenko_v', 'teacher_tarasenko@school3.uz.ua'),
            ('Фізична культура', 'Захарченко Максим Ілліч', 'zakharchenko_m', 'teacher_zakharchenko@school3.uz.ua'),
        ]
        import bcrypt
        default_pw = os.environ.get('DEFAULT_TEACHER_PASSWORD', 'Teacher2025!')
        t_hash = bcrypt.hashpw(default_pw.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        for subj, full_name, username, email in subject_teachers_data:
            user = conn.execute(
                "SELECT id FROM users WHERE username=? OR email=?", (username, email)
            ).fetchone()
            if not user:
                conn.execute(
                    """INSERT INTO users 
                       (username, email, password_hash, role, full_name, phone, is_approved, is_active)
                       VALUES (?, ?, ?, 'teacher', ?, '+380312000000', 1, 1)""",
                    (username, email, t_hash, full_name)
                )
                uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            else:
                uid = user[0]
            
            tp = conn.execute("SELECT 1 FROM teacher_profiles WHERE user_id=?", (uid,)).fetchone()
            if not tp:
                conn.execute(
                    """INSERT INTO teacher_profiles (user_id, subject_specialty, bio, experience_years)
                       VALUES (?, ?, ?, 10)""",
                    (uid, subj, f"Викладач предмету «{subj}» в Ужгородському ліцеї №3.")
                )
        conn.commit()
    except Exception as e:
        logger.warning(f"Failed to populate teachers: {e}")

    try:
        superuser_pw = os.environ.get('SUPERUSER_PASSWORD', 'Admin2025!Secure')
        pw_hash = bcrypt.hashpw(superuser_pw.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        existing_su = conn.execute(
            "SELECT id FROM users WHERE username IN ('medleb', 'meleb', 'gmedvedev') OR email='gmedvede226@gmail.com'"
        ).fetchone()

        if existing_su:
            conn.execute(
                '''UPDATE users SET 
                    username='medleb',
                    email='gmedvede226@gmail.com',
                    password_hash=?,
                    role='superuser',
                    full_name='Гліб Медведєв',
                    phone='+380990571770',
                    is_approved=1,
                    is_active=1,
                    telegram_id=513546547,
                    telegram_username='medleb7'
                WHERE id=?''',
                (pw_hash, existing_su[0])
            )
            su_id = existing_su[0]
        else:
            conn.execute(
                '''INSERT INTO users 
                    (username, email, password_hash, role, full_name, phone, telegram_id, telegram_username, is_approved, is_active)
                VALUES 
                    ('medleb', 'gmedvede226@gmail.com', ?, 'superuser', 'Гліб Медведєв', '+380990571770', 513546547, 'medleb7', 1, 1)''',
                (pw_hash,)
            )
            su_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        class_11a = conn.execute("SELECT id FROM classes WHERE name='11-А'").fetchone()
        if class_11a and su_id:
            conn.execute(
                "INSERT OR IGNORE INTO student_classes (student_id, class_id) VALUES (?, ?)",
                (su_id, class_11a[0])
            )
        conn.commit()
    except Exception as e:
        logger.warning(f"Failed to ensure superuser in 11-A: {e}")
    conn.commit()
    conn.close()
    logger.info('Sample data seeded successfully')
