import { useState } from 'react'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts'

const CLASS_CONFIG = [
  { key: 'car',     label: 'Car / Van',    hex: 'var(--chart-car)',     tailwind: 'bg-chart-car'     },
  { key: 'bike',    label: 'Two Wheelers', hex: 'var(--chart-bike)',    tailwind: 'bg-chart-bike'    },
  { key: 'heavy',   label: 'Heavy',        hex: 'var(--chart-heavy)',   tailwind: 'bg-chart-heavy'   },
  { key: 'bus',     label: 'Bus',          hex: 'var(--chart-bus)',     tailwind: 'bg-chart-bus'     },
  { key: 'bicycle', label: 'Bicycle',      hex: 'var(--chart-bicycle)', tailwind: 'bg-chart-bicycle' },
]

function CustomTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const { name, value, payload: p } = payload[0]
  const total = p?.total ?? 1
  return (
    <div className="bg-bg-card border border-bg-border rounded-xl shadow-card p-3">
      <p className="text-text-primary text-sm font-semibold">{name}</p>
      <p className="text-text-secondary text-xs mt-1">
        {value.toLocaleString()} &nbsp;
        <span className="text-accent-cyan font-medium">
          ({((value / total) * 100).toFixed(1)}%)
        </span>
      </p>
    </div>
  )
}

function CenterLabel({ cx, cy, total }) {
  return (
    <g>
      <text x={cx} y={cy - 10} textAnchor="middle" fill="var(--text-primary)" fontSize={26} fontWeight={800}>
        {total.toLocaleString()}
      </text>
      <text x={cx} y={cy + 12} textAnchor="middle" fill="var(--text-muted)" fontSize={11}>
        Total Vehicles
      </text>
    </g>
  )
}

export default function ClassDonutChart({ data, isLoading }) {
  const [activeIndex, setActiveIndex] = useState(null)

  if (isLoading) {
    return <div className="skeleton h-64 w-full rounded-xl" />
  }

  // Parse data whether it is an array or object
  let parsedData = {}
  let total = 0
  if (Array.isArray(data)) {
    data.forEach((item) => {
      const cat = item.category || item.vehicle_class
      const val = item.count || 0
      if (cat === 'cars' || cat === 'car') {
        parsedData.car = (parsedData.car || 0) + val
      } else if (cat === 'motorcycles' || cat === 'motorcycle' || cat === 'bike' || cat === 'bicycles' || cat === 'bicycle') {
        parsedData.bike = (parsedData.bike || 0) + val
      } else if (cat === 'heavy' || cat === 'truck') {
        parsedData.heavy = (parsedData.heavy || 0) + val
      } else if (cat === 'buses' || cat === 'bus') {
        parsedData.bus = (parsedData.bus || 0) + val
      }
      total += val
    })
    parsedData.total = total
  } else {
    parsedData = data || {}
    total = parsedData.total ?? 0
  }

  const chartData = CLASS_CONFIG.map((cls) => ({
    name:  cls.label,
    value: parsedData[cls.key] ?? 0,
    total,
    key:   cls.key,
    hex:   cls.hex,
  })).filter((d) => d.value > 0)

  if (!chartData.length) {
    return (
      <div className="h-64 flex items-center justify-center text-text-muted text-sm">
        No data available
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4 h-full">
      <div style={{ height: 220 }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={chartData}
              cx="50%"
              cy="50%"
              innerRadius={65}
              outerRadius={90}
              paddingAngle={3}
              dataKey="value"
              isAnimationActive
              animationDuration={900}
              onMouseEnter={(_, index) => setActiveIndex(index)}
              onMouseLeave={() => setActiveIndex(null)}
            >
              {chartData.map((entry, index) => (
                <Cell
                  key={entry.key}
                  fill={entry.hex}
                  opacity={activeIndex === null || activeIndex === index ? 1 : 0.45}
                  stroke={activeIndex === index ? 'rgba(255,255,255,0.2)' : 'transparent'}
                  strokeWidth={activeIndex === index ? 2 : 0}
                  style={{
                    transform: activeIndex === index ? 'scale(1.05)' : 'scale(1)',
                    transformOrigin: '50% 50%',
                    transition: 'all 0.2s ease',
                    cursor: 'pointer',
                  }}
                />
              ))}
              {/* Center label rendered via label prop */}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        {/* Overlay center text absolutely */}
        <div className="relative" style={{ marginTop: '-140px', pointerEvents: 'none', textAlign: 'center' }}>
          <p className="text-3xl font-black text-text-primary">
            {activeIndex !== null && chartData[activeIndex] 
              ? chartData[activeIndex].value.toLocaleString() 
              : total.toLocaleString()}
          </p>
          <p className="text-xs text-text-muted mt-1 uppercase tracking-wider font-semibold">
            {activeIndex !== null && chartData[activeIndex] 
              ? `${chartData[activeIndex].name} (${((chartData[activeIndex].value / total) * 100).toFixed(1)}%)` 
              : 'Total'}
          </p>
        </div>
      </div>

      {/* Custom legend */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 mt-2">
        {chartData.map((entry, i) => {
          const cls = CLASS_CONFIG.find((c) => c.key === entry.key)
          const pct = total > 0 ? ((entry.value / total) * 100).toFixed(1) : '0.0'
          return (
            <div
              key={entry.key}
              className="flex items-center gap-2 cursor-pointer"
              onMouseEnter={() => setActiveIndex(i)}
              onMouseLeave={() => setActiveIndex(null)}
            >
              <span className={`w-2.5 h-2.5 rounded-sm flex-shrink-0 ${cls?.tailwind}`} />
              <span className="text-xs text-text-secondary truncate">{entry.name}</span>
              <span className="ml-auto text-xs font-semibold text-text-primary">{pct}%</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
