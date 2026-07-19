import { useState, useEffect } from 'react'
import { RefreshCw, AlertCircle, Radio, Ruler } from 'lucide-react'
import { useApi } from '../hooks/useApi'
import api from '../lib/api'
import CountingLineEditor from './CountingLineEditor'

const STREAM_BASE = import.meta.env.VITE_STREAM_BASE_URL || `${window.location.protocol}//${window.location.hostname}:8001`

export default function LiveFeedPanel({ lastMessage }) {
  const { data: camerasResp, loading: camsLoading } = useApi('/api/cameras')
  const cameras = camerasResp?.items ?? []
  const [selectedId, setSelectedId] = useState(null)
  const [imgError, setImgError] = useState(false)
  const [imgKey, setImgKey] = useState(0)
  const [liveStats, setLiveStats] = useState(null)
  const [showLineEditor, setShowLineEditor] = useState(false)

  // Pick first camera by default
  useEffect(() => {
    if (cameras?.length && selectedId === null) {
      setSelectedId(cameras[0]?.id ?? cameras[0]?.camera_id)
    }
  }, [cameras, selectedId])

  // Fetch initial stats for selected camera from events history
  useEffect(() => {
    if (!selectedId) return
    setLiveStats(null) // reset stats while loading
    api.get('/api/events', { params: { camera_id: selectedId, limit: 1000 } })
      .then((res) => {
        const items = res.data?.items ?? []
        const stats = { car: 0, bike: 0, heavy: 0, bus: 0 }
        items.forEach((evt) => {
          const cls = evt.vehicle_class
          if (cls === 'car') stats.car += 1
          else if (cls === 'motorcycle' || cls === 'bicycle') stats.bike += 1
          else if (cls === 'truck') stats.heavy += 1
          else if (cls === 'bus') stats.bus += 1
        })
        setLiveStats(stats)
      })
      .catch((err) => {
        console.error("Failed to fetch camera stats:", err)
        setLiveStats({ car: 0, bike: 0, heavy: 0, bus: 0 })
      })
  }, [selectedId])

  // Update live stats from WebSocket events
  useEffect(() => {
    if (!lastMessage) return
    if (lastMessage.type === 'new_event') {
      const evt = lastMessage.event
      if (String(evt.camera_id) === String(selectedId)) {
        setLiveStats((prev) => {
          const next = prev ? { ...prev } : { car: 0, bike: 0, heavy: 0, bus: 0 }
          const cls = evt.vehicle_class
          if (cls === 'car') next.car += 1
          else if (cls === 'motorcycle' || cls === 'bicycle') next.bike += 1
          else if (cls === 'truck') next.heavy += 1
          else if (cls === 'bus') next.bus += 1
          return next
        })
      }
    }
  }, [lastMessage, selectedId])

  const selectedCam = cameras?.find((c) => (c.id ?? c.camera_id) === selectedId)
  const streamSrc = selectedId ? `${STREAM_BASE}/stream/${selectedId}` : null

  const handleCameraChange = (e) => {
    setSelectedId(e.target.value)
    setImgError(false)
    setImgKey((k) => k + 1)
  }

  const handleRetry = () => {
    setImgError(false)
    setImgKey((k) => k + 1)
  }

  const totalNow = liveStats
    ? ((liveStats.car ?? 0) + (liveStats.bike ?? 0) + (liveStats.heavy ?? 0) + (liveStats.bus ?? 0) + (liveStats.bicycle ?? 0))
    : null

  return (
    <div className="flex flex-col gap-3 h-full">
      {/* ── Camera selector ── */}
      <div className="flex items-center gap-3">
        <label className="text-text-muted text-xs font-medium uppercase tracking-widest whitespace-nowrap">
          Camera
        </label>
        <div className="relative flex-1 max-w-[260px]">
          <select
            value={selectedId ?? ''}
            onChange={handleCameraChange}
            disabled={camsLoading}
            className="w-full bg-bg border border-bg-border rounded-lg px-3 py-2 text-text-primary text-sm
                       appearance-none cursor-pointer hover:border-accent-cyan/40 transition-colors"
          >
            {camsLoading && <option value="">Loading cameras…</option>}
            {!camsLoading && !cameras?.length && <option value="">No cameras found</option>}
            {cameras?.map((cam) => {
              const id = cam.id ?? cam.camera_id
              return (
                <option key={id} value={id}>
                  {cam.name ?? `Camera ${id}`} — {cam.location ?? ''}
                </option>
              )
            })}
          </select>
          <div className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-text-muted text-xs">▾</div>
        </div>
        {/* Configure Lines button */}
        {selectedCam && (
          <button
            onClick={() => setShowLineEditor(true)}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-bg border border-bg-border text-text-secondary text-xs hover:border-accent-cyan/40 hover:text-accent-cyan transition-all"
          >
            <Ruler size={13} />
            Configure Lines
          </button>
        )}
      </div>

      {/* ── Stream container ── */}
      <div className="relative flex-1 min-h-[240px] rounded-xl overflow-hidden bg-bg border border-bg-border">
        {/* MJPEG stream */}
        {streamSrc && !imgError && (
          <img
            key={imgKey}
            src={streamSrc}
            alt="Live camera feed"
            className="w-full h-full object-cover"
            onError={() => setImgError(true)}
          />


        )}

        {/* Offline state */}
        {(imgError || !streamSrc) && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3">
            <AlertCircle size={36} className="text-text-muted" />
            <p className="text-text-muted text-sm">
              {!streamSrc ? 'No camera selected' : 'Camera offline or stream unavailable'}
            </p>
            {imgError && (
              <button
                onClick={handleRetry}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-bg-hover border border-bg-border
                           text-text-secondary text-xs hover:border-accent-cyan/40 hover:text-accent-cyan transition-all"
              >
                <RefreshCw size={13} />
                Retry
              </button>
            )}
          </div>
        )}

        {/* Bottom gradient overlay */}
        {streamSrc && !imgError && (
          <div className="absolute inset-x-0 bottom-0 h-24 pointer-events-none"
            style={{ background: 'linear-gradient(to top, rgba(10,15,30,0.9) 0%, transparent 100%)' }} />
        )}

        {/* Top-left: camera info */}
        {selectedCam && (
          <div className="absolute top-3 left-3 flex items-center gap-2 px-3 py-1.5 rounded-full glass text-xs">
            <span className="w-2 h-2 rounded-full bg-accent-red live-pulse" />
            <span className="text-text-primary font-medium">{selectedCam.name ?? `Camera ${selectedId}`}</span>
            <span className="text-text-muted">·</span>
            <span className="text-text-muted">{selectedCam.location ?? '—'}</span>
          </div>
        )}

        {/* Bottom-left: LIVE label */}
        {streamSrc && !imgError && (
          <div className="absolute bottom-3 left-3 flex items-center gap-1.5 text-xs">
            <Radio size={11} className="text-accent-red live-pulse" />
            <span className="text-text-secondary font-semibold uppercase tracking-wide">LIVE</span>
          </div>
        )}
      </div>

      {/* Counting Line Editor Modal */}
      {showLineEditor && selectedCam && (
        <CountingLineEditor
          camera={selectedCam}
          onClose={() => setShowLineEditor(false)}
          onSaved={() => {
            setShowLineEditor(false)
            setImgKey(k => k + 1)
          }}
        />
      )}
    </div>
  )
}
