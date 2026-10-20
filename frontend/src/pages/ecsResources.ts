export const clusterName = (item: any): string => item.clusterName || item.cluster_name || item.name || '';
export const serviceName = (item: any): string => item.serviceName || item.service_name || '';
export const taskArn = (item: any): string => item.taskArn || item.task_arn || '';
export const taskId = (item: any): string => taskArn(item).split('/').pop() || '';
export const clusterPath = (name: string) => `ecs/clusters/${encodeURIComponent(name)}`;
export const servicePath = (cluster: string, service: string) => `${clusterPath(cluster)}/services/${encodeURIComponent(service)}`;
export const taskPath = (cluster: string, task: any, service?: string) => `${service ? servicePath(cluster, service) : clusterPath(cluster)}/tasks/${encodeURIComponent(taskId(task))}`;
export function parseEcsRoute(path: string) {
  let parts: string[];
  try { parts = path.split('?')[0].replace(/^\//, '').split('/').map(decodeURIComponent); }
  catch { return { invalid: true }; }
  const [, section, cluster, kind, resource, nested, task] = parts;
  if (section !== 'clusters' || !cluster) return {};
  if (!kind && parts.length === 3) return { cluster };
  if (kind === 'tasks' && resource && parts.length === 5) return { cluster, task: resource };
  if (kind === 'services' && resource) {
    if (!nested && parts.length === 5) return { cluster, service: resource };
    if (nested === 'tasks' && task && parts.length === 7) return { cluster, service: resource, task };
  }
  return { cluster, invalid: true };
}
export function normalizeEcsInventory(raw: any) {
  const clusters = (raw.clusters || []).map((cluster: any) => ({ ...cluster, clusterName: clusterName(cluster), clusterArn: cluster.clusterArn || cluster.cluster_arn || cluster.arn }));
  const resources = (key: string) => {
    const combined = [...(raw[key] || []), ...clusters.flatMap((cluster: any) => (cluster[key] || []).map((item: any) => ({ ...item, clusterArn: item.clusterArn || cluster.clusterArn || cluster.clusterName })))];
    return [...new Map(combined.map((item: any) => [item.serviceArn || taskArn(item) || item.containerInstanceArn || `${item.clusterArn}:${serviceName(item)}`, item])).values()];
  };
  return { ...raw, clusters, services: resources('services'), tasks: resources('tasks'), container_instances: resources('container_instances'), task_definitions: (raw.task_definitions || []).map((item: any) => ({ ...item, taskDefinitionArn: item.taskDefinitionArn || item.arn, compatibilities: item.compatibilities || item.requires_compatibilities })) };
}
