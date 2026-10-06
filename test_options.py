import urllib.request

# Test OPTIONS preflight with Origin
url = 'http://localhost:8000/api/students/'
req = urllib.request.Request(url, method='OPTIONS', headers={
    'Origin': 'http://localhost:3000',
    'Access-Control-Request-Method': 'GET',
    'Access-Control-Request-Headers': 'authorization,content-type'
})
try:
    resp = urllib.request.urlopen(req)
    print('Status:', resp.status)
    for k, v in resp.headers.items():
        print('  ' + k + ': ' + v)
except Exception as e:
    print('Error:', e)