# Known and accepted risks

Reviewed and deliberately accepted. The audit confirms each is **still true**
and reports them in the "known" bucket — it does not re-raise them as new
findings. If one stops being true, that IS a new finding.

Remove an entry when it is actually fixed. Add one only after the repo owner
has decided to accept it, with the date and the reasoning.

---

### Session tokens cannot be revoked
*Accepted 2026-08-19.* The bearer token's payload is just `{"uid": ...}`, so
there is no "sign out everywhere". Combined with localStorage storage, any XSS
would mean a permanently stolen session — which is why the XSS surface is
audited hard and why `script-src` no longer allows `'unsafe-inline'`.
**Revisit when:** multi-tenant B2B customers arrive, or if any XSS is ever found.

### The token signing key is the secret-at-rest key
*Accepted 2026-06.* `services/auth.py` signs tokens with the same Fernet key
that encrypts stored API keys, so the two cannot be rotated independently.
`backend/scripts/rotate_secret_key.py` exists for the at-rest half and has
**not** been run against production. Never swap `SECRET_ENCRYPTION_KEY` in
place — `decrypt_secret()` returns the stored ciphertext unchanged on a key
mismatch, so the failure is silent.

### `style-src 'unsafe-inline'`
*Accepted 2026-08-19.* styled-jsx and Next's injected `<style>` tags are not
nonced. `script-src` was tightened to a per-request nonce; style was not, and
the XSS value of tightening it is much lower.

### The standalone MCP server is unscoped
*Accepted 2026-08-19.* `mcp_server.py` scopes documents by the
`MCP_ACTING_USER_ID` env var. The app's bridge always sets it, so no HTTP
request path is unscoped. Run directly (Claude Desktop, `mcp_client.py`) with
the var absent, it reads every user's documents — acceptable because that is a
local stdio process run by the developer against their own machine.
**Revisit when:** the MCP server is ever exposed over a network transport.

### `org_id` is stored but not filtered on
*By design 2026-08-19.* Every owned table carries `org_id` alongside `user_id`,
but filtering is by `user_id` alone (one org per user today). The column exists
so org-tenancy is not a second schema migration. An audit should confirm
`org_id` is still being *populated* on insert — a NULL there is a latent bug,
not an access-control hole today.

### Admins cannot see other users' data
*By design 2026-08-19, not a gap.* Admin power is confined to the `users`,
`audit_log` and `admin_personas` routers plus settings writes. If an audit finds
an `if admin:` branch that skips an ownership filter, that is a **finding** —
someone reintroduced the bypass this decision rules out.

### Production holds no content
*Observed 2026-08-19.* Prod has accounts but zero documents/reports/sessions —
data was lost in the 2026-07-16 Render incident and deliberately not restored
(it was sample material). Do not read "prod is empty" as a fresh incident.
**Revisit when:** the document library is re-uploaded; the empty state currently
masks whether disk persistence is genuinely working end to end.
