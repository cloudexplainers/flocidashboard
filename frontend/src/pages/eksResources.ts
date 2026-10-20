export const eksClusterPath = (cluster: string) => `eks/clusters/${encodeURIComponent(cluster)}`;
export const eksNodegroupPath = (cluster: string, nodegroup: string) => `${eksClusterPath(cluster)}/nodegroups/${encodeURIComponent(nodegroup)}`;
export function parseEksRoute(path: string) {
  try {
    const parts = path.split('?')[0].replace(/^\//, '').split('/').map(decodeURIComponent);
    if (parts[1] !== 'clusters' || !parts[2]) return {};
    if (parts.length === 3) return { cluster: parts[2] };
    if (parts.length === 5 && parts[3] === 'nodegroups' && parts[4]) return { cluster: parts[2], nodegroup: parts[4] };
    return { cluster: parts[2], invalid: true };
  } catch { return { invalid: true }; }
}
export function normalizeEksClusters(raw: any): any[] {
  return (raw.clusters || []).map((value: any) => {
    const cluster = typeof value === 'string' ? { name: value } : value;
    const name = cluster.name || cluster.Name;
    const refs = cluster.nodegroups || [];
    const details = (raw.nodegroups || []).filter((item: any) => (item.cluster_name || item.clusterName) === name);
    const nodegroups = [...new Map([...refs.map((item: any) => typeof item === 'string' ? { name: item } : item), ...details].map((item: any) => {
      const name = item.nodegroupName || item.name;
      return [name, { ...item, name, nodegroupName: name, scalingConfig: item.scalingConfig ?? item.scaling_config, instanceTypes: item.instanceTypes ?? item.instance_types, nodeRole: item.nodeRole ?? item.node_role, capacityType: item.capacityType ?? item.capacity_type, diskSize: item.diskSize ?? item.disk_size }];
    })).values()];
    const profiles = (raw.fargate_profiles || []).filter((item: any) => (item.cluster_name || item.clusterName) === name);
    const fargate_profiles = [...new Map([...(cluster.fargate_profiles || []).map((item: any) => typeof item === 'string' ? { name: item } : item), ...profiles].map((item: any) => [item.fargateProfileName || item.name, { ...item, fargateProfileName: item.fargateProfileName || item.name, podExecutionRoleArn: item.podExecutionRoleArn ?? item.pod_execution_role_arn }])).values()];
    return { ...cluster, name, arn: cluster.arn || cluster.Arn, status: cluster.status || cluster.Status || 'Unknown', version: cluster.version || cluster.Version, roleArn: cluster.roleArn ?? cluster.role_arn, createdAt: cluster.createdAt ?? cluster.created_at, resourcesVpcConfig: cluster.resourcesVpcConfig ?? cluster.resources_vpc_config, platformVersion: cluster.platformVersion ?? cluster.platform_version, nodegroups, fargate_profiles };
  });
}
