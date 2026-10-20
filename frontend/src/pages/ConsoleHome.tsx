import Alert from '@cloudscape-design/components/alert';
import React, { useState, useEffect } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Cards from '@cloudscape-design/components/cards';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import TextFilter from '@cloudscape-design/components/text-filter';
import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import { ServiceDefinition } from '../types';
import { AwsServiceIcon } from '../components/AwsServiceIcons';
import { useSession } from '../components/SessionContext';
import { FLAT_CURRICULUM_LABS } from '../labCurriculum';

interface ConsoleHomeProps {
  services: ServiceDefinition[];
  onSelectService: (serviceKey: string) => void;
  onNavigateLabs: () => void;
  onNavigateInspector?: () => void;
}

export const ConsoleHome: React.FC<ConsoleHomeProps> = ({
  services,
  onSelectService,
  onNavigateLabs,
  onNavigateInspector,
}) => {
  const [filterText, setFilterText] = useState('');

  const { identity } = useSession();
  const [health, setHealth] = useState<{ ok: boolean; error?: string } | null>(null);
  const [showAll, setShowAll] = useState(false);
  useEffect(() => {
    let alive = true;
    fetch('/api/health/').then(async response => {
      if (!response.ok) throw new Error('Health check failed');
      return response.json();
    }).then(result => { if (alive) setHealth(result); })
      .catch(() => { if (alive) setHealth({ ok: false, error: 'Health check unavailable' }); });
    return () => { alive = false; };
  }, []);
  let recent: string[] = [];
  try { recent = JSON.parse(localStorage.getItem('floci_recent_services') || '[]'); } catch { /* Ignore invalid storage. */ }
  const featured = recent.map(key => services.find(service => service.key === key)).filter((service): service is ServiceDefinition => Boolean(service));
  const recommended = services.filter(service => ['s3', 'ec2', 'iam', 'dynamodb', 'lambda', 'sqs', 'sns', 'rds'].includes(service.key));

  const filteredServices = services.filter(
    (s) =>
      s.title.toLowerCase().includes(filterText.toLowerCase()) ||
      s.category.toLowerCase().includes(filterText.toLowerCase()) ||
      s.key.toLowerCase().includes(filterText.toLowerCase())
  );

  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header
            variant="h1"
            description="Manage resources and explore AWS workflows in your local Floci environment."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="primary" onClick={onNavigateLabs}>
                  Launch Workflow Labs ({FLAT_CURRICULUM_LABS.length})
                </Button>
                <Button onClick={onNavigateInspector || (() => onSelectService('inspector'))}>
                  Open Inspector Inbox
                </Button>
              </SpaceBetween>
            }
          >
            AWS Management Console
          </Header>
        }
      >
        <div className="console-summary">
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Available Services</Box>
            <Box variant="h1" color="text-status-info">
              {services.length}
            </Box>
            <Box variant="small" color="text-status-success">
              <StatusIndicator type="success">Services in this environment</StatusIndicator>
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Workflow Labs</Box>
            <Box variant="h1" color="text-status-info">
              {FLAT_CURRICULUM_LABS.length} Labs
            </Box>
            <Box variant="small" color="text-label">
              {FLAT_CURRICULUM_LABS.reduce((sum, lab) => sum + lab.stepCount, 0)} runnable CLI / Python / Terraform steps
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Local Cloud Health</Box>
            <Box variant="h1">
              <StatusIndicator type={health === null ? 'loading' : health.ok ? 'success' : 'error'}>{health === null ? 'Checking' : health.ok ? 'Healthy' : 'Unavailable'}</StatusIndicator>
            </Box>
            <Box variant="small" color="text-status-success">
              {identity?.endpoint || 'Loading endpoint…'}
            </Box>
          </Box>
        </div>
      </Container>

      {health && !health.ok && <Alert type="error" header="Local cloud connection needs attention" action={<Button onClick={() => onSelectService('settings')}>Review connection settings</Button>}>{health.error || 'The configured endpoint could not be reached. Check the endpoint and credentials, then retry.'}</Alert>}
      <Container header={<Header variant="h2">{featured.length ? 'Recently visited services' : 'Recommended services'}</Header>}>
        <Cards
          cardDefinition={{
            header: (item) => (
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <AwsServiceIcon service={item.key} size={28} />
                <Button variant="inline-link" onClick={() => onSelectService(item.key)}>
                  <strong>{item.title}</strong>
                </Button>
              </div>
            ),
            sections: [
              {
                id: 'category',
                content: (item) => <Badge color="blue">{item.category}</Badge>,
              },
              {
                id: 'eyebrow',
                content: (item) => item.eyebrow,
              },
              {
                id: 'status',
                content: (item) => <Box variant="small">{item.maturity.replace(/_/g, ' ')}</Box>,
              },
            ],
          }}
          items={featured.length ? featured : recommended}
          cardsPerRow={[{ cards: 1 }, { minWidth: 500, cards: 4 }]}
        />
      </Container>

      <Container
        header={
          <Header
            variant="h2"
            counter={`(${filteredServices.length})`}
            description={`Explore ${services.length} local AWS services.`}
          >
            All AWS Services Catalog
          </Header>
        }
      >
        <SpaceBetween size="m">
          <TextFilter
            filteringText={filterText}
            filteringPlaceholder="Filter by service name, category, or keyword..."
            onChange={({ detail }) => setFilterText(detail.filteringText)}
          />
          <Cards
            cardDefinition={{
              header: (item) => (
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <AwsServiceIcon service={item.key} size={24} />
                    <Button variant="inline-link" onClick={() => onSelectService(item.key)}>
                      <strong>{item.title}</strong>
                    </Button>
                  </div>
                  <Badge color={item.maturity === 'interactive_workbench' ? 'green' : 'grey'}>
                    {item.category}
                  </Badge>
                </div>
              ),
              sections: [
                {
                  id: 'description',
                  content: (item) => item.eyebrow,
                },
                {
                  id: 'actions',
                  content: (item) => (
                    <Button variant="normal" iconName="external" onClick={() => onSelectService(item.key)}>
                      Open Console
                    </Button>
                  ),
                },
              ],
            }}
            items={showAll || filterText ? filteredServices : filteredServices.slice(0, 12)}
            cardsPerRow={[{ cards: 1 }, { minWidth: 600, cards: 3 }]}
          />
          {!filterText && filteredServices.length > 12 && <Button onClick={() => setShowAll(!showAll)}>{showAll ? 'Show fewer services' : `Show all ${services.length} services`}</Button>}
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );
};
