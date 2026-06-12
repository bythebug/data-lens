import { useState, useEffect, useCallback } from 'react';
import { api } from '../api';
import FilterPanel from './FilterPanel';

const PAGE_SIZE = 20;

export default function DataTable({ id, columns }) {
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [searchQ, setSearchQ] = useState('');
  const [filters, setFilters] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [mode, setMode] = useState('query'); // 'query' | 'search'

  const colNames = columns.map(c => c.column_name);

  const fetchData = useCallback(async () => {
    setLoading(true); setError('');
    try {
      let res;
      if (mode === 'search' && searchQ.trim()) {
        res = await api.search(id, searchQ, { page, pageSize: PAGE_SIZE });
        setRows(res.rows.map(r => ({ id: r.id, ...r.data })));
      } else {
        res = await api.query(id, { filters, page, pageSize: PAGE_SIZE });
        setRows(res.rows.map(r => ({ id: r.id, ...r.data })));
      }
      setTotal(res.total);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [id, mode, searchQ, filters, page]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const handleSearch = (e) => {
    e.preventDefault();
    setMode(searchQ.trim() ? 'search' : 'query');
    setPage(1);
  };

  const handleFiltersChange = (newFilters) => {
    setFilters(newFilters);
    setMode('query');
    setSearchQ('');
    setPage(1);
  };

  return (
    <div>
      {/* Search bar */}
      <form onSubmit={handleSearch} style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
        <input
          className="input"
          style={{ maxWidth: 380 }}
          placeholder='Full-text search: "phrase", word1 OR word2, -exclude'
          value={searchQ}
          onChange={e => { setSearchQ(e.target.value); if (!e.target.value) { setMode('query'); setPage(1); } }}
        />
        <button className="btn-secondary" type="submit">Search</button>
        {mode === 'search' && <button className="btn-ghost" type="button" onClick={() => { setSearchQ(''); setMode('query'); setPage(1); }}>✕ Clear</button>}
      </form>

      {/* Filter panel */}
      <FilterPanel columns={columns} onChange={handleFiltersChange} />

      {error && <div className="error-banner">{error}</div>}

      {/* Table */}
      {loading
        ? <div className="spinner" />
        : rows.length === 0
          ? <div className="empty"><div className="empty-icon">🔍</div><h3>No results</h3><p>Try adjusting your search or filters.</p></div>
          : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {colNames.map(c => <th key={c}>{c}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, i) => (
                    <tr key={row.id ?? i}>
                      {colNames.map(c => (
                        <td key={c} style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {row[c] === null || row[c] === undefined ? <span style={{ color: 'var(--muted)' }}>—</span> : String(row[c])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
      }

      {/* Pagination */}
      {total > 0 && (
        <div className="pagination">
          <span>{total.toLocaleString()} rows · page {page} of {totalPages}</span>
          <div className="pagination-btns">
            <button className="btn-secondary" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>← Prev</button>
            <button className="btn-secondary" disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>Next →</button>
          </div>
        </div>
      )}
    </div>
  );
}
