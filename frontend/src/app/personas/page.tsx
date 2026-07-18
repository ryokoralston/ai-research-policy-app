"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Plus, Trash2, Pencil, X } from "lucide-react";
import { api, type CustomPersonaApi } from "@/lib/api";
import { useCurrentUser } from "@/components/layout/UserContext";
import Badge from "@/components/ui/Badge";

interface PersonaFormState {
  name: string;
  title: string;
  initials: string;
  priorities: string;
  style: string;
}

const EMPTY_FORM: PersonaFormState = { name: "", title: "", initials: "", priorities: "", style: "" };

export default function PersonasPage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [personas, setPersonas] = useState<CustomPersonaApi[] | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [form, setForm] = useState<PersonaFormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (currentUser && currentUser.role !== "admin") {
      router.replace("/");
      return;
    }
    if (currentUser?.role === "admin") {
      api.personas.adminList().then(setPersonas).catch(() => setPersonas([]));
    }
  }, [currentUser, router]);

  function flash(msg: string) {
    setBanner(msg);
    setTimeout(() => setBanner(null), 3500);
  }

  async function refresh() {
    setPersonas(await api.personas.adminList());
  }

  function openCreateForm() {
    setEditingKey(null);
    setForm(EMPTY_FORM);
    setShowForm(true);
  }

  function openEditForm(p: CustomPersonaApi) {
    setEditingKey(p.key);
    setForm({ name: p.name, title: p.title, initials: p.initials, priorities: p.priorities, style: p.style });
    setShowForm(true);
  }

  function closeForm() {
    setShowForm(false);
    setEditingKey(null);
    setForm(EMPTY_FORM);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      if (editingKey) {
        await api.personas.update(editingKey, form);
        flash("Persona updated.");
      } else {
        await api.personas.create(form);
        flash("Persona created.");
      }
      closeForm();
      await refresh();
    } catch (err) {
      flash(`Error: ${err instanceof Error ? err.message : "Failed to save persona"}`);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(p: CustomPersonaApi) {
    if (!window.confirm(`Delete "${p.name}"? This cannot be undone.`)) return;
    try {
      await api.personas.delete(p.key);
      await refresh();
      flash("Persona deleted.");
    } catch (err) {
      flash(`Error: ${err instanceof Error ? err.message : "Failed to delete persona"}`);
    }
  }

  if (!currentUser || currentUser.role !== "admin" || personas === null) {
    return (
      <div className="flex items-center justify-center h-64 text-slate-400 text-sm">Loading…</div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto py-10 px-4">
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-2xl font-bold text-slate-100">Custom Personas</h1>
        <button
          onClick={showForm ? closeForm : openCreateForm}
          className="flex items-center gap-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium px-3 py-2 rounded-md transition-colors"
        >
          {showForm ? <X size={16} /> : <Plus size={16} />}
          {showForm ? "Cancel" : "Add persona"}
        </button>
      </div>
      <p className="text-slate-400 text-sm mb-8">
        Organization-specific debate personas (e.g. &ldquo;our VP of Engineering&rdquo;), shared across
        all users. Any authenticated user can select these in a debate — only creating, editing, and
        deleting them is restricted to admins here. Unlike the built-in debate personas, a custom
        persona may be modeled on a real individual within your organization for internal
        decision-support purposes.
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

      {showForm && (
        <form
          onSubmit={handleSubmit}
          className="bg-slate-900 border border-slate-800 rounded-lg p-5 mb-6 space-y-4"
        >
          <p className="text-sm font-semibold text-slate-200">
            {editingKey ? `Edit ${editingKey}` : "New custom persona"}
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <input
              type="text"
              required
              placeholder="Name (e.g. Jane Doe)"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              className="sm:col-span-2 bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <input
              type="text"
              required
              maxLength={3}
              placeholder="Initials (e.g. JD)"
              value={form.initials}
              onChange={(e) => setForm((f) => ({ ...f, initials: e.target.value }))}
              className="bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <input
            type="text"
            required
            placeholder="Title (e.g. VP of Engineering)"
            value={form.title}
            onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
            className="w-full bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <div>
            <label className="block text-xs text-slate-400 mb-1">
              Priorities — what this person cares about / evaluates proposals by
            </label>
            <textarea
              required
              rows={3}
              placeholder="e.g. Ships reliable systems on schedule, is skeptical of scope creep, weighs engineering headcount cost heavily..."
              value={form.priorities}
              onChange={(e) => setForm((f) => ({ ...f, priorities: e.target.value }))}
              className="w-full px-3 py-2 rounded-md bg-slate-800 border border-slate-700 text-slate-100 text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors resize-none"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">
              Style — how they communicate, what they push back on, tone
            </label>
            <textarea
              required
              rows={3}
              placeholder="e.g. Blunt and direct, asks for concrete numbers before agreeing, pushes back hard on unquantified risk claims..."
              value={form.style}
              onChange={(e) => setForm((f) => ({ ...f, style: e.target.value }))}
              className="w-full px-3 py-2 rounded-md bg-slate-800 border border-slate-700 text-slate-100 text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors resize-none"
            />
          </div>
          {editingKey && (
            <p className="text-xs text-slate-500">
              Note: the persona key &ldquo;{editingKey}&rdquo; stays fixed regardless of name changes,
              since it&apos;s what existing debates reference.
            </p>
          )}
          <button
            type="submit"
            disabled={saving}
            className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium px-4 py-2 rounded-md transition-colors"
          >
            {saving ? "Saving…" : editingKey ? "Save changes" : "Create persona"}
          </button>
        </form>
      )}

      {personas.length === 0 ? (
        <p className="text-slate-500 text-sm">No custom personas yet. Add one above.</p>
      ) : (
        <div className="space-y-3">
          {personas.map((p) => (
            <div
              key={p.key}
              className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex items-start gap-3"
            >
              <span className={`w-9 h-9 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 ${p.color} ${p.text_color}`}>
                {p.initials}
              </span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <p className="text-sm font-medium text-slate-100">{p.name}</p>
                  <Badge variant="blue">Custom</Badge>
                </div>
                <p className="text-xs text-slate-500 mt-0.5">{p.title}</p>
                <p className="text-xs text-slate-400 mt-1.5">{p.priorities}</p>
              </div>
              <div className="flex items-center gap-1 flex-shrink-0">
                <button
                  onClick={() => openEditForm(p)}
                  title="Edit"
                  className="p-1.5 text-slate-400 hover:text-slate-100 hover:bg-slate-800 rounded-md transition-colors"
                >
                  <Pencil size={14} />
                </button>
                <button
                  onClick={() => handleDelete(p)}
                  title="Delete"
                  className="p-1.5 text-slate-400 hover:text-red-400 hover:bg-slate-800 rounded-md transition-colors"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
