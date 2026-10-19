import React, { useState } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Cards from '@cloudscape-design/components/cards';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Grid from '@cloudscape-design/components/grid';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import TextFilter from '@cloudscape-design/components/text-filter';
import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import { ServiceDefinition } from '../types';
import { AwsServiceIcon } from '../components/AwsServiceIcons';
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

  const featured = services.filter((s) =>
    ['s3', 'ec2', 'iam', 'dynamodb', 'lambda', 'sqs', 'sns', 'rds'].includes(s.key)
  );

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
            description="Build, test, and orchestrate local AWS cloud infrastructure offline with 1:1 fidelity."
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
        <Grid gridDefinition={[{ colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }]}>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Active Services</Box>
            <Box variant="h1" color="text-status-info">
              65 / 65
            </Box>
            <Box variant="small" color="text-status-success">
              <StatusIndicator type="success">100% Pedagogical Coverage</StatusIndicator>
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Workflow Labs</Box>
            <Box variant="h1" color="text-status-info">
              63 Labs
            </Box>
            <Box variant="small" color="text-label">
              349 runnable CLI / Python / Terraform steps
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Local Cloud Health</Box>
            <Box variant="h1" color="text-status-success">
              Healthy
            </Box>
            <Box variant="small" color="text-status-success">
              <StatusIndicator type="success">http://localhost:4566</StatusIndicator>
            </Box>
          </Box>
        </Grid>
      </Container>

      <Container header={<Header variant="h2">Recently Visited & Recommended Services</Header>}>
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
                content: () => <StatusIndicator type="success">Interactive Workbench</StatusIndicator>,
              },
            ],
          }}
          items={featured}
          cardsPerRow={[{ cards: 1 }, { minWidth: 500, cards: 4 }]}
        />
      </Container>

      <Container
        header={
          <Header
            variant="h2"
            counter={`(${filteredServices.length})`}
            description="Explore and manage all 65 emulated AWS services."
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
            items={filteredServices}
            cardsPerRow={[{ cards: 1 }, { minWidth: 600, cards: 3 }]}
          />
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );
};
