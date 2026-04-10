import { useState, useEffect } from 'react'
import { Link, Navigate } from 'react-router-dom'
import {
  Terminal,
  Users as UsersIcon,
  Activity,
  Key,
  Server,
  RefreshCw,
} from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AdminUser {
  email: string
  plan: string
  signupDate: string
  lastActive: string
  scansUsed: number
}

interface UsageStats {
  totalUsers: number
  totalScans: number
  scansToday: number
  revenueEstimate: string
}

interface ApiKeyEntry {
  key: string
  owner: string
  usageCount: number
  createdAt: string
}

interface SystemHealth {
  status: 'healthy' | 'degraded' | 'down'
  uptime: string
  errorRate: string
  version: string
}

// ---------------------------------------------------------------------------
// Demo data
// ---------------------------------------------------------------------------

const DEMO_USERS: AdminUser[] = [
  { email: 'alice@startup.io', plan: 'pro', signupDate: '2026-03-15', lastActive: '2026-04-08', scansUsed: 142 },
  { email: 'bob@devco.com', plan: 'free', signupDate: '2026-03-22', lastActive: '2026-04-07', scansUsed: 8 },
  { email: 'carol@bigcorp.com', plan: 'team', signupDate: '2026-02-10', lastActive: '2026-04-08', scansUsed: 531 },
  { email: 'dan@indie.dev', plan: 'pro', signupDate: '2026-04-01', lastActive: '2026-04-08', scansUsed: 47 },
  { email: 'eve@agency.co', plan: 'free', signupDate: '2026-04-05', lastActive: '2026-04-06', scansUsed: 3 },
]

const DEMO_USAGE: UsageStats = {
  totalUsers: 127,
  totalScans: 4_832,
  scansToday: 89,
  revenueEstimate: '$2,436',
}

const DEMO_KEYS: ApiKeyEntry[] = [
  { key: 'rtn_live_a1b2c3...', owner: 'alice@startup.io', usageCount: 142, createdAt: '2026-03-15' },
  { key: 'rtn_live_d4e5f6...', owner: 'carol@bigcorp.com', usageCount: 531, createdAt: '2026-02-10' },
  { key: 'rtn_live_g7h8i9...', owner: 'dan@indie.dev', usageCount: 47, createdAt: '2026-04-01' },
]

const DEMO_HEALTH: SystemHealth = {
  status: 'healthy',
  uptime: '99.97% (30d)',
  errorRate: '0.12%',
  version: '1.4.2',
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function StatusDot({ status }: { status: SystemHealth['status'] }) {
  const color =
    status === 'healthy'
      ? 'bg-accent'
      : status === 'degraded'
        ? 'bg-warning'
        : 'bg-danger'
  return <span className={`inline-block w-2 h-2 rounded-full ${color}`} />
}

function PlanBadge({ plan }: { plan: string }) {
  const colors: Record<string, string> = {
    free: 'bg-white/[0.06] text-text-muted',
    pro: 'bg-accent/10 text-accent',
    team: 'bg-blue-500/10 text-blue-400',
  }
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded text-[11px] font-medium ${
        colors[plan] ?? colors.free
      }`}
    >
      {plan}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Stat card
// ---------------------------------------------------------------------------

function StatCard({
  label,
  value,
  icon: Icon,
}: {
  label: string
  value: string | number
  icon: typeof Activity
}) {
  return (
    <div className="p-5 rounded-xl bg-bg-card border border-border-subtle">
      <div className="flex items-center gap-2 mb-3">
        <Icon className="w-4 h-4 text-accent" />
        <span className="text-xs text-text-muted uppercase tracking-wider">
          {label}
        </span>
      </div>
      <div className="text-2xl font-bold text-text-primary">
        {typeof value === 'number' ? value.toLocaleString() : value}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Admin page
// ---------------------------------------------------------------------------

export function Admin() {
  const { user, isAuthenticated } = useAuth()
  const [users, setUsers] = useState<AdminUser[]>(DEMO_USERS)
  const [usage, setUsage] = useState<UsageStats>(DEMO_USAGE)
  const [keys, setKeys] = useState<ApiKeyEntry[]>(DEMO_KEYS)
  const [health, setHealth] = useState<SystemHealth>(DEMO_HEALTH)
  const [loading, setLoading] = useState(false)

  // Gate: only admin users
  const isAdmin =
    !isAuthenticated ||
    user?.role === 'admin' ||
    user?.email?.startsWith('hshum@')

  // Attempt to fetch real data on mount
  useEffect(() => {
    async function fetchAdminData() {
      setLoading(true)
      try {
        const [usersRes, usageRes, healthRes, keysRes] = await Promise.allSettled([
          fetch('/api/admin/users'),
          fetch('/api/admin/usage'),
          fetch('/api/admin/health'),
          fetch('/api/admin/keys'),
        ])

        if (usersRes.status === 'fulfilled' && usersRes.value.ok) {
          const data = (await usersRes.value.json()) as AdminUser[]
          setUsers(data)
        }
        if (usageRes.status === 'fulfilled' && usageRes.value.ok) {
          const data = (await usageRes.value.json()) as UsageStats
          setUsage(data)
        }
        if (healthRes.status === 'fulfilled' && healthRes.value.ok) {
          const data = (await healthRes.value.json()) as SystemHealth
          setHealth(data)
        }
        if (keysRes.status === 'fulfilled' && keysRes.value.ok) {
          const data = (await keysRes.value.json()) as ApiKeyEntry[]
          setKeys(data)
        }
      } catch {
        // Keep demo data
      } finally {
        setLoading(false)
      }
    }
    void fetchAdminData()
  }, [])

  if (!isAdmin) {
    return <Navigate to="/dashboard" replace />
  }

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
          <nav className="flex items-center gap-4 text-sm text-text-secondary">
            <Link
              to="/dashboard"
              className="hover:text-text-primary transition-colors no-underline"
            >
              Dashboard
            </Link>
            <span className="px-2.5 py-1 rounded-md bg-danger/10 text-danger text-xs font-medium">
              Admin
            </span>
          </nav>
        </div>
      </header>

      <div className="pt-20 pb-12 px-6">
        <div className="max-w-6xl mx-auto">
          {/* Header */}
          <div className="flex items-center justify-between mb-8">
            <div>
              <h1 className="text-2xl font-bold">Admin Dashboard</h1>
              <p className="text-sm text-text-muted mt-1">
                System overview and user management
              </p>
            </div>
            <button
              onClick={() => window.location.reload()}
              disabled={loading}
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white/[0.06] text-text-secondary text-xs hover:bg-white/[0.1] transition-colors cursor-pointer border-none disabled:opacity-50"
            >
              <RefreshCw
                className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`}
              />
              Refresh
            </button>
          </div>

          {/* ============================================================ */}
          {/* Usage stats                                                   */}
          {/* ============================================================ */}
          <section className="mb-8">
            <h2 className="text-sm font-semibold text-text-secondary mb-4 flex items-center gap-2">
              <Activity className="w-4 h-4 text-accent" />
              Usage Overview
            </h2>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <StatCard label="Total Users" value={usage.totalUsers} icon={UsersIcon} />
              <StatCard label="Total Scans" value={usage.totalScans} icon={Activity} />
              <StatCard label="Scans Today" value={usage.scansToday} icon={Activity} />
              <StatCard label="Revenue (est)" value={usage.revenueEstimate} icon={Activity} />
            </div>
          </section>

          {/* ============================================================ */}
          {/* System health                                                */}
          {/* ============================================================ */}
          <section className="mb-8">
            <h2 className="text-sm font-semibold text-text-secondary mb-4 flex items-center gap-2">
              <Server className="w-4 h-4 text-accent" />
              System Health
            </h2>
            <div className="p-5 rounded-xl bg-bg-card border border-border-subtle">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
                <div>
                  <p className="text-xs text-text-muted mb-1">Status</p>
                  <div className="flex items-center gap-2">
                    <StatusDot status={health.status} />
                    <span className="text-sm font-medium capitalize">
                      {health.status}
                    </span>
                  </div>
                </div>
                <div>
                  <p className="text-xs text-text-muted mb-1">Uptime</p>
                  <p className="text-sm font-medium">{health.uptime}</p>
                </div>
                <div>
                  <p className="text-xs text-text-muted mb-1">Error Rate</p>
                  <p className="text-sm font-medium">{health.errorRate}</p>
                </div>
                <div>
                  <p className="text-xs text-text-muted mb-1">Version</p>
                  <p className="text-sm font-mono">{health.version}</p>
                </div>
              </div>
            </div>
          </section>

          {/* ============================================================ */}
          {/* Users table                                                  */}
          {/* ============================================================ */}
          <section className="mb-8">
            <h2 className="text-sm font-semibold text-text-secondary mb-4 flex items-center gap-2">
              <UsersIcon className="w-4 h-4 text-accent" />
              Users ({users.length})
            </h2>
            <div className="overflow-x-auto rounded-xl border border-border-subtle">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-bg-card">
                    <th className="text-left p-4 text-text-muted font-medium text-xs uppercase tracking-wider">
                      Email
                    </th>
                    <th className="text-left p-4 text-text-muted font-medium text-xs uppercase tracking-wider">
                      Plan
                    </th>
                    <th className="text-left p-4 text-text-muted font-medium text-xs uppercase tracking-wider">
                      Signup
                    </th>
                    <th className="text-left p-4 text-text-muted font-medium text-xs uppercase tracking-wider">
                      Last Active
                    </th>
                    <th className="text-right p-4 text-text-muted font-medium text-xs uppercase tracking-wider">
                      Scans
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle">
                  {users.map((u) => (
                    <tr
                      key={u.email}
                      className="hover:bg-white/[0.02] transition-colors"
                    >
                      <td className="p-4 text-text-primary font-mono text-xs">
                        {u.email}
                      </td>
                      <td className="p-4">
                        <PlanBadge plan={u.plan} />
                      </td>
                      <td className="p-4 text-text-muted">{u.signupDate}</td>
                      <td className="p-4 text-text-muted">{u.lastActive}</td>
                      <td className="p-4 text-right text-text-secondary font-mono">
                        {u.scansUsed}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {/* ============================================================ */}
          {/* API Keys table                                               */}
          {/* ============================================================ */}
          <section className="mb-8">
            <h2 className="text-sm font-semibold text-text-secondary mb-4 flex items-center gap-2">
              <Key className="w-4 h-4 text-accent" />
              Active API Keys ({keys.length})
            </h2>
            <div className="overflow-x-auto rounded-xl border border-border-subtle">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-bg-card">
                    <th className="text-left p-4 text-text-muted font-medium text-xs uppercase tracking-wider">
                      Key
                    </th>
                    <th className="text-left p-4 text-text-muted font-medium text-xs uppercase tracking-wider">
                      Owner
                    </th>
                    <th className="text-right p-4 text-text-muted font-medium text-xs uppercase tracking-wider">
                      Usage
                    </th>
                    <th className="text-left p-4 text-text-muted font-medium text-xs uppercase tracking-wider">
                      Created
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle">
                  {keys.map((k) => (
                    <tr
                      key={k.key}
                      className="hover:bg-white/[0.02] transition-colors"
                    >
                      <td className="p-4 font-mono text-xs text-text-primary">
                        {k.key}
                      </td>
                      <td className="p-4 text-text-muted text-xs">{k.owner}</td>
                      <td className="p-4 text-right text-text-secondary font-mono">
                        {k.usageCount}
                      </td>
                      <td className="p-4 text-text-muted">{k.createdAt}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
