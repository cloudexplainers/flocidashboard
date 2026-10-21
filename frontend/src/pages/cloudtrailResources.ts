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
