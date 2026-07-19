import { useEffect, useRef } from 'react'
import { useApi } from '../hooks/useApi'

export default function TopLocationsChart() {
  const mapContainerRef = useRef(null)
  const mapRef = useRef(null)
  const markersRef = useRef([])

  // Fetch cameras which now include latitude, longitude and event counts
  const { data: camerasResp, loading: camerasLoading } = useApi('/api/cameras')
  const cameras = camerasResp?.items ?? []

  useEffect(() => {
    if (!mapContainerRef.current) return
    if (mapRef.current) return // Already initialized

    // Ensure Leaflet is globally available
    if (typeof window.L === 'undefined') return

    // Center of Bangalore coordinates: 12.9716, 77.5946
    mapRef.current = window.L.map(mapContainerRef.current, {
      center: [12.9716, 77.5946],
      zoom: 11,
      zoomControl: true,
      attributionControl: false
    })

    // Use CartoDB Dark Matter tiles to match VCC premium dark theme aesthetics
    window.L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      maxZoom: 20
    }).addTo(mapRef.current)

    return () => {
      if (mapRef.current) {
        mapRef.current.remove()
        mapRef.current = null
      }
    }
  }, [])

  useEffect(() => {
    if (!mapRef.current || typeof window.L === 'undefined') return

    // Clear existing markers
    markersRef.current.forEach(m => m.remove())
    markersRef.current = []

    // Filter cameras that have valid numeric coordinates
    const validCameras = cameras.filter(cam => 
      cam.latitude !== null && 
      cam.latitude !== undefined && 
      cam.longitude !== null && 
      cam.longitude !== undefined &&
      !isNaN(Number(cam.latitude)) &&
      !isNaN(Number(cam.longitude))
    )

    if (validCameras.length > 0) {
      const bounds = []
      validCameras.forEach(cam => {
        // Create custom glowing circle marker for each camera
        const marker = window.L.circleMarker([cam.latitude, cam.longitude], {
          radius: 8,
          fillColor: cam.status === 'active' ? '#00d4ff' : '#94a3b8',
          color: '#ffffff',
          weight: 1.5,
          opacity: 1,
          fillOpacity: 0.8
        }).addTo(mapRef.current)

        // Bind interactive popup styled to match dashboard theme
        marker.bindPopup(`
          <div style="color: #1a2035; font-family: Inter, sans-serif; font-size: 12px; padding: 4px; min-width: 150px;">
            <strong style="font-size: 13px; color: #111827; display: block; border-bottom: 1px solid #e5e7eb; padding-bottom: 4px; margin-bottom: 6px;">
              ${cam.name}
            </strong>
            <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 4px;">
              <span style="width: 7px; height: 7px; border-radius: 50%; display: inline-block; background: ${cam.status === 'active' ? '#10b981' : '#94a3b8'};"></span>
              <span style="font-weight: 600; text-transform: capitalize; color: #374151;">Status: ${cam.status}</span>
            </div>
            <div style="font-weight: 700; color: #7c3aed; margin-top: 6px; font-size: 11px;">
              ⚡ Vehicles Counted: ${cam.event_count?.toLocaleString() ?? 0}
            </div>
          </div>
        `)

        markersRef.current.push(marker)
        bounds.push([cam.latitude, cam.longitude])
      })

      // Adjust map viewport to fit all plotted camera pins automatically
      if (bounds.length > 0) {
        mapRef.current.fitBounds(bounds, { padding: [40, 40] })
      }
    }
  }, [cameras])

  if (typeof window.L === 'undefined') {
    return (
      <div className="h-72 w-full rounded-xl border border-bg-border flex items-center justify-center text-text-muted text-sm bg-bg-card">
        Loading Leaflet Map Library...
      </div>
    )
  }

  return (
    <div className="relative w-full h-72 rounded-xl overflow-hidden border border-bg-border">
      {camerasLoading && (
        <div className="absolute inset-0 bg-bg/50 backdrop-blur-sm z-[1000] flex items-center justify-center text-xs text-text-secondary">
          Syncing Camera Coordinates...
        </div>
      )}
      <div ref={mapContainerRef} className="w-full h-full" style={{ background: '#0a0f1e' }} />
    </div>
  )
}
