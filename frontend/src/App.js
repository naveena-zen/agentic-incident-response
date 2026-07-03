import { useState, useEffect, useRef, useCallback } from 'react';
import { api } from './api';
import { MiniChart } from './MiniChart';
import { ToastContainer } from './Toast';

// ── Helpers ────────────────────────────────────────────────────────────────────
function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
function fmtDur(a, b) {
  if (!a || !b) return '';
  const s = Math.round((new Date(b) - new Date(a)) / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}
function confidenceColor(c) {
  if (c == null) return '#64748b';
  if (c >= 80) return '#22c55e';
  if (c >= 50) return '#f59e0b';
  return '#ef4444';
}
function statusDotClass(svc) {
  if (svc.investigation_in_progress) return 'amber';
  if (svc.anomaly_active) return 'red';
  return 'green';
}

// ── Login Screen Component ─────────────────────────────────────────────────────
function LoginScreen({ onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const data = await api.login(username, password);
      onLogin(data.access_token);
    } catch (err) {
      setError(err.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-wrap">
      <div className="login-card">
        <div className="login-logo">
          <div className="icon-big">🛡</div>
          <h1>Vigil Console</h1>
          <p>Autonomous Incident Response Agent</p>
        </div>
        
        {error && <div className="login-error">{error}</div>}
        
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label className="form-label">Username</label>
            <input
              type="text"
              className="form-input"
              value={username}
              onChange={e => setUsername(e.target.value)}
              placeholder="e.g. admin"
              required
            />
          </div>
          <div className="form-group">
            <label className="form-label">Password</label>
            <input
              type="password"
              className="form-input"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="e.g. vigil2025"
              required
            />
          </div>
          <button type="submit" className="btn-login" disabled={loading}>
            {loading ? 'Authenticating...' : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  );
}

// ── Confidence Bar ─────────────────────────────────────────────────────────────
function ConfidenceBar({ value }) {
  const pct = value ?? 0;
  const color = confidenceColor(pct);
  return (
    <div className="confidence-bar">
      <div className="confidence-label">Agent Confidence</div>
      <div className="confidence-track">
        <div className="confidence-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <div className="confidence-value" style={{ color }}>{pct.toFixed(0)}%</div>
    </div>
  );
}

// ── Similar Incidents ──────────────────────────────────────────────────────────
function SimilarIncidents({ items = [] }) {
  if (!items.length) return null;
  return (
    <div className="similar-incidents">
      <div className="step-label" style={{ marginBottom: 8 }}>📚 Similar past incidents (RAG)</div>
      {items.slice(0, 3).map((it, i) => (
        <div className="similar-item" key={i}>
          <span className="sim-score">{((it.similarity ?? 0) * 100).toFixed(0)}% match</span>
          <div className="sim-title">{it.title}</div>
          <div className="sim-action">→ {it.action}</div>
        </div>
      ))}
    </div>
  );
}

// ── Incident Timeline ──────────────────────────────────────────────────────────
function IncidentTimeline({ inc, onApprove, approving }) {
  const { tool_call_trace = [], similar_incidents = [] } = inc;
  const tools = tool_call_trace.slice(0, 8);

  return (
    <div className="timeline">
      {tools.map((tc, i) => (
        <div className="timeline-step" key={i}>
          <div className="step-icon">
            {tc.tool === 'get_metrics'            ? '📊'
           : tc.tool === 'get_logs'               ? '📋'
           : tc.tool === 'get_recent_deploys'     ? '🚀'
           : tc.tool === 'search_similar_incidents'? '🔍'
           : '⚙️'}
          </div>
          <div className="step-content">
            <div className="step-label">Tool call — {tc.tool}</div>
            <div className="step-text" style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>
              {JSON.stringify(tc.args)}
            </div>
          </div>
        </div>
      ))}

      {similar_incidents.length > 0 && (
        <div className="timeline-step">
          <div className="step-icon">🧠</div>
          <div className="step-content">
            <SimilarIncidents items={similar_incidents} />
          </div>
        </div>
      )}

      {inc.root_cause_hypothesis && (
        <div className="timeline-step">
          <div className="step-icon">⚠️</div>
          <div className="step-content">
            <div className="step-label">Root cause hypothesis</div>
            <div className="step-text highlight">{inc.root_cause_hypothesis}</div>
            <ConfidenceBar value={inc.confidence} />
            {inc.reasoning && (
              <details style={{ marginTop: 8 }}>
                <summary style={{ fontSize: 11, color: 'var(--text-muted)', cursor: 'pointer' }}>Show reasoning</summary>
                <div className="step-text" style={{ marginTop: 6, whiteSpace: 'pre-wrap' }}>{inc.reasoning}</div>
              </details>
            )}
            {inc.referenced_similar_incident && (
              <div style={{ marginTop: 8 }}>
                <span className="tag purple">📌 Referenced: {inc.referenced_similar_incident}</span>
              </div>
            )}
          </div>
        </div>
      )}

      {inc.status !== 'investigating' && (
        <div className="timeline-step">
          <div className="step-icon">
            {inc.status === 'resolved_auto' ? '✅'
           : inc.status === 'paged'         ? '📧'
           : inc.status === 'approved'      ? '✅'
           : '⏳'}
          </div>
          <div className="step-content">
            <div className="step-label">
              {inc.status === 'resolved_auto' ? 'Policy engine — auto-resolved'
             : inc.status === 'paged'         ? 'Policy engine — paged human'
             : inc.status === 'approved'      ? 'Human approved → action executed'
             : 'Status'}
            </div>
            {inc.action_detail && (
              <div className="step-text" style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                {inc.action_detail.slice(0, 300)}
              </div>
            )}
            {inc.decided_at && (
              <div className="step-text" style={{ marginTop: 4, fontSize: 11, color: 'var(--text-muted)' }}>
                Decision at {fmtTime(inc.decided_at)}
                {inc.phase1_completed_at && ` • Phase 1 took ${fmtDur(inc.created_at, inc.phase1_completed_at)}`}
              </div>
            )}
          </div>
        </div>
      )}

      {inc.status === 'paged' && (
        <div className="timeline-step">
          <div className="step-icon">👤</div>
          <div className="step-content">
            <div className="step-label">Awaiting human approval</div>
            <div className="step-text" style={{ marginBottom: 10, color: 'var(--amber)' }}>
              Agent recommends: restart or rollback. Confidence: {(inc.confidence ?? 0).toFixed(0)}%
            </div>
            <button
              className="btn btn-approve"
              disabled={approving}
              onClick={() => onApprove(inc.id)}
            >
              {approving ? <><span className="spinner" /> Executing…</> : '✅ Approve action'}
            </button>
          </div>
        </div>
      )}

      {inc.status === 'approved' && (
        <div className="timeline-step">
          <div className="step-icon">✅</div>
          <div className="step-content">
            <div className="step-label">Approved by {inc.approved_by}</div>
            <div className="step-text">Action executed directly via Python (not LLM). Approved at {fmtTime(inc.approved_at)}</div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Incident Card ──────────────────────────────────────────────────────────────
function IncidentCard({ inc, onApprove, approving }) {
  const [open, setOpen] = useState(inc.status === 'paged' || inc.status === 'investigating');

  const cardClass = `incident-card ${
    inc.status === 'paged'         ? 'paged'
  : inc.status === 'resolved_auto' ? 'resolved'
  : inc.status === 'approved'      ? 'approved'
  : 'investigating'
  }`;

  return (
    <div className={cardClass}>
      <div className="incident-header" onClick={() => setOpen(o => !o)}>
        <div>
          <div className="incident-title-row">
            <span className={`status-badge ${inc.status}`}>{inc.status.replace('_', ' ')}</span>
            <span className="incident-service" style={{ fontFamily: 'var(--font-mono)' }}>{inc.service}</span>
          </div>
          <div className="incident-meta" style={{ marginTop: 4 }}>
            <span>{fmtTime(inc.created_at)}</span>
            {inc.confidence != null && (
              <span style={{ color: confidenceColor(inc.confidence) }}>
                {inc.confidence.toFixed(0)}% confidence
              </span>
            )}
            {inc.action_taken && <span className="tag blue">{inc.action_taken}</span>}
          </div>
        </div>
        <svg className={`chevron ${open ? 'open' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </div>

      {open && (
        <div className="incident-body">
          <IncidentTimeline inc={inc} onApprove={onApprove} approving={approving === inc.id} />
        </div>
      )}
    </div>
  );
}

// ── Service Card ───────────────────────────────────────────────────────────────
function ServiceCard({ svc, metrics }) {
  const dotClass = statusDotClass(svc);
  const cardClass = `service-card ${svc.anomaly_active ? 'anomaly' : ''} ${svc.investigation_in_progress ? 'investigating' : ''}`;

  const latData = (metrics || []).map(m => ({ timestamp: m.timestamp, value: m.latency_ms })).filter(d => d.value != null);
  const errData = (metrics || []).map(m => ({ timestamp: m.timestamp, value: (m.error_rate || 0) * 100 }));

  const latColor = (svc.latency_ms || 0) > 500 ? '#ef4444' : (svc.latency_ms || 0) > 300 ? '#f59e0b' : '#22c55e';
  const errColor = (svc.error_rate || 0) > 0.05 ? '#ef4444' : (svc.error_rate || 0) > 0.02 ? '#f59e0b' : '#22c55e';

  return (
    <div className={cardClass}>
      <div className="svc-header">
        <div className="svc-name-row">
          <div className={`status-dot ${dotClass}`} />
          <span className="svc-name">{svc.name}</span>
          {!svc.is_simulated && <span className="svc-badge real">real</span>}
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          {svc.investigation_in_progress && <span className="svc-badge invest">🔍 investing</span>}
          {svc.anomaly_active && !svc.investigation_in_progress && <span className="svc-badge anomaly">⚠ anomaly</span>}
          {!svc.anomaly_active && !svc.investigation_in_progress && <span className="svc-badge healthy">healthy</span>}
        </div>
      </div>

      <div className="svc-metrics">
        <div className="metric-pill">
          <div className="label">CPU</div>
          <div className={`value ${(svc.cpu_pct || 0) > 85 ? 'crit' : (svc.cpu_pct || 0) > 70 ? 'warn' : 'ok'}`}>
            {svc.cpu_pct != null ? `${svc.cpu_pct.toFixed(1)}%` : '—'}
          </div>
        </div>
        <div className="metric-pill">
          <div className="label">Memory</div>
          <div className={`value ${(svc.memory_pct || 0) > 90 ? 'crit' : (svc.memory_pct || 0) > 75 ? 'warn' : 'ok'}`}>
            {svc.memory_pct != null ? `${svc.memory_pct.toFixed(1)}%` : '—'}
          </div>
        </div>
        {svc.latency_ms != null && (
          <div className="metric-pill">
            <div className="label">Latency</div>
            <div className={`value ${(svc.latency_ms || 0) > 500 ? 'crit' : (svc.latency_ms || 0) > 300 ? 'warn' : 'ok'}`}>
              {svc.latency_ms.toFixed(0)}ms
            </div>
          </div>
        )}
        {svc.error_rate != null && (
          <div className="metric-pill">
            <div className="label">Error Rate</div>
            <div className={`value ${(svc.error_rate || 0) > 0.05 ? 'crit' : (svc.error_rate || 0) > 0.02 ? 'warn' : 'ok'}`}>
              {((svc.error_rate || 0) * 100).toFixed(2)}%
            </div>
          </div>
        )}
      </div>

      {latData.length > 2 && (
        <div className="chart-container">
          <MiniChart data={latData} color={latColor} label="Latency ms" />
        </div>
      )}
      {errData.length > 2 && svc.is_simulated && (
        <div className="chart-container" style={{ marginTop: 6 }}>
          <MiniChart data={errData} color={errColor} label="Error %" />
        </div>
      )}
    </div>
  );
}

// ── Main App ──────────────────────────────────────────────────────────────────
export default function App() {
  const [token, setToken]         = useState(localStorage.getItem('vigil_token'));
  const [services, setServices]   = useState([]);
  const [metricsMap, setMetrics]  = useState({});
  const [incidents, setIncidents] = useState([]);
  const [approving, setApproving] = useState(null);
  const [toasts, setToasts]       = useState([]);
  const incidentIds = useRef(new Set());

  const handleLogin = (jwt) => {
    localStorage.setItem('vigil_token', jwt);
    setToken(jwt);
  };

  const handleLogout = () => {
    localStorage.removeItem('vigil_token');
    setToken(null);
  };

  const addToast = useCallback((t) => {
    const id = Date.now() + Math.random();
    setToasts(prev => [...prev, { ...t, id }]);
    setTimeout(() => setToasts(prev => prev.filter(x => x.id !== id)), 6000);
  }, []);

  // Poll services + metrics every 4s
  useEffect(() => {
    if (!token) return;
    const fetchAll = async () => {
      try {
        const { services: svcs } = await api.services();
        setServices(svcs || []);

        const maps = {};
        await Promise.all((svcs || []).map(async s => {
          const res = await api.metrics(s.name, 60);
          maps[s.name] = res.metrics || [];
        }));
        setMetrics(maps);
      } catch (err) {
        if (localStorage.getItem('vigil_token') === null) {
          setToken(null);
        }
      }
    };
    fetchAll();
    const t = setInterval(fetchAll, 4000);
    return () => clearInterval(t);
  }, [token]);

  // Poll incidents every 5s — show toast on new ones
  useEffect(() => {
    if (!token) return;
    const fetchInc = async () => {
      try {
        const { incidents: incs } = await api.incidents(30);
        if (!incs) return;

        incs.forEach(inc => {
          if (!incidentIds.current.has(inc.id)) {
            incidentIds.current.add(inc.id);
            addToast({
              title: `🚨 New incident — ${inc.service}`,
              body: `Status: ${inc.status}`,
              type: 'anomaly',
            });
          }
        });
        setIncidents(incs);
      } catch (err) {
        if (localStorage.getItem('vigil_token') === null) {
          setToken(null);
        }
      }
    };
    fetchInc();
    const t = setInterval(fetchInc, 5000);
    return () => clearInterval(t);
  }, [token, addToast]);

  const handleApprove = async (id) => {
    setApproving(id);
    try {
      const res = await api.approve(id);
      if (res.status === 'approved') {
        addToast({ title: '✅ Action approved & executed', body: `Incident ${id.slice(0,8)}`, type: 'resolved' });
        const { incidents: incs } = await api.incidents(30);
        setIncidents(incs || []);
      } else {
        addToast({ title: '❌ Approval failed', body: res.detail || JSON.stringify(res), type: '' });
      }
    } catch (e) {
      addToast({ title: '❌ Network error', body: e.message });
    } finally {
      setApproving(null);
    }
  };

  if (!token) {
    return <LoginScreen onLogin={handleLogin} />;
  }

  const anomalyCount = services.filter(s => s.anomaly_active).length;
  const investCount  = services.filter(s => s.investigation_in_progress).length;

  return (
    <div className="app">
      {/* Topbar */}
      <header className="topbar">
        <div className="topbar-logo">
          <div className="logo-icon">🛡</div>
          Vigil
          <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--text-muted)' }}>Autonomous IR Agent</span>
        </div>
        <div className="topbar-status">
          <div className="pulse-dot" />
          Live
          {anomalyCount > 0 && (
            <span style={{ marginLeft: 12, color: 'var(--red)', fontWeight: 600 }}>
              ⚠ {anomalyCount} anomal{anomalyCount === 1 ? 'y' : 'ies'}
            </span>
          )}
          {investCount > 0 && (
            <span style={{ marginLeft: 12, color: 'var(--amber)', fontWeight: 600 }}>
              🔍 {investCount} investigating
            </span>
          )}
          <span style={{ marginLeft: 16 }}>{services.length} services</span>
          <button
            style={{ marginLeft: 16, background: 'rgba(239,68,68,.15)', color: 'var(--red)', border: '1px solid rgba(239,68,68,.3)', borderRadius: 6, padding: '3px 10px', cursor: 'pointer', fontSize: 12, fontWeight: 600 }}
            onClick={() => api.triggerAnomaly('checkout-api', 'high_latency').then(() => addToast({ title: '💥 Anomaly injected', body: 'checkout-api · high_latency', type: 'anomaly' }))}
          >
            Inject anomaly
          </button>
          <button
            style={{ marginLeft: 12, background: 'rgba(255,255,255,.05)', color: 'var(--text-secondary)', border: '1px solid var(--border)', borderRadius: 6, padding: '3px 10px', cursor: 'pointer', fontSize: 12, fontWeight: 600 }}
            onClick={handleLogout}
          >
            Sign Out
          </button>
        </div>
      </header>

      <main className="main-content">
        {/* Service Grid */}
        <div className="section-title">Service Status</div>
        <div className="service-grid">
          {services.map(s => (
            <ServiceCard key={s.name} svc={s} metrics={metricsMap[s.name]} />
          ))}
        </div>

        {/* Incidents Feed */}
        <div className="section-title">Incidents</div>
        <div className="incidents-section">
          {incidents.length === 0 ? (
            <div className="empty-state">
              <div className="icon">✅</div>
              <div>No incidents yet. Inject an anomaly to see the agent in action.</div>
            </div>
          ) : (
            incidents.map(inc => (
              <IncidentCard
                key={inc.id}
                inc={inc}
                onApprove={handleApprove}
                approving={approving}
              />
            ))
          )}
        </div>
      </main>

      <ToastContainer toasts={toasts} removeToast={id => setToasts(p => p.filter(x => x.id !== id))} />
    </div>
  );
}
