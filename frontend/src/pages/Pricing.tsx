import { Link } from 'react-router-dom'
import { Terminal, Check, ArrowRight, Zap, Users, Shield } from 'lucide-react'

// ---------------------------------------------------------------------------
// Plan data
// ---------------------------------------------------------------------------

interface PlanFeature {
  text: string
  included: boolean
}

interface Plan {
  name: string
  price: string
  period: string
  description: string
  icon: typeof Zap
  features: PlanFeature[]
  cta: string
  ctaLink: string
  highlighted: boolean
  badge?: string
}

const PLANS: Plan[] = [
  {
    name: 'Free',
    price: '$0',
    period: 'forever',
    description: 'Get started with agent QA. No credit card required.',
    icon: Terminal,
    features: [
      { text: '10 scans / month', included: true },
      { text: 'CLI + MCP access', included: true },
      { text: 'Community support', included: true },
      { text: '1 user', included: true },
      { text: 'Workflow replay', included: false },
      { text: 'Run history', included: false },
      { text: 'Team dashboard', included: false },
      { text: 'SSO', included: false },
    ],
    cta: 'Get Started',
    ctaLink: '/auth',
    highlighted: false,
  },
  {
    name: 'Pro',
    price: '$29',
    period: '/ month',
    description: 'Unlimited scans, full replay, 30-day history.',
    icon: Zap,
    features: [
      { text: 'Unlimited scans', included: true },
      { text: 'CLI + MCP access', included: true },
      { text: 'Priority support', included: true },
      { text: '1 user', included: true },
      { text: 'Workflow replay', included: true },
      { text: 'Run history (30 days)', included: true },
      { text: 'Team dashboard', included: false },
      { text: 'SSO', included: false },
    ],
    cta: 'Start Free Trial',
    ctaLink: '/auth?plan=pro',
    highlighted: true,
    badge: 'Popular',
  },
  {
    name: 'Team',
    price: '$99',
    period: '/ month',
    description: 'Everything in Pro, plus team features and SSO.',
    icon: Users,
    features: [
      { text: 'Unlimited scans', included: true },
      { text: 'CLI + MCP access', included: true },
      { text: 'Priority support', included: true },
      { text: '5 users included', included: true },
      { text: 'Workflow replay', included: true },
      { text: 'Run history (30 days)', included: true },
      { text: 'Shared workflow memory', included: true },
      { text: 'Team dashboard + SSO', included: true },
    ],
    cta: 'Contact Us',
    ctaLink: 'mailto:info@retention.sh',
    highlighted: false,
  },
]

// ---------------------------------------------------------------------------
// Comparison table data
// ---------------------------------------------------------------------------

const COMPARISON_ROWS = [
  { feature: 'Scans per month', free: '10', pro: 'Unlimited', team: 'Unlimited' },
  { feature: 'CLI access', free: 'Yes', pro: 'Yes', team: 'Yes' },
  { feature: 'MCP server', free: 'Yes', pro: 'Yes', team: 'Yes' },
  { feature: 'Workflow replay', free: '--', pro: 'Yes', team: 'Yes' },
  { feature: 'Run history', free: '--', pro: '30 days', team: '30 days' },
  { feature: 'Users', free: '1', pro: '1', team: '5 included' },
  { feature: 'Team dashboard', free: '--', pro: '--', team: 'Yes' },
  { feature: 'Shared memory', free: '--', pro: '--', team: 'Yes' },
  { feature: 'SSO / SAML', free: '--', pro: '--', team: 'Yes' },
  { feature: 'Support', free: 'Community', pro: 'Priority', team: 'Priority + Slack' },
]

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function Pricing() {
  return (
    <div className="min-h-screen bg-bg-primary">
      {/* Nav */}
      <header className="fixed top-0 w-full z-50 backdrop-blur-md bg-bg-primary/80 border-b border-border-subtle">
        <div className="max-w-6xl mx-auto px-6 h-14 flex items-center justify-between">
          <Link
            to="/"
            className="flex items-center gap-2 text-accent font-semibold text-sm no-underline"
          >
            <Terminal className="w-4.5 h-4.5" />
            retention.sh
          </Link>
          <nav className="hidden sm:flex items-center gap-6 text-sm text-text-secondary">
            <Link
              to="/"
              className="hover:text-text-primary transition-colors no-underline"
            >
              Home
            </Link>
            <Link
              to="/dashboard"
              className="hover:text-text-primary transition-colors no-underline"
            >
              Dashboard
            </Link>
            <Link
              to="/auth"
              className="px-3 py-1.5 rounded-lg bg-accent text-black font-medium hover:bg-accent-muted transition-colors no-underline text-sm"
            >
              Sign In
            </Link>
          </nav>
        </div>
      </header>

      {/* Hero */}
      <section className="pt-32 pb-16 px-6 text-center">
        <p className="text-[11px] uppercase tracking-[0.2em] text-text-muted mb-3 font-mono">
          Pricing
        </p>
        <h1 className="text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight mb-4">
          Simple pricing.
          <br />
          <span className="text-accent">Start free.</span>
        </h1>
        <p className="text-text-secondary text-base max-w-lg mx-auto">
          No hidden fees. Upgrade when you need unlimited scans, replay, and team features.
        </p>
      </section>

      {/* Plan cards */}
      <section className="px-6 pb-20">
        <div className="max-w-5xl mx-auto grid md:grid-cols-3 gap-6">
          {PLANS.map((plan) => {
            const Icon = plan.icon
            const isExternal = plan.ctaLink.startsWith('mailto:')

            return (
              <div
                key={plan.name}
                className={`relative p-6 rounded-2xl flex flex-col ${
                  plan.highlighted
                    ? 'bg-bg-card border-2 border-accent/40 shadow-[0_0_40px_-12px_rgba(34,197,94,0.15)]'
                    : 'bg-bg-card border border-border-subtle'
                }`}
              >
                {plan.badge && (
                  <span className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 rounded-full bg-accent text-black text-[11px] font-semibold">
                    {plan.badge}
                  </span>
                )}

                <div className="flex items-center gap-2 mb-4">
                  <div
                    className={`w-8 h-8 rounded-lg flex items-center justify-center ${
                      plan.highlighted ? 'bg-accent/15' : 'bg-white/[0.04]'
                    }`}
                  >
                    <Icon
                      className={`w-4 h-4 ${
                        plan.highlighted ? 'text-accent' : 'text-text-muted'
                      }`}
                    />
                  </div>
                  <h3 className="font-semibold text-base">{plan.name}</h3>
                </div>

                <div className="mb-4">
                  <span className="text-3xl font-bold">{plan.price}</span>
                  <span className="text-sm text-text-muted ml-1">{plan.period}</span>
                </div>

                <p className="text-sm text-text-secondary mb-6 leading-relaxed">
                  {plan.description}
                </p>

                <ul className="space-y-2.5 mb-8 flex-1">
                  {plan.features.map((f) => (
                    <li
                      key={f.text}
                      className={`flex items-center gap-2 text-sm ${
                        f.included ? 'text-text-secondary' : 'text-text-muted/50'
                      }`}
                    >
                      {f.included ? (
                        <Check className="w-3.5 h-3.5 text-accent shrink-0" />
                      ) : (
                        <span className="w-3.5 h-3.5 flex items-center justify-center text-text-muted/30 shrink-0">
                          --
                        </span>
                      )}
                      {f.text}
                    </li>
                  ))}
                </ul>

                {isExternal ? (
                  <a
                    href={plan.ctaLink}
                    className={`flex items-center justify-center gap-2 px-4 py-3 rounded-lg text-sm font-semibold transition-colors no-underline ${
                      plan.highlighted
                        ? 'bg-accent text-black hover:bg-accent-muted'
                        : 'bg-white/[0.06] text-text-primary hover:bg-white/[0.1]'
                    }`}
                  >
                    {plan.cta}
                    <ArrowRight className="w-4 h-4" />
                  </a>
                ) : (
                  <Link
                    to={plan.ctaLink}
                    className={`flex items-center justify-center gap-2 px-4 py-3 rounded-lg text-sm font-semibold transition-colors no-underline ${
                      plan.highlighted
                        ? 'bg-accent text-black hover:bg-accent-muted'
                        : 'bg-white/[0.06] text-text-primary hover:bg-white/[0.1]'
                    }`}
                  >
                    {plan.cta}
                    <ArrowRight className="w-4 h-4" />
                  </Link>
                )}
              </div>
            )
          })}
        </div>
      </section>

      {/* Feature comparison table */}
      <section className="px-6 pb-20">
        <div className="max-w-4xl mx-auto">
          <h2 className="text-xl font-bold text-center mb-8">
            Feature comparison
          </h2>

          <div className="overflow-x-auto rounded-xl border border-border-subtle">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-bg-card">
                  <th className="text-left p-4 text-text-muted font-medium text-xs uppercase tracking-wider">
                    Feature
                  </th>
                  <th className="p-4 text-center text-text-muted font-medium text-xs uppercase tracking-wider">
                    Free
                  </th>
                  <th className="p-4 text-center text-accent font-semibold text-xs uppercase tracking-wider border-x border-accent/10">
                    Pro
                  </th>
                  <th className="p-4 text-center text-text-muted font-medium text-xs uppercase tracking-wider">
                    Team
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {COMPARISON_ROWS.map((row) => (
                  <tr
                    key={row.feature}
                    className="hover:bg-white/[0.02] transition-colors"
                  >
                    <td className="p-4 text-text-secondary">{row.feature}</td>
                    <td className="p-4 text-center text-text-muted">{row.free}</td>
                    <td className="p-4 text-center text-text-primary border-x border-accent/5 font-medium">
                      {row.pro}
                    </td>
                    <td className="p-4 text-center text-text-muted">{row.team}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* Trust bar */}
      <section className="px-6 pb-20">
        <div className="max-w-3xl mx-auto text-center">
          <div className="flex items-center justify-center gap-6 flex-wrap text-xs text-text-muted">
            <span className="flex items-center gap-1.5">
              <Shield className="w-3.5 h-3.5 text-accent" /> SOC 2 planned
            </span>
            <span className="flex items-center gap-1.5">
              <Check className="w-3.5 h-3.5 text-accent" /> Cancel anytime
            </span>
            <span className="flex items-center gap-1.5">
              <Check className="w-3.5 h-3.5 text-accent" /> 14-day free trial on Pro
            </span>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-8 px-6 border-t border-border-subtle">
        <div className="max-w-6xl mx-auto flex items-center justify-between text-xs text-text-muted">
          <span className="font-mono">retention.sh</span>
          <div className="flex gap-5">
            <Link
              to="/"
              className="hover:text-text-secondary transition-colors no-underline"
            >
              Home
            </Link>
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
