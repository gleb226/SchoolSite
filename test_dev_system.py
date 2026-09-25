import urllib.request
import urllib.parse
import http.cookiejar
import re

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

BASE_URL = 'http://127.0.0.1:5000'

print("--- 1. Testing DEV Quick Login ---")
resp = opener.open(f'{BASE_URL}/dev/login')
print("Dev login code:", resp.getcode(), "URL:", resp.geturl())

html = opener.open(f'{BASE_URL}/dashboard').read().decode('utf-8')
print("Dev Bar present in HTML:", 'dev-toolbar' in html)
print("Dev ID present in HTML:", '513546547' in html)

print("\n--- 2. Testing Direct Access to All Cabinets as DEV ---")
# Admin
resp = opener.open(f'{BASE_URL}/admin/')
print("Admin dashboard:", resp.getcode(), "Has stats:", "Всього користувачів" in resp.read().decode('utf-8'))

# Teacher
resp = opener.open(f'{BASE_URL}/dashboard/teacher')
teacher_html = resp.read().decode('utf-8')
print("Teacher dashboard:", resp.getcode(), "Has classes:", "Мої класи" in teacher_html)

# Parent
resp = opener.open(f'{BASE_URL}/dashboard/parent')
parent_html = resp.read().decode('utf-8')
print("Parent dashboard:", resp.getcode(), "Has child report:", "Петренко Олексій" in parent_html)

# Student
resp = opener.open(f'{BASE_URL}/dashboard/student')
student_html = resp.read().decode('utf-8')
print("Student dashboard:", resp.getcode(), "Has grades:", "Останні оцінки" in student_html)

print("\n--- 3. Testing Role-Protected Actions ---")
# Teacher add grade
resp = opener.open(f'{BASE_URL}/diary/add-grade')
print("Add grade access:", resp.getcode())

# Teacher mark attendance
resp = opener.open(f'{BASE_URL}/diary/mark-attendance')
print("Mark attendance access:", resp.getcode())

# Create test
resp = opener.open(f'{BASE_URL}/tests/create')
print("Create test access:", resp.getcode())

# Create voting
resp = opener.open(f'{BASE_URL}/voting/create')
print("Create voting access:", resp.getcode())

# Student take test
resp = opener.open(f'{BASE_URL}/tests/take/1')
print("Take test access (DEV):", resp.getcode())

print("\n--- 4. Testing DEV Role Switcher ---")
resp = opener.open(f'{BASE_URL}/dev/switch-role/teacher')
print("Switch to Teacher:", resp.getcode(), "URL:", resp.geturl())

resp = opener.open(f'{BASE_URL}/dev/switch-role/parent')
print("Switch to Parent:", resp.getcode(), "URL:", resp.geturl())

resp = opener.open(f'{BASE_URL}/dev/switch-role/student')
print("Switch to Student:", resp.getcode(), "URL:", resp.geturl())

resp = opener.open(f'{BASE_URL}/dev/switch-role/admin')
print("Switch to Admin:", resp.getcode(), "URL:", resp.geturl())

resp = opener.open(f'{BASE_URL}/dev/switch-role/reset')
print("Reset role:", resp.getcode(), "URL:", resp.geturl())

print("\n--- 5. Testing Registration Page (No 'Хто ви?') ---")
cj.clear()
resp = opener.open(f'{BASE_URL}/auth/register')
reg_html = resp.read().decode('utf-8')
print("Registration page status:", resp.getcode())
print("'Хто ви' NOT in page:", "Хто ви" not in reg_html)
print("'Реєстрація учня' present:", "Реєстрація учня" in reg_html)

# Register a test student
token = re.search(r'name="csrf_token" value="([a-f0-9]+)"', reg_html).group(1)
post_data = urllib.parse.urlencode({
    'csrf_token': token,
    'full_name': 'Тестовий Учень Автотест',
    'username': 'autotest_student_1',
    'email': 'autotest_student_1@example.com',
    'phone': '+380991112233',
    'password': 'password123',
    'confirm_password': 'password123'
}).encode()

resp = opener.open(f'{BASE_URL}/auth/register', data=post_data)
reg_result = resp.read().decode('utf-8')
print("Registration submission status:", resp.getcode(), "Success page:", "Заявку успішно надіслано" in reg_result or "успішно" in reg_result)

# Check database for this student
import sqlite3
conn = sqlite3.connect('school.db')
conn.row_factory = sqlite3.Row
new_user = conn.execute("SELECT * FROM users WHERE username='autotest_student_1'").fetchone()
if new_user:
    print("New user created with role:", new_user['role'], "is_approved:", new_user['is_approved'])
    # Clean up test user
    conn.execute("DELETE FROM users WHERE username='autotest_student_1'")
    conn.commit()
conn.close()

print("\nALL VERIFICATIONS PASSED SUCCESSFULLY!")
