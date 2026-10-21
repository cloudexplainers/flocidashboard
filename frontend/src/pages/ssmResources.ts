export interface SsmParameter {
  name: string;
  type?: string;
  version?: number;
  modified?: string | number;
  modifiedBy?: string;
  description?: string;
  keyId?: string;
  tier?: string;
  dataType?: string;
  arn?: string;
}
export function ssmParameters(data: any): SsmParameter[] {
  return (data.parameters || data.Parameters || []).map((p: any) => ({
    name: p.name ?? p.Name,
    type: p.type ?? p.Type,
    version: p.version ?? p.Version,
    modified: p.last_modified ?? p.LastModifiedDate,
    modifiedBy: p.last_modified_user ?? p.LastModifiedUser,
    description: p.description ?? p.Description,
    keyId: p.key_id ?? p.KeyId,
    tier: p.tier ?? p.Tier,
    dataType: p.data_type ?? p.DataType,
    arn: p.arn ?? p.ARN,
  }));
}
export function ssmDate(value?: string | number) {
  if (value == null || value === "") return "Unavailable";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? "Unavailable" : date.toLocaleString();
}
export const parameterUrl = (name: string) =>
  `/api/ssm/parameters/${encodeURIComponent(name)}/value/`;
export async function ssmRequest(
  url: string,
  method = "GET",
  body?: Record<string, unknown>,
) {
  const csrf =
    document
      .querySelector('meta[name="csrf-token"]')
      ?.getAttribute("content") ||
    document.cookie.match(/csrftoken=([^;]+)/)?.[1] ||
    "";
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const result = await response.json();
  if (!response.ok || result.error)
    throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}
