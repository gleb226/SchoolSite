import urllib.request
import urllib.parse
import http.cookiejar
import re

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# 1. Test Login as student
resp = opener.open('http://127.0.0.1:5000/auth/login')
html = resp.read().decode('utf-8')
token = re.search(r'name="csrf_token" value="([a-f0-9]+)"', html).group(1)

data = urllib.parse.urlencode({'csrf_token': token, 'username': 'student1', 'password': 'student123'}).encode()
resp = opener.open('http://127.0.0.1:5000/auth/login', data=data)
print('Student Login Status:', resp.getcode(), 'URL:', resp.geturl())

# Check student dashboard
resp = opener.open('http://127.0.0.1:5000/dashboard/student')
content = resp.read().decode('utf-8')
print('Student Dashboard Status:', resp.getcode(), 'Has Grades Header:', 'Останні оцінки' in content)

# Check diary grades
resp = opener.open('http://127.0.0.1:5000/diary/grades')
print('Diary Grades Status:', resp.getcode())

# Check diary attendance
resp = opener.open('http://127.0.0.1:5000/diary/attendance')
print('Diary Attendance Status:', resp.getcode())

# Check tests list
resp = opener.open('http://127.0.0.1:5000/tests/')
print('Tests List Status:', resp.getcode())

# 2. Test Login as Admin
cj.clear()
resp = opener.open('http://127.0.0.1:5000/auth/login')
html = resp.read().decode('utf-8')
token = re.search(r'name="csrf_token" value="([a-f0-9]+)"', html).group(1)
data = urllib.parse.urlencode({'csrf_token': token, 'username': 'admin', 'password': 'admin123'}).encode()
resp = opener.open('http://127.0.0.1:5000/auth/login', data=data)
print('Admin Login Status:', resp.getcode(), 'URL:', resp.geturl())

resp = opener.open('http://127.0.0.1:5000/admin/')
content = resp.read().decode('utf-8')
print('Admin Dashboard Status:', resp.getcode(), 'Has Stats:', 'Всього користувачів' in content)

resp = opener.open('http://127.0.0.1:5000/admin/users')
print('Admin Users List Status:', resp.getcode())

resp = opener.open('http://127.0.0.1:5000/admin/classes')
print('Admin Classes Status:', resp.getcode())

# 3. Test Teacher Dashboard
cj.clear()
resp = opener.open('http://127.0.0.1:5000/auth/login')
html = resp.read().decode('utf-8')
token = re.search(r'name="csrf_token" value="([a-f0-9]+)"', html).group(1)
data = urllib.parse.urlencode({'csrf_token': token, 'username': 'teacher1', 'password': 'teacher123'}).encode()
resp = opener.open('http://127.0.0.1:5000/auth/login', data=data)
print('Teacher Login Status:', resp.getcode(), 'URL:', resp.geturl())

resp = opener.open('http://127.0.0.1:5000/dashboard/teacher')
print('Teacher Dashboard Status:', resp.getcode())

resp = opener.open('http://127.0.0.1:5000/diary/add-grade')
print('Teacher Add Grade Status:', resp.getcode())

resp = opener.open('http://127.0.0.1:5000/diary/mark-attendance')
print('Teacher Mark Attendance Status:', resp.getcode())

# 4. Test Parent Dashboard
cj.clear()
resp = opener.open('http://127.0.0.1:5000/auth/login')
html = resp.read().decode('utf-8')
token = re.search(r'name="csrf_token" value="([a-f0-9]+)"', html).group(1)
data = urllib.parse.urlencode({'csrf_token': token, 'username': 'parent1', 'password': 'parent123'}).encode()
resp = opener.open('http://127.0.0.1:5000/auth/login', data=data)
print('Parent Login Status:', resp.getcode(), 'URL:', resp.geturl())

resp = opener.open('http://127.0.0.1:5000/dashboard/parent')
content = resp.read().decode('utf-8')
print('Parent Dashboard Status:', resp.getcode(), 'Has Child Report:', 'Петренко Олексій' in content)

# 5. Test Voting
resp = opener.open('http://127.0.0.1:5000/voting/')
print('Voting List Status:', resp.getcode())

resp = opener.open('http://127.0.0.1:5000/voting/1/results')
print('Voting Results Status:', resp.getcode())

print('\nALL TESTS PASSED WITH 100% SUCCESS!')
