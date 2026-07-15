"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Lock, ShieldCheck } from "lucide-react";
import { api, setToken } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [setupRequired, setSetupRequired] = useState<boolean | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.auth
      .status()
      .then(({ setup_required }) => setSetupRequired(setup_required))
      .catch(() => setSetupRequired(false));
  }, []);

  const handleBootstrap = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const { token } = await api.auth.bootstrap(email, password);
      setToken(token);
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the admin account.");
    } finally {
      setLoading(false);
    }
  };

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const { token } = await api.auth.login(email, password);
      setToken(token);
      router.replace("/");
    } catch {
      setError("Incorrect email or password.");
    } finally {
      setLoading(false);
    }
  };

  if (setupRequired === null) return null; // avoid a form flash before status resolves

  return (
    <div className="flex items-center justify-center min-h-[70vh] px-4">
      <form
        onSubmit={setupRequired ? handleBootstrap : handleLogin}
        className="w-full max-w-sm bg-slate-900 border border-slate-800 rounded-xl p-8 space-y-5"
      >
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-blue-600/20 flex items-center justify-center">
            {setupRequired ? (
              <ShieldCheck className="text-blue-400" size={18} />
            ) : (
              <Lock className="text-blue-400" size={18} />
            )}
          </div>
          <div>
            <h1 className="text-lg font-semibold text-slate-100">
              {setupRequired ? "Create the admin account" : "Sign in"}
            </h1>
            <p className="text-sm text-slate-400">
              {setupRequired
                ? "No accounts exist yet — set up the first admin."
                : "Enter your email and password to continue"}
            </p>
          </div>
        </div>

        <div>
          <label htmlFor="email" className="block text-xs text-slate-400 mb-1.5">
            Email
          </label>
          <input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoFocus
            autoComplete="email"
            className="w-full px-3 py-2 rounded-md bg-slate-800 border border-slate-700 text-slate-100
                       text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors"
          />
        </div>

        <div>
          <label htmlFor="password" className="block text-xs text-slate-400 mb-1.5">
            Password
          </label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={setupRequired ? "new-password" : "current-password"}
            className="w-full px-3 py-2 rounded-md bg-slate-800 border border-slate-700 text-slate-100
                       text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors"
          />
        </div>

        {setupRequired && (
          <div>
            <label htmlFor="confirmPassword" className="block text-xs text-slate-400 mb-1.5">
              Confirm password
            </label>
            <input
              id="confirmPassword"
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
              className="w-full px-3 py-2 rounded-md bg-slate-800 border border-slate-700 text-slate-100
                         text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors"
            />
          </div>
        )}

        {error && <p className="text-sm text-red-400">{error}</p>}

        <button
          type="submit"
          disabled={
            loading ||
            !email ||
            !password ||
            (setupRequired ? !confirmPassword : false)
          }
          className="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed
                     text-white font-medium text-sm py-2.5 rounded-md transition-colors"
        >
          {loading
            ? setupRequired
              ? "Creating account…"
              : "Signing in…"
            : setupRequired
            ? "Create admin account"
            : "Sign in"}
        </button>
      </form>
    </div>
  );
}
