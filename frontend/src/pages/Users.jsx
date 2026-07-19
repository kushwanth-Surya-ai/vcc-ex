import { useState } from 'react'
import { UserPlus, Trash2, ShieldAlert } from 'lucide-react'
import { useApi } from '../hooks/useApi'
import api from '../lib/api'
import { getAccessToken } from '../lib/auth'

function getOwnEmail() {
  const token = getAccessToken()
  if (!token) return ''
  try {
    const payload = JSON.parse(atob(token.split('.')[1]))
    return payload.sub || ''
  } catch (e) {
    return ''
  }
}

export default function Users() {
  const { data, loading, refetch } = useApi('/api/users')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState('viewer')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const usersList = data?.items || []
  const ownEmail = getOwnEmail()


  const handleCreate = async (e) => {
    e.preventDefault()
    setError('')
    setSuccess('')
    setSubmitting(true)

    // Build email from username: if no @, append @vcc.local
    const emailValue = username.includes('@') ? username : `${username}@vcc.local`

    try {
      await api.post('/api/users', { email: emailValue, password, role })
      setSuccess(`User '${username}' created successfully.`)
      setUsername('')
      setPassword('')
      setRole('viewer')
      refetch()
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to create user. Ensure password has at least 8 characters.")
    } finally {
      setSubmitting(false)
    }
  }


  const handleDelete = async (userId, userEmail) => {
    if (userEmail === ownEmail) {
      alert("You cannot delete your own logged-in account.")
      return
    }
    if (!window.confirm(`Are you sure you want to delete user ${userEmail}?`)) {
      return
    }

    try {
      await api.delete(`/api/users/${userId}`)
      refetch()
    } catch (err) {
      alert(err.response?.data?.detail || "Failed to delete user.")
    }
  }

  return (
    <div className="p-6 space-y-6 max-w-5xl mx-auto">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-black bg-gradient-to-r from-accent-purple to-accent-cyan bg-clip-text text-transparent">
          User Management
        </h1>
        <p className="text-text-secondary mt-1">Manage platform credentials and access roles</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-12 gap-6 items-start">
        {/* Left: Add user form */}
        <div className="md:col-span-4 bg-bg-card rounded-xl border border-bg-border shadow-card p-5">
          <h2 className="text-text-secondary uppercase tracking-widest text-xs font-semibold flex items-center gap-2 mb-4">
            <UserPlus size={14} className="text-accent-cyan" />
            Add New User
          </h2>
          
          <form onSubmit={handleCreate} className="space-y-4">
            {error && <p className="text-xs text-accent-red font-semibold">{error}</p>}
            {success && <p className="text-xs text-accent-green font-semibold">{success}</p>}
            
            <div className="flex flex-col gap-1.5">
              <label className="text-xs text-text-secondary font-semibold uppercase">Username</label>
              <input 
                type="text" 
                required 
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="e.g. operator1 or user@vcc.local"
                className="bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-cyan w-full"
              />
              <p className="text-[10px] text-text-muted">Login email will be <span className="font-mono">{username && !username.includes('@') ? `${username}@vcc.local` : (username || 'username@vcc.local')}</span></p>
            </div>


            <div className="flex flex-col gap-1.5">
              <label className="text-xs text-text-secondary font-semibold uppercase">Password (min 8 chars)</label>
              <input 
                type="password" 
                required 
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-cyan w-full"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs text-text-secondary font-semibold uppercase">Access Role</label>
              <select 
                value={role} 
                onChange={(e) => setRole(e.target.value)}
                className="bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-cyan w-full"
              >
                <option value="viewer">Viewer (Read-only)</option>
                <option value="admin">Administrator (Full Access)</option>
              </select>
            </div>

            <button 
              type="submit" 
              disabled={submitting}
              className="w-full bg-gradient-to-r from-accent-cyan to-accent-purple text-white text-sm font-bold py-2.5 rounded-lg hover:shadow-glow-cyan transition-all"
            >
              {submitting ? 'Creating...' : 'Create User'}
            </button>
          </form>
        </div>

        {/* Right: Users table list */}
        <div className="md:col-span-8 bg-bg-card rounded-xl border border-bg-border shadow-card overflow-hidden">
          <div className="p-4 border-b border-bg-border">
            <h2 className="text-text-secondary uppercase tracking-widest text-xs font-semibold">
              Active Users
            </h2>
          </div>
          
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm border-collapse">
              <thead>
                <tr className="border-b border-bg-border text-text-muted text-xs uppercase font-semibold">
                  <th className="px-5 py-3">Email Address</th>
                  <th className="px-5 py-3">Role</th>
                  <th className="px-5 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-bg-border text-text-secondary">
                {loading ? (
                  <tr>
                    <td colSpan="3" className="text-center py-8 text-text-muted">Loading users...</td>
                  </tr>
                ) : usersList.length === 0 ? (
                  <tr>
                    <td colSpan="3" className="text-center py-8 text-text-muted">No users found.</td>
                  </tr>
                ) : (
                  usersList.map((usr) => (
                    <tr key={usr.id} className="hover:bg-bg-hover/20 transition-colors">
                      <td className="px-5 py-4 font-medium text-text-primary">{usr.email}</td>
                      <td className="px-5 py-4">
                        <span className={`text-[10px] font-bold px-2 py-0.5 rounded-sm uppercase tracking-wider
                          ${usr.role === 'admin' ? 'bg-accent-purple/10 text-accent-purple' : 'bg-bg-border text-text-muted'}`}>
                          {usr.role}
                        </span>
                      </td>
                      <td className="px-5 py-4 text-right">
                        {usr.email !== ownEmail ? (
                          <button 
                            onClick={() => handleDelete(usr.id, usr.email)}
                            className="text-text-muted hover:text-accent-red transition-colors"
                            title="Delete User"
                          >
                            <Trash2 size={16} />
                          </button>
                        ) : (
                          <span className="text-xs text-text-muted flex items-center gap-1 justify-end">
                            <ShieldAlert size={12} />
                            Logged In
                          </span>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}
