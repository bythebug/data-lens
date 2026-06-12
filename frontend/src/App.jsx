import { useState } from 'react';
import DatasetList from './components/DatasetList';
import DatasetView from './components/DatasetView';
import Upload from './components/Upload';

export default function App() {
  const [view, setView] = useState('list');
  const [selectedId, setSelectedId] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const openDataset = (id) => { setSelectedId(id); setView('dataset'); };
  const afterUpload = () => { setRefreshKey(k => k + 1); setView('list'); };

  return (
    <div className="app">
      <header className="header">
        <span className="logo" onClick={() => setView('list')}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <rect x="3" y="3" width="18" height="4" rx="1"/><rect x="3" y="10" width="18" height="4" rx="1"/><rect x="3" y="17" width="18" height="4" rx="1"/>
          </svg>
          data-lens
        </span>
        {view !== 'upload' && (
          <button className="btn-primary" onClick={() => setView('upload')}>
            + Upload Dataset
          </button>
        )}
      </header>

      <main className="main">
        {view === 'upload' && (
          <Upload onDone={afterUpload} onCancel={() => setView('list')} />
        )}
        {view === 'list' && (
          <DatasetList key={refreshKey} onSelect={openDataset} />
        )}
        {view === 'dataset' && selectedId && (
          <DatasetView id={selectedId} onBack={() => setView('list')} />
        )}
      </main>

      <footer className="footer">
        <span>Analytics and search platform for CSV datasets. Full-text search, filtering, aggregation, statistics, and time-series analysis via REST API.</span>
        <a href="https://github.com/bythebug" target="_blank" rel="noreferrer" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 2C6.477 2 2 6.477 2 12c0 4.418 2.865 8.166 6.839 9.489.5.092.682-.217.682-.482 0-.237-.009-.868-.013-1.703-2.782.604-3.369-1.341-3.369-1.341-.454-1.155-1.11-1.462-1.11-1.462-.908-.62.069-.608.069-.608 1.003.07 1.531 1.03 1.531 1.03.892 1.529 2.341 1.087 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.11-4.555-4.943 0-1.091.39-1.984 1.029-2.683-.103-.253-.446-1.27.098-2.647 0 0 .84-.269 2.75 1.025A9.578 9.578 0 0 1 12 6.836a9.59 9.59 0 0 1 2.504.337c1.909-1.294 2.747-1.025 2.747-1.025.546 1.377.203 2.394.1 2.647.64.699 1.028 1.592 1.028 2.683 0 3.842-2.339 4.687-4.566 4.935.359.309.678.919.678 1.852 0 1.336-.012 2.415-.012 2.741 0 .267.18.579.688.481C19.138 20.163 22 16.418 22 12c0-5.523-4.477-10-10-10z"/>
          </svg>
          bythebug
        </a>
      </footer>
    </div>
  );
}
