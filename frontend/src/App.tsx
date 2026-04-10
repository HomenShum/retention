import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Landing } from './pages/Landing'
import { Dashboard } from './pages/Dashboard'
import { Workflows } from './pages/Workflows'
import { Judge } from './pages/Judge'
import { Anatomy } from './pages/Anatomy'
import { Benchmark } from './pages/Benchmark'
import { Compare } from './pages/Compare'
import { Proof } from './pages/Proof'
import { Run } from './pages/Run'
import { Auth } from './pages/Auth'
import { Pricing } from './pages/Pricing'
import { Admin } from './pages/Admin'
import { Layout } from './components/Layout'
import { ChatProvider } from './contexts/ChatContext'
import { AuthProvider } from './contexts/AuthContext'
import { ChatPanel } from './components/ChatPanel'

export default function App() {
  return (
    <AuthProvider>
      <ChatProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<Landing />} />
            <Route path="/proof" element={<Proof />} />
            <Route path="/auth" element={<Auth />} />
            <Route path="/pricing" element={<Pricing />} />
            <Route path="/admin" element={<Admin />} />
            <Route path="/run/:id" element={<Run />} />
            <Route element={<Layout />}>
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/workflows" element={<Workflows />} />
              <Route path="/judge" element={<Judge />} />
              <Route path="/anatomy" element={<Anatomy />} />
              <Route path="/benchmark" element={<Benchmark />} />
              <Route path="/compare" element={<Compare />} />
            </Route>
          </Routes>
          <ChatPanel />
        </BrowserRouter>
      </ChatProvider>
    </AuthProvider>
  )
}
