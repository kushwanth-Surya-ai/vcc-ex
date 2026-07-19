import { useState, useEffect } from 'react'
import { useOutletContext } from 'react-router-dom'
import { Activity, Camera } from 'lucide-react'
import LiveFeedPanel from '../components/LiveFeedPanel'
import { useApi } from '../hooks/useApi'

export default function LiveView() {
  const { lastMessage, connectionStatus } = useOutletContext()
  const [events, setEvents] = useState([])
  const { data: camerasResp, loading: camerasLoading } = useApi('/api/cameras')
  const cameras = camerasResp?.items ?? []

  useEffect(() => {
    if (lastMessage && lastMessage.type === 'new_event') {
      setEvents(prev => [lastMessage.event, ...prev].slice(0, 20)) // Keep last 20 events
    }
  }, [lastMessage])

  return (
    <div className="p-6 h-full flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-black bg-gradient-to-r from-accent-red to-accent-amber bg-clip-text text-transparent flex items-center gap-3">
            <Activity className="text-accent-red animate-pulse" />
            Live View
          </h1>
          <p className="text-text-secondary mt-1">Real-time camera feed and event stream</p>
        </div>
        <div className="flex items-center gap-3 bg-bg-card border border-bg-border px-4 py-2 rounded-full">
            <div className={`w-3 h-3 rounded-full ${connectionStatus === 'connected' ? 'bg-accent-green animate-pulse' : 'bg-accent-red'}`}></div>
            <span className="text-sm font-medium text-text-primary uppercase tracking-wide">
                {connectionStatus}
            </span>
        </div>
      </div>

      <div className="flex-1 grid grid-cols-1 xl:grid-cols-4 gap-6 min-h-0">
        {/* Main Feed */}
        <div className="xl:col-span-3 flex flex-col min-h-0 bg-bg-card rounded-xl border border-bg-border shadow-card overflow-hidden">
          <LiveFeedPanel lastMessage={lastMessage} />
        </div>

        {/* Side Panel */}
        <div className="xl:col-span-1 flex flex-col min-h-0 gap-6">
          <div className="bg-bg-card rounded-xl border border-bg-border shadow-card p-4 flex-1 flex flex-col overflow-hidden">
            <h2 className="text-text-secondary uppercase tracking-widest text-xs font-semibold flex items-center gap-2 mb-4 pb-2 border-b border-bg-border">
              <span className="w-2 h-2 rounded-full bg-accent-cyan"></span>
              Recent Events
            </h2>
            <div className="flex-1 overflow-y-auto space-y-3 pr-2 custom-scrollbar">
              {events.length === 0 ? (
                <div className="text-center text-text-muted py-8 text-sm">
                  Waiting for events...
                </div>
              ) : (
                events.map((evt, idx) => (
                  <div key={idx} className="bg-bg border border-bg-border rounded-lg p-3 text-sm animate-fade-in-right">
                    <div className="flex justify-between items-start mb-1">
                      <span className="font-semibold text-text-primary capitalize">{evt.vehicle_class}</span>
                      <span className="text-xs text-text-muted">
                        {new Intl.DateTimeFormat(undefined, { timeStyle: 'medium' }).format(new Date(evt.timestamp || Date.now()))}
                      </span>
                    </div>
                    <div className="text-xs text-text-secondary flex justify-between">
                      <span>Cam: {evt.camera_id}</span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
