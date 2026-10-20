import { LogStreamExplorer } from '../components/LogStreamExplorer';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import { MetricExplorer } from '../components/MetricExplorer';
import React, { useState, useEffect } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Table from '../components/ResourceTable';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Select from '@cloudscape-design/components/select';
import Modal from '@cloudscape-design/components/modal';
import Box from '@cloudscape-design/components/box';
import Badge from '@cloudscape-design/components/badge';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Grid from '@cloudscape-design/components/grid';
import Alert from '@cloudscape-design/components/alert';
import TextFilter from '@cloudscape-design/components/text-filter';
import Tabs from '@cloudscape-design/components/tabs';
import {
  fetchInventory,
  executeServiceAction,
  fetchInspectorLogGroups,
  putCloudWatchMetricData,
  setCloudWatchAlarmState,
  deleteCloudWatchAlarms,
  createCloudWatchLogStream,
  putCloudWatchLogRetention,
} from '../api/client';

interface CloudWatchConsoleProps {
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const CloudWatchConsole: React.FC<CloudWatchConsoleProps> = ({ activeTab, onTabChange }) => {
  const [data, setData] = useState<any>({ metric_alarms: [] });
  const [loading, setLoading] = useState(true);
  const [filterText, setFilterText] = useState('');
  const [selectedAlarms, setSelectedAlarms] = useState<any[]>([]);
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [selectedTabId, setSelectedTabId] = useState(activeTab || 'alarms');

  useEffect(() => {
    if (activeTab) {
      setSelectedTabId(activeTab);
    }
  }, [activeTab]);

  // Create Alarm Modal
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [alarmName, setAlarmName] = useState('');
  const [metricName, setMetricName] = useState('CPUUtilization');
  const [namespace, setNamespace] = useState('AWS/EC2');
  const [threshold, setThreshold] = useState('80');
  const [comparisonOp, setComparisonOp] = useState({ label: 'GreaterThanThreshold (>)', value: 'GreaterThanThreshold' });
  const [creating, setCreating] = useState(false);

  // Metrics Ingest State
  const [ingestNamespace, setIngestNamespace] = useState('MyApp/CustomMetrics');
  const [ingestMetricName, setIngestMetricName] = useState('RequestLatency');
  const [ingestValue, setIngestValue] = useState('42.5');
  const [ingestUnit, setIngestUnit] = useState({ label: 'Milliseconds', value: 'Milliseconds' });
  const [publishingMetric, setPublishingMetric] = useState(false);
  const [metricsList, setMetricsList] = useState<any[]>([]);

  // CloudWatch Logs
  const [logGroups, setLogGroups] = useState<any[]>([]);
  const [selectedLogGroup, setSelectedLogGroup] = useState<any>(null);
  const [logRevision, setLogRevision] = useState(0);
  const [focusedMetric, setFocusedMetric] = useState<any>(null);

  // Retention Modal
  const [retentionModalOpen, setRetentionModalOpen] = useState(false);
  const [selectedRetention, setSelectedRetention] = useState({ label: '30 days', value: '30' });
  const [savingRetention, setSavingRetention] = useState(false);

  // Create Stream Modal
  const [createStreamOpen, setCreateStreamOpen] = useState(false);
  const [newStreamName, setNewStreamName] = useState('');
  const [creatingStream, setCreatingStream] = useState(false);

  const loadData = async () => {
    setLoading(true);
    try {
      const res = await fetchInventory('cloudwatch');
      setData(res || { metric_alarms: [] });
      setMetricsList(res.metrics || []);
      const logsRes = await fetchInspectorLogGroups();
      setLogGroups(logsRes.log_groups || []);
      if (logsRes.log_groups?.length > 0 && !selectedLogGroup) {
        setSelectedLogGroup({ label: logsRes.log_groups[0].logGroupName, value: logsRes.log_groups[0].logGroupName });
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleCreateAlarm = async () => {
    if (!alarmName.trim()) return;
    setCreating(true);
    setActionMessage(null);
    try {
      await executeServiceAction('cloudwatch', 'put_metric_alarm', {
        alarm_name: alarmName.trim(),
        metric_name: metricName,
        namespace,
        threshold: Number(threshold) || 80,
        comparison_operator: comparisonOp.value,
        evaluation_periods: 1,
        period: 300,
        statistic: 'Average',
      });
      setActionMessage({ type: 'success', text: `Metric Alarm "${alarmName.trim()}" created successfully.` });
      setCreateModalOpen(false);
      setAlarmName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create metric alarm' });
    } finally {
      setCreating(false);
    }
  };

  const handleSetAlarmState = async (stateValue: 'OK' | 'ALARM' | 'INSUFFICIENT_DATA') => {
    if (!selectedAlarms.length) return;
    const alarm = selectedAlarms[0];
    try {
      await setCloudWatchAlarmState(
        alarm.AlarmName,
        stateValue,
        `Manual simulator state override from CloudWatch Console`
      );
      setActionMessage({ type: 'success', text: `Alarm "${alarm.AlarmName}" state transitioned to ${stateValue}.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to update alarm state' });
    }
  };

  const handleDeleteAlarm = async () => {
    if (!selectedAlarms.length) return;
    const alarm = selectedAlarms[0];
    try {
      await deleteCloudWatchAlarms([alarm.AlarmName]);
      setActionMessage({ type: 'success', text: `Alarm "${alarm.AlarmName}" deleted.` });
      setSelectedAlarms([]);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete alarm' });
    }
  };

  const handlePublishMetric = async () => {
    if (!ingestNamespace.trim() || !ingestMetricName.trim()) return;
    setPublishingMetric(true);
    try {
      await putCloudWatchMetricData(ingestNamespace.trim(), [{
        MetricName: ingestMetricName.trim(),
        Value: Number(ingestValue) || 0,
        Unit: ingestUnit.value,
        Timestamp: new Date().toISOString(),
      }]);
      setMetricsList((prev) => [
        { Namespace: ingestNamespace.trim(), MetricName: ingestMetricName.trim() },
        ...prev.filter((m) => !(m.Namespace === ingestNamespace.trim() && m.MetricName === ingestMetricName.trim())),
      ]);
      setActionMessage({ type: 'success', text: `Metric "${ingestMetricName.trim()}" published to "${ingestNamespace.trim()}".` });
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to publish metric' });
    } finally {
      setPublishingMetric(false);
    }
  };

  const handleSaveRetention = async () => {
    if (!selectedLogGroup?.value) return;
    setSavingRetention(true);
    try {
      await putCloudWatchLogRetention(selectedLogGroup.value, Number(selectedRetention.value) || 30);
      setActionMessage({ type: 'success', text: `Retention policy updated for ${selectedLogGroup.value}.` });
      setRetentionModalOpen(false);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to update retention policy' });
    } finally {
      setSavingRetention(false);
    }
  };

  const handleCreateLogStream = async () => {
    if (!selectedLogGroup?.value || !newStreamName.trim()) return;
    setCreatingStream(true);
    try {
      await createCloudWatchLogStream(selectedLogGroup.value, newStreamName.trim());
      setActionMessage({ type: 'success', text: `Log stream "${newStreamName.trim()}" created.` });
      setCreateStreamOpen(false);
      setNewStreamName('');
      setLogRevision(value => value + 1);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create log stream' });
    } finally {
      setCreatingStream(false);
    }
  };

  const alarmsList = (data.metric_alarms || data.alarms || []).map((a: any) => ({
    ...a,
    AlarmName: a.AlarmName || a.alarm_name,
    MetricName: a.MetricName || a.metric_name || 'Unavailable',
    Namespace: a.Namespace || a.namespace || 'Unavailable',
    Dimensions: a.Dimensions || a.dimensions || [],
    StateValue: a.StateValue || a.state_value || 'INSUFFICIENT_DATA',
    Threshold: a.Threshold ?? a.threshold ?? 'Unavailable',
  }));

  const filteredAlarms = alarmsList.filter((a: any) => {
    const text = `${a.AlarmName} ${a.MetricName} ${a.Namespace}`.toLowerCase();
    return text.includes(filterText.toLowerCase());
  });

  return (
    <SpaceBetween size="l">
      {/* Header Container */}
      <Container
        header={
          <Header
            variant="h1"
            description="Observability and monitoring for AWS cloud resources and applications."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={loadData} loading={loading}>
                  Refresh
                </Button>
                <Button variant="primary" iconName="add-plus" onClick={() => setCreateModalOpen(true)}>
                  Create Alarm
                </Button>
              </SpaceBetween>
            }
          >
            Amazon CloudWatch
          </Header>
        }
      >
        {actionMessage && (
          <Box margin={{ bottom: 'm' }}>
            <Alert type={actionMessage.type} dismissible onDismiss={() => setActionMessage(null)}>
              {actionMessage.text}
            </Alert>
          </Box>
        )}

        <div className="console-summary">
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Metric Alarms</Box>
            <Box variant="h1" color="text-status-info">
              {alarmsList.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Log Groups</Box>
            <Box variant="h1" color="text-status-info">
              {logGroups.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Metrics Engine</Box>
            <Box variant="h2" color="text-status-info">
              <span>{metricsList.length} metrics reported</span>
            </Box>
          </Box>
        </div>
      </Container>

      {/* Main Tabs */}
      <Tabs
        activeTabId={selectedTabId}
        onChange={({ detail }) => {
          setSelectedTabId(detail.activeTabId);
          onTabChange?.(detail.activeTabId);
        }}
        tabs={[
          {
            label: `Metric Alarms (${alarmsList.length})`,
            id: 'alarms',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <SpaceBetween direction="horizontal" size="xs">
                        <Button disabled={!selectedAlarms.length} onClick={() => handleSetAlarmState('OK')}>
                          Simulate OK
                        </Button>
                        <Button disabled={!selectedAlarms.length} onClick={() => handleSetAlarmState('ALARM')}>
                          Simulate ALARM
                        </Button>
                        <Button disabled={!selectedAlarms.length} onClick={handleDeleteAlarm}>
                          Delete Alarm
                        </Button>
                      </SpaceBetween>
                    }
                  >
                    Metric Alarms
                  </Header>
                }
              >
                <SpaceBetween size="m">
                  <TextFilter
                    filteringText={filterText}
                    filteringPlaceholder="Filter alarms by name, metric, or namespace..."
                    onChange={({ detail }) => setFilterText(detail.filteringText)}
                  />

                  <Table
                    columnDefinitions={[
                      {
                        id: 'name',
                        header: 'Alarm Name',
                        cell: (item) => <Button variant="inline-link" onClick={() => {
                          setFocusedMetric({ Namespace: item.Namespace, MetricName: item.MetricName, Dimensions: item.Dimensions || [] });
                          setSelectedTabId('metrics'); onTabChange?.('metrics');
                        }}>{item.AlarmName}</Button>,
                      },
                      {
                        id: 'state',
                        header: 'State',
                        cell: (item) => (
                          <StatusIndicator type={item.StateValue === 'OK' ? 'success' : item.StateValue === 'ALARM' ? 'error' : 'in-progress'}>
                            {item.StateValue}
                          </StatusIndicator>
                        ),
                        width: 130,
                      },
                      {
                        id: 'metric',
                        header: 'Metric Name',
                        cell: (item) => <code>{item.MetricName}</code>,
                        width: 180,
                      },
                      {
                        id: 'namespace',
                        header: 'Namespace',
                        cell: (item) => <Badge color="blue">{item.Namespace}</Badge>,
                        width: 140,
                      },
                      {
                        id: 'threshold',
                        header: 'Threshold',
                        cell: (item) => <span>&gt; {item.Threshold}</span>,
                        width: 120,
                      },
                    ]}
                    items={filteredAlarms}
                    selectionType="single"
                    selectedItems={selectedAlarms}
                    onSelectionChange={({ detail }) => setSelectedAlarms(detail.selectedItems)}
                    empty={<Box textAlign="center">No metric alarms found.</Box>}
                  />
                </SpaceBetween>
              </Container>
            ),
          },
          {
            label: 'Metrics',
            id: 'metrics',
            content: (
              <SpaceBetween size="l">
                <MetricExplorer metrics={focusedMetric && !metricsList.some(metric => metric.Namespace === focusedMetric.Namespace && metric.MetricName === focusedMetric.MetricName && JSON.stringify(metric.Dimensions || []) === JSON.stringify(focusedMetric.Dimensions || [])) ? [...metricsList, focusedMetric] : metricsList} preferredMetric={focusedMetric} />
                <ExpandableSection headerText="Publish Custom Metric Data">
                  <SpaceBetween size="m">
                    <Grid gridDefinition={[{ colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }]}>
                      <FormField label="Namespace">
                        <Input value={ingestNamespace} onChange={({ detail }) => setIngestNamespace(detail.value)} placeholder="MyApp/Metrics" />
                      </FormField>
                      <FormField label="Metric Name">
                        <Input value={ingestMetricName} onChange={({ detail }) => setIngestMetricName(detail.value)} placeholder="RequestCount" />
                      </FormField>
                      <FormField label="Value">
                        <Input value={ingestValue} onChange={({ detail }) => setIngestValue(detail.value)} type="number" />
                      </FormField>
                      <FormField label="Unit">
                        <Select
                          selectedOption={ingestUnit}
                          onChange={({ detail }) => setIngestUnit(detail.selectedOption as any)}
                          options={[
                            { label: 'Count', value: 'Count' },
                            { label: 'Milliseconds', value: 'Milliseconds' },
                            { label: 'Bytes', value: 'Bytes' },
                            { label: 'Percent', value: 'Percent' },
                          ]}
                        />
                      </FormField>
                    </Grid>
                    <Button variant="primary" loading={publishingMetric} onClick={handlePublishMetric}>
                      Publish Metric Data
                    </Button>
                  </SpaceBetween>
                </ExpandableSection>


              </SpaceBetween>
            ),
          },
          {
            label: `CloudWatch Logs (${logGroups.length})`,
            id: 'logs',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    description="Real-time log events captured from Lambda functions and API requests."
                    actions={
                      <SpaceBetween direction="horizontal" size="xs">
                        <Button disabled={!selectedLogGroup?.value} onClick={() => setRetentionModalOpen(true)}>Retention Settings</Button>
                        <Button disabled={!selectedLogGroup?.value} variant="primary" iconName="add-plus" onClick={() => setCreateStreamOpen(true)}>
                          Create Log Stream
                        </Button>
                      </SpaceBetween>
                    }
                  >
                    CloudWatch Log Viewer
                  </Header>
                }
              >
                <SpaceBetween size="m">
                  <FormField label="Select Log Group">
                    <Select
                      selectedOption={selectedLogGroup}
                      onChange={({ detail }) => setSelectedLogGroup(detail.selectedOption)}
                      options={logGroups.map((g) => ({ label: g.logGroupName, value: g.logGroupName }))}
                    />
                  </FormField>

                  {selectedLogGroup?.value && <LogStreamExplorer key={selectedLogGroup.value} groupName={selectedLogGroup.value} revision={logRevision} />}
                </SpaceBetween>
              </Container>
            ),
          },
        ]}
      />

      {/* Create Alarm Modal */}
      <Modal
        visible={createModalOpen}
        onDismiss={() => setCreateModalOpen(false)}
        header="Create CloudWatch Metric Alarm"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creating} onClick={handleCreateAlarm}>
                Create Alarm
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Alarm Name" description="Unique alarm identifier.">
            <Input
              value={alarmName}
              onChange={({ detail }) => setAlarmName(detail.value)}
              placeholder="HighCPUAlarm"
            />
          </FormField>

          <FormField label="Metric Name">
            <Input
              value={metricName}
              onChange={({ detail }) => setMetricName(detail.value)}
              placeholder="CPUUtilization"
            />
          </FormField>

          <FormField label="Namespace">
            <Input
              value={namespace}
              onChange={({ detail }) => setNamespace(detail.value)}
              placeholder="AWS/EC2"
            />
          </FormField>

          <FormField label="Threshold (Value)">
            <Input
              type="number"
              value={threshold}
              onChange={({ detail }) => setThreshold(detail.value)}
              placeholder="80"
            />
          </FormField>

          <FormField label="Comparison Operator">
            <Select
              selectedOption={comparisonOp}
              onChange={({ detail }) => setComparisonOp(detail.selectedOption as any)}
              options={[
                { label: 'GreaterThanThreshold (>)', value: 'GreaterThanThreshold' },
                { label: 'GreaterThanOrEqualToThreshold (>=)', value: 'GreaterThanOrEqualToThreshold' },
                { label: 'LessThanThreshold (<)', value: 'LessThanThreshold' },
                { label: 'LessThanOrEqualToThreshold (<=)', value: 'LessThanOrEqualToThreshold' },
              ]}
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Retention Settings Modal */}
      <Modal
        visible={retentionModalOpen}
        onDismiss={() => setRetentionModalOpen(false)}
        header={`Set Retention for ${selectedLogGroup?.value}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setRetentionModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={savingRetention} onClick={handleSaveRetention}>
                Save Retention
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Retention Period">
          <Select
            selectedOption={selectedRetention}
            onChange={({ detail }) => setSelectedRetention(detail.selectedOption as any)}
            options={[
              { label: '1 day', value: '1' },
              { label: '7 days', value: '7' },
              { label: '30 days', value: '30' },
              { label: '90 days', value: '90' },
              { label: '365 days', value: '365' },
              { label: 'Never expire (0)', value: '0' },
            ]}
          />
        </FormField>
      </Modal>

      {/* Create Log Stream Modal */}
      <Modal
        visible={createStreamOpen}
        onDismiss={() => setCreateStreamOpen(false)}
        header={`Create Log Stream in ${selectedLogGroup?.value}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateStreamOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingStream} onClick={handleCreateLogStream}>
                Create Stream
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Log Stream Name">
          <Input value={newStreamName} onChange={({ detail }) => setNewStreamName(detail.value)} placeholder="2026/08/24/[$LATEST]abc123" />
        </FormField>
      </Modal>
    </SpaceBetween>
  );
};
