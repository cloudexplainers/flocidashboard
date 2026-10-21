import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Box from "@cloudscape-design/components/box";
import { AppSyncApi } from "../pages/appsyncResources";

export function AppSyncOverview({ api }: { api: AppSyncApi }) {
  return (
    <SpaceBetween size="l">
      <Container header={<Header variant="h2">API details</Header>}>
        <KeyValuePairs
          minColumnWidth={260}
          columns={2}
          items={[
            { label: "API ID", value: api.apiId },
            { label: "API ARN", value: api.arn || "Not reported" },
            {
              label: "Default authorization",
              value: api.authenticationType || "Not reported",
            },
            {
              label: "GraphQL endpoint",
              value: (
                <span className="appsync-resource-value">
                  {api.uris?.GRAPHQL || "Not reported"}
                </span>
              ),
            },
            {
              label: "Schema status",
              value: api.schema_status?.status || "Not reported",
            },
            {
              label: "Additional authorization",
              value:
                api.additionalAuthenticationProviders
                  ?.map((provider: any) => provider.authenticationType)
                  .join(", ") || "None reported",
            },
          ]}
        />
      </Container>
      <Container
        header={
          <Header
            variant="h2"
            description="A GraphQL field is resolved through its configured resolver and data source. This view shows configuration, not a request trace."
          >
            GraphQL request flow
          </Header>
        }
      >
        <div className="resource-flow appsync-request-flow">
          <section>
            <Header variant="h3">GraphQL API</Header>
            <p>{api.name}</p>
            <p>{api.authenticationType || "Authorization not reported"}</p>
          </section>
          <span aria-hidden="true">→</span>
          <section>
            <Header variant="h3">Field resolvers</Header>
            {api.resolvers.length ? (
              api.resolvers.map((resolver: any) => (
                <p key={`${resolver.typeName}.${resolver.fieldName}`}>
                  {resolver.typeName}.{resolver.fieldName}
                  {resolver.kind ? ` · ${resolver.kind}` : ""}
                  {resolver.dataSourceName
                    ? ` → ${resolver.dataSourceName}`
                    : resolver.kind === "PIPELINE"
                      ? ` → ${(resolver.pipelineConfig?.functions || []).length} configured functions`
                      : ""}
                </p>
              ))
            ) : (
              <p>No resolvers reported.</p>
            )}
          </section>
          <span aria-hidden="true">→</span>
          <section>
            <Header variant="h3">Data sources</Header>
            {api.data_sources.length ? (
              api.data_sources.map((source: any) => (
                <p key={source.name}>
                  {source.name} · {source.type || "Type not reported"}
                </p>
              ))
            ) : (
              <p>No data sources reported.</p>
            )}
          </section>
        </div>
        <Box margin={{ top: "m" }}>
          {api.resolvers.length} resolvers · {api.data_sources.length} data
          sources · {api.functions.length} pipeline functions ·{" "}
          {api.api_keys.length} API keys reported
        </Box>
      </Container>
    </SpaceBetween>
  );
}
