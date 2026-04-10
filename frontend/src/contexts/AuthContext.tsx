import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
} from 'react'
import type { ReactNode } from 'react'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AuthUser {
  email: string
  api_key: string
  plan: 'free' | 'pro' | 'team'
  role?: 'admin' | 'user'
}

interface AuthContextValue {
  user: AuthUser | null
  isAuthenticated: boolean
  login: (email: string, password: string) => Promise<void>
  signup: (email: string, password: string) => Promise<{ api_key: string }>
  logout: () => void
}

// ---------------------------------------------------------------------------
// Storage helpers
// ---------------------------------------------------------------------------

const TOKEN_KEY = 'retention_token'
const USER_KEY = 'retention_user'

function persistAuth(token: string, user: AuthUser) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

function loadUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    if (!raw) return null
    return JSON.parse(raw) as AuthUser
  } catch {
    return null
  }
}

// ---------------------------------------------------------------------------
// Admin email check
// ---------------------------------------------------------------------------

function isAdminEmail(email: string): boolean {
  return email.startsWith('hshum@') || email === 'admin@retention.sh'
}

// ---------------------------------------------------------------------------
// Context + Provider
// ---------------------------------------------------------------------------

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)

  // Hydrate from localStorage on mount
  useEffect(() => {
    const stored = loadUser()
    if (stored) setUser(stored)
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })

      if (res.ok) {
        const data = (await res.json()) as { token: string; api_key: string; plan?: string }
        const authUser: AuthUser = {
          email,
          api_key: data.api_key,
          plan: (data.plan as AuthUser['plan']) ?? 'free',
          role: isAdminEmail(email) ? 'admin' : 'user',
        }
        persistAuth(data.token, authUser)
        setUser(authUser)
        return
      }
    } catch {
      // Backend unreachable — fall through to demo mode
    }

    // Demo fallback: simulate login locally
    const demoUser: AuthUser = {
      email,
      api_key: `rtn_demo_${Date.now().toString(36)}`,
      plan: 'free',
      role: isAdminEmail(email) ? 'admin' : 'user',
    }
    persistAuth(`demo_${Date.now()}`, demoUser)
    setUser(demoUser)
  }, [])

  const signup = useCallback(async (email: string, password: string) => {
    let apiKey: string

    try {
      const res = await fetch('/api/signup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })

      if (res.ok) {
        const data = (await res.json()) as { token: string; api_key: string; plan?: string }
        apiKey = data.api_key
        const authUser: AuthUser = {
          email,
          api_key: apiKey,
          plan: (data.plan as AuthUser['plan']) ?? 'free',
          role: isAdminEmail(email) ? 'admin' : 'user',
        }
        persistAuth(data.token, authUser)
        setUser(authUser)
        return { api_key: apiKey }
      }
    } catch {
      // Backend unreachable — fall through to demo mode
    }

    // Demo fallback
    apiKey = `rtn_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
    const demoUser: AuthUser = {
      email,
      api_key: apiKey,
      plan: 'free',
      role: isAdminEmail(email) ? 'admin' : 'user',
    }
    persistAuth(`demo_${Date.now()}`, demoUser)
    setUser(demoUser)
    return { api_key: apiKey }
  }, [])

  const logout = useCallback(() => {
    clearAuth()
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: user !== null,
        login,
        signup,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within <AuthProvider>')
  return ctx
}
