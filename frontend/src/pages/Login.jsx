import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { Eye, EyeOff, Loader2, ShieldCheck } from "lucide-react"
import api from "../lib/api"
import { setAccessToken } from "../lib/auth"

export default function Login() {
  const navigate = useNavigate()
  const [email, setEmail]       = useState("")
  const [password, setPassword] = useState("")
  const [showPw, setShowPw]     = useState(false)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState("")

  async function handleSubmit(e) {
    e.preventDefault()
    setError("")
    setLoading(true)
    try {
      // Send email and password as JSON matching FastAPI LoginRequest schema
      const res = await api.post("/auth/login", { email, password }, {
        headers: { "Content-Type": "application/json" },
        withCredentials: true,
      })
      const token = res.data?.access_token
      if (!token) throw new Error("No access token received")
      setAccessToken(token)
      navigate("/", { replace: true })
    } catch (err) {
      let errorDetail = err?.response?.data?.detail;
      if (Array.isArray(errorDetail)) {
        errorDetail = errorDetail.map(e => e.msg || JSON.stringify(e)).join(", ");
      }
      setError(errorDetail ?? err.message ?? "Login failed. Please try again.")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      className="min-h-screen flex items-center justify-center relative overflow-hidden"
      style={{
        background: "radial-gradient(ellipse 80% 60% at 50% -20%, rgba(0,212,255,0.12) 0%, transparent 60%), linear-gradient(135deg, #0a0f1e 0%, #0d1528 50%, #0a0f1e 100%)",
      }}
    >
      {/* Background decorative blobs */}
      <div className="absolute top-0 left-0 w-full h-full pointer-events-none overflow-hidden">
        <div className="absolute -top-40 -left-40 w-96 h-96 rounded-full opacity-5"
          style={{ background: "radial-gradient(circle, var(--accent-cyan), transparent 70%)" }} />
        <div className="absolute -bottom-40 -right-40 w-96 h-96 rounded-full opacity-5"
          style={{ background: "radial-gradient(circle, var(--accent-purple), transparent 70%)" }} />
      </div>

      {/* Glass card */}
      <div className="relative w-full max-w-md mx-4 animate-fade-in">
        <div className="glass rounded-2xl p-8 shadow-card border border-bg-border"
          style={{ boxShadow: "0 0 0 1px rgba(0,212,255,0.08), 0 32px 64px rgba(0,0,0,0.6)" }}>

          {/* Logo */}
          <div className="flex flex-col items-center mb-8">
            <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-accent-cyan to-accent-purple
                            flex items-center justify-center mb-4 shadow-glow-cyan">
              <ShieldCheck size={28} className="text-white" />
            </div>
            <h1 className="text-2xl font-black bg-gradient-to-r from-accent-cyan to-accent-purple
                           bg-clip-text text-transparent">
              VCC System
            </h1>
            <p className="text-text-muted text-sm mt-1">Vehicle Count &amp; Classification</p>
          </div>

          {/* Error message */}
          {error && (
            <div className="mb-5 px-4 py-3 rounded-lg bg-accent-red/10 border border-accent-red/20 text-accent-red text-sm">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-5">
            {/* Email */}
            <div className="relative">
              <label className="block text-xs font-medium text-text-muted uppercase tracking-wider mb-1.5">
                Email / Username
              </label>
              <input
                type="text"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="username"
                placeholder="admin@vcc.local"
                className="w-full bg-bg border border-bg-border rounded-xl px-4 py-3
                           text-text-primary placeholder-text-muted text-sm
                           focus:border-accent-cyan/60 transition-colors"
              />
            </div>

            {/* Password */}
            <div className="relative">
              <label className="block text-xs font-medium text-text-muted uppercase tracking-wider mb-1.5">
                Password
              </label>
              <div className="relative">
                <input
                  type={showPw ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoComplete="current-password"
                  placeholder="••••••••"
                  className="w-full bg-bg border border-bg-border rounded-xl px-4 py-3 pr-12
                             text-text-primary placeholder-text-muted text-sm
                             focus:border-accent-cyan/60 transition-colors"
                />
                <button
                  type="button"
                  onClick={() => setShowPw((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary transition-colors"
                >
                  {showPw ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>

            {/* Submit */}
            <button
              type="submit"
              disabled={loading}
              className="w-full py-3 rounded-xl font-semibold text-white text-sm
                         bg-gradient-to-r from-accent-cyan to-accent-purple
                         hover:opacity-90 active:opacity-80
                         disabled:opacity-50 disabled:cursor-not-allowed
                         transition-all duration-200
                         flex items-center justify-center gap-2
                         shadow-glow-cyan"
            >
              {loading ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  Signing in…
                </>
              ) : (
                "Sign In"
              )}
            </button>
          </form>

          <p className="text-text-muted text-xs text-center mt-6">
            Secured by VCC Auth · Session tokens stored in memory only
          </p>
        </div>
      </div>
    </div>
  )
}
