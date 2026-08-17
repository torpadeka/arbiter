import { useEffect, useState } from 'react'

/** Server side folder browser.
 *
 *  The browser cannot pass a path to the server, so the server lists folders and
 *  this walks them. Each row shows how many readable files are inside, which is
 *  the only thing that matters when choosing where to point the ingest.
 */
export default function FolderPicker({ current, onPick, onClose }) {
  const [view, setView] = useState(null)
  const [error, setError] = useState('')

  async function load(path) {
    setError('')
    try {
      const r = await fetch(`/api/browse?path=${encodeURIComponent(path || '')}`)
      const d = await r.json()
      if (d.detail) { setError(d.detail); return }
      setView(d)
    } catch {
      setError('could not read that folder')
    }
  }

  useEffect(() => { load(current) }, [])

  if (!view) return <div className="picker"><div className="dimmed">reading folders</div></div>

  return (
    <div className="picker">
      <div className="picker-head">
        <span className="picker-path mono">{view.path}</span>
        <button className="badge quiet" onClick={onClose}>close</button>
      </div>

      <div className="picker-shortcuts">
        {view.shortcuts.map((s) => (
          <button key={s.path} className="badge quiet" onClick={() => load(s.path)}>{s.name}</button>
        ))}
      </div>

      {error && <div className="failed">{error}</div>}

      <div className="picker-list">
        {view.parent && (
          <button className="picker-row" onClick={() => load(view.parent)}>
            <span className="picker-name">.. up one level</span>
          </button>
        )}
        {view.folders.length === 0 && !view.parent && (
          <div className="dimmed">no subfolders here</div>
        )}
        {view.folders.map((f) => (
          <button key={f.path} className="picker-row" onClick={() => load(f.path)}>
            <span className="picker-name">{f.name}</span>
            <span className="picker-count mono">
              {f.files > 0 ? `${f.files >= 400 ? '400+' : f.files} readable files` : 'nothing readable'}
            </span>
          </button>
        ))}
      </div>

      <div className="picker-foot">
        <span className="dimmed">
          {view.files_here > 0
            ? `${view.files_here} readable file${view.files_here > 1 ? 's' : ''} directly in this folder`
            : 'no readable files directly in this folder, subfolders are read too'}
        </span>
        <button className="pill ember" onClick={() => { onPick(view.path); onClose() }}>
          use this folder
        </button>
      </div>
    </div>
  )
}
