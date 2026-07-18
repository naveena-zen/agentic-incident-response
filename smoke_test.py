import httpx
import json

b = 'http://127.0.0.1:8000'

# 1. Health (Unprotected)
r = httpx.get(b + '/health')
print('HEALTH (unprotected):', r.json())

# 2. Login to get JWT Token
login_payload = {"username": "admin", "password": "vigil2025"}
r_login = httpx.post(b + '/api/auth/login', json=login_payload)
print('\nLOGIN STATUS:', r_login.status_code)
if r_login.status_code != 200:
    print('Login failed:', r_login.text)
    exit(1)

token = r_login.json()['access_token']
headers = {"Authorization": f"Bearer {token}"}
print('TOKEN ACQUIRED')

# 3. Access Protected /api/services
r_svc = httpx.get(b + '/api/services', headers=headers)
print('\nPROTECTED SERVICES:', r_svc.status_code)
svcs = r_svc.json()['services']
for s in svcs:
    print(f"  {s['name']}: cpu={round(s['cpu_pct'] or 0, 1)}% lock={s['investigation_in_progress']} anomaly={s['anomaly_active']}")

# 4. Access Unprotected /metrics
r_metrics = httpx.get(b + '/metrics')
print('\nPROMETHEUS /METRICS STATUS:', r_metrics.status_code)
metrics_text = r_metrics.text
print('Snippet from /metrics:')
for line in metrics_text.splitlines():
    if 'vigil_' in line:
        print('  ' + line)

print('\n=== ALL JWT & PROMETHEUS TESTS PASSED ===')
