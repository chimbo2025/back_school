import urllib.request
import urllib.parse
import json

# Test login
url = 'http://localhost:8000/api/auth/login/'
data = urllib.parse.urlencode({'username': 'admin', 'password': 'admin123'}).encode()
req = urllib.request.Request(url, data=data, headers={
    'Content-Type': 'application/x-www-form-urlencoded',
    'Origin': 'http://localhost:3000'
})
try:
    resp = urllib.request.urlopen(req)
    print('Login Status:', resp.status)
    for k, v in resp.headers.items():
        if 'access' in k.lower() or 'origin' in k.lower() or 'allow' in k.lower() or 'vary' in k.lower():
            print('  ' + k + ': ' + v)
except urllib.error.HTTPError as e:
    print('Error:', e.code, e.read().decode())
except Exception as e:
    print('Exception:', e)