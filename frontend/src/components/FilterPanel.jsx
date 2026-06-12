import { useState } from 'react';

const OPS = ['=', '!=', '>', '<', '>=', '<=', 'IN', 'ILIKE', 'IS NULL', 'IS NOT NULL'];

function emptyFilter(columns) {
  return { column: columns[0]?.column_name || '', operator: '=', value: '' };
}

export default function FilterPanel({ columns, onChange }) {
  const [filters, setFilters] = useState([]);
  const [logic, setLogic] = useState('AND');
  const [open, setOpen] = useState(false);

  const update = (newFilters, newLogic = logic) => {
    setFilters(newFilters);
    const active = newFilters.filter(f => {
      if (['IS NULL', 'IS NOT NULL'].includes(f.operator)) return f.column;
      return f.column && f.value !== '';
    });
    const payload = active.map(f => ({
      column: f.column,
      operator: f.operator,
      value: f.operator === 'IN' ? f.value.split(',').map(v => v.trim()).filter(Boolean)
           : ['IS NULL', 'IS NOT NULL'].includes(f.operator) ? undefined
           : f.value,
    }));
    onChange(payload, newLogic);
  };

  const add = () => { const next = [...filters, emptyFilter(columns)]; setFilters(next); setOpen(true); };
  const remove = (i) => { const next = filters.filter((_, idx) => idx !== i); update(next); };
  const change = (i, field, val) => { const next = filters.map((f, idx) => idx === i ? { ...f, [field]: val } : f); update(next); };
  const changeLogic = (l) => { setLogic(l); update(filters, l); };

  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: filters.length && open ? 10 : 0 }}>
        <button className="btn-secondary" type="button" style={{ fontSize: 12 }} onClick={add}>
          + Add Filter
        </button>
        {filters.length > 1 && (
          <select className="input" style={{ width: 'auto' }} value={logic} onChange={e => changeLogic(e.target.value)}>
            <option value="AND">AND</option>
            <option value="OR">OR</option>
          </select>
        )}
        {filters.length > 0 && (
          <button className="btn-ghost" style={{ fontSize: 12 }} onClick={() => { setFilters([]); onChange([]); }}>
            Clear all
          </button>
        )}
      </div>

      {open && filters.map((f, i) => (
        <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 6, flexWrap: 'wrap' }}>
          <select className="input" style={{ width: 140 }} value={f.column} onChange={e => change(i, 'column', e.target.value)}>
            {columns.map(c => <option key={c.column_name} value={c.column_name}>{c.column_name}</option>)}
          </select>
          <select className="input" style={{ width: 130 }} value={f.operator} onChange={e => change(i, 'operator', e.target.value)}>
            {OPS.map(op => <option key={op} value={op}>{op}</option>)}
          </select>
          {!['IS NULL', 'IS NOT NULL'].includes(f.operator) && (
            <input
              className="input"
              style={{ width: 160 }}
              placeholder={f.operator === 'IN' ? 'a, b, c' : 'value'}
              value={f.value}
              onChange={e => change(i, 'value', e.target.value)}
            />
          )}
          <button className="btn-ghost" style={{ color: 'var(--danger)' }} onClick={() => remove(i)}>✕</button>
        </div>
      ))}
    </div>
  );
}
