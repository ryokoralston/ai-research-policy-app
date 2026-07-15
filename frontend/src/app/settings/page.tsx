"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const MODELS = [
  { group: "Anthropic", id: "claude-opus-4-6", label: "Claude Opus 4.6" },
  { group: "Anthropic", id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6" },
  { group: "Anthropic", id: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5 (Fast)" },
  { group: "OpenAI", id: "gpt-4o", label: "GPT-4o" },
  { group: "OpenAI", id: "gpt-4o-mini", label: "GPT-4o Mini (Fast)" },
];

const groups = ["Anthropic", "OpenAI"];

export default function SettingsPage() {
  const [mainModel, setMainModel] = useState("claude-opus-4-6");
  const [fastModel, setFastModel] = useState("claude-haiku-4-5-20251001");
  const [anthropicKey, setAnthropicKey] = useState("");
  const [openaiKey, setOpenaiKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [changingPassword, setChangingPassword] = useState(false);
  const [passwordBanner, setPasswordBanner] = useState<string | null>(null);

  useEffect(() => {
    api.settings.getModels().then((data) => {
      setMainModel(data.main_model);
      setFastModel(data.fast_model);
      // Keys come back masked ("***") — don't pre-fill
      setLoading(false);
    });
  }, []);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const body: Parameters<typeof api.settings.saveModels>[0] = {
        main_model: mainModel,
        fast_model: fastModel,
      };
      if (anthropicKey) body.anthropic_api_key = anthropicKey;
      if (openaiKey) body.openai_api_key = openaiKey;

      await api.settings.saveModels(body);
      setBanner("Settings saved successfully.");
      setAnthropicKey("");
      setOpenaiKey("");
      setTimeout(() => setBanner(null), 2500);
    } catch (err) {
      setBanner(`Error: ${err instanceof Error ? err.message : "Failed to save"}`);
      setTimeout(() => setBanner(null), 4000);
    } finally {
      setSaving(false);
    }
  }

  async function handleChangePassword(e: React.FormEvent) {
    e.preventDefault();
    if (newPassword !== confirmPassword) {
      setPasswordBanner("Error: new passwords do not match.");
      setTimeout(() => setPasswordBanner(null), 4000);
      return;
    }
    setChangingPassword(true);
    try {
      await api.auth.changePassword(currentPassword, newPassword);
      setPasswordBanner("Password changed successfully.");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setTimeout(() => setPasswordBanner(null), 2500);
    } catch (err) {
      setPasswordBanner(`Error: ${err instanceof Error ? err.message : "Failed to change password"}`);
      setTimeout(() => setPasswordBanner(null), 4000);
    } finally {
      setChangingPassword(false);
    }
  }

  function ModelSelect({
    id,
    value,
    onChange,
    label,
  }: {
    id: string;
    value: string;
    onChange: (v: string) => void;
    label: string;
  }) {
    return (
      <div>
        <label htmlFor={id} className="block text-sm font-medium text-slate-300 mb-1">
          {label}
        </label>
        <select
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {groups.map((g) => (
            <optgroup key={g} label={g}>
              {MODELS.filter((m) => m.group === g).map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-slate-400 text-sm">
        Loading settings…
      </div>
    );
  }

  return (
    <div className="max-w-xl mx-auto py-10 px-4">
      <h1 className="text-2xl font-bold text-slate-100 mb-1">AI Model Settings</h1>
      <p className="text-slate-400 text-sm mb-8">
        Choose the models used for research, reports, and analysis.
        API keys are stored securely in the database and never exposed.
      </p>

      {banner && (
        <div
          className={`mb-6 px-4 py-3 rounded-md text-sm font-medium ${
            banner.startsWith("Error")
              ? "bg-red-900/40 text-red-300 border border-red-700"
              : "bg-green-900/40 text-green-300 border border-green-700"
          }`}
        >
          {banner}
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-6">
        {/* Model selection */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-4">
          <h2 className="text-sm font-semibold text-slate-200 uppercase tracking-wide">
            Model Selection
          </h2>
          <ModelSelect
            id="main_model"
            label="Main Model (reports, synthesis, streaming)"
            value={mainModel}
            onChange={setMainModel}
          />
          <ModelSelect
            id="fast_model"
            label="Fast Model (query decomposition, per-source summaries)"
            value={fastModel}
            onChange={setFastModel}
          />
        </div>

        {/* API Keys */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-4">
          <h2 className="text-sm font-semibold text-slate-200 uppercase tracking-wide">
            API Keys
          </h2>
          <p className="text-xs text-slate-500">
            Leave blank to keep the current key. Keys are displayed as *** once saved.
          </p>

          <div>
            <label htmlFor="anthropic_key" className="block text-sm font-medium text-slate-300 mb-1">
              Anthropic API Key
            </label>
            <input
              id="anthropic_key"
              type="password"
              value={anthropicKey}
              onChange={(e) => setAnthropicKey(e.target.value)}
              placeholder="Leave blank to keep current"
              autoComplete="off"
              className="w-full bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <div>
            <label htmlFor="openai_key" className="block text-sm font-medium text-slate-300 mb-1">
              OpenAI API Key
            </label>
            <input
              id="openai_key"
              type="password"
              value={openaiKey}
              onChange={(e) => setOpenaiKey(e.target.value)}
              placeholder="Leave blank to keep current"
              autoComplete="off"
              className="w-full bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
        </div>

        <button
          type="submit"
          disabled={saving}
          className="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-medium text-sm py-2.5 rounded-md transition-colors"
        >
          {saving ? "Saving…" : "Save Settings"}
        </button>
      </form>

      {passwordBanner && (
        <div
          className={`mt-8 mb-2 px-4 py-3 rounded-md text-sm font-medium ${
            passwordBanner.startsWith("Error")
              ? "bg-red-900/40 text-red-300 border border-red-700"
              : "bg-green-900/40 text-green-300 border border-green-700"
          }`}
        >
          {passwordBanner}
        </div>
      )}

      <form onSubmit={handleChangePassword} className="space-y-6 mt-8">
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-4">
          <h2 className="text-sm font-semibold text-slate-200 uppercase tracking-wide">
            Change Password
          </h2>

          <div>
            <label htmlFor="current_password" className="block text-sm font-medium text-slate-300 mb-1">
              Current password
            </label>
            <input
              id="current_password"
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              autoComplete="current-password"
              className="w-full bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <div>
            <label htmlFor="new_password" className="block text-sm font-medium text-slate-300 mb-1">
              New password
            </label>
            <input
              id="new_password"
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
              className="w-full bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <div>
            <label htmlFor="confirm_new_password" className="block text-sm font-medium text-slate-300 mb-1">
              Confirm new password
            </label>
            <input
              id="confirm_new_password"
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
              className="w-full bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
        </div>

        <button
          type="submit"
          disabled={changingPassword || !currentPassword || !newPassword || !confirmPassword}
          className="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium text-sm py-2.5 rounded-md transition-colors"
        >
          {changingPassword ? "Changing…" : "Change Password"}
        </button>
      </form>
    </div>
  );
}
