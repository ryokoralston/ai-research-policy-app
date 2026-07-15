"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { UserPlus } from "lucide-react";
import { api } from "@/lib/api";
import { useCurrentUser } from "@/components/layout/UserContext";
import Badge from "@/components/ui/Badge";

type UserRow = Awaited<ReturnType<typeof api.users.list>>[number];

export default function UsersPage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [users, setUsers] = useState<UserRow[] | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const [showAddForm, setShowAddForm] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState<"admin" | "member">("member");
  const [creating, setCreating] = useState(false);

  const [resetTarget, setResetTarget] = useState<string | null>(null);
  const [resetPassword, setResetPassword] = useState("");

  useEffect(() => {
    if (currentUser && currentUser.role !== "admin") {
      router.replace("/");
      return;
    }
    if (currentUser?.role === "admin") {
      api.users.list().then(setUsers).catch(() => setUsers([]));
    }
  }, [currentUser, router]);

  function flash(msg: string) {
    setBanner(msg);
    setTimeout(() => setBanner(null), 3500);
  }

  async function refresh() {
    setUsers(await api.users.list());
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    try {
      await api.users.create({ email: newEmail, password: newPassword, role: newRole });
      setNewEmail("");
      setNewPassword("");
      setNewRole("member");
      setShowAddForm(false);
      await refresh();
      flash("User created.");
    } catch (err) {
      flash(`Error: ${err instanceof Error ? err.message : "Failed to create user"}`);
    } finally {
      setCreating(false);
    }
  }

  async function toggleActive(u: UserRow) {
    try {
      await api.users.update(u.id, { is_active: !u.is_active });
      await refresh();
    } catch (err) {
      flash(`Error: ${err instanceof Error ? err.message : "Failed to update user"}`);
    }
  }

  async function toggleRole(u: UserRow) {
    try {
      await api.users.update(u.id, { role: u.role === "admin" ? "member" : "admin" });
      await refresh();
    } catch (err) {
      flash(`Error: ${err instanceof Error ? err.message : "Failed to update user"}`);
    }
  }

  async function handleResetPassword(e: React.FormEvent) {
    e.preventDefault();
    if (!resetTarget) return;
    try {
      await api.users.update(resetTarget, { new_password: resetPassword });
      setResetTarget(null);
      setResetPassword("");
      flash("Password reset.");
    } catch (err) {
      flash(`Error: ${err instanceof Error ? err.message : "Failed to reset password"}`);
    }
  }

  if (!currentUser || currentUser.role !== "admin" || users === null) {
    return (
      <div className="flex items-center justify-center h-64 text-slate-400 text-sm">Loading…</div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto py-10 px-4">
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-2xl font-bold text-slate-100">Users</h1>
        <button
          onClick={() => setShowAddForm((v) => !v)}
          className="flex items-center gap-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium px-3 py-2 rounded-md transition-colors"
        >
          <UserPlus size={16} />
          Add user
        </button>
      </div>
      <p className="text-slate-400 text-sm mb-8">
        Manage who can access this app and what they can do.
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

      {showAddForm && (
        <form
          onSubmit={handleCreate}
          className="bg-slate-900 border border-slate-800 rounded-lg p-5 mb-6 space-y-4"
        >
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <input
              type="email"
              required
              placeholder="Email"
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
              className="bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <input
              type="password"
              required
              placeholder="Temporary password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
              className="bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <select
              value={newRole}
              onChange={(e) => setNewRole(e.target.value as "admin" | "member")}
              className="bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="member">Member</option>
              <option value="admin">Admin</option>
            </select>
          </div>
          <button
            type="submit"
            disabled={creating || !newEmail || !newPassword}
            className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium px-4 py-2 rounded-md transition-colors"
          >
            {creating ? "Creating…" : "Create user"}
          </button>
        </form>
      )}

      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-left text-slate-400 text-xs uppercase tracking-wide">
              <th className="px-4 py-3 font-medium">Email</th>
              <th className="px-4 py-3 font-medium">Role</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium">Last login</th>
              <th className="px-4 py-3 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-b border-slate-800 last:border-0">
                <td className="px-4 py-3 text-slate-200">{u.email}</td>
                <td className="px-4 py-3">
                  <Badge variant={u.role === "admin" ? "blue" : "default"}>{u.role}</Badge>
                </td>
                <td className="px-4 py-3">
                  <Badge variant={u.is_active ? "green" : "red"}>
                    {u.is_active ? "active" : "deactivated"}
                  </Badge>
                </td>
                <td className="px-4 py-3 text-slate-400">
                  {u.last_login_at ? new Date(u.last_login_at).toLocaleString() : "never"}
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center justify-end gap-2 text-xs">
                    <button
                      onClick={() => toggleRole(u)}
                      className="text-slate-400 hover:text-slate-100 transition-colors"
                    >
                      Make {u.role === "admin" ? "member" : "admin"}
                    </button>
                    <span className="text-slate-700">·</span>
                    <button
                      onClick={() => toggleActive(u)}
                      className={
                        u.is_active
                          ? "text-red-400 hover:text-red-300 transition-colors"
                          : "text-green-400 hover:text-green-300 transition-colors"
                      }
                    >
                      {u.is_active ? "Deactivate" : "Activate"}
                    </button>
                    <span className="text-slate-700">·</span>
                    <button
                      onClick={() => setResetTarget(resetTarget === u.id ? null : u.id)}
                      className="text-slate-400 hover:text-slate-100 transition-colors"
                    >
                      Reset password
                    </button>
                  </div>
                  {resetTarget === u.id && (
                    <form onSubmit={handleResetPassword} className="flex items-center gap-2 mt-2">
                      <input
                        type="password"
                        required
                        autoFocus
                        placeholder="New password"
                        value={resetPassword}
                        onChange={(e) => setResetPassword(e.target.value)}
                        autoComplete="new-password"
                        className="flex-1 bg-slate-800 border border-slate-700 rounded-md px-2 py-1 text-xs text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
                      />
                      <button
                        type="submit"
                        className="bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium px-2 py-1 rounded-md transition-colors"
                      >
                        Save
                      </button>
                    </form>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
