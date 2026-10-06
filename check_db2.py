import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'schoolportal.settings')
django.setup()

from django.db import connection

cursor = connection.cursor()
cursor.execute('SELECT sql FROM sqlite_master WHERE type="table" AND name="admissions_student"')
result = cursor.fetchone()
if result:
    print(result[0])
else:
    print('Table not found')