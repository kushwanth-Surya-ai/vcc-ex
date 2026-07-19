/**
 * api.js — Axios instance with auth interceptors.
 *
 * Request interceptor  → injects Authorization: Bearer {token}
 * Response interceptor → on 401 attempts one token refresh, retries request;
 *                        on second failure clears tokens and redirects to /login
 */

import axios from 'axios'
import { getAccessToken, refreshAccessToken, clearTokens } from './auth'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '',
  withCredentials: true, // needed so refresh cookie is always sent
  headers: {
    'Content-Type': 'application/json',
  },
})

// ─── Request interceptor ──────────────────────────────────────────────────────
api.interceptors.request.use(
  (config) => {
    const token = getAccessToken()
    if (token) {
      config.headers['Authorization'] = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error),
)

// ─── Response interceptor ─────────────────────────────────────────────────────
let _isRefreshing = false
let _pendingQueue = []  // { resolve, reject }[]

function processPendingQueue(error, token = null) {
  _pendingQueue.forEach(({ resolve, reject }) => {
    if (error) reject(error)
    else resolve(token)
  })
  _pendingQueue = []
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config

    if (error.response?.status === 401 && !originalRequest._retry) {
      if (_isRefreshing) {
        // Queue this request until the ongoing refresh completes
        return new Promise((resolve, reject) => {
          _pendingQueue.push({ resolve, reject })
        }).then((token) => {
          originalRequest.headers['Authorization'] = `Bearer ${token}`
          return api(originalRequest)
        }).catch(Promise.reject.bind(Promise))
      }

      originalRequest._retry = true
      _isRefreshing = true

      try {
        const newToken = await refreshAccessToken()
        processPendingQueue(null, newToken)
        originalRequest.headers['Authorization'] = `Bearer ${newToken}`
        return api(originalRequest)
      } catch (refreshError) {
        processPendingQueue(refreshError, null)
        clearTokens()
        // Hard redirect to login — works outside React context too
        window.location.href = '/login'
        return Promise.reject(refreshError)
      } finally {
        _isRefreshing = false
      }
    }

    return Promise.reject(error)
  },
)

const mainApiUrl = import.meta.env.VITE_API_URL || `${window.location.protocol}//${window.location.hostname}:8000`
const trainingApiUrl = import.meta.env.VITE_TRAINING_API_URL || mainApiUrl.replace(/(:\d+)?\/?$/, ':8002')

export const trainingApi = axios.create({
  baseURL: trainingApiUrl,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
})

trainingApi.interceptors.request.use(
  (config) => {
    const token = getAccessToken()
    if (token) {
      config.headers['Authorization'] = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error),
)

trainingApi.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config

    if (error.response?.status === 401 && !originalRequest._retry) {
      if (_isRefreshing) {
        return new Promise((resolve, reject) => {
          _pendingQueue.push({ resolve, reject })
        }).then((token) => {
          originalRequest.headers['Authorization'] = `Bearer ${token}`
          return trainingApi(originalRequest)
        }).catch(Promise.reject.bind(Promise))
      }

      originalRequest._retry = true
      _isRefreshing = true

      try {
        const newToken = await refreshAccessToken()
        processPendingQueue(null, newToken)
        originalRequest.headers['Authorization'] = `Bearer ${newToken}`
        return trainingApi(originalRequest)
      } catch (refreshError) {
        processPendingQueue(refreshError, null)
        clearTokens()
        window.location.href = '/login'
        return Promise.reject(refreshError)
      } finally {
        _isRefreshing = false
      }
    }

    return Promise.reject(error)
  },
)

export default api

