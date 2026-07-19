import { useEffect, useRef, useState } from 'react'
import {
  Car, Bike, Bus, Truck, TrendingUp, TrendingDown, Minus,
} from 'lucide-react'

// ─── Animated number counter ──────────────────────────────────────────────────
function useCountUp(target, duration = 1200) {
  const [value, setValue] = useState(0)
  const frameRef = useRef(null)
  const startTimeRef = useRef(null)
  const startValueRef = useRef(0)

  useEffect(() => {
    if (target === undefined || target === null) return
    startValueRef.current = 0
    startTimeRef.current = null

    const animate = (timestamp) => {
      if (!startTimeRef.current) startTimeRef.current = timestamp
      const elapsed = timestamp - startTimeRef.current
      const progress = Math.min(elapsed / duration, 1)
      // Ease out cubic
      const ease = 1 - Math.pow(1 - progress, 3)
      setValue(Math.round(startValueRef.current + (target - startValueRef.current) * ease))
      if (progress < 1) {
        frameRef.current = requestAnimationFrame(animate)
      }
    }

    frameRef.current = requestAnimationFrame(animate)
    return () => cancelAnimationFrame(frameRef.current)
  }, [target, duration])

  return value
}

// ─── Card config ──────────────────────────────────────────────────────────────
const CARD_CONFIGS = [
  {
    key: 'total',
    label: 'Total Vehicles',
    icon: Car,
    gradient: 'from-accent-cyan to-accent-blue',
    glow: 'hover:shadow-glow-cyan',
    borderHover: 'hover:border-accent-cyan/40',
    iconBg: 'bg-accent-cyan/10',
    iconColor: 'text-accent-cyan',
    progressColor: 'bg-accent-cyan',
  },
  {
    key: 'car',
    label: 'Car / Jeep / Van',
    icon: Car,
    gradient: 'from-accent-blue to-accent-cyan',
    glow: 'hover:shadow-glow-cyan',
    borderHover: 'hover:border-accent-blue/40',
    iconBg: 'bg-accent-blue/10',
    iconColor: 'text-accent-blue',
    progressColor: 'bg-chart-car',
  },
  {
    key: 'bike',
    label: 'Two Wheelers',
    icon: Bike,
    gradient: 'from-accent-purple to-accent-blue',
    glow: 'hover:shadow-glow-purple',
    borderHover: 'hover:border-accent-purple/40',
    iconBg: 'bg-accent-purple/10',
    iconColor: 'text-accent-purple',
    progressColor: 'bg-chart-bike',
  },
  {
    key: 'heavy',
    label: 'Heavy Vehicles',
    icon: Truck,
    gradient: 'from-accent-amber to-accent-red',
    glow: 'hover:shadow-glow-amber',
    borderHover: 'hover:border-accent-amber/40',
    iconBg: 'bg-accent-amber/10',
    iconColor: 'text-accent-amber',
    progressColor: 'bg-chart-heavy',
  },
  {
    key: 'bus',
    label: 'Buses',
    icon: Bus,
    gradient: 'from-accent-green to-accent-cyan',
    glow: 'hover:shadow-glow-green',
    borderHover: 'hover:border-accent-green/40',
    iconBg: 'bg-accent-green/10',
    iconColor: 'text-accent-green',
    progressColor: 'bg-chart-bus',
  },
]

// ─── Delta badge ──────────────────────────────────────────────────────────────
function DeltaBadge({ delta }) {
  if (delta === undefined || delta === null) return null
  const isUp = delta >= 0
  const isNeutral = delta === 0

  return (
    <span
      className={`inline-flex items-center gap-0.5 text-xs font-semibold px-2 py-0.5 rounded-full
        ${isNeutral
          ? 'bg-text-muted/10 text-text-muted'
          : isUp
            ? 'bg-accent-green/10 text-accent-green'
            : 'bg-accent-red/10 text-accent-red'
        }`}
    >
      {isNeutral ? <Minus size={10} /> : isUp ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
      {Math.abs(delta).toFixed(1)}%
    </span>
  )
}

// ─── Single summary card ──────────────────────────────────────────────────────
function SummaryCard({ config, count, percentage, delta, isLoading }) {
  const animated = useCountUp(isLoading ? 0 : (count ?? 0))
  const Icon = config.icon
  const pct = percentage ?? 0

  return (
    <div
      className={`bg-bg-card rounded-xl border border-bg-border shadow-card
                  transition-all duration-300 cursor-default card-hover-glow
                  ${config.glow} ${config.borderHover}
                  p-5 flex flex-col gap-3 min-w-0`}
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-2">
        <div className={`w-10 h-10 rounded-lg ${config.iconBg} flex items-center justify-center flex-shrink-0`}>
          <Icon size={20} className={config.iconColor} />
        </div>
        <DeltaBadge delta={delta} />
      </div>

      {/* Count */}
      {isLoading ? (
        <div className="space-y-2">
          <div className="skeleton h-8 w-24 rounded" />
          <div className="skeleton h-4 w-16 rounded" />
        </div>
      ) : (
        <div className="count-up-animate">
          <p className={`text-3xl font-black bg-gradient-to-r ${config.gradient}
                         bg-clip-text text-transparent leading-none`}>
            {animated.toLocaleString()}
          </p>
        </div>
      )}

      {/* Label */}
      <p className="text-text-secondary text-xs font-medium uppercase tracking-wide">
        {config.label}
      </p>

      {/* Progress bar */}
      {config.key !== 'total' && !isLoading && (
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-xs text-text-muted">% of total</span>
            <span className="text-xs font-semibold text-text-secondary">
              {pct.toFixed(1)}%
            </span>
          </div>
          <div className="h-1.5 bg-bg-border rounded-full overflow-hidden">
            <div
              className={`h-full ${config.progressColor} rounded-full progress-bar-animate`}
              style={{ '--target-width': `${Math.min(pct, 100)}%`, width: `${Math.min(pct, 100)}%` }}
            />
          </div>
        </div>
      )}

      {config.key === 'total' && !isLoading && (
        <div className="text-xs text-text-muted">
          Today so far
        </div>
      )}
    </div>
  )
}

// ─── SummaryCards ─────────────────────────────────────────────────────────────
export default function SummaryCards({ data, isLoading }) {
  // data shape: { total, car, bike, heavy, bus, deltas: { total, car, bike, heavy, bus } }
  const total = data?.total ?? 0
  const safeData = {
    total,
    car:   data?.car   ?? 0,
    bike:  data?.bike  ?? 0,
    heavy: data?.heavy ?? 0,
    bus:   data?.bus   ?? 0,
  }

  const pct = (val) => total > 0 ? (val / total) * 100 : 0

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4">
      {CARD_CONFIGS.map((config) => (
        <SummaryCard
          key={config.key}
          config={config}
          count={safeData[config.key]}
          percentage={pct(safeData[config.key])}
          delta={data?.deltas?.[config.key]}
          isLoading={isLoading}
        />
      ))}
    </div>
  )
}
