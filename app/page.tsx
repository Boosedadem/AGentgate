import Link from "next/link";

const quickstart = [
  "npm install",
  "npm run setup:api",
  "npm run api",
  "npm run dashboard",
  "npm run demo:agent",
];

const supportedControls = [
  {
    title: "Backend-owned decisions",
    copy:
      "The Python SDK sends every decorated tool call to the FastAPI policy service before the wrapped function executes.",
  },
  {
    title: "Hard blocks and kill switch",
    copy:
      "Rate limits, value caps, and the kill switch are evaluated in the backend. Blocked actions do not run in the demo agent.",
  },
  {
    title: "Manual approval path",
    copy:
      "High-risk calls pause at the gate, wait for operator approval in the dashboard, and only execute after a release decision.",
  },
  {
    title: "Operational feed",
    copy:
      "The dashboard streams persisted action records from SQLite, including gate decision, final resolution, and rule context.",
  },
];

const scopeNotes = [
  "Python SDK with decorator-based interception",
  "FastAPI + SQLite backend for local evaluation and action history",
  "Next.js dashboard for feed visibility, approvals, kill switch, and rule editing",
  "Demo support agent that exercises the real gate path",
];

const limitations = [
  "No hosted control plane or multi-tenant auth",
  "No LangChain auto-discovery in this repo",
  "No non-Python SDKs yet",
  "Rules are local to the SQLite store on your machine",
];

export default function LandingPage() {
  return (
    <div className="landing-page">
      <header className="topbar">
        <Link href="/" className="brand">
          <span className="brand-mark">AG</span>
          <span>AgentGate</span>
        </Link>
        <nav className="topbar-links">
          <a href="#quickstart" className="topbar-link">
            Quickstart
          </a>
          <a href="#architecture" className="topbar-link">
            Architecture
          </a>
          <Link href="/dashboard" className="topbar-cta">
            Open dashboard
          </Link>
        </nav>
      </header>

      <main className="landing-main">
        <section className="hero-section">
          <div className="eyebrow">Local runtime control for agent tool calls</div>
          <div className="hero-layout">
            <div className="hero-copy-block">
              <h1 className="hero-title">
                Decide before an agent call executes.
              </h1>
              <p className="hero-copy">
                This repository ships a real local control path: Python SDK
                interception, FastAPI policy evaluation, SQLite-backed action
                records, and a dashboard that reflects backend state.
              </p>
              <div className="hero-actions">
                <Link href="/dashboard" className="button button-primary">
                  Open dashboard
                </Link>
                <a href="#quickstart" className="button button-secondary">
                  Read quickstart
                </a>
              </div>
            </div>

            <div className="hero-panel">
              <div className="panel-kicker">What this repo actually includes</div>
              <ul className="bullet-list">
                {scopeNotes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <section className="section" id="quickstart">
          <div className="section-header">
            <div className="section-eyebrow">Quickstart</div>
            <h2>Run the full local path.</h2>
            <p>
              Start the backend, the dashboard, and the demo agent in separate
              terminals. The dashboard is only useful once the API and agent are
              running.
            </p>
          </div>
          <div className="grid-two">
            <div className="command-card">
              <div className="panel-kicker">Commands</div>
              <pre className="command-block">
                <code>{quickstart.join("\n")}</code>
              </pre>
              <p className="command-note">
                Need a clean local run? <code>npm run reset:state</code> clears
                the SQLite store.
              </p>
            </div>
            <div className="code-card">
              <div className="panel-kicker">Python SDK example</div>
              <pre className="code-block">
                <code>{`import agentgate

ag = agentgate.init(
    api_key="ag_demo_key_001",
    api_url="http://localhost:8000",
    agent_name="support-agent-prod",
)

@ag.tool(rate_limit=5, rate_window="minute", max_value={"amount": 200})
def issue_refund(user_id: str, amount: float):
    ...

@ag.tool(require_approval=True, approval_timeout=90)
def delete_account(user_id: str, reason: str):
    ...
`}</code>
              </pre>
            </div>
          </div>
        </section>

        <section className="section" id="architecture">
          <div className="section-header">
            <div className="section-eyebrow">Architecture</div>
            <h2>The real path in this repository.</h2>
            <p>
              There is still no hosted service here. This is a local control
              plane, but the call path itself is real.
            </p>
          </div>
          <div className="feature-grid">
            {supportedControls.map((item) => (
              <article className="feature-card" key={item.title}>
                <div className="feature-label">{item.title}</div>
                <p>{item.copy}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="section">
          <div className="section-header">
            <div className="section-eyebrow">Current scope</div>
            <h2>Honest boundaries.</h2>
            <p>
              The repo is stronger when it says exactly what it does and what it
              still does not do.
            </p>
          </div>
          <div className="grid-two">
            <div className="scope-card">
              <div className="panel-kicker">Implemented now</div>
              <ul className="bullet-list">
                {scopeNotes.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
            <div className="scope-card">
              <div className="panel-kicker">Still missing</div>
              <ul className="bullet-list">
                {limitations.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}
