import { useState, useEffect, useRef } from 'react'
import { Cpu, Video, Layers, Plus, Play, Square, RefreshCw, Check, Trash2, Camera, Info, Tag, ArrowRight } from 'lucide-react'
import { useApi } from '../hooks/useApi'
import api, { trainingApi } from '../lib/api'

const mainApiUrl = import.meta.env.VITE_API_URL || `${window.location.protocol}//${window.location.hostname}:8000`
const TRAINING_BASE = import.meta.env.VITE_TRAINING_API_URL || mainApiUrl.replace(/(:\d+)?\/?$/, ':8002')

const MIN_LABELED_IMAGES = 5

const PRESET_COLORS = [
  'border-[#00d4ff] text-[#00d4ff] bg-[#00d4ff]/10',
  'border-[#7c3aed] text-[#7c3aed] bg-[#7c3aed]/10',
  'border-[#10b981] text-[#10b981] bg-[#10b981]/10',
  'border-[#f59e0b] text-[#f59e0b] bg-[#f59e0b]/10',
  'border-[#f97316] text-[#f97316] bg-[#f97316]/10',
  'border-[#ef4444] text-[#ef4444] bg-[#ef4444]/10',
  'border-[#ec4899] text-[#ec4899] bg-[#ec4899]/10',
  'border-[#3b82f6] text-[#3b82f6] bg-[#3b82f6]/10',
  'border-[#06b6d4] text-[#06b6d4] bg-[#06b6d4]/10',
  'border-[#14b8a6] text-[#14b8a6] bg-[#14b8a6]/10'
]


export default function TrainingStudio() {
  const [activeTab, setActiveTab] = useState('dataset') // dataset | labeler | training
  const { data: camerasData } = useApi('/api/cameras')
  const cameras = camerasData?.items ?? []
  const { data: imagesData, refetch: refetchImages } = useApi('/api/training/images', {}, [], { apiInstance: trainingApi })
  const { data: trainingStatus, refetch: refetchStatus } = useApi('/api/training/status', {}, [], { apiInstance: trainingApi })

  const [selectedCamera, setSelectedCamera] = useState('')
  const [selectedImage, setSelectedImage] = useState(null)
  
  // Labeler canvas states
  const [drawnBoxes, setDrawnBoxes] = useState([])
  const [activeClass, setActiveClass] = useState(0)
  const [isDrawing, setIsDrawing] = useState(false)
  const [startPos, setStartPos] = useState(null)
  const [drawingBox, setDrawingBox] = useState(null)
  const [selectedBoxIndex, setSelectedBoxIndex] = useState(null)
  const [floatingPopupPos, setFloatingPopupPos] = useState(null)
  const imageRef = useRef(null)
  const containerRef = useRef(null)
  const rawBoxesRef = useRef([])



  // Training parameters
  const [epochs, setEpochs] = useState(10)
  const [batchSize, setBatchSize] = useState(8)
  const [isTrainingSubmitting, setIsTrainingSubmitting] = useState(false)

  // Auto-capture state
  const [autoCaptureEnabled, setAutoCaptureEnabled] = useState(false)
  const [autoCaptureInterval, setAutoCaptureInterval] = useState(10) // seconds
  const [autoCaptureStatus, setAutoCaptureStatus] = useState('') // last capture result
  const autoCaptureRef = useRef(null)

  // Label Classes states
  const [classLabels, setClassLabels] = useState({
    0: { name: 'Car', color: 'border-[#00d4ff] text-[#00d4ff] bg-[#00d4ff]/10' },
    1: { name: 'Motorcycle', color: 'border-[#7c3aed] text-[#7c3aed] bg-[#7c3aed]/10' },
    2: { name: 'Bus', color: 'border-[#10b981] text-[#10b981] bg-[#10b981]/10' },
    3: { name: 'Truck', color: 'border-[#f59e0b] text-[#f59e0b] bg-[#f59e0b]/10' },
    4: { name: 'Bicycle', color: 'border-[#f97316] text-[#f97316] bg-[#f97316]/10' }
  })
  const [newLabelName, setNewLabelName] = useState('')
  const [isLabelSubmitting, setIsLabelSubmitting] = useState(false)

  // Fetch classes dynamically on mount
  useEffect(() => {
    const fetchLabels = async () => {
      try {
        const res = await trainingApi.get('/api/training/labels')
        if (res.data) {
          const mapped = {}
          res.data.forEach(item => {
            mapped[item.id] = { name: item.name, color: item.color }
          })
          setClassLabels(mapped)
        }
      } catch (err) {
        console.error("Failed to load custom labels", err)
      }
    }
    fetchLabels()
  }, [])



  // Auto-select first camera
  useEffect(() => {
    if (cameras.length && !selectedCamera) {
      setSelectedCamera(cameras[0].id)
    }
  }, [cameras, selectedCamera])

  // Poll training status if actively training
  useEffect(() => {
    if (trainingStatus?.status === 'training') {
      const timer = setInterval(refetchStatus, 3000)
      return () => clearInterval(timer)
    }
  }, [trainingStatus, refetchStatus])

  // Poll captured images every 5 seconds while on the dataset gallery tab to show auto-captured frames live
  useEffect(() => {
    if (activeTab === 'dataset') {
      const timer = setInterval(refetchImages, 5000)
      return () => clearInterval(timer)
    }
  }, [activeTab, refetchImages])

  // Auto-capture interval effect
  useEffect(() => {
    if (autoCaptureRef.current) {
      clearInterval(autoCaptureRef.current)
      autoCaptureRef.current = null
    }
    if (autoCaptureEnabled && selectedCamera) {
      const doCapture = async () => {
        try {
          const res = await trainingApi.post(`/api/training/auto-capture?camera_id=${selectedCamera}`)
          const captured = res.data?.captured ?? 0
          setAutoCaptureStatus(captured > 0 ? `✓ Frame captured` : `⚠ Stream offline`)
          if (captured > 0) refetchImages()
        } catch (err) {
          setAutoCaptureStatus(`✗ Error: ${err.response?.data?.detail || err.message}`)
        }
      }
      doCapture() // capture immediately on enable
      autoCaptureRef.current = setInterval(doCapture, autoCaptureInterval * 1000)
    } else if (!autoCaptureEnabled) {
      setAutoCaptureStatus('')
    }
    return () => {
      if (autoCaptureRef.current) clearInterval(autoCaptureRef.current)
    }
  }, [autoCaptureEnabled, autoCaptureInterval, selectedCamera])


  // Fetch labels when selected image changes
  useEffect(() => {
    setSelectedBoxIndex(null)
    setFloatingPopupPos(null)
    if (!selectedImage) {
      setDrawnBoxes([])
      rawBoxesRef.current = []
      return
    }


    const fetchLabels = async () => {
      try {
        const res = await trainingApi.get(`/api/training/images/${selectedImage.filename}/label`)
        if (res.data?.boxes) {
          rawBoxesRef.current = res.data.boxes
          // If image is already loaded, map immediately, otherwise wait for onLoad trigger
          if (imageRef.current && imageRef.current.complete) {
            mapNormalizedToPixels(res.data.boxes)
          }
        }
      } catch (err) {
        console.error("Failed to fetch labels", err)
      }
    }

    fetchLabels()
  }, [selectedImage])

  // Handle keyboard shortcuts (Roboflow-style navigation, nudging, delete and class setting)
  useEffect(() => {
    const handleKeyDown = (e) => {
      // Ignore key events if the user is typing in an input/select/textarea element
      if (document.activeElement && ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
        return
      }

      // 1. Delete selected box (Delete or Backspace)
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedBoxIndex !== null) {
        handleRemoveBox(selectedBoxIndex)
        setSelectedBoxIndex(null)
        setFloatingPopupPos(null)
        e.preventDefault()
        return
      }

      // 2. Image Gallery Navigation (a/d or Left/Right Arrow keys)
      if (imagesData && imagesData.length > 0 && selectedImage) {
        const currentIdx = imagesData.findIndex(img => img.filename === selectedImage.filename)
        if (e.key === 'a' || e.key === 'ArrowLeft') {
          // If we are currently not nudging a selected box
          if (selectedBoxIndex === null) {
            if (currentIdx > 0) {
              setSelectedImage(imagesData[currentIdx - 1])
            }
            e.preventDefault()
            return
          }
        }
        if (e.key === 'd' || e.key === 'ArrowRight') {
          // If we are currently not nudging a selected box
          if (selectedBoxIndex === null) {
            if (currentIdx < imagesData.length - 1) {
              setSelectedImage(imagesData[currentIdx + 1])
            }
            e.preventDefault()
            return
          }
        }
      }

      // 3. Label category assignment (1-9 keys)
      const classesList = Object.keys(classLabels).map(Number).sort((a, b) => a - b)
      const pressedNum = parseInt(e.key, 10)
      if (!isNaN(pressedNum) && pressedNum >= 1 && pressedNum <= classesList.length) {
        const classId = classesList[pressedNum - 1]
        if (selectedBoxIndex !== null) {
          setDrawnBoxes(prev => prev.map((box, idx) => idx === selectedBoxIndex ? { ...box, class_id: classId } : box))
        } else {
          setActiveClass(classId)
        }
        e.preventDefault()
        return
      }

      // 4. Box nudging (Arrow keys)
      if (selectedBoxIndex !== null && ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) {
        const step = e.shiftKey ? 5 : 1
        setDrawnBoxes(prev => prev.map((box, idx) => {
          if (idx !== selectedBoxIndex) return box
          let { x, y, w, h } = box
          if (e.key === 'ArrowUp') y -= step
          if (e.key === 'ArrowDown') y += step
          if (e.key === 'ArrowLeft') x -= step
          if (e.key === 'ArrowRight') x += step
          return { ...box, x, y, w, h }
        }))
        e.preventDefault()
        return
      }

      // 5. Escape key to clear selection / close popup
      if (e.key === 'Escape') {
        setSelectedBoxIndex(null)
        setFloatingPopupPos(null)
        e.preventDefault()
        return
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [selectedBoxIndex, imagesData, selectedImage, classLabels])


  const mapNormalizedToPixels = (normBoxes) => {
    if (!normBoxes || !Array.isArray(normBoxes)) return
    if (!imageRef.current) return
    const w = imageRef.current.clientWidth
    const h = imageRef.current.clientHeight

    
    // If the image layout is not settled yet, retry in 50ms
    if (w === 0 || h === 0) {
      setTimeout(() => mapNormalizedToPixels(normBoxes), 50)
      return
    }
    
    const pixBoxes = normBoxes.map(box => ({
      class_id: box.class_id,
      x: (box.x_center - box.width / 2) * w,
      y: (box.y_center - box.height / 2) * h,
      w: box.width * w,
      h: box.height * h
    }))
    setDrawnBoxes(pixBoxes)
  }


  // Handle frame capture
  const handleCapture = async () => {
    if (!selectedCamera) return
    try {
      await trainingApi.post(`/api/training/capture?camera_id=${selectedCamera}`)
      refetchImages()
    } catch (err) {
      alert('Capture failed: ' + (err.response?.data?.detail || err.message))
    }
  }

  // Handle delete single image
  const handleDeleteImage = async (filename, e) => {
    e.stopPropagation()
    if (!window.confirm(`Delete ${filename}?`)) return
    try {
      await trainingApi.delete(`/api/training/images/${filename}`)
      if (selectedImage?.filename === filename) setSelectedImage(null)
      refetchImages()
    } catch (err) {
      alert('Delete failed: ' + (err.response?.data?.detail || err.message))
    }
  }

  // Handle delete all images
  const handleDeleteAllImages = async () => {
    if (!window.confirm('Delete ALL training images and labels? This cannot be undone.')) return
    try {
      await trainingApi.delete('/api/training/images')
      setSelectedImage(null)
      refetchImages()
    } catch (err) {
      alert('Delete all failed: ' + (err.response?.data?.detail || err.message))
    }
  }
  // Add new dynamic custom label category
  const handleAddLabelClass = async (e) => {
    e.preventDefault()
    if (!newLabelName.trim()) return
    setIsLabelSubmitting(true)
    try {
      const currentList = Object.entries(classLabels).map(([id, item]) => ({
        id: Number(id),
        name: item.name,
        color: item.color
      }))
      const nextId = currentList.length ? Math.max(...currentList.map(c => c.id)) + 1 : 0
      const nextColor = PRESET_COLORS[nextId % PRESET_COLORS.length]
      
      const updatedList = [...currentList, { id: nextId, name: newLabelName.trim(), color: nextColor }]
      
      await trainingApi.post('/api/training/labels', updatedList)
      
      // Update local state
      const mapped = {}
      updatedList.forEach(item => {
        mapped[item.id] = { name: item.name, color: item.color }
      })
      setClassLabels(mapped)
      setNewLabelName('')
    } catch (err) {
      alert("Failed to add label class: " + (err.response?.data?.detail || err.message))
    } finally {
      setIsLabelSubmitting(false)
    }
  }

  // Reset training categories to defaults
  const handleResetLabels = async () => {
    if (!window.confirm("Are you sure you want to reset categories to default? This will clear any custom categories you've added.")) return
    setIsLabelSubmitting(true)
    try {
      const defaults = [
        { id: 0, name: "car", color: "border-[#00d4ff] text-[#00d4ff] bg-[#00d4ff]/10" },
        { id: 1, name: "motorcycle", color: "border-[#7c3aed] text-[#7c3aed] bg-[#7c3aed]/10" },
        { id: 2, name: "bus", color: "border-[#10b981] text-[#10b981] bg-[#10b981]/10" },
        { id: 3, name: "truck", color: "border-[#f59e0b] text-[#f59e0b] bg-[#f59e0b]/10" },
        { id: 4, name: "bicycle", color: "border-[#f97316] text-[#f97316] bg-[#f97316]/10" }
      ]
      await trainingApi.post('/api/training/labels', defaults)
      const mapped = {}
      defaults.forEach(item => {
        mapped[item.id] = { name: item.name, color: item.color }
      })
      setClassLabels(mapped)
      setActiveClass(0)
    } catch (err) {
      alert("Failed to reset labels: " + err.message)
    } finally {
      setIsLabelSubmitting(false)
    }
  }




  // Labeler canvas mouse events
  const handleMouseDown = (e) => {
    if (!imageRef.current) return
    const rect = containerRef.current.getBoundingClientRect()
    const startX = e.clientX - rect.left
    const startY = e.clientY - rect.top
    setStartPos({ x: startX, y: startY })
    setDrawingBox({ x: startX, y: startY, w: 0, h: 0 })
    setIsDrawing(true)
  }

  const handleMouseMove = (e) => {
    if (!isDrawing || !startPos) return
    const rect = containerRef.current.getBoundingClientRect()
    const currentX = e.clientX - rect.left
    const currentY = e.clientY - rect.top
    
    const x = Math.min(startPos.x, currentX)
    const y = Math.min(startPos.y, currentY)
    const w = Math.abs(startPos.x - currentX)
    const h = Math.abs(startPos.y - currentY)
    
    setDrawingBox({ x, y, w, h })
  }

  const handleMouseUp = () => {
    if (!isDrawing || !drawingBox) return
    setIsDrawing(false)
    
    if (drawingBox.w > 6 && drawingBox.h > 6) {
      const newBoxIndex = drawnBoxes.length
      setDrawnBoxes(prev => [...prev, {
        class_id: activeClass,
        x: drawingBox.x,
        y: drawingBox.y,
        w: drawingBox.w,
        h: drawingBox.h
      }])
      setSelectedBoxIndex(newBoxIndex)
      setFloatingPopupPos({
        x: drawingBox.x + drawingBox.w,
        y: drawingBox.y
      })
    } else {
      // Clicked on empty space without dragging - deselect
      setSelectedBoxIndex(null)
      setFloatingPopupPos(null)
    }
    setDrawingBox(null)
    setStartPos(null)
  }

  const handleRemoveBox = (idx) => {
    setDrawnBoxes(prev => prev.filter((_, i) => i !== idx))
    setSelectedBoxIndex(null)
    setFloatingPopupPos(null)
  }


  const handleSaveLabels = async () => {
    if (!selectedImage || !imageRef.current) return
    const canvasW = imageRef.current.clientWidth
    const canvasH = imageRef.current.clientHeight
    
    // Normalize coordinates: class_id x_center y_center width height
    const normalized = drawnBoxes.map(box => {
      const x_center = (box.x + box.w / 2) / canvasW
      const y_center = (box.y + box.h / 2) / canvasH
      const width = box.w / canvasW
      const height = box.h / canvasH
      return {
        class_id: box.class_id,
        x_center: Math.max(0, Math.min(1, x_center)),
        y_center: Math.max(0, Math.min(1, y_center)),
        width: Math.max(0, Math.min(1, width)),
        height: Math.max(0, Math.min(1, height))
      }
    })

    try {
      await trainingApi.post(`/api/training/images/${selectedImage.filename}/label`, {
        boxes: normalized
      })
      alert("Annotations saved successfully!")
      refetchImages()
    } catch (err) {
      alert("Failed to save labels: " + (err.response?.data?.detail || err.message))
    }
  }

  // Training triggers
  const handleStartTraining = async () => {
    setIsTrainingSubmitting(true)
    try {
      await trainingApi.post('/api/training/train', {
        epochs: Number(epochs),
        batch_size: Number(batchSize)
      })
      alert("Model training initiated!")
      refetchStatus()
    } catch (err) {
      alert("Failed to start training: " + (err.response?.data?.detail || err.message))
    } finally {
      setIsTrainingSubmitting(false)
    }
  }

  const handleCancelTraining = async () => {
    if (!window.confirm("Are you sure you want to stop training? Progress will be lost.")) return
    try {
      await trainingApi.post('/api/training/cancel')
      refetchStatus()
    } catch (err) {
      alert("Failed to cancel training: " + err.message)
    }
  }

  // Computed helper details
  const labeledCount = imagesData?.filter(img => img.labeled).length || 0
  const isMinimumMet = labeledCount >= MIN_LABELED_IMAGES

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto page-mount">
      {/* Title */}
      <div>
        <h1 className="text-3xl font-black bg-gradient-to-r from-accent-cyan to-accent-purple bg-clip-text text-transparent flex items-center gap-3">
          <Cpu className="text-text-primary" />
          Model Training Studio
        </h1>
        <p className="text-text-secondary mt-1">Capture frames, label datasets, and fine-tune your custom object detection model</p>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-bg-border pb-px overflow-x-auto">
        <button
          onClick={() => setActiveTab('dataset')}
          className={`flex items-center gap-2 px-5 py-3 border-b-2 font-semibold text-sm transition-all whitespace-nowrap
            ${activeTab === 'dataset' 
              ? 'border-accent-cyan text-accent-cyan bg-accent-cyan/5' 
              : 'border-transparent text-text-muted hover:text-text-secondary'}`}
        >
          <Video size={16} />
          Capture & Dataset ({imagesData?.length || 0})
        </button>
        <button
          onClick={() => setActiveTab('labeler')}
          className={`flex items-center gap-2 px-5 py-3 border-b-2 font-semibold text-sm transition-all whitespace-nowrap
            ${activeTab === 'labeler' 
              ? 'border-accent-purple text-accent-purple bg-accent-purple/5' 
              : 'border-transparent text-text-muted hover:text-text-secondary'}`}
        >
          <Tag size={16} />
          Labeling Canvas
        </button>
        <button
          onClick={() => setActiveTab('training')}
          className={`flex items-center gap-2 px-5 py-3 border-b-2 font-semibold text-sm transition-all whitespace-nowrap
            ${activeTab === 'training' 
              ? 'border-accent-amber text-accent-amber bg-accent-amber/5' 
              : 'border-transparent text-text-muted hover:text-text-secondary'}`}
        >
          <Cpu size={16} />
          Model Training
        </button>
      </div>

      {/* Content */}
      <div className="grid grid-cols-1 gap-6">
        
        {/* Tab 1: Dataset */}
        {activeTab === 'dataset' && (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            {/* Capture Panel */}
            <div className="lg:col-span-4 bg-bg-card rounded-xl border border-bg-border p-5 shadow-card space-y-4 h-fit">
              <h2 className="text-text-primary font-semibold flex items-center gap-2">
                <Camera size={18} className="text-accent-cyan" />
                Live Frame Capture
              </h2>
              <p className="text-xs text-text-secondary leading-relaxed">
                Select a camera and enable auto-capture to automatically collect frames for training.
              </p>
              <div className="space-y-3">
                <select 
                  value={selectedCamera} 
                  onChange={(e) => setSelectedCamera(e.target.value)}
                  className="bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary w-full focus:outline-none focus:border-accent-cyan"
                >
                  <option value="">Select Camera...</option>
                  {cameras.map(cam => (
                    <option key={cam.id} value={cam.id}>{cam.name} (ID: {cam.id})</option>
                  ))}
                </select>

                {/* Auto-capture toggle */}
                <div className="bg-bg rounded-lg border border-bg-border p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-text-secondary">Auto-Capture</span>
                    <button
                      onClick={() => setAutoCaptureEnabled(v => !v)}
                      disabled={!selectedCamera}
                      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors disabled:opacity-40
                        ${autoCaptureEnabled ? 'bg-accent-cyan' : 'bg-bg-border'}`}
                    >
                      <span className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform
                        ${autoCaptureEnabled ? 'translate-x-4.5' : 'translate-x-0.5'}`} />
                    </button>
                  </div>
                  {autoCaptureEnabled && (
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-text-muted">Every</span>
                      <select
                        value={autoCaptureInterval}
                        onChange={e => setAutoCaptureInterval(Number(e.target.value))}
                        className="flex-1 bg-bg-card border border-bg-border rounded px-2 py-1 text-xs text-text-primary focus:outline-none"
                      >
                        <option value={5}>5s</option>
                        <option value={10}>10s</option>
                        <option value={15}>15s</option>
                        <option value={30}>30s</option>
                        <option value={60}>60s</option>
                      </select>
                    </div>
                  )}
                  {autoCaptureStatus && (
                    <p className={`text-[10px] font-mono truncate
                      ${autoCaptureStatus.startsWith('✓') ? 'text-accent-green' : 'text-accent-amber'}`}>
                      {autoCaptureStatus}
                    </p>
                  )}
                  {!autoCaptureEnabled && (
                    <p className="text-[10px] text-text-muted">Enable to capture frames automatically.</p>
                  )}
                </div>

                {/* Manual capture button */}
                <button
                  onClick={handleCapture}
                  disabled={!selectedCamera}
                  className="w-full bg-accent-cyan/10 hover:bg-accent-cyan/20 border border-accent-cyan/20 text-accent-cyan px-4 py-2.5 rounded-lg text-sm font-semibold flex items-center justify-center gap-2 transition-colors disabled:opacity-50"
                >
                  <Camera size={16} />
                  Capture Frame Now
                </button>
              </div>
            </div>


            {/* Gallery Panel */}
            <div className="lg:col-span-8 bg-bg-card rounded-xl border border-bg-border p-5 shadow-card space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-text-primary font-semibold flex items-center gap-2">
                  <Layers size={18} className="text-accent-purple" />
                  Dataset Gallery
                  {autoCaptureEnabled && (
                    <span className="flex items-center gap-1 text-[10px] text-accent-cyan bg-accent-cyan/10 border border-accent-cyan/20 px-2 py-0.5 rounded-full animate-pulse">
                      <span className="w-1.5 h-1.5 rounded-full bg-accent-cyan"></span>
                      Auto-capturing every {autoCaptureInterval}s
                    </span>
                  )}
                </h2>
                <div className="flex items-center gap-2">
                  {imagesData?.length > 0 && (
                    <button 
                      onClick={handleDeleteAllImages}
                      className="text-accent-red/60 hover:text-accent-red text-xs flex items-center gap-1 transition-colors"
                      title="Delete all images"
                    >
                      <Trash2 size={13} />
                      Clear All
                    </button>
                  )}
                  <button onClick={refetchImages} className="text-text-muted hover:text-text-secondary">
                    <RefreshCw size={16} />
                  </button>
                </div>
              </div>

              {!imagesData?.length ? (
                <div className="text-center py-12 text-text-muted text-sm border border-bg-border border-dashed rounded-lg bg-bg/20">
                  No images captured yet. Enable auto-capture on the left or manually capture a frame.
                </div>
              ) : (
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4 overflow-y-auto max-h-[500px] pr-1">
                  {imagesData.map(img => (
                    <div 
                      key={img.filename}
                      onClick={() => {
                        setSelectedImage(img)
                        setActiveTab('labeler')
                      }}
                      className={`group bg-bg border rounded-lg overflow-hidden cursor-pointer transition-all hover:scale-[1.02] hover:border-accent-purple/50 relative
                        ${selectedImage?.filename === img.filename ? 'border-2 border-accent-purple' : 'border-bg-border'}`}
                    >
                      <div className="aspect-video w-full bg-black relative">
                        <img 
                          src={`${TRAINING_BASE}/api/training/images/${img.filename}`} 
                          alt={img.filename}
                          className="w-full h-full object-cover"
                        />
                        <span className={`absolute top-2 right-2 text-[9px] font-bold px-2 py-0.5 rounded-full shadow-sm
                          ${img.labeled ? 'bg-accent-green/20 text-accent-green border border-accent-green/30' : 'bg-accent-amber/20 text-accent-amber border border-accent-amber/30'}`}
                        >
                          {img.labeled ? 'Labeled' : 'Unlabeled'}
                        </span>
                        {/* Delete button on hover */}
                        <button
                          onClick={(e) => handleDeleteImage(img.filename, e)}
                          className="absolute top-2 left-2 opacity-0 group-hover:opacity-100 w-5 h-5 bg-black/60 text-accent-red rounded flex items-center justify-center transition-opacity"
                          title="Delete image"
                        >
                          <Trash2 size={10} />
                        </button>
                      </div>
                      <div className="p-2">
                        <p className="text-[10px] text-text-secondary font-mono truncate">{img.filename}</p>
                        <p className="text-[9px] text-text-muted mt-0.5">
                          {new Intl.DateTimeFormat(undefined, { timeStyle: 'short', dateStyle: 'short' }).format(new Date(img.timestamp * 1000))}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}


        {/* Tab 2: Labeler */}
        {activeTab === 'labeler' && (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            {!selectedImage ? (
              <div className="col-span-12 text-center py-20 bg-bg-card rounded-xl border border-bg-border border-dashed text-text-muted text-sm">
                No image selected. Go to <span className="text-accent-cyan cursor-pointer underline" onClick={() => setActiveTab('dataset')}>Dataset tab</span> and select an image to label.
              </div>
            ) : (
              <>
                {/* Canvas Container */}
                <div className="lg:col-span-8 bg-bg-card rounded-xl border border-bg-border p-5 shadow-card flex flex-col items-center">
                  <div className="w-full flex items-center justify-between mb-4">
                    <div>
                      <h3 className="text-sm font-semibold text-text-primary font-mono">{selectedImage.filename}</h3>
                      <p className="text-[10px] text-text-muted">Click and drag to draw a bounding box around objects</p>
                    </div>
                    <button 
                      onClick={handleSaveLabels}
                      className="bg-accent-green/10 hover:bg-accent-green/20 border border-accent-green/20 text-accent-green px-4 py-1.5 rounded-lg text-sm font-semibold flex items-center gap-1.5 transition-colors"
                    >
                      <Check size={16} />
                      Save Labels
                    </button>
                  </div>

                  {/* Relative wrapping canvas container */}
                  <div className="w-full flex items-center justify-between px-1 mb-2 text-[10px] text-text-muted select-none">

                    <span>Keyboard shortcuts: <strong className="text-accent-cyan">[A / D]</strong> Previous/Next image &bull; <strong className="text-accent-cyan">[1-9]</strong> Set box class &bull; <strong className="text-accent-cyan">[Arrows]</strong> Nudge box &bull; <strong className="text-accent-cyan">[Del / Backspace]</strong> Delete box</span>
                  </div>
                  
                  <div 
                    ref={containerRef}
                    onMouseDown={handleMouseDown}
                    onMouseMove={handleMouseMove}
                    onMouseUp={handleMouseUp}
                    className="relative max-w-full bg-black rounded-lg overflow-hidden border border-bg-border cursor-crosshair select-none"
                  >
                    <img 
                      ref={imageRef}
                      src={`${TRAINING_BASE}/api/training/images/${selectedImage.filename}`}
                      alt="labeling canvas"
                      className="max-h-[500px] w-auto object-contain block pointer-events-none select-none"
                      draggable={false}
                      onLoad={() => mapNormalizedToPixels(rawBoxesRef.current)}
                    />

                    {/* Render already drawn boxes */}
                    {drawnBoxes.map((box, idx) => {
                      const label = classLabels[box.class_id]
                      const isSelected = selectedBoxIndex === idx
                      const colorHex = label ? label.color.split(' ')[0].replace('border-[', '').replace(']', '') : '#fff'
                      return (
                        <div 
                          key={idx}
                          className={`absolute border-2 pointer-events-none group transition-all
                            ${isSelected 
                              ? 'border-dashed ring-2 ring-accent-cyan ring-offset-1 ring-offset-black z-20 scale-[1.01]' 
                              : 'hover:border-white z-10'}`}
                          style={{
                            left: `${box.x}px`,
                            top: `${box.y}px`,
                            width: `${box.w}px`,
                            height: `${box.h}px`,
                            borderColor: colorHex
                          }}
                        >
                          <span 
                            className="absolute -top-5 left-0 text-[10px] font-bold text-white px-1.5 py-0.5 rounded shadow-md pointer-events-auto cursor-pointer flex items-center gap-1 select-none"
                            style={{ backgroundColor: colorHex }}
                            onMouseDown={(e) => {
                              e.stopPropagation()
                              setSelectedBoxIndex(idx)
                              setFloatingPopupPos({
                                x: box.x + box.w,
                                y: box.y
                              })
                            }}
                          >
                            {label?.name}
                            <button 
                              onClick={(e) => {
                                e.stopPropagation()
                                handleRemoveBox(idx)
                              }}
                              className="hover:text-red-300 ml-1 font-bold text-xs"
                            >
                              ×
                            </button>
                          </span>
                        </div>
                      )
                    })}


                    {/* Render active drawing box */}
                    {isDrawing && drawingBox && (
                      <div 
                        className="absolute border-2 border-dashed border-white pointer-events-none"
                        style={{
                          left: `${drawingBox.x}px`,
                          top: `${drawingBox.y}px`,
                          width: `${drawingBox.w}px`,
                          height: `${drawingBox.h}px`
                        }}
                      />
                    )}

                    {/* Floating Class Selector Popup */}
                    {floatingPopupPos && selectedBoxIndex !== null && (
                      <div 
                        className="absolute z-30 bg-bg-card border border-bg-border rounded-lg shadow-xl p-2 space-y-1 text-xs"
                        style={{
                          left: `${Math.min(containerRef.current ? containerRef.current.clientWidth - 150 : 300, Math.max(10, floatingPopupPos.x))}px`,
                          top: `${Math.min(containerRef.current ? containerRef.current.clientHeight - 200 : 300, Math.max(10, floatingPopupPos.y))}px`,
                          width: '140px'
                        }}
                        onMouseDown={(e) => e.stopPropagation()} // prevent starting a new draw
                      >
                        <div className="font-semibold text-text-muted pb-1 border-b border-bg-border flex justify-between items-center mb-1 select-none">
                          <span>Set Class</span>
                          <button 
                            onClick={() => {
                              setFloatingPopupPos(null)
                              setSelectedBoxIndex(null)
                            }}
                            className="text-text-muted hover:text-text-secondary font-bold text-xs"
                          >
                            ×
                          </button>
                        </div>
                        <div className="max-h-[120px] overflow-y-auto space-y-1">
                          {Object.entries(classLabels).map(([id, item]) => {
                            const cid = Number(id)
                            return (
                              <button
                                key={cid}
                                onClick={() => {
                                  setDrawnBoxes(prev => prev.map((box, idx) => idx === selectedBoxIndex ? { ...box, class_id: cid } : box))
                                  setFloatingPopupPos(null)
                                }}
                                className={`w-full flex items-center gap-2 p-1.5 rounded text-left transition-colors font-medium text-[11px]
                                  ${drawnBoxes[selectedBoxIndex]?.class_id === cid
                                    ? 'bg-accent-cyan/10 text-accent-cyan' 
                                    : 'text-text-secondary hover:bg-bg-hover'}`}
                              >
                                <span className={`w-2 h-2 rounded-sm ${item.color.split(' ')[0].replace('border-', 'bg-')}`} />
                                {item.name}
                              </button>
                            )
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                </div>


                {/* Class selector & sidebar */}
                <div className="lg:col-span-4 bg-bg-card rounded-xl border border-bg-border p-5 shadow-card space-y-6">
                  <div className="space-y-3">
                    <h3 className="text-text-primary font-semibold text-sm flex items-center gap-2">
                      <Tag size={16} className="text-accent-cyan" />
                      Select Label Category
                    </h3>
                    <div className="grid grid-cols-1 gap-2">
                      {Object.entries(classLabels).map(([id, item]) => {
                        const classId = Number(id)
                        const isActive = activeClass === classId
                        return (
                          <button
                            key={classId}
                            onClick={() => setActiveClass(classId)}
                            className={`w-full flex items-center gap-3 p-3 rounded-lg border text-left transition-all font-medium text-xs
                              ${isActive 
                                ? `${item.color} border-2` 
                                : 'border-bg-border text-text-secondary hover:bg-bg-hover/30'}`}
                          >
                            <span className={`w-2.5 h-2.5 rounded-sm ${item.color.split(' ')[0].replace('border-', 'bg-')}`} />
                            {item.name}
                          </button>
                        )
                      })}
                    </div>

                    {/* Add Category Form */}
                    <form onSubmit={handleAddLabelClass} className="flex gap-2 pt-3 border-t border-bg-border">
                      <input
                        type="text"
                        required
                        value={newLabelName}
                        onChange={e => setNewLabelName(e.target.value)}
                        placeholder="New category..."
                        className="flex-1 bg-bg border border-bg-border rounded-lg px-2.5 py-1.5 text-xs text-text-primary focus:outline-none focus:border-accent-cyan"
                      />
                      <button
                        type="submit"
                        disabled={isLabelSubmitting}
                        className="bg-accent-cyan/10 hover:bg-accent-cyan/20 border border-accent-cyan/20 text-accent-cyan px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1 transition-colors disabled:opacity-50"
                      >
                        <Plus size={12} />
                        Add
                      </button>
                    </form>

                    {/* Reset Button */}
                    <button
                      onClick={handleResetLabels}
                      disabled={isLabelSubmitting}
                      className="text-text-muted hover:text-text-secondary text-[10px] underline block text-left"
                    >
                      Reset to defaults
                    </button>
                  </div>


                  <div className="border-t border-bg-border pt-4 space-y-3">
                    <h3 className="text-text-primary font-semibold text-sm flex items-center gap-2">
                      <Layers size={16} className="text-accent-purple" />
                      Active Annotations ({drawnBoxes.length})
                    </h3>
                    {!drawnBoxes.length ? (
                      <p className="text-xs text-text-muted italic">No bounding boxes drawn on this image.</p>
                    ) : (
                      <div className="space-y-2 max-h-[180px] overflow-y-auto pr-1">
                        {drawnBoxes.map((box, idx) => {
                          const lbl = classLabels[box.class_id]
                          return (

                            <div key={idx} className="flex items-center justify-between p-2 bg-bg border border-bg-border rounded text-xs">
                              <div className="flex items-center gap-2">
                                <span className={`w-2 h-2 rounded-sm ${lbl?.color.split(' ')[0].replace('border-', 'bg-')}`} />
                                <span className="font-semibold text-text-secondary">{lbl?.name}</span>
                              </div>
                              <button onClick={() => handleRemoveBox(idx)} className="text-text-muted hover:text-accent-red transition-colors">
                                <Trash2 size={14} />
                              </button>
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* Tab 3: Model Training */}
        {activeTab === 'training' && (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            
            {/* Parameters card */}
            <div className="lg:col-span-5 bg-bg-card rounded-xl border border-bg-border p-5 shadow-card space-y-6 h-fit">
              <div>
                <h2 className="text-text-primary font-semibold flex items-center gap-2">
                  <Cpu size={18} className="text-accent-amber" />
                  Train Parameters
                </h2>
                <p className="text-xs text-text-secondary mt-1">Configure epochs and start fine-tuning the model.</p>
              </div>

              {/* Status info bar */}
              <div className="bg-bg/40 border border-bg-border rounded-xl p-4 space-y-2.5">
                <div className="flex justify-between items-center text-xs">
                  <span className="text-text-muted font-medium">Labeled Dataset Count:</span>
                  <span className={`font-mono font-bold ${isMinimumMet ? 'text-accent-green' : 'text-accent-amber'}`}>
                    {labeledCount} / {MIN_LABELED_IMAGES}
                  </span>
                </div>
                {!isMinimumMet && (
                  <div className="bg-accent-amber/5 border border-accent-amber/20 rounded p-2.5 flex gap-2 items-start text-[11px] text-accent-amber leading-relaxed">
                    <Info size={14} className="flex-shrink-0 mt-0.5" />
                    <span>
                      You must label at least **{MIN_LABELED_IMAGES} images** before initiating model training. Currently missing **{MIN_LABELED_IMAGES - labeledCount}** labels.
                    </span>
                  </div>
                )}
              </div>

              <div className="space-y-4">
                <div className="space-y-1.5">
                  <div className="flex justify-between text-xs font-semibold uppercase text-text-muted">
                    <span>Training Epochs</span>
                    <span className="text-accent-amber">{epochs}</span>
                  </div>
                  <input 
                    type="range" 
                    min="5" 
                    max="100" 
                    value={epochs} 
                    onChange={(e) => setEpochs(e.target.value)}
                    disabled={trainingStatus?.status === 'training'}
                    className="w-full h-1.5 bg-bg rounded-lg appearance-none cursor-pointer accent-accent-amber"
                  />
                </div>

                <div className="space-y-1.5">
                  <label className="text-xs font-semibold uppercase text-text-muted block">Batch Size</label>
                  <select
                    value={batchSize}
                    onChange={(e) => setBatchSize(e.target.value)}
                    disabled={trainingStatus?.status === 'training'}
                    className="bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary w-full focus:outline-none focus:border-accent-amber"
                  >
                    <option value="4">4</option>
                    <option value="8">8</option>
                    <option value="16">16</option>
                    <option value="32">32</option>
                  </select>
                </div>

                {trainingStatus?.status !== 'training' ? (
                  <button
                    onClick={handleStartTraining}
                    disabled={!isMinimumMet || isTrainingSubmitting}
                    className="w-full bg-accent-amber/10 hover:bg-accent-amber/20 border border-accent-amber/20 text-accent-amber px-4 py-2.5 rounded-lg text-sm font-semibold flex items-center justify-center gap-2 transition-colors disabled:opacity-50"
                  >
                    <Play size={16} />
                    Start Fine-Tuning
                  </button>
                ) : (
                  <button
                    onClick={handleCancelTraining}
                    className="w-full bg-accent-red/10 hover:bg-accent-red/20 border border-accent-red/20 text-accent-red px-4 py-2.5 rounded-lg text-sm font-semibold flex items-center justify-center gap-2 transition-colors"
                  >
                    <Square size={16} />
                    Cancel Training
                  </button>
                )}
              </div>
            </div>

            {/* Logging panel */}
            <div className="lg:col-span-7 bg-bg-card rounded-xl border border-bg-border p-5 shadow-card flex flex-col space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-text-primary font-semibold flex items-center gap-2">
                  <Cpu size={18} className="text-accent-cyan animate-pulse" />
                  Live Training Console
                </h2>
                {trainingStatus?.status === 'training' && (
                  <span className="flex items-center gap-1.5 text-xs text-accent-amber font-semibold">
                    <span className="w-2 h-2 rounded-full bg-accent-amber animate-ping" />
                    Epoch {trainingStatus.current_epoch} / {trainingStatus.total_epochs}
                  </span>
                )}
              </div>

              {/* Status Banner */}
              {trainingStatus && (
                <div className={`p-4 rounded-xl border text-xs font-semibold flex items-center justify-between
                  ${trainingStatus.status === 'training' ? 'bg-accent-amber/5 border-accent-amber/20 text-accent-amber' :
                    trainingStatus.status === 'complete' ? 'bg-accent-green/5 border-accent-green/20 text-accent-green' :
                    trainingStatus.status === 'failed' ? 'bg-accent-red/5 border-accent-red/20 text-accent-red' :
                    'bg-bg/40 border-bg-border text-text-muted'}`}
                >
                  <span className="capitalize">Status: {trainingStatus.status}</span>
                  {trainingStatus.status === 'complete' && trainingStatus.new_model_name && (
                    <span className="font-mono">Weights file: {trainingStatus.new_model_name}</span>
                  )}
                </div>
              )}

              {/* Scrollable logs terminal */}
              <div className="flex-1 bg-black text-[#00ff66] font-mono text-[11px] rounded-lg p-4 min-h-[300px] max-h-[420px] overflow-y-auto border border-bg-border flex flex-col space-y-1">
                {!trainingStatus?.logs?.length ? (
                  <span className="text-gray-500 italic">Terminal idle. Start training to view live training logs...</span>
                ) : (
                  trainingStatus.logs.map((log, idx) => (
                    <span key={idx} className="block leading-relaxed">{log}</span>
                  ))
                )}
              </div>
            </div>

          </div>
        )}

      </div>
    </div>
  )
}
