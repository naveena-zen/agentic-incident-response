import { useState, useCallback } from 'react';

export function useToasts() {
  const [toasts, setToasts] = useState([]);

  const addToast = useCallback((t) => {
    const id = Date.now() + Math.random();
    setToasts(prev => [...prev, { ...t, id }]);
    setTimeout(() => setToasts(prev => prev.filter(x => x.id !== id)), 6000);
  }, []);

  const removeToast = useCallback((id) => {
    setToasts(prev => prev.filter(x => x.id !== id));
  }, []);

  return { toasts, addToast, removeToast };
}

export function ToastContainer({ toasts, removeToast }) {
  return (
    <div className="toast-container">
      {toasts.map(t => (
        <div key={t.id} className={`toast ${t.type || ''}`}>
          <div style={{ flex: 1 }}>
            <div className="toast-title">{t.title}</div>
            {t.body && <div className="toast-body">{t.body}</div>}
          </div>
          <span className="toast-close" onClick={() => removeToast(t.id)}>×</span>
        </div>
      ))}
    </div>
  );
}
