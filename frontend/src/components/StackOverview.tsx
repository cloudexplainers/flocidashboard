import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Alert from "@cloudscape-design/components/alert";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import {
  cfnStatus,
  cfnTime,
  StackItem,
} from "../pages/cloudformationResources";

export function StackOverview({ stack }: { stack: StackItem }) {
  const latestFailure = stack.Events.find((event) =>
    (event.ResourceStatus || "").includes("FAILED"),
  );
  return (
    <SpaceBetween size="l">
      <Container header={<Header variant="h2">Stack overview</Header>}>
        <KeyValuePairs
          columns={2}
          minColumnWidth={260}
          items={[
            {
              label: "Status",
              value: (
                <StatusIndicator type={cfnStatus(stack.StackStatus)}>
                  {stack.StackStatus || "Not reported"}
                </StatusIndicator>
              ),
            },
            {
              label: "Status reason",
              value: stack.StackStatusReason || "None reported",
            },
            {
              label: "Stack ID",
              value: (
                <span className="cfn-resource-value">
                  {stack.StackId || "Not reported"}
                </span>
              ),
            },
            {
              label: "Description",
              value: stack.Description || "Not configured",
            },
            { label: "Created (UTC)", value: cfnTime(stack.CreationTime) },
            { label: "Last updated (UTC)", value: cfnTime(stack.UpdatedTime) },
            {
              label: "Rollback on failure",
              value:
                stack.DisableRollback === undefined
                  ? "Not reported"
                  : stack.DisableRollback
                    ? "Disabled"
                    : "Enabled",
            },
            {
              label: "Capabilities",
              value: stack.Capabilities.join(", ") || "None reported",
            },
            { label: "Resources reported", value: stack.Resources.length },
            { label: "Outputs reported", value: stack.Outputs.length },
          ]}
        />
      </Container>
      {latestFailure && (
        <Alert type="warning" header="Most recent failed resource event">
          {latestFailure.LogicalResourceId || "Resource not reported"} ·{" "}
          {latestFailure.ResourceStatus} · {cfnTime(latestFailure.Timestamp)}
          <p>
            {latestFailure.ResourceStatusReason ||
              "No failure reason reported. Inspect the deployment events for context."}
          </p>
        </Alert>
      )}
    </SpaceBetween>
  );
}
