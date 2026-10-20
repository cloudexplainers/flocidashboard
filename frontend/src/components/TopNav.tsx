import React, { useState } from 'react';
import Autosuggest from '@cloudscape-design/components/autosuggest';
import TopNavigation from '@cloudscape-design/components/top-navigation';
import { IdentityInfo, ServiceDefinition } from '../types';
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
        <Autosuggest
          ariaLabel="Search AWS services"
          value={searchValue}
          onChange={({ detail }) => setSearchValue(detail.value)}
          onSelect={({ detail }) => {
            if (detail.selectedOption?.value) {
              onSelectService(detail.selectedOption.value);
              setSearchValue('');
            }
          }}
          options={services.map(service => ({ value: service.key, label: service.title, description: service.category }))}
          filteringType="auto"
          hideEnteredTextOption
          selectedAriaLabel="Selected"
          placeholder="Search AWS services"
          empty="No matching services"
          enteredTextLabel={value => `Search for ${value}`}
        />
      }
      utilities={[
        {
          type: 'button',
          text: `Workflow Labs (${FLAT_CURRICULUM_LABS.length})`,
          iconName: 'folder-open',
          onClick: () => onNavigateLabs(),
        },
        {
          type: 'button',
          text: identity?.region || 'Loading region…',
          iconName: 'globe',
          onClick: () => onSelectService('settings'),
        },
        {
          type: 'menu-dropdown',
          text: identity ? `${identity.user_id} @ ${identity.account_id}` : 'Loading identity…',
          description: identity?.arn || 'Resolving caller identity',
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
