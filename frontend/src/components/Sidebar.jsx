import { useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import {
  LayoutDashboard,
  Video,
  BarChart2,
  ChevronDown,
  ChevronRight,
  AlertTriangle,
  FileText,
  Bell,
  Monitor,
  Settings,
  ChevronLeft,
  ChevronRight as ChevronRightIcon,
  Hash,
  Layers,
  GitBranch,
  Gauge,
  User,
  Wifi,
  WifiOff,
  BarChart3,
  PieChart,
  Cpu,
  KeyRound,
} from 'lucide-react'
import { getAccessToken, getUserRole } from '../lib/auth'

function getProfile() {
  const token = getAccessToken()
  if (!token) return { email: 'admin@vcc.local', role: 'admin' }
  try {
    const payload = JSON.parse(atob(token.split('.')[1]))
    return {
      email: payload.sub || 'admin@vcc.local',
      role: payload.role || 'admin'
    }
  } catch (e) {
    return { email: 'admin@vcc.local', role: 'admin' }
  }
}

const navItems = [
  { label: 'Dashboard',         path: '/',           icon: LayoutDashboard, exact: true },
  { label: 'Live View',         path: '/live',        icon: Video },
  {
    label: 'Vehicle Analytics',
    path: '/analytics',
    icon: BarChart2,
    subItems: [
      { label: 'Traffic Volume',      path: '/analytics/volume',         icon: BarChart3 },
      { label: 'Class Distribution',  path: '/analytics/classification', icon: PieChart }
    ]
  },
  { label: 'Devices',           path: '/devices',     icon: Monitor },
  { label: 'Training Studio',   path: '/training',    icon: Cpu },
  { label: 'Users',             path: '/users',       icon: User },
  { label: 'Audit Logs',        path: '/logs',        icon: FileText },
  { label: 'Settings',          path: '/settings',    icon: Settings },
]

// ─── Status pill ─────────────────────────────────────────────────────────────
function WsStatusPill({ status, collapsed }) {
  const isConnected = status === 'connected'
  return (
    <div
      className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium
        ${isConnected
          ? 'bg-accent-green/10 text-accent-green'
          : 'bg-text-muted/10 text-text-muted'
        }
        transition-all duration-300`}
      title={`WebSocket: ${status}`}
    >
      {isConnected
        ? <Wifi size={12} className="flex-shrink-0" />
        : <WifiOff size={12} className="flex-shrink-0" />
      }
      {!collapsed && (
        <span className="whitespace-nowrap">
          {status === 'connected'      ? 'Live'
          : status === 'connecting'   ? 'Connecting…'
          : status === 'authenticating'? 'Auth…'
          : 'Offline'}
        </span>
      )}
    </div>
  )
}

// ─── Single nav item ──────────────────────────────────────────────────────────
function NavItem({ item, collapsed, onNavigate }) {
  const location = useLocation()
  const [subOpen, setSubOpen] = useState(() =>
    item.subItems?.some((s) => location.pathname.startsWith(s.path)) ?? false,
  )

  const isActive = item.exact
    ? location.pathname === item.path
    : location.pathname === item.path ||
      (item.subItems && item.subItems.some((s) => location.pathname.startsWith(s.path)))

  const hasSubItems = Boolean(item.subItems?.length)

  const handleClick = (e) => {
    if (hasSubItems) {
      e.preventDefault()
      setSubOpen((v) => !v)
    } else {
      onNavigate?.()
    }
  }

  return (
    <li>
      <NavLink
        to={item.path}
        end={item.exact}
        onClick={handleClick}
        className={({ isActive: navActive }) =>
          `flex items-center gap-3 px-3 py-2.5 rounded-lg mx-2 cursor-pointer select-none
           transition-all duration-200 group relative
           ${isActive || navActive
             ? 'nav-active text-text-primary'
             : 'text-text-secondary hover:bg-bg-hover hover:text-text-primary'
           }`
        }
        title={collapsed ? item.label : undefined}
      >
        {/* Left accent bar (active) */}
        {isActive && (
          <span className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-6 bg-accent-cyan rounded-r" />
        )}

        {/* Icon */}
        <item.icon
          size={18}
          className={`flex-shrink-0 transition-colors duration-200
            ${isActive ? 'text-accent-cyan' : 'text-text-muted group-hover:text-text-secondary'}`}
        />

        {/* Label */}
        {!collapsed && (
          <span className="flex-1 text-sm font-medium truncate">
            {item.label}
          </span>
        )}

        {/* Chevron for expandable */}
        {!collapsed && hasSubItems && (
          <span className="ml-auto text-text-muted">
            {subOpen
              ? <ChevronDown size={14} />
              : <ChevronRight size={14} />
            }
          </span>
        )}

        {/* Active dot when collapsed */}
        {collapsed && isActive && (
          <span className="absolute right-1 top-1/2 -translate-y-1/2 w-1.5 h-1.5 rounded-full bg-accent-cyan" />
        )}
      </NavLink>

      {/* Sub-items */}
      {hasSubItems && !collapsed && (
        <div
          className={`overflow-hidden transition-all duration-300 ease-in-out
            ${subOpen ? 'max-h-64 opacity-100' : 'max-h-0 opacity-0'}`}
        >
          <ul className="mt-0.5 ml-4 border-l border-bg-border pl-3 pb-1 space-y-0.5">
            {item.subItems.map((sub) => {
              const SubIcon = sub.icon
              return (
                <li key={sub.path}>
                  <NavLink
                    to={sub.path}
                    className={({ isActive }) =>
                      `flex items-center gap-2.5 px-3 py-2 rounded-md text-xs font-medium
                       transition-all duration-150
                       ${isActive
                         ? 'text-accent-cyan bg-accent-cyan/10'
                         : 'text-text-muted hover:text-text-secondary hover:bg-bg-hover'
                       }`
                    }
                  >
                    <SubIcon size={13} className="flex-shrink-0" />
                    {sub.label}
                  </NavLink>
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </li>
  )
}

// ─── Sidebar ──────────────────────────────────────────────────────────────────
export default function Sidebar({ collapsed, onToggle, connectionStatus = 'disconnected' }) {
  return (
    <aside
      className="flex flex-col h-full bg-bg-card border-r border-bg-border relative z-30
                 transition-all duration-250 ease-in-out flex-shrink-0"
      style={{ width: collapsed ? '72px' : '260px' }}
    >
      {/* ── Logo ── */}
      <div className="flex items-center justify-between px-4 py-5 border-b border-bg-border min-h-[72px]">
        {!collapsed ? (
          <div className="flex items-center gap-3 overflow-hidden">
            {/* Logo mark */}
            <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-accent-cyan to-accent-purple
                            flex items-center justify-center flex-shrink-0 shadow-glow-cyan">
              <span className="text-white font-black text-sm leading-none">VC</span>
            </div>
            <div className="min-w-0">
              <p className="text-sm font-bold bg-gradient-to-r from-accent-cyan to-accent-purple
                            bg-clip-text text-transparent leading-tight">
                VCC System
              </p>
              <p className="text-xs text-text-muted leading-tight truncate">
                Analytics Dashboard
              </p>
            </div>
          </div>
        ) : (
          <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-accent-cyan to-accent-purple
                          flex items-center justify-center mx-auto shadow-glow-cyan">
            <span className="text-white font-black text-sm">VC</span>
          </div>
        )}
      </div>

      {/* ── Nav ── */}
      <nav className="flex-1 overflow-y-auto overflow-x-hidden py-3">
        <ul className="space-y-0.5">
          {navItems.filter(item => {
            const role = getUserRole()
            if (item.label === 'Training Studio' && role !== 'admin') return false
            if (item.label === 'Users' && role !== 'admin') return false
            if (item.label === 'Audit Logs' && role !== 'admin') return false
            return true
          }).map((item) => (
            <NavItem
              key={item.path}
              item={item}
              collapsed={collapsed}
            />
          ))}
        </ul>
      </nav>

      {/* ── Footer ── */}
      <div className="border-t border-bg-border px-3 py-3 space-y-3">
        {/* WS status */}
        <div className={`flex ${collapsed ? 'justify-center' : ''}`}>
          <WsStatusPill status={connectionStatus} collapsed={collapsed} />
        </div>

        {/* User profile */}
        <div className={`flex items-center gap-3 px-1 ${collapsed ? 'justify-center' : ''}`}>
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-accent-purple to-accent-cyan
                          flex items-center justify-center flex-shrink-0">
            <User size={14} className="text-white" />
          </div>
          {!collapsed && (
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold text-text-primary truncate capitalize">{getProfile().role}</p>
              <p className="text-xs text-text-muted truncate">{getProfile().email.split('@')[0]}</p>
            </div>
          )}
        </div>
        {/* Change Password link */}
        {!collapsed && (
          <NavLink
            to="/change-password"
            className={({ isActive }) =>
              `flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs font-medium transition-colors
               ${isActive
                 ? 'bg-accent-cyan/10 text-accent-cyan'
                 : 'text-text-muted hover:text-text-secondary hover:bg-bg-hover'
               }`
            }
          >
            <KeyRound size={12} className="flex-shrink-0" />
            Change Password
          </NavLink>
        )}
      </div>

      {/* ── Collapse toggle button ── */}
      <button
        onClick={onToggle}
        className="absolute -right-3 top-20 w-6 h-6 rounded-full bg-bg-card border border-bg-border
                   flex items-center justify-center text-text-muted hover:text-accent-cyan
                   hover:border-accent-cyan/50 transition-all duration-200 shadow-card z-10"
        title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      >
        {collapsed
          ? <ChevronRightIcon size={12} />
          : <ChevronLeft size={12} />
        }
      </button>
    </aside>
  )
}
