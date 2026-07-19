import { useMemo } from 'react'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts'

const CLASS_CONFIG = [
  { key: 'car',     label: 'Car / Van',    hex: 'var(--chart-car)' },
  { key: 'bike',    label: 'Two Wheelers', hex: 'var(--chart-bike)' },
  { key: 'heavy',   label: 'Heavy',        hex: 'var(--chart-heavy)' },
  { key: 'bus',     label: 'Bus',          hex: 'var(--chart-bus)' },
  { key: 'bicycle', label: 'Bicycle',      hex: 'var(--chart-bicycle)' },
]

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-bg-card border border-bg-border rounded-xl shadow-card p-3 min-w-[140px]">
      <p className="text-text-primary text-xs font-bold mb-2">{label}</p>
      {payload.map((entry) => (
        <div key={entry.dataKey} className="flex items-center justify-between gap-4 mb-1">
          <div className="flex items-center gap-1.5">
            <span className="inline-block w-2 h-2 rounded-full flex-shrink-0" style={{ background: entry.color }} />
            <span className="text-text-secondary text-xs">{CLASS_CONFIG.find((c) => c.key === entry.dataKey)?.label ?? entry.dataKey}</span>
          </div>
          <span className="text-text-primary text-xs font-semibold">{entry.value}</span>
        </div>
      ))}
    </div>
  )
}

export default function LaneBarChart({ data = [], isLoading }) {
  const chartData = useMemo(() => {
    if (!Array.isArray(data)) return []
    
    // Group raw by lane_id
    const grouped = {}
    data.forEach((item) => {
      const lane = item.lane_id || 1
      const cls = item.vehicle_class
      const val = item.count || 0
      
      const laneName = `Lane ${lane}`
      if (!grouped[laneName]) {
        grouped[laneName] = { name: laneName, car: 0, bike: 0, heavy: 0, bus: 0, bicycle: 0 }
      }
      
      if (cls === 'car') {
        grouped[laneName].car += val
      } else if (cls === 'motorcycle') {
        grouped[laneName].bike += val
      } else if (cls === 'truck') {
        grouped[laneName].heavy += val
      } else if (cls === 'bus') {
        grouped[laneName].bus += val
      } else if (cls === 'bicycle') {
        grouped[laneName].bicycle += val
      }
    })
    
    return Object.values(grouped).sort((a, b) => a.name.localeCompare(b.name))
  }, [data])

  if (isLoading) {
    return <div className="skeleton h-72 w-full rounded-xl" />
  }

  if (chartData.length === 0) {
    return (
      <div className="h-72 flex items-center justify-center text-text-muted text-sm">
        No lane data available
      </div>
    )
  }

  return (
    <div className="w-full h-72">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={chartData} margin={{ top: 8, right: 16, left: -10, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--bg-border)" strokeOpacity={0.5} vertical={false} />
          <XAxis dataKey="name" tick={{ fontSize: 11, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} />
          <YAxis tick={{ fontSize: 11, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} width={36} />
          <Tooltip content={<CustomTooltip />} />
          <Legend wrapperStyle={{ paddingTop: '12px', fontSize: '12px' }} formatter={(v) => CLASS_CONFIG.find((c) => c.key === v)?.label ?? v} />
          {CLASS_CONFIG.map((cls) => (
            <Bar
              key={cls.key}
              dataKey={cls.key}
              fill={cls.hex}
              radius={[4, 4, 0, 0]}
              animationDuration={800}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
