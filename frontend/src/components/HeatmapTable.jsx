import { useMemo } from 'react'
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'

function CustomTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-bg-card border border-bg-border rounded-xl shadow-card p-3">
      <p className="text-text-secondary text-xs uppercase font-semibold">Time: {payload[0].payload.hour}</p>
      <p className="text-accent-purple text-sm font-bold mt-1">
        {payload[0].value.toLocaleString()} vehicles
      </p>
    </div>
  )
}

export default function HeatmapTable({ data = [], isLoading }) {
  const chartData = useMemo(() => {
    // Initialize 24 hour buckets
    const hours = Array.from({ length: 24 }, (_, i) => {
      const ampm = i >= 12 ? 'PM' : 'AM'
      const displayHour = i % 12 === 0 ? 12 : i % 12
      return {
        key: i,
        hour: `${displayHour} ${ampm}`,
        count: 0
      }
    })

    if (Array.isArray(data)) {
      data.forEach(cell => {
        if (!cell.hour) return
        const d = new Date(cell.hour)
        if (isNaN(d.getTime())) return
        const h = d.getHours()
        hours[h].count += Number(cell.count || 0)
      })
    }
    return hours
  }, [data])

  if (isLoading) {
    return <div className="skeleton h-52 w-full rounded-xl" />
  }

  if (chartData.reduce((acc, h) => acc + h.count, 0) === 0) {
    return (
      <div className="h-52 flex items-center justify-center text-text-muted text-sm bg-bg-card/20 rounded-xl border border-bg-border border-dashed">
        No hourly data available
      </div>
    )
  }

  return (
    <div className="w-full h-52">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={chartData} margin={{ top: 8, right: 16, left: -10, bottom: 0 }}>
          <defs>
            <linearGradient id="heatmapAreaGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="var(--accent-purple)" stopOpacity={0.35} />
              <stop offset="95%" stopColor="var(--accent-purple)" stopOpacity={0.01} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--bg-border)" strokeOpacity={0.4} vertical={false} />
          <XAxis 
            dataKey="hour" 
            tick={{ fontSize: 10, fill: 'var(--text-muted)' }} 
            axisLine={false} 
            tickLine={false} 
            minTickGap={20}
          />
          <YAxis 
            tick={{ fontSize: 10, fill: 'var(--text-muted)' }} 
            axisLine={false} 
            tickLine={false} 
            width={30}
          />
          <Tooltip content={<CustomTooltip />} />
          <Area 
            type="monotone" 
            dataKey="count" 
            stroke="var(--accent-purple)" 
            strokeWidth={2} 
            fill="url(#heatmapAreaGrad)" 
            dot={false} 
            activeDot={{ r: 4, strokeWidth: 0, fill: 'var(--accent-purple)' }}
            animationDuration={800}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
