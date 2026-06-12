import { useState, useEffect } from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { api } from '../../api';

export default function DistributionChart({ id, column, buckets }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setLoading(true); setError(''); setData(null);
    api.distribution(id, column, buckets)
      .then(res => {
        setData(res.bin_centres.map((centre, i) => ({
          bin: centre > 1000
            ? centre.toLocaleString(undefined, { notation: 'compact', maximumFractionDigits: 1 })
            : Number(centre.toFixed(2)),
          count: res.counts[i],
        })));
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [id, column, buckets]);

  if (loading) return <div className="spinner" />;
  if (error) return <div className="error-banner">{error}</div>;
  if (!data) return null;

  return (
    <div className="card" style={{ padding: 16 }}>
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={data} margin={{ top: 4, right: 16, bottom: 24, left: 16 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey="bin" tick={{ fontSize: 11, fill: 'var(--muted)' }} angle={-30} textAnchor="end" />
          <YAxis tick={{ fontSize: 11, fill: 'var(--muted)' }} />
          <Tooltip
            contentStyle={{ fontSize: 12, borderRadius: 6, border: '1px solid var(--border)' }}
            formatter={(v) => [v.toLocaleString(), 'count']}
          />
          <Bar dataKey="count" fill="#2563eb" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
