import { useState } from 'react'
import { KeyRound, ShieldCheck, Eye, EyeOff } from 'lucide-react'
import api from '../lib/api'
import { getAccessToken } from '../lib/auth'

function getOwnUserId() {
  const token = getAccessToken()
  if (!token) return null
  try {
    const payload = JSON.parse(atob(token.split('.')[1]))
    // sub is email — we need user_id; we store it in a separate call
    return payload
  } catch (e) {
    return null
  }
}

export default function ChangePassword() {
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showCurrent, setShowCurrent] = useState(false)
  const [showNew, setShowNew] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setSuccess('')

    if (newPassword !== confirmPassword) {
      setError('New passwords do not match.')
      return
    }
    if (newPassword.length < 8) {
      setError('New password must be at least 8 characters.')
      return
    }

    setSubmitting(true)
    try {
      // First, get own user id from /api/users list
      const usersResp = await api.get('/api/users?limit=100')
      const token = getAccessToken()
      const payload = JSON.parse(atob(token.split('.')[1]))
      const myEmail = payload.sub
      const myUser = usersResp.data?.items?.find(u => u.email === myEmail)

      if (!myUser) {
        setError('Could not find your user account. Please contact an administrator.')
        return
      }

      await api.put(`/api/users/${myUser.id}/password`, {
        current_password: currentPassword,
        new_password: newPassword
      })

      setSuccess('Password updated successfully! Please use your new password next time you log in.')
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to change password.')
    } finally {
      setSubmitting(false)
    }
  }

  const strength = (() => {
    if (!newPassword) return null
    let score = 0
    if (newPassword.length >= 8) score++
    if (newPassword.length >= 12) score++
    if (/[A-Z]/.test(newPassword)) score++
    if (/[0-9]/.test(newPassword)) score++
    if (/[^A-Za-z0-9]/.test(newPassword)) score++
    if (score <= 1) return { label: 'Weak', color: 'bg-accent-red', width: '20%' }
    if (score === 2) return { label: 'Fair', color: 'bg-accent-amber', width: '40%' }
    if (score === 3) return { label: 'Good', color: 'bg-accent-amber', width: '65%' }
    return { label: 'Strong', color: 'bg-accent-green', width: '100%' }
  })()

  return (
    <div className="p-6 max-w-xl mx-auto space-y-6 page-mount">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-black bg-gradient-to-r from-accent-cyan to-accent-purple bg-clip-text text-transparent flex items-center gap-3">
          <KeyRound className="text-accent-cyan" />
          Change Password
        </h1>
        <p className="text-text-secondary mt-1">Update your login credentials</p>
      </div>

      <div className="bg-bg-card rounded-xl border border-bg-border shadow-card p-6">
        <form onSubmit={handleSubmit} className="space-y-5">
          {error && (
            <div className="bg-accent-red/10 border border-accent-red/30 text-accent-red text-sm rounded-lg px-4 py-3">
              {error}
            </div>
          )}
          {success && (
            <div className="bg-accent-green/10 border border-accent-green/30 text-accent-green text-sm rounded-lg px-4 py-3 flex items-center gap-2">
              <ShieldCheck size={16} />
              {success}
            </div>
          )}

          {/* Current Password */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs text-text-secondary font-semibold uppercase">Current Password</label>
            <div className="relative">
              <input
                type={showCurrent ? 'text' : 'password'}
                required
                value={currentPassword}
                onChange={e => setCurrentPassword(e.target.value)}
                placeholder="••••••••"
                className="w-full bg-bg border border-bg-border rounded-lg px-3 py-2.5 pr-10 text-sm text-text-primary focus:outline-none focus:border-accent-cyan"
              />
              <button
                type="button"
                onClick={() => setShowCurrent(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary"
              >
                {showCurrent ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          </div>

          {/* New Password */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs text-text-secondary font-semibold uppercase">New Password</label>
            <div className="relative">
              <input
                type={showNew ? 'text' : 'password'}
                required
                value={newPassword}
                onChange={e => setNewPassword(e.target.value)}
                placeholder="Min. 8 characters"
                className="w-full bg-bg border border-bg-border rounded-lg px-3 py-2.5 pr-10 text-sm text-text-primary focus:outline-none focus:border-accent-cyan"
              />
              <button
                type="button"
                onClick={() => setShowNew(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary"
              >
                {showNew ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
            {/* Strength bar */}
            {strength && (
              <div className="space-y-1">
                <div className="h-1 bg-bg-border rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-300 ${strength.color}`}
                    style={{ width: strength.width }}
                  />
                </div>
                <p className={`text-[10px] font-semibold ${
                  strength.label === 'Weak' ? 'text-accent-red' :
                  strength.label === 'Fair' ? 'text-accent-amber' :
                  strength.label === 'Good' ? 'text-accent-amber' : 'text-accent-green'
                }`}>
                  Password strength: {strength.label}
                </p>
              </div>
            )}
          </div>

          {/* Confirm Password */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs text-text-secondary font-semibold uppercase">Confirm New Password</label>
            <input
              type="password"
              required
              value={confirmPassword}
              onChange={e => setConfirmPassword(e.target.value)}
              placeholder="Re-enter new password"
              className={`w-full bg-bg border rounded-lg px-3 py-2.5 text-sm text-text-primary focus:outline-none focus:border-accent-cyan
                ${confirmPassword && confirmPassword !== newPassword ? 'border-accent-red/50' : 'border-bg-border'}`}
            />
            {confirmPassword && confirmPassword !== newPassword && (
              <p className="text-[10px] text-accent-red">Passwords do not match</p>
            )}
          </div>

          <button
            type="submit"
            disabled={submitting}
            className="w-full bg-gradient-to-r from-accent-cyan to-accent-purple text-white text-sm font-bold py-3 rounded-lg hover:shadow-glow-cyan transition-all disabled:opacity-60"
          >
            {submitting ? 'Updating...' : 'Update Password'}
          </button>
        </form>
      </div>

      {/* Tips */}
      <div className="bg-bg-card/50 rounded-xl border border-bg-border p-4 space-y-2">
        <p className="text-xs font-semibold text-text-muted uppercase">Password Tips</p>
        <ul className="text-xs text-text-muted space-y-1 list-disc list-inside">
          <li>Use at least 8 characters</li>
          <li>Include uppercase and lowercase letters</li>
          <li>Add numbers and special characters (e.g. !@#$)</li>
          <li>Avoid using your username or common words</li>
        </ul>
      </div>
    </div>
  )
}
