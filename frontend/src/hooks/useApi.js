/**
 * useApi.js
 *
 * Generic polling hook.
 * Usage: const { data, loading, error, refetch } = useApi('/api/endpoint', { param: value }, [dep])
 *
 * - Polls every VITE_POLL_INTERVAL_MS (default 30 000 ms)
 * - Cancels in-flight requests on unmount / param change via AbortController
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import api from '../lib/api'

const POLL_INTERVAL = Number(import.meta.env.VITE_POLL_INTERVAL_MS) || 30_000

export function useApi(endpoint, params = {}, deps = [], { apiInstance } = {}) {
  const client = apiInstance || api
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const abortControllerRef = useRef(null)
  const intervalRef = useRef(null)

  const fetchData = useCallback(async (signal) => {
    try {
      setError(null)
      const response = await client.get(endpoint, {
        params,
        signal,
      })
      setData(response.data)
    } catch (err) {
      if (err.name === 'CanceledError' || err.name === 'AbortError') return
      setError(err?.response?.data?.detail || err.message || 'Unknown error')
    } finally {
      setLoading(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endpoint, JSON.stringify(params)])

  const refetch = useCallback(() => {
    // Cancel any pending request
    abortControllerRef.current?.abort()
    const controller = new AbortController()
    abortControllerRef.current = controller
    setLoading(true)
    fetchData(controller.signal)
  }, [fetchData])

  useEffect(() => {
    // Initial fetch
    abortControllerRef.current?.abort()
    const controller = new AbortController()
    abortControllerRef.current = controller
    setLoading(true)
    fetchData(controller.signal)

    // Start polling
    intervalRef.current = setInterval(() => {
      abortControllerRef.current?.abort()
      const ctrl = new AbortController()
      abortControllerRef.current = ctrl
      fetchData(ctrl.signal)
    }, POLL_INTERVAL)

    return () => {
      abortControllerRef.current?.abort()
      clearInterval(intervalRef.current)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchData, ...deps])

  return { data, loading, error, refetch }
}
