import sqlite3
conn = sqlite3.connect('D:/PROJECT/School/backend/db.sqlite3')
cursor = conn.cursor()
cursor.execute('SELECT sql FROM sqlite_master WHERE type="table" AND name="students"')
result = cursor.fetchone()
if result:
    print(result[0])
conn.close()