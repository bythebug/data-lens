import { useState } from 'react';
import DistributionChart from './charts/DistributionChart';
import CorrelationHeatmap from './charts/CorrelationHeatmap';
import TimeSeriesChart from './charts/TimeSeriesChart';

export default function ChartsTab({ id, numericCols, columns }) {
  const [distCol, setDistCol] = useState(numericCols[0] || '');
  const [buckets, setBuckets] = useState(20);
  const dateCols = columns.filter(c => c.data_type === 'date').map(c => c.column_name);
  const [tsDateCol, setTsDateCol] = useState(dateCols[0] || '');
  const [tsMetricCol, setTsMetricCol] = useState(numericCols[0] || '');
  const [tsPeriod, setTsPeriod] = useState('month');

  if (!numericCols.length) {
    return <div className="empty"><div className="empty-icon">📉</div><h3>No numeric columns</h3><p>Charts require at least one numeric column.</p></div>;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 32 }}>

      {/* Distribution */}
      <section>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
          <h3 style={{ margin: 0, fontSize: 15 }}>Distribution</h3>
          <select className="input" style={{ width: 160 }} value={distCol} onChange={e => setDistCol(e.target.value)}>
            {numericCols.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
          <select className="input" style={{ width: 100 }} value={buckets} onChange={e => setBuckets(+e.target.value)}>
            {[10, 15, 20, 30, 50].map(b => <option key={b} value={b}>{b} bins</option>)}
          </select>
        </div>
        {distCol && <DistributionChart id={id} column={distCol} buckets={buckets} />}
      </section>

      {/* Correlations */}
      {numericCols.length >= 2 && (
        <section>
          <h3 style={{ margin: '0 0 16px', fontSize: 15 }}>Correlation Matrix</h3>
          <CorrelationHeatmap id={id} />
        </section>
      )}

      {/* Time-series */}
      {dateCols.length > 0 && (
        <section>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
            <h3 style={{ margin: 0, fontSize: 15 }}>Time Series</h3>
            <select className="input" style={{ width: 140 }} value={tsDateCol} onChange={e => setTsDateCol(e.target.value)}>
              {dateCols.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
            <select className="input" style={{ width: 140 }} value={tsMetricCol} onChange={e => setTsMetricCol(e.target.value)}>
              {numericCols.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
            <select className="input" style={{ width: 120 }} value={tsPeriod} onChange={e => setTsPeriod(e.target.value)}>
              {['day','week','month','quarter','year'].map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
          <TimeSeriesChart id={id} dateColumn={tsDateCol} metricColumn={tsMetricCol} period={tsPeriod} />
        </section>
      )}
    </div>
  );
}
