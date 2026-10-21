export interface SubscriptionItem {
  SubscriptionArn: string;
  Protocol: string;
  Endpoint: string;
  TopicArn: string;
  Owner?: string;
  attributes?: Record<string, any>;
}
export interface TopicItem {
  TopicArn: string;
  TopicName: string;
  SubscriptionsPending?: number;
  SubscriptionsConfirmed?: number;
  Type?: string;
  DisplayName?: string;
  Policy?: string;
  subscriptions: SubscriptionItem[];
  attributes: Record<string, any>;
  tags: { Key: string; Value: string }[];
  issues: string[];
}
export function subscriptionState(sub: SubscriptionItem): string {
  if (
    sub.SubscriptionArn === "PendingConfirmation" ||
    sub.attributes?.PendingConfirmation === "true" ||
    sub.attributes?.PendingConfirmation === true
  )
    return "Pending confirmation";
  if (sub.SubscriptionArn === "Deleted") return "Deleted";
  return sub.SubscriptionArn?.startsWith("arn:") ? "Confirmed" : "Not reported";
}
export function snsTopics(data: any): TopicItem[] {
  const flat = Array.isArray(data.subscriptions) ? data.subscriptions : [];
  return (data.topics || data.Topics || []).map((raw: any) => {
    const topic = typeof raw === "string" ? { arn: raw } : raw;
    const arn = topic.TopicArn || topic.arn || "";
    const name =
      topic.name || topic.TopicName || arn.split(":").pop() || "Unnamed topic";
    const attrs = topic.attributes || topic.Attributes || {};
    const normalize = (sub: any): SubscriptionItem => {
      const subArn = sub.SubscriptionArn || sub.arn || "";
      const detail = flat.find((item: any) =>
        subArn.startsWith("arn:")
          ? (item.arn || item.SubscriptionArn) === subArn
          : (item.topic_arn || item.TopicArn) === arn &&
            (item.endpoint || item.Endpoint) ===
              (sub.Endpoint || sub.endpoint) &&
            (item.protocol || item.Protocol) === (sub.Protocol || sub.protocol),
      );
      return {
        SubscriptionArn: subArn,
        Protocol: sub.Protocol || sub.protocol,
        Endpoint: sub.Endpoint || sub.endpoint,
        TopicArn: sub.TopicArn || sub.topic_arn || arn,
        Owner: sub.Owner || sub.owner,
        attributes:
          sub.attributes || sub.Attributes || detail?.attributes || {},
      };
    };
    const subscriptions = (
      Array.isArray(topic.subscriptions)
        ? topic.subscriptions
        : flat.filter((sub: any) => (sub.topic_arn || sub.TopicArn) === arn)
    ).map(normalize);
    const reported = (value: any, fallback: number) =>
      value == null || !Number.isFinite(Number(value))
        ? fallback
        : Number(value);
    return {
      TopicArn: arn,
      TopicName: name,
      Type:
        name.endsWith(".fifo") || attrs.FifoTopic === "true"
          ? "FIFO"
          : "Standard",
      DisplayName: attrs.DisplayName,
      Policy: attrs.Policy,
      attributes: attrs,
      subscriptions,
      tags: Array.isArray(topic.tags) ? topic.tags : [],
      SubscriptionsPending: reported(
        attrs.SubscriptionsPending ?? topic.SubscriptionsPending,
        subscriptions.filter(
          (sub: SubscriptionItem) =>
            subscriptionState(sub) === "Pending confirmation",
        ).length,
      ),
      SubscriptionsConfirmed: reported(
        attrs.SubscriptionsConfirmed ?? topic.SubscriptionsConfirmed,
        subscriptions.filter(
          (sub: SubscriptionItem) => subscriptionState(sub) === "Confirmed",
        ).length,
      ),
      issues: [attrs.error, topic.subscriptions?.error].filter(Boolean),
    };
  });
}
export function prettySnsPolicy(value: any): string {
  if (value == null || value === "") return "Not reported";
  try {
    return JSON.stringify(
      typeof value === "string" ? JSON.parse(value) : value,
      null,
      2,
    );
  } catch {
    return String(value);
  }
}
