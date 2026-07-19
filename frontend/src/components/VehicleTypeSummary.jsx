import { useRef, useEffect } from 'react'
import { Car, Bike, Bus, Truck } from 'lucide-react'

const CLASS_CONFIG = [
  { key: 'car',     label: 'Car / Jeep / Van', Icon: Car,   tailwindBar: 'bg-chart-car',     tailwindText: 'text-chart-car'     },
  { key: 'bike',    label: 'Two Wheelers',      Icon: Bike,  tailwindBar: 'bg-chart-bike',    tailwindText: 'text-chart-bike'    },
  { key: 'heavy',   label: 'Heavy Vehicles',    Icon: Truck, tailwindBar: 'bg-chart-heavy',   tailwindText: 'text-chart-heavy'   },
  { key: 'bus',     label: 'Bus',               Icon: Bus,   tailwindBar: 'bg-chart-bus',     tailwindText: 'text-chart-bus'     },
  { key: 'bicycle', label: 'Bicycle',           Icon: Bike,  tailwindBar: 'bg-chart-bicycle', tailwindText: 'text-chart-bicycle' },
]

function ProgressBar({ percentage, colorClass }) {
  const barRef = useRef(null)

  useEffect(() => {
    const el = barRef.current
    if (!el) return
    el.style.width = '0%'
    const raf = requestAnimationFrame(() => {
      el.style.transition = 'width 1.2s cubic-bezier(0.4, 0, 0.2, 1)'
      el.style.width = `${Math.min(percentage, 100)}%`
    })
    return () => cancelAnimationFrame(raf)
  }, [percentage])

  return (
    <div className="h-1.5 bg-bg-border rounded-full overflow-hidden mt-1.5">
      <div ref={barRef} className={`h-full ${colorClass} rounded-full`} style={{ width: '0%' }} />
    </div>
  )
}

export default function VehicleTypeSummary({ data, isLoading }) {
  const total = data?.total ?? 0

  if (isLoading) {
    return (
      <div className="space-y-4">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="flex items-center gap-3">
            <div className="skeleton w-8 h-8 rounded-lg" />
            <div className="flex-1 space-y-2">
              <div className="skeleton h-3 w-28 rounded" />
              <div className="skeleton h-1.5 w-full rounded-full" />
            </div>
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {CLASS_CONFIG.map(({ key, label, Icon, tailwindBar, tailwindText }) => {
        const count = data?.[key] ?? 0
        const pct = total > 0 ? (count / total) * 100 : 0

        return (
          <div key={key} className="flex items-start gap-3 group">
            {/* Icon */}
            <div className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0
                             bg-bg-border group-hover:scale-105 transition-transform`}>
              <Icon size={15} className={tailwindText} />
            </div>

            {/* Label + bar */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between mb-0.5">
                <span className="text-sm text-text-secondary font-medium">{label}</span>
                <div className="flex items-center gap-2 flex-shrink-0 ml-2">
                  <span className={`text-sm font-bold ${tailwindText}`}>
                    {count.toLocaleString()}
                  </span>
                  <span className="text-text-muted text-xs">
                    {pct.toFixed(1)}%
                  </span>
                </div>
              </div>
              <ProgressBar percentage={pct} colorClass={tailwindBar} />
            </div>
          </div>
        )
      })}
    </div>
  )
}
