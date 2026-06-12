import { useState, useRef } from 'react';
import { api } from '../api';

export default function Upload({ onDone, onCancel }) {
  const [file, setFile] = useState(null);
  const [name, setName] = useState('');
  const [over, setOver] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const inputRef = useRef();

  const pickFile = (f) => {
    if (!f || !f.name.endsWith('.csv')) { setError('Please select a .csv file'); return; }
    setFile(f);
    setError('');
    if (!name) setName(f.name.replace(/\.csv$/i, ''));
  };

  const handleDrop = (e) => {
    e.preventDefault(); setOver(false);
    pickFile(e.dataTransfer.files[0]);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!file) { setError('Please select a file'); return; }
    if (!name.trim()) { setError('Please enter a dataset name'); return; }
    setLoading(true); setError('');
    try {
      await api.uploadDataset(name.trim(), file);
      onDone();
    } catch (err) {
      setError(err.message);
      setLoading(false);
    }
  };

  return (
    <div style={{ maxWidth: 520, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 24 }}>
        <button className="btn-ghost" onClick={onCancel}>← Back</button>
        <h2 style={{ margin: 0, fontSize: 18 }}>Upload Dataset</h2>
      </div>

      <form onSubmit={handleSubmit} className="card" style={{ padding: 24 }}>
        {error && <div className="error-banner">{error}</div>}

        {/* Drop zone */}
        <div
          className={`dropzone ${over ? 'over' : ''}`}
          onClick={() => inputRef.current.click()}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={handleDrop}
          style={{ marginBottom: 20 }}
        >
          <input
            ref={inputRef} type="file" accept=".csv"
            style={{ display: 'none' }}
            onChange={(e) => pickFile(e.target.files[0])}
          />
          <div className="dropzone-icon">📂</div>
          {file
            ? <><strong>{file.name}</strong><div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 4 }}>{(file.size / 1024).toFixed(1)} KB</div></>
            : <><div style={{ fontWeight: 500 }}>Drop a CSV file here</div><div style={{ color: 'var(--muted)', marginTop: 6, fontSize: 13 }}>or click to browse</div></>
          }
        </div>

        {/* Dataset name */}
        <label style={{ display: 'block', marginBottom: 16 }}>
          <div style={{ fontWeight: 500, marginBottom: 6, fontSize: 13 }}>Dataset name</div>
          <input
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. sales_2024"
          />
        </label>

        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button type="button" className="btn-secondary" onClick={onCancel}>Cancel</button>
          <button className="btn-primary" disabled={loading}>
            {loading ? 'Uploading…' : 'Upload'}
          </button>
        </div>
      </form>
    </div>
  );
}
