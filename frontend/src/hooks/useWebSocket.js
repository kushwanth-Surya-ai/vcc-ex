/**
 * useWebSocket.js
 *
 * Connects to VITE_WS_URL.
 * Auth handshake:
 *   1. Server sends  { type: 'auth_required' }
 *   2. Client sends  { type: 'auth', token: <access_token> }   (NOT in URL)
 *   3. Server replies { type: 'auth_ok' } or { type: 'auth_failed' }
 *
 * Auto-reconnects with exponential back-off capped at 30 s.
 */

import { useEffect, useRef, useState, useCallback } from 'react'
import { getAccessToken } from '../lib/auth'

const WS_URL = import.meta.env.VITE_WS_URL || (window.location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + window.location.host + '/ws'
const MAX_BACKOFF_MS = 30_000

export function useWebSocket() {
  const wsRef = useRef(null)
  const reconnectTimeoutRef = useRef(null)
  const attemptRef = useRef(0)
  const unmountedRef = useRef(false)

  const [lastMessage, setLastMessage] = useState(null)
  const [connectionStatus, setConnectionStatus] = useState('disconnected') // 'connecting' | 'authenticating' | 'connected' | 'disconnected' | 'error'

  const connect = useCallback(() => {
    if (unmountedRef.current) return
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return

    setConnectionStatus('connecting')

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      // Don't mark as connected yet — wait for auth handshake
      setConnectionStatus('authenticating')
    }

    ws.onmessage = (event) => {
      let parsed
      try {
        parsed = JSON.parse(event.data)
      } catch {
        parsed = { type: 'raw', data: event.data }
      }

      if (parsed.type === 'auth_required') {
        const token = getAccessToken()
        ws.send(JSON.stringify({ type: 'auth', token }))
        return
      }

      if (parsed.type === 'auth_ok') {
        setConnectionStatus('connected')
        attemptRef.current = 0 // reset backoff on successful auth
        return
      }

      if (parsed.type === 'auth_failed') {
        setConnectionStatus('error')
        ws.close()
        return
      }

      setLastMessage(parsed)
    }

    ws.onclose = (event) => {
      if (unmountedRef.current) return
      setConnectionStatus('disconnected')
      scheduleReconnect()
    }

    ws.onerror = () => {
      setConnectionStatus('error')
      ws.close()
    }
  }, [])

  const scheduleReconnect = useCallback(() => {
    if (unmountedRef.current) return
    const attempt = attemptRef.current
    const delay = Math.min(1_000 * Math.pow(2, attempt), MAX_BACKOFF_MS)
    attemptRef.current = attempt + 1
    reconnectTimeoutRef.current = setTimeout(connect, delay)
  }, [connect])

  useEffect(() => {
    unmountedRef.current = false
    connect()

    return () => {
      unmountedRef.current = true
      clearTimeout(reconnectTimeoutRef.current)
      if (wsRef.current) {
        wsRef.current.onclose = null // prevent reconnect on intentional close
        wsRef.current.close()
      }
    }
  }, [connect])

  const sendMessage = useCallback((data) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data))
    }
  }, [])

  return { lastMessage, connectionStatus, sendMessage }
}
