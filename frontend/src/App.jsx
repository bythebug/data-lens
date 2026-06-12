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
    </div>
  );
}
