import { IdentityInfo } from "./types";

/** The API normalizes STS fields to lowercase; accept AWS-shaped callers as well. */
export function normalizeSessionIdentity(data: any): IdentityInfo {
  const caller = data.caller_identity || {};
  const account = caller.account ?? caller.Account ?? data.account_id;
  const arn = caller.arn ?? caller.Arn ?? data.arn;
  const userId = caller.user_id ?? caller.UserId ?? data.user_id;
  const resolved = data.identity_resolved !== false && !!(account && arn);
  return {
    account_id: resolved ? account : "Unknown",
    user_id: resolved ? userId || "Unavailable" : "Unavailable",
    arn: resolved ? arn : "Unavailable",
    role:
      data.session_identity?.label ||
      data.identity_label ||
      data.role ||
      "Configured credentials",
    region: data.region || "Unknown",
    endpoint: data.endpoint_url || data.endpoint || "Unavailable",
    resolved,
    error: data.identity_error,
    expires_at: data.session_identity?.expires_at || data.identity_expires_at,
  };
}
export function sessionExpiration(
  value: string | null | undefined,
  now = Date.now(),
) {
  if (!value) return { label: "Not reported", expired: false, soon: false };
  const expiration = new Date(value).getTime();
  if (!Number.isFinite(expiration))
    return { label: "Unavailable", expired: false, soon: false };
  const remaining = expiration - now;
  return {
    label: new Date(expiration).toLocaleString(),
    expired: remaining <= 0,
    soon: remaining > 0 && remaining < 15 * 60_000,
  };
}
