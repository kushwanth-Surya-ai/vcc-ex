/**
 * auth.js — In-memory access token store.
 * The token NEVER touches localStorage or sessionStorage.
 * Refresh uses an httpOnly cookie automatically sent by the browser.
 */

import axios from 'axios'

// ─── Module-level private state ───────────────────────────────────────────────
let _accessToken = null

// ─── Token management ─────────────────────────────────────────────────────────
export function setAccessToken(token) {
  _accessToken = token
}

export function getAccessToken() {
  return _accessToken
}

export function clearTokens() {
  _accessToken = null
}

export function isAuthenticated() {
  return Boolean(_accessToken)
}

// ─── Refresh ──────────────────────────────────────────────────────────────────
/**
 * Calls POST /auth/refresh.  The httpOnly refresh cookie is sent automatically
 * by the browser (withCredentials).  Returns the new access token string or
 * throws if refresh fails.
 */
export async function refreshAccessToken() {
  const baseURL = import.meta.env.VITE_API_URL || ''
  const response = await axios.post(
    `${baseURL}/auth/refresh`,
    {},
    { withCredentials: true },
  )
  const newToken = response.data?.access_token
  if (!newToken) throw new Error('No access token in refresh response')
  setAccessToken(newToken)
  return newToken
}

export function getUserRole() {
  const token = getAccessToken()
  if (!token) return 'viewer'
  try {
    const payload = JSON.parse(atob(token.split('.')[1]))
    return payload.role || 'viewer'
  } catch (e) {
    return 'viewer'
  }
}
