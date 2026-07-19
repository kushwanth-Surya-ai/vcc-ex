import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Download, Filter, BarChart3, PieChart } from 'lucide-react'
import VehicleLineChart from '../components/VehicleLineChart'
import ClassDonutChart from '../components/ClassDonutChart'
import VehicleTypeSummary from '../components/VehicleTypeSummary'
import { useApi } from '../hooks/useApi'

export default function Analytics({ tab = 'volume' }) {
  // Date state for range selection
  const [startDate, setStartDate] = useState(new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString().split('T')[0])
  const [endDate, setEndDate] = useState(new Date().toISOString().split('T')[0])

  // Fetch API endpoints
  const { data: timeseriesData, loading: tsLoading } = useApi(`/api/analytics/timeseries?from=${startDate}&to=${endDate}`)
  const { data: classData, loading: classLoading } = useApi(`/api/analytics/by-class`)

  const downloadCSV = (headers, rows, filename) => {
    const csvContent = [
      headers.join(','),
      ...rows.map(row => row.map(val => `"${val}"`).join(','))
    ].join('\n')
    
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement("a")
    link.setAttribute("href", url)
    link.setAttribute("download", filename)
    link.style.visibility = 'hidden'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  const handleExport = () => {
    if (tab === 'volume') {
      if (!timeseriesData || !timeseriesData.length) {
        alert("No volume data available to export.")
        return
      }
      
      const headers = ['Timestamp', 'Car/Van', 'Two Wheelers', 'Heavy Vehicles', 'Buses', 'Bicycles', 'Total Count']
      const rows = timeseriesData.map(pt => [
        new Date(pt.ts).toISOString(),
        pt.car || 0,
        pt.bike || 0,
        pt.heavy || 0,
        pt.bus || 0,
        pt.bicycle || 0,
        pt.count || 0
      ])
      
      downloadCSV(headers, rows, `traffic_volume_${startDate}_to_${endDate}.csv`)
    } else {
      if (!classData || !classData.length) {
        alert("No classification data available to export.")
        return
      }
      
      const headers = ['Vehicle Class', 'Count']
      const rows = classData.map(item => [
        item.vehicle_class || item.category,
        item.count || 0
      ])
      
      downloadCSV(headers, rows, `class_distribution.csv`)
    }
  }

  // Define tab headers configuration
  const tabsConfig = [
    { id: 'volume',         label: 'Traffic Volume',      icon: BarChart3, path: '/analytics/volume' },
    { id: 'classification', label: 'Class Distribution',  icon: PieChart,  path: '/analytics/classification' }
  ]

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto page-mount">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-black bg-gradient-to-r from-accent-purple to-accent-blue bg-clip-text text-transparent">
            Vehicle Analytics
          </h1>
          <p className="text-text-secondary mt-1">Advanced traffic reporting and visual analysis</p>
        </div>
        <button onClick={handleExport} className="flex items-center gap-2 bg-bg-card hover:bg-bg-border border border-bg-border px-4 py-2 rounded-lg text-sm font-medium transition-colors">
          <Download size={16} className="text-accent-cyan" />
          Export CSV
        </button>
      </div>

      {/* Tabs navigation */}
      <div className="flex border-b border-bg-border overflow-x-auto pb-px">
        {tabsConfig.map((t) => {
          const ActiveIcon = t.icon
          const isActive = tab === t.id
          return (
            <Link
              key={t.id}
              to={t.path}
              className={`flex items-center gap-2 px-5 py-3 border-b-2 font-semibold text-sm transition-all whitespace-nowrap
                ${isActive 
                  ? 'border-accent-purple text-accent-purple bg-accent-purple/5' 
                  : 'border-transparent text-text-muted hover:text-text-secondary hover:bg-bg-hover/30'
                }`}
            >
              <ActiveIcon size={16} />
              {t.label}
            </Link>
          )
        })}
      </div>

      {/* Filters */}
      <div className="bg-bg-card rounded-xl border border-bg-border p-4 flex flex-wrap gap-4 items-end shadow-card">
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-semibold text-text-muted uppercase">Date Range</label>
          <div className="flex items-center gap-2">
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className="bg-bg border border-bg-border rounded-lg px-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent-purple" />
            <span className="text-text-muted">to</span>
            <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className="bg-bg border border-bg-border rounded-lg px-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent-purple" />
          </div>
        </div>
        <div className="flex flex-col gap-1.5 flex-1 min-w-[200px]">
          <label className="text-xs font-semibold text-text-muted uppercase">Camera Location</label>
          <select className="bg-bg border border-bg-border rounded-lg px-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent-purple w-full appearance-none">
            <option value="all">All Locations</option>
            <option value="1">Location 1</option>
          </select>
        </div>
        <button className="flex items-center gap-2 bg-accent-purple/10 text-accent-purple hover:bg-accent-purple/20 border border-accent-purple/20 px-4 py-1.5 rounded-lg text-sm font-medium transition-colors">
          <Filter size={16} />
          Apply Filters
        </button>
      </div>

      {/* Dynamic Tab Content */}
      <div className="grid grid-cols-1 gap-6">
        {/* TAB 1: VOLUME */}
        {tab === 'volume' && (
          <div className="bg-bg-card rounded-xl border border-bg-border shadow-card p-5">
            <h2 className="text-text-secondary uppercase tracking-widest text-xs font-semibold flex items-center gap-2 mb-6">
               <span className="w-2 h-2 rounded-full bg-accent-cyan animate-pulse"></span>
               Traffic Volume Trend Over Time
            </h2>
            <div className="h-96">
              <VehicleLineChart historicalData={timeseriesData} isLoading={tsLoading} />
            </div>
          </div>
        )}

        {/* TAB 2: CLASSIFICATION */}
        {tab === 'classification' && (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            <div className="lg:col-span-5 bg-bg-card rounded-xl border border-bg-border shadow-card p-5 flex flex-col justify-between">
              <div>
                <h2 className="text-text-secondary uppercase tracking-widest text-xs font-semibold flex items-center gap-2 mb-6">
                  <span className="w-2 h-2 rounded-full bg-accent-purple animate-pulse"></span>
                  Class Distribution
                </h2>
                <div className="h-64 flex items-center justify-center">
                  <ClassDonutChart data={classData} isLoading={classLoading} />
                </div>
              </div>
            </div>

            <div className="lg:col-span-7 bg-bg-card rounded-xl border border-bg-border shadow-card p-5">
              <h2 className="text-text-secondary uppercase tracking-widest text-xs font-semibold flex items-center gap-2 mb-6">
                <span className="w-2 h-2 rounded-full bg-accent-green animate-pulse"></span>
                Detailed Vehicle Type Breakdown
              </h2>
              <VehicleTypeSummary data={classData ? {
                total: classData.reduce((acc, c) => acc + (c.count || 0), 0),
                car: classData.find(c => c.vehicle_class === 'car')?.count || 0,
                bike: classData.find(c => c.vehicle_class === 'motorcycle')?.count || 0,
                heavy: classData.find(c => c.vehicle_class === 'truck')?.count || 0,
                bus: classData.find(c => c.vehicle_class === 'bus')?.count || 0,
                bicycle: classData.find(c => c.vehicle_class === 'bicycle')?.count || 0,
              } : null} isLoading={classLoading} />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
