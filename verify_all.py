import sqlite3
conn = sqlite3.connect('D:/PROJECT/School/backend/db.sqlite3')
cursor = conn.cursor()

# Check grades
cursor.execute('SELECT COUNT(*) FROM grades')
print(f'Grades: {cursor.fetchone()[0]}')

# Check fee_groups
cursor.execute('SELECT COUNT(*) FROM fee_groups')
print(f'Fee Groups: {cursor.fetchone()[0]}')

# Check fee_items
cursor.execute('SELECT COUNT(*) FROM fee_items')
print(f'Fee Items: {cursor.fetchone()[0]}')

# Check payments
cursor.execute('SELECT COUNT(*) FROM payments')
print(f'Payments: {cursor.fetchone()[0]}')

# Check students with all fields
cursor.execute('SELECT id, application_no, first_name, last_name, status, parent_relationship, parent_national_id, emergency_contact_name, emergency_contact_relationship FROM students')
print('\nAll students:')
for row in cursor.fetchall():
    print(f'  {row[0]}: {row[1]} - {row[2]} {row[3]} ({row[4]}) | Parent: {row[5]}, NID: {row[6]} | Emergency: {row[7]} ({row[8]})')

conn.close()