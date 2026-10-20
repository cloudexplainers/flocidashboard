export function normalizeLoadBalancing(raw: any) {
  const load_balancers = (raw.load_balancers || raw.v2_load_balancers || []).map((item: any) => ({ ...item,
    LoadBalancerName: item.LoadBalancerName ?? item.name, LoadBalancerArn: item.LoadBalancerArn ?? item.arn,
    DNSName: item.DNSName ?? item.dns_name, State: item.State ?? item.state, Type: item.Type ?? item.type, Scheme: item.Scheme ?? item.scheme, VpcId: item.VpcId ?? item.vpc_id,
  }));
  const target_groups = (raw.target_groups || []).map((item: any) => ({ ...item, TargetGroupName: item.TargetGroupName ?? item.name, TargetGroupArn: item.TargetGroupArn ?? item.arn, Protocol: item.Protocol ?? item.protocol, Port: item.Port ?? item.port, TargetType: item.TargetType ?? item.target_type, HealthCheckPath: item.HealthCheckPath ?? item.health_check_path, VpcId: item.VpcId ?? item.vpc_id }));
  const listeners = (raw.listeners || []).map((item: any) => ({ ...item, ListenerArn: item.ListenerArn ?? item.arn, Protocol: item.Protocol ?? item.protocol, Port: item.Port ?? item.port, DefaultActions: item.DefaultActions ?? item.default_actions, LoadBalancerArn: item.LoadBalancerArn ?? load_balancers.find((lb: any) => lb.LoadBalancerName === item.load_balancer_name)?.LoadBalancerArn }));
  const targets = (raw.target_health || []).flatMap((set: any) => (set.target_health_descriptions || []).map((item: any) => ({ ...item, TargetGroupArn: set.target_group_arn })));
  return { ...raw, load_balancers, target_groups, listeners, targets: targets.length || raw.target_health ? targets : raw.targets || [] };
}
