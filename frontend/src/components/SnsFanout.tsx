import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import { TopicItem, subscriptionState } from "../pages/snsResources";
function filterDescription(value: unknown) {
  if (value == null || value === '') return 'None reported';
  try { const policy = typeof value === 'string' ? JSON.parse(value) : value; return Object.keys(policy as object).length ? 'Configured' : 'All messages (empty policy)'; }
  catch { return 'Policy could not be read'; }
}
export function SnsFanout({
  topic,
  onInspect,
}: {
  topic: TopicItem;
  onInspect: (index: number) => void;
}) {
  return (
    <Container
      header={
        <Header
          variant="h2"
          description="A publisher sends one message to the topic. Confirmed subscriptions receive messages that match their filter policy."
        >
          Message fan-out
        </Header>
      }
    >
      <SpaceBetween size="m">
        <div className="sns-fanout-source">
          <Box variant="awsui-key-label">Publisher → SNS topic</Box>
          <strong>{topic.TopicName}</strong>
          <Box>
            {topic.Type} topic · {topic.SubscriptionsConfirmed} confirmed ·{" "}
            {topic.SubscriptionsPending} pending
          </Box>
        </div>
        <div
          className="sns-fanout-destinations"
          role="list"
          aria-label="Subscription destinations"
        >
          {topic.subscriptions.map((sub, index) => {
            const status = subscriptionState(sub);
            return (
              <section
                role="listitem"
                key={`${sub.SubscriptionArn}-${sub.Endpoint}-${index}`}
              >
                <SpaceBetween size="s">
                  <Header variant="h3">
                    {sub.Protocol?.toUpperCase() || "Unknown protocol"}
                  </Header>
                  <div style={{ overflowWrap: "anywhere" }}>
                    {sub.Endpoint || "Endpoint not reported"}
                  </div>
                  <StatusIndicator
                    type={
                      status === "Confirmed"
                        ? "success"
                        : status === "Pending confirmation"
                          ? "pending"
                          : "not-started"
                    }
                  >
                    {status}
                  </StatusIndicator>
                  <div>
                    Filter:{" "}
                    {filterDescription(sub.attributes?.FilterPolicy)}
                  </div>
                  <div>
                    Payload:{" "}
                    {sub.attributes?.RawMessageDelivery === "true"
                      ? "Raw message body"
                      : sub.attributes?.RawMessageDelivery === "false"
                        ? "SNS JSON envelope"
                        : "Not reported"}
                  </div>
                  <Button onClick={() => onInspect(index)}>
                    Inspect delivery settings
                  </Button>
                </SpaceBetween>
              </section>
            );
          })}
        </div>
        {!topic.subscriptions.length && (
          <Box>
            No subscriptions reported. Create a subscription to connect a
            destination.
          </Box>
        )}
        <Box color="text-body-secondary">
          Confirmation and configuration describe eligible destinations. They do
          not confirm that a specific message was delivered.
        </Box>
      </SpaceBetween>
    </Container>
  );
}
