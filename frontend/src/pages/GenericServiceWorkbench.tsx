import { ResourceInventory } from '../components/ResourceInventory';
import React, { useState } from 'react';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Textarea from '@cloudscape-design/components/textarea';
import Checkbox from '@cloudscape-design/components/checkbox';
import Modal from '@cloudscape-design/components/modal';
import Box from '@cloudscape-design/components/box';
import Badge from '@cloudscape-design/components/badge';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Grid from '@cloudscape-design/components/grid';
import Alert from '@cloudscape-design/components/alert';
import TextFilter from '@cloudscape-design/components/text-filter';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import Select from '@cloudscape-design/components/select';
import { ServiceAction, ServiceDefinition, ActionField } from '../types';
import { executeServiceAction } from '../api/client';
import { CodeSnippet } from '../components/CodeSnippet';

interface GenericServiceWorkbenchProps {
  service: ServiceDefinition;
  onNavigateLabs?: () => void;
}

export const GenericServiceWorkbench: React.FC<GenericServiceWorkbenchProps> = ({
  service,
  onNavigateLabs,
}) => {
  const [inventoryRevision, setInventoryRevision] = useState(0);
  const [filterText, setFilterText] = useState('');
  const [selectedKind, setSelectedKind] = useState<string>('all');
  const [selectedAction, setSelectedAction] = useState('');
  const [selectedResource, setSelectedResource] = useState<Record<string, any> | null>(null);
  const [formValues, setFormValues] = useState<Record<string, Record<string, any>>>({});
  const [fileValues, setFileValues] = useState<Record<string, Record<string, File>>>({});
  const [actionOutputs, setActionOutputs] = useState<Record<string, { status: 'success' | 'error'; data: any; elapsed: number }>>({});
  const [runningActionName, setRunningActionName] = useState<string | null>(null);

  // Destructive confirmation modal state
  const [pendingAction, setPendingAction] = useState<ServiceAction | null>(null);

  const actions = (service.actions || []).map(action => ({
    ...action,
    fields: [
      ...(action.path.match(/\{([^}]+)\}/g) || []).map(parameter => parameter.slice(1, -1))
        .filter(name => !(action.fields || []).some(field => field.name === name))
        .map(name => ({ name, label: name.replace(/_/g, ' '), required: true, field_type: 'string', help_text: 'Resource identifier for this operation' })),
      ...(action.fields || []),
    ],
  }));

  const handleFieldChange = (actionName: string, fieldName: string, value: any) => {
    setFormValues((prev) => ({
      ...prev,
      [actionName]: {
        ...(prev[actionName] || {}),
        [fieldName]: value,
      },
    }));
  };

  const handleFileChange = (actionName: string, fieldName: string, file: File | null) => {
    if (!file) return;
    setFileValues((prev) => ({
      ...prev,
      [actionName]: {
        ...(prev[actionName] || {}),
        [fieldName]: file,
      },
    }));
  };

  const logActivity = (action: ServiceAction, payload: any, result: any) => {
    try {
      const saved = localStorage.getItem('floci_activity');
      const list = saved ? JSON.parse(saved) : [];
      list.unshift({
        service: service.key,
        action: action.name,
        title: `${service.title}: ${action.label}`,
        summary: action.description || `${action.method} ${action.path}`,
        timestamp: Date.now(),
        payload: { request: payload, response: result },
      });
      localStorage.setItem('floci_activity', JSON.stringify(list.slice(0, 100)));
    } catch (e) {
      console.error(e);
    }
  };

  const executeAction = async (action: ServiceAction) => {
    if (action.safety === 'destructive' && !pendingAction) {
      setPendingAction(action);
      return;
    }

    setRunningActionName(action.name);
    setPendingAction(null);
    const start = performance.now();
    const actionForm = formValues[action.name] || {};
    const actionFiles = fileValues[action.name] || {};

    try {
      const data = await executeServiceAction(action, actionForm, actionFiles);
      const elapsed = Math.round(performance.now() - start);
      setActionOutputs((prev) => ({
        ...prev,
        [action.name]: {
          status: 'success',
          data,
          elapsed,
        },
      }));
      logActivity(action, actionForm, data);
      setInventoryRevision(value => value + 1);
    } catch (err: any) {
      const elapsed = Math.round(performance.now() - start);
      setActionOutputs((prev) => ({
        ...prev,
        [action.name]: {
          status: 'error',
          data: { error: err.message || 'Execution error' },
          elapsed,
        },
      }));
    } finally {
      setRunningActionName(null);
    }
  };

  const filteredActions = actions.filter((act) => {
    const matchesFilter =
      act.name.toLowerCase().includes(filterText.toLowerCase()) ||
      act.label.toLowerCase().includes(filterText.toLowerCase()) ||
      (act.description && act.description.toLowerCase().includes(filterText.toLowerCase())) ||
      act.path.toLowerCase().includes(filterText.toLowerCase());

    const matchesKind = selectedKind === 'all' || act.kind === selectedKind;
    return matchesFilter && matchesKind;
  });

  const renderFieldInput = (action: ServiceAction, f: ActionField) => {
    const currentVal = (formValues[action.name] || {})[f.name] ?? '';

    switch (f.field_type) {
      case 'number':
        return (
          <Input
            type="number"
            value={String(currentVal)}
            onChange={({ detail }) => handleFieldChange(action.name, f.name, detail.value)}
            placeholder="0"
          />
        );
      case 'boolean':
        return (
          <Checkbox
            checked={Boolean(currentVal)}
            onChange={({ detail }) => handleFieldChange(action.name, f.name, detail.checked)}
          >
            {f.label}
          </Checkbox>
        );
      case 'textarea':
        return (
          <Textarea
            rows={3}
            value={String(currentVal)}
            onChange={({ detail }) => handleFieldChange(action.name, f.name, detail.value)}
            placeholder={`Enter ${f.label.toLowerCase()}...`}
          />
        );
      case 'object':
      case 'array':
        return (
          <Textarea
            rows={4}
            value={typeof currentVal === 'object' ? JSON.stringify(currentVal, null, 2) : String(currentVal)}
            onChange={({ detail }) => handleFieldChange(action.name, f.name, detail.value)}
            placeholder={f.field_type === 'array' ? '[\n  "item1",\n  "item2"\n]' : '{\n  "Key": "Value"\n}'}
          />
        );
      case 'file':
        return (
          <input
            type="file"
            onChange={(e) => {
              const file = e.target.files?.[0] || null;
              handleFileChange(action.name, f.name, file);
            }}
            style={{ color: '#ffffff' }}
          />
        );
      default:
        return (
          <Input
            value={String(currentVal)}
            onChange={({ detail }) => handleFieldChange(action.name, f.name, detail.value)}
            placeholder={`Enter ${f.label.toLowerCase()}...`}
          />
        );
    }
  };

  return (
    <SpaceBetween size="l">
      {/* Service Header */}
      <Container
        header={
          <Header
            variant="h1"
            description={service.eyebrow || 'Local AWS Service Workbench'}
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                {onNavigateLabs && (
                  <Button variant="normal" iconName="folder-open" onClick={onNavigateLabs}>
                    Launch Guided Labs
                  </Button>
                )}
                {service.docs_url && (
                  <Button href={service.docs_url} target="_blank" iconName="external">
                    AWS Documentation
                  </Button>
                )}
              </SpaceBetween>
            }
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
              <Badge color="blue">{service.key.toUpperCase()}</Badge>
              <span>{service.title}</span>
              <StatusIndicator type="info">Local AWS workbench</StatusIndicator>
            </div>
          </Header>
        }
      >
        <SpaceBetween direction="horizontal" size="s">
          <Badge color="blue">{service.category || 'AWS Service'}</Badge>
          <Box color="text-body-secondary">{actions.length} available operations</Box>
        </SpaceBetween>
      </Container>

      <ResourceInventory key={service.key} service={service} revision={inventoryRevision} onSelectResource={resource => {
        setSelectedResource(resource || null);
        setFormValues(previous => Object.fromEntries(actions.map(action => [action.name, {
          ...(previous[action.name] || {}),
          ...Object.fromEntries(action.fields.filter(field => selectedResource?.[field.name] != null).map(field => [field.name, ''])),
          ...Object.fromEntries(action.fields.filter(field => resource?.[field.name] != null).map(field => [field.name, resource[field.name]])),
        }])));
      }} />

      <Container header={<Header variant="h2" description={selectedResource ? 'Actions apply to the selected resource. Review the form before executing.' : 'Create a resource or select one in the inventory to manage it.'}>Resource actions</Header>}>
        <SpaceBetween direction="horizontal" size="xs">
          {['create', 'update', 'delete'].map(kind => {
            const available = actions.filter(action => action.kind === kind);
            if (!available.length) return null;
            return <Button key={kind} variant={kind === 'create' ? 'primary' : 'normal'} disabled={kind !== 'create' && !selectedResource} onClick={() => {
              setSelectedKind(kind); setFilterText(''); setSelectedAction(available.length === 1 ? available[0].name : '');
              document.getElementById('resource-operations')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }}>{kind === 'create' ? 'Create resource' : kind === 'update' ? 'Edit resource' : 'Delete resource'}</Button>;
          })}
        </SpaceBetween>
      </Container>
      <div id="resource-operations" />
      {/* Operations Directory */}
      <Container
        header={
          <Header
            variant="h2"
            description="Execute authenticated local AWS API operations, test payloads, and inspect live responses."
          >
            Interactive Operations Workbench ({filteredActions.length} of {actions.length})
          </Header>
        }
      >
        <SpaceBetween size="m">
          {selectedResource && <StatusIndicator type="info">Selected resource: {String(selectedResource.name || selectedResource.Name || selectedResource.id || selectedResource.arn || selectedResource.ARN || 'Resource selected')}. Matching resource fields are filled in below.</StatusIndicator>}
          <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ flex: 1, minWidth: '260px' }}>
              <TextFilter
                filteringText={filterText}
                filteringPlaceholder="Filter operations by name, path, description..."
                onChange={({ detail }) => { setFilterText(detail.filteringText); setSelectedAction(''); }}
              />
            </div>
            <SegmentedControl
              selectedId={selectedKind}
              onChange={({ detail }) => { setSelectedKind(detail.selectedId); setSelectedAction(''); }}
              options={[
                { text: 'All Operations', id: 'all' },
                { text: 'Create', id: 'create' },
                { text: 'Read', id: 'read' },
                { text: 'Update', id: 'update' },
                { text: 'Delete', id: 'delete' },
                { text: 'Execute', id: 'execute' },
              ]}
            />
          </div>
          <FormField label="Operation" description="Choose a task to open its form.">
            <Select ariaLabel="Operation" selectedOption={selectedAction ? { value: selectedAction, label: actions.find(action => action.name === selectedAction)?.label } : null}
              placeholder="Choose an operation" options={filteredActions.map(action => ({ value: action.name, label: action.label, description: action.description }))}
              onChange={({ detail }) => setSelectedAction(detail.selectedOption.value!)} />
          </FormField>

          {filteredActions.length === 0 ? (
            <Box textAlign="center" color="inherit" padding="l">
              <b>No operations found matching your criteria</b>
              <p>Try clearing the filter or selecting another action category.</p>
            </Box>
          ) : (
            <SpaceBetween size="l">
              {filteredActions.filter(action => action.name === selectedAction).map((action) => {
                const output = actionOutputs[action.name];
                const isRunning = runningActionName === action.name;
                const missingRequired = action.fields.some(field => field.required && !String((formValues[action.name] || {})[field.name] ?? '').trim());

                return (
                  <Container
                    key={action.name}
                    header={
                      <Header
                        variant="h3"
                        description={action.description}
                        actions={
                          <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                            <Badge color={action.method === 'DELETE' ? 'red' : action.method === 'GET' ? 'green' : 'blue'}>
                              {action.method}
                            </Badge>
                            <Badge color={action.safety === 'destructive' ? 'red' : action.safety === 'safe' ? 'green' : 'blue'}>
                              {action.safety ? action.safety.toUpperCase() : 'MUTATING'}
                            </Badge>
                          </div>
                        }
                      >
                        <span style={{ fontSize: '15px' }}>{action.label}</span>
                      </Header>
                    }
                  >
                    <SpaceBetween size="m">
                      <ExpandableSection headerText="API details"><code>{action.method} {action.path}</code></ExpandableSection>

                      {/* Fields Form */}
                      {action.fields && action.fields.length > 0 ? (
                        <Grid
                          gridDefinition={action.fields.map(() => ({ colspan: { default: 12, s: action.fields.length === 1 ? 12 : 6 } }))}
                        >
                          {action.fields.map((f) => (
                            <FormField
                              key={f.name}
                              label={f.label}
                              description={f.help_text}
                              constraintText={f.required ? 'Required' : 'Optional'}
                            >
                              {renderFieldInput(action, f)}
                            </FormField>
                          ))}
                        </Grid>
                      ) : (
                        <p style={{ margin: 0, color: '#879596', fontSize: '13px' }}>
                          This operation takes no required parameters. Click Execute below to trigger it.
                        </p>
                      )}

                      <SpaceBetween direction="horizontal" size="xs">
                        <Button
                          variant={action.safety === 'destructive' ? 'normal' : 'primary'}
                          iconName={action.safety === 'destructive' ? 'delete-marker' : 'caret-right-filled'}
                          loading={isRunning}
                          disabled={missingRequired || Boolean(runningActionName && !isRunning)}
                          onClick={() => executeAction(action)}
                        >
                          Execute {action.label}
                        </Button>
                        <Button
                          onClick={() => {
                            setFormValues((prev) => ({ ...prev, [action.name]: {} }));
                            setActionOutputs((prev) => {
                              const next = { ...prev };
                              delete next[action.name];
                              return next;
                            });
                          }}
                        >
                          Clear
                        </Button>
                      </SpaceBetween>

                      {/* Result Output */}
                      {output && (
                        <Container
                          header={
                            <Header
                              variant="h3"
                              actions={
                                <SpaceBetween direction="horizontal" size="xs">
                                  <StatusIndicator type={output.status === 'success' ? 'success' : 'error'}>
                                    {output.status === 'success' ? action.success_message || 'Operation Succeeded' : 'Operation Failed'}
                                  </StatusIndicator>
                                  <Badge color="grey">{output.elapsed}ms</Badge>
                                </SpaceBetween>
                              }
                            >
                              Execution Result
                            </Header>
                          }
                        >
                          <ExpandableSection defaultExpanded={output.status === 'error'} headerText="Response details"><CodeSnippet language="json" code={JSON.stringify(output.data, null, 2)} /></ExpandableSection>
                        </Container>
                      )}
                    </SpaceBetween>
                  </Container>
                );
              })}
            </SpaceBetween>
          )}
        </SpaceBetween>
      </Container>

      {/* Destructive Action Modal */}
      <Modal
        visible={Boolean(pendingAction)}
        onDismiss={() => setPendingAction(null)}
        header={`Confirm Destructive Operation: ${pendingAction?.label}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setPendingAction(null)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={Boolean(runningActionName)}
                onClick={() => {
                  if (pendingAction) {
                    executeAction(pendingAction);
                  }
                }}
              >
                Confirm & Execute
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Alert type="warning">
          <strong>Warning: </strong> {pendingAction?.confirm || `Are you sure you want to execute ${pendingAction?.label}? This operation is marked as destructive.`}
        </Alert>
      </Modal>
    </SpaceBetween>
  );
};
