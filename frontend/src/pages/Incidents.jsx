import { useState, useEffect } from 'react'
import { AlertTriangle, CheckCircle, Clock, Archive, Bell } from 'lucide-react'
import { useApi } from '../hooks/useApi'
import api from '../lib/api'

export default function Incidents() {
  const [filter, setFilter] = useState('ALL')
  const [viewTab, setViewTab] = useState('active') // 'active' | 'archived'
  const { data, loading, refetch } = useApi(`/api/alerts?limit=50&offset=0`)
  
  // Local state to store alerts for instant optimistic updates
  const [alerts, setAlerts] = useState([])

  useEffect(() => {
    if (data?.items) {
      setAlerts(data.items)
    }
  }, [data])

  const handleAcknowledge = async (id) => {
    // Optimistic UI Update: immediately mark as acknowledged locally
    setAlerts(prev => prev.map(a => a.id === id ? { ...a, acknowledged: true } : a))
    
    try {
      await api.patch(`/api/alerts/${id}/acknowledge`, { acknowledged: true })
      refetch()
    } catch (err) {
      console.error("Failed to acknowledge alert", err)
      refetch() // Revert to server state on failure
    }
  }

  const handleAcknowledgeAll = async () => {
    const unacknowledgedCount = alerts.filter(a => !a.acknowledged).length
    if (unacknowledgedCount === 0) {
      alert("No unacknowledged alerts to clear.")
      return
    }

    if (!window.confirm(`Are you sure you want to acknowledge all ${unacknowledgedCount} active alerts?`)) {
      return
    }

    // Optimistic UI Update: mark all as acknowledged locally
    setAlerts(prev => prev.map(a => ({ ...a, acknowledged: true })))

    try {
      await api.post('/api/alerts/acknowledge-all')
      refetch()
    } catch (err) {
      console.error("Failed to acknowledge all alerts", err)
      refetch() // Revert on failure
    }
  }

  const filteredAlerts = alerts.filter(a => {
    if (filter !== 'ALL' && a.severity !== filter) return false
    if (viewTab === 'active' && a.acknowledged) return false
    if (viewTab === 'archived' && !a.acknowledged) return false
    return true
  })

  const activeCount = alerts.filter(a => !a.acknowledged).length
  const archivedCount = alerts.filter(a => a.acknowledged).length

  return (
    <div className="p-6 space-y-6 max-w-5xl mx-auto">
      {/* Page Title */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-black bg-gradient-to-r from-accent-red to-accent-amber bg-clip-text text-transparent flex items-center gap-3">
            <AlertTriangle className="text-accent-red" />
            Incidents & Alerts
          </h1>
          <p className="text-text-secondary mt-1">Manage system alerts and anomaly detections</p>
        </div>
      </div>

      {/* Tabs Selector */}
      <div className="flex border-b border-bg-border pb-px">
        <button
          onClick={() => setViewTab('active')}
          className={`flex items-center gap-2 px-5 py-3 border-b-2 font-semibold text-sm transition-all
            ${viewTab === 'active' 
              ? 'border-accent-red text-accent-red bg-accent-red/5' 
              : 'border-transparent text-text-muted hover:text-text-secondary'}`}
        >
          <Bell size={16} />
          Active Alerts ({activeCount})
        </button>
        <button
          onClick={() => setViewTab('archived')}
          className={`flex items-center gap-2 px-5 py-3 border-b-2 font-semibold text-sm transition-all
            ${viewTab === 'archived' 
              ? 'border-accent-green text-accent-green bg-accent-green/5' 
              : 'border-transparent text-text-muted hover:text-text-secondary'}`}
        >
          <Archive size={16} />
          Acknowledged Archive ({archivedCount})
        </button>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-4 bg-bg-card p-4 rounded-xl border border-bg-border shadow-card">
         <div className="flex space-x-1 bg-bg p-1 rounded-lg border border-bg-border">
           {['ALL', 'HIGH', 'MEDIUM', 'LOW'].map(f => (
             <button
               key={f}
               onClick={() => setFilter(f)}
               className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-colors ${
                 filter === f 
                   ? 'bg-bg-card shadow-sm text-text-primary' 
                   : 'text-text-muted hover:text-text-secondary'
               }`}
             >
               {f}
             </button>
           ))}
         </div>
         
         <div className="flex items-center gap-3 ml-auto">
           {viewTab === 'active' && (
             <button 
               onClick={handleAcknowledgeAll} 
               className="px-3 py-1.5 bg-accent-green/10 border border-accent-green/20 hover:bg-accent-green/20 text-accent-green rounded-lg text-sm font-semibold transition-colors"
             >
                Acknowledge All
             </button>
           )}

           <button 
             onClick={refetch} 
             className="px-3 py-1.5 bg-bg border border-bg-border rounded-lg text-sm hover:bg-bg-border transition-colors"
           >
              Refresh
           </button>
         </div>
      </div>

      {/* List */}
      <div className="space-y-3">
        {loading && alerts.length === 0 ? (
           <div className="text-center py-10 text-text-muted">Loading alerts...</div>
        ) : filteredAlerts.length === 0 ? (
           <div className="text-center py-10 text-text-muted bg-bg-card rounded-xl border border-bg-border border-dashed">
             No alerts found in this section.
           </div>
        ) : (
          filteredAlerts.map(alert => (
            <div key={alert.id} className={`bg-bg-card rounded-xl border p-4 shadow-sm flex flex-col sm:flex-row gap-4 items-start sm:items-center transition-all hover:shadow-md
              ${alert.severity === 'HIGH' ? 'border-l-4 border-l-accent-red border-y-bg-border border-r-bg-border' : 
                alert.severity === 'MEDIUM' ? 'border-l-4 border-l-accent-amber border-y-bg-border border-r-bg-border' : 
                'border-l-4 border-l-accent-blue border-y-bg-border border-r-bg-border'}`}
            >
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <span className={`text-[10px] font-bold px-2 py-0.5 rounded-sm uppercase tracking-wider
                    ${alert.severity === 'HIGH' ? 'bg-accent-red/10 text-accent-red' : 
                      alert.severity === 'MEDIUM' ? 'bg-accent-amber/10 text-accent-amber' : 
                      'bg-accent-blue/10 text-accent-blue'}`}
                  >
                    {alert.severity}
                  </span>
                  <span className="text-xs font-semibold text-text-primary uppercase tracking-widest">{alert.alert_type.replace('_', ' ')}</span>
                  <span className="text-xs text-text-muted flex items-center gap-1 ml-auto sm:ml-2">
                    <Clock size={12} />
                    {new Intl.DateTimeFormat(undefined, { dateStyle: 'short', timeStyle: 'medium' }).format(new Date(alert.timestamp))}
                  </span>
                </div>
                <p className="text-text-secondary text-sm mt-1">{alert.message}</p>
                <div className="text-xs text-text-muted mt-2">Camera ID: {alert.camera_id}</div>
              </div>
              
              {!alert.acknowledged ? (
                <button 
                  onClick={() => handleAcknowledge(alert.id)}
                  className="w-full sm:w-auto px-4 py-2 bg-bg border border-bg-border hover:bg-accent-green/10 hover:border-accent-green/30 hover:text-accent-green text-text-secondary rounded-lg text-sm font-medium transition-all flex items-center justify-center gap-2 shrink-0"
                >
                  <CheckCircle size={16} />
                  Acknowledge
                </button>
              ) : (
                <div className="flex items-center gap-1 text-accent-green text-xs font-semibold shrink-0">
                  <CheckCircle size={14} />
                  Acknowledged
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
