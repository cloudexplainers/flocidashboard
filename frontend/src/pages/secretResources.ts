export interface SecretVersion {
  id: string;
  stages: string[];
  created?: string | number;
  accessed?: string | number;
}
export interface SecretResource {
  id: string;
  name: string;
  arn?: string;
  description: string;
  kmsKey?: string;
  created?: string | number;
  changed?: string | number;
  accessed?: string | number;
  deleted?: string | number;
  rotation?: boolean;
  rotationLambda?: string;
  rotationRules: Record<string, any>;
  rotated?: string | number;
  nextRotation?: string | number;
  tags: { Key: string; Value: string }[];
  versions: SecretVersion[];
  versionsError?: string;
}
export function secretResources(data: any): SecretResource[] {
  return (Array.isArray(data.secrets) ? data.secrets : []).map((s: any) => {
    const stages =
      s.version_ids_to_stages ||
      s.VersionIdsToStages ||
      s.SecretVersionsToStages ||
      {};
    const versions: SecretVersion[] = (
      Array.isArray(s.versions) ? s.versions : []
    ).map((v: any) => ({
      id: v.VersionId || v.version_id,
      stages: v.VersionStages || v.version_stages || [],
      created: v.CreatedDate || v.created_date,
      accessed: v.LastAccessedDate || v.last_accessed_date,
    }));
    Object.entries(stages).forEach(([id, labels]) => {
      const existing = versions.find((v) => v.id === id);
      if (existing)
        existing.stages = Array.isArray(labels) ? labels : existing.stages;
      else versions.push({ id, stages: Array.isArray(labels) ? labels : [] });
    });
    return {
      id: s.arn || s.ARN || s.name || s.Name,
      name: s.name || s.Name,
      arn: s.arn || s.ARN,
      description: s.description ?? s.Description ?? "",
      kmsKey: s.kms_key_id || s.KmsKeyId,
      created: s.created ?? s.CreatedDate,
      changed: s.last_changed ?? s.LastChangedDate,
      accessed: s.last_accessed ?? s.LastAccessedDate,
      deleted: s.deleted ?? s.DeletedDate,
      rotation: s.rotation_enabled ?? s.RotationEnabled,
      rotationLambda: s.rotation_lambda_arn || s.RotationLambdaARN,
      rotationRules: s.rotation_rules || s.RotationRules || {},
      rotated: s.last_rotated ?? s.LastRotatedDate,
      nextRotation: s.next_rotation ?? s.NextRotationDate,
      tags: s.tags || s.Tags || [],
      versions,
      versionsError: s.versions?.error,
    };
  });
}
export function secretDate(value?: string | number): string {
  if (value == null || value === "") return "—";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}
export const secretPath = (name: string) =>
  `secretsmanager/details?secret=${encodeURIComponent(name)}`;
