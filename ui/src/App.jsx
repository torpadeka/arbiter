import { useEffect, useState } from 'react'
import Ingest from './Ingest.jsx'
import Chat from './Chat.jsx'

export default function App() {
  const [stats, setStats] = useState(null)
  const [people, setPeople] = useState([])
  const [view, setView] = useState('ingest')

  function refresh() {
    fetch('/api/stats').then((r) => r.json()).then(setStats).catch(() => {})
    fetch('/api/entities').then((r) => r.json()).then((d) => setPeople(d.people || [])).catch(() => {})
  }

  useEffect(refresh, [])

  const claims = stats?.counts?.Claim || 0
  const loaded = claims > 0

  // Questions do not exist before a corpus does. An empty graph has nothing to
  // be asked about, and offering a chat box would imply otherwise.
  useEffect(() => { if (!loaded) setView('ingest') }, [loaded])

  return (
    <>
      <div className="newsbar mono">
        arbiter<span className="sep">•</span>enterprise ontology on hydradb
        <span className="sep">•</span>
        {loaded
          ? <span className="live">{claims.toLocaleString()} claims loaded</span>
          : <span>no corpus ingested</span>}
      </div>

      <div className="shell">
        <header className="head">
          <div className="mark">
            <Constellation />
            <div>
              <div className="wordmark">arbiter</div>
              <div className="tagline">what is currently true, who established it, and when</div>
            </div>
          </div>

          <div className="stats">
            {stats && Object.entries(stats.counts).filter(([, v]) => v > 0).map(([k, v]) => (
              <span key={k} className="badge quiet">
                <span className="num">{v.toLocaleString()}</span> <span className="lbl">{k}</span>
              </span>
            ))}
            {loaded && (
              <button className="pill" onClick={() => setView(view === 'chat' ? 'ingest' : 'chat')}>
                {view === 'chat' ? 'corpus' : 'ask'}
              </button>
            )}
          </div>
        </header>

        {view === 'ingest' && (
          <Ingest onChanged={refresh} onReady={() => setView('chat')} />
        )}

        {view === 'chat' && loaded && <Chat people={people} />}

        <footer className="foot">
          every answer is a traversal in hydradb. gates run before any model call, so an unanswerable
          question costs nothing and cannot be answered by a guess.
        </footer>
      </div>
    </>
  )
}

/** Four point mark: a centred diamond with extending points, thin strokes only. */
function Constellation() {
  return (
    <svg width="42" height="42" viewBox="0 0 42 42" fill="none" aria-hidden="true">
      <rect x="21" y="12.5" width="12" height="12" transform="rotate(45 21 12.5)"
            stroke="#ffffff" strokeWidth="1" />
      <path d="M21 2 L23 9 L21 11 L19 9 Z" stroke="#cc6437" strokeWidth="1" />
      <path d="M21 40 L23 33 L21 31 L19 33 Z" stroke="#ffffff" strokeWidth="1" />
      <path d="M2 21 L9 23 L11 21 L9 19 Z" stroke="#ffffff" strokeWidth="1" />
      <path d="M40 21 L33 23 L31 21 L33 19 Z" stroke="#ffffff" strokeWidth="1" />
    </svg>
  )
}
