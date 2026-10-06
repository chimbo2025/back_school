import sqlite3
conn = sqlite3.connect('D:/PROJECT/School/backend/db.sqlite3')
cursor = conn.cursor()
cursor.execute('SELECT COUNT(*) FROM students')
print(f'Total students: {cursor.fetchone()[0]}')
cursor.execute('SELECT id, first_name, last_name, parent_relationship, parent_national_id, emergency_contact_name FROM students')
for row in cursor.fetchall():
    print(row)
conn.close()