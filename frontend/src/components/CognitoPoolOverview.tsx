import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Button from '@cloudscape-design/components/button';

const list = (value: unknown) => Array.isArray(value) ? value.length ? value.join(', ') : 'None configured' : 'Not reported';
export function CognitoPoolOverview({ pool, client, onClients, onTest, detailsOnly = false }: { detailsOnly?: boolean; pool: any; client?: any; onClients: () => void; onTest: () => void }) {
  const details = pool.details || pool;
  const config = client?.details || client || {};
  return <SpaceBetween size="m">
    {!detailsOnly && <><KeyValuePairs columns={3} items={[
      { label: 'User pool ID', value: pool.Id },
      { label: 'Created', value: pool.CreationDate },
      { label: 'Multi-factor authentication', value: details.MfaConfiguration || 'Not reported' },
      { label: 'Sign-in aliases', value: list(details.AliasAttributes || details.UsernameAttributes) },
      { label: 'Auto-verified attributes', value: list(details.AutoVerifiedAttributes) },
      { label: 'Domain', value: details.CustomDomain || details.Domain || 'Not reported' },
    ]} />
    <Container header={<Header variant="h3">Authentication flow</Header>}>
      <div className="resource-flow">
        <section><Header variant="h3">Application</Header><p>{client?.ClientName || 'Select an app client'}</p><Button onClick={onClients}>View app clients</Button></section>
        <span aria-hidden="true">→</span>
        <section><Header variant="h3">User pool</Header><p>{pool.Name}</p><p>{pool.users.length} users reported · {pool.clients.length} app clients reported</p></section>
        <span aria-hidden="true">→</span>
        <section><Header variant="h3">Authentication result</Header><p>Tokens or an authentication challenge, as returned by the endpoint.</p><Button disabled={!client?.ClientId} onClick={onTest}>Test selected client</Button></section>
      </div>
    </Container>
    </>}
    {detailsOnly && client && <Container header={<Header variant="h3" description={client.ClientId} actions={detailsOnly ? <Button onClick={onTest}>Test selected client</Button> : undefined}>App client: {client.ClientName}</Header>}>
      <KeyValuePairs columns={2} items={[
        { label: 'Authentication flows', value: list(config.ExplicitAuthFlows) },
        { label: 'OAuth grant types', value: list(config.AllowedOAuthFlows) },
        { label: 'OAuth scopes', value: list(config.AllowedOAuthScopes) },
        { label: 'Identity providers', value: list(config.SupportedIdentityProviders) },
        { label: 'Callback URLs', value: list(config.CallbackURLs) },
        { label: 'Sign-out URLs', value: list(config.LogoutURLs) },
        { label: 'Access token validity', value: config.AccessTokenValidity == null ? 'Not reported' : `${config.AccessTokenValidity} ${config.TokenValidityUnits?.AccessToken || 'hours'}` },
        { label: 'Refresh token validity', value: config.RefreshTokenValidity == null ? 'Not reported' : `${config.RefreshTokenValidity} ${config.TokenValidityUnits?.RefreshToken || 'days'}` },
      ]} />
    </Container>}
    {!detailsOnly && <KeyValuePairs columns={1} items={[
      { label: 'Local OIDC discovery URL', value: pool.discovery_url || 'Not reported' },
      { label: 'Local JWKS URL', value: pool.jwks_url || 'Not reported' },
    ]} />}
  </SpaceBetween>;
}
