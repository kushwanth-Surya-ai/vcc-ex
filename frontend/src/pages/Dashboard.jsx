import { useState, useEffect } from 'react'
import { useOutletContext } from 'react-router-dom'
import { useApi } from '../hooks/useApi'
import SummaryCards from '../components/SummaryCards'
import HeatmapTable from '../components/HeatmapTable'
import LiveFeedPanel from '../components/LiveFeedPanel'

export default function Dashboard() {
  const { lastMessage, connectionStatus } = useOutletContext()
  const [currentTime, setCurrentTime] = useState(new Date())

  // Update clock every second
  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 1000)
    return () => clearInterval(timer)
  }, [])

  // Fetch summary data
  const { data: summaryData, loading: summaryLoading } = useApi('/api/analytics/summary')
  // Fetch heatmap data
  const { data: heatmapData, loading: hmLoading, refetch: refetchHeatmap } = useApi('/api/analytics/hourly-heatmap')

  // State to hold live summary counts (mapped to match SummaryCards keys)
  const [liveSummaryData, setLiveSummaryData] = useState({
    total: 0,
    car: 0,
    bike: 0,
    heavy: 0,
    bus: 0,
    bicycle: 0,
    deltas: {
      total: 0,
      car: 0,
      bike: 0,
      heavy: 0,
      bus: 0,
      bicycle: 0
    }
  })

  // Synchronize initial REST API response to state
  useEffect(() => {
    if (summaryData) {
      setLiveSummaryData({
        total: summaryData.total_vehicles || 0,
        car: summaryData.class_counts?.car || 0,
        bike: (summaryData.class_counts?.motorcycle || 0) + (summaryData.class_counts?.bicycle || 0),
        heavy: summaryData.class_counts?.truck || 0,
        bus: summaryData.class_counts?.bus || 0,
        bicycle: summaryData.class_counts?.bicycle || 0,
        deltas: {
          total: summaryData.deltas?.total || 0,
          car: summaryData.deltas?.car || 0,
          bike: (summaryData.deltas?.motorcycle || 0) + (summaryData.deltas?.bicycle || 0),
          heavy: summaryData.deltas?.truck || 0,
          bus: summaryData.deltas?.bus || 0,
          bicycle: summaryData.deltas?.bicycle || 0
        }
      })
    }
  }, [summaryData])

  // Process live events from WebSocket
  useEffect(() => {
    if (lastMessage && lastMessage.type === 'new_event') {
      const evt = lastMessage.event
      const cls = evt.vehicle_class
      setLiveSummaryData(prev => {
        const next = { ...prev, total: (prev.total || 0) + 1 }
        
        if (cls === 'car') {
          next.car = (prev.car || 0) + 1
        } else if (cls === 'motorcycle') {
          next.bike = (prev.bike || 0) + 1
        } else if (cls === 'bus') {
          next.bus = (prev.bus || 0) + 1
        } else if (cls === 'truck') {
          next.heavy = (prev.heavy || 0) + 1
        } else if (cls === 'bicycle') {
          next.bike = (prev.bike || 0) + 1
          next.bicycle = (prev.bicycle || 0) + 1
        }
        
        return next
      })
      
      // Instantly refetch heatmap to show today's hour updates live
      refetchHeatmap()
    }
  }, [lastMessage, refetchHeatmap])

  return (
    <div className="p-6 space-y-6 page-mount">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-black bg-gradient-to-r from-accent-cyan to-accent-purple bg-clip-text text-transparent">
            Dashboard
          </h1>
          <p className="text-text-secondary mt-1">Real-time vehicle counting and classification</p>
        </div>
        <div className="text-right">
          <div className="text-xl font-mono text-text-primary">
            {new Intl.DateTimeFormat(undefined, { timeStyle: 'medium' }).format(currentTime)}
          </div>
          <div className="text-sm text-text-muted">
            {new Intl.DateTimeFormat(undefined, { dateStyle: 'full' }).format(currentTime)}
          </div>
        </div>
      </div>

      {/* Row 1: Summary Cards */}
      <SummaryCards data={liveSummaryData} isLoading={summaryLoading} />

      {/* Row 2: Live Feed & Heatmap */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <div className="xl:col-span-4 flex flex-col min-h-0 h-full">
          <LiveFeedPanel lastMessage={lastMessage} />
        </div>
        <div className="xl:col-span-8 flex flex-col min-h-0 bg-bg-card rounded-xl border border-bg-border shadow-card overflow-hidden">
          <div className="p-4 border-b border-bg-border">
            <h2 className="text-text-secondary uppercase tracking-widest text-xs font-semibold flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-accent-purple"></span>
              Hourly Traffic Analysis
            </h2>
          </div>
          <div className="flex-1 p-4 overflow-auto">
            <HeatmapTable data={heatmapData} isLoading={hmLoading} />
          </div>
        </div>
      </div>
    </div>
  )
}
