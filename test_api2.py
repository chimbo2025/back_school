import urllib.request
import json

# Test single grade detail
url = 'http://localhost:8000/api/grades/grade-1/'
response = urllib.request.urlopen(url)
grade = json.load(response)
print(f"Grade: {grade['name']}")
print(f"  fee_groups: {grade.get('fee_groups', [])}")

# Test finance summary
url2 = 'http://localhost:8000/api/finance/summary/'
response2 = urllib.request.urlopen(url2)
finance = json.load(response2)
print(f"\nFinance summary: {finance['summary']}")

# Test student detail
url3 = 'http://localhost:8000/api/students/stu-1/'
response3 = urllib.request.urlopen(url3)
student = json.load(response3)
print(f"\nStudent: {student['first_name']} {student['last_name']}")
print(f"  parent_relationship: {student.get('parent_relationship')}")
print(f"  parent_national_id: {student.get('parent_national_id')}")
print(f"  emergency_contact_name: {student.get('emergency_contact_name')}")
print(f"  emergency_contact_phone: {student.get('emergency_contact_phone')}")
print(f"  emergency_contact_relationship: {student.get('emergency_contact_relationship')}")
print(f"  payments: {len(student.get('payments', []))}")
print(f"  expected_fees: {student.get('expected_fees')}")
print(f"  total_paid: {student.get('total_paid')}")
print(f"  outstanding: {student.get('outstanding')}")