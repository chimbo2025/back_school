import urllib.request
import urllib.parse
import json

# First login
url = 'http://localhost:8000/api/auth/login/'
data = urllib.parse.urlencode({'username': 'admin', 'password': 'admin123'}).encode()
req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/x-www-form-urlencoded'})
try:
    response = urllib.request.urlopen(req)
    tokens = json.load(response)
    access = tokens['access']
    print(f"Login successful, access token: {access[:50]}...")
    
    # Now test /auth/me/
    url2 = 'http://localhost:8000/api/auth/me/'
    req2 = urllib.request.Request(url2, headers={'Authorization': f'Bearer {access}'})
    response2 = urllib.request.urlopen(req2)
    user = json.load(response2)
    print(f"User: {user}")
except urllib.error.HTTPError as e:
    print(f"Error: {e.code} - {e.read().decode()}")
except Exception as e:
    print(f"Exception: {e}")