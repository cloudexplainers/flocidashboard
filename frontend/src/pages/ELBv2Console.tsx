import { normalizeLoadBalancing } from './deliveryResources';
import { TargetGroupHealth } from '../components/TargetGroupHealth';
import { ListenerEditModal, ListenerRules, TargetGroupAttributes, TargetGroupHealthChecks, describeAction } from '../components/ELBv2Editors';
import { ResourceValue } from '../components/ResourceDetails';
import React, { useState, useEffect, useMemo } from 'react';
import Table from '../components/ResourceTable';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Container from '@cloudscape-design/components/container';
import Modal from '@cloudscape-design/components/modal';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Select from '@cloudscape-design/components/select';
import Grid from '@cloudscape-design/components/grid';
import Badge from '@cloudscape-design/components/badge';
import Alert from '@cloudscape-design/components/alert';
import TextFilter from '@cloudscape-design/components/text-filter';
import Tabs from '@cloudscape-design/components/tabs';
import Link from '@cloudscape-design/components/link';
import {
  createElbv2LoadBalancer,
  deleteElbv2LoadBalancer,
  createElbv2TargetGroup,
  deleteElbv2TargetGroup,
  registerElbv2Targets,
  deregisterElbv2Targets,
  createElbv2Listener,
  deleteElbv2Listener,
} from '../api/client';

interface ELBv2ConsoleProps {
  activeTab?: string;
  resourcePath?: string;
  onNavigate?: (path: string) => void;
  onTabChange?: (tabId: string) => void;
}

export const ELBv2Console: React.FC<ELBv2ConsoleProps> = ({ activeTab, onTabChange, resourcePath = 'elasticloadbalancing', onNavigate }) => {
  const query = new URLSearchParams(resourcePath.split('?')[1] || '');
  const pinned = query.get('lb');
  const [loadError, setLoadError] = useState('');
  const showTab = (tab: string, values?: Record<string, string>) => {
    const next = new URLSearchParams(resourcePath.split('?')[1] || '');
    Object.entries(values || {}).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key));
    if (onNavigate) onNavigate(`elasticloadbalancing/${tab}${next.size ? '?' + next : ''}`); else onTabChange?.(tab);
  };
  const [data, setData] = useState<any>({
    load_balancers: [],
    target_groups: [],
    listeners: [],
    targets: [],
  });
  const [loading, setLoading] = useState(true);
  const [filterText, setFilterText] = useState('');
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Selections
  const [selectedLbs, setSelectedLbs] = useState<any[]>([]);
  const [selectedTgs, setSelectedTgs] = useState<any[]>([]);

  // Create LB Modal
  const [createLbOpen, setCreateLbOpen] = useState(false);
  const [lbName, setLbName] = useState('');
  const [lbSubnets, setLbSubnets] = useState('');
  const [lbType, setLbType] = useState<{ label: string; value: 'application' | 'network' }>({ label: 'Application Load Balancer (HTTP/HTTPS)', value: 'application' });
  const [lbScheme, setLbScheme] = useState<{ label: string; value: 'internet-facing' | 'internal' }>({ label: 'internet-facing', value: 'internet-facing' });
  const [creatingLb, setCreatingLb] = useState(false);

  // Create Target Group Modal
  const [createTgOpen, setCreateTgOpen] = useState(false);
  const [tgName, setTgName] = useState('');
  const [tgProtocol, setTgProtocol] = useState({ label: 'HTTP', value: 'HTTP' });
  const [tgPort, setTgPort] = useState('80');
  const [tgTargetType, setTgTargetType] = useState<{ label: string; value: 'instance' | 'ip' | 'lambda' }>({ label: 'Instances (EC2)', value: 'instance' });
  const [tgHealthPath, setTgHealthPath] = useState('/health');
  const [creatingTg, setCreatingTg] = useState(false);

  // Register Targets Modal
  const [registerTargetOpen, setRegisterTargetOpen] = useState(false);
  const [targetId, setTargetId] = useState('');
  const [targetPort, setTargetPort] = useState('80');
  const [registeringTarget, setRegisteringTarget] = useState(false);

  // Create Listener Modal
  const [createListenerOpen, setCreateListenerOpen] = useState(false);
  const [listenerProtocol, setListenerProtocol] = useState({ label: 'HTTP', value: 'HTTP' });
  const [listenerPort, setListenerPort] = useState('80');
  const [listenerTgArn, setListenerTgArn] = useState('');
  const [creatingListener, setCreatingListener] = useState(false);
  const [editingListener, setEditingListener] = useState<any | null>(null);
  const [selectedListenerArn, setSelectedListenerArn] = useState('');
  const notify = (type: 'success' | 'error', text: string) => setActionMessage({ type, text });

  const loadData = async () => {
    setLoading(true); setLoadError('');
    try {
      const response = await fetch('/api/elasticloadbalancing/');
      const payload = await response.json();
      if (!response.ok || payload.error) throw new Error(payload.error || 'Inventory unavailable');
      const res = normalizeLoadBalancing(payload);
      setData(
        res || {
          load_balancers: [],
          target_groups: [],
          listeners: [],
          targets: [],
        }
      );
      if (res?.load_balancers?.length > 0 && selectedLbs.length === 0) {
        setSelectedLbs([res.load_balancers[0]]);
      }
      if (res?.target_groups?.length > 0 && selectedTgs.length === 0) {
        setSelectedTgs([res.target_groups[0]]);
        setListenerTgArn(res.target_groups[0].TargetGroupArn || res.target_groups[0].target_group_arn || '');
      }
    } catch (err: any) {
      setLoadError(err.message || 'Inventory unavailable');
      setData({});
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const activeLb = (data.load_balancers || []).find((item: any) => item.LoadBalancerArn === (pinned || selectedLbs[0]?.LoadBalancerArn)) || null;
  const activeLbArn = activeLb ? activeLb.LoadBalancerArn || activeLb.load_balancer_arn : '';

  const pinnedTg = query.get('tg');
  const activeTg = (data.target_groups || []).find((item: any) => item.TargetGroupArn === (pinnedTg || selectedTgs[0]?.TargetGroupArn)) || null;
  const activeTgArn = activeTg ? activeTg.TargetGroupArn || activeTg.target_group_arn : '';

  const filteredLbs = useMemo(() => {
    const list = data.load_balancers || [];
    if (!filterText) return list;
    return list.filter((lb: any) =>
      (lb.LoadBalancerName || lb.name || '').toLowerCase().includes(filterText.toLowerCase())
    );
  }, [data.load_balancers, filterText]);

  const lbListeners = useMemo(() => {
    const all = data.listeners || [];
    if (!activeLbArn) return pinned ? [] : all;
    return all.filter((l: any) => l.LoadBalancerArn === activeLbArn);
  }, [data.listeners, activeLbArn, pinned]);

  const activeListener = lbListeners.find((item: any) => item.ListenerArn === selectedListenerArn) || lbListeners[0] || null;

  const groupTargets = (data.targets || []).filter((item: any) => item.TargetGroupArn === activeTgArn);

  // Actions
  const handleCreateLb = async () => {
    if (!lbName.trim()) return;
    setCreatingLb(true);
    try {
      await createElbv2LoadBalancer({
        name: lbName.trim(),
        type: lbType.value,
        subnets: lbSubnets.split(',').map(value => value.trim()).filter(Boolean),
        scheme: lbScheme.value,
      });
      setActionMessage({ type: 'success', text: `Load Balancer "${lbName.trim()}" created.` });
      setCreateLbOpen(false);
      setLbName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create load balancer' });
    } finally {
      setCreatingLb(false);
    }
  };

  const handleDeleteLb = async (arn: string) => {
    if (!confirm('Are you sure you want to delete this load balancer?')) return;
    try {
      await deleteElbv2LoadBalancer(arn);
      setActionMessage({ type: 'success', text: 'Load balancer deleted.' });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete load balancer' });
    }
  };

  const handleCreateTg = async () => {
    if (!tgName.trim()) return;
    setCreatingTg(true);
    try {
      await createElbv2TargetGroup({
        name: tgName.trim(),
        protocol: tgProtocol.value,
        port: parseInt(tgPort, 10) || 80,
        target_type: tgTargetType.value,
        health_check_path: tgHealthPath.trim(),
      });
      setActionMessage({ type: 'success', text: `Target Group "${tgName.trim()}" created.` });
      setCreateTgOpen(false);
      setTgName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create target group' });
    } finally {
      setCreatingTg(false);
    }
  };

  const handleDeleteTg = async (arn: string) => {
    if (!confirm('Are you sure you want to delete this target group?')) return;
    try {
      await deleteElbv2TargetGroup(arn);
      setActionMessage({ type: 'success', text: 'Target group deleted.' });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete target group' });
    }
  };

  const handleRegisterTarget = async () => {
    if (!activeTgArn || !targetId.trim()) return;
    setRegisteringTarget(true);
    try {
      await registerElbv2Targets(activeTgArn, [{ Id: targetId.trim(), Port: parseInt(targetPort, 10) || 80 }]);
      setActionMessage({ type: 'success', text: `Target "${targetId.trim()}" registered.` });
      setRegisterTargetOpen(false);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to register target' });
    } finally {
      setRegisteringTarget(false);
    }
  };

  const handleDeregisterTarget = async (tId: string) => {
    if (!activeTgArn) return;
    try {
      await deregisterElbv2Targets(activeTgArn, [{ Id: tId }]);
      setActionMessage({ type: 'success', text: `Target "${tId}" deregistered.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to deregister target' });
    }
  };

  const handleCreateListener = async () => {
    if (!activeLbArn || !listenerTgArn) return;
    setCreatingListener(true);
    try {
      await createElbv2Listener({
        load_balancer_arn: activeLbArn,
        protocol: listenerProtocol.value,
        port: parseInt(listenerPort, 10) || 80,
        target_group_arn: listenerTgArn,
      });
      setActionMessage({ type: 'success', text: 'Listener created.' });
      setCreateListenerOpen(false);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create listener' });
    } finally {
      setCreatingListener(false);
    }
  };

  const handleDeleteListener = async (arn: string) => {
    if (!confirm('Delete this listener? The load balancer stops accepting traffic on its port.')) return;
    try {
      await deleteElbv2Listener(arn);
      setActionMessage({ type: 'success', text: 'Listener deleted.' });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete listener' });
    }
  };

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Elastic Load Balancing (ELBv2) distributes incoming application traffic across multiple targets such as EC2 instances and containers."
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button iconName="refresh" loading={loading} onClick={loadData}>
              Refresh
            </Button>
            <Button variant="primary" iconName="add-plus" onClick={() => setCreateLbOpen(true)}>
              Create Load Balancer
            </Button>
          </SpaceBetween>
        }
      >
        Elastic Load Balancing (ELBv2)
      </Header>

      {actionMessage && (
        <Alert type={actionMessage.type} dismissible onDismiss={() => setActionMessage(null)}>
          {actionMessage.text}
        </Alert>
      )}

      {loadError && <Alert type="error">{loadError}</Alert>}
      {/* Metrics */}
      <Grid gridDefinition={[{ colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }]}>
        <Container>
          <Box variant="awsui-key-label">Load Balancers</Box>
          <Box variant="awsui-value-large">{(data.load_balancers || []).length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">Target Groups</Box>
          <Box variant="awsui-value-large">{(data.target_groups || []).length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">Listeners</Box>
          <Box variant="awsui-value-large">{(data.listeners || []).length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">Registered Targets</Box>
          <Box variant="awsui-value-large">{(data.targets || []).length}</Box>
        </Container>
      </Grid>

      {activeLb && <Container header={<Header variant="h2" actions={<Button onClick={() => showTab('listeners')}>View listeners</Button>}>Traffic routing: {activeLb.LoadBalancerName || activeLb.name}</Header>}>
        <div className="resource-flow"><section><Header variant="h3">Load balancer</Header><p>{activeLb.DNSName || 'DNS name not reported'}</p><p>State: {activeLb.State?.Code || 'Not reported'}</p>{activeLb.State?.Reason && <p>{activeLb.State.Reason}</p>}</section><span aria-hidden="true">→</span><section><Header variant="h3">Listeners</Header>{lbListeners.length ? lbListeners.map((listener: any) => <p key={listener.ListenerArn}>{listener.Protocol}:{listener.Port}</p>) : <p>No listeners reported for this load balancer.</p>}</section><span aria-hidden="true">→</span><section><Header variant="h3">Forwarding targets</Header>{lbListeners.flatMap((listener: any) => (listener.DefaultActions || []).flatMap((action: any) => action.TargetGroupArn ? [action.TargetGroupArn] : (action.ForwardConfig?.TargetGroups || []).map((group: any) => group.TargetGroupArn))).map((arn: string, index: number) => <p key={`${arn}-${index}`}><Button variant="inline-link" onClick={() => showTab('targets', { tg: arn })}>{arn}</Button></p>)}<Button onClick={() => showTab('targetgroups')}>View target groups</Button></section></div>
      </Container>}
      {pinned && !loading && !loadError && !activeLb && <Alert type="warning">Load balancer not found in the active account and Region.</Alert>}
      {pinnedTg && !loading && !loadError && !activeTg && <Alert type="warning">Target group not found in the active account and Region.</Alert>}
      <FormField label="Load balancer context"><Select ariaLabel="Load balancer context" selectedOption={activeLb ? { label: activeLb.LoadBalancerName, value: activeLbArn } : null} placeholder="Select a load balancer" options={(data.load_balancers || []).map((item: any) => ({ label: item.LoadBalancerName, value: item.LoadBalancerArn }))} onChange={({ detail }) => showTab(activeTab || 'listeners', { lb: detail.selectedOption.value! })} /></FormField>
      <FormField label="Target group context"><Select ariaLabel="Target group context" selectedOption={activeTg ? { label: activeTg.TargetGroupName, value: activeTgArn } : null} placeholder="Select a target group" options={(data.target_groups || []).map((item: any) => ({ label: item.TargetGroupName, value: item.TargetGroupArn }))} onChange={({ detail }) => showTab('targets', { tg: detail.selectedOption.value! })} /></FormField>
      {activeTg && <TargetGroupHealth group={activeTg} targets={groupTargets} />}
      <Tabs
        activeTabId={activeTab || 'loadbalancers'}
        onChange={({ detail }) => showTab(detail.activeTabId)}
        tabs={[
          {
            label: `Load Balancers (${(data.load_balancers || []).length})`,
            id: 'loadbalancers',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setCreateLbOpen(true)}>
                        Create Load Balancer
                      </Button>
                    }
                  >
                    Load Balancers (ALB / NLB)
                  </Header>
                }
              >
                <SpaceBetween size="m">
                  <TextFilter
                    filteringText={filterText}
                    onChange={({ detail }) => setFilterText(detail.filteringText)}
                    filteringPlaceholder="Filter load balancers..."
                  />
                  <Table
                    columnDefinitions={[
                      {
                        id: 'name',
                        header: 'Name',
                        cell: (item: any) => (
                          <Button variant="inline-link" onClick={() => { setSelectedLbs([item]); showTab('listeners', { lb: item.LoadBalancerArn }); }}>
                            <strong>{item.LoadBalancerName || item.name}</strong>
                          </Button>
                        ),
                      },
                      {
                        id: 'dns',
                        header: 'DNS Name',
                        cell: (item: any) => (
                          item.DNSName ? <Link href={`http://${item.DNSName}`} external>{item.DNSName}</Link> : <span>Not reported</span>
                        ),
                      },
                      {
                        id: 'type',
                        header: 'Type',
                        cell: (item: any) => (
                          <Badge color={item.Type === 'network' ? 'blue' : 'green'}>
                            {item.Type || 'application'}
                          </Badge>
                        ),
                        width: 140,
                      },
                      {
                        id: 'scheme',
                        header: 'Scheme',
                        cell: (item: any) => item.Scheme || 'internet-facing',
                        width: 140,
                      },
                      {
                        id: 'state',
                        header: 'State',
                        cell: (item: any) => <ResourceValue name="status" value={item.State?.Code} />,
                        width: 120,
                      },
                      {
                        id: 'actions',
                        header: 'Actions',
                        cell: (item: any) => (
                          <Button
                            iconName="remove"
                            onClick={() => handleDeleteLb(item.LoadBalancerArn || item.load_balancer_arn)}
                          >
                            Delete
                          </Button>
                        ),
                        width: 110,
                      },
                    ]}
                    items={filteredLbs}
                    selectionType="single"
                    selectedItems={selectedLbs}
                    onSelectionChange={({ detail }) => { setSelectedLbs(detail.selectedItems); showTab(activeTab || 'loadbalancers', { lb: detail.selectedItems[0]?.LoadBalancerArn || '' }); }}
                    empty={<Box textAlign="center">No load balancers found.</Box>}
                  />
                </SpaceBetween>
              </Container>
            ),
          },
          {
            label: `Target Groups (${(data.target_groups || []).length})`,
            id: 'targetgroups',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setCreateTgOpen(true)}>
                        Create Target Group
                      </Button>
                    }
                  >
                    Target Groups
                  </Header>
                }
              >
                <Table
                  columnDefinitions={[
                    {
                      id: 'name',
                      header: 'Target Group Name',
                      cell: (item: any) => (
                        <Button variant="inline-link" onClick={() => { setSelectedTgs([item]); showTab('targets', { tg: item.TargetGroupArn }); }}>
                          <strong>{item.TargetGroupName || item.name}</strong>
                        </Button>
                      ),
                    },
                    {
                      id: 'protocol',
                      header: 'Protocol / Port',
                      cell: (item: any) => `${item.Protocol || 'Not reported'}:${item.Port ?? 'Not reported'}`,
                      width: 140,
                    },
                    {
                      id: 'type',
                      header: 'Target Type',
                      cell: (item: any) => <Badge color="blue">{item.TargetType || 'Not reported'}</Badge>,
                      width: 140,
                    },
                    {
                      id: 'health',
                      header: 'Health Check Path',
                      cell: (item: any) => <code>{item.HealthCheckPath || 'Not reported'}</code>,
                      width: 180,
                    },
                    {
                      id: 'actions',
                      header: 'Actions',
                      cell: (item: any) => (
                        <Button
                          iconName="remove"
                          onClick={() => handleDeleteTg(item.TargetGroupArn || item.target_group_arn)}
                        >
                          Delete
                        </Button>
                      ),
                      width: 110,
                    },
                  ]}
                  items={data.target_groups || []}
                  selectionType="single"
                  selectedItems={selectedTgs}
                  onSelectionChange={({ detail }) => { setSelectedTgs(detail.selectedItems); showTab(activeTab || 'targetgroups', { tg: detail.selectedItems[0]?.TargetGroupArn || '' }); }}
                  empty={<Box textAlign="center">No target groups created.</Box>}
                />
              </Container>
            ),
          },
          {
            label: 'Registered Targets',
            id: 'targets',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    description={activeTg ? `Targets in "${activeTg.TargetGroupName || activeTg.name}"` : 'Select a Target Group'}
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setRegisterTargetOpen(true)}>
                        Register Targets
                      </Button>
                    }
                  >
                    Targets
                  </Header>
                }
              >
                <Table
                  columnDefinitions={[
                    {
                      id: 'id',
                      header: 'Target ID / Instance',
                      cell: (item: any) => <code>{item.Target?.Id || item.Id || item.id || 'Not reported'}</code>,
                    },
                    {
                      id: 'port',
                      header: 'Port',
                      cell: (item: any) => item.Target?.Port ?? item.Port ?? 'Not reported',
                      width: 120,
                    },
                    {
                      id: 'health',
                      header: 'Health State',
                      cell: (item: any) => <ResourceValue name="status" value={item.TargetHealth?.State} />,
                      width: 140,
                    },
                    {
                      id: 'actions',
                      header: 'Actions',
                      cell: (item: any) => (
                        <Button
                          iconName="remove"
                          onClick={() => handleDeregisterTarget(item.Target?.Id || item.Id || item.id)}
                        >
                          Deregister
                        </Button>
                      ),
                      width: 130,
                    },
                  ]}
                  items={groupTargets}
                  empty={<span>No targets reported for this target group.</span>}
                />
              </Container>
            ),
          },
          {
            label: 'Health checks',
            id: 'healthchecks',
            content: activeTg ? <TargetGroupHealthChecks group={activeTg} onSaved={loadData} notify={notify} /> : <Container><Box textAlign="center">Select a target group to see its health check settings.</Box></Container>,
          },
          {
            label: 'Attributes',
            id: 'attributes',
            content: activeTg ? <TargetGroupAttributes group={activeTg} onSaved={loadData} notify={notify} /> : <Container><Box textAlign="center">Select a target group to see its attributes.</Box></Container>,
          },
          {
            label: `Listeners (${lbListeners.length})`,
            id: 'listeners',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    description={activeLb ? `Listeners for "${activeLb.LoadBalancerName || activeLb.name}"` : 'Select a Load Balancer'}
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setCreateListenerOpen(true)}>
                        Add Listener
                      </Button>
                    }
                  >
                    Listeners & Routing
                  </Header>
                }
              >
                <Table
                  columnDefinitions={[
                    {
                      id: 'listenerArn',
                      header: 'Listener ARN',
                      cell: (item: any) => <code style={{ display: 'block', maxWidth: 360, overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>{item.ListenerArn || item.listener_arn}</code>,
                    },
                    {
                      id: 'port',
                      header: 'Port / Protocol',
                      cell: (item: any) => `${item.Protocol || 'Not reported'}:${item.Port ?? 'Not reported'}`,
                      width: 150,
                    },
                    {
                      id: 'defaultAction',
                      header: 'Default action',
                      cell: (item: any) => describeAction(item.DefaultActions?.[0], data.target_groups || []),
                    },
                    {
                      id: 'actions',
                      header: 'Actions',
                      cell: (item: any) => (
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button ariaLabel={`Edit listener ${item.Protocol}:${item.Port}`} onClick={() => setEditingListener(item)}>Edit</Button>
                          <Button iconName="remove" onClick={() => handleDeleteListener(item.ListenerArn || item.listener_arn)}>
                            Delete
                          </Button>
                        </SpaceBetween>
                      ),
                      width: 190,
                    },
                  ]}
                  items={lbListeners}
                  selectionType="single"
                  selectedItems={activeListener ? [activeListener] : []}
                  onSelectionChange={({ detail }) => setSelectedListenerArn(detail.selectedItems[0]?.ListenerArn || '')}
                  empty={<Box textAlign="center">No listeners configured on this load balancer.</Box>}
                />
                {activeListener && <Box margin={{ top: 'l' }}><ListenerRules listener={activeListener} rules={data.rules || []} targetGroups={data.target_groups || []} onSaved={loadData} notify={notify} /></Box>}
              </Container>
            ),
          },
        ]}
      />

      {/* Create Load Balancer Modal */}
      <Modal
        visible={createLbOpen}
        onDismiss={() => setCreateLbOpen(false)}
        header="Create Load Balancer"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateLbOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingLb} onClick={handleCreateLb}>
                Create Load Balancer
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Subnet IDs" description="Comma-separated subnet IDs. Application Load Balancers require subnets in at least two Availability Zones."><Input value={lbSubnets} onChange={({ detail }) => setLbSubnets(detail.value)} /></FormField>
          <FormField label="Load Balancer Name">
            <Input value={lbName} onChange={({ detail }) => setLbName(detail.value)} placeholder="app-alb" />
          </FormField>
          <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
            <FormField label="Type">
              <Select
                selectedOption={lbType}
                onChange={({ detail }) => setLbType(detail.selectedOption as any)}
                options={[
                  { label: 'Application Load Balancer (HTTP/HTTPS)', value: 'application' },
                  { label: 'Network Load Balancer (TCP/UDP)', value: 'network' },
                ]}
              />
            </FormField>
            <FormField label="Scheme">
              <Select
                selectedOption={lbScheme}
                onChange={({ detail }) => setLbScheme(detail.selectedOption as any)}
                options={[
                  { label: 'internet-facing', value: 'internet-facing' },
                  { label: 'internal', value: 'internal' },
                ]}
              />
            </FormField>
          </Grid>
        </SpaceBetween>
      </Modal>

      {/* Create Target Group Modal */}
      <Modal
        visible={createTgOpen}
        onDismiss={() => setCreateTgOpen(false)}
        header="Create Target Group"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateTgOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingTg} onClick={handleCreateTg}>
                Create Target Group
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Target Group Name">
            <Input value={tgName} onChange={({ detail }) => setTgName(detail.value)} placeholder="web-tg" />
          </FormField>
          <Grid gridDefinition={[{ colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }]}>
            <FormField label="Protocol">
              <Select
                selectedOption={tgProtocol}
                onChange={({ detail }) => setTgProtocol(detail.selectedOption as any)}
                options={[
                  { label: 'HTTP', value: 'HTTP' },
                  { label: 'HTTPS', value: 'HTTPS' },
                  { label: 'TCP', value: 'TCP' },
                ]}
              />
            </FormField>
            <FormField label="Port">
              <Input type="number" value={tgPort} onChange={({ detail }) => setTgPort(detail.value)} />
            </FormField>
            <FormField label="Target Type">
              <Select
                selectedOption={tgTargetType}
                onChange={({ detail }) => setTgTargetType(detail.selectedOption as any)}
                options={[
                  { label: 'Instances (EC2)', value: 'instance' },
                  { label: 'IP addresses', value: 'ip' },
                  { label: 'Lambda function', value: 'lambda' },
                ]}
              />
            </FormField>
          </Grid>
          <FormField label="Health Check Path">
            <Input value={tgHealthPath} onChange={({ detail }) => setTgHealthPath(detail.value)} placeholder="/health" />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Register Target Modal */}
      <Modal
        visible={registerTargetOpen}
        onDismiss={() => setRegisterTargetOpen(false)}
        header="Register Target"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setRegisterTargetOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={registeringTarget} onClick={handleRegisterTarget}>
                Register Target
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Target ID / Instance ID">
            <Input value={targetId} onChange={({ detail }) => setTargetId(detail.value)} placeholder="i-0123456789abcdef0" />
          </FormField>
          <FormField label="Port">
            <Input type="number" value={targetPort} onChange={({ detail }) => setTargetPort(detail.value)} />
          </FormField>
        </SpaceBetween>
      </Modal>

      <ListenerEditModal listener={editingListener} targetGroups={data.target_groups || []} onClose={() => setEditingListener(null)} onSaved={loadData} notify={notify} />

      {/* Create Listener Modal */}
      <Modal
        visible={createListenerOpen}
        onDismiss={() => setCreateListenerOpen(false)}
        header="Create Listener"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateListenerOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingListener} onClick={handleCreateListener}>
                Create Listener
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
            <FormField label="Protocol">
              <Select
                selectedOption={listenerProtocol}
                onChange={({ detail }) => setListenerProtocol(detail.selectedOption as any)}
                options={[
                  { label: 'HTTP', value: 'HTTP' },
                  { label: 'HTTPS', value: 'HTTPS' },
                  { label: 'TCP', value: 'TCP' },
                ]}
              />
            </FormField>
            <FormField label="Port">
              <Input type="number" value={listenerPort} onChange={({ detail }) => setListenerPort(detail.value)} />
            </FormField>
          </Grid>
          <FormField label="Default Action Target Group ARN">
            <Input value={listenerTgArn} onChange={({ detail }) => setListenerTgArn(detail.value)} placeholder="arn:aws:elasticloadbalancing:..." />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
