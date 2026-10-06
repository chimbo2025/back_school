import sqlite3
conn = sqlite3.connect('D:/PROJECT/School/backend/db.sqlite3')
cursor = conn.cursor()
cursor.execute('SELECT * FROM django_migrations WHERE app="admissions"')
result = cursor.fetchall()
for r in result:
    print(r)
conn.close()