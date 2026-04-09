import { Link } from 'react-router-dom'
import { Terminal, ArrowRight, ChevronRight, ShieldCheck, RotateCcw, Eye, Sparkles, Users, Code2 } from 'lucide-react'
import { useState } from 'react'

const INSTALL_CMD = 'curl -sL retention.sh/install.sh | bash'

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

function InstallBlock({ className = '' }: { className?: string }) {
  return (
    <div className={`inline-flex items-center gap-3 px-5 py-3 rounded-xl bg-bg-card border border-border-muted font-mono text-sm ${className}`}>
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
            <a href="#menu" className="hover:text-text-primary transition-colors no-underline">What we do</a>
            <a href="#proof" className="hover:text-text-primary transition-colors no-underline">Proof</a>
            <a href="#start" className="hover:text-text-primary transition-colors no-underline">Try it</a>
            <Link to="/dashboard" className="px-3 py-1.5 rounded-lg bg-accent text-black font-medium hover:bg-accent-muted transition-colors no-underline text-sm">
              Dashboard
            </Link>
          </nav>
        </div>
      </header>

      {/* ============================================ */}
      {/* SIGNATURE DISH — the one thing people get */}
      {/* ============================================ */}
      <section className="pt-28 pb-16 px-6">
        <div className="max-w-3xl mx-auto text-center">
          <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold tracking-tight leading-[1.1] mb-6">
            See what your AI agent
            <br />
            <span className="text-danger">actually missed.</span>
          </h1>
          <p className="text-lg text-text-secondary max-w-lg mx-auto mb-4 leading-relaxed">
            Your agent says "done." retention.sh shows you the skipped tests,
            the forgotten steps, and the missing context — then blocks it
            from happening again.
          </p>
          <p className="text-sm text-text-muted mb-8">
            60 seconds to install. Works with Claude Code, Cursor, Windsurf.
          </p>
          <InstallBlock />
          <div className="flex items-center justify-center gap-4 mt-6">
            <a href="#start" className="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg bg-accent text-black font-semibold text-sm hover:bg-accent-muted transition-colors no-underline">
              Try the tasting menu <ArrowRight className="w-4 h-4" />
            </a>
            <a href="https://github.com/HomenShum/retention" target="_blank" rel="noopener" className="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg border border-border-muted text-text-secondary text-sm hover:text-text-primary transition-colors no-underline">
              GitHub <ChevronRight className="w-3.5 h-3.5" />
            </a>
          </div>
        </div>
      </section>

      {/* ============================================ */}
      {/* THE MENU — 3 dishes, not the whole kitchen  */}
      {/* ============================================ */}
      <section id="menu" className="py-14 px-6 border-t border-border-subtle">
        <div className="max-w-4xl mx-auto">
          <div className="text-center mb-10">
            <p className="text-[11px] uppercase tracking-[0.2em] text-text-muted mb-2">The menu</p>
            <h2 className="text-2xl font-bold">Three things we do. That's it.</h2>
          </div>

          <div className="grid sm:grid-cols-3 gap-5">
            {/* Dish 1: Workflow Judge */}
            <div className="p-6 rounded-xl bg-bg-card border border-border-subtle flex flex-col">
              <ShieldCheck className="w-6 h-6 text-accent mb-4" />
              <h3 className="font-semibold mb-1">Workflow Judge</h3>
              <p className="text-text-muted text-xs mb-4">The signature dish</p>
              <p className="text-text-secondary text-sm leading-relaxed flex-1">
                See what the agent did, what it missed, and whether it should
                have kept going. Hard verdict: PASS, FAIL, or BLOCKED.
              </p>
              <div className="mt-4 pt-4 border-t border-border-subtle">
                <p className="text-xs text-text-muted italic">
                  "Stop re-explaining the same steps every time."
                </p>
              </div>
            </div>

            {/* Dish 2: Replay Kit */}
            <div className="p-6 rounded-xl bg-bg-card border border-border-subtle flex flex-col">
              <RotateCcw className="w-6 h-6 text-accent mb-4" />
              <h3 className="font-semibold mb-1">Replay Kit</h3>
              <p className="text-text-muted text-xs mb-4">Save money on repeat work</p>
              <p className="text-text-secondary text-sm leading-relaxed flex-1">
                Capture one expensive workflow. Replay it at 60-70% lower cost.
                Strict judge verifies the replay actually worked.
              </p>
              <div className="mt-4 pt-4 border-t border-border-subtle">
                <p className="text-xs text-text-muted italic">
                  "Replay the same workflow cheaper, with proof it still works."
                </p>
              </div>
            </div>

            {/* Dish 3: Run Anatomy */}
            <div className="p-6 rounded-xl bg-bg-card border border-border-subtle flex flex-col">
              <Eye className="w-6 h-6 text-accent mb-4" />
              <h3 className="font-semibold mb-1">Run Anatomy</h3>
              <p className="text-text-muted text-xs mb-4">See what actually happened</p>
              <p className="text-text-secondary text-sm leading-relaxed flex-1">
                Full trace of every tool call, with screenshots, evidence,
                and per-step cost. Shareable link for your team.
              </p>
              <div className="mt-4 pt-4 border-t border-border-subtle">
                <p className="text-xs text-text-muted italic">
                  "Here's what happened. Here's what got skipped."
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ============================================ */}
      {/* WHO THIS IS FOR — 3 customer types           */}
      {/* ============================================ */}
      <section className="py-14 px-6 border-t border-border-subtle">
        <div className="max-w-4xl mx-auto">
          <div className="text-center mb-10">
            <p className="text-[11px] uppercase tracking-[0.2em] text-text-muted mb-2">Who sits at the table</p>
            <h2 className="text-2xl font-bold">Built for people who use AI agents daily</h2>
          </div>

          <div className="grid sm:grid-cols-3 gap-5">
            {[
              {
                icon: Code2,
                who: 'Engineers',
                pain: 'Agent keeps skipping tests and search steps',
                promise: 'Catch skipped steps. Replay repeated workflows cheaper.',
              },
              {
                icon: Users,
                who: 'Team Leads',
                pain: 'No visibility into what agents actually did',
                promise: 'See what happened, what was missed, where savings came from.',
              },
              {
                icon: Sparkles,
                who: 'Founders',
                pain: 'Repeating expensive AI work manually every time',
                promise: 'Turn repeated work into reusable operating leverage.',
              },
            ].map(({ icon: Icon, who, pain, promise }) => (
              <div key={who} className="p-5 rounded-xl bg-bg-card border border-border-subtle">
                <Icon className="w-5 h-5 text-accent mb-3" />
                <h3 className="font-semibold text-sm mb-1">{who}</h3>
                <p className="text-text-muted text-xs mb-3">Pain: {pain}</p>
                <p className="text-text-secondary text-sm">{promise}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ============================================ */}
      {/* PROOF WALL — receipts, not promises           */}
      {/* ============================================ */}
      <section id="proof" className="py-14 px-6 border-t border-border-subtle">
        <div className="max-w-4xl mx-auto">
          <div className="text-center mb-10">
            <p className="text-[11px] uppercase tracking-[0.2em] text-text-muted mb-2">Proof wall</p>
            <h2 className="text-2xl font-bold">Measured, not promised</h2>
            <p className="text-text-muted text-sm mt-2">Real API calls. Independent LLM judge. Reproducible.</p>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
            {[
              { value: '63-73%', label: 'Cost savings on reruns' },
              { value: '89%', label: 'Judge agreement' },
              { value: '0', label: 'Corrections needed' },
              { value: '21', label: 'Live API proof runs' },
            ].map(({ value, label }) => (
              <div key={label} className="text-center p-5 rounded-xl bg-bg-card border border-border-subtle">
                <div className="text-2xl font-bold text-accent">{value}</div>
                <div className="text-text-muted text-xs mt-1">{label}</div>
              </div>
            ))}
          </div>

          {/* Before/after — the case study snapshot */}
          <div className="grid sm:grid-cols-2 gap-5">
            <div className="p-5 rounded-xl bg-bg-card border border-danger/15">
              <h3 className="text-sm font-semibold text-danger mb-3">Without retention.sh</h3>
              <div className="space-y-2 text-sm">
                <div className="text-text-secondary">Agent implements code</div>
                <div className="text-text-muted line-through">Skips tests</div>
                <div className="text-text-muted line-through">Skips search</div>
                <div className="text-text-secondary">Says "Done!"</div>
                <div className="text-text-muted">You: "You forgot the tests..."</div>
                <div className="text-text-muted">2000 tokens wasted</div>
                <div className="text-text-muted">You: "Also the search..."</div>
                <div className="text-text-muted">1500 more tokens wasted</div>
              </div>
            </div>
            <div className="p-5 rounded-xl bg-bg-card border border-accent/15">
              <h3 className="text-sm font-semibold text-accent mb-3">With retention.sh</h3>
              <div className="space-y-2 text-sm">
                <div className="text-text-secondary">Agent implements code</div>
                <div className="text-accent font-mono text-xs">on-prompt: injects 5 required steps</div>
                <div className="text-accent font-mono text-xs">on-tool-use: evidence 3/5 done</div>
                <div className="text-text-secondary">Agent tries to stop</div>
                <div className="text-accent font-mono text-xs">on-stop: BLOCKED — missing: tests, search</div>
                <div className="text-text-secondary">Agent completes all steps</div>
                <div className="text-accent font-mono text-xs">on-stop: PASSED — 5/5 complete</div>
                <div className="text-accent font-semibold text-xs">Saved: 3500 tokens + 0 corrections</div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ============================================ */}
      {/* TASTING MENU — guided first experience        */}
      {/* ============================================ */}
      <section id="start" className="py-14 px-6 border-t border-border-subtle">
        <div className="max-w-3xl mx-auto">
          <div className="text-center mb-10">
            <p className="text-[11px] uppercase tracking-[0.2em] text-text-muted mb-2">Tasting menu</p>
            <h2 className="text-2xl font-bold">Try it in 5 steps</h2>
            <p className="text-text-muted text-sm mt-2">Bring one repeated workflow. See the difference.</p>
          </div>

          <div className="space-y-4">
            {[
              { n: '1', title: 'Install', detail: 'curl -sL retention.sh/install.sh | bash', mono: true },
              { n: '2', title: 'Point it at your app', detail: 'ta.qa_check(url="http://localhost:3000")', mono: true },
              { n: '3', title: 'See the trace', detail: 'Every tool call, screenshot, and evidence artifact — visible in the dashboard.' },
              { n: '4', title: 'See what was missed', detail: 'Verdict card shows PASS/FAIL/BLOCKED with the exact missing steps listed.' },
              { n: '5', title: 'Replay cheaper next time', detail: 'Same workflow, 60-70% fewer tokens. Strict judge confirms quality held.' },
            ].map(({ n, title, detail, mono }) => (
              <div key={n} className="flex gap-4 p-4 rounded-xl bg-bg-card border border-border-subtle">
                <div className="w-7 h-7 rounded-lg bg-accent/10 text-accent font-bold text-xs flex items-center justify-center shrink-0">
                  {n}
                </div>
                <div>
                  <h3 className="font-semibold text-sm">{title}</h3>
                  {mono
                    ? <code className="text-xs text-accent font-mono">{detail}</code>
                    : <p className="text-text-secondary text-sm mt-0.5">{detail}</p>
                  }
                </div>
              </div>
            ))}
          </div>

          {/* SDK callout */}
          <div className="mt-8 p-5 rounded-xl bg-bg-card border border-border-subtle text-center">
            <p className="text-sm text-text-secondary mb-3">
              Not using MCP? Add telemetry to any Python agent:
            </p>
            <div className="inline-block px-5 py-3 rounded-lg bg-bg-primary border border-border-muted font-mono text-sm text-left">
              <div className="text-text-muted text-xs"># pip install retention</div>
              <div><span className="text-accent">from</span> retention <span className="text-accent">import</span> track</div>
              <div>track()  <span className="text-text-muted"># auto-detects OpenAI, Anthropic, LangChain, CrewAI</span></div>
            </div>
          </div>
        </div>
      </section>

      {/* ============================================ */}
      {/* CTA — stop correcting                        */}
      {/* ============================================ */}
      <section className="py-14 px-6 border-t border-border-subtle">
        <div className="max-w-2xl mx-auto text-center">
          <h2 className="text-2xl font-bold mb-2">Stop correcting. Start shipping.</h2>
          <p className="text-text-muted text-sm mb-6">
            The agent gets better every run. Your workflows get cheaper every replay.
          </p>
          <InstallBlock />
          <div className="mt-6">
            <Link to="/dashboard" className="inline-flex items-center gap-2 text-accent text-sm font-medium hover:underline no-underline">
              Open the dashboard <ArrowRight className="w-3.5 h-3.5" />
            </Link>
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
