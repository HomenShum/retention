import { Link } from 'react-router-dom'
import { Terminal, ArrowRight, ChevronRight, Eye, ShieldCheck, RotateCcw, Activity, Code2, Puzzle } from 'lucide-react'
import { useState } from 'react'

const INSTALL_CMD = 'curl -sL retention.sh/install.sh | bash'

const PAIN_QUOTES = [
  "You didn't run the tests.",
  "Where's the search step? You skipped the research.",
  "You didn't check the console for errors.",
  "I asked you to QA all 5 surfaces, not just the landing page.",
  "The deploy is broken. The agent said it was done.",
]

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 2000) }}
      className="text-xs px-2 py-1 rounded bg-white/10 hover:bg-white/20 transition-colors text-text-secondary"
    >
      {copied ? 'Copied!' : 'Copy'}
    </button>
  )
}

function InstallBlock() {
  return (
    <div className="inline-flex items-center gap-3 px-5 py-3 rounded-xl bg-bg-card border border-border-muted font-mono text-sm">
      <span className="text-accent">$</span>
      <code className="text-text-primary">{INSTALL_CMD}</code>
      <CopyButton text={INSTALL_CMD} />
    </div>
  )
}

export function Landing() {
  return (
    <div className="min-h-screen bg-bg-primary">
      {/* Nav */}
      <header className="fixed top-0 w-full z-50 backdrop-blur-md bg-bg-primary/80 border-b border-border-subtle">
        <div className="max-w-5xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-2 text-accent font-semibold">
            <Terminal className="w-5 h-5" />
            retention.sh
          </div>
          <nav className="hidden sm:flex items-center gap-6 text-sm text-text-secondary">
            <a href="#pain" className="hover:text-text-primary transition-colors no-underline">The problem</a>
            <a href="#how" className="hover:text-text-primary transition-colors no-underline">How it works</a>
            <a href="#sdk" className="hover:text-text-primary transition-colors no-underline">SDK</a>
            <Link to="/dashboard" className="px-3 py-1.5 rounded-lg bg-accent text-black font-medium hover:bg-accent-muted transition-colors no-underline text-sm">
              Dashboard
            </Link>
          </nav>
        </div>
      </header>

      {/* Hero — pain first */}
      <section className="pt-28 pb-14 px-6">
        <div className="max-w-3xl mx-auto text-center">
          <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold tracking-tight leading-[1.1] mb-6">
            Your AI agent says{' '}
            <span className="text-text-muted line-through decoration-danger/60">"Done!"</span>
            <br />
            <span className="text-danger">It isn't.</span>
          </h1>
          <p className="text-lg text-text-secondary max-w-xl mx-auto mb-10 leading-relaxed">
            You're correcting the same mistakes every session.
            Skipped tests. Missing steps. Forgotten context.
            retention.sh watches every tool call and blocks incomplete work.
          </p>

          <InstallBlock />

          <div className="flex items-center justify-center gap-4 mt-6">
            <Link
              to="/dashboard"
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg bg-accent text-black font-semibold text-sm hover:bg-accent-muted transition-colors no-underline"
            >
              Open Dashboard <ArrowRight className="w-4 h-4" />
            </Link>
            <a
              href="https://github.com/HomenShum/retention"
              target="_blank"
              rel="noopener"
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg border border-border-muted text-text-secondary text-sm hover:text-text-primary hover:border-border-subtle transition-colors no-underline"
            >
              GitHub <ChevronRight className="w-3.5 h-3.5" />
            </a>
          </div>
        </div>
      </section>

      {/* Pain quotes — things you've actually said */}
      <section id="pain" className="py-14 px-6 border-t border-border-subtle">
        <div className="max-w-3xl mx-auto">
          <h2 className="text-xl font-bold text-center mb-2">Sound familiar?</h2>
          <p className="text-text-muted text-center text-sm mb-8">Things you've said to your AI agent this week.</p>
          <div className="space-y-3">
            {PAIN_QUOTES.map((q, i) => (
              <div key={i} className="flex items-start gap-3 p-4 rounded-xl bg-bg-card border border-danger/10">
                <span className="text-danger text-lg leading-none mt-0.5">"</span>
                <p className="text-text-secondary text-sm italic">{q}</p>
              </div>
            ))}
          </div>
          <p className="text-center text-text-muted text-sm mt-8">
            Every correction costs tokens, time, and trust.{' '}
            <span className="text-text-primary font-medium">retention.sh makes these impossible.</span>
          </p>
        </div>
      </section>

      {/* How it works — before/after */}
      <section id="how" className="py-14 px-6 border-t border-border-subtle">
        <div className="max-w-4xl mx-auto">
          <h2 className="text-2xl font-bold text-center mb-10">Without vs. with retention.sh</h2>
          <div className="grid sm:grid-cols-2 gap-6">
            {/* Without */}
            <div className="p-6 rounded-xl bg-bg-card border border-danger/20">
              <h3 className="text-sm font-semibold text-danger mb-4">Without retention.sh</h3>
              <div className="space-y-2.5 text-sm">
                {[
                  { text: 'Agent receives task', dim: false },
                  { text: 'Implements the code', dim: false },
                  { text: 'Skips tests', dim: true, strike: true },
                  { text: 'Skips search', dim: true, strike: true },
                  { text: 'Agent says "Done!"', dim: false },
                  { text: 'You: "You forgot the tests..."', dim: true },
                  { text: '2000 tokens wasted on correction', dim: true },
                  { text: 'You: "Also the search..."', dim: true },
                  { text: '1500 more tokens wasted', dim: true },
                ].map(({ text, dim, strike }, i) => (
                  <div key={i} className={`${dim ? 'text-text-muted' : 'text-text-secondary'} ${strike ? 'line-through' : ''}`}>
                    {text}
                  </div>
                ))}
              </div>
            </div>
            {/* With */}
            <div className="p-6 rounded-xl bg-bg-card border border-accent/20">
              <h3 className="text-sm font-semibold text-accent mb-4">With retention.sh</h3>
              <div className="space-y-2.5 text-sm">
                {[
                  { text: 'Agent receives task', accent: false },
                  { text: 'on-prompt: injects 5 required steps', accent: true },
                  { text: 'Implements the code', accent: false },
                  { text: 'on-tool-use: tracks evidence (3/5 done)', accent: true },
                  { text: 'Agent tries to stop', accent: false },
                  { text: 'on-stop: BLOCKED — missing: tests, search', accent: true },
                  { text: 'Agent runs tests + search', accent: false },
                  { text: 'on-stop: PASSED — all 5 steps complete', accent: true },
                  { text: 'Saved: 3500 tokens + 0 corrections', accent: true },
                ].map(({ text, accent }, i) => (
                  <div key={i} className={accent ? 'text-accent font-mono text-xs' : 'text-text-secondary'}>
                    {text}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* 4 hooks */}
      <section className="py-14 px-6 border-t border-border-subtle">
        <div className="max-w-4xl mx-auto">
          <h2 className="text-2xl font-bold text-center mb-4">4 hooks. Always on.</h2>
          <p className="text-text-muted text-center text-sm mb-10">
            Fires on every prompt, tool call, and session. No opt-out.
          </p>
          <div className="grid sm:grid-cols-2 gap-4">
            {[
              { icon: Eye, hook: 'on-session-start', desc: 'Resumes prior incomplete work. Remembers what was left undone.' },
              { icon: Code2, hook: 'on-prompt', desc: 'Detects workflow type. Injects required steps before the agent starts.' },
              { icon: Activity, hook: 'on-tool-use', desc: 'Every tool call is tracked as evidence. Nudges if steps are missing.' },
              { icon: ShieldCheck, hook: 'on-stop', desc: 'The gate. Blocks completion if mandatory steps are incomplete.' },
            ].map(({ icon: Icon, hook, desc }) => (
              <div key={hook} className="flex gap-4 p-5 rounded-xl bg-bg-card border border-border-subtle">
                <Icon className="w-5 h-5 text-accent shrink-0 mt-0.5" />
                <div>
                  <code className="text-sm font-semibold text-accent">{hook}</code>
                  <p className="text-text-secondary text-sm mt-1">{desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* SDK — one-line install per provider */}
      <section id="sdk" className="py-14 px-6 border-t border-border-subtle">
        <div className="max-w-3xl mx-auto">
          <h2 className="text-2xl font-bold text-center mb-4">One line. Any agent.</h2>
          <p className="text-text-muted text-center text-sm mb-10">
            Auto-detects your provider and starts tracking. No config.
          </p>
          <div className="space-y-3">
            {[
              { label: 'Any provider (auto-detect)', code: 'from retention import track\ntrack()' },
              { label: 'OpenAI', code: 'from retention import track\ntrack(providers=["openai"])' },
              { label: 'Anthropic', code: 'from retention import track\ntrack(providers=["anthropic"])' },
              { label: 'OpenAI Agents SDK', code: 'from retention import track\ntrack(providers=["openai_agents"])' },
              { label: 'LangChain', code: 'from retention import track\ntrack(providers=["langchain"])' },
              { label: 'CrewAI', code: 'from retention import track\ntrack(providers=["crewai"])' },
            ].map(({ label, code }) => (
              <div key={label} className="p-4 rounded-xl bg-bg-card border border-border-subtle">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-text-muted">{label}</span>
                  <Puzzle className="w-3.5 h-3.5 text-text-muted" />
                </div>
                <pre className="font-mono text-xs text-accent whitespace-pre">{code}</pre>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Proof */}
      <section className="py-14 px-6 border-t border-border-subtle">
        <div className="max-w-3xl mx-auto">
          <h2 className="text-2xl font-bold text-center mb-4">Measured, not promised</h2>
          <p className="text-text-muted text-center text-sm mb-10">
            Real API calls. Independent LLM judge. Reproducible.
          </p>
          <div className="grid grid-cols-3 gap-4 mb-8">
            {[
              { value: '63-73%', label: 'Cost savings on reruns' },
              { value: '89%', label: 'Judge agreement rate' },
              { value: '0', label: 'Corrections needed' },
            ].map(({ value, label }) => (
              <div key={label} className="text-center p-5 rounded-xl bg-bg-card border border-border-subtle">
                <div className="text-2xl font-bold text-accent">{value}</div>
                <div className="text-text-muted text-xs mt-1">{label}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-14 px-6 border-t border-border-subtle">
        <div className="max-w-2xl mx-auto text-center">
          <h2 className="text-2xl font-bold mb-2">Stop correcting. Start shipping.</h2>
          <p className="text-text-muted text-sm mb-6">60 seconds to install. Works with Claude Code, Cursor, Windsurf, or any MCP client.</p>
          <InstallBlock />
          <p className="text-text-muted text-sm mt-6">
            Or add telemetry to any Python agent:
          </p>
          <div className="inline-block mt-3 px-5 py-3 rounded-xl bg-bg-card border border-border-muted font-mono text-sm text-left">
            <div className="text-text-muted text-xs mb-1"># pip install retention</div>
            <div><span className="text-accent">from</span> retention <span className="text-accent">import</span> track</div>
            <div>track()</div>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-8 px-6 border-t border-border-subtle">
        <div className="max-w-5xl mx-auto flex items-center justify-between text-xs text-text-muted">
          <span>retention.sh</span>
          <div className="flex gap-4">
            <a href="https://github.com/HomenShum/retention" target="_blank" rel="noopener" className="hover:text-text-secondary transition-colors no-underline">GitHub</a>
            <Link to="/dashboard" className="hover:text-text-secondary transition-colors no-underline">Dashboard</Link>
          </div>
        </div>
      </footer>
    </div>
  )
}
