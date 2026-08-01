import { redirect } from "next/navigation";

/**
 * The dashboard used to live here, but it was a grid of links that mirrored the
 * sidebar exactly — and once Recent moved into the sidebar too, it carried
 * nothing the nav didn't already show. "/" now just hands off to the primary
 * workflow. Keeping the route (rather than deleting it) means every existing
 * `router.replace("/")` — login, and the admin pages bouncing non-admins —
 * still has one place that defines where "home" is.
 */
export default function Home() {
  redirect("/research");
}
