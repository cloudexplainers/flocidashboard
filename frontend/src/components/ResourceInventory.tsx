import { useEffect, useRef, useState } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Button from '@cloudscape-design/components/button';
import Select from '@cloudscape-design/components/select';
import TextFilter from '@cloudscape-design/components/text-filter';
import Alert from '@cloudscape-design/components/alert';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Box from '@cloudscape-design/components/box';
import Table from './ResourceTable';
import { ResourceDetails, ResourceValue, attributeLabel } from './ResourceDetails';
import { ServiceDefinition } from '../types';

const label = attributeLabel;
export function ResourceInventory({ service, revision = 0, onSelectResource }: { service: ServiceDefinition; revision?: number; onSelectResource?: (resource: any) => void }) {
  const [collections, setCollections] = useState<Record<string, any[]>>({});
  const [kind, setKind] = useState('');
  const [filter, setFilter] = useState('');
  const [selected, setSelected] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const onSelect = useRef(onSelectResource);
  onSelect.current = onSelectResource;
  const selectResources = (items: any[]) => { setSelected(items); onSelect.current?.(items[0] || null); };
  useEffect(() => {
    let alive = true;
    setLoading(true); setError(''); selectResources([]);
    fetch(service.api_path || `/api/${service.key}/`).then(async response => {
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || 'Inventory unavailable');
      return data;
    }).then(data => {
      if (!alive) return;
      const groups = Object.fromEntries(Object.entries(data).filter(([key, value]) => Array.isArray(value) && !/(supported|operations|warnings|errors)/i.test(key)).map(([key, value]) => [key, (value as any[]).map(item => typeof item === 'object' && item !== null ? item : { value: item })]));
      setCollections(groups);
      setKind(previous => previous in groups ? previous : Object.keys(groups)[0] || '');
    }).catch(err => { if (alive) { setError(err.message); setCollections({}); } }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [service.key, service.api_path, refresh, revision]);
  const items = collections[kind] || [];
  const allKeys = [...new Set(items.flatMap(item => Object.keys(item)))].filter(key => items.some(item => ['string', 'number', 'boolean'].includes(typeof item[key])));
  const rank = (key: string) => /^(name|.*Name|domainName|DomainName)$/.test(key) ? 0 : /^(id|.*Id|arn|.*Arn|ARN)$/.test(key) ? 1 : /status|state/i.test(key) ? 2 : 3;
  const keys = allKeys.sort((a, b) => rank(a) - rank(b)).slice(0, 8);
  const filtered = items.filter(item => JSON.stringify(item).toLowerCase().includes(filter.toLowerCase()));
  return <Container header={<Header variant="h2" description="Inspect resources before choosing an operation." actions={<Button onClick={() => setRefresh(refresh + 1)} iconName="refresh" loading={loading}>Refresh resources</Button>}>Resource inventory</Header>}>
    <SpaceBetween size="m">
      {error && <Alert type="error">{error}</Alert>}
      <Select ariaLabel="Resource type" selectedOption={kind ? { label: label(kind), value: kind } : null} placeholder="Resource type" options={Object.keys(collections).map(key => ({ value: key, label: `${label(key)} (${collections[key].length})` }))} onChange={({ detail }) => { setKind(detail.selectedOption.value!); selectResources([]); setFilter(''); }} />
      <TextFilter filteringText={filter} filteringPlaceholder="Find resources" onChange={({ detail }) => { setFilter(detail.filteringText); selectResources([]); }} />
      <Table loading={loading} loadingText="Loading resources" selectionType="single" selectedItems={selected} onSelectionChange={({ detail }) => selectResources([...detail.selectedItems])} columnDefinitions={keys.length ? keys.map(key => ({ id: key, header: label(key), sortingField: key, minWidth: key === keys[0] ? 160 : 140, cell: (item: any) => key === keys[0] ? <Button variant="inline-link" onClick={() => selectResources([item])}>{String(item[key] ?? 'Not reported')}</Button> : <div style={{ maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={item[key] == null ? 'Not reported' : String(item[key])}>{/status|state/i.test(key) || typeof item[key] === 'boolean' ? <ResourceValue name={key} value={item[key]} /> : String(item[key] ?? 'Not reported')}</div> })) : [{ id: 'resource', header: 'Resource', cell: (item: any) => JSON.stringify(item) }]} items={filtered} empty={<Box textAlign="center">{filter ? 'No resources match your filter.' : 'No resources reported by this endpoint.'}</Box>} />
      {selected[0] && <ResourceDetails resource={selected[0]} />}
    </SpaceBetween>
  </Container>;
}
