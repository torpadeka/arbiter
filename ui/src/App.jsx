import { useEffect, useState } from 'react'
import Pipeline from './Pipeline.jsx'

const DEMO = [
  { q: 'who is ENG-4471 assigned to?', tag: 'provenance' },
  { q: 'what does @soham work on?', tag: 'alias' },
  { q: 'who does @soham report to?', tag: 'prose only' },
  { q: 'who owns Atlas Migration?', tag: 'conflict' },
  { q: 'when does Atlas Migration launch?', tag: 'conflict' },
  { q: 'what is the budget for Atlas Migration?', tag: 'abstains' },
  { q: 'who does Wei Chen report to?', tag: 'abstains' },
  { q: 'who owns Project Zephyr?', tag: 'abstains' },
]

const GATE_COPY = {
  entity: 'Gate 1 · the thing asked about is not in the corpus',
  coverage: 'Gate 2 · the entity exists, the asked-about relation is not recorded',
  sufficiency: 'Gate 3 · the only evidence is too weak to stand behind',
  verification: 'the generated answer cited something that was never retrieved',
}

const TOOL_COLOR = {
  jira: '#5b8def', linear: '#5b8def', github: '#b57edc', hubspot: '#3fb6b6',
  confluence: '#4aa96c', drive: '#4aa96c', fireflies: '#d4a13a', gmail: '#d4a13a',
  slack: '#8a8f98', herb_slack: '#8a8f98', herb_doc: '#4aa96c', herb_pr: '#b57edc',
}

function api(path, body) {
  return fetch(`/api${path}`, body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : undefined).then((r) => r.json())
}

export default function App() {
  const [question, setQuestion] = useState('who owns Atlas Migration?')
  const [asOf, setAsOf] = useState('')
  const [result, setResult] = useState(null)
  const [stats, setStats] = useState(null)
  const [people, setPeople] = useState([])
  const [busy, setBusy] = useState(false)
  const [showPeople, setShowPeople] = useState(false)
  const [showPipeline, setShowPipeline] = useState(false)

  function refresh() {
    api('/stats').then(setStats).catch(() => {})
    api('/entities').then((d) => setPeople(d.people || [])).catch(() => {})
  }

  useEffect(refresh, [])

  // An empty graph means there is nothing to ask about yet, so lead with ingest.
  useEffect(() => {
    if (stats && (stats.counts?.Claim || 0) === 0) setShowPipeline(true)
  }, [stats])

  async function ask(q = question, when = asOf) {
    setBusy(true)
    setQuestion(q)
    try {
      setResult(await api('/ask', { question: q, as_of: when }))
    } finally {
      setBusy(false)
    }
  }

  const abstained = result && result.status === 'abstained'

  return (
    <div className="app">
      <header>
        <div className="brand">
          <h1>Arbiter</h1>
          <span className="sub">what is currently true, who established it, and when</span>
        </div>
        <div className="stats">
          {stats && Object.entries(stats.counts).filter(([, v]) => v > 0).map(([k, v]) => (
            <span key={k} className="stat"><b>{v.toLocaleString()}</b> {k}</span>
          ))}
          <button className="ghost" onClick={() => setShowPipeline(!showPipeline)}>
            {showPipeline ? 'hide corpus' : 'ingest a corpus'}
          </button>
        </div>
      </header>

      {showPipeline && <div className="pipewrap"><Pipeline onChanged={refresh} /></div>}

      <section className="askbar">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && ask()}
          placeholder="ask the graph a question"
        />
        <input className="asof" type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} title="answer as the graph stood on this date" />
        {asOf && <button className="ghost" onClick={() => { setAsOf(''); ask(question, '') }}>clear</button>}
        <button className="primary" onClick={() => ask()} disabled={busy}>{busy ? '…' : 'Ask'}</button>
      </section>

      <div className="chips">
        {DEMO.map((d) => (
          <button key={d.q} className="chip" onClick={() => ask(d.q, asOf)}>
            {d.q}<span className="tag">{d.tag}</span>
          </button>
        ))}
      </div>

      {result && (
        <main className="grid">
          <div className="col">
            <div className={`answer ${abstained ? 'abstained' : 'answered'}`}>
              <div className="answer-head">
                {abstained ? 'NOT IN THE DATA' : 'ANSWER'}
                {result.as_of && <span className="asof-badge">as of {result.as_of.slice(0, 10)}</span>}
                <span className="latency">{result.latency_ms} ms</span>
              </div>
              <p>{result.answer}</p>
              {abstained && <div className="gate">{GATE_COPY[result.gate] || result.gate}</div>}
            </div>

            {result.path.length > 0 && (
              <Card title="Traversal path">
                <div className="path">
                  {result.path.map((hop, i) => (
                    <span key={i} className="hop">
                      <span className="node">{hop.from}</span>
                      <span className="edge">{hop.predicate}</span>
                      <span className="node">{hop.to}</span>
                    </span>
                  ))}
                </div>
              </Card>
            )}

            {result.citations.length > 0 && (
              <Card title={`Evidence · ${result.citations.length} claim${result.citations.length > 1 ? 's' : ''}`}>
                {result.citations.map((c) => <Claim key={c.key} c={c} />)}
              </Card>
            )}
          </div>

          <div className="col">
            {(result.superseded.length > 0 || result.contested.length > 0) && (
              <Card title="Conflict arbitrated" accent="red">
                {result.citations[0] && <Claim c={result.citations[0]} verdict="current" />}
                {[...result.superseded, ...result.contested].map((c) => (
                  <Claim key={c.key} c={c} verdict={c.status} />
                ))}
                <p className="note">
                  Nothing is deleted. The losing claim stays in the graph, linked by <code>SUPERSEDES</code>,
                  which is what makes the date filter above able to answer as of an earlier day.
                </p>
              </Card>
            )}

            <Card title="Query plan">
              <div className="kv"><span>predicates</span><b>{result.predicates.join(', ') || 'none matched'}</b></div>
              {result.entities.map((e, i) => (
                <div className="kv" key={i}>
                  <span>{e.label.toLowerCase()}</span>
                  <b>{e.name} <em>{e.matched_via}</em></b>
                </div>
              ))}
            </Card>

            <Card title={`Entity resolution · ${people.length} people`}>
              <button className="ghost wide" onClick={() => setShowPeople(!showPeople)}>
                {showPeople ? 'hide' : 'show'} merge evidence
              </button>
              {showPeople && people.map((p) => (
                <div key={p.key} className="person">
                  <div className="person-head">
                    <b>{p.name}</b>
                    <span>{p.aliases} aliases · {p.mentions} mentions</span>
                  </div>
                  {p.evidence.map((e, i) => <div key={i} className="ev">{e}</div>)}
                </div>
              ))}
            </Card>
          </div>
        </main>
      )}

      <footer>
        Every answer is a traversal in HydraDB. Gates run before any model call, so an unanswerable
        question costs zero tokens and cannot be answered by a guess.
      </footer>
    </div>
  )
}

function Card({ title, children, accent }) {
  return (
    <section className={`card ${accent || ''}`}>
      <h2>{title}</h2>
      {children}
    </section>
  )
}

function Claim({ c, verdict }) {
  return (
    <div className={`claim ${verdict || ''}`}>
      <div className="claim-head">
        {verdict && <span className={`verdict ${verdict}`}>{verdict}</span>}
        <span className="tool" style={{ color: TOOL_COLOR[c.source_tool] || '#9aa0a6' }}>{c.source_tool}</span>
        <span className="src">{c.source}</span>
        <span className="date">{c.asserted_at}</span>
        <span className="score" title="authority, recency, specificity, corroboration, hedging">
          {c.score.toFixed(2)}
        </span>
      </div>
      <div className="triple">
        <b>{c.subject}</b> <span className="pred">{c.predicate}</span> <b>{c.object}</b>
      </div>
      {c.evidence && <div className="quote">{c.evidence}</div>}
    </div>
  )
}
