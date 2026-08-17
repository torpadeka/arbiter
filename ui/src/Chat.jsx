import { useEffect, useRef, useState } from 'react'

const WHY_COPY = {
  entity: 'nothing in the loaded files mentions what you asked about',
  coverage: 'the thing exists in your files, but nobody recorded this about it',
  sufficiency: 'the only mention is too weak to rely on, so it is not presented as fact',
  verification: 'the wording could not be traced back to the sources, so it was discarded',
}

export default function Chat({ people }) {
  const [question, setQuestion] = useState('')
  const [suggested, setSuggested] = useState([])
  const [asOf, setAsOf] = useState('')
  const [turns, setTurns] = useState([])
  const [busy, setBusy] = useState(false)
  const endRef = useRef(null)

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [turns, busy])

  // Built from whatever is loaded right now, so they always hit real subjects.
  useEffect(() => {
    fetch('/api/suggestions')
      .then((r) => r.json())
      .then((d) => setSuggested(d.suggestions || []))
      .catch(() => {})
  }, [])

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
      <div className="eyebrow"><span className="tick">/</span>now ask anything</div>
      <h2 className="headline">ask a question</h2>
      <p className="subline prose">
        Every answer shows where it came from. When two sources disagreed, you see the one that was
        overruled as well as the one that won. When your files do not contain the answer, it says so
        rather than guessing.
      </p>

      <div className="chat">
        {turns.length === 0 && !busy && (
          <div className="empty">ask something to begin</div>
        )}
        {turns.map((turn, i) => (
          <Turn key={i} turn={turn} people={people} />
        ))}
        {busy && <div className="working">looking through your files</div>}
        <div ref={endRef} />
      </div>

      <div className="composer">
        {turns.length === 0 && suggested.length > 0 && (
          <>
            <div className="label" style={{ marginBottom: 10 }}>
              questions drawn from what you just loaded
            </div>
            <div className="suggest">
              {suggested.map((s) => (
                <button key={s.q} onClick={() => ask(s.q)}>
                  {s.q}<span className="tag">{s.tag}</span>
                </button>
              ))}
            </div>
          </>
        )}
        <div className="row">
          <input
            className="field ask"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && ask()}
            placeholder="for example, who owns the atlas migration"
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
          <span className="state">{abstained ? 'not in your files' : 'answered'}</span>
          {result.as_of && <span className="badge quiet">as of {result.as_of.slice(0, 10)}</span>}
          <span className="meta mono">{result.latency_ms} ms</span>
        </div>

        {abstained ? (
          <>
            <p className="answer-text abstained">{result.answer}</p>
            <div className="block">
              <div className="label">why not</div>
              <div className="prose" style={{ color: 'var(--dim)' }}>
                {WHY_COPY[result.gate] || result.gate}
              </div>
            </div>
          </>
        ) : (
          <>
            {top ? (
              <div className="triple">
                <span className="term">{top.subject}</span>
                <span className="rel">{top.predicate.replace(/_/g, ' ')}</span>
                <span className="term">{top.object}</span>
              </div>
            ) : (
              <p className="answer-text">{result.answer}</p>
            )}
            {result.citations.length > 1 && (
              <div className="prose" style={{ color: 'var(--dimmer)', marginTop: 12 }}>
                {result.citations.length} sources say the same thing
              </div>
            )}
          </>
        )}

        {result.path?.length > 0 && (
          <div className="block">
            <div className="label">how it got there</div>
            <div className="triple">
              {result.path.map((hop, i) => (
                <span key={i} className="triple" style={{ gap: 12 }}>
                  <span className="term">{hop.from}</span>
                  <span className="rel">{hop.predicate.replace(/_/g, ' ')}</span>
                  <span className="term">{hop.to}</span>
                </span>
              ))}
            </div>
          </div>
        )}

        {conflicts.length > 0 && (
          <div className="block">
            <div className="label">two sources disagreed</div>
            {top && <Claim c={top} verdict="current" />}
            {conflicts.map((c) => <Claim key={c.key} c={c} verdict={c.status} />)}
            <p className="prose" style={{ color: 'var(--dimmer)', marginTop: 14 }}>
              Nothing is thrown away. The overruled statement is kept, which is how the date box above
              can answer the same question as it stood on an earlier day.
            </p>
          </div>
        )}

        {result.citations?.length > 0 && (
          <div className="block">
            <div className="label">where this came from</div>
            {result.citations.map((c) => <Claim key={c.key} c={c} />)}
          </div>
        )}

        <div className="block">
          <div className="label">how the question was read</div>
          <div className="kv">
            <span className="k">looking for</span>
            <span className="v">{result.predicates?.map((p) => p.replace(/_/g, ' ')).join(', ') || 'nothing recognised'}</span>
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
            <div className="label">people found under more than one name</div>
            {people.slice(0, 4).map((p) => (
              <div className="kv" key={p.key}>
                <span className="k">{p.name}</span>
                <span className="v">
                  {p.aliases} other names, mentioned {p.mentions} times
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
        {verdict && <span className={`verdict-tag ${verdict}`}>{verdict === 'superseded' ? 'overruled' : verdict}</span>}
        <span className="tool">{c.source_tool}</span>
        <span className="src">{c.source}</span>
        <span>{c.asserted_at}</span>
        <span className="score">{c.score.toFixed(2)}</span>
      </div>
      <div className="statement">
        {c.subject} <span className="rel">{c.predicate.replace(/_/g, ' ')}</span> {c.object}
      </div>
      {c.evidence && <div className="quote prose">{c.evidence}</div>}
    </div>
  )
}
