import { useEffect, useRef, useState } from 'react'

const SUGGESTED = [
  'who is eng-4471 assigned to?',
  'what does @soham work on?',
  'who does @soham report to?',
  'who owns atlas migration?',
  'when does atlas migration launch?',
  'what is the budget for atlas migration?',
  'who does wei chen report to?',
]

const GATE_COPY = {
  entity: 'gate one: the thing asked about is not in the corpus',
  coverage: 'gate two: the entity exists, the asked about relation is not recorded',
  sufficiency: 'gate three: the only evidence is too weak to stand behind',
  verification: 'the generated answer cited something that was never retrieved',
}

export default function Chat({ people }) {
  const [question, setQuestion] = useState('')
  const [asOf, setAsOf] = useState('')
  const [turns, setTurns] = useState([])
  const [busy, setBusy] = useState(false)
  const endRef = useRef(null)

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [turns, busy])

  async function ask(q) {
    const text = (q || question).trim()
    if (!text || busy) return
    setBusy(true)
    setQuestion('')
    try {
      const result = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: text, as_of: asOf }),
      }).then((r) => r.json())
      setTurns((prev) => [...prev, { question: text, result }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="section">
      <div className="eyebrow"><span className="tick">/</span>step two</div>
      <h2 className="headline">ask the graph</h2>
      <p className="subline prose">
        Every answer carries the path that produced it and the claims it rests on. When sources
        disagree, the losing claim is shown rather than hidden. When the corpus does not hold an
        answer, the system says so instead of guessing.
      </p>

      <div className="chat">
        {turns.length === 0 && !busy && (
          <div className="empty">no questions yet</div>
        )}
        {turns.map((turn, i) => (
          <Turn key={i} turn={turn} people={people} />
        ))}
        {busy && <div className="working">traversing</div>}
        <div ref={endRef} />
      </div>

      <div className="composer">
        {turns.length === 0 && (
          <div className="suggest">
            {SUGGESTED.map((s) => (
              <button key={s} onClick={() => ask(s)}>{s}</button>
            ))}
          </div>
        )}
        <div className="row">
          <input
            className="field ask"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && ask()}
            placeholder="ask a question"
          />
          <input className="field" style={{ minWidth: 150 }} type="date" value={asOf}
                 onChange={(e) => setAsOf(e.target.value)} title="answer as the graph stood on this date" />
          {asOf && <button className="badge quiet" onClick={() => setAsOf('')}>clear date</button>}
          <button className="pill" onClick={() => ask()} disabled={busy}>send</button>
        </div>
      </div>
    </section>
  )
}

function Turn({ turn, people }) {
  const { question, result } = turn
  const abstained = result.status === 'abstained'
  const conflicts = [...(result.superseded || []), ...(result.contested || [])]
  const top = result.citations?.[0]

  return (
    <div className="turn">
      <div className="asked"><span className="q">{question}</span></div>

      <div className="card">
        <div className="verdict">
          <span className="mark-line" />
          <span className="state">{abstained ? 'not in the data' : 'answered'}</span>
          {result.as_of && <span className="badge quiet">as of {result.as_of.slice(0, 10)}</span>}
          <span className="meta mono">{result.latency_ms} ms</span>
        </div>

        {abstained ? (
          <>
            <p className="answer-text abstained">{result.answer}</p>
            <div className="block">
              <div className="label">why</div>
              <div className="prose" style={{ color: 'var(--dim)' }}>
                {GATE_COPY[result.gate] || result.gate}
              </div>
            </div>
          </>
        ) : (
          <>
            {top ? (
              <div className="triple">
                <span className="term">{top.subject}</span>
                <span className="rel">{top.predicate}</span>
                <span className="term">{top.object}</span>
              </div>
            ) : (
              <p className="answer-text">{result.answer}</p>
            )}
            {result.citations.length > 1 && (
              <div className="prose" style={{ color: 'var(--dimmer)', marginTop: 12 }}>
                {result.citations.length} sources agree
              </div>
            )}
          </>
        )}

        {result.path?.length > 0 && (
          <div className="block">
            <div className="label">traversal</div>
            <div className="triple">
              {result.path.map((hop, i) => (
                <span key={i} className="triple" style={{ gap: 12 }}>
                  <span className="term">{hop.from}</span>
                  <span className="rel">{hop.predicate}</span>
                  <span className="term">{hop.to}</span>
                </span>
              ))}
            </div>
          </div>
        )}

        {conflicts.length > 0 && (
          <div className="block">
            <div className="label">conflict arbitrated</div>
            {top && <Claim c={top} verdict="current" />}
            {conflicts.map((c) => <Claim key={c.key} c={c} verdict={c.status} />)}
            <p className="prose" style={{ color: 'var(--dimmer)', marginTop: 14 }}>
              Nothing is deleted. The losing claim stays in the graph, linked by SUPERSEDES, which is
              what lets the date field answer as of an earlier day.
            </p>
          </div>
        )}

        {result.citations?.length > 0 && (
          <div className="block">
            <div className="label">evidence</div>
            {result.citations.map((c) => <Claim key={c.key} c={c} />)}
          </div>
        )}

        <div className="block">
          <div className="label">query plan</div>
          <div className="kv">
            <span className="k">predicates</span>
            <span className="v">{result.predicates?.join(', ') || 'none matched'}</span>
          </div>
          {result.entities?.map((e, i) => (
            <div className="kv" key={i}>
              <span className="k">{e.label}</span>
              <span className="v">{e.name} <em>{e.matched_via}</em></span>
            </div>
          ))}
        </div>

        {people?.length > 0 && (
          <div className="block">
            <div className="label">entity resolution</div>
            {people.slice(0, 4).map((p) => (
              <div className="kv" key={p.key}>
                <span className="k">{p.name}</span>
                <span className="v">
                  {p.aliases} aliases, {p.mentions} mentions
                  {p.evidence?.[0] && <em style={{ display: 'block', marginTop: 4 }}>{p.evidence[0]}</em>}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function Claim({ c, verdict }) {
  return (
    <div className={`claim ${verdict === 'superseded' ? 'superseded' : ''}`}>
      <div className="row">
        {verdict && <span className={`verdict-tag ${verdict}`}>{verdict}</span>}
        <span className="tool">{c.source_tool}</span>
        <span className="src">{c.source}</span>
        <span>{c.asserted_at}</span>
        <span className="score">{c.score.toFixed(2)}</span>
      </div>
      <div className="statement">
        {c.subject} <span className="rel">{c.predicate}</span> {c.object}
      </div>
      {c.evidence && <div className="quote prose">{c.evidence}</div>}
    </div>
  )
}
