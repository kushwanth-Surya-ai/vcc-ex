import { useState, useEffect } from 'react'
import { Settings as SettingsIcon, Server, User, LogOut, Activity, ChevronDown, ChevronUp, Cpu, HardDrive, Sliders } from 'lucide-react'
import api from '../lib/api'
import { useNavigate } from 'react-router-dom'
import { clearTokens, getUserRole } from '../lib/auth'

export default function Settings() {
  const navigate = useNavigate()
  const [showLiveMetrics, setShowLiveMetrics] = useState(false)
  const [metrics, setMetrics] = useState(null)
  const [loading, setLoading] = useState(false)
  
  // Dynamic config states
  const [confidenceThreshold, setConfidenceThreshold] = useState(0.45)
  const [isConfigSaving, setIsConfigSaving] = useState(false)

  const handleLogout = async () => {
    try {
      await api.post('/auth/logout')
    } catch (e) { } // Ignore if logout endpoint fails
    clearTokens()
    navigate('/login')
  }

  // Fetch metrics when toggle is active
  useEffect(() => {
    if (!showLiveMetrics) {
      setMetrics(null)
      return
    }

    const fetchMetrics = async () => {
      try {
        const res = await api.get('/api/health/system-metrics')
        setMetrics(res.data)
      } catch (err) {
        console.error("Failed to fetch system metrics", err)
      } finally {
        setLoading(false)
      }
    }

    setLoading(true)
    fetchMetrics()
    const timer = setInterval(fetchMetrics, 3000)
    return () => clearInterval(timer)
  }, [showLiveMetrics])

  // Fetch current confidence threshold on mount
  useEffect(() => {
    const fetchConfig = async () => {
      try {
        const res = await api.get('/api/settings/config')
        setConfidenceThreshold(res.data.confidence_threshold)
      } catch (err) {
        console.error("Failed to fetch settings config", err)
      }
    }
    fetchConfig()
  }, [])

  const handleSaveConfig = async () => {
    setIsConfigSaving(true)
    try {
      await api.post('/api/settings/config', {
        confidence_threshold: Number(confidenceThreshold)
      })
      alert("Configuration updated successfully!")
    } catch (err) {
      alert("Failed to update config: " + (err.response?.data?.detail || err.message))
    } finally {
      setIsConfigSaving(false)
    }
  }

  const getBarColor = (pct) => {
    if (pct > 85) return 'bg-accent-red'
    if (pct > 65) return 'bg-accent-amber'
    return 'bg-accent-green'
  }

  const role = getUserRole()

  return (
    <div className="p-6 space-y-8 max-w-4xl mx-auto page-mount">
      <div>
        <h1 className="text-3xl font-black bg-gradient-to-r from-text-primary to-text-muted bg-clip-text text-transparent flex items-center gap-3">
          <SettingsIcon className="text-text-primary" />
          Settings
        </h1>
        <p className="text-text-secondary mt-1">System configuration and resource monitoring</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="space-y-6 md:col-span-2">
          {/* Live System Resource Utilization Toggle */}
          <div className="bg-bg-card rounded-xl border border-bg-border shadow-card overflow-hidden transition-all">
            <button 
              onClick={() => setShowLiveMetrics(!showLiveMetrics)}
              className="w-full flex items-center justify-between p-5 hover:bg-bg-hover/30 transition-colors text-left focus:outline-none"
            >
              <div className="flex items-center gap-3">
                <Activity size={20} className={showLiveMetrics ? 'text-accent-red animate-pulse' : 'text-text-muted'} />
                <div>
                  <h2 className="text-text-primary font-semibold text-base">Live Resource Utilization</h2>
                  <p className="text-xs text-text-muted mt-0.5">Monitor server CPU, RAM, Disk and GPU performance</p>
                </div>
              </div>
              {showLiveMetrics ? <ChevronUp size={20} className="text-text-muted" /> : <ChevronDown size={20} className="text-text-muted" />}
            </button>

            {showLiveMetrics && (
              <div className="border-t border-bg-border p-6 bg-bg/25 space-y-6">
                {loading && !metrics ? (
                  <div className="flex items-center justify-center py-6 gap-2 text-sm text-text-muted">
                    <span className="w-4 h-4 border-2 border-accent-purple border-t-transparent rounded-full animate-spin"></span>
                    Querying system resources...
                  </div>
                ) : !metrics ? (
                  <div className="text-center py-6 text-sm text-text-muted">
                    Metrics currently unavailable.
                  </div>
                ) : (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
                    {/* CPU */}
                    <div className="space-y-2 bg-bg-card p-4 rounded-lg border border-bg-border">
                      <div className="flex justify-between items-center text-sm font-semibold">
                        <span className="text-text-secondary flex items-center gap-1.5">
                          <Cpu size={14} className="text-accent-cyan" />
                          CPU Usage
                        </span>
                        <span className="text-text-primary font-mono">{metrics.cpu.toFixed(1)}%</span>
                      </div>
                      <div className="w-full h-2.5 bg-bg rounded-full overflow-hidden">
                        <div 
                          className={`h-full transition-all duration-1000 ${getBarColor(metrics.cpu)}`}
                          style={{ width: `${Math.min(metrics.cpu, 100)}%` }}
                        />
                      </div>
                    </div>

                    {/* RAM */}
                    <div className="space-y-2 bg-bg-card p-4 rounded-lg border border-bg-border">
                      <div className="flex justify-between items-center text-sm font-semibold">
                        <span className="text-text-secondary flex items-center gap-1.5">
                          <Activity size={14} className="text-accent-purple" />
                          RAM Utilization
                        </span>
                        <span className="text-text-primary font-mono">{metrics.ram.toFixed(1)}%</span>
                      </div>
                      <div className="w-full h-2.5 bg-bg rounded-full overflow-hidden">
                        <div 
                          className={`h-full transition-all duration-1000 ${getBarColor(metrics.ram)}`}
                          style={{ width: `${Math.min(metrics.ram, 100)}%` }}
                        />
                      </div>
                    </div>

                    {/* DISK */}
                    <div className="space-y-2 bg-bg-card p-4 rounded-lg border border-bg-border">
                      <div className="flex justify-between items-center text-sm font-semibold">
                        <span className="text-text-secondary flex items-center gap-1.5">
                          <HardDrive size={14} className="text-accent-green" />
                          Disk Space
                        </span>
                        <span className="text-text-primary font-mono">{metrics.disk.toFixed(1)}%</span>
                      </div>
                      <div className="w-full h-2.5 bg-bg rounded-full overflow-hidden">
                        <div 
                          className={`h-full transition-all duration-1000 ${getBarColor(metrics.disk)}`}
                          style={{ width: `${Math.min(metrics.disk, 100)}%` }}
                        />
                      </div>
                    </div>

                    {/* GPU */}
                    <div className="space-y-2 bg-bg-card p-4 rounded-lg border border-bg-border">
                      <div className="flex justify-between items-center text-sm font-semibold">
                        <span className="text-text-secondary flex items-center gap-1.5">
                          <Server size={14} className="text-accent-amber" />
                          GPU Memory
                        </span>
                        <span className="text-text-primary font-mono">{metrics.gpu.toFixed(1)}%</span>
                      </div>
                      <div className="w-full h-2.5 bg-bg rounded-full overflow-hidden">
                        <div 
                          className={`h-full transition-all duration-1000 ${getBarColor(metrics.gpu)}`}
                          style={{ width: `${Math.min(metrics.gpu, 100)}%` }}
                        />
                      </div>
                      <p className="text-[10px] text-text-muted truncate mt-1">
                        Active GPU: {metrics.gpu_name}
                      </p>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Model Configuration Card (Admin Only) */}
        {role === 'admin' && (
          <div className="bg-bg-card rounded-xl border border-bg-border shadow-card p-5">
            <h2 className="text-text-primary font-semibold flex items-center gap-2 mb-4">
              <Sliders size={18} className="text-accent-cyan" />
              Model Configuration
            </h2>
            <div className="space-y-4">
              <div>
                <div className="flex justify-between text-xs font-semibold uppercase text-text-muted mb-1">
                  <span>Confidence Threshold</span>
                  <span className="text-accent-cyan font-mono">{Number(confidenceThreshold).toFixed(2)}</span>
                </div>
                <input 
                  type="range" 
                  min="0.10" 
                  max="0.90" 
                  step="0.05"
                  value={confidenceThreshold} 
                  onChange={(e) => setConfidenceThreshold(e.target.value)}
                  className="w-full h-1.5 bg-bg rounded-lg appearance-none cursor-pointer accent-accent-cyan"
                />
                <p className="text-[10px] text-text-muted mt-1 leading-relaxed">
                  Adjust the confidence threshold at which objects are counted. Lower values capture more targets but increase false detections.
                </p>
              </div>
              <button 
                onClick={handleSaveConfig} 
                disabled={isConfigSaving}
                className="w-full bg-accent-cyan/10 hover:bg-accent-cyan/20 border border-accent-cyan/20 text-accent-cyan px-4 py-2 rounded-lg text-sm font-semibold transition-colors disabled:opacity-50"
              >
                {isConfigSaving ? 'Saving...' : 'Save Settings'}
              </button>
            </div>
          </div>
        )}

        {/* System Info */}
        <div className="bg-bg-card rounded-xl border border-bg-border shadow-card p-5">
          <h2 className="text-text-primary font-semibold flex items-center gap-2 mb-4">
            <Server size={18} className="text-accent-cyan" />
            System Info
          </h2>
          <div className="space-y-4">
            <div>
              <label className="text-xs text-text-muted uppercase font-semibold">API Endpoint</label>
              <div className="text-sm text-text-secondary break-all bg-bg p-2 rounded border border-bg-border mt-1">
                {import.meta.env.VITE_API_URL || `${window.location.protocol}//${window.location.host}/api`}
              </div>
            </div>
            <div>
              <label className="text-xs text-text-muted uppercase font-semibold">WebSocket Endpoint</label>
              <div className="text-sm text-text-secondary break-all bg-bg p-2 rounded border border-bg-border mt-1">
                {import.meta.env.VITE_WS_URL || `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`}
              </div>
            </div>
            <div>
              <label className="text-xs text-text-muted uppercase font-semibold">Version</label>
              <div className="text-sm text-text-secondary mt-1 font-mono">VCC Dashboard v1.0.0</div>
            </div>
          </div>
        </div>

        {/* Account Management */}
        <div className="bg-bg-card rounded-xl border border-bg-border shadow-card p-5">
          <h2 className="text-text-primary font-semibold flex items-center gap-2 mb-4">
            <User size={18} className="text-accent-purple" />
            Account Management
          </h2>
          <div className="space-y-4">
            <p className="text-sm text-text-secondary">Logged in to the command dashboard.</p>
            <button onClick={handleLogout} className="w-full bg-accent-red/10 border border-accent-red/20 text-accent-red hover:bg-accent-red/20 hover:border-accent-red/30 px-4 py-2 rounded-lg text-sm font-medium flex items-center justify-center gap-2 transition-colors">
              <LogOut size={16} />
              Log Out
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
