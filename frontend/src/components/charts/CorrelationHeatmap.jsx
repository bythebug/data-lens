import { useState, useEffect } from 'react';
import { api } from '../../api';

function corrColor(r) {
  // r: -1 → blue, 0 → white, 1 → red
  const t = (r + 1) / 2; // 0..1
  if (t >= 0.5) {
    const x = (t - 0.5) * 2;
    return `rgb(${Math.round(255)}, ${Math.round(255 * (1 - x))}, ${Math.round(255 * (1 - x))})`;
  } else {
    const x = t * 2;
    return `rgb(${Math.round(255 * x)}, ${Math.round(255 * x)}, 255)`;
  }
}

function textColor(r) {
  return Math.abs(r) > 0.5 ? '#fff' : 'var(--text)';
}

export default function CorrelationHeatmap({ id }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    api.correlations(id)
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) return <div className="spinner" />;
  if (error) return <div className="error-banner">{error}</div>;
  if (!data?.matrix?.length) return (
    <div className="empty"><div className="empty-icon">📊</div><h3>Need ≥ 2 numeric columns</h3></div>
  );

  const { columns, matrix } = data;
  const n = columns.length;
  const cellSize = Math.max(48, Math.min(72, Math.floor(560 / n)));

  return (
    <div className="card" style={{ padding: 16, overflowX: 'auto', display: 'inline-block', maxWidth: '100%' }}>
      <div style={{ display: 'grid', gridTemplateColumns: `80px repeat(${n}, ${cellSize}px)`, gap: 2, alignItems: 'center' }}>
        {/* header row */}
        <div />
        {columns.map(c => (
          <div key={c} style={{ fontSize: 11, fontWeight: 500, color: 'var(--muted)', textAlign: 'center', padding: '0 2px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c}</div>
        ))}

        {/* data rows */}
        {matrix.map((row) => (
          <>
            <div key={`lbl-${row.column}`} style={{ fontSize: 11, fontWeight: 500, color: 'var(--muted)', textAlign: 'right', paddingRight: 8, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {row.column}
            </div>
            {columns.map(col => {
              const r = row.correlations[col];
              return (
                <div
                  key={col}
                  style={{
                    height: cellSize * 0.75,
                    background: corrColor(r),
                    borderRadius: 4,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 11,
                    fontWeight: 600,
                    color: textColor(r),
                    title: `${row.column} vs ${col}: ${r}`,
                  }}
                  title={`${row.column} × ${col} = ${r}`}
                >
                  {r.toFixed(2)}
                </div>
              );
            })}
          </>
        ))}
      </div>

      {/* Legend */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12, fontSize: 11, color: 'var(--muted)' }}>
        <span>−1</span>
        <div style={{ width: 120, height: 10, borderRadius: 4, background: 'linear-gradient(to right, rgb(0,0,255), white, rgb(255,0,0))' }} />
        <span>+1</span>
        <span style={{ marginLeft: 8 }}>Pearson r</span>
      </div>
    </div>
  );
}
