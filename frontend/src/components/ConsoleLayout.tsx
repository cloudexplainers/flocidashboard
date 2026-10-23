import { useSession } from './SessionContext';
import React, { useState, useEffect } from 'react';
import AppLayout from '@cloudscape-design/components/app-layout';
import SideNavigation, { SideNavigationProps } from '@cloudscape-design/components/side-navigation';
import BreadcrumbGroup, { BreadcrumbGroupProps } from '@cloudscape-design/components/breadcrumb-group';
import Flashbar, { FlashbarProps } from '@cloudscape-design/components/flashbar';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import { PARTIAL_LOAD_EVENT, PartialLoadDetail, describeLoadErrors } from '../api/loadErrors';

interface ConsoleLayoutProps {
  breadcrumbs: BreadcrumbGroupProps.Item[];
  sideNavHeader: { href: string; text: string };
  sideNavItems: SideNavigationProps.Item[];
  activeSideNavHref: string;
  onSideNavFollow: (href: string) => void;
  notifications?: FlashbarProps.MessageDefinition[];
  toolsContent?: React.ReactNode;
  children: React.ReactNode;
}

export const ConsoleLayout: React.FC<ConsoleLayoutProps> = ({
  breadcrumbs,
  sideNavHeader,
  sideNavItems,
  activeSideNavHref,
  onSideNavFollow,
  notifications = [],
  toolsContent,
  children,
}) => {
  const { identity, auth } = useSession();
  const [loadError, setLoadError] = useState('');
  const [partialLoad, setPartialLoad] = useState<PartialLoadDetail | null>(null);
  useEffect(() => {
    const handler = (event: Event) => setLoadError((event as CustomEvent<string>).detail);
    const partial = (event: Event) => setPartialLoad((event as CustomEvent<PartialLoadDetail>).detail);
    const clear = () => { setLoadError(''); setPartialLoad(null); };
    window.addEventListener('floci_load_error', handler);
    window.addEventListener(PARTIAL_LOAD_EVENT, partial);
    window.addEventListener('floci_navigate', clear);
    return () => { window.removeEventListener('floci_load_error', handler); window.removeEventListener(PARTIAL_LOAD_EVENT, partial); window.removeEventListener('floci_navigate', clear); };
  }, []);
  const partialLoadText = partialLoad ? describeLoadErrors(partialLoad.errors) : null;
  const partialLoadItems: FlashbarProps.MessageDefinition[] = partialLoad && partialLoadText ? [{
    type: 'warning',
    id: 'floci-partial-load',
    header: 'Some lists are incomplete',
    content: <div data-testid="partial-load-warning">
      Floci did not return these lists in full. A list shown as empty here is not known to be empty.
      <ul style={{ margin: '4px 0 0', paddingLeft: 20 }}>
        {partialLoadText.unsupported.length > 0 && <li>Not implemented by this Floci version: {partialLoadText.unsupported.join(', ')}</li>}
        {partialLoadText.failed.map(line => <li key={line}>{line}</li>)}
      </ul>
    </div>,
    dismissible: true,
    onDismiss: () => setPartialLoad(null),
  }] : [];
  const [toolsOpen, setToolsOpen] = useState(false);

  return (
    <AppLayout
      breadcrumbs={
        <BreadcrumbGroup
          items={breadcrumbs}
          onFollow={(e) => {
            e.preventDefault();
            onSideNavFollow(e.detail.href);
          }}
        />
      }
      navigation={
        <SideNavigation
          header={sideNavHeader}
          activeHref={activeSideNavHref}
          onFollow={(e) => {
            e.preventDefault();
            onSideNavFollow(e.detail.href);
          }}
          items={sideNavItems}
        />
      }
      notifications={<Flashbar items={[...notifications, ...partialLoadItems, ...(loadError ? [{ type: 'error' as const, content: loadError, dismissible: true, onDismiss: () => setLoadError('') }] : [])]} />}
      toolsOpen={toolsOpen}
      onToolsChange={(e) => setToolsOpen(e.detail.open)}
      tools={
        toolsContent || (
          <Container header={<Header variant="h2">AWS Console Info & Help</Header>}>
            <SpaceBetween size="m">
              <p>
                You are using the <strong>Floci Local AWS Console</strong> powered by Cloudscape.
              </p>
              <div>
                <strong>Local Endpoint:</strong>
                <p>
                  <code>{identity?.endpoint || 'Loading…'}</code>
                </p>
              </div>
              <div>
                <strong>Authentication:</strong>
                <p>IAM policy enforcement: {auth?.iam_enforcement === true ? 'Enabled' : auth?.iam_enforcement === false ? 'Disabled' : 'Not known (set in Floci&apos;s environment)'}</p>
              </div>
            </SpaceBetween>
          </Container>
        )
      }
      content={children}
    />
  );
};
