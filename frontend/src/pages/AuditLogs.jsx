import { useState } from 'react'
import { FileText, ShieldAlert, Key, RefreshCw, Search } from 'lucide-react'
import { useApi } from '../hooks/useApi'

export default function AuditLogs() {
  const [activeTab, setActiveTab] = useState('audit') // 'audit' | 'login'
  const [emailFilter, setEmailFilter] = useState('')
  const [actionFilter, setActionFilter] = useState('')
  const [successFilter, setSuccessFilter] = useState('all') // 'all' | 'true' | 'false'

  // Build query params
  const auditParams = new URLSearchParams()
  if (emailFilter) auditParams.append('email', emailFilter)
  if (actionFilter) auditParams.append('action', actionFilter)
  auditParams.append('limit', '100')

  const loginParams = new URLSearchParams()
  if (emailFilter) loginParams.append('email', emailFilter)
  if (successFilter !== 'all') loginParams.append('success', successFilter)
  loginParams.append('limit', '100')

  // Fetch logs
  const { data: auditData, loading: auditLoading, refetch: refetchAudit } = useApi(`/api/logs/audit?${auditParams.toString()}`)
  const { data: loginData, loading: loginLoading, refetch: refetchLogin } = useApi(`/api/logs/login?${loginParams.toString()}`)

  const auditLogs = auditData?.items || []
  const loginLogs = loginData?.items || []

  const handleRefresh = () => {
    if (activeTab === 'audit') refetchAudit()
    else refetchLogin()
  }

  return (
    <div className="p-6 space-y-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-black bg-gradient-to-r from-accent-cyan to-accent-purple bg-clip-text text-transparent flex items-center gap-3">
            <ShieldAlert size={28} className="text-accent-cyan" />
            Security & Audit Logs
          </h1>
          <p className="text-text-secondary mt-1">Audit platform activities and authentication history</p>
        </div>
        <button onClick={handleRefresh} className="flex items-center gap-2 bg-bg-card hover:bg-bg-border border border-bg-border px-4 py-2 rounded-lg text-sm font-medium transition-colors">
          <RefreshCw size={14} className="text-accent-cyan" />
          Refresh Logs
        </button>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-bg-border">
        <button 
          onClick={() => setActiveTab('audit')}
          className={`flex items-center gap-2 px-5 py-3 border-b-2 font-semibold text-sm transition-all
            ${activeTab === 'audit' 
              ? 'border-accent-cyan text-accent-cyan bg-accent-cyan/5' 
              : 'border-transparent text-text-muted hover:text-text-secondary'}`}
        >
          <FileText size={16} />
          Action Audit Trail
        </button>
        <button 
          onClick={() => setActiveTab('login')}
          className={`flex items-center gap-2 px-5 py-3 border-b-2 font-semibold text-sm transition-all
            ${activeTab === 'login' 
              ? 'border-accent-cyan text-accent-cyan bg-accent-cyan/5' 
              : 'border-transparent text-text-muted hover:text-text-secondary'}`}
        >
          <Key size={16} />
          Login History
        </button>
      </div>

      {/* Search / Filters block */}
      <div className="bg-bg-card border border-bg-border p-4 rounded-xl shadow-card flex flex-wrap gap-4 items-end">
        <div className="flex flex-col gap-1.5 flex-1 min-w-[200px]">
          <label className="text-xs text-text-muted uppercase font-semibold">Filter by Email</label>
          <div className="relative">
            <Search className="absolute left-3 top-2.5 text-text-muted" size={16} />
            <input 
              type="text" 
              value={emailFilter}
              onChange={(e) => setEmailFilter(e.target.value)}
              placeholder="Search user email..."
              className="bg-bg border border-bg-border rounded-lg pl-9 pr-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-cyan w-full"
            />
          </div>
        </div>

        {activeTab === 'audit' && (
          <div className="flex flex-col gap-1.5 min-w-[150px]">
            <label className="text-xs text-text-muted uppercase font-semibold">Action Type</label>
            <select 
              value={actionFilter}
              onChange={(e) => setActionFilter(e.target.value)}
              className="bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-cyan w-full"
            >
              <option value="">All Actions</option>
              <option value="USER_CREATED">User Created</option>
              <option value="USER_DELETED">User Deleted</option>
              <option value="CAMERA_ADDED">Camera Added</option>
              <option value="CAMERA_EDITED">Camera Edited</option>
              <option value="CAMERA_DELETED">Camera Deleted</option>
              <option value="ALERT_ACKNOWLEDGED">Alert Acknowledged</option>
            </select>
          </div>
        )}

        {activeTab === 'login' && (
          <div className="flex flex-col gap-1.5 min-w-[150px]">
            <label className="text-xs text-text-muted uppercase font-semibold">Login Status</label>
            <select 
              value={successFilter}
              onChange={(e) => setSuccessFilter(e.target.value)}
              className="bg-bg border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-cyan w-full"
            >
              <option value="all">All</option>
              <option value="true">Successful Logins</option>
              <option value="false">Failed Logins</option>
            </select>
          </div>
        )}

        <button 
          onClick={handleRefresh}
          className="flex items-center gap-1.5 bg-accent-cyan/10 hover:bg-accent-cyan/20 border border-accent-cyan/20 px-4 py-2 rounded-lg text-sm text-accent-cyan font-bold transition-all"
        >
          Apply Filters
        </button>
      </div>

      {/* Table section */}
      <div className="bg-bg-card rounded-xl border border-bg-border shadow-card overflow-hidden">
        {activeTab === 'audit' ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm border-collapse">
              <thead>
                <tr className="border-b border-bg-border text-text-muted text-xs uppercase font-semibold">
                  <th className="px-5 py-3">Timestamp</th>
                  <th className="px-5 py-3">User</th>
                  <th className="px-5 py-3">Action</th>
                  <th className="px-5 py-3">Details</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-bg-border text-text-secondary">
                {auditLoading ? (
                  <tr>
                    <td colSpan="4" className="text-center py-8 text-text-muted">Loading audit logs...</td>
                  </tr>
                ) : auditLogs.length === 0 ? (
                  <tr>
                    <td colSpan="4" className="text-center py-8 text-text-muted">No audit logs found.</td>
                  </tr>
                ) : (
                  auditLogs.map((log) => (
                    <tr key={log.id} className="hover:bg-bg-hover/20 transition-colors">
                      <td className="px-5 py-4 font-mono text-xs whitespace-nowrap">
                        {new Intl.DateTimeFormat(undefined, { dateStyle: 'short', timeStyle: 'medium' }).format(new Date(log.timestamp))}
                      </td>
                      <td className="px-5 py-4 font-medium text-text-primary">{log.email}</td>
                      <td className="px-5 py-4">
                        <span className="text-[10px] font-bold px-2 py-0.5 rounded-sm uppercase tracking-wider bg-accent-cyan/10 text-accent-cyan">
                          {log.action.replace('_', ' ')}
                        </span>
                      </td>
                      <td className="px-5 py-4 max-w-xs truncate" title={log.details}>{log.details || '—'}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm border-collapse">
              <thead>
                <tr className="border-b border-bg-border text-text-muted text-xs uppercase font-semibold">
                  <th className="px-5 py-3">Timestamp</th>
                  <th className="px-5 py-3">Email Address</th>
                  <th className="px-5 py-3">IP Address</th>
                  <th className="px-5 py-3">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-bg-border text-text-secondary">
                {loginLoading ? (
                  <tr>
                    <td colSpan="4" className="text-center py-8 text-text-muted">Loading login logs...</td>
                  </tr>
                ) : loginLogs.length === 0 ? (
                  <tr>
                    <td colSpan="4" className="text-center py-8 text-text-muted">No login attempts recorded.</td>
                  </tr>
                ) : (
                  loginLogs.map((log) => (
                    <tr key={log.id} className="hover:bg-bg-hover/20 transition-colors">
                      <td className="px-5 py-4 font-mono text-xs whitespace-nowrap">
                        {new Intl.DateTimeFormat(undefined, { dateStyle: 'short', timeStyle: 'medium' }).format(new Date(log.timestamp))}
                      </td>
                      <td className="px-5 py-4 font-medium text-text-primary">{log.email}</td>
                      <td className="px-5 py-4 font-mono text-xs">{log.ip_address}</td>
                      <td className="px-5 py-4">
                        <span className={`text-[10px] font-bold px-2 py-0.5 rounded-sm uppercase tracking-wider
                          ${log.success ? 'bg-accent-green/10 text-accent-green' : 'bg-accent-red/10 text-accent-red'}`}>
                          {log.success ? 'Success' : 'Failed'}
                        </span>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
