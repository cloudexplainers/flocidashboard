import { useEffect, useState } from "react";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Button from "@cloudscape-design/components/button";
import Alert from "@cloudscape-design/components/alert";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Box from "@cloudscape-design/components/box";
import Table from "./ResourceTable";
import { CodeSnippet } from "./CodeSnippet";
import { describeCloudFormationChangeSet } from "../api/client";
import {
  canExecuteChangeSet,
  cfnStatus,
  cfnTime,
} from "../pages/cloudformationResources";

export function ChangeSetReview({
  stack,
  name,
  onExecute,
  onDelete,
}: {
  stack: string;
  name: string;
  onExecute: (change: any) => void;
  onDelete: (name: string) => void;
}) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [selected, setSelected] = useState<any>(null);
  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setError("");
    setSelected(null);
    setData(null);
    describeCloudFormationChangeSet(stack, name)
      .then((payload) => {
        if (!disposed) setData(payload.change_set);
      })
      .catch((error) => {
        if (!disposed) setError(error.message);
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [stack, name, refresh]);
  return (
    <Container
      header={
        <Header
          variant="h2"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                iconName="refresh"
                loading={loading}
                onClick={() => setRefresh((value) => value + 1)}
              >
                Refresh change set
              </Button>
              <Button disabled={!data} onClick={() => onDelete(name)}>
                Delete change set
              </Button>
              <Button
                variant="primary"
                disabled={!canExecuteChangeSet(data)}
                onClick={() => onExecute(data)}
              >
                Execute change set
              </Button>
            </SpaceBetween>
          }
        >
          Change set: {name}
        </Header>
      }
    >
      <SpaceBetween size="m">
        {error && <Alert type="error">{error}</Alert>}
        {loading && (
          <StatusIndicator type="loading">
            Loading change set details
          </StatusIndicator>
        )}
        {data && (
          <SpaceBetween size="m">
            <KeyValuePairs
              columns={2}
              minColumnWidth={260}
              items={[
                {
                  label: "Creation status",
                  value: (
                    <StatusIndicator type={cfnStatus(data.Status)}>
                      {data.Status || "Not reported"}
                    </StatusIndicator>
                  ),
                },
                {
                  label: "Execution status",
                  value: (
                    <StatusIndicator type={cfnStatus(data.ExecutionStatus)}>
                      {data.ExecutionStatus || "Not reported"}
                    </StatusIndicator>
                  ),
                },
                {
                  label: "Status reason",
                  value: data.StatusReason || "None reported",
                },
                { label: "Created (UTC)", value: cfnTime(data.CreationTime) },
                { label: "Stack", value: data.StackName || stack },
                {
                  label: "Change set ID",
                  value: (
                    <span className="cfn-resource-value">
                      {data.ChangeSetId || "Not reported"}
                    </span>
                  ),
                },
              ]}
            />
            <Box>
              Review the resource actions and replacement requirements before
              execution. This is the service’s proposed change set; execution
              status must be AVAILABLE to run it.
            </Box>
            <Table
              header={
                <Header
                  variant="h3"
                  counter={`(${Array.isArray(data.Changes) ? data.Changes.length : 0})`}
                >
                  Resource changes
                </Header>
              }
              items={Array.isArray(data.Changes) ? data.Changes : []}
              columnDefinitions={[
                {
                  id: "logical",
                  header: "Logical ID",
                  cell: (change: any) => (
                    <Button
                      variant="inline-link"
                      onClick={() => setSelected(change)}
                    >
                      {change.ResourceChange?.LogicalResourceId ||
                        "Not reported"}
                    </Button>
                  ),
                  isRowHeader: true,
                },
                {
                  id: "action",
                  header: "Action",
                  cell: (change: any) =>
                    change.ResourceChange?.Action || "Not reported",
                },
                {
                  id: "type",
                  header: "Resource type",
                  cell: (change: any) =>
                    change.ResourceChange?.ResourceType || "Not reported",
                },
                {
                  id: "replacement",
                  header: "Replacement",
                  cell: (change: any) =>
                    change.ResourceChange?.Replacement ?? "Not reported",
                },
                {
                  id: "scope",
                  header: "Scope",
                  cell: (change: any) =>
                    change.ResourceChange?.Scope?.join(", ") || "Not reported",
                },
              ]}
              empty={
                <Box>
                  No resource changes were reported. An empty list alone does
                  not establish whether this service supports a complete
                  resource diff.
                </Box>
              }
            />
            {selected && (
              <Container
                header={
                  <Header variant="h3">
                    Change details: {selected.ResourceChange?.LogicalResourceId}
                  </Header>
                }
              >
                <CodeSnippet
                  language="json"
                  code={JSON.stringify(selected, null, 2)}
                />
              </Container>
            )}
          </SpaceBetween>
        )}
      </SpaceBetween>
    </Container>
  );
}
