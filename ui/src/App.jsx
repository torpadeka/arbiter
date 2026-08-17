import { useEffect, useState } from 'react'
import Ingest from './Ingest.jsx'
import Chat from './Chat.jsx'
import HowItWorks from './HowItWorks.jsx'
import Mark from './Mark.jsx'

/** Optional ambient backdrop.
 *
 *  Drop a file into ui/public and it is picked up automatically:
 *    atmosphere.mp4   preferred, looped and muted
 *    atmosphere.jpg   fallback still
 *  With neither present the UI stays flat void, which is also correct.
 */
function Atmosphere({ onFound }) {
  const [kind, setKind] = useState(null)

  useEffect(() => {
    let cancelled = false
    async function probe(path) {
      try {
        const r = await fetch(path, { method: 'HEAD' })
        return r.ok && !(r.headers.get('content-type') || '').includes('text/html')
      } catch { return false }
    }
    ;(async () => {
      for (const [path, k] of [['/atmosphere.mp4', 'video'], ['/atmosphere.jpg', 'image'], ['/atmosphere.png', 'image']]) {
        if (cancelled) return
        if (await probe(path)) {
          setKind({ k, path })
          onFound?.()
          return
        }
      }
    })()
    return () => { cancelled = true }
  }, [])

  if (!kind) return null
  return (
    <>
      {kind.k === 'video' ? (
        <video className="atmosphere" src={kind.path} autoPlay muted loop playsInline />
      ) : (
        <img className="atmosphere" src={kind.path} alt="" />
      )}
      <div className="veil" />
    </>
  )
}

const LABEL_COPY = {
  Claim: 'statements', Artifact: 'documents', Person: 'people', Alias: 'alternate names',
  Group: 'access groups', Topic: 'values', Project: 'projects', Account: 'customers', Team: 'teams',
}

export default function App() {
  const [stats, setStats] = useState(null)
  const [people, setPeople] = useState([])
  const [view, setView] = useState('ingest')
  const [atmosphere, setAtmosphere] = useState(false)

  function refresh() {
    fetch('/api/stats').then((r) => r.json()).then(setStats).catch(() => {})
    fetch('/api/entities').then((r) => r.json()).then((d) => setPeople(d.people || [])).catch(() => {})
  }

  useEffect(refresh, [])

  const claims = stats?.counts?.Claim || 0
  const loaded = claims > 0

  // Questions do not exist before a corpus does. An empty graph has nothing to
  // be asked about, and offering a chat box would imply otherwise.
  useEffect(() => { if (!loaded && view === 'chat') setView('ingest') }, [loaded, view])

  return (
    <div className={atmosphere ? 'has-atmosphere' : ''}>
      <Atmosphere onFound={() => setAtmosphere(true)} />

      <div className="newsbar mono">
        arbiter<span className="sep">•</span>answers from your company's own files
        <span className="sep">•</span>
        {loaded
          ? <span className="live">{claims.toLocaleString()} statements loaded</span>
          : <span>nothing loaded yet</span>}
      </div>

      <div className="shell">
        <header className="head">
          <div className="mark">
            <Mark size={44} />
            <div>
              <div className="wordmark">arbiter</div>
              <div className="tagline">what is currently true, who established it, and when</div>
            </div>
          </div>

          <div className="stats">
            {stats && Object.entries(stats.counts).filter(([, v]) => v > 0).map(([k, v]) => (
              <span key={k} className="badge quiet">
                <span className="num">{v.toLocaleString()}</span>{' '}
                <span className="lbl">{LABEL_COPY[k] || k.toLowerCase()}</span>
              </span>
            ))}
            <nav className="tabs">
              <button className={`pill ${view === 'ingest' ? 'ember' : ''}`} onClick={() => setView('ingest')}>
                load data
              </button>
              {loaded && (
                <button className={`pill ${view === 'chat' ? 'ember' : ''}`} onClick={() => setView('chat')}>
                  ask
                </button>
              )}
              <button className={`pill ${view === 'how' ? 'ember' : ''}`} onClick={() => setView('how')}>
                how it works
              </button>
            </nav>
          </div>
        </header>

        {view === 'ingest' && (
          <Ingest onChanged={refresh} onReady={() => setView('chat')} ai={stats?.ai} />
        )}

        {view === 'chat' && loaded && <Chat people={people} />}

        {view === 'how' && <HowItWorks stats={stats} />}

        <footer className="foot">
          every answer is traced back to the document it came from. when your files do not hold the
          answer, this says so instead of guessing. built on hydradb.
        </footer>
      </div>
    </div>
  )
}
