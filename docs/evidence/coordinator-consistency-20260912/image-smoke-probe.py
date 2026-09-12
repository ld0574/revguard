import json,time,urllib.request,urllib.error,re
base='http://127.0.0.1:9000'
for attempt in range(30):
    try:
        with urllib.request.urlopen(base+'/api/v1/health',timeout=3) as response:
            health=json.load(response)
        if health.get('ready'):break
    except (urllib.error.URLError,TimeoutError):pass
    time.sleep(1)
else:raise AssertionError('API did not start')
assert health['release']=='0.5.10' and health['cases']==8 and not health['maintenance']
with urllib.request.urlopen(base+'/demo/',timeout=5) as response:html=response.read().decode()
assets=re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"',html)
assert assets, 'Built demo assets are missing'
for asset in assets:
    url=base+asset if asset.startswith('/') else base+'/demo/'+asset
    with urllib.request.urlopen(url,timeout=5) as response:assert response.status==200
print(json.dumps({'environment':'10.10.10.202 Docker, isolated SQLite','health':health,'static_assets':len(assets),'passed':True}))
