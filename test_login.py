import urllib.request
import urllib.parse
import json

# Test login
url = 'http://localhost:8000/api/auth/login/'
data = urllib.parse.urlencode({'username': 'admin', 'password': 'admin123'}).encode()
req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/x-www-form-urlencoded'})
try:
    response = urllib.request.urlopen(req)
    result = json.load(response)
    print("Login successful!")
    print(f"Access token: {result.get('access', 'N/A')[:50]}...")
    print(f"Refresh token: {result.get('refresh', 'N/A')[:50]}...")
except urllib.error.HTTPError as e:
    print(f"Login failed: {e.code} - {e.read().decode()}")