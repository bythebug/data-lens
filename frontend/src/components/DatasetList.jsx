import { useState, useEffect } from 'react';
import { api } from '../api';

export default function DatasetList({ onSelect }) {
  const [datasets, setDatasets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    api.listDatasets()
      .then(setDatasets)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="spinner" />;
  if (error) return <div className="error-banner">{error}</div>;

  if (!datasets.length) return (
    <div className="empty">
      <div className="empty-icon">📊</div>
      <h3>No datasets yet</h3>
      <p>Upload a CSV to get started.</p>
    </div>
  );

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Datasets <span style={{ color: 'var(--muted)', fontWeight: 400, fontSize: 14 }}>({datasets.length})</span></h2>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 14 }}>
        {datasets.map((ds) => (
          <button
            key={ds.dataset_id}
            className="card"
            onClick={() => onSelect(ds.dataset_id)}
            style={{ padding: '18px 20px', text: 'left', textAlign: 'left', cursor: 'pointer', border: '1px solid var(--border)', background: 'var(--surface)', borderRadius: 'var(--radius)', transition: 'box-shadow .15s' }}
            onMouseOver={(e) => e.currentTarget.style.boxShadow = '0 4px 12px rgba(0,0,0,.1)'}
            onMouseOut={(e) => e.currentTarget.style.boxShadow = 'var(--shadow)'}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
              <span style={{ fontWeight: 600, fontSize: 15, color: 'var(--text)' }}>{ds.name}</span>
              <span className="badge badge-blue">CSV</span>
            </div>
            <div style={{ color: 'var(--muted)', fontSize: 13 }}>
              <span>🗂 {ds.row_count.toLocaleString()} rows</span>
            </div>
            <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 6 }}>
              {new Date(ds.created_at).toLocaleDateString()}
            </div>
          </button>
        ))}
      </div>
    </>
  );
}
