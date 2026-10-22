import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Tabs from "@cloudscape-design/components/tabs";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Alert from "@cloudscape-design/components/alert";
import Button from "@cloudscape-design/components/button";
import FormField from "@cloudscape-design/components/form-field";
import Textarea from "@cloudscape-design/components/textarea";
import { CodeSnippet } from "./CodeSnippet";
import {
  RdsInstance,
  RdsCluster,
  rdsStatus, rdsStatusLabel, rdsEngineLabel,
  rdsBoolean,
  rdsTime,
  rdsConnection,
  memberRole,
} from "../pages/rdsResources";
const present = (value: any) => value ?? "Not reported";
export function RdsDatabaseDetails({
  db,
  cluster,
  tab,
  onTabChange,
  onCluster,
}: {
  db: RdsInstance;
  cluster?: RdsCluster;
  tab: string;
  onTabChange: (tab: string) => void;
  onCluster: (name: string) => void;
}) {
  const connection = rdsConnection(db);
  const membership = cluster?.members.find(
    (member) => member.DBInstanceIdentifier === db.name,
  );
  return (
    <Tabs
      activeTabId={tab}
      onChange={({ detail }) => onTabChange(detail.activeTabId)}
      tabs={[
        {
          id: "overview",
          label: "Overview",
          content: (
            <Container header={<Header variant="h2">Database overview</Header>}>
              <KeyValuePairs
                columns={2}
                minColumnWidth={260}
                items={[
                  { label: "DB identifier", value: db.name },
                  {
                    label: "Status",
                    value: (
                      <StatusIndicator type={rdsStatus(db.status)}>
                        {rdsStatusLabel(db.status)}
                      </StatusIndicator>
                    ),
                  },
                  { label: "Engine", value: rdsEngineLabel(db.engine) },
                  {
                    label: "Engine version",
                    value: present(db.engine_version),
                  },
                  { label: "Instance class", value: present(db.class) },
                  {
                    label: "Availability Zone",
                    value: present(db.availability_zone),
                  },
                  {
                    label: "DB instance ARN",
                    value: (
                      <span className="rds-resource-value">
                        {present(db.arn)}
                      </span>
                    ),
                  },
                  { label: "Created (UTC)", value: rdsTime(db.created) },
                  {
                    label: "DB cluster",
                    value: db.cluster_identifier ? (
                      <Button
                        variant="inline-link"
                        onClick={() => onCluster(db.cluster_identifier)}
                      >
                        {db.cluster_identifier}
                      </Button>
                    ) : (
                      "None reported"
                    ),
                  },
                  {
                    label: "Cluster role",
                    value: membership ? memberRole(membership) : "Not reported",
                  },
                ]}
              />
            </Container>
          ),
        },
        {
          id: "connectivity",
          label: "Connectivity",
          content: (
            <SpaceBetween size="l">
              <Container
                header={<Header variant="h2">Connectivity and security</Header>}
              >
                <KeyValuePairs
                  columns={2}
                  minColumnWidth={260}
                  items={[
                    {
                      label: "Reported endpoint",
                      value: (
                        <span className="rds-resource-value">
                          {present(db.connect_host)}
                        </span>
                      ),
                    },
                    { label: "Port", value: present(db.connect_port) },
                    { label: "Database name", value: present(db.db_name) },
                    {
                      label: "Master username",
                      value: present(db.master_username),
                    },
                    {
                      label: "Publicly accessible",
                      value: rdsBoolean(db.publicly_accessible),
                    },
                    {
                      label: "IAM database authentication",
                      value: rdsBoolean(db.iam_authentication),
                    },
                    {
                      label: "Subnet group",
                      value:
                        db.subnet_group?.DBSubnetGroupName || "Not reported",
                    },
                    {
                      label: "VPC",
                      value: db.subnet_group?.VpcId || "Not reported",
                    },
                    {
                      label: "VPC security groups",
                      value: Array.isArray(db.vpc_security_groups)
                        ? db.vpc_security_groups
                            .map(
                              (group: any) =>
                                `${group.VpcSecurityGroupId}${group.Status ? ` (${group.Status})` : ""}`,
                            )
                            .join(", ") || "None reported"
                        : "Not reported",
                    },
                    {
                      label: "Subnets",
                      value:
                        db.subnet_group?.Subnets?.map(
                          (subnet: any) => subnet.SubnetIdentifier,
                        ).join(", ") || "Not reported",
                    },
                  ]}
                />
              </Container>
              <Container
                header={
                  <Header
                    variant="h2"
                    description="These password-authentication examples use reported connection values. Passwords are prompted at runtime; endpoint presence does not verify reachability."
                  >
                    Connection examples
                  </Header>
                }
              >
                <SpaceBetween size="m">
                  {connection.cli ? (
                    <SpaceBetween size="m">
                      <FormField label="CLI Connection Command">
                        <Textarea value={connection.cli} rows={4} readOnly />
                      </FormField>
                      <FormField label="Python Connection Snippet">
                        <Textarea
                          value={connection.python || ""}
                          rows={12}
                          readOnly
                        />
                      </FormField>
                    </SpaceBetween>
                  ) : (
                    <Alert type="info">{connection.reason}</Alert>
                  )}
                </SpaceBetween>
              </Container>
            </SpaceBetween>
          ),
        },
        {
          id: "configuration",
          label: "Configuration",
          content: (
            <SpaceBetween size="l">
              <Container
                header={<Header variant="h2">Storage and configuration</Header>}
              >
                <KeyValuePairs
                  columns={2}
                  minColumnWidth={260}
                  items={[
                    {
                      label: "Allocated storage",
                      value:
                        db.allocated_storage == null
                          ? "Not reported"
                          : `${db.allocated_storage} GiB`,
                    },
                    { label: "Storage type", value: present(db.storage_type) },
                    {
                      label: "Storage encrypted",
                      value: rdsBoolean(db.storage_encrypted),
                    },
                    { label: "Multi-AZ", value: rdsBoolean(db.multi_az) },
                    {
                      label: "Secondary Availability Zone",
                      value: present(db.secondary_availability_zone),
                    },
                    {
                      label: "Deletion protection",
                      value: rdsBoolean(db.deletion_protection),
                    },
                    {
                      label: "Backup retention",
                      value:
                        db.backup_retention_period == null
                          ? "Not reported"
                          : `${db.backup_retention_period} days`,
                    },
                    {
                      label: "Parameter groups",
                      value:
                        db.parameter_groups
                          ?.map(
                            (group: any) =>
                              `${group.DBParameterGroupName}${group.ParameterApplyStatus ? ` (${group.ParameterApplyStatus})` : ""}`,
                          )
                          .join(", ") || "None reported",
                    },
                  ]}
                />
              </Container>
              {db.pending_modifications && (
                <Container
                  header={<Header variant="h2">Pending modifications</Header>}
                >
                  <CodeSnippet
                    language="json"
                    code={JSON.stringify(db.pending_modifications, null, 2)}
                  />
                </Container>
              )}
            </SpaceBetween>
          ),
        },
      ]}
    />
  );
}
