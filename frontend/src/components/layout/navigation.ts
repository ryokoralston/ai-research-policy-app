import {
  Search,
  FileText,
  BookOpen,
  Shield,
  Users,
  Mail,
  Settings,
  FlaskConical,
  UserCog,
  History,
  Drama,
} from "lucide-react";

export type NavItem = {
  href: string;
  label: string;
  icon: React.ElementType;
};

/**
 * Shared by the Sidebar (which lists the feature pages) and the AccountMenu
 * (which holds the admin tools and Settings). Kept in its own module so those
 * two don't have to import each other.
 */

/** Feature pages — always listed in the sidebar. */
export const NAV_ITEMS: NavItem[] = [
  { href: "/research", label: "Research", icon: Search },
  { href: "/reports", label: "Reports", icon: FileText },
  { href: "/library", label: "Library", icon: BookOpen },
  { href: "/analysis", label: "Risk Analysis", icon: Shield },
  { href: "/datalab", label: "Data Lab", icon: FlaskConical },
  { href: "/debate", label: "Debate", icon: Users },
  { href: "/digest", label: "Daily Digest", icon: Mail },
];

/** Admin-only tools — surfaced in the account menu, not the main nav. */
export const ADMIN_NAV_ITEMS: NavItem[] = [
  { href: "/activity-log", label: "Activity Log", icon: History },
  { href: "/personas", label: "Personas", icon: Drama },
  { href: "/users", label: "Users", icon: UserCog },
];

export const SETTINGS_NAV_ITEM: NavItem = {
  href: "/settings",
  label: "Settings",
  icon: Settings,
};
