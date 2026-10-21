import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Button from "@cloudscape-design/components/button";
import Box from "@cloudscape-design/components/box";
import { CodeSnippet } from "./CodeSnippet";

export interface EventTarget {
  Id: string;
  Arn: string;
  Input?: string;
  InputPath?: string;
  InputTransformer?: unknown;
  RoleArn?: string;
  DeadLetterConfig?: { Arn?: string };
  RetryPolicy?: {
    MaximumRetryAttempts?: number;
    MaximumEventAgeInSeconds?: number;
  };
}

export function EventBridgeTargets({
  targets,
  onRemove,
}: {
  targets: EventTarget[];
  onRemove: (id: string) => void;
}) {
  return (
    <SpaceBetween size="m">
      <Box>
        Targets receive matching events while the rule is enabled. Input
        settings determine which payload each target receives.
      </Box>
      {targets.length ? (
        targets.map((target) => (
          <Container
            key={target.Id}
            header={
              <Header
                variant="h3"
                actions={
                  <Button onClick={() => onRemove(target.Id)}>Remove</Button>
                }
              >
                {target.Id}
              </Header>
            }
          >
            <SpaceBetween size="m">
              <KeyValuePairs
                columns={2}
                items={[
                  {
                    label: "Target ARN",
                    value: (
                      <span className="eventbridge-resource-value">
                        {target.Arn}
                      </span>
                    ),
                  },
                  {
                    label: "Execution role",
                    value: (
                      <span className="eventbridge-resource-value">
                        {target.RoleArn || "Not configured"}
                      </span>
                    ),
                  },
                  {
                    label: "Dead-letter queue",
                    value: (
                      <span className="eventbridge-resource-value">
                        {target.DeadLetterConfig?.Arn || "Not configured"}
                      </span>
                    ),
                  },
                  {
                    label: "Retry policy",
                    value: (
                      <SpaceBetween size="xs">
                        <div>
                          Retry attempts:{" "}
                          {target.RetryPolicy?.MaximumRetryAttempts ??
                            "Not reported"}
                        </div>
                        <div>
                          Maximum event age:{" "}
                          {target.RetryPolicy?.MaximumEventAgeInSeconds !==
                          undefined
                            ? `${target.RetryPolicy.MaximumEventAgeInSeconds} seconds`
                            : "Not reported"}
                        </div>
                      </SpaceBetween>
                    ),
                  },
                ]}
              />
              <Header variant="h3">Target input</Header>
              {target.Input !== undefined ? (
                <>
                  <Box>Constant JSON replaces the matched event.</Box>
                  <CodeSnippet language="json" code={target.Input} />
                </>
              ) : target.InputPath ? (
                <Box>
                  Event path: <code>{target.InputPath}</code>
                </Box>
              ) : target.InputTransformer ? (
                <>
                  <Box>
                    Input transformer extracts event fields into a template.
                  </Box>
                  <CodeSnippet
                    language="json"
                    code={JSON.stringify(target.InputTransformer, null, 2)}
                  />
                </>
              ) : (
                <Box>Matched event. No input override is configured.</Box>
              )}
            </SpaceBetween>
          </Container>
        ))
      ) : (
        <Box>No targets attached to this rule yet.</Box>
      )}
    </SpaceBetween>
  );
}
