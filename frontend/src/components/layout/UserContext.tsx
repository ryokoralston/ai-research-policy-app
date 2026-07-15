"use client";

import { createContext, useContext, useState } from "react";

export type CurrentUser = { id: string; email: string; role: "admin" | "member" };

const UserContext = createContext<{
  user: CurrentUser | null;
  setUser: (u: CurrentUser | null) => void;
}>({ user: null, setUser: () => {} });

/** The logged-in user's own profile, populated by AuthGuard once it confirms
 * a valid session. null while unauthenticated or before the /me fetch. */
export function useCurrentUser() {
  return useContext(UserContext).user;
}

export function UserProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  return (
    <UserContext.Provider value={{ user, setUser }}>{children}</UserContext.Provider>
  );
}

export function useSetCurrentUser() {
  return useContext(UserContext).setUser;
}
