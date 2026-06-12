const BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const USER_ID = '1';

async function req(path, opts = {}) {
  const res = await fetch(`${BASE}${path}`, {
    ...opts,
    headers: { 'X-User-Id': USER_ID, ...opts.headers },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  uploadDataset: (name, file) => {
    const fd = new FormData();
    fd.append('name', name);
    fd.append('file', file);
    return req('/datasets', { method: 'POST', body: fd });
  },

  listDatasets: () => req('/datasets'),

  datasetInfo: (id) => req(`/datasets/${id}/info`),

  search: (id, q, { columns, page = 1, pageSize = 20 } = {}) => {
    const p = new URLSearchParams({ q, page, page_size: pageSize });
    if (columns) p.set('columns', columns);
    return req(`/datasets/${id}/search?${p}`);
  },

  query: (id, { filters = [], logic = 'AND', sortBy, sortDir = 'ASC', page = 1, pageSize = 20 } = {}) => {
    const p = new URLSearchParams({ page, page_size: pageSize, logic });
    if (filters.length) p.set('filters', JSON.stringify(filters));
    if (sortBy) { p.set('sort_by', sortBy); p.set('sort_dir', sortDir); }
    return req(`/datasets/${id}/query?${p}`);
  },

  distribution: (id, column, buckets = 20) =>
    req(`/datasets/${id}/distribution/${column}?buckets=${buckets}`),

  correlations: (id) => req(`/datasets/${id}/correlations`),

  columnStats: (id, column) => req(`/datasets/${id}/stats/${column}`),

  outliers: (id, column) => req(`/datasets/${id}/outliers/${column}`),
};
