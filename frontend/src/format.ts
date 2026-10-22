/** Formatting helpers that match how the AWS Management Console displays values. */

/** Byte counts as the S3 console shows them: "38.0 B", "1.2 KB", "5.0 MB". Uses 1024-based units. */
export function formatBytes(value: number | null | undefined): string {
  if (value == null || Number.isNaN(Number(value))) return '-';
  let size = Number(value);
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(1)} ${units[unit]}`;
}

/** Timestamps in the console's long form, e.g. "October 22, 2026, 22:05:05 (UTC)". Returns '-' when absent. */
export function formatAwsDate(value: string | number | Date | null | undefined): string {
  if (value == null || value === '') return '-';
  const date = typeof value === 'number' && value < 1e12 ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const datePart = date.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
  const timePart = date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const offset = -date.getTimezoneOffset();
  const zone = offset === 0 ? 'UTC' : `UTC${offset > 0 ? '+' : '-'}${String(Math.floor(Math.abs(offset) / 60)).padStart(2, '0')}:${String(Math.abs(offset) % 60).padStart(2, '0')}`;
  return `${datePart}, ${timePart} (${zone})`;
}
