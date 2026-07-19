import { BrowserRouter, Routes, Route, Navigate, Outlet } from 'react-router-dom'
import { useState, useEffect } from 'react'
import { isAuthenticated, getUserRole, refreshAccessToken } from './lib/auth'
import Sidebar from './components/Sidebar'
import AlertBanner from './components/AlertBanner'
import { useWebSocket } from './hooks/useWebSocket'

import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import LiveView from './pages/LiveView'
import Analytics from './pages/Analytics'
import Incidents from './pages/Incidents'
import Settings from './pages/Settings'
import Devices from './pages/Devices'
import Users from './pages/Users'
import AuditLogs from './pages/AuditLogs'
import TrainingStudio from './pages/TrainingStudio'
import ChangePassword from './pages/ChangePassword'


// ─── Protected route wrapper ───────────────────────────────────────────────────
function ProtectedRoute() {
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace />
  }
  return <AppShell />
}

// ─── Admin route wrapper ───────────────────────────────────────────────────────
function AdminRoute({ children }) {
  if (getUserRole() !== 'admin') {
    return <Navigate to="/" replace />
  }
  return children
}

// ─── App shell: Sidebar + content area + global alert banner ─────────────────
function AppShell() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const { lastMessage, connectionStatus } = useWebSocket()

  return (
    <div className="flex h-screen bg-bg overflow-hidden">
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((v) => !v)}
        connectionStatus={connectionStatus}
      />

      {/* Main content */}
      <main
        className="flex-1 overflow-y-auto bg-bg transition-all duration-250"
        style={{ minWidth: 0 }}
      >
        <Outlet context={{ lastMessage, connectionStatus }} />
      </main>

      {/* Global alert toasts */}
      <AlertBanner lastMessage={lastMessage} />
    </div>
  )
}

// ─── Root App ─────────────────────────────────────────────────────────────────
export default function App() {
  const [initializing, setInitializing] = useState(true)

  useEffect(() => {
    const initAuth = async () => {
      try {
        await refreshAccessToken()
      } catch (err) {
        console.log("No active session found, redirecting to login.")
      } finally {
        setInitializing(false)
      }
    }
    initAuth()
  }, [])

  if (initializing) {
    return (
      <div className="min-h-screen bg-bg flex flex-col items-center justify-center gap-3">
        <span className="w-10 h-10 border-4 border-accent-cyan border-t-transparent rounded-full animate-spin"></span>
        <span className="text-text-secondary text-sm font-semibold tracking-wide">Initializing VCC Auth Session...</span>
      </div>
    )
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<ProtectedRoute />}>
          <Route path="/"           element={<Dashboard />} />
          <Route path="/live"       element={<LiveView />} />
          <Route path="/analytics"  element={<Analytics />} />
          <Route path="/incidents"  element={<Incidents />} />
          <Route path="/settings"   element={<Settings />} />
          <Route path="/devices"    element={<Devices />} />
          <Route path="/users"      element={<AdminRoute><Users /></AdminRoute>} />
          <Route path="/logs"       element={<AdminRoute><AuditLogs /></AdminRoute>} />
          <Route path="/training"   element={<AdminRoute><TrainingStudio /></AdminRoute>} />
          <Route path="/change-password" element={<ChangePassword />} />
          {/* Analytics sub-routes */}
          <Route path="/analytics/volume"         element={<Analytics tab="volume" />} />
          <Route path="/analytics/classification" element={<Analytics tab="classification" />} />
        </Route>

        {/* Catch-all */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
