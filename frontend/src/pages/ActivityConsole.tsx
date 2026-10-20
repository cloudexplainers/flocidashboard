import React, { useState, useEffect } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Table from '../components/ResourceTable';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Modal from '@cloudscape-design/components/modal';
import Box from '@cloudscape-design/components/box';
import Badge from '@cloudscape-design/components/badge';
import TextFilter from '@cloudscape-design/components/text-filter';
import { CodeSnippet } from '../components/CodeSnippet';

interface ActivityItem {
  id?: string;
  service: string;
  action: string;
  title?: string;
  summary?: string;
  detail?: string;
  timestamp: number | string;
  payload?: any;
  replayable?: boolean;
}

export const ActivityConsole: React.FC<{ onNavigateService?: (serviceKey: string) => void }> = ({ onNavigateService }) => {
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const [filterText, setFilterText] = useState('');
  const [selectedActivity, setSelectedActivity] = useState<ActivityItem | null>(null);
  const [showClearModal, setShowClearModal] = useState(false);

  const loadActivities = () => {
    try {
      const saved = localStorage.getItem('floci_activity') || localStorage.getItem('floci.activity.v1');
      if (saved) {
        const parsed = JSON.parse(saved);
        setActivities(Array.isArray(parsed) ? parsed : []);
        return;
      }
    } catch (e) {
      console.error(e);
    }
    setActivities([]);
  };

  useEffect(() => {
    loadActivities();
  }, []);

  const handleClear = () => {
    try {
      localStorage.removeItem('floci_activity');
      localStorage.removeItem('floci.activity.v1');
    } catch (e) {}
    setActivities([]);
    setShowClearModal(false);
  };

  const filtered = activities.filter((item) => {
    const text = [
      item.service,
      item.action,
      item.title,
      item.summary,
      item.detail,
      JSON.stringify(item.payload || {}),
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    return text.includes(filterText.toLowerCase());
  });

  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header
            variant="h1"
            description="Replayable requests and API operations recorded across local service workbenches."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={loadActivities}>
                  Refresh
                </Button>
                <Button
                  iconName="remove"
                  disabled={activities.length === 0}
                  onClick={() => setShowClearModal(true)}
                >
                  Clear Activity
                </Button>
              </SpaceBetween>
            }
          >
            Activity & Audit Event Log ({activities.length})
          </Header>
        }
      >
        <SpaceBetween size="m">
          <TextFilter
            filteringText={filterText}
            filteringPlaceholder="Filter activity by service, action, payload..."
            onChange={({ detail }) => setFilterText(detail.filteringText)}
          />

          <Table
            columnDefinitions={[
              {
                id: 'service',
                header: 'Service',
                cell: (item) => <Badge color="blue">{item.service ? item.service.toUpperCase() : 'AWS'}</Badge>,
                width: 120,
              },
              {
                id: 'action',
                header: 'Action / Title',
                cell: (item) => (
                  <Button variant="inline-link" onClick={() => setSelectedActivity(item)}>
                    <strong>{item.title || item.action}</strong>
                  </Button>
                ),
              },
              {
                id: 'summary',
                header: 'Summary',
                cell: (item) => item.summary || item.detail || '—',
              },
              {
                id: 'timestamp',
                header: 'Timestamp',
                cell: (item) => (
                  <span style={{ color: '#879596', fontSize: '12px' }}>
                    {item.timestamp ? new Date(item.timestamp).toLocaleString() : 'Just now'}
                  </span>
                ),
                width: 180,
              },
              {
                id: 'nav',
                header: 'Workbench',
                cell: (item) => (
                  <Button
                    variant="normal"
                    onClick={() => {
                      if (onNavigateService && item.service) {
                        onNavigateService(item.service);
                      }
                    }}
                  >
                    Open Service
                  </Button>
                ),
                width: 140,
              },
            ]}
            items={filtered}
            empty={
              <Box textAlign="center" color="inherit">
                <b>No activity recorded yet</b>
                <p>Execute actions in any AWS service console or run Workflow Labs to see activity stream here.</p>
              </Box>
            }
          />
        </SpaceBetween>
      </Container>

      {/* Activity Item Detail Modal */}
      <Modal
        visible={Boolean(selectedActivity)}
        onDismiss={() => setSelectedActivity(null)}
        header={`Activity: ${selectedActivity?.title || selectedActivity?.action}`}
        size="large"
        footer={
          <Box float="right">
            <Button variant="primary" onClick={() => setSelectedActivity(null)}>
              Close
            </Button>
          </Box>
        }
      >
        {selectedActivity && (
          <SpaceBetween size="m">
            <div>
              <strong>Service: </strong> <Badge color="blue">{selectedActivity.service.toUpperCase()}</Badge>
            </div>
            <div>
              <strong>Action: </strong> <code>{selectedActivity.action}</code>
            </div>
            <div>
              <strong>Timestamp: </strong> {selectedActivity.timestamp ? new Date(selectedActivity.timestamp).toLocaleString() : '—'}
            </div>
            {selectedActivity.summary && (
              <div>
                <strong>Summary: </strong> {selectedActivity.summary}
              </div>
            )}
            <Header variant="h3">Request Payload</Header>
            <CodeSnippet language="json" code={JSON.stringify(selectedActivity.payload || {}, null, 2)} />
          </SpaceBetween>
        )}
      </Modal>

      {/* Clear Modal */}
      <Modal
        visible={showClearModal}
        onDismiss={() => setShowClearModal(false)}
        header="Clear Local Activity Log"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setShowClearModal(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleClear}>
                Clear All Activity
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        Are you sure you want to clear all {activities.length} recorded activity logs?
      </Modal>
    </SpaceBetween>
  );
};
