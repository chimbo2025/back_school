import urllib.request
import urllib.parse
import json

# Test login
url = 'http://localhost:8000/api/auth/login/'
data = urllib.parse.urlencode({'username': 'admin', 'password': 'admin123'}).encode()
req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/x-www-form-urlencoded'})
try:
    response = urllib.request.urlopen(req)
    tokens = json.load(response)
    access = tokens['access']
    print('Login: OK')
    
    # Test GET with auth
    url2 = 'http://localhost:8000/api/students/'
    req2 = urllib.request.Request(url2, headers={'Authorization': 'Bearer ' + access})
    response2 = urllib.request.urlopen(req2)
    data2 = json.load(response2)
    print('Students GET: OK, count=' + str(data2['count']))
except urllib.error.HTTPError as e:
    print('Error: ' + str(e.code) + ' - ' + e.read().decode())
except Exception as e:
    print('Exception: ' + str(e))