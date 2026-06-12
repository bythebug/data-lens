import { useState, useEffect } from 'react';
import { api } from '../api';

function fmt(v) {
  if (v === null || v === undefined) return '—';
  return typeof v === 'number' ? v.toLocaleString(undefined, { maximumFractionDigits: 4 }) : v;
}

export default function StatsTab({ id, numericCols }) {
  const [col, setCol] = useState(numericCols[0] || '');
  const [stats, setStats] = useState(null);
  const [outliers, setOutliers] = useState(null);
  const [loading, setLoading] = useState(false);
  const [showOutliers, setShowOutliers] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!col) return;
    setLoading(true); setError(''); setStats(null); setOutliers(null); setShowOutliers(false);
    api.columnStats(id, col)
      .then(setStats)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [id, col]);

  const loadOutliers = async () => {
    try {
      const res = await api.outliers(id, col);
      setOutliers(res);
      setShowOutliers(true);
    } catch(e) { setError(e.message); }
  };

  if (!numericCols.length) return (
    <div className="empty"><div className="empty-icon">📊</div><h3>No numeric columns</h3></div>
  );

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24 }}>
        <span style={{ fontWeight: 500 }}>Column:</span>
        <select className="input" style={{ width: 200 }} value={col} onChange={e => setCol(e.target.value)}>
          {numericCols.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {loading && <div className="spinner" />}

      {stats && (
        <>
          <div className="stat-grid" style={{ marginBottom: 24 }}>
            {[
              ['Count',    stats.count],
              ['Mean',     stats.mean],
              ['Median',   stats.median],
              ['Std Dev',  stats.std],
              ['Min',      stats.min],
              ['Max',      stats.max],
              ['Skewness', stats.skewness],
              ['Kurtosis', stats.kurtosis],
            ].map(([label, value]) => (
              <div className="stat-card" key={label}>
                <div className="stat-label">{label}</div>
                <div className="stat-value" style={{ fontSize: 18 }}>{fmt(value)}</div>
              </div>
            ))}
          </div>

          {stats.quantiles && (
            <>
              <h4 style={{ margin: '0 0 12px', fontSize: 14, color: 'var(--muted)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '.05em' }}>Percentiles</h4>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 24 }}>
                {[['P25', 'p25'], ['P50', 'p50'], ['P75', 'p75'], ['P90', 'p90'], ['P99', 'p99']].map(([label, key]) => (
                  <div className="card" key={key} style={{ padding: '10px 18px', textAlign: 'center' }}>
                    <div style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 500 }}>{label}</div>
                    <div style={{ fontWeight: 600, marginTop: 4 }}>{fmt(stats.quantiles[key])}</div>
                  </div>
                ))}
              </div>
            </>
          )}

          <button className="btn-secondary" onClick={loadOutliers} style={{ marginBottom: 16 }}>
            🔍 Detect Outliers (IQR method)
          </button>

          {showOutliers && outliers && (
            <div className="card" style={{ padding: 16 }}>
              <div style={{ display: 'flex', gap: 20, marginBottom: 12, flexWrap: 'wrap' }}>
                <span><strong>Q1:</strong> {fmt(outliers.q1)}</span>
                <span><strong>Q3:</strong> {fmt(outliers.q3)}</span>
                <span><strong>IQR:</strong> {fmt(outliers.iqr)}</span>
                <span><strong>Lower fence:</strong> {fmt(outliers.lower_fence)}</span>
                <span><strong>Upper fence:</strong> {fmt(outliers.upper_fence)}</span>
                <span className={`badge ${outliers.outlier_count > 0 ? 'badge-blue' : 'badge-green'}`}>
                  {outliers.outlier_count} outliers
                </span>
              </div>

              {outliers.outliers.length > 0 && (
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Row ID</th><th>Value</th></tr></thead>
                    <tbody>
                      {outliers.outliers.slice(0, 50).map(o => (
                        <tr key={o.id}><td>{o.id}</td><td>{o.value}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
