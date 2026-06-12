import { useState, useEffect } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { api } from '../../api';

export default function TimeSeriesChart({ id, dateColumn, metricColumn, period }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!dateColumn || !metricColumn) return;
    setLoading(true); setError(''); setData(null);

    // Use the aggregate endpoint in time-series mode
    const params = new URLSearchParams({
      group_by: dateColumn,
      metrics: `SUM(${metricColumn})`,
      time_column: dateColumn,
      time_truncate: period,
    });

    fetch(`http://localhost:8000/datasets/${id}/aggregate?${params}`, {
      headers: { 'X-User-Id': '1' },
    })
      .then(r => r.json())
      .then(res => {
        const alias = `sum_${metricColumn}`;
        setData((res.rows || []).map(r => ({
          period: String(r.period).slice(0, 10),
          value: r[alias] ?? 0,
        })));
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [id, dateColumn, metricColumn, period]);

  if (loading) return <div className="spinner" />;
  if (error) return <div className="error-banner">{error}</div>;
  if (!data?.length) return (
    <div className="empty"><div className="empty-icon">📈</div><h3>No time-series data</h3><p>Ensure the date column has valid date values.</p></div>
  );

  return (
    <div className="card" style={{ padding: 16 }}>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data} margin={{ top: 4, right: 16, bottom: 24, left: 16 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey="period" tick={{ fontSize: 11, fill: 'var(--muted)' }} angle={-30} textAnchor="end" />
          <YAxis tick={{ fontSize: 11, fill: 'var(--muted)' }} />
          <Tooltip
            contentStyle={{ fontSize: 12, borderRadius: 6, border: '1px solid var(--border)' }}
            formatter={(v) => [v.toLocaleString(undefined, { maximumFractionDigits: 2 }), `SUM(${metricColumn})`]}
          />
          <Line type="monotone" dataKey="value" stroke="#2563eb" strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} name={`SUM(${metricColumn})`} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
