import { Outlet, Link, useLocation } from 'react-router-dom'
import { LayoutDashboard, GitBranch, ShieldCheck, Eye, BarChart3, GitCompareArrows, Settings, Terminal, MessageSquare } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'

const nav = [
  { to: '/dashboard', label: 'QA Results', icon: LayoutDashboard },
  { to: '/workflows', label: 'Workflows', icon: GitBranch },
  { to: '/judge', label: 'Judge', icon: ShieldCheck },
  { to: '/anatomy', label: 'Run Anatomy', icon: Eye },
  { to: '/benchmark', label: 'Benchmark', icon: BarChart3 },
  { to: '/compare', label: 'Compare', icon: GitCompareArrows },
]

export function Layout() {
  const { pathname } = useLocation()
  const { togglePanel, isOpen } = useChat()

  return (
    <div className="flex min-h-screen">
      <aside className="w-56 border-r border-border-subtle bg-bg-surface flex flex-col shrink-0">
        <Link to="/" className="px-4 py-5 flex items-center gap-2 text-accent font-semibold text-sm no-underline">
          <Terminal className="w-5 h-5" />
          retention.sh
        </Link>
        <nav className="flex-1 px-2 space-y-0.5">
          {nav.map(({ to, label, icon: Icon }) => {
            const active = pathname === to
            return (
              <Link
                key={to}
                to={to}
                className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] no-underline transition-colors ${
                  active
                    ? 'bg-white/[0.06] text-text-primary'
                    : 'text-text-secondary hover:text-text-primary hover:bg-white/[0.03]'
                }`}
              >
                <Icon className="w-4 h-4" />
                {label}
              </Link>
            )
          })}
        </nav>
        <div className="p-4 border-t border-border-subtle space-y-2">
          <button
            onClick={togglePanel}
            className={`flex items-center gap-2 text-xs transition-colors w-full cursor-pointer bg-transparent border-none p-0 ${
              isOpen
                ? 'text-accent'
                : 'text-text-muted hover:text-text-secondary'
            }`}
          >
            <MessageSquare className="w-3.5 h-3.5" />
            Ask Agent
          </button>
          <button className="flex items-center gap-2 text-text-muted text-xs hover:text-text-secondary transition-colors cursor-pointer bg-transparent border-none p-0 w-full">
            <Settings className="w-3.5 h-3.5" />
            Settings
          </button>
        </div>
      </aside>
      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  )
}
