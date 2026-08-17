import { useEffect, useRef, useState } from 'react'

function post(path, body) {
  return fetch(`/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  }).then((r) => r.json())
}

/** Reset, induce, build. The log is whatever the pipeline actually printed. */
export default function Pipeline({ onChanged }) {
  const [dataDir, setDataDir] = useState('data/raw')
  const [source, setSource] = useState('folder')
  const [tierB, setTierB] = useState(false)
  const [job, setJob] = useState(null)
  const [log, setLog] = useState([])
  const [status, setStatus] = useState('')
  const [induced, setInduced] = useState(null)
  const [built, setBuilt] = useState(null)
  const logRef = useRef(null)

  useEffect(() => {
    if (!job || status === 'done' || status === 'failed') return
    const timer = setInterval(async () => {
      const d = await fetch(`/api/job/${job}`).then((r) => r.json())
      setLog(d.log || [])
      setStatus(d.status)
      if (d.status === 'done' || d.status === 'failed') {
        clearInterval(timer)
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

  async function launch(path, body) {
    setLog([])
    setStatus('running')
    const d = await post(path, body)
    setJob(d.job)
  }

  const running = status === 'running'

  return (
    <section className="card pipeline">
      <h2>Corpus</h2>

      <div className="pipe-row">
        <select value={source} onChange={(e) => setSource(e.target.value)} disabled={running}>
          <option value="folder">any folder (ontology induced)</option>
          <option value="herb">Salesforce HERB (built-in adapter)</option>
        </select>
        {source === 'folder' && (
          <input value={dataDir} onChange={(e) => setDataDir(e.target.value)} disabled={running}
                 placeholder="folder of exports" />
        )}
        <label className="check">
          <input type="checkbox" checked={tierB} onChange={(e) => setTierB(e.target.checked)} disabled={running} />
          tier B (LLM over free text)
        </label>
      </div>

      <div className="pipe-row">
        <button className="ghost" disabled={running} onClick={() => launch('/reset')}>
          1 · Reset graph
        </button>
        <button className="ghost" disabled={running || source === 'herb'}
                onClick={() => launch('/induce', { data_dir: dataDir })}>
          2 · Induce ontology
        </button>
        <button className="primary" disabled={running}
                onClick={() => launch('/ingest', { tier_b: tierB, source, products: 5 })}>
          3 · Build graph
        </button>
        {running && <span className="spin">working…</span>}
        {status === 'failed' && <span className="failed">failed</span>}
      </div>

      {log.length > 0 && (
        <pre className="log" ref={logRef}>{log.join('\n')}</pre>
      )}

      {induced && (
        <div className="induced">
          <div className="induced-head">
            <b>{induced.predicates.length}</b> predicates ·
            <b> {induced.sources}</b> sources ·
            <b> {induced.rules}</b> field rules, none written by hand
          </div>
          <table className="preds">
            <thead>
              <tr><th>predicate</th><th>domain</th><th>range</th><th>cardinality</th><th title="distinct objects per subject, counted in the corpus">observed</th></tr>
            </thead>
            <tbody>
              {induced.predicates.map((p) => (
                <tr key={p.name}>
                  <td className="pname">{p.name}</td>
                  <td>{p.domain.join('|')}</td>
                  <td>{p.range.join('|')}</td>
                  <td className={p.cardinality === 'one' ? 'one' : 'many'}>{p.cardinality}</td>
                  <td>{p.observed ? `${p.observed} obj/subj` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="note">
            Cardinality is counted, not guessed: a predicate whose subjects hold one object is
            functional, so competing values are a contradiction to arbitrate.
          </p>
        </div>
      )}

      {built && (
        <div className="built">
          <b>{built.nodes.toLocaleString()}</b> nodes · <b>{built.edges.toLocaleString()}</b> edges
          <div className="labels">
            {Object.entries(built.by_label).map(([k, v]) => (
              <span key={k}>{k} <b>{v.toLocaleString()}</b></span>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
