import { useState, useRef, useEffect, useCallback } from 'react'
import { Plus, Trash2, Check, X, Palette, GripVertical } from 'lucide-react'
import api from '../lib/api'

const LINE_COLORS = ['#00d4ff', '#f59e0b', '#10b981', '#ef4444', '#7c3aed', '#ec4899', '#3b82f6', '#f97316']

const STREAM_BASE = import.meta.env.VITE_STREAM_BASE_URL || `${window.location.protocol}//${window.location.hostname}:8001`

export default function CountingLineEditor({ camera, onClose, onSaved }) {
  const [lines, setLines] = useState([])
  const [isDrawing, setIsDrawing] = useState(false)
  const [drawStart, setDrawStart] = useState(null)
  const [drawEnd, setDrawEnd] = useState(null)
  const [editingLineId, setEditingLineId] = useState(null)
  const [draggingEndpoint, setDraggingEndpoint] = useState(null) // { lineIdx, point: 'start'|'end' }
  const [streamError, setStreamError] = useState(false)
  const [saving, setSaving] = useState(false)
  const containerRef = useRef(null)

  // Load existing lines
  useEffect(() => {
    const fetchLines = async () => {
      try {
        const res = await api.get(`/api/counting-lines?camera_id=${camera.id}`)
        setLines(res.data || [])
      } catch (err) {
        console.warn('Failed to fetch counting lines:', err)
        // Fallback: use lines from camera object
        setLines(camera.counting_lines || [])
      }
    }
    fetchLines()
  }, [camera.id])

  const getRelativeCoords = useCallback((e) => {
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return { x: 0, y: 0 }
    return {
      x: Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height)),
    }
  }, [])

  const handleMouseDown = (e) => {
    if (draggingEndpoint) return
    const { x, y } = getRelativeCoords(e)
    setIsDrawing(true)
    setDrawStart({ x, y })
    setDrawEnd({ x, y })
  }

  const handleMouseMove = (e) => {
    const { x, y } = getRelativeCoords(e)

    if (draggingEndpoint) {
      setLines(prev => prev.map((line, idx) => {
        if (idx !== draggingEndpoint.lineIdx) return line
        if (draggingEndpoint.point === 'start') {
          return { ...line, x1: x, y1: y }
        } else {
          return { ...line, x2: x, y2: y }
        }
      }))
      return
    }

    if (isDrawing && drawStart) {
      setDrawEnd({ x, y })
    }
  }

  const handleMouseUp = () => {
    if (draggingEndpoint) {
      setDraggingEndpoint(null)
      return
    }

    if (isDrawing && drawStart && drawEnd) {
      const dx = drawEnd.x - drawStart.x
      const dy = drawEnd.y - drawStart.y
      const dist = Math.sqrt(dx * dx + dy * dy)

      if (dist > 0.02) {
        const nextLaneId = lines.length + 1
        const color = LINE_COLORS[(lines.length) % LINE_COLORS.length]
        const newLine = {
          id: null, // will be assigned by server
          _tempId: Date.now(),
          camera_id: camera.id,
          name: `Lane ${nextLaneId}`,
          x1: drawStart.x,
          y1: drawStart.y,
          x2: drawEnd.x,
          y2: drawEnd.y,
          lane_id: nextLaneId,
          direction: 'both',
          color: color,
        }
        setLines(prev => [...prev, newLine])
      }
    }

    setIsDrawing(false)
    setDrawStart(null)
    setDrawEnd(null)
  }

  const handleEndpointMouseDown = (e, lineIdx, point) => {
    e.stopPropagation()
    e.preventDefault()
    setDraggingEndpoint({ lineIdx, point })
  }

  const handleDeleteLine = (idx) => {
    setLines(prev => prev.filter((_, i) => i !== idx))
  }

  const handleLineNameChange = (idx, name) => {
    setLines(prev => prev.map((line, i) => i === idx ? { ...line, name } : line))
  }

  const handleDirectionChange = (idx, direction) => {
    setLines(prev => prev.map((line, i) => i === idx ? { ...line, direction } : line))
  }

  const handleColorChange = (idx, color) => {
    setLines(prev => prev.map((line, i) => i === idx ? { ...line, color } : line))
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      // Fetch existing server lines to diff
      const existingRes = await api.get(`/api/counting-lines?camera_id=${camera.id}`)
      const existing = existingRes.data || []
      const existingIds = new Set(existing.map(l => l.id))

      // Lines in current state that have server IDs
      const currentServerIds = new Set(lines.filter(l => l.id).map(l => l.id))

      // Delete lines that were removed
      for (const ex of existing) {
        if (!currentServerIds.has(ex.id)) {
          await api.delete(`/api/counting-lines/${ex.id}`)
        }
      }

      // Create or update lines
      for (const line of lines) {
        const payload = {
          name: line.name,
          x1: line.x1,
          y1: line.y1,
          x2: line.x2,
          y2: line.y2,
          lane_id: line.lane_id,
          direction: line.direction,
          color: line.color,
        }

        if (line.id && existingIds.has(line.id)) {
          // Update
          await api.patch(`/api/counting-lines/${line.id}`, payload)
        } else {
          // Create
          await api.post('/api/counting-lines', {
            ...payload,
            camera_id: camera.id,
          })
        }
      }

      onSaved?.()
      onClose()
    } catch (err) {
      alert('Failed to save lines: ' + (err.response?.data?.detail || err.message))
    } finally {
      setSaving(false)
    }
  }

  const hexToBorderClass = (hex) => {
    return { borderColor: hex, color: hex }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-bg-card border border-bg-border shadow-card rounded-xl w-full max-w-3xl p-6 space-y-4 max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold text-text-primary">Configure Counting Lines</h2>
            <p className="text-xs text-text-muted mt-1">
              Draw counting lines on camera: <span className="text-text-primary font-mono">{camera.name}</span>
            </p>
          </div>
          <button onClick={onClose} className="text-text-muted hover:text-text-primary p-1">
            <X size={20} />
          </button>
        </div>

        {/* Drawing Canvas */}
        <div
          ref={containerRef}
          className="relative border border-bg-border rounded-lg bg-black aspect-video overflow-hidden cursor-crosshair select-none"
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
        >
          {!streamError ? (
            <img
              src={`${STREAM_BASE}/stream/${camera.id}`}
              alt="live stream"
              className="w-full h-full object-contain pointer-events-none block"
              onError={() => setStreamError(true)}
            />
          ) : (
            <div className="absolute inset-0 flex flex-col items-center justify-center text-text-muted text-xs p-6 bg-gradient-to-br from-bg-card to-bg select-none">
              <span className="text-center font-semibold">Live stream offline</span>
              <span className="text-center mt-1 text-[10px]">Draw lines on this canvas by clicking and dragging</span>
            </div>
          )}

          {/* SVG Drawing Layer */}
          <svg className="absolute inset-0 w-full h-full" style={{ pointerEvents: 'none' }}>
            {/* Existing lines */}
            {lines.map((line, idx) => (
              <g key={line.id || line._tempId || idx}>
                {/* Line */}
                <line
                  x1={`${line.x1 * 100}%`}
                  y1={`${line.y1 * 100}%`}
                  x2={`${line.x2 * 100}%`}
                  y2={`${line.y2 * 100}%`}
                  stroke={line.color}
                  strokeWidth="3"
                  strokeDasharray={editingLineId === idx ? '6 4' : 'none'}
                />
                {/* Line name label at midpoint */}
                <text
                  x={`${((line.x1 + line.x2) / 2) * 100}%`}
                  y={`${((line.y1 + line.y2) / 2) * 100}%`}
                  fill={line.color}
                  fontSize="12"
                  fontWeight="bold"
                  textAnchor="middle"
                  dy="-8"
                  style={{ textShadow: '1px 1px 2px rgba(0,0,0,0.8)' }}
                >
                  {line.name}
                </text>
                {/* Start endpoint (draggable) */}
                <circle
                  cx={`${line.x1 * 100}%`}
                  cy={`${line.y1 * 100}%`}
                  r="7"
                  fill="#ef4444"
                  stroke="white"
                  strokeWidth="2"
                  style={{ pointerEvents: 'all', cursor: 'grab' }}
                  onMouseDown={(e) => handleEndpointMouseDown(e, idx, 'start')}
                />
                {/* End endpoint (draggable) */}
                <circle
                  cx={`${line.x2 * 100}%`}
                  cy={`${line.y2 * 100}%`}
                  r="7"
                  fill="#10b981"
                  stroke="white"
                  strokeWidth="2"
                  style={{ pointerEvents: 'all', cursor: 'grab' }}
                  onMouseDown={(e) => handleEndpointMouseDown(e, idx, 'end')}
                />
              </g>
            ))}

            {/* Currently drawing line */}
            {isDrawing && drawStart && drawEnd && (
              <line
                x1={`${drawStart.x * 100}%`}
                y1={`${drawStart.y * 100}%`}
                x2={`${drawEnd.x * 100}%`}
                y2={`${drawEnd.y * 100}%`}
                stroke="#ffffff"
                strokeWidth="2"
                strokeDasharray="6 3"
                opacity="0.8"
              />
            )}
          </svg>
        </div>

        {/* Drawing Instructions */}
        <div className="bg-bg/40 border border-bg-border rounded-lg p-3 text-[11px] text-text-secondary flex flex-col gap-1">
          <span className="font-semibold text-text-primary">💡 Drawing Guidelines:</span>
          <span>1. Click and drag on the frame to draw a new counting line across the road.</span>
          <span>2. Drag the <span className="text-red-400 font-bold">red</span> (start) or <span className="text-green-400 font-bold">green</span> (end) endpoint circles to reposition existing lines.</span>
          <span>3. Each line counts vehicles independently when they cross it.</span>
        </div>

        {/* Lines List */}
        {lines.length > 0 && (
          <div className="space-y-2">
            <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide">Configured Lines ({lines.length})</h3>
            {lines.map((line, idx) => (
              <div
                key={line.id || line._tempId || idx}
                className="flex items-center gap-3 bg-bg border border-bg-border rounded-lg p-3 group"
              >
                {/* Color swatch */}
                <div
                  className="w-4 h-4 rounded-full border-2 shrink-0"
                  style={{ backgroundColor: line.color, borderColor: line.color }}
                />
                {/* Name input */}
                <input
                  type="text"
                  value={line.name}
                  onChange={(e) => handleLineNameChange(idx, e.target.value)}
                  className="bg-transparent border-b border-bg-border text-text-primary text-sm font-medium w-28 focus:outline-none focus:border-accent-cyan"
                />
                {/* Direction select */}
                <select
                  value={line.direction}
                  onChange={(e) => handleDirectionChange(idx, e.target.value)}
                  className="bg-bg-card border border-bg-border text-text-secondary text-xs rounded px-2 py-1"
                >
                  <option value="both">Both</option>
                  <option value="down">Down only</option>
                  <option value="up">Up only</option>
                </select>
                {/* Color picker */}
                <div className="flex gap-1">
                  {LINE_COLORS.slice(0, 4).map((c) => (
                    <button
                      key={c}
                      onClick={() => handleColorChange(idx, c)}
                      className={`w-4 h-4 rounded-full border-2 transition-transform ${line.color === c ? 'scale-125 border-white' : 'border-transparent opacity-60 hover:opacity-100'}`}
                      style={{ backgroundColor: c }}
                    />
                  ))}
                </div>
                {/* Delete button */}
                <button
                  onClick={() => handleDeleteLine(idx)}
                  className="ml-auto text-accent-red/60 hover:text-accent-red transition-colors opacity-0 group-hover:opacity-100"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Actions */}
        <div className="flex justify-between items-center pt-2 border-t border-bg-border">
          <span className="text-xs text-text-muted">
            {lines.length === 0 ? 'No lines configured — draw on the canvas above' : `${lines.length} line${lines.length > 1 ? 's' : ''} configured`}
          </span>
          <div className="flex gap-3">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm font-medium text-text-secondary hover:text-text-primary transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="bg-accent-cyan hover:bg-accent-cyan/90 text-white px-5 py-2 rounded-lg text-sm font-medium flex items-center gap-1.5 disabled:opacity-50 transition-colors"
            >
              <Check size={16} />
              {saving ? 'Saving...' : 'Save Lines'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
