import { useEffect, useRef, useState } from 'react'

function post(path, body) {
  return fetch(`/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  }).then((r) => r.json())
}

/** Plain language throughout. Someone who has never seen this project should be
 *  able to load their own files and ask a question without a glossary. */
export default function Ingest({ onReady, onChanged, ai }) {
  const [dataDir, setDataDir] = useState('data/raw')
  const [readProse, setReadProse] = useState(false)
  const [job, setJob] = useState(null)
  const [kind, setKind] = useState('')
  const [log, setLog] = useState([])
  const [status, setStatus] = useState('')
  const [learned, setLearned] = useState(null)
  const [built, setBuilt] = useState(null)
  const [cleared, setCleared] = useState(false)
  const logRef = useRef(null)

  const aiReady = !!ai?.configured

  useEffect(() => {
    if (!job || status !== 'running') return
    const timer = setInterval(async () => {
      const d = await fetch(`/api/job/${job}`).then((r) => r.json())
      setLog(d.log || [])
      setStatus(d.status)
      if (d.status !== 'running') {
        clearInterval(timer)
        if (d.kind === 'reset') { setCleared(true); setLearned(null); setBuilt(null) }
        if (d.kind === 'induce' && d.result) setLearned(d.result)
        if (d.kind === 'ingest' && d.result) setBuilt(d.result)
        onChanged?.()
      }
    }, 500)
    return () => clearInterval(timer)
  }, [job, status])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [log])

  async function launch(path, body, which) {
    setLog([]); setStatus('running'); setKind(which)
    const d = await post(path, body)
    if (d.detail) { setStatus('failed'); setLog([d.detail]); return }
    setJob(d.job)
  }

  const running = status === 'running'
  const busyLabel = { reset: 'clearing', induce: 'reading your files', ingest: 'building' }[kind] || 'working'

  return (
    <section className="section">
      <div className="eyebrow"><span className="tick">/</span>first, load some data</div>
      <h2 className="headline">load your files</h2>
      <p className="subline prose">
        Point this at a folder of exports from the tools your company uses. It reads the files, works
        out what is in them, merges people who appear under different names, decides which statements
        are current when two sources disagree, and stores everything with a link back to where it came
        from. You do not configure anything.
      </p>

      <div className="card" style={{ marginTop: 32 }}>
        <div className="label">where are your files</div>
        <div className="controls" style={{ marginTop: 0, marginBottom: 8 }}>
          <input className="field" value={dataDir} disabled={running}
                 onChange={(e) => setDataDir(e.target.value)} placeholder="folder path" />
        </div>
        <p className="hint prose" style={{ marginBottom: 28 }}>
          A folder on this machine. Reads json, jsonl, csv, markdown and plain text, including
          subfolders. The example folder holds nine tool exports.
        </p>

        <div className="label">how much should it read</div>
        <div className="choices">
          <button className={`choice ${!readProse ? 'on' : ''}`} disabled={running}
                  onClick={() => setReadProse(false)}>
            <span className="choice-title">fields only</span>
            <span className="choice-body prose">
              Reads the structured parts: who a ticket is assigned to, a due date, a deal owner.
              Fast, free, and needs no account.
            </span>
          </button>
          <button className={`choice ${readProse ? 'on' : ''} ${!aiReady ? 'off' : ''}`}
                  disabled={running || !aiReady}
                  onClick={() => aiReady && setReadProse(true)}>
            <span className="choice-title">fields and written text</span>
            <span className="choice-body prose">
              Also reads messages, emails and meeting transcripts with an ai model, which finds facts
              stated in sentences rather than fields. Slower.
              {aiReady
                ? ` Using ${ai.provider}.`
                : ' Unavailable: no ai key is set in the .env file.'}
            </span>
          </button>
        </div>

        <div className="steps" style={{ marginTop: 30 }}>
          <Step n="01" done={cleared} title="clear what is loaded now"
                hint="Empties the database so you start from nothing. Checks it is really empty before continuing.">
            <button className="pill" disabled={running} onClick={() => launch('/reset', {}, 'reset')}>clear</button>
          </Step>

          <Step n="02" done={!!learned} title="work out what is in the files"
                hint="Looks at a sample of your records and works out which fields hold ids, authors and dates, what kinds of things exist, and how they relate to each other. You can review the result before anything is stored.">
            <button className="pill" disabled={running}
                    onClick={() => launch('/induce', { data_dir: dataDir }, 'induce')}>read the files</button>
          </Step>

          <Step n="03" done={!!built} title="build the knowledge graph"
                hint="Reads every file, merges duplicate people, settles disagreements between sources, and stores the result so it can be questioned.">
            <button className="pill" disabled={running || !learned}
                    onClick={() => launch('/ingest', { tier_b: readProse }, 'ingest')}>build</button>
            {!learned && <span className="hint prose">do step 02 first</span>}
          </Step>
        </div>

        {running && <div className="working" style={{ marginTop: 18 }}>{busyLabel}, this can take a moment</div>}
        {status === 'failed' && <div className="failed" style={{ marginTop: 18 }}>something went wrong, details below</div>}
        {log.length > 0 && (
          <>
            <div className="label" style={{ marginTop: 22 }}>what it is doing</div>
            <pre className="log" ref={logRef}>{log.join('\n')}</pre>
          </>
        )}

        {learned && <Learned learned={learned} />}
        {built && <Built built={built} onReady={onReady} />}
      </div>

      <div className="demo">
        <div className="demo-copy">
          <div className="label">no data of your own to hand?</div>
          <p className="hint prose" style={{ margin: 0 }}>
            Use a real published dataset instead. Salesforce herb is 39,190 documents from a fictional
            company: slack threads, documents, meeting transcripts and pull requests across 30 products.
            This downloads it, clears the database, and builds five products. Takes about a minute.
          </p>
        </div>
        <button className="pill ember" disabled={running}
                onClick={() => launch('/demo/herb', { products: 5, tier_b: readProse }, 'ingest')}>
          use the example dataset
        </button>
      </div>
    </section>
  )
}

const LABEL_COPY = {
  Claim: 'statements', Artifact: 'documents', Person: 'people', Alias: 'alternate names',
  Group: 'access groups', Topic: 'values', Project: 'projects', Account: 'customers', Team: 'teams',
}

function Learned({ learned }) {
  return (
    <div className="block">
      <div className="label">what it found in your files</div>
      <p className="hint prose" style={{ marginBottom: 16 }}>
        {learned.predicates.length} kinds of relationship across {learned.sources} sources, and{' '}
        {learned.rules} fields it can read directly. None of this was written by hand.
      </p>
      <table className="preds">
        <thead>
          <tr>
            <th>relationship</th><th>from</th><th>to</th>
            <th>one value or many</th><th>values seen per thing</th>
          </tr>
        </thead>
        <tbody>
          {learned.predicates.map((p) => (
            <tr key={p.name}>
              <td className="name">{p.name.replace(/_/g, ' ')}</td>
              <td>{p.domain.join(' ')}</td>
              <td>{p.range.join(' ')}</td>
              <td className={p.cardinality === 'one' ? 'one' : ''}>
                {p.cardinality === 'one' ? 'one at a time' : 'many'}
              </td>
              <td className="obs">{p.observed ? p.observed : 'not seen yet'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="subline prose" style={{ marginTop: 16 }}>
        That last column is counted from your data, not assumed. If a thing only ever has one value,
        two sources claiming different values is a disagreement worth settling. If it normally has
        many, they simply coexist.
      </p>
    </div>
  )
}

function Built({ built, onReady }) {
  return (
    <div className="block">
      <div className="label">done, this is what was stored</div>
      <div className="built">
        {Object.entries(built.by_label).map(([k, v]) => (
          <span key={k} className="badge quiet">{LABEL_COPY[k] || k.toLowerCase()} {v.toLocaleString()}</span>
        ))}
        <span className="badge ember">{built.edges.toLocaleString()} connections</span>
      </div>
      <div className="controls">
        <button className="pill" onClick={onReady}>start asking questions</button>
      </div>
    </div>
  )
}

function Step({ n, title, hint, children, done }) {
  return (
    <div className={`step ${done ? 'done' : ''}`}>
      <span className="badge num">{n}</span>
      <div className="body">
        <div className="title">{title}</div>
        <div className="hint prose">{hint}</div>
        <div className="controls">{children}</div>
      </div>
    </div>
  )
}
