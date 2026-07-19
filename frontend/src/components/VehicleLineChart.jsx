import { useEffect, useRef, useState } from 'react'
import {
  AreaChart,
  Area,
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

function formatTick(ts) {
  if (!ts) return ''
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(ts))
}

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const formattedLabel = label
    ? new Intl.DateTimeFormat(undefined, {
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        month: 'short',  day: 'numeric',
      }).format(new Date(label))
    : ''
  return (
    <div className="bg-bg-card border border-bg-border rounded-xl shadow-card p-3 min-w-[160px]">
      <p className="text-text-muted text-xs mb-2">{formattedLabel}</p>
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

const MAX_LIVE_POINTS = 120

export default function VehicleLineChart({ historicalData = [], lastMessage, isLoading }) {
  const [chartData, setChartData] = useState([])

  useEffect(() => {
    if (historicalData?.length) {
      setChartData(historicalData.slice(-MAX_LIVE_POINTS))
    }
  }, [historicalData])

  useEffect(() => {
    if (!lastMessage || lastMessage.type !== 'vehicle_count') return
    const point = {
      ts:      lastMessage.timestamp ?? Date.now(),
      car:     lastMessage.car     ?? 0,
      bike:    lastMessage.bike    ?? 0,
      heavy:   lastMessage.heavy   ?? 0,
      bus:     lastMessage.bus     ?? 0,
      bicycle: lastMessage.bicycle ?? 0,
    }
    setChartData((prev) => {
      const next = [...prev, point]
      return next.length > MAX_LIVE_POINTS ? next.slice(-MAX_LIVE_POINTS) : next
    })
  }, [lastMessage])

  if (isLoading) {
    return <div className="skeleton h-72 w-full rounded-xl" />
  }

  return (
    <div className="w-full h-72">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={chartData} margin={{ top: 8, right: 16, left: -10, bottom: 0 }}>
          <defs>
            {CLASS_CONFIG.map((cls) => (
              <linearGradient key={cls.key} id={`vl-grad-${cls.key}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor={cls.hex} stopOpacity={0.18} />
                <stop offset="95%" stopColor={cls.hex} stopOpacity={0} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--bg-border)" strokeOpacity={0.5} vertical={false} />
          <XAxis dataKey="ts" tickFormatter={formatTick} tick={{ fontSize: 11, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} minTickGap={60} />
          <YAxis tick={{ fontSize: 11, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} width={36} />
          <Tooltip content={<CustomTooltip />} />
          <Legend wrapperStyle={{ paddingTop: '12px', fontSize: '12px' }} formatter={(v) => CLASS_CONFIG.find((c) => c.key === v)?.label ?? v} />
          {CLASS_CONFIG.map((cls) => (
            <Area key={cls.key} type="monotone" dataKey={cls.key} stroke={cls.hex} strokeWidth={2}
              fill={`url(#vl-grad-${cls.key})`} dot={false}
              activeDot={{ r: 4, strokeWidth: 0, fill: cls.hex }}
              isAnimationActive animationDuration={800} />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
