import { useEffect, useRef, useState } from 'react'
import Ingest from './Ingest.jsx'
import Chat from './Chat.jsx'
import HowItWorks from './HowItWorks.jsx'
import Mark from './Mark.jsx'

/** The ambient backdrop, part of the design rather than an optional extra.
 *
 *  `ui/public/atmosphere.mp4` ships with the repo, so a fresh clone looks the way
 *  it is meant to. Replace that file to change the atmosphere; if it is missing
 *  the page falls back to flat void, which is also a valid reading of the style.
 *
 *  Blur is deliberately light in styles.css: this footage is thin bright streaks,
 *  and a heavy blur erases them entirely.
 */
const ATMOSPHERE = '/atmosphere.mp4'
const ATMOSPHERE_STILL = '/atmosphere.jpg'

function Atmosphere({ onReady }) {
  const video = useRef(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => { if (!failed) onReady?.() }, [failed])

  // Autoplay is a request, not a guarantee: a browser can refuse it for reasons
  // the page cannot see, and a refused video renders nothing at all. So the
  // still is painted behind it as a poster and as a CSS background, the page
  // looks right either way, and a play is retried on the first interaction.
  useEffect(() => {
    const el = video.current
    if (!el) return
    const attempt = () => el.play?.().catch(() => {})
    attempt()
    const events = ['pointerdown', 'keydown', 'wheel']
    events.forEach((e) => window.addEventListener(e, attempt, { once: true, passive: true }))
    return () => events.forEach((e) => window.removeEventListener(e, attempt))
  }, [])

  if (failed) return null
  return (
    <>
      <video
        ref={video}
        className="atmosphere"
        src={ATMOSPHERE}
        poster={ATMOSPHERE_STILL}
        autoPlay
        muted
        loop
        playsInline
        preload="auto"
        aria-hidden="true"
        onError={() => setFailed(true)}
      />
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
      <Atmosphere onReady={() => setAtmosphere(true)} />

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
            </div>
          </div>
          <div className="tagline">what is currently true, who established it, and when</div>

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
