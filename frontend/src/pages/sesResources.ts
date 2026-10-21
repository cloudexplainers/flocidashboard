export interface SesIdentity {
  name: string;
  type: "Email address" | "Domain";
  status?: string;
  verified?: boolean;
  token?: string;
  dkim?: {
    DkimEnabled?: boolean;
    DkimVerificationStatus?: string;
    DkimTokens?: string[];
    error?: string;
  };
  notifications?: Record<string, any>;
}
export function sesIdentities(data: any): SesIdentity[] {
  const identities = new Map<string, SesIdentity>();
  const v2 = Array.isArray(data.v2_identities) ? data.v2_identities : [];
  [...(Array.isArray(data.identities) ? data.identities : []), ...v2].forEach(
    (item: any) => {
      const name =
        typeof item === "string"
          ? item
          : item.name || item.identity || item.IdentityName;
      if (!name) return;
      const previous = identities.get(name);
      const status =
        item.verification?.VerificationStatus ||
        item.VerificationStatus ||
        item.status ||
        previous?.status;
      identities.set(name, {
        name,
        type: name.includes("@") ? "Email address" : "Domain",
        status,
        verified: status
          ? ["SUCCESS", "VERIFIED"].includes(status.toUpperCase())
          : typeof item.VerifiedForSendingStatus === "boolean"
            ? item.VerifiedForSendingStatus
            : previous?.verified,
        token: item.verification?.VerificationToken || previous?.token,
        dkim: item.dkim || previous?.dkim,
        notifications: item.notifications || previous?.notifications,
      });
    },
  );
  return [...identities.values()];
}
export function sesStatus(identity: SesIdentity): string {
  return identity.verified === true
    ? "Verified"
    : identity.status ||
        (identity.verified === false ? "Not verified" : "Unavailable");
}
export function sesDate(value?: string | number): string {
  if (value == null || value === "") return "Unavailable";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? "Unavailable" : date.toLocaleString();
}
export const validSesEmail = (value: string) =>
  /^[^\s@,]+@[^\s@,]+\.[^\s@,]+$/.test(value);
export function verifiedSesSender(address: string, identities: SesIdentity[]) {
  const email = address.toLowerCase();
  const domain = email.split("@")[1];
  return (
    validSesEmail(address) &&
    identities.some(
      (identity) =>
        identity.verified === true &&
        (identity.type === "Email address"
          ? identity.name.toLowerCase() === email
          : domain === identity.name.toLowerCase() ||
            domain?.endsWith(`.${identity.name.toLowerCase()}`)),
    )
  );
}
export async function sesRequest(url: string) {
  const response = await fetch(url);
  const result = await response.json();
  if (!response.ok || result.error)
    throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}
export function sesRecipients(value: any): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.join(", ");
  if (value && typeof value === "object")
    return [
      ...(value.ToAddresses || []),
      ...(value.CcAddresses || []),
      ...(value.BccAddresses || []),
    ].join(", ");
  return "Unavailable";
}
export function sesMailboxMessage(message: any) {
  const content = message.Message || message.message || {};
  const body = content.Body || message.Body || message.body;
  const text =
    body?.Text?.Data ??
    body?.text ??
    message.text ??
    (typeof body === "string" ? body : undefined);
  const html = body?.Html?.Data ?? body?.html ?? message.html;
  return {
    id: message.id || message.Id || message.messageId || message.MessageId,
    source: message.source || message.Source || message.from || message.From,
    to: sesRecipients(
      message.destination || message.Destination || message.to || message.To,
    ),
    subject: message.subject ?? message.Subject ?? content.Subject?.Data,
    timestamp: message.timestamp || message.Timestamp,
    text: typeof text === "string" ? text : undefined,
    html: typeof html === "string" ? html : undefined,
    raw: message,
  };
}
