import { useState } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Select from '@cloudscape-design/components/select';
import FormField from '@cloudscape-design/components/form-field';
import Button from '@cloudscape-design/components/button';
import Alert from '@cloudscape-design/components/alert';

const rows = (value: any): any[] => Array.isArray(value) ? value : [];
export function ApiRequestFlow({ api, onTest }: { api: any; onTest: (method: string, path: string, stage: string) => void }) {
  const http = api.api_type === 'http';
  const routes = http ? rows(api.routes).map(route => {
    const integrationId = String(route.Target || '').replace(/^integrations\//, '');
    return { id: route.RouteId, label: route.RouteKey, authorization: route.AuthorizationType, authorizer: route.AuthorizerId, integration: rows(api.integrations).find(item => item.IntegrationId === integrationId) };
  }) : rows(api.resources).flatMap(resource => Object.entries(resource.resourceMethods || {}).map(([verb, method]: [string, any]) => ({ id: `${resource.id}:${verb}`, label: `${verb} ${resource.path || '/'}`, authorization: method.authorizationType, authorizer: method.authorizerId, integration: method.methodIntegration })));
  const [routeId, setRoute] = useState('');
  const [stageId, setStage] = useState('');
  const route = routes.find(item => item.id === routeId) || routes[0];
  const stages = rows(api.stages);
  const stageName = (stage: any) => stage.StageName || stage.stageName || stage.name;
  const stage = stages.find(item => stageName(item) === stageId) || stages[0];
  const integration = route?.integration;
  return <Container header={<Header variant="h2" description="Follow the configured route through authorization, integration, and stage context.">Request flow</Header>}><SpaceBetween size="m">
    <FormField label="Inspect route"><Select ariaLabel="Inspect route" placeholder="No routes reported" selectedOption={route ? { label: route.label, value: route.id } : null} options={routes.map(item => ({ label: item.label, value: item.id }))} onChange={({ detail }) => setRoute(detail.selectedOption.value!)} /></FormField>
    <div className="resource-flow">
      <section><Header variant="h3">Route and authorization</Header><p>{route?.label || 'No route reported'}</p><p>Authorization: {route?.authorization || 'Not reported'}</p>{route?.authorizer && <p>Authorizer: {route.authorizer}</p>}</section>
      <span aria-hidden="true">→</span><section><Header variant="h3">Integration</Header><p>{integration?.IntegrationType || integration?.type || 'No integration reported for this route'}</p><p>{integration?.IntegrationUri || integration?.uri || 'URI not reported'}</p></section>
      <span aria-hidden="true">→</span><section><Header variant="h3">Stage context</Header><Select ariaLabel="Flow stage" placeholder="No stages reported" selectedOption={stage ? { label: stageName(stage), value: stageName(stage) } : null} options={stages.map(item => ({ label: stageName(item), value: stageName(item) }))} onChange={({ detail }) => setStage(detail.selectedOption.value!)} /><p>Deployment: {stage?.DeploymentId || stage?.deploymentId || 'Not reported'}</p><p>Automatic deployment: {stage?.AutoDeploy == null ? 'Not reported' : stage.AutoDeploy ? 'Enabled' : 'Disabled'}</p></section>
    </div>
    {!stage && <Alert type="info">No deployed stages reported. Configure a stage before testing a request.</Alert>}
    <Button disabled={!route || !stage} onClick={() => { const [verb, ...parts] = String(route.label).split(' '); onTest(verb === 'ANY' || verb === '$default' ? 'GET' : verb, parts.join(' ') || '/', stageName(stage)); }}>Test selected route</Button>
  </SpaceBetween></Container>;
}
