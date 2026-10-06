import urllib.request
import json

# Test students endpoint
url = 'http://localhost:8000/api/students/'
response = urllib.request.urlopen(url)
data = json.load(response)
print(f"Students count: {data['count']}")
if data['results']:
    student = data['results'][0]
    print(f"First student: {student['first_name']} {student['last_name']}")
    print(f"  parent_relationship: {student.get('parent_relationship')}")
    print(f"  parent_national_id: {student.get('parent_national_id')}")
    print(f"  emergency_contact_name: {student.get('emergency_contact_name')}")
    print(f"  emergency_contact_relationship: {student.get('emergency_contact_relationship')}")

# Test grades endpoint
url2 = 'http://localhost:8000/api/grades/'
response2 = urllib.request.urlopen(url2)
grades_data = json.load(response2)
grades = grades_data.get('results', grades_data)
print(f"\nGrades count: {len(grades)}")
if grades:
    grade = grades[0]
    print(f"First grade: {grade['name']}")
    print(f"  fee_groups: {len(grade.get('fee_groups', []))}")