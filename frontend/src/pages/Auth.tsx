import { useState } from 'react'
import { useNavigate, useSearchParams, Link } from 'react-router-dom'
import { Terminal, Copy, CheckCheck, GitBranch, Mail, Lock, ArrowRight } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'

type Tab = 'signin' | 'signup'

export function Auth() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { signup, login } = useAuth()

  const [tab, setTab] = useState<Tab>(
    searchParams.get('tab') === 'signin' ? 'signin' : 'signup',
  )
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [newApiKey, setNewApiKey] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const plan = searchParams.get('plan') ?? 'free'

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)

    try {
      if (tab === 'signup') {
        const result = await signup(email, password)
        setNewApiKey(result.api_key)
      } else {
        await login(email, password)
        navigate('/dashboard')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong')
    } finally {
      setLoading(false)
    }
  }

  function handleCopyKey() {
    if (!newApiKey) return
    navigator.clipboard.writeText(newApiKey)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  // After signup: show API key screen
  if (newApiKey) {
    return (
      <div className="min-h-screen bg-bg-primary flex items-center justify-center px-4">
        <div className="w-full max-w-md">
          <div className="p-8 rounded-2xl bg-bg-card border border-border-subtle">
            <div className="text-center mb-6">
              <div className="w-12 h-12 rounded-xl bg-accent/10 flex items-center justify-center mx-auto mb-4">
                <CheckCheck className="w-6 h-6 text-accent" />
              </div>
              <h1 className="text-xl font-bold">Account created</h1>
              <p className="text-sm text-text-muted mt-1">
                Save your API key -- you will not see it again.
              </p>
            </div>

            <div className="p-4 rounded-xl bg-bg-primary border border-accent/20 mb-6">
              <p className="text-[11px] uppercase tracking-[0.15em] text-accent font-medium mb-2">
                Your API Key
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 text-sm font-mono text-text-primary break-all select-all">
                  {newApiKey}
                </code>
                <button
                  onClick={handleCopyKey}
                  className="shrink-0 flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-md bg-white/[0.06] hover:bg-white/[0.12] transition-colors text-text-secondary cursor-pointer border-none"
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
              </div>
            </div>

            <button
              onClick={() => navigate('/dashboard')}
              className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-lg bg-accent text-black font-semibold text-sm hover:bg-accent-muted transition-colors cursor-pointer border-none"
            >
              Go to Dashboard
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-bg-primary flex items-center justify-center px-4">
      <div className="w-full max-w-md">
        {/* Logo */}
        <Link
          to="/"
          className="flex items-center justify-center gap-2 text-accent font-semibold text-sm mb-8 no-underline"
        >
          <Terminal className="w-5 h-5" />
          retention.sh
        </Link>

        {/* Card */}
        <div className="p-8 rounded-2xl bg-bg-card border border-border-subtle">
          {/* Plan badge */}
          {plan !== 'free' && tab === 'signup' && (
            <div className="text-center mb-4">
              <span className="inline-block px-3 py-1 rounded-full bg-accent/10 text-accent text-xs font-medium">
                {plan === 'pro' ? 'Pro Plan -- 14-day free trial' : `${plan} Plan`}
              </span>
            </div>
          )}

          {/* Tab switcher */}
          <div className="flex rounded-lg bg-bg-primary p-1 mb-6">
            <button
              onClick={() => { setTab('signup'); setError(null) }}
              className={`flex-1 py-2 rounded-md text-sm font-medium transition-colors cursor-pointer border-none ${
                tab === 'signup'
                  ? 'bg-white/[0.08] text-text-primary'
                  : 'bg-transparent text-text-muted hover:text-text-secondary'
              }`}
            >
              Sign Up
            </button>
            <button
              onClick={() => { setTab('signin'); setError(null) }}
              className={`flex-1 py-2 rounded-md text-sm font-medium transition-colors cursor-pointer border-none ${
                tab === 'signin'
                  ? 'bg-white/[0.08] text-text-primary'
                  : 'bg-transparent text-text-muted hover:text-text-secondary'
              }`}
            >
              Sign In
            </button>
          </div>

          {/* OAuth placeholder */}
          <button
            onClick={() => setError('GitHub OAuth coming soon. Use email/password for now.')}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg border border-border-muted text-text-secondary text-sm hover:text-text-primary hover:border-text-muted transition-colors cursor-pointer bg-transparent mb-4"
          >
            <GitBranch className="w-4 h-4" />
            Continue with GitHub
          </button>

          {/* Divider */}
          <div className="flex items-center gap-3 mb-4">
            <div className="flex-1 h-px bg-border-subtle" />
            <span className="text-xs text-text-muted">or</span>
            <div className="flex-1 h-px bg-border-subtle" />
          </div>

          {/* Form */}
          <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4">
            <div>
              <label
                htmlFor="auth-email"
                className="block text-xs text-text-muted mb-1.5"
              >
                Email
              </label>
              <div className="relative">
                <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted pointer-events-none" />
                <input
                  id="auth-email"
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@company.com"
                  className="w-full pl-10 pr-4 py-2.5 rounded-lg bg-bg-primary border border-border-subtle text-sm text-text-primary placeholder:text-text-muted/60 outline-none focus:border-accent/40 transition-colors"
                />
              </div>
            </div>

            <div>
              <label
                htmlFor="auth-password"
                className="block text-xs text-text-muted mb-1.5"
              >
                Password
              </label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted pointer-events-none" />
                <input
                  id="auth-password"
                  type="password"
                  required
                  minLength={8}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Min 8 characters"
                  className="w-full pl-10 pr-4 py-2.5 rounded-lg bg-bg-primary border border-border-subtle text-sm text-text-primary placeholder:text-text-muted/60 outline-none focus:border-accent/40 transition-colors"
                />
              </div>
            </div>

            {error && (
              <p className="text-xs text-danger bg-danger/[0.08] border border-danger/15 rounded-lg px-3 py-2">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-lg bg-accent text-black font-semibold text-sm hover:bg-accent-muted transition-colors cursor-pointer border-none disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading
                ? 'Please wait...'
                : tab === 'signup'
                  ? 'Create Account'
                  : 'Sign In'}
              {!loading && <ArrowRight className="w-4 h-4" />}
            </button>
          </form>

          {/* Toggle hint */}
          <p className="text-xs text-text-muted text-center mt-5">
            {tab === 'signup' ? (
              <>
                Already have an account?{' '}
                <button
                  onClick={() => { setTab('signin'); setError(null) }}
                  className="text-accent hover:underline cursor-pointer bg-transparent border-none p-0 text-xs"
                >
                  Sign in
                </button>
              </>
            ) : (
              <>
                No account yet?{' '}
                <button
                  onClick={() => { setTab('signup'); setError(null) }}
                  className="text-accent hover:underline cursor-pointer bg-transparent border-none p-0 text-xs"
                >
                  Sign up
                </button>
              </>
            )}
          </p>
        </div>

        {/* Footer */}
        <p className="text-center text-[11px] text-text-muted mt-6">
          By signing up you agree to our Terms of Service.
          <br />
          Guest mode still works without an account.
        </p>
      </div>
    </div>
  )
}
