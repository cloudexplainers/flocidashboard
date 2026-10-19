import React, { useState } from 'react';
import TopNavigation from '@cloudscape-design/components/top-navigation';
import { IdentityInfo, ServiceDefinition } from '../types';
import { AwsServiceIcon } from './AwsServiceIcons';
import { FLAT_CURRICULUM_LABS } from '../labCurriculum';

interface TopNavProps {
  identity: IdentityInfo | null;
  services: ServiceDefinition[];
  onSelectService: (serviceKey: string) => void;
  onNavigateHome: () => void;
  onNavigateLabs: () => void;
}

export const TopNav: React.FC<TopNavProps> = ({
  identity,
  services,
  onSelectService,
  onNavigateHome,
  onNavigateLabs,
}) => {
  const [searchValue, setSearchValue] = useState('');

  return (
    <TopNavigation
      identity={{
        href: '#',
        title: 'floci',
        onFollow: (e) => {
          e.preventDefault();
          onNavigateHome();
        },
      }}
      search={
        <div style={{ position: 'relative', width: '320px' }}>
          <input
            type="search"
            value={searchValue}
            onChange={(e) => setSearchValue(e.target.value)}
            placeholder="Search services (e.g. S3, EC2, IAM, Labs)..."
            style={{
              width: '100%',
              padding: '6px 12px',
              borderRadius: '4px',
              border: '1px solid #545b64',
              background: '#232f3e',
              color: '#ffffff',
              fontSize: '13px',
            }}
          />
          {searchValue.trim().length > 0 && (
            <div
              style={{
                position: 'absolute',
                top: '100%',
                left: 0,
                right: 0,
                marginTop: '4px',
                background: '#161e2e',
                border: '1px solid #414d5c',
                borderRadius: '4px',
                boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
                zIndex: 9999,
                maxHeight: '300px',
                overflowY: 'auto',
              }}
            >
              {services
                .filter(
                  (s) =>
                    s.title.toLowerCase().includes(searchValue.toLowerCase()) ||
                    s.key.toLowerCase().includes(searchValue.toLowerCase()) ||
                    s.category.toLowerCase().includes(searchValue.toLowerCase())
                )
                .slice(0, 10)
                .map((s) => (
                  <div
                    key={s.key}
                    onClick={() => {
                      setSearchValue('');
                      onSelectService(s.key);
                    }}
                    style={{
                      padding: '8px 12px',
                      cursor: 'pointer',
                      borderBottom: '1px solid #232f3e',
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = '#232f3e')}
                    onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <AwsServiceIcon service={s.key} size={20} />
                      <div>
                        <strong style={{ color: '#fff', fontSize: '13px' }}>{s.title}</strong>
                        <span style={{ color: '#879596', fontSize: '11px', marginLeft: '8px' }}>
                          {s.category}
                        </span>
                      </div>
                    </div>
                    <span style={{ color: '#ec7211', fontSize: '11px', fontWeight: 600 }}>
                      Open ↗
                    </span>
                  </div>
                ))}
            </div>
          )}
        </div>
      }
      utilities={[
        {
          type: 'button',
          text: `Workflow Labs (${FLAT_CURRICULUM_LABS.length})`,
          iconName: 'folder-open',
          onClick: () => onNavigateLabs(),
        },
        {
          type: 'menu-dropdown',
          text: identity ? `${identity.region}` : 'us-east-1',
          iconName: 'globe',
          items: [
            { id: 'us-east-1', text: 'US East (N. Virginia) us-east-1' },
            { id: 'us-west-2', text: 'US West (Oregon) us-west-2' },
            { id: 'eu-west-1', text: 'Europe (Ireland) eu-west-1' },
          ],
        },
        {
          type: 'menu-dropdown',
          text: identity ? `${identity.user_id} @ ${identity.account_id}` : 'Floci Admin',
          description: identity?.arn || 'arn:aws:iam::000000000000:root',
          iconName: 'user-profile',
          onItemClick: ({ detail }) => {
            if (['environment', 'settings', 'console', 'activity'].includes(detail.id)) {
              onSelectService(detail.id);
            }
          },
          items: [
            { id: 'environment', text: 'Session Identity & STS Assumer', iconName: 'user-profile' },
            { id: 'settings', text: 'Dashboard Settings & Endpoints', iconName: 'settings' },
            { id: 'console', text: 'AWS CLI Terminal Sandbox', iconName: 'script' },
            { id: 'activity', text: 'Activity & Audit Log', iconName: 'status-info' },
          ],
        },
      ]}
    />
  );
};
