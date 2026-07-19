import { useEffect, useState, useCallback } from "react"
import { X, AlertTriangle, AlertCircle, Info } from "lucide-react"

const MAX_TOASTS = 5
const AUTO_DISMISS_MS = 8000
let _id = 0
const nextId = () => ++_id

const SEV = {
  HIGH:   { border: "border-l-accent-red",   Icon: AlertTriangle, text: "text-accent-red",   progressBg: "bg-accent-red"   },
  MEDIUM: { border: "border-l-accent-amber", Icon: AlertCircle,   text: "text-accent-amber", progressBg: "bg-accent-amber" },
  LOW:    { border: "border-l-accent-blue",  Icon: Info,          text: "text-accent-blue",  progressBg: "bg-accent-blue"  },
}

function Toast({ toast, onDismiss }) {
  const [pct, setPct] = useState(100)
  const cfg = SEV[toast.severity] ?? SEV.LOW
  const { Icon } = cfg

  useEffect(() => {
    const step = 100 / (AUTO_DISMISS_MS / 80)
    const iv = setInterval(() => {
      setPct((p) => {
        if (p <= step) { clearInterval(iv); onDismiss(toast.id); return 0 }
        return p - step
      })
    }, 80)
    return () => clearInterval(iv)
  }, [toast.id, onDismiss])

  const ts = toast.timestamp
    ? new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(toast.timestamp))
    : ""

  return (
    <div className={`toast-slide-in relative bg-bg-card border border-bg-border border-l-4 ${cfg.border} rounded-xl shadow-card overflow-hidden max-w-sm w-full`}>
      <div className="p-4">
        <div className="flex items-start gap-3">
          <Icon size={18} className={`${cfg.text} flex-shrink-0 mt-0.5`} />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-0.5">
              <span className={`text-xs font-bold uppercase tracking-wider ${cfg.text}`}>{toast.severity}</span>
              {toast.camera && <span className="text-text-muted text-xs">· {toast.camera}</span>}
            </div>
            <p className="text-text-primary text-sm leading-snug">{toast.message}</p>
            {ts && <p className="text-text-muted text-xs mt-1">{ts}</p>}
          </div>
          <button onClick={() => onDismiss(toast.id)} className="text-text-muted hover:text-text-primary transition-colors flex-shrink-0">
            <X size={14} />
          </button>
        </div>
      </div>
      <div className="absolute bottom-0 left-0 h-0.5 bg-bg-border w-full">
        <div className={`h-full ${cfg.progressBg}`} style={{ width: `${pct}%`, transition: "none" }} />
      </div>
    </div>
  )
}

export default function AlertBanner({ lastMessage }) {
  const [toasts, setToasts] = useState([])
  const dismiss = useCallback((id) => setToasts((p) => p.filter((t) => t.id !== id)), [])

  useEffect(() => {
    if (!lastMessage || lastMessage.type !== "alert") return
    const t = {
      id: nextId(),
      severity: (lastMessage.severity ?? "LOW").toUpperCase(),
      message: lastMessage.message ?? "New alert",
      camera: lastMessage.camera_name ?? lastMessage.camera ?? null,
      timestamp: lastMessage.timestamp ?? Date.now(),
    }
    setToasts((p) => [t, ...p].slice(0, MAX_TOASTS))
  }, [lastMessage])

  if (!toasts.length) return null
  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col gap-3 pointer-events-none">
      {toasts.map((t) => (
        <div key={t.id} className="pointer-events-auto">
          <Toast toast={t} onDismiss={dismiss} />
        </div>
      ))}
    </div>
  )
}
