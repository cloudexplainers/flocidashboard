import React, { useState, useEffect } from 'react';
import Modal from '@cloudscape-design/components/modal';
import Box from '@cloudscape-design/components/box';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Button from '@cloudscape-design/components/button';
import Checkbox from '@cloudscape-design/components/checkbox';
import TextFilter from '@cloudscape-design/components/text-filter';
import Grid from '@cloudscape-design/components/grid';
import { ServiceDefinition } from '../types';
import { AwsServiceIcon } from './AwsServiceIcons';

interface FavoritesBarProps {
  activeService: string;
  onSelectService: (serviceKey: string) => void;
  allServices: ServiceDefinition[];
}

const DEFAULT_FAVORITES = [
  's3',
  'ec2',
  'lambda',
  'dynamodb',
  'sqs',
  'sns',
  'iam',
  'cloudwatch',
  'topology',
  'labs',
  'inspector',
];

const SERVICE_SHORT_NAMES: Record<string, string> = {
  s3: 'S3',
  ec2: 'EC2',
  lambda: 'Lambda',
  dynamodb: 'DynamoDB',
  sqs: 'SQS',
  sns: 'SNS',
  iam: 'IAM',
  cloudwatch: 'CloudWatch',
  topology: 'Topology',
  labs: 'Labs',
  inspector: 'Inspector',
  rds: 'RDS',
  kms: 'KMS',
  secretsmanager: 'Secrets Manager',
  cloudformation: 'CloudFormation',
  route53: 'Route 53',
  eventbridge: 'EventBridge',
  stepfunctions: 'Step Functions',
  cognito: 'Cognito',
  apigateway: 'API Gateway',
  ssm: 'SSM',
  console: 'CLI',
  activity: 'Audit Log',
  environment: 'STS',
  settings: 'Settings',
};

export const FavoritesBar: React.FC<FavoritesBarProps> = ({
  activeService,
  onSelectService,
  allServices,
}) => {
  const [favorites, setFavorites] = useState<string[]>(() => {
    try {
      const stored = localStorage.getItem('floci_favorite_services');
      if (stored) {
        const parsed = JSON.parse(stored);
        if (Array.isArray(parsed) && parsed.length > 0) return parsed;
      }
    } catch {}
    return DEFAULT_FAVORITES;
  });

  const [editModalOpen, setEditModalOpen] = useState(false);
  const [modalFilter, setModalFilter] = useState('');
  const [tempFavorites, setTempFavorites] = useState<string[]>(favorites);

  useEffect(() => {
    try {
      localStorage.setItem('floci_favorite_services', JSON.stringify(favorites));
    } catch {}
  }, [favorites]);

  const openEditModal = () => {
    setTempFavorites([...favorites]);
    setModalFilter('');
    setEditModalOpen(true);
  };

  const handleSaveFavorites = () => {
    setFavorites(tempFavorites);
    setEditModalOpen(false);
  };

  const handleResetDefaults = () => {
    setTempFavorites(DEFAULT_FAVORITES);
  };

  const toggleFavorite = (key: string) => {
    if (tempFavorites.includes(key)) {
      setTempFavorites(tempFavorites.filter((k) => k !== key));
    } else {
      setTempFavorites([...tempFavorites, key]);
    }
  };

  // Combine hardcoded special views with allServices
  const catalogList: Array<{ key: string; title: string; category?: string }> = [
    { key: 'labs', title: 'Workflow Labs', category: 'Developer Tools' },
    { key: 'inspector', title: 'Local Inspector Inbox', category: 'Developer Tools' },
    { key: 'topology', title: 'Architecture Topology Graph', category: 'Developer Tools' },
    { key: 'console', title: 'AWS CLI Terminal', category: 'Developer Tools' },
    { key: 'activity', title: 'Activity Audit Log', category: 'Developer Tools' },
    { key: 'environment', title: 'Session Identity & STS', category: 'Developer Tools' },
    { key: 'settings', title: 'Dashboard Settings', category: 'Developer Tools' },
    ...allServices.map((s) => ({ key: s.key, title: s.title, category: s.category })),
  ];

  // Remove duplicates
  const uniqueCatalog = Array.from(new Map(catalogList.map((item) => [item.key, item])).values());

  const filteredCatalog = uniqueCatalog.filter(
    (c) =>
      c.title.toLowerCase().includes(modalFilter.toLowerCase()) ||
      c.key.toLowerCase().includes(modalFilter.toLowerCase()) ||
      (c.category && c.category.toLowerCase().includes(modalFilter.toLowerCase()))
  );

  return (
    <>
      <div
        style={{
          background: '#0d131e',
          borderBottom: '1px solid #1f2a3a',
          height: '34px',
          display: 'flex',
          alignItems: 'center',
          padding: '0 16px',
          overflowX: 'auto',
          whiteSpace: 'nowrap',
          zIndex: 90,
          boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
          fontFamily: 'inherit',
        }}
      >
        <span
          style={{
            color: '#ec7211',
            fontSize: '13px',
            marginRight: '8px',
            display: 'inline-flex',
            alignItems: 'center',
            userSelect: 'none',
          }}
          title="Favorites"
        >
          ★
        </span>

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
            flex: 1,
            overflowX: 'auto',
          }}
        >
          {favorites.map((key) => {
            const isActive = activeService === key;
            const shortName =
              SERVICE_SHORT_NAMES[key] ||
              uniqueCatalog.find((s) => s.key === key)?.title ||
              key.toUpperCase();

            return (
              <button
                key={key}
                aria-label={`Open ${shortName}`}
                aria-current={isActive ? 'page' : undefined}
                onClick={() => onSelectService(key)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '3px 8px',
                  borderRadius: '3px',
                  fontSize: '13px',
                  fontFamily: 'inherit',
                  fontWeight: isActive ? 600 : 400,
                  color: isActive ? '#58a6ff' : '#d1d5db',
                  background: isActive ? 'rgba(9, 114, 211, 0.2)' : 'transparent',
                  border: isActive ? '1px solid #0972d3' : '1px solid transparent',
                  cursor: 'pointer',
                  transition: 'all 0.12s ease',
                }}
                onMouseEnter={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.background = 'rgba(255, 255, 255, 0.08)';
                    e.currentTarget.style.color = '#ffffff';
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.background = 'transparent';
                    e.currentTarget.style.color = '#d1d5db';
                  }
                }}
              >
                <AwsServiceIcon service={key} size={15} />
                <span>{shortName}</span>
              </button>
            );
          })}
        </div>

        <button
          onClick={openEditModal}
          style={{
            background: 'transparent',
            border: 'none',
            color: '#879596',
            fontSize: '12px',
            fontFamily: 'inherit',
            cursor: 'pointer',
            padding: '4px 8px',
            borderRadius: '3px',
            marginLeft: '8px',
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
            transition: 'color 0.15s ease',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = '#ffffff')}
          onMouseLeave={(e) => (e.currentTarget.style.color = '#879596')}
        >
          <span>⚙</span>
          <span>Edit</span>
        </button>
      </div>

      {/* Edit Favorites Modal */}
      <Modal
        visible={editModalOpen}
        onDismiss={() => setEditModalOpen(false)}
        header="Customize Favorites Bar"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={handleResetDefaults}>
                Reset to Defaults
              </Button>
              <Button variant="link" onClick={() => setEditModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleSaveFavorites}>
                Save Favorites ({tempFavorites.length})
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <TextFilter
            filteringText={modalFilter}
            filteringPlaceholder="Filter services to pin/unpin..."
            onChange={({ detail }) => setModalFilter(detail.filteringText)}
          />

          <div style={{ maxHeight: '400px', overflowY: 'auto', padding: '4px' }}>
            <Grid
              gridDefinition={[
                { colspan: { default: 12, s: 6, m: 4 } },
                { colspan: { default: 12, s: 6, m: 4 } },
                { colspan: { default: 12, s: 6, m: 4 } },
              ]}
            >
              {filteredCatalog.map((item) => {
                const isChecked = tempFavorites.includes(item.key);
                return (
                  <div
                    key={item.key}
                    onClick={() => toggleFavorite(item.key)}
                    style={{
                      padding: '8px 10px',
                      borderRadius: '4px',
                      background: isChecked ? 'rgba(9, 114, 211, 0.15)' : 'rgba(255,255,255,0.03)',
                      border: isChecked ? '1px solid #0972d3' : '1px solid #232f3e',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                      marginBottom: '8px',
                    }}
                  >
                    <Checkbox checked={isChecked} onChange={() => toggleFavorite(item.key)} />
                    <AwsServiceIcon service={item.key} size={18} />
                    <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      <strong style={{ fontSize: '12px' }}>{item.title}</strong>
                      {item.category && (
                        <div style={{ fontSize: '10px', color: '#879596' }}>{item.category}</div>
                      )}
                    </div>
                  </div>
                );
              })}
            </Grid>
          </div>
        </SpaceBetween>
      </Modal>
    </>
  );
};
