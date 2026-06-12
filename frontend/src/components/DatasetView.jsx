import { useState, useEffect } from 'react';
import { api } from '../api';
import DataTable from './DataTable';
import ChartsTab from './ChartsTab';
import StatsTab from './StatsTab';

const TABS = ['Data', 'Charts', 'Statistics'];

export default function DatasetView({ id, onBack }) {
  const [info, setInfo] = useState(null);
  const [tab, setTab] = useState('Data');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    api.datasetInfo(id)
      .then(setInfo)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) return <div className="spinner" />;
  if (error) return <div className="error-banner">{error}</div>;

  const numericCols = info.columns.filter(c => c.data_type === 'numeric').map(c => c.column_name);
  const textCols    = info.columns.filter(c => c.data_type === 'text').map(c => c.column_name);

  return (
    <div>
      {/* Breadcrumb */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 20 }}>
        <button className="btn-ghost" onClick={onBack}>← Datasets</button>
        <span style={{ color: 'var(--border)' }}>/</span>
        <span style={{ fontWeight: 600, fontSize: 16 }}>{info.name}</span>
        <span className="badge badge-gray">{info.row_count.toLocaleString()} rows</span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          {info.columns.map(c => (
            <span key={c.column_name} className={`badge ${c.data_type === 'numeric' ? 'badge-blue' : c.data_type === 'date' ? 'badge-green' : 'badge-gray'}`}>
              {c.column_name}
            </span>
          ))}
        </div>
      </div>

      {/* Tabs */}
      <div className="tabs">
        {TABS.map(t => (
          <button key={t} className={`tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>

      {tab === 'Data'       && <DataTable id={id} columns={info.columns} />}
      {tab === 'Charts'     && <ChartsTab id={id} numericCols={numericCols} columns={info.columns} />}
      {tab === 'Statistics' && <StatsTab  id={id} numericCols={numericCols} />}
    </div>
  );
}
