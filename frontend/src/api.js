// api.js — typed API calls to the Vigil backend with JWT authentication
const BASE = process.env.REACT_APP_API_BASE_URL || 'http://localhost:8000';

const getHeaders = () => {
  const token = localStorage.getItem('vigil_token');
  const headers = { 'Content-Type': 'application/json' };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
};

const handleResponse = async (r) => {
  if (r.status === 401) {
    localStorage.removeItem('vigil_token');
    // Force redirect/reload to show login screen
    if (!window.location.pathname.includes('/login')) {
      window.location.reload();
    }
  }
  return r.json();
};

const get = (path) =>
  fetch(`${BASE}${path}`, {
    headers: getHeaders(),
  }).then(handleResponse);

const post = (path, body) =>
  fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: getHeaders(),
    body: body ? JSON.stringify(body) : undefined,
  }).then(handleResponse);

export const api = {
  login:         (username, password) =>
    fetch(`${BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }).then(r => {
      if (!r.ok) throw new Error('Incorrect credentials');
      return r.json();
    }),
  health:        ()           => fetch(`${BASE}/health`).then(r => r.json()),
  services:      ()           => get('/api/services'),
  metrics:       (svc, n=60) => get(`/api/metrics/${svc}?limit=${n}`),
  logs:          (svc, n=20) => get(`/api/logs/${svc}?limit=${n}`),
  deploys:       (svc)       => get(`/api/deploys/${svc}`),
  incidents:     (n=30)      => get(`/api/incidents?limit=${n}`),
  incident:      (id)        => get(`/api/incidents/${id}`),
  pastIncidents: (n=20)      => get('/api/past-incidents?limit=20'),
  approve:       (id)        => post(`/api/incidents/${id}/approve`),
  triggerAnomaly:(svc, type) => post(`/api/debug/trigger-anomaly?service=${svc}&anomaly_type=${type}`),
  triggerInvest: (svc)       => post(`/api/debug/trigger-investigation?service=${svc}&reason=manual+test`),
};
