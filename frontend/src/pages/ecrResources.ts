/** Normalize the nested inventory response and older AWS-shaped fixtures at the boundary. */
export interface EcrImage {
  digest?: string;
  tags: string[];
  size?: number;
  pushed?: string | number;
  mediaType?: string;
  scanStatus?: string;
}
export interface EcrRepository {
  name: string;
  arn?: string;
  uri?: string;
  registryId?: string;
  created?: string | number;
  mutability?: string;
  encryption?: string;
  scanOnPush?: boolean;
  tags: { Key: string; Value: string }[];
  lifecycle?: string;
  policy?: string;
  images: EcrImage[];
  issues: string[];
}
export function ecrRepositories(data: any): EcrRepository[] {
  return (data.repositories || []).map((repo: any) => {
    const name = repo.name || repo.repositoryName;
    const rawDetails = repo.image_details ?? repo.imageDetails;
    const details = Array.isArray(rawDetails) ? rawDetails : [];
    const rawIds =
      repo.images ??
      (data.images || []).filter(
        (image: any) => (image.repositoryName || image.repository) === name,
      );
    const ids = Array.isArray(rawIds) ? rawIds : [];
    const images = new Map<string, EcrImage>();
    [...details, ...ids].forEach((image: any, index: number) => {
      const digest = image.imageDigest;
      const tags: string[] =
        image.imageTags ?? (image.imageTag ? [image.imageTag] : []);
      const key = digest || tags[0] || `unknown-${index}`;
      const previous = images.get(key);
      images.set(key, {
        digest,
        tags: [...new Set([...(previous?.tags || []), ...tags])],
        size: previous?.size ?? image.imageSizeInBytes,
        pushed: previous?.pushed ?? image.imagePushedAt,
        mediaType: previous?.mediaType ?? image.imageManifestMediaType,
        scanStatus: previous?.scanStatus ?? image.imageScanStatus?.status,
      });
    });
    return {
      name,
      arn: repo.arn || repo.repositoryArn,
      uri: repo.uri || repo.repositoryUri,
      registryId: repo.registry_id || repo.registryId,
      created: repo.created ?? repo.createdAt,
      mutability: repo.tag_mutability || repo.imageTagMutability,
      encryption: (
        repo.encryption_configuration || repo.encryptionConfiguration
      )?.encryptionType,
      scanOnPush: (
        repo.scanning_configuration || repo.imageScanningConfiguration
      )?.scanOnPush,
      tags: Array.isArray(repo.tags) ? repo.tags : [],
      lifecycle: repo.lifecycle_policy?.lifecyclePolicyText,
      policy: repo.repository_policy?.policyText,
      images: [...images.values()],
      issues: [
        rawDetails,
        rawIds,
        repo.lifecycle_policy,
        repo.repository_policy,
        repo.tags,
      ]
        .filter((value) => value?.error)
        .map((value) => String(value.error)),
    };
  });
}
export function ecrDate(value?: string | number): string {
  if (value == null || value === "") return "Unavailable";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? "Unavailable" : date.toLocaleString();
}
export function ecrSize(bytes?: number): string {
  return bytes == null
    ? "Unavailable"
    : `${(bytes / 1024 / 1024).toFixed(2)} MiB`;
}
export const shellQuote = (value: string) =>
  `'${value.replace(/'/g, `'"'"'`)}'`;
