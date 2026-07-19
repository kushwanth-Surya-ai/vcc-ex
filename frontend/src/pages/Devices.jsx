import { useState, useRef } from 'react'
import { Monitor, Database, Plus, Camera, Trash2, Check } from 'lucide-react'
import { useApi } from '../hooks/useApi'
import api, { trainingApi } from '../lib/api'
import CountingLineEditor from '../components/CountingLineEditor'




const STREAM_BASE = import.meta.env.VITE_STREAM_BASE_URL || `${window.location.protocol}//${window.location.hostname}:8001`
const mainApiUrl = import.meta.env.VITE_API_URL || `${window.location.protocol}//${window.location.hostname}:8000`
const TRAINING_BASE = import.meta.env.VITE_TRAINING_API_URL || mainApiUrl.replace(/(:\d+)?\/?$/, ':8002')


export default function Devices() {
  const { data: camerasResp, loading, refetch } = useApi('/api/cameras')
  const cameras = camerasResp?.items ?? []
  
  const [isAddCameraModalOpen, setIsAddCameraModalOpen] = useState(false)
  const [newCamera, setNewCamera] = useState({
    name: '',
    rtsp_url: '',
    rtsp_username: '',
    rtsp_password: '',
    latitude: '',
    longitude: ''
  })
 
  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [editingCamera, setEditingCamera] = useState(null)
 
  // Configure Counting Line states
  const [isConfigureLineModalOpen, setIsConfigureLineModalOpen] = useState(false)
  const [lineCamera, setLineCamera] = useState(null)

  const handleConfigureLineClick = (cam) => {
    setLineCamera(cam)
    setIsConfigureLineModalOpen(true)
  }

 
  const handleRemoveCamera = async (id) => {
    if (!window.confirm('Are you sure you want to remove this camera?')) return
    try {
      await api.delete(`/api/cameras/${id}`)
      refetch()
    } catch (err) {
      alert("Failed to remove camera: " + (err.response?.data?.detail?.[0]?.msg || err.message))
    }
  }
 
  const handleEditClick = (cam) => {
    let username = ''
    let password = ''
    let baseUrl = cam.rtsp_url || ''
    if (baseUrl.startsWith('rtsp://')) {
      try {
        const u = new URL(baseUrl)
        username = decodeURIComponent(u.username)
        password = decodeURIComponent(u.password)
        u.username = ''
        u.password = ''
        baseUrl = u.toString()
      } catch(e) {}
    }
    setEditingCamera({
      id: cam.id,
      name: cam.name,
      location_id: cam.location_id,
      lane_count: cam.lane_count,
      rtsp_url: baseUrl,
      rtsp_username: username,
      rtsp_password: password,
      latitude: cam.latitude !== null && cam.latitude !== undefined ? String(cam.latitude) : '',
      longitude: cam.longitude !== null && cam.longitude !== undefined ? String(cam.longitude) : ''
    })
    setIsEditModalOpen(true)
  }
 
  const handleEditSubmit = async (e) => {
    e.preventDefault()
    let finalRtspUrl = editingCamera.rtsp_url.trim()
    if (finalRtspUrl && editingCamera.rtsp_username && finalRtspUrl.startsWith('rtsp://')) {
       const auth = encodeURIComponent(editingCamera.rtsp_username) + (editingCamera.rtsp_password ? ':' + encodeURIComponent(editingCamera.rtsp_password) : '')
       finalRtspUrl = finalRtspUrl.replace('rtsp://', `rtsp://${auth}@`)
    }
    try {
      await api.patch(`/api/cameras/${editingCamera.id}`, {
        name: editingCamera.name,
        location_id: editingCamera.location_id || 1,
        lane_count: editingCamera.lane_count || 1,
        rtsp_url: finalRtspUrl || null,
        latitude: editingCamera.latitude ? parseFloat(editingCamera.latitude) : null,
        longitude: editingCamera.longitude ? parseFloat(editingCamera.longitude) : null
      })
      setIsEditModalOpen(false)
      setEditingCamera(null)
      refetch()
    } catch (err) {
      alert("Failed to update camera: " + (err.response?.data?.detail?.[0]?.msg || err.message))
    }
  }
 
  const handleAddCamera = async (e) => {
    e.preventDefault()
    let finalRtspUrl = newCamera.rtsp_url.trim()
    if (finalRtspUrl && newCamera.rtsp_username && finalRtspUrl.startsWith('rtsp://')) {
       const auth = encodeURIComponent(newCamera.rtsp_username) + (newCamera.rtsp_password ? ':' + encodeURIComponent(newCamera.rtsp_password) : '')
       finalRtspUrl = finalRtspUrl.replace('rtsp://', `rtsp://${auth}@`)
    }
    try {
      await api.post('/api/cameras', {
        name: newCamera.name,
        location_id: 1, // default location
        lane_count: 1, // default lane count
        rtsp_url: finalRtspUrl || null,
        latitude: newCamera.latitude ? parseFloat(newCamera.latitude) : null,
        longitude: newCamera.longitude ? parseFloat(newCamera.longitude) : null
      })
      setIsAddCameraModalOpen(false)
      setNewCamera({ name: '', rtsp_url: '', rtsp_username: '', rtsp_password: '', latitude: '', longitude: '' })
      refetch()
    } catch (err) {
      alert("Failed to add camera: " + (err.response?.data?.detail?.[0]?.msg || err.message))
    }
  }

  return (
    <div className="p-6 space-y-8 max-w-6xl mx-auto page-mount">
      <div>
        <h1 className="text-3xl font-black bg-gradient-to-r from-text-primary to-text-muted bg-clip-text text-transparent flex items-center gap-3">
          <Monitor className="text-text-primary" />
          Devices
        </h1>
        <p className="text-text-secondary mt-1">Manage connected cameras and edge devices</p>
      </div>

      <div className="grid grid-cols-1 gap-6">
        {/* Cameras */}
        <div className="col-span-1">
          <div className="bg-bg-card rounded-xl border border-bg-border shadow-card overflow-hidden">
            <div className="p-5 border-b border-bg-border flex justify-between items-center bg-bg/50">
              <h2 className="text-text-primary font-semibold flex items-center gap-2">
                <Database size={18} className="text-accent-blue" />
                Configured Cameras
              </h2>
              <button onClick={() => setIsAddCameraModalOpen(true)} className="flex items-center gap-1 bg-accent-cyan/10 hover:bg-accent-cyan/20 text-accent-cyan border border-accent-cyan/20 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors">
                <Plus size={16} />
                Add Camera
              </button>
            </div>
            
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-bg-card text-xs uppercase text-text-muted border-b border-bg-border">
                  <tr>
                    <th className="px-6 py-3 font-semibold">Name / ID</th>
                    <th className="px-6 py-3 font-semibold">Coordinates</th>
                    <th className="px-6 py-3 font-semibold">Status</th>
                    <th className="px-6 py-3 font-semibold text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-bg-border">
                  {loading ? (
                    <tr><td colSpan="4" className="px-6 py-4 text-center text-text-muted">Loading...</td></tr>
                  ) : cameras && cameras.length > 0 ? (
                    cameras.map(cam => (
                      <tr key={cam.id} className="hover:bg-bg transition-colors">
                        <td className="px-6 py-4 font-medium text-text-primary">
                          {cam.name} <span className="text-text-muted text-xs block font-normal">#{cam.id}</span>
                        </td>
                        <td className="px-6 py-4 text-xs font-mono text-text-secondary">
                          {cam.latitude !== null && cam.latitude !== undefined && cam.longitude !== null && cam.longitude !== undefined 
                            ? `${Number(cam.latitude).toFixed(6)}, ${Number(cam.longitude).toFixed(6)}` 
                            : '—'}
                        </td>
                        <td className="px-6 py-4">
                          <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold
                            ${cam.status === 'active' ? 'bg-accent-green/10 text-accent-green' : 'bg-text-muted/10 text-text-muted'}`}>
                            <span className={`w-1.5 h-1.5 rounded-full ${cam.status === 'active' ? 'bg-accent-green animate-pulse' : 'bg-text-muted'}`}></span>
                            {cam.status}
                          </span>
                        </td>
                        <td className="px-6 py-4 text-right">
                          <button onClick={() => handleConfigureLineClick(cam)} className="text-accent-purple hover:text-accent-purple/80 text-xs font-semibold mr-3">Edit Line</button>
                          <button onClick={() => handleEditClick(cam)} className="text-accent-cyan hover:text-accent-cyan/80 text-xs font-semibold mr-3">Edit</button>
                          <button onClick={() => handleRemoveCamera(cam.id)} className="text-accent-red hover:text-accent-red/80 text-xs font-semibold">Remove</button>
                        </td>

                      </tr>
                    ))
                  ) : (
                    <tr><td colSpan="4" className="px-6 py-8 text-center text-text-muted bg-bg/20">No cameras configured</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

      {/* Add Camera Modal */}
      {isAddCameraModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-bg-card border border-bg-border shadow-card rounded-xl w-full max-w-md p-6">
            <h2 className="text-xl font-bold text-text-primary mb-4">Add New Camera</h2>
            <form onSubmit={handleAddCamera} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-text-muted uppercase mb-1">Camera Name</label>
                <input required type="text" value={newCamera.name} onChange={e => setNewCamera({...newCamera, name: e.target.value})} className="w-full bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary focus:border-accent-cyan" placeholder="e.g. Main Gate Camera" />
              </div>
              <div>
                <label className="block text-xs font-semibold text-text-muted uppercase mb-1">RTSP Stream URL</label>
                <input required type="text" value={newCamera.rtsp_url} onChange={e => setNewCamera({...newCamera, rtsp_url: e.target.value})} className="w-full bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary focus:border-accent-cyan" placeholder="rtsp://..." />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-semibold text-text-muted uppercase mb-1">RTSP Username</label>
                  <input type="text" value={newCamera.rtsp_username} onChange={e => setNewCamera({...newCamera, rtsp_username: e.target.value})} className="w-full bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary focus:border-accent-cyan" placeholder="Username" />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-text-muted uppercase mb-1">RTSP Password</label>
                  <input type="password" value={newCamera.rtsp_password} onChange={e => setNewCamera({...newCamera, rtsp_password: e.target.value})} className="w-full bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary focus:border-accent-cyan" placeholder="Password" />
                </div>
              </div>
              
              {/* Latitude and Longitude Fields */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-semibold text-text-muted uppercase mb-1">Latitude</label>
                  <input type="number" step="any" value={newCamera.latitude} onChange={e => setNewCamera({...newCamera, latitude: e.target.value})} className="w-full bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary focus:border-accent-cyan" placeholder="e.g. 12.9716" />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-text-muted uppercase mb-1">Longitude</label>
                  <input type="number" step="any" value={newCamera.longitude} onChange={e => setNewCamera({...newCamera, longitude: e.target.value})} className="w-full bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary focus:border-accent-cyan" placeholder="e.g. 77.5946" />
                </div>
              </div>
              
              <div className="flex justify-end gap-3 mt-6">
                <button type="button" onClick={() => setIsAddCameraModalOpen(false)} className="px-4 py-2 text-sm font-medium text-text-secondary hover:text-text-primary">Cancel</button>
                <button type="submit" className="bg-accent-cyan hover:bg-accent-cyan/90 text-white px-4 py-2 rounded-lg text-sm font-medium">Save Camera</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Edit Camera Modal */}
      {isEditModalOpen && editingCamera && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-bg-card border border-bg-border shadow-card rounded-xl w-full max-w-md p-6">
            <h2 className="text-xl font-bold text-text-primary mb-4">Edit Camera</h2>
            <form onSubmit={handleEditSubmit} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-text-muted uppercase mb-1">Camera Name</label>
                <input required type="text" value={editingCamera.name} onChange={e => setEditingCamera({...editingCamera, name: e.target.value})} className="w-full bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary focus:border-accent-cyan" placeholder="e.g. Main Gate Camera" />
              </div>
              <div>
                <label className="block text-xs font-semibold text-text-muted uppercase mb-1">RTSP Stream URL</label>
                <input required type="text" value={editingCamera.rtsp_url} onChange={e => setEditingCamera({...editingCamera, rtsp_url: e.target.value})} className="w-full bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary focus:border-accent-cyan" placeholder="rtsp://..." />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-semibold text-text-muted uppercase mb-1">RTSP Username</label>
                  <input type="text" value={editingCamera.rtsp_username} onChange={e => setEditingCamera({...editingCamera, rtsp_username: e.target.value})} className="w-full bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary focus:border-accent-cyan" placeholder="Username" />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-text-muted uppercase mb-1">RTSP Password</label>
                  <input type="password" value={editingCamera.rtsp_password} onChange={e => setEditingCamera({...editingCamera, rtsp_password: e.target.value})} className="w-full bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary focus:border-accent-cyan" placeholder="Password" />
                </div>
              </div>
              
              {/* Latitude and Longitude Fields */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-semibold text-text-muted uppercase mb-1">Latitude</label>
                  <input type="number" step="any" value={editingCamera.latitude} onChange={e => setEditingCamera({...editingCamera, latitude: e.target.value})} className="w-full bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary focus:border-accent-cyan" placeholder="e.g. 12.9716" />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-text-muted uppercase mb-1">Longitude</label>
                  <input type="number" step="any" value={editingCamera.longitude} onChange={e => setEditingCamera({...editingCamera, longitude: e.target.value})} className="w-full bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary focus:border-accent-cyan" placeholder="e.g. 77.5946" />
                </div>
              </div>
              
              <div className="flex justify-end gap-3 mt-6">
                <button type="button" onClick={() => setIsEditModalOpen(false)} className="px-4 py-2 text-sm font-medium text-text-secondary hover:text-text-primary">Cancel</button>
                <button type="submit" className="bg-accent-cyan hover:bg-accent-cyan/90 text-white px-4 py-2 rounded-lg text-sm font-medium">Save Changes</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Configure Counting Line Modal */}
      {isConfigureLineModalOpen && lineCamera && (
        <CountingLineEditor
          camera={lineCamera}
          onClose={() => setIsConfigureLineModalOpen(false)}
          onSaved={refetch}
        />
      )}
    </div>
  )
}

