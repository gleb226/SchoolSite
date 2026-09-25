from app.database import get_db
import logging

logger = logging.getLogger(__name__)


class UserModel:
    @staticmethod
    def get_by_id(user_id):
        db = get_db()
        row = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def get_by_username(username):
        db = get_db()
        row = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def get_by_telegram_id(telegram_id):
        db = get_db()
        row = db.execute('SELECT * FROM users WHERE telegram_id = ?', (telegram_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def get_all(role=None, approved_only=False):
        db = get_db()
        q = 'SELECT * FROM users WHERE 1=1'
        params = []
        if role:
            q += ' AND role = ?'
            params.append(role)
        if approved_only:
            q += ' AND is_approved = 1'
        q += ' ORDER BY full_name'
        rows = db.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def create(username, email, password_hash, role, full_name, phone=''):
        db = get_db()
        db.execute(
            'INSERT INTO users (username,email,password_hash,role,full_name,phone,is_approved) VALUES (?,?,?,?,?,?,0)',
            (username, email, password_hash, role, full_name, phone)
        )
        db.commit()
        row = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        return dict(row)

    @staticmethod
    def update_approval(user_id, approved):
        db = get_db()
        db.execute('UPDATE users SET is_approved = ? WHERE id = ?', (1 if approved else 0, user_id))
        db.commit()

    @staticmethod
    def link_telegram(user_id, telegram_id, telegram_username):
        db = get_db()
        db.execute(
            'UPDATE users SET telegram_id = ?, telegram_username = ? WHERE id = ?',
            (telegram_id, telegram_username, user_id)
        )
        db.commit()

    @staticmethod
    def get_students_of_parent(parent_id):
        db = get_db()
        rows = db.execute(
            'SELECT u.* FROM users u JOIN parent_student ps ON u.id = ps.student_id WHERE ps.parent_id = ?',
            (parent_id,)
        ).fetchall()
        return [dict(r) for r in rows]


class NewsModel:
    @staticmethod
    def get_published(limit=10, offset=0):
        db = get_db()
        rows = db.execute(
            'SELECT n.*, u.full_name as author_name FROM news n JOIN users u ON n.author_id = u.id WHERE n.is_published = 1 ORDER BY n.created_at DESC LIMIT ? OFFSET ?',
            (limit, offset)
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_by_id(news_id):
        db = get_db()
        row = db.execute(
            'SELECT n.*, u.full_name as author_name FROM news n JOIN users u ON n.author_id = u.id WHERE n.id = ?',
            (news_id,)
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def get_all():
        db = get_db()
        rows = db.execute(
            'SELECT n.*, u.full_name as author_name FROM news n JOIN users u ON n.author_id = u.id ORDER BY n.created_at DESC'
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def create(title, content, author_id):
        db = get_db()
        db.execute('INSERT INTO news (title,content,author_id) VALUES (?,?,?)', (title, content, author_id))
        db.commit()

    @staticmethod
    def publish(news_id):
        db = get_db()
        db.execute('UPDATE news SET is_published = 1 WHERE id = ?', (news_id,))
        db.commit()

    @staticmethod
    def delete(news_id):
        db = get_db()
        db.execute('DELETE FROM news WHERE id = ?', (news_id,))
        db.commit()


class GradeModel:
    @staticmethod
    def get_student_grades(student_id, subject_id=None):
        db = get_db()
        q = """SELECT g.*, s.name as subject_name, u.full_name as teacher_name
               FROM grades g
               JOIN subjects s ON g.subject_id = s.id
               JOIN users u ON g.teacher_id = u.id
               WHERE g.student_id = ?"""
        params = [student_id]
        if subject_id:
            q += ' AND g.subject_id = ?'
            params.append(subject_id)
        q += ' ORDER BY g.date DESC'
        rows = db.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def add_grade(student_id, subject_id, teacher_id, grade, grade_type, comment, date):
        db = get_db()
        db.execute(
            'INSERT INTO grades (student_id,subject_id,teacher_id,grade,grade_type,comment,date) VALUES (?,?,?,?,?,?,?)',
            (student_id, subject_id, teacher_id, grade, grade_type, comment, date)
        )
        db.commit()


class AttendanceModel:
    @staticmethod
    def get_student_attendance(student_id, class_id=None):
        db = get_db()
        q = """SELECT a.*, s.name as subject_name
               FROM attendance a
               JOIN subjects s ON a.subject_id = s.id
               WHERE a.student_id = ?"""
        params = [student_id]
        if class_id:
            q += ' AND a.class_id = ?'
            params.append(class_id)
        q += ' ORDER BY a.date DESC'
        rows = db.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def mark_attendance(student_id, class_id, subject_id, date, status, note=''):
        db = get_db()
        db.execute(
            'INSERT OR REPLACE INTO attendance (student_id,class_id,subject_id,date,status,note) VALUES (?,?,?,?,?,?)',
            (student_id, class_id, subject_id, date, status, note)
        )
        db.commit()
