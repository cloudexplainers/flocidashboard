export function eventDocument(event: any): { document: Record<string, any>; error?: string } {
  const raw = event.CloudTrailEvent;
  if (raw == null) return { document: {} };
  if (typeof raw === 'object' && !Array.isArray(raw)) return { document: raw };
  try { const document = JSON.parse(raw); if (!document || typeof document !== 'object' || Array.isArray(document)) throw new Error(); return { document }; }
  catch { return { document: {}, error: 'CloudTrail returned an event body that could not be parsed. The original value is shown below.' }; }
}
export function trailLogging(trail: any): string {
  return trail.status?.IsLogging === true ? 'Logging' : trail.status?.IsLogging === false ? 'Stopped' : 'Not reported';
}
export const trailPath = (trail: string, tab = 'overview') => `cloudtrail/trails?${new URLSearchParams({ trail, tab })}`;
/** Who made a call in a delivered log record: the principal ARN, else the user name, principal ID or identity type. */
export function logRecordUser(record: any): string | undefined {
  const identity = record?.userIdentity;
  if (!identity || typeof identity !== 'object') return undefined;
  return identity.arn || identity.userName || identity.principalId || identity.type || undefined;
}
/** Resource ARNs listed in a delivered log record. */
export function logRecordResources(record: any): string | undefined {
  const resources = Array.isArray(record?.resources) ? record.resources : [];
  const names = resources.map((item: any) => item?.ARN || item?.arn || item?.type).filter(Boolean);
  return names.length ? names.join(', ') : undefined;
}
