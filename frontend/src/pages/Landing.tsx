import { Link } from 'react-router-dom'
import {
  Terminal,
  ArrowRight,
  ChevronRight,
  ShieldCheck,
  RotateCcw,
  Eye,
  Code2,
  Users,
  Sparkles,
  Check,
  X,
  Minus,
  Copy,
  CheckCheck,
  FolderTree,
  Gauge,
  ClipboardCheck,
  Zap,
} from 'lucide-react'
import { useState } from 'react'

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const INSTALL_CMD = 'curl -sL retention.sh/install.sh | bash'

const PROVIDERS = [
  'Claude Code',
  'Cursor',
  'Windsurf',
  'OpenAI Agents',
  'LangChain',
  'CrewAI',
]

const HOOKS = [
  'on-session-start',
  'on-prompt',
  'on-tool-use',
  'on-stop',
]

const REPLAY_BADGES = [
  'step-elimination',
  'context-compression',
  'checkpoint-pruning',
]

/* ------------------------------------------------------------------ */
/*  Shared components                                                  */
/* ------------------------------------------------------------------ */

function SectionLabel({ n }: { n: number }) {
  return (
    <p className="text-[11px] uppercase tracking-[0.2em] text-text-muted mb-3 font-mono">
      [{n}/8]
    </p>
  )
}

function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-block px-2.5 py-1 rounded-md bg-white/[0.04] border border-border-subtle text-[11px] text-text-muted font-mono">
      {children}
    </span>
  )
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(text)
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      }}
      className="flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-md bg-white/[0.06] hover:bg-white/[0.12] transition-colors text-text-secondary cursor-pointer"
      aria-label="Copy to clipboard"
    >
      {copied ? (
        <>
          <CheckCheck className="w-3 h-3 text-accent" /> Copied
        </>
      ) : (
        <>
          <Copy className="w-3 h-3" /> Copy
        </>
      )}
    </button>
  )
}

function ComparisonIcon({ value }: { value: 'yes' | 'no' | 'partial' }) {
  if (value === 'yes')
    return <Check className="w-4 h-4 text-accent mx-auto" />
  if (value === 'no')
    return <X className="w-4 h-4 text-danger mx-auto" />
  return <Minus className="w-4 h-4 text-warning mx-auto" />
}

/* ------------------------------------------------------------------ */
/*  Landing page                                                       */
/* ------------------------------------------------------------------ */

export function Landing() {
  return (
    <div className="min-h-screen bg-bg-primary">
      {/* ============================================================ */}
      {/* NAV                                                          */}
      {/* ============================================================ */}
      <header className="fixed top-0 w-full z-50 backdrop-blur-md bg-bg-primary/80 border-b border-border-subtle">
        <div className="max-w-6xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-2 text-accent font-semibold text-sm">
            <Terminal className="w-4.5 h-4.5" />
            retention.sh
          </div>
          <nav className="hidden sm:flex items-center gap-6 text-sm text-text-secondary">
            <a href="#how" className="hover:text-text-primary transition-colors no-underline">
              How it works
            </a>
            <a href="#proof" className="hover:text-text-primary transition-colors no-underline">
              Proof
            </a>
            <a href="#start" className="hover:text-text-primary transition-colors no-underline">
              Try it
            </a>
            <Link
              to="/dashboard"
              className="px-3 py-1.5 rounded-lg bg-accent text-black font-medium hover:bg-accent-muted transition-colors no-underline text-sm"
            >
              Dashboard
            </Link>
          </nav>
        </div>
      </header>

      {/* ============================================================ */}
      {/* [1/8] HERO                                                   */}
      {/* ============================================================ */}
      <section className="pt-32 pb-20 px-6">
        <div className="max-w-3xl mx-auto text-center">
          <SectionLabel n={1} />

          <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-white/[0.04] border border-border-subtle text-[11px] text-text-muted mb-8">
            Works with {PROVIDERS.slice(0, 3).join(', ')}, {PROVIDERS.slice(3).join(', ')}
          </div>

          <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold tracking-tight leading-[1.1] mb-6">
            See what your AI agent
            <br />
            <span className="text-danger">actually missed.</span>
          </h1>

          <p className="text-lg text-text-secondary max-w-xl mx-auto mb-10 leading-relaxed">
            Your agent says done. retention.sh shows the skipped tests,
            forgotten steps, missing context &mdash; then blocks it from
            happening again.
          </p>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
            <a
              href="#start"
              className="inline-flex items-center gap-2 px-6 py-3 rounded-lg bg-accent text-black font-semibold text-sm hover:bg-accent-muted transition-colors no-underline"
            >
              Try the tasting menu
              <ArrowRight className="w-4 h-4" />
            </a>
            <a
              href="https://github.com/HomenShum/retention"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 px-6 py-3 rounded-lg border border-border-muted text-text-secondary text-sm hover:text-text-primary hover:border-text-muted transition-colors no-underline"
            >
              GitHub
              <ChevronRight className="w-3.5 h-3.5" />
            </a>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* [2/8] PRODUCT IN ONE SCREEN                                  */}
      {/* ============================================================ */}
      <section className="py-20 px-6 border-t border-border-subtle">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-12">
            <SectionLabel n={2} />
            <h2 className="text-2xl sm:text-3xl font-bold">
              The product in one screen
            </h2>
          </div>

          <div className="grid md:grid-cols-2 gap-6">
            {/* Left: what agent reports */}
            <div className="p-6 rounded-xl bg-bg-card border border-danger/20">
              <p className="text-[11px] uppercase tracking-[0.15em] text-danger font-semibold mb-5">
                What your agent reports
              </p>
              <div className="space-y-3">
                <div className="flex items-center gap-3 p-3 rounded-lg bg-bg-surface border border-border-subtle">
                  <div className="w-2 h-2 rounded-full bg-accent shrink-0" />
                  <span className="text-sm text-text-secondary">
                    &ldquo;Done! All tasks complete.&rdquo;
                  </span>
                </div>
                {[
                  'Tests: skipped',
                  'Console check: skipped',
                  'QA surfaces: 1/5',
                ].map((item) => (
                  <div
                    key={item}
                    className="flex items-center gap-3 p-3 rounded-lg bg-danger/[0.06] border border-danger/10"
                  >
                    <X className="w-3.5 h-3.5 text-danger shrink-0" />
                    <span className="text-sm text-danger/80 font-mono">
                      MISSING &mdash; {item}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Right: what retention.sh catches */}
            <div className="p-6 rounded-xl bg-bg-card border border-accent/20">
              <p className="text-[11px] uppercase tracking-[0.15em] text-accent font-semibold mb-5">
                What retention.sh catches
              </p>
              <div className="space-y-3">
                <div className="flex items-center gap-3 p-3 rounded-lg bg-danger/[0.08] border border-danger/15">
                  <ShieldCheck className="w-4 h-4 text-danger shrink-0" />
                  <span className="text-sm font-semibold text-danger">
                    Verdict: BLOCKED
                  </span>
                </div>
                {[
                  'Run test suite (skipped)',
                  'Check console for errors (skipped)',
                  'Verify all 5 QA surfaces (1/5)',
                ].map((step) => (
                  <div
                    key={step}
                    className="flex items-center gap-3 p-3 rounded-lg bg-bg-surface border border-border-subtle"
                  >
                    <ChevronRight className="w-3.5 h-3.5 text-accent shrink-0" />
                    <span className="text-sm text-text-secondary">{step}</span>
                  </div>
                ))}
                <div className="p-3 rounded-lg bg-accent/[0.06] border border-accent/15">
                  <p className="text-xs text-accent leading-relaxed">
                    Agent re-runs &rarr; completes all steps &rarr;{' '}
                    <span className="font-semibold">63% cheaper on replay</span>
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* [3/8] HOW IT WORKS                                           */}
      {/* ============================================================ */}
      <section id="how" className="py-20 px-6 border-t border-border-subtle">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-14">
            <SectionLabel n={3} />
            <h2 className="text-2xl sm:text-3xl font-bold">How it works</h2>
            <p className="text-text-muted text-sm mt-2">
              Three stages. No config required.
            </p>
          </div>

          <div className="grid md:grid-cols-3 gap-6">
            {/* Step 1 */}
            <div className="p-6 rounded-xl bg-bg-card border border-border-subtle flex flex-col">
              <div className="w-8 h-8 rounded-lg bg-accent/10 text-accent font-bold text-xs flex items-center justify-center mb-4">
                1
              </div>
              <h3 className="font-semibold text-base mb-1">CAPTURE</h3>
              <p className="text-text-secondary text-sm leading-relaxed flex-1 mb-5">
                Run your workflow once. Every tool call, prompt, and result is
                recorded as a canonical event.
              </p>
              <div className="flex flex-wrap gap-1.5">
                {PROVIDERS.map((p) => (
                  <Badge key={p}>{p}</Badge>
                ))}
              </div>
            </div>

            {/* Step 2 */}
            <div className="p-6 rounded-xl bg-bg-card border border-border-subtle flex flex-col">
              <div className="w-8 h-8 rounded-lg bg-accent/10 text-accent font-bold text-xs flex items-center justify-center mb-4">
                2
              </div>
              <h3 className="font-semibold text-base mb-1">JUDGE</h3>
              <p className="text-text-secondary text-sm leading-relaxed flex-1 mb-5">
                4 hooks fire on every session. <code className="text-accent text-xs">on-prompt</code> injects
                required steps. <code className="text-accent text-xs">on-tool-use</code> tracks evidence.{' '}
                <code className="text-accent text-xs">on-stop</code> blocks if incomplete.
              </p>
              <div className="flex flex-wrap gap-1.5">
                {HOOKS.map((h) => (
                  <Badge key={h}>{h}</Badge>
                ))}
              </div>
            </div>

            {/* Step 3 */}
            <div className="p-6 rounded-xl bg-bg-card border border-border-subtle flex flex-col">
              <div className="w-8 h-8 rounded-lg bg-accent/10 text-accent font-bold text-xs flex items-center justify-center mb-4">
                3
              </div>
              <h3 className="font-semibold text-base mb-1">REPLAY</h3>
              <p className="text-text-secondary text-sm leading-relaxed flex-1 mb-5">
                Same workflow, 60-70% fewer tokens. Strict judge verifies
                quality held.
              </p>
              <div className="flex flex-wrap gap-1.5">
                {REPLAY_BADGES.map((b) => (
                  <Badge key={b}>{b}</Badge>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* [4/8] COMPETITIVE MATRIX                                     */}
      {/* ============================================================ */}
      <section className="py-20 px-6 border-t border-border-subtle">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-12">
            <SectionLabel n={4} />
            <h2 className="text-2xl sm:text-3xl font-bold">
              Why not just use memory?
            </h2>
            <p className="text-text-muted text-sm mt-2">
              Memory remembers. retention.sh judges, blocks, and replays.
            </p>
          </div>

          <div className="overflow-x-auto rounded-xl border border-border-subtle">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-bg-card">
                  <th className="text-left p-4 text-text-muted font-medium text-xs uppercase tracking-wider">
                    Feature
                  </th>
                  <th className="p-4 text-center text-text-muted font-medium text-xs uppercase tracking-wider">
                    Claude Memory
                  </th>
                  <th className="p-4 text-center text-text-muted font-medium text-xs uppercase tracking-wider">
                    Supermemory
                  </th>
                  <th className="p-4 text-center text-text-muted font-medium text-xs uppercase tracking-wider">
                    OpenAI Codex
                  </th>
                  <th className="p-4 text-center text-accent font-semibold text-xs uppercase tracking-wider border-l border-accent/20">
                    retention.sh
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {(
                  [
                    ['Cross-session memory', 'partial', 'yes', 'no', 'yes'],
                    ['Workflow detection', 'no', 'no', 'no', 'yes'],
                    ['Step tracking', 'no', 'no', 'no', 'yes'],
                    ['Block incomplete work', 'no', 'no', 'no', 'yes'],
                    ['Learn from corrections', 'no', 'no', 'no', 'yes'],
                    ['Cheaper replay', 'no', 'no', 'no', 'yes'],
                    ['Runtime agnostic', 'no', 'partial', 'no', 'yes'],
                    ['Self-improving judge', 'no', 'no', 'no', 'yes'],
                  ] as const
                ).map(([feature, claude, supermem, codex, retention]) => (
                  <tr
                    key={feature}
                    className="hover:bg-white/[0.02] transition-colors"
                  >
                    <td className="p-4 text-text-secondary">{feature}</td>
                    <td className="p-4 text-center">
                      <ComparisonIcon value={claude} />
                    </td>
                    <td className="p-4 text-center">
                      <ComparisonIcon value={supermem} />
                    </td>
                    <td className="p-4 text-center">
                      <ComparisonIcon value={codex} />
                    </td>
                    <td className="p-4 text-center border-l border-accent/10">
                      <ComparisonIcon value={retention} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* [5/8] REAL BENCHMARK                                         */}
      {/* ============================================================ */}
      <section id="proof" className="py-20 px-6 border-t border-border-subtle">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-12">
            <SectionLabel n={5} />
            <h2 className="text-2xl sm:text-3xl font-bold">
              Measured, not promised
            </h2>
            <p className="text-text-muted text-sm mt-2">
              All numbers from real API calls. Independent LLM judge. N=15 CSP
              runs.
            </p>
          </div>

          {/* Stat cards */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-10">
            {[
              {
                value: '14/15',
                pct: '93%',
                label: 'Replays passed',
                icon: CheckCheck,
              },
              {
                value: '60-70%',
                pct: 'cost',
                label: 'Cheaper on replay',
                icon: Zap,
              },
              {
                value: '8/8',
                pct: '100%',
                label: 'Required steps completed',
                icon: ClipboardCheck,
              },
              {
                value: '45%',
                pct: 'tokens',
                label: 'Token reduction (distilled)',
                icon: Gauge,
              },
            ].map(({ value, pct, label, icon: Icon }) => (
              <div
                key={label}
                className="p-5 rounded-xl bg-bg-card border border-border-subtle text-center"
              >
                <Icon className="w-5 h-5 text-accent mx-auto mb-3" />
                <div className="text-2xl font-bold text-text-primary">
                  {value}
                </div>
                <div className="text-xs text-accent font-mono mt-0.5">
                  {pct}
                </div>
                <div className="text-text-muted text-xs mt-2">{label}</div>
              </div>
            ))}
          </div>

          {/* Verify yourself */}
          <div className="p-6 rounded-xl bg-bg-card border border-border-subtle">
            <h3 className="text-sm font-semibold mb-4 text-text-secondary">
              Verify it yourself
            </h3>
            <div className="space-y-2 font-mono text-xs">
              {[
                'retention benchmark run --suite csp --n 15',
                'retention benchmark report --format table',
                'retention benchmark compare --baseline original',
              ].map((cmd) => (
                <div
                  key={cmd}
                  className="flex items-center gap-3 p-3 rounded-lg bg-bg-primary border border-border-subtle"
                >
                  <span className="text-accent">$</span>
                  <code className="text-text-secondary flex-1">{cmd}</code>
                  <CopyButton text={cmd} />
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* [6/8] WHO IT'S FOR                                           */}
      {/* ============================================================ */}
      <section className="py-20 px-6 border-t border-border-subtle">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-12">
            <SectionLabel n={6} />
            <h2 className="text-2xl sm:text-3xl font-bold">
              Built for people who use AI agents daily
            </h2>
          </div>

          <div className="grid md:grid-cols-3 gap-6">
            {[
              {
                icon: Code2,
                who: 'Engineers',
                pain: 'Agent keeps skipping tests',
                solution:
                  'Catch skipped steps and replay repeated workflows cheaper.',
              },
              {
                icon: Users,
                who: 'Team Leads',
                pain: 'No visibility into what agents did',
                solution:
                  'See what happened, what was missed, where savings came from.',
              },
              {
                icon: Sparkles,
                who: 'Founders',
                pain: 'Repeating expensive AI work every time',
                solution:
                  'Turn repeated work into reusable operating leverage.',
              },
            ].map(({ icon: Icon, who, pain, solution }) => (
              <div
                key={who}
                className="p-6 rounded-xl bg-bg-card border border-border-subtle flex flex-col"
              >
                <Icon className="w-6 h-6 text-accent mb-4" />
                <h3 className="font-semibold text-base mb-1">{who}</h3>
                <p className="text-xs text-danger mb-3 font-mono">
                  &ldquo;{pain}&rdquo;
                </p>
                <p className="text-text-secondary text-sm leading-relaxed flex-1">
                  {solution}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* [7/8] UNDER THE HOOD                                         */}
      {/* ============================================================ */}
      <section className="py-20 px-6 border-t border-border-subtle">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-12">
            <SectionLabel n={7} />
            <h2 className="text-2xl sm:text-3xl font-bold">Under the hood</h2>
          </div>

          <div className="grid md:grid-cols-2 gap-6">
            {/* Architecture tree */}
            <div className="p-6 rounded-xl bg-bg-card border border-border-subtle">
              <div className="flex items-center gap-2 mb-5">
                <FolderTree className="w-4 h-4 text-accent" />
                <span className="text-sm font-semibold">Architecture</span>
              </div>
              <div className="font-mono text-xs text-text-secondary space-y-1.5 pl-1">
                <p className="text-accent font-semibold">retention.sh/</p>
                <p className="pl-4">
                  <span className="text-text-muted">backend/</span>{' '}
                  <span className="text-text-muted">&mdash;</span> FastAPI,
                  agents, judge
                </p>
                <p className="pl-4">
                  <span className="text-text-muted">packages/</span>
                </p>
                <p className="pl-8">retention-cli</p>
                <p className="pl-8">retention-mcp</p>
                <p className="pl-8">retention-sdk</p>
                <p className="pl-8">tcwp</p>
                <p className="pl-4">
                  <span className="text-text-muted">frontend/</span>{' '}
                  <span className="text-text-muted">&mdash;</span> React, Vite,
                  Tailwind
                </p>
              </div>
            </div>

            {/* SDK snippet */}
            <div className="p-6 rounded-xl bg-bg-card border border-border-subtle flex flex-col">
              <div className="flex items-center gap-2 mb-5">
                <Terminal className="w-4 h-4 text-accent" />
                <span className="text-sm font-semibold">Python SDK</span>
              </div>
              <div className="rounded-lg bg-bg-primary border border-border-subtle p-4 font-mono text-xs flex-1">
                <p className="text-text-muted"># pip install retention</p>
                <p className="mt-1">
                  <span className="text-accent">from</span>{' '}
                  <span className="text-text-primary">retention</span>{' '}
                  <span className="text-accent">import</span>{' '}
                  <span className="text-text-primary">track</span>
                </p>
                <p className="mt-2">
                  <span className="text-text-primary">track</span>
                  <span className="text-text-muted">()</span>
                </p>
                <p className="mt-2 text-text-muted">
                  # Auto-detects OpenAI, Anthropic,
                </p>
                <p className="text-text-muted"># LangChain, CrewAI</p>
              </div>

              {/* Stats row */}
              <div className="grid grid-cols-4 gap-3 mt-5">
                {[
                  { value: '710', label: 'files' },
                  { value: '7', label: 'provider wrappers' },
                  { value: '87', label: 'tests' },
                  { value: 'MIT', label: 'licensed' },
                ].map(({ value, label }) => (
                  <div key={label} className="text-center">
                    <div className="text-sm font-bold text-text-primary">
                      {value}
                    </div>
                    <div className="text-[10px] text-text-muted">{label}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* [8/8] TRY IT                                                 */}
      {/* ============================================================ */}
      <section id="start" className="py-20 px-6 border-t border-border-subtle">
        <div className="max-w-3xl mx-auto text-center">
          <SectionLabel n={8} />
          <h2 className="text-2xl sm:text-3xl font-bold mb-2">
            Stop correcting. Start shipping.
          </h2>
          <p className="text-text-muted text-sm mb-8">
            60 seconds to install. The agent gets better every run.
          </p>

          {/* Install block */}
          <div className="inline-flex items-center gap-3 px-5 py-3.5 rounded-xl bg-bg-card border border-border-muted font-mono text-sm">
            <span className="text-accent">$</span>
            <code className="text-text-primary">{INSTALL_CMD}</code>
            <CopyButton text={INSTALL_CMD} />
          </div>

          {/* Provider badges */}
          <div className="flex flex-wrap items-center justify-center gap-2 mt-6">
            {PROVIDERS.map((p) => (
              <Badge key={p}>{p}</Badge>
            ))}
          </div>

          {/* Secondary links */}
          <div className="flex items-center justify-center gap-6 mt-8 text-sm">
            <a
              href="https://github.com/HomenShum/retention"
              target="_blank"
              rel="noopener noreferrer"
              className="text-text-secondary hover:text-text-primary transition-colors no-underline"
            >
              GitHub
            </a>
            <Link
              to="/dashboard"
              className="text-text-secondary hover:text-text-primary transition-colors no-underline"
            >
              Dashboard
            </Link>
            <a
              href="https://github.com/HomenShum/retention#readme"
              target="_blank"
              rel="noopener noreferrer"
              className="text-text-secondary hover:text-text-primary transition-colors no-underline"
            >
              Docs
            </a>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* FOOTER                                                       */}
      {/* ============================================================ */}
      <footer className="py-8 px-6 border-t border-border-subtle">
        <div className="max-w-6xl mx-auto flex items-center justify-between text-xs text-text-muted">
          <span className="font-mono">retention.sh</span>
          <div className="flex gap-5">
            <a
              href="https://github.com/HomenShum/retention"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-text-secondary transition-colors no-underline"
            >
              GitHub
            </a>
            <Link
              to="/dashboard"
              className="hover:text-text-secondary transition-colors no-underline"
            >
              Dashboard
            </Link>
          </div>
        </div>
      </footer>
    </div>
  )
}
