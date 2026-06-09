"use client";

import { useEffect, useState } from "react";
import { Mail, RefreshCw, Send, CheckCircle, AlertCircle, Clock, Settings } from "lucide-react";
import { api } from "@/lib/api";

type DigestStatus = {
  configured: boolean;
  recipient: string;
  sender: string;
  topics: string[];
  schedule: string;
  current_time_local: string;
  last_sent_at: string | null;
  next_run_at: string | null;
};

type DigestSettings = {
  email_to: string;
  email_from: string;
  smtp_password: string;
  topics: string;
  timezone: string;
  send_hour: number;
  updated_at: string | null;
};

type SendResult = {
  success: boolean;
  sent_at: string;
  article_count: number;
  recipient: string;
};

const TIMEZONES = [
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "UTC",
  "Asia/Tokyo",
  "Europe/London",
  "Europe/Paris",
];

function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-US", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      timeZoneName: "short",
    });
  } catch {
    return iso;
  }
}

export default function DigestPage() {
  const [status, setStatus] = useState<DigestStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [sendResult, setSendResult] = useState<SendResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Settings form state
  const [settings, setSettings] = useState<DigestSettings | null>(null);
  const [form, setForm] = useState<Omit<DigestSettings, "updated_at">>({
    email_to: "",
    email_from: "",
    smtp_password: "",
    topics: "",
    timezone: "America/New_York",
    send_hour: 5,
  });
  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  // Whether a password is already stored on the server. The secret itself is
  // never sent to the browser — GET returns a masked "***" when one is set.
  const [passwordSet, setPasswordSet] = useState(false);

  const fetchStatus = async () => {
    try {
      const data = await api.digest.status();
      setStatus(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load status");
    } finally {
      setLoading(false);
    }
  };

  const fetchSettings = async () => {
    try {
      const data = await api.digest.getSettings();
      setSettings(data);
      // Never pre-fill the secret — leave the field blank; blank means "keep current".
      setPasswordSet(Boolean(data.smtp_password));
      setForm({
        email_to: data.email_to,
        email_from: data.email_from,
        smtp_password: "",
        topics: data.topics,
        timezone: data.timezone,
        send_hour: data.send_hour,
      });
    } catch {
      // settings fetch failure is non-critical
    }
  };

  useEffect(() => {
    fetchStatus();
    fetchSettings();
  }, []);

  const handleSendNow = async () => {
    setSending(true);
    setSendResult(null);
    setError(null);
    try {
      const result = await api.digest.sendNow();
      setSendResult(result);
      await fetchStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send digest");
    } finally {
      setSending(false);
    }
  };

  const handleSaveSettings = async () => {
    setSaving(true);
    setSaveSuccess(false);
    setSaveError(null);
    try {
      const updated = await api.digest.saveSettings(form);
      setSettings(updated);
      setPasswordSet(Boolean(updated.smtp_password));
      // Clear the password field after saving so the secret never lingers in state.
      setForm((f) => ({ ...f, smtp_password: "" }));
      setSaveSuccess(true);
      await fetchStatus();
      setTimeout(() => setSaveSuccess(false), 3000);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto p-8">
      {/* Header */}
      <div className="flex items-center gap-3 mb-8">
        <div className="w-10 h-10 rounded-lg bg-blue-600/20 flex items-center justify-center">
          <Mail className="text-blue-400" size={20} />
        </div>
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Daily Digest</h1>
          <p className="text-sm text-slate-400">Automated AI policy news delivery</p>
        </div>
        <button
          onClick={() => { fetchStatus(); fetchSettings(); }}
          className="ml-auto p-2 rounded-md text-slate-400 hover:text-slate-100 hover:bg-slate-800 transition-colors"
          title="Refresh"
        >
          <RefreshCw size={15} />
        </button>
      </div>

      {loading && (
        <div className="text-slate-400 text-sm">Loading...</div>
      )}

      {!loading && status && (
        <>
          {/* Config status banner */}
          {!status.configured && (
            <div className="flex items-start gap-3 p-4 mb-6 rounded-lg bg-amber-500/10 border border-amber-500/30">
              <AlertCircle size={16} className="text-amber-400 mt-0.5 flex-shrink-0" />
              <div className="text-sm text-amber-300">
                <p className="font-medium mb-1">Setup required</p>
                <p className="text-amber-400">
                  Enter your email address and password in the settings form below and save.
                </p>
              </div>
            </div>
          )}

          {/* Info grid */}
          <div className="grid gap-3 mb-6">
            <InfoRow label="Recipient" value={status.recipient} />
            <InfoRow label="Sender" value={status.sender} />
            <InfoRow label="Schedule" value={status.schedule} />
            <InfoRow label="Current Time (Local)" value={status.current_time_local} />
            <InfoRow
              label="Last Sent"
              value={formatDateTime(status.last_sent_at)}
              icon={status.last_sent_at ? <CheckCircle size={13} className="text-green-400" /> : undefined}
            />
            <InfoRow
              label="Next Run"
              value={formatDateTime(status.next_run_at)}
              icon={<Clock size={13} className="text-blue-400" />}
            />
          </div>

          {/* Topics */}
          <div className="mb-6">
            <p className="text-xs font-medium text-slate-400 uppercase tracking-wider mb-2">
              Search Topics
            </p>
            <div className="flex flex-wrap gap-2">
              {status.topics.map((t) => (
                <span
                  key={t}
                  className="px-2 py-0.5 text-xs rounded-full bg-blue-600/20 text-blue-300 border border-blue-600/30"
                >
                  {t}
                </span>
              ))}
            </div>
          </div>

          {/* Send now button */}
          <button
            onClick={handleSendNow}
            disabled={sending || !status.configured}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium
                       hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {sending ? (
              <RefreshCw size={15} className="animate-spin" />
            ) : (
              <Send size={15} />
            )}
            {sending ? "Sending..." : "Send Now (Test)"}
          </button>
        </>
      )}

      {/* Send result */}
      {sendResult && (
        <div className="mt-4 flex items-start gap-3 p-4 rounded-lg bg-green-500/10 border border-green-500/30">
          <CheckCircle size={16} className="text-green-400 mt-0.5 flex-shrink-0" />
          <div className="text-sm text-green-300">
            <p className="font-medium mb-1">Sent successfully</p>
            <p className="text-green-400">
              {sendResult.article_count} articles sent to{" "}
              <span className="font-mono">{sendResult.recipient}</span>.
            </p>
            <p className="text-green-500 text-xs mt-1">{formatDateTime(sendResult.sent_at)}</p>
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="mt-4 flex items-start gap-3 p-4 rounded-lg bg-red-500/10 border border-red-500/30">
          <AlertCircle size={16} className="text-red-400 mt-0.5 flex-shrink-0" />
          <p className="text-sm text-red-300">{error}</p>
        </div>
      )}

      {/* ── Settings Form ─────────────────────────────────────────────────── */}
      <div className="mt-10 border-t border-slate-800 pt-8">
        <div className="flex items-center gap-2 mb-6">
          <Settings size={16} className="text-slate-400" />
          <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider">Settings</h2>
          {settings?.updated_at && (
            <span className="ml-auto text-xs text-slate-500">
              Last updated: {formatDateTime(settings.updated_at)}
            </span>
          )}
        </div>

        <div className="grid gap-4">
          <FormField label="Recipient Email">
            <input
              type="email"
              value={form.email_to}
              onChange={(e) => setForm((f) => ({ ...f, email_to: e.target.value }))}
              placeholder="you@example.com"
              className="w-full px-3 py-2 rounded-md bg-slate-800 border border-slate-700 text-slate-100
                         text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors"
            />
          </FormField>

          <FormField label="Gmail Sender Address">
            <input
              type="email"
              value={form.email_from}
              onChange={(e) => setForm((f) => ({ ...f, email_from: e.target.value }))}
              placeholder="sender@gmail.com"
              className="w-full px-3 py-2 rounded-md bg-slate-800 border border-slate-700 text-slate-100
                         text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors"
            />
          </FormField>

          <FormField label="Gmail App Password">
            <input
              type="password"
              value={form.smtp_password}
              onChange={(e) => setForm((f) => ({ ...f, smtp_password: e.target.value }))}
              placeholder={passwordSet ? "•••• saved — leave blank to keep" : "xxxx xxxx xxxx xxxx"}
              autoComplete="off"
              className="w-full px-3 py-2 rounded-md bg-slate-800 border border-slate-700 text-slate-100
                         text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors"
            />
          </FormField>

          <FormField label="Search Topics (comma-separated)">
            <textarea
              value={form.topics}
              onChange={(e) => setForm((f) => ({ ...f, topics: e.target.value }))}
              rows={3}
              placeholder="AI policy,AI regulation,AI governance"
              className="w-full px-3 py-2 rounded-md bg-slate-800 border border-slate-700 text-slate-100
                         text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors resize-none"
            />
          </FormField>

          <div className="grid grid-cols-2 gap-4">
            <FormField label="Send Hour">
              <select
                value={form.send_hour}
                onChange={(e) => setForm((f) => ({ ...f, send_hour: Number(e.target.value) }))}
                className="w-full px-3 py-2 rounded-md bg-slate-800 border border-slate-700 text-slate-100
                           text-sm focus:outline-none focus:border-blue-500 transition-colors"
              >
                {Array.from({ length: 24 }, (_, i) => (
                  <option key={i} value={i}>
                    {String(i).padStart(2, "0")}:00
                  </option>
                ))}
              </select>
            </FormField>

            <FormField label="Timezone">
              <select
                value={form.timezone}
                onChange={(e) => setForm((f) => ({ ...f, timezone: e.target.value }))}
                className="w-full px-3 py-2 rounded-md bg-slate-800 border border-slate-700 text-slate-100
                           text-sm focus:outline-none focus:border-blue-500 transition-colors"
              >
                {TIMEZONES.map((tz) => (
                  <option key={tz} value={tz}>{tz}</option>
                ))}
              </select>
            </FormField>
          </div>
        </div>

        {/* Save button + feedback */}
        <div className="mt-6 flex items-center gap-4">
          <button
            onClick={handleSaveSettings}
            disabled={saving}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-slate-700 text-slate-100 text-sm font-medium
                       hover:bg-slate-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {saving ? <RefreshCw size={14} className="animate-spin" /> : <Settings size={14} />}
            {saving ? "Saving..." : "Save Settings"}
          </button>

          {saveSuccess && (
            <span className="flex items-center gap-1.5 text-sm text-green-400">
              <CheckCircle size={14} />
              Saved
            </span>
          )}
        </div>

        {saveError && (
          <div className="mt-3 flex items-start gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/30">
            <AlertCircle size={14} className="text-red-400 mt-0.5 flex-shrink-0" />
            <p className="text-xs text-red-300">{saveError}</p>
          </div>
        )}
      </div>
    </div>
  );
}

function InfoRow({
  label,
  value,
  icon,
}: {
  label: string;
  value: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-slate-800">
      <span className="text-xs text-slate-400 w-32 flex-shrink-0">{label}</span>
      <span className="text-sm text-slate-200 flex items-center gap-1.5 min-w-0">
        {icon}
        <span className="truncate">{value}</span>
      </span>
    </div>
  );
}

function FormField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-xs text-slate-400 mb-1.5">{label}</label>
      {children}
    </div>
  );
}
