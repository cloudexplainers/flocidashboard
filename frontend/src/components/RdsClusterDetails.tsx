import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Tabs from "@cloudscape-design/components/tabs";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Button from "@cloudscape-design/components/button";
import Box from "@cloudscape-design/components/box";
import {
  RdsCluster,
  RdsInstance,
  rdsStatus, rdsStatusLabel, rdsEngineLabel,
  rdsBoolean,
  rdsTime,
  clusterMembers,
  memberRole,
} from "../pages/rdsResources";
export function RdsClusterDetails({
  cluster,
  instances,
  tab,
  onTabChange,
  onInstance,
}: {
  cluster: RdsCluster;
  instances: RdsInstance[];
  tab: string;
  onTabChange: (tab: string) => void;
  onInstance: (name: string) => void;
}) {
  const members = clusterMembers(cluster, instances);
  return (
    <Tabs
      activeTabId={tab}
      onChange={({ detail }) => onTabChange(detail.activeTabId)}
      tabs={[
        {
          id: "overview",
          label: "Overview",
          content: (
            <Container header={<Header variant="h2">Cluster overview</Header>}>
              <KeyValuePairs
                columns={2}
                minColumnWidth={260}
                items={[
                  { label: "DB cluster identifier", value: cluster.name },
                  {
                    label: "Status",
                    value: (
                      <StatusIndicator type={rdsStatus(cluster.status)}>
                        {rdsStatusLabel(cluster.status)}
                      </StatusIndicator>
                    ),
                  },
                  { label: "Engine", value: rdsEngineLabel(cluster.engine) },
                  {
                    label: "Engine version",
                    value: cluster.engine_version ?? "Not reported",
                  },
                  {
                    label: "Cluster ARN",
                    value: (
                      <span className="rds-resource-value">
                        {cluster.arn ?? "Not reported"}
                      </span>
                    ),
                  },
                  { label: "Created (UTC)", value: rdsTime(cluster.created) },
                  {
                    label: "Database name",
                    value: cluster.database_name ?? "Not reported",
                  },
                  {
                    label: "Master username",
                    value: cluster.master_username ?? "Not reported",
                  },
                  {
                    label: "Storage encrypted",
                    value: rdsBoolean(cluster.storage_encrypted),
                  },
                  {
                    label: "Deletion protection",
                    value: rdsBoolean(cluster.deletion_protection),
                  },
                  {
                    label: "IAM database authentication",
                    value: rdsBoolean(cluster.iam_authentication),
                  },
                  {
                    label: "Parameter group",
                    value: cluster.parameter_group ?? "Not reported",
                  },
                ]}
              />
            </Container>
          ),
        },
        {
          id: "members",
          label: `Members (${members.length})`,
          content: (
            <SpaceBetween size="l">
              <Container
                header={
                  <Header
                    variant="h2"
                    description="Writer and reader roles come from reported cluster membership. A missing role is not inferred from instance order."
                  >
                    Cluster topology
                  </Header>
                }
              >
                <SpaceBetween size="m">
                  <KeyValuePairs
                    columns={2}
                    minColumnWidth={260}
                    items={[
                      {
                        label: "Writer endpoint",
                        value: (
                          <span className="rds-resource-value">
                            {cluster.endpoint ?? "Not reported"}
                          </span>
                        ),
                      },
                      {
                        label: "Reader endpoint",
                        value: (
                          <span className="rds-resource-value">
                            {cluster.reader_endpoint ?? "Not reported"}
                          </span>
                        ),
                      },
                      { label: "Port", value: cluster.port ?? "Not reported" },
                      {
                        label: "Availability Zones",
                        value:
                          cluster.availability_zones?.join(", ") ||
                          "Not reported",
                      },
                    ]}
                  />
                  <div className="rds-member-grid">
                    {members.map((member) => (
                      <Container
                        key={member.DBInstanceIdentifier}
                        header={
                          <Header variant="h3">
                            {member.DBInstanceIdentifier}
                          </Header>
                        }
                      >
                        <SpaceBetween size="m">
                          <KeyValuePairs
                            columns={1}
                            items={[
                              { label: "Role", value: memberRole(member) },
                              {
                                label: "Instance status",
                                value: member.instance?.status ? (
                                  <StatusIndicator
                                    type={rdsStatus(member.instance.status)}
                                  >
                                    {member.instance.status}
                                  </StatusIndicator>
                                ) : (
                                  "Not reported"
                                ),
                              },
                              {
                                label: "Availability Zone",
                                value:
                                  member.instance?.availability_zone ??
                                  "Not reported",
                              },
                              {
                                label: "Promotion tier",
                                value: member.PromotionTier ?? "Not reported",
                              },
                              {
                                label: "Cluster parameter group status",
                                value:
                                  member.DBClusterParameterGroupStatus ??
                                  "Not reported",
                              },
                            ]}
                          />
                          {member.instance ? (
                            <Button
                              onClick={() =>
                                onInstance(member.DBInstanceIdentifier)
                              }
                            >
                              View database
                            </Button>
                          ) : (
                            <Box>
                              Instance details are not reported in this
                              inventory.
                            </Box>
                          )}
                        </SpaceBetween>
                      </Container>
                    ))}
                  </div>
                  {!members.length && <Box>No cluster members reported.</Box>}
                </SpaceBetween>
              </Container>
            </SpaceBetween>
          ),
        },
      ]}
    />
  );
}
