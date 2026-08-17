import { useEffect, useRef, useState } from 'react'

function post(path, body) {
  return fetch(`/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  }).then((r) => r.json())
}

/** Three steps: empty the graph, derive an ontology, build. The log is
 *  whatever the pipeline actually printed, not a simulation of progress. */
export default function Ingest({ onReady, onChanged }) {
  const [dataDir, setDataDir] = useState('data/raw')
  const [source, setSource] = useState('folder')
  const [tierB, setTierB] = useState(false)
  const [job, setJob] = useState(null)
  const [kind, setKind] = useState('')
  const [log, setLog] = useState([])
  const [status, setStatus] = useState('')
  const [induced, setInduced] = useState(null)
  const [built, setBuilt] = useState(null)
  const [wiped, setWiped] = useState(false)
  const logRef = useRef(null)

  useEffect(() => {
    if (!job || status !== 'running') return
    const timer = setInterval(async () => {
      const d = await fetch(`/api/job/${job}`).then((r) => r.json())
      setLog(d.log || [])
      setStatus(d.status)
      if (d.status !== 'running') {
        clearInterval(timer)
        if (d.kind === 'reset') { setWiped(true); setInduced(null); setBuilt(null) }
        if (d.kind === 'induce' && d.result) setInduced(d.result)
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
    setJob(d.job)
  }

  const running = status === 'running'
  const usesInduction = source === 'folder'

  return (
    <section className="section">
      <div className="eyebrow"><span className="tick">/</span>step one</div>
      <h2 className="headline">ingest a corpus</h2>
      <p className="subline prose">
        Point Arbiter at a folder of exports. It profiles the files, derives an ontology from the data,
        resolves entities, arbitrates contradictions, and writes a graph. Nothing is configured by hand.
      </p>

      <div className="card" style={{ marginTop: 32 }}>
        <div className="controls" style={{ marginTop: 0, marginBottom: 24 }}>
          <select className="field" value={source} onChange={(e) => setSource(e.target.value)} disabled={running}>
            <option value="folder">any folder, ontology induced</option>
            <option value="herb">salesforce herb, built in adapter</option>
          </select>
          {usesInduction && (
            <input className="field" value={dataDir} disabled={running}
                   onChange={(e) => setDataDir(e.target.value)} placeholder="folder path" />
          )}
          <button className="badge quiet" onClick={() => setTierB(!tierB)} disabled={running}>
            {tierB ? 'tier b on' : 'tier b off'}
          </button>
        </div>

        <div className="steps">
          <Step n="01" done={wiped} title="empty the graph"
                hint="The engine has no DELETE, so a reset wipes the object store and verifies it is empty.">
            <button className="pill" disabled={running} onClick={() => launch('/reset', {}, 'reset')}>reset</button>
          </Step>

          {usesInduction && (
            <Step n="02" done={!!induced} title="derive the ontology"
                  hint="Profiles every file, maps each source onto a document envelope, discovers a vocabulary from the prose, then counts cardinality across the corpus.">
              <button className="pill" disabled={running}
                      onClick={() => launch('/induce', { data_dir: dataDir }, 'induce')}>induce</button>
            </Step>
          )}

          <Step n={usesInduction ? '03' : '02'} done={!!built} title="build the graph"
                hint="Parse, resolve entities, arbitrate contradictions, write claims and provenance edges.">
            <button className="pill" disabled={running}
                    onClick={() => launch('/ingest', { tier_b: tierB, source, products: 5 }, 'ingest')}>build</button>
          </Step>
        </div>

        {running && <div className="working" style={{ marginTop: 18 }}>working on {kind}</div>}
        {status === 'failed' && <div className="failed" style={{ marginTop: 18 }}>{kind} failed, see the log</div>}
        {log.length > 0 && <pre className="log" ref={logRef}>{log.join('\n')}</pre>}

        {induced && (
          <div className="block">
            <div className="label">
              ontology derived: {induced.predicates.length} predicates, {induced.sources} sources,
              {' '}{induced.rules} field rules
            </div>
            <table className="preds">
              <thead>
                <tr><th>predicate</th><th>domain</th><th>range</th><th>cardinality</th><th>counted</th></tr>
              </thead>
              <tbody>
                {induced.predicates.map((p) => (
                  <tr key={p.name}>
                    <td className="name">{p.name}</td>
                    <td>{p.domain.join(' ')}</td>
                    <td>{p.range.join(' ')}</td>
                    <td className={p.cardinality === 'one' ? 'one' : ''}>{p.cardinality}</td>
                    <td className="obs">{p.observed ? `${p.observed} obj/subj` : 'unobserved'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="subline prose" style={{ marginTop: 16 }}>
              Cardinality is counted, not guessed. A predicate whose subjects hold a single object is
              functional, so two sources claiming different values is a contradiction to arbitrate.
            </p>
          </div>
        )}

        {built && (
          <div className="block">
            <div className="label">graph written</div>
            <div className="built">
              <span className="badge ember">{built.nodes.toLocaleString()} nodes</span>
              <span className="badge ember">{built.edges.toLocaleString()} edges</span>
              {Object.entries(built.by_label).map(([k, v]) => (
                <span key={k} className="badge quiet">{k} {v.toLocaleString()}</span>
              ))}
            </div>
            <div className="controls">
              <button className="pill" onClick={onReady}>ask questions</button>
            </div>
          </div>
        )}
      </div>
    </section>
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
