import { useState } from 'react';
import Alert from '@cloudscape-design/components/alert';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from './ResourceTable';
import { ResourceDetails, ResourceValue } from './ResourceDetails';

export function DistributionBehaviors({ distribution }: { distribution: any }) {
  const [originId, setOrigin] = useState('');
  const origins = distribution.Origins?.Items || [];
  const origin = origins.find((item: any) => item.Id === originId);
  const behaviors = [...(distribution.DefaultCacheBehavior ? [{ ...distribution.DefaultCacheBehavior, PathPattern: 'Default (*)' }] : []), ...(distribution.CacheBehaviors?.Items || [])];
  return <SpaceBetween size="m">{distribution.configuration_error && <Alert type="warning">Distribution configuration could not be retrieved: {distribution.configuration_error}</Alert>}<Container header={<Header variant="h3" description="Cache behaviors map request paths to origin IDs. The default behavior applies when no path behavior matches.">Cache behavior routing</Header>}><Table items={behaviors} columnDefinitions={[
    { id: 'path', header: 'Path pattern', cell: (item: any) => item.PathPattern },
    { id: 'origin', header: 'Target origin', cell: (item: any) => <Button variant="inline-link" disabled={!origins.some((origin: any) => origin.Id === item.TargetOriginId)} onClick={() => setOrigin(item.TargetOriginId)}>{item.TargetOriginId || 'Not reported'}</Button> },
    { id: 'protocol', header: 'Viewer protocol', cell: (item: any) => item.ViewerProtocolPolicy || 'Not reported' },
    { id: 'cache', header: 'Cache policy', cell: (item: any) => item.CachePolicyId || 'Not reported' },
    { id: 'compress', header: 'Compression', cell: (item: any) => <ResourceValue value={item.Compress} /> },
  ]} empty={<span>No cache behaviors reported.</span>} /></Container>{origin && <ResourceDetails resource={origin} title={`Origin: ${origin.Id}`} />}</SpaceBetween>;
}
