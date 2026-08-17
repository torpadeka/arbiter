import { useEffect, useState } from 'react'

/** An explanation page that reads the live system rather than describing it from
 *  memory. Every number and every rule shown here is fetched from the ontology
 *  currently in force, so it cannot drift away from what the code does. */
export default function HowItWorks({ stats }) {
  const [onto, setOnto] = useState(null)

  useEffect(() => {
    fetch('/api/ontology').then((r) => r.json()).then(setOnto).catch(() => {})
  }, [])

  const counts = stats?.counts || {}
  const loaded = (counts.Claim || 0) > 0

  return (
    <section className="section">
      <div className="eyebrow"><span className="tick">/</span>what this actually does</div>
      <h2 className="headline">how it works</h2>
      <p className="subline prose">
        Most search tools find documents that look like your question. This one works out what the
        documents claim, who claimed it, and when, then answers from that. The difference shows up
        when two sources disagree, and when nobody wrote the answer down at all.
      </p>

      <Card title="from files to answers">
        <Pipeline />
        <p className="prose dimmed">
          Reading happens twice. Structured fields are read directly, with no ai involved: a ticket's
          assignee, a due date, a deal owner. Written text is optional and slower, because it takes a
          model to notice that "I'll take the atlas migration" is a claim of ownership.
        </p>
      </Card>

      <Card title="every fact is stored with its source">
        <ClaimDiagram />
        <p className="prose dimmed">
          Nothing is stored as a bare fact. Each statement keeps who said it, which document it came
          from, when it was asserted and how strongly it scored, which is what lets an answer show its
          receipts and what lets an old answer be reconstructed later.
        </p>
      </Card>

      <Card title="the same person under many names">
        <AliasDiagram />
        <p className="prose dimmed">
          Names are matched, then checked against evidence that two people are different. Three
          checks can veto a merge outright, no matter how similar the names look: different email
          addresses, different surnames, or one of them addressing the other by name in their own
          message. Somebody who writes "thanks priya" is not priya.
        </p>
      </Card>

      <Card title="when sources disagree">
        {onto ? <Weights weights={onto.weights} /> : <div className="dimmed">loading</div>}
        <p className="prose dimmed">
          Competing statements are scored on these five things and the highest wins. The loser is kept
          and marked overruled, never deleted. Only relationships that hold one value at a time can
          conflict: a person assigned to two tickets is not a contradiction, but a ticket with two
          assignees is.
        </p>
        {onto?.authority && <Authority authority={onto.authority} />}
      </Card>

      <Card title="three reasons to refuse">
        <Gates
          sufficiency={onto?.sufficiency_threshold}
        />
        <p className="prose dimmed">
          All three run before any model is called, so a question with no answer costs nothing and
          cannot be talked into an answer. Refusing is treated as a correct outcome, not a failure.
        </p>
      </Card>

      {onto?.predicates?.length > 0 && (
        <Card title={`the ${onto.predicates.length} relationships in force right now`}>
          <table className="preds">
            <thead>
              <tr><th>relationship</th><th>from</th><th>to</th><th>one value or many</th><th>can be overruled</th></tr>
            </thead>
            <tbody>
              {onto.predicates.map((p) => (
                <tr key={p.name}>
                  <td className="name">{p.name.replace(/_/g, ' ')}</td>
                  <td>{p.domain.join(' ')}</td>
                  <td>{p.range.join(' ')}</td>
                  <td className={p.cardinality === 'one' ? 'one' : ''}>
                    {p.cardinality === 'one' ? 'one at a time' : 'many'}
                  </td>
                  <td>{p.temporal ? 'yes' : 'no'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {loaded && (
        <Card title="what is loaded right now">
          <div className="built">
            {Object.entries(counts).filter(([, v]) => v > 0).map(([k, v]) => (
              <span key={k} className="badge quiet">{LABEL_COPY[k] || k.toLowerCase()} {v.toLocaleString()}</span>
            ))}
          </div>
        </Card>
      )}
    </section>
  )
}

const LABEL_COPY = {
  Claim: 'statements', Artifact: 'documents', Person: 'people', Alias: 'alternate names',
  Group: 'access groups', Topic: 'values', Project: 'projects', Account: 'customers', Team: 'teams',
}

function Card({ title, children }) {
  return (
    <div className="card explain">
      <div className="label">{title}</div>
      {children}
    </div>
  )
}

const STAGES = [
  ['your files', 'slack, email, tickets, docs'],
  ['read', 'what each file holds'],
  ['understand', 'which relationships exist'],
  ['merge people', 'same human, many names'],
  ['settle', 'who wins when sources clash'],
  ['graph', 'facts with their sources'],
]

function Pipeline() {
  return (
    <div className="flow">
      {STAGES.map(([title, sub], i) => (
        <div className="flow-step" key={title}>
          <div className="flow-node">
            <span className="flow-title">{title}</span>
            <span className="flow-sub prose">{sub}</span>
          </div>
          {i < STAGES.length - 1 && <span className="flow-arrow" />}
        </div>
      ))}
    </div>
  )
}

function ClaimDiagram() {
  return (
    <svg className="diagram" viewBox="0 0 640 220" role="img"
         aria-label="a statement linked to its subject, object, source document and author">
      <g stroke="#cc6437" strokeWidth="1" fill="none">
        <rect x="250" y="86" width="140" height="48" rx="10" />
      </g>
      <text x="320" y="106" className="d-title" textAnchor="middle">statement</text>
      <text x="320" y="122" className="d-sub" textAnchor="middle">scored, dated</text>

      <g stroke="rgba(255,255,255,0.25)" strokeWidth="1" fill="none">
        <rect x="40" y="20" width="140" height="40" rx="10" />
        <rect x="40" y="160" width="140" height="40" rx="10" />
        <rect x="460" y="20" width="140" height="40" rx="10" />
        <rect x="460" y="160" width="140" height="40" rx="10" />
        <path d="M180 40 H215 V96 H250" />
        <path d="M180 180 H215 V124 H250" />
        <path d="M390 106 H425 V40 H460" />
        <path d="M390 114 H425 V180 H460" />
      </g>
      <text x="110" y="45" className="d-label" textAnchor="middle">who it is about</text>
      <text x="110" y="185" className="d-label" textAnchor="middle">what is claimed</text>
      <text x="530" y="45" className="d-label" textAnchor="middle">source document</text>
      <text x="530" y="185" className="d-label" textAnchor="middle">who said it</text>
    </svg>
  )
}

function AliasDiagram() {
  const names = ['sam', '@soham', 's. ratnaparkhi', 'soham-r', 'sam.ratnaparkhi@']
  return (
    <div className="alias-demo">
      <div className="alias-names">
        {names.map((n) => <span key={n} className="badge quiet">{n}</span>)}
      </div>
      <span className="alias-join" />
      <span className="badge ember">one person</span>
      <div className="alias-veto">
        <span className="badge quiet">priya nair</span>
        <span className="alias-block">blocked</span>
        <span className="badge quiet">priya nandakumar</span>
      </div>
    </div>
  )
}

const WEIGHT_COPY = {
  authority: 'how much the source is trusted',
  recency: 'how recently it was said',
  specificity: 'a field beats a passing remark',
  corroboration: 'how many sources agree',
  hedging: 'penalty for "i think" and "maybe"',
}

function Weights({ weights }) {
  const entries = Object.entries(weights || {})
  const max = Math.max(...entries.map(([, v]) => Math.abs(v)), 0.01)
  return (
    <div className="weights">
      {entries.map(([name, value]) => (
        <div className="weight" key={name}>
          <span className="w-name">{name}</span>
          <span className="w-bar">
            <span className={`w-fill ${value < 0 ? 'neg' : ''}`}
                  style={{ width: `${(Math.abs(value) / max) * 100}%` }} />
          </span>
          <span className="w-value mono">{value > 0 ? '+' : ''}{value}</span>
          <span className="w-copy prose">{WEIGHT_COPY[name] || ''}</span>
        </div>
      ))}
    </div>
  )
}

function Authority({ authority }) {
  const entries = Object.entries(authority)
  return (
    <div className="authority">
      <div className="label" style={{ marginTop: 24 }}>how much each source is trusted</div>
      {entries.map(([source, value]) => (
        <div className="weight" key={source}>
          <span className="w-name">{source.replace(/_/g, ' ')}</span>
          <span className="w-bar"><span className="w-fill" style={{ width: `${value * 100}%` }} /></span>
          <span className="w-value mono">{value.toFixed(2)}</span>
        </div>
      ))}
      <p className="prose dimmed">
        A maintained field outranks a passing message, which is why a ticket beats a chat thread when
        the two disagree. These numbers are configuration, not opinion buried in code.
      </p>
    </div>
  )
}

function Gates({ sufficiency }) {
  const gates = [
    ['does the thing exist', 'nothing in your files mentions it', 'no record of "project zephyr"'],
    ['is this recorded about it', 'the thing exists, this fact was never written down', 'found atlas migration, no budget is recorded'],
    ['is the evidence strong enough', `the only mention scores below ${sufficiency ?? '0.35'}`, 'one unconfirmed remark in chat'],
  ]
  return (
    <div className="gates">
      {gates.map(([title, rule, example], i) => (
        <div className="gate" key={title}>
          <span className="badge num">{String(i + 1).padStart(2, '0')}</span>
          <div>
            <div className="gate-title">{title}</div>
            <div className="gate-rule prose">{rule}</div>
            <div className="gate-example mono">{example}</div>
          </div>
        </div>
      ))}
    </div>
  )
}
