export interface KmsKey {
  id: string;
  arn?: string;
  description?: string;
  aliases: string[];
  state?: string;
  spec?: string;
  usage?: string;
  origin?: string;
  manager?: string;
  created?: string | number;
  deletionDate?: string | number;
  multiRegion?: boolean;
  replica: boolean;
  rotation?: boolean;
  policy?: string;
  grants: any[];
  tags: { TagKey: string; TagValue: string }[];
  issues: string[];
}
export function kmsKeys(data: any): KmsKey[] {
  return (data.keys || []).map((key: any) => {
    const metadata = key.metadata || key;
    const id = key.key_id || metadata.KeyId || key.name;
    const aliases = Array.isArray(key.aliases)
      ? key.aliases
      : (data.aliases || []).filter(
          (alias: any) => (alias.TargetKeyId || alias.target_key_id) === id,
        );
    const policy = key.policy;
    return {
      id,
      arn: metadata.Arn || key.key_arn || key.arn,
      description: metadata.Description ?? key.description,
      aliases: aliases.map((alias: any) => alias.AliasName || alias.alias_name),
      state:
        metadata.KeyState ||
        key.key_state ||
        (metadata.Enabled === true
          ? "Enabled"
          : metadata.Enabled === false
            ? "Disabled"
            : undefined),
      spec: metadata.KeySpec || metadata.CustomerMasterKeySpec || key.key_spec,
      usage: metadata.KeyUsage || key.key_usage,
      origin: metadata.Origin,
      manager: metadata.KeyManager,
      created: metadata.CreationDate,
      deletionDate: metadata.DeletionDate,
      multiRegion: metadata.MultiRegion,
      replica:
        metadata.MultiRegionConfiguration?.MultiRegionKeyType === "REPLICA",
      rotation:
        typeof key.rotation_enabled === "boolean"
          ? key.rotation_enabled
          : undefined,
      policy:
        policy && !policy.error
          ? typeof policy === "string"
            ? policy
            : JSON.stringify(policy, null, 2)
          : undefined,
      grants: Array.isArray(key.grants) ? key.grants : [],
      tags: Array.isArray(key.tags) ? key.tags : [],
      issues: [
        metadata,
        key.aliases,
        key.policy,
        key.rotation_enabled,
        key.grants,
        key.tags,
      ]
        .filter((value) => value?.error)
        .map((value) => String(value.error)),
    };
  });
}
export function kmsDate(value?: string | number): string {
  if (value == null || value === "") return "Unavailable";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? "Unavailable" : date.toLocaleString();
}
export const canRotateKmsKey = (key: KmsKey) =>
  key.state === "Enabled" &&
  key.spec === "SYMMETRIC_DEFAULT" &&
  key.usage === "ENCRYPT_DECRYPT" &&
  key.origin === "AWS_KMS" &&
  key.manager === "CUSTOMER" &&
  !key.replica;
export const canUseKmsTools = (key: KmsKey) =>
  key.state === "Enabled" &&
  key.spec === "SYMMETRIC_DEFAULT" &&
  key.usage === "ENCRYPT_DECRYPT";
