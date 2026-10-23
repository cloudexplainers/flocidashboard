import { PolicySummary } from "../components/PolicySummary";
import React, { useState, useEffect, useRef } from "react";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Table from "../components/ResourceTable";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import TextFilter from "@cloudscape-design/components/text-filter";
import Tabs from "@cloudscape-design/components/tabs";
import Modal from "@cloudscape-design/components/modal";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Textarea from "@cloudscape-design/components/textarea";
import Select from "@cloudscape-design/components/select";
import Checkbox from "@cloudscape-design/components/checkbox";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import Box from "@cloudscape-design/components/box";
import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import { publishSnsMessage, setSnsTopicAttribute, snsRequest } from "../api/client";
import { useSession } from "../components/SessionContext";
import { SnsFanout } from "../components/SnsFanout";
import {
  TopicItem,
  SubscriptionItem,
  snsTopics,
  subscriptionState,
  prettySnsPolicy,
} from "./snsResources";
import { CodeSnippet } from "../components/CodeSnippet";
import ConfirmActionModal from "../components/ConfirmActionModal";

const PROTOCOL_OPTIONS = [
  {
    label: "Amazon SQS",
    value: "sqs",
    description: "Deliver messages to an SQS queue ARN",
  },
  {
    label: "AWS Lambda",
    value: "lambda",
    description: "Invoke a Lambda function ARN",
  },
  {
    label: "Email",
    value: "email",
    description: "Send email notification to an address",
  },
  {
    label: "HTTP webhook",
    value: "http",
    description: "Deliver HTTP POST payload to a URL",
  },
  {
    label: "HTTPS webhook",
    value: "https",
    description: "Deliver HTTP POST payload to a URL",
  },
];

type Props = { resourcePath?: string; onNavigate?: (path: string) => void };
export const SNSConsole: React.FC<Props> = (props) => {
  const { identity, details, loading } = useSession();
  if (!identity && loading)
    return (
      <SpaceBetween size="m">
        <Header variant="h1">Amazon SNS</Header>
        <StatusIndicator type="loading">
          Loading session context
        </StatusIndicator>
      </SpaceBetween>
    );
  return (
    <SnsWorkspace
      key={JSON.stringify([
        identity?.arn,
        identity?.account_id,
        identity?.region,
        identity?.endpoint,
        identity?.expires_at,
        details?.session_identity?.access_key_id,
      ])}
      {...props}
    />
  );
};
const SnsWorkspace: React.FC<Props> = ({ resourcePath, onNavigate }) => {
  const mounted = useRef(true);
  const loadGeneration = useRef(0);
  const mutationLock = useRef(false);
  const [modalError, setModalError] = useState("");
  const [contentDedup, setContentDedup] = useState(false);
  const [messageAttributes, setMessageAttributes] = useState("{}");
  const [published, setPublished] = useState<any>(null);
  const [rawDelivery, setRawDelivery] = useState(false);
  const [filterPolicy, setFilterPolicy] = useState("{}");
  const [filterScope, setFilterScope] = useState("MessageAttributes");
  const [inspectedIndex, setInspectedIndex] = useState<number | null>(null);
  const [unsubscribeTarget, setUnsubscribeTarget] =
    useState<SubscriptionItem | null>(null);
  const [settingsTarget, setSettingsTarget] = useState<SubscriptionItem | null>(
    null,
  );
  const [settingName, setSettingName] = useState("FilterPolicy");
  const [settingValue, setSettingValue] = useState("{}");
  const [savingSetting, setSavingSetting] = useState(false);
  const parseObject = (text: string, label: string) => {
    let value;
    try {
      value = JSON.parse(text);
    } catch {
      throw new Error(`${label} must be valid JSON.`);
    }
    if (!value || Array.isArray(value) || typeof value !== "object")
      throw new Error(`${label} must be a JSON object.`);
    return value;
  };
  const requested = new URLSearchParams(
    (resourcePath || "").split("?")[1] || "",
  ).get("topic");
  const resourceTab =
    (resourcePath || "").split("?")[0].split("/")[1] || "overview";
  const navigateResource = (item: TopicItem, tab = activeTabId) => {
    if (onNavigate)
      onNavigate(`sns/${tab}?${new URLSearchParams({ topic: item.TopicArn })}`);
  };
  const [topics, setTopics] = useState<TopicItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedItems, setSelectedItems] = useState<TopicItem[]>([]);
  const [filterText, setFilterText] = useState("");
  const [activeTabId, setActiveTabId] = useState(resourceTab);

  // Modals
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [publishModalOpen, setPublishModalOpen] = useState(false);
  const [subscribeModalOpen, setSubscribeModalOpen] = useState(false);
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);

  // Create Topic state
  const [newTopicName, setNewTopicName] = useState("");
  const [isFifo, setIsFifo] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [creating, setCreating] = useState(false);

  // Publish Message state
  const [subject, setSubject] = useState("");
  const [messageBody, setMessageBody] = useState("");
  const [messageGroupId, setMessageGroupId] = useState("");
  const [messageDeduplicationId, setMessageDeduplicationId] = useState("");
  const [publishing, setPublishing] = useState(false);

  // Create Subscription state
  const [subProtocol, setSubProtocol] = useState(PROTOCOL_OPTIONS[0]);
  const [subEndpoint, setSubEndpoint] = useState("");
  const [subscribing, setSubscribing] = useState(false);

  // Deleting Subscription / Topic
  const [deletingSubArn, setDeletingSubArn] = useState<string | null>(null);
  const [deletingTopic, setDeletingTopic] = useState(false);
  const [policyDraft, setPolicyDraft] = useState<string | null>(null);
  const [policyError, setPolicyError] = useState("");
  const [savingPolicy, setSavingPolicy] = useState(false);
  const [actionAlert, setActionAlert] = useState<{
    type: "success" | "error" | "info";
    message: string;
  } | null>(null);

  const activeTopic = requested
    ? topics.find(
        (item) => item.TopicArn === requested || item.TopicName === requested,
      ) || null
    : onNavigate
      ? null
      : selectedItems[0] || null;
  const currentTopic = useRef(activeTopic?.TopicArn);
  currentTopic.current = activeTopic?.TopicArn;
  const inspected =
    inspectedIndex == null
      ? undefined
      : activeTopic?.subscriptions[inspectedIndex];
  useEffect(() => {
    setInspectedIndex(null);
    setPublished(null);
    setPublishModalOpen(false);
    setSubscribeModalOpen(false);
    setDeleteModalOpen(false);
    setSettingsTarget(null);
    setUnsubscribeTarget(null);
  }, [requested]);

  useEffect(() => {
    setActiveTabId(resourceTab);
  }, [resourceTab]);
  useEffect(() => {
    if (requested)
      setSelectedItems(
        topics.filter(
          (item) => item.TopicArn === requested || item.TopicName === requested,
        ),
      );
  }, [requested, topics]);

  const savePolicy = async () => {
    if (!activeTopic || policyDraft === null) return;
    try {
      JSON.parse(policyDraft);
    } catch {
      setPolicyError("The access policy must be valid JSON.");
      return;
    }
    setSavingPolicy(true);
    setPolicyError("");
    try {
      await setSnsTopicAttribute(activeTopic.TopicArn, "Policy", policyDraft);
      setPolicyDraft(null);
      setActionAlert({ type: "success", message: `Access policy updated for ${activeTopic.TopicArn}.` });
      await loadData();
    } catch (err) {
      setPolicyError(err instanceof Error ? err.message : "Failed to update the access policy.");
    } finally {
      setSavingPolicy(false);
    }
  };

  const loadData = async () => {
    const generation = ++loadGeneration.current;
    setLoading(true);
    try {
      const data = await snsRequest();
      if (!mounted.current || generation !== loadGeneration.current) return;
      const list = snsTopics(data);
      setTopics(list);
      setSelectedItems((previous) =>
        list.filter((item) =>
          previous.some((old) => old.TopicArn === item.TopicArn),
        ),
      );
    } catch (err: any) {
      if (!mounted.current || generation !== loadGeneration.current) return;
      setActionAlert({
        type: "error",
        message: err.message || "Failed to load SNS topics.",
      });
    } finally {
      if (mounted.current && generation === loadGeneration.current)
        setLoading(false);
    }
  };

  useEffect(() => {
    mounted.current = true;
    void loadData();
    return () => {
      mounted.current = false;
      loadGeneration.current++;
    };
  }, []);

  const handleCreateTopic = async () => {
    if (!newTopicName.trim()) return;
    let finalName = newTopicName.trim();
    if (isFifo && !finalName.endsWith(".fifo")) {
      finalName += ".fifo";
    }
    setCreating(true);
    setActionAlert(null);
    try {
      await snsRequest("topics/", "POST", {
        name: finalName,
        fifo: isFifo,
        display_name: displayName,
        content_based_deduplication: contentDedup,
      });
      if (!mounted.current) return;
      setActionAlert({
        type: "success",
        message: `Topic "${finalName}" created successfully.`,
      });
      setCreateModalOpen(false);
      setNewTopicName("");
      setDisplayName("");
      setIsFifo(false);
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to create topic.",
      });
      if (mounted.current) setModalError(err.message || "Operation failed.");
    } finally {
      setCreating(false);
    }
  };

  const handleDeleteTopic = async () => {
    if (!activeTopic) return;
    setDeletingTopic(true);
    setActionAlert(null);
    try {
      await snsRequest(
        `topics/${encodeURIComponent(activeTopic.TopicArn)}/`,
        "DELETE",
      );
      if (!mounted.current) return;
      onNavigate?.("sns");
      setActionAlert({
        type: "success",
        message: `Topic "${activeTopic.TopicName}" deleted.`,
      });
      setDeleteModalOpen(false);
      setSelectedItems([]);
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to delete topic.",
      });
      if (mounted.current) setModalError(err.message || "Operation failed.");
    } finally {
      setDeletingTopic(false);
    }
  };

  const handlePublishMessage = async () => {
    if (!activeTopic || mutationLock.current) return;
    mutationLock.current = true;
    setModalError("");
    setPublishing(true);
    setActionAlert(null);
    try {
      const res = await publishSnsMessage(
        activeTopic.TopicArn,
        messageBody,
        subject || undefined,
        parseObject(messageAttributes, "Message attributes"),
        undefined,
        activeTopic.Type === "FIFO" ? messageGroupId : undefined,
        activeTopic.Type === "FIFO" && messageDeduplicationId
          ? messageDeduplicationId
          : undefined,
      );
      if (!mounted.current || currentTopic.current !== activeTopic.TopicArn)
        return;
      setPublished({ ...res, topicArn: activeTopic.TopicArn });
      setActionAlert({
        type: "success",
        message: `Message accepted by ${activeTopic.TopicName}. This does not confirm delivery to every subscriber.`,
      });
      setPublishModalOpen(false);
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to publish message.",
      });
      if (mounted.current) setModalError(err.message || "Operation failed.");
    } finally {
      mutationLock.current = false;
      if (mounted.current) setPublishing(false);
    }
  };

  const handleCreateSubscription = async () => {
    if (!activeTopic || !subEndpoint.trim()) return;
    setSubscribing(true);
    setActionAlert(null);
    try {
      const res = await snsRequest("subscriptions/", "POST", {
        topic_arn: activeTopic.TopicArn,
        protocol: subProtocol.value,
        endpoint: subEndpoint.trim(),
        filter_policy: parseObject(filterPolicy, "Filter policy"),
        filter_policy_scope: filterScope,
        raw_message_delivery: rawDelivery,
      });
      if (!mounted.current || currentTopic.current !== activeTopic.TopicArn)
        return;
      setActionAlert({
        type: "success",
        message:
          res.subscription_arn === "PendingConfirmation"
            ? "Subscription requested. The endpoint must confirm before receiving messages."
            : `Subscription requested for ${subEndpoint}. Refresh to check confirmation status.`,
      });
      setSubscribeModalOpen(false);
      setSubEndpoint("");
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to create subscription.",
      });
      if (mounted.current) setModalError(err.message || "Operation failed.");
    } finally {
      setSubscribing(false);
    }
  };

  const handleDeleteSubscription = async (subArn: string) => {
    if (!subArn || subArn === "PendingConfirmation") return;
    setDeletingSubArn(subArn);
    try {
      await snsRequest(
        `subscriptions/${encodeURIComponent(subArn)}/`,
        "DELETE",
      );
      if (!mounted.current) return;
      setUnsubscribeTarget(null);
      setActionAlert({ type: "success", message: "Subscription removed." });
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to unsubscribe.",
      });
      if (mounted.current) setModalError(err.message || "Operation failed.");
    } finally {
      setDeletingSubArn(null);
    }
  };

  const editSetting = (sub: SubscriptionItem) => {
    setSettingsTarget(sub);
    setSettingName("FilterPolicy");
    setSettingValue(sub.attributes?.FilterPolicy || "{}");
    setModalError("");
  };
  const saveSetting = async () => {
    if (!settingsTarget || mutationLock.current) return;
    mutationLock.current = true;
    setSavingSetting(true);
    setModalError("");
    try {
      const value =
        settingName === "FilterPolicy"
          ? parseObject(settingValue, "Filter policy")
          : settingValue;
      await snsRequest(
        `subscriptions/${encodeURIComponent(settingsTarget.SubscriptionArn)}/attributes/`,
        "PUT",
        { attribute_name: settingName, attribute_value: value },
      );
      if (mounted.current) {
        setSettingsTarget(null);
        setActionAlert({ type: "success", message: `Updated ${settingName}.` });
        await loadData();
      }
    } catch (error: any) {
      if (mounted.current) setModalError(error.message);
    } finally {
      mutationLock.current = false;
      if (mounted.current) setSavingSetting(false);
    }
  };
  const filteredTopics = topics.filter(
    (t) =>
      t.TopicName.toLowerCase().includes(filterText.toLowerCase()) ||
      t.TopicArn.toLowerCase().includes(filterText.toLowerCase()),
  );

  return (
    <SpaceBetween size="l">
      {actionAlert && (
        <Alert
          type={actionAlert.type}
          dismissible
          onDismiss={() => setActionAlert(null)}
          header={
            actionAlert.type === "error"
              ? "SNS Operation Error"
              : "SNS Notification"
          }
        >
          {actionAlert.message}
        </Alert>
      )}

      {!requested && (
        <Table
          header={
            <Header
              variant="h1"
              counter={`(${filteredTopics.length}${filterText ? ` of ${topics.length}` : ""})`}
              description="Fully managed pub/sub messaging service for microservices, distributed systems, and serverless applications."
              actions={
                <SpaceBetween direction="horizontal" size="xs">
                  <Button
                    iconName="refresh"
                    onClick={loadData}
                    loading={loading}
                  >
                    Refresh
                  </Button>
                  <Button
                    disabled={!activeTopic}
                    iconName="share"
                    onClick={() => {
                      setModalError("");
                      setPublishModalOpen(true);
                    }}
                  >
                    Publish message
                  </Button>
                  <Button
                    disabled={!activeTopic}
                    iconName="remove"
                    onClick={() => {
                      setModalError("");
                      setDeleteModalOpen(true);
                    }}
                  >
                    Delete topic
                  </Button>
                  <Button
                    variant="primary"
                    iconName="add-plus"
                    onClick={() => {
                      setModalError("");
                      setCreateModalOpen(true);
                    }}
                  >
                    Create topic
                  </Button>
                </SpaceBetween>
              }
            >
              Topics
            </Header>
          }
          columnDefinitions={[
            {
              id: "name",
              header: "Topic name",
              cell: (item) => (
                <Button
                  variant="inline-link"
                  onClick={() => {
                    setSelectedItems([item]);
                    navigateResource(item);
                  }}
                >
                  <strong>{item.TopicName}</strong>
                </Button>
              ),
              sortingField: "TopicName",
              isRowHeader: true,
            },
            {
              id: "type",
              header: "Type",
              cell: (item) => (
                <Badge color={item.Type === "FIFO" ? "blue" : "grey"}>
                  {item.Type || "Standard"}
                </Badge>
              ),
            },
            {
              id: "arn",
              header: "Topic ARN",
              cell: (item) => (
                <code style={{ fontSize: "11px" }}>{item.TopicArn}</code>
              ),
            },
            {
              id: "subscriptions",
              header: "Subscriptions",
              cell: (item) => (
                <span>
                  {item.SubscriptionsConfirmed ?? 0} confirmed ·{" "}
                  {item.SubscriptionsPending ?? 0} pending
                </span>
              ),
            },
          ]}
          items={filteredTopics}
          loading={loading}
          loadingText="Loading SNS topics..."
          selectionType="single"
          selectedItems={selectedItems}
          onSelectionChange={({ detail }) => {
            setSelectedItems(detail.selectedItems);
            if (detail.selectedItems[0])
              navigateResource(detail.selectedItems[0]);
          }}
          filter={
            <TextFilter
              filteringText={filterText}
              filteringPlaceholder="Filter topics by name or ARN..."
              onChange={({ detail }) => setFilterText(detail.filteringText)}
            />
          }
          empty={
            <Box textAlign="center" color="inherit" padding={{ vertical: "l" }}>
              <SpaceBetween size="m">
                <b>No SNS topics found</b>
                <p>
                  Create a topic to publish messages to multiple subscribers
                  simultaneously.
                </p>
                <Button
                  variant="primary"
                  onClick={() => {
                    setModalError("");
                    setCreateModalOpen(true);
                  }}
                >
                  Create topic
                </Button>
              </SpaceBetween>
            </Box>
          }
        />
      )}
      {requested && (
        <>
          <Header
            variant="h1"
            description={activeTopic?.TopicArn}
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button loading={loading} iconName="refresh" onClick={loadData}>
                  Refresh
                </Button>
                <Button
                  disabled={!activeTopic}
                  variant="primary"
                  onClick={() => {
                    setModalError("");
                    setPublishModalOpen(true);
                  }}
                >
                  Publish message
                </Button>
                <Button
                  disabled={!activeTopic}
                  onClick={() => {
                    setModalError("");
                    setDeleteModalOpen(true);
                  }}
                >
                  Delete topic
                </Button>
              </SpaceBetween>
            }
          >
            Topic: {activeTopic?.TopicName || requested}
          </Header>
          <Button
            variant="link"
            iconName="arrow-left"
            onClick={() => {
              setSelectedItems([]);
              onNavigate?.("sns");
            }}
          >
            Topics
          </Button>
        </>
      )}
      {published && (
        <Container header={<Header variant="h2">Last publish result</Header>}>
          <KeyValuePairs
            columns={2}
            minColumnWidth={280}
            items={[
              {
                label: "Message ID",
                value: published.message_id || "Not reported",
              },
              {
                label: "Sequence number",
                value: published.sequence_number || "Not reported",
              },
              { label: "Topic ARN", value: published.topicArn },
            ]}
          />
        </Container>
      )}
      {requested && !loading && !activeTopic && (
        <Alert type="warning">
          Requested topic was not found. Select an available resource.
        </Alert>
      )}
      {activeTopic && (
        <Container
          header={
            <Header
              variant="h2"
              description={`ARN: ${activeTopic.TopicArn}`}
              actions={
                <SpaceBetween direction="horizontal" size="xs"></SpaceBetween>
              }
            >
              Topic details
            </Header>
          }
        >
          {activeTopic.issues.map((issue, index) => <Alert key={index} type="warning" header="Some topic data could not be loaded">{issue}</Alert>)}
          <Tabs
            activeTabId={activeTabId}
            onChange={({ detail }) => {
              setActiveTabId(detail.activeTabId);
              navigateResource(activeTopic, detail.activeTabId);
            }}
            tabs={[
              {
                id: "overview",
                label: "Overview",
                content: (
                  <SnsFanout
                    topic={activeTopic}
                    onInspect={(index) => {
                      setInspectedIndex(index);
                      setActiveTabId("subscriptions");
                      navigateResource(activeTopic, "subscriptions");
                    }}
                  />
                ),
              },
              {
                id: "subscriptions",
                label: `Subscriptions (${activeTopic.subscriptions.length})`,
                content: (
                  <SpaceBetween size="l">
                    <Table
                      header={
                        <Header
                          variant="h3"
                          counter={`(${activeTopic.subscriptions?.length || 0})`}
                          actions={
                            <Button
                              variant="primary"
                              iconName="add-plus"
                              onClick={() => {
                                setModalError("");
                                setSubscribeModalOpen(true);
                              }}
                            >
                              Create subscription
                            </Button>
                          }
                        >
                          Subscriptions
                        </Header>
                      }
                      columnDefinitions={[
                        {
                          id: "protocol",
                          header: "Protocol",
                          cell: (sub) => (
                            <span>{sub.Protocol ? sub.Protocol.toUpperCase() : "-"}</span>
                          ),
                        },
                        {
                          id: "endpoint",
                          header: "Endpoint",
                          cell: (sub) =>
                            sub.Protocol === "sqs" &&
                            sub.Endpoint?.startsWith("arn:") ? (
                              <Button
                                variant="inline-link"
                                href={`/sqs/attributes?${new URLSearchParams({ queue: sub.Endpoint })}`}
                                onClick={(event) => {
                                  if (onNavigate) {
                                    event.preventDefault();
                                    onNavigate(
                                      `sqs/attributes?${new URLSearchParams({ queue: sub.Endpoint })}`,
                                    );
                                  }
                                }}
                              >
                                {sub.Endpoint}
                              </Button>
                            ) : (
                              <span style={{ overflowWrap: "anywhere" }}>
                                {sub.Endpoint || "Not reported"}
                              </span>
                            ),
                        },
                        {
                          id: "arn",
                          header: "Subscription ARN",
                          cell: (sub) => (
                            <code
                              style={{
                                fontSize: "11px",
                                whiteSpace: "normal",
                                overflowWrap: "anywhere",
                              }}
                            >
                              {sub.SubscriptionArn || "Not reported"}
                            </code>
                          ),
                        },
                        {
                          id: "status",
                          header: "Status",
                          cell: (sub) => (
                            <StatusIndicator
                              type={
                                subscriptionState(sub) === "Confirmed"
                                  ? "success"
                                  : subscriptionState(sub) ===
                                      "Pending confirmation"
                                    ? "pending"
                                    : "not-started"
                              }
                            >
                              {subscriptionState(sub)}
                            </StatusIndicator>
                          ),
                        },
                        {
                          id: "delivery",
                          header: "Delivery settings",
                          cell: (sub) => (
                            <div style={{ overflowWrap: "anywhere" }}>
                              Raw delivery:{" "}
                              {sub.attributes?.RawMessageDelivery ??
                                "Not reported"}
                              <br />
                              Filter policy:{" "}
                              {sub.attributes?.FilterPolicy || "Not reported"}
                              <br />
                              Filter scope:{" "}
                              {sub.attributes?.FilterPolicyScope ||
                                "Not reported"}
                            </div>
                          ),
                        },
                        {
                          id: "actions",
                          header: "Action",
                          cell: (sub) => (
                            <SpaceBetween direction="horizontal" size="xs">
                              <Button
                                onClick={() =>
                                  setInspectedIndex(
                                    activeTopic.subscriptions.indexOf(sub),
                                  )
                                }
                              >
                                Details
                              </Button>
                              <Button
                                disabled={
                                  subscriptionState(sub) !== "Confirmed"
                                }
                                onClick={() => editSetting(sub)}
                              >
                                Edit settings
                              </Button>
                              <Button
                                iconName="remove"
                                disabled={
                                  !sub.SubscriptionArn?.startsWith("arn:")
                                }
                                onClick={() => {
                                  setModalError("");
                                  setUnsubscribeTarget(sub);
                                }}
                                loading={deletingSubArn === sub.SubscriptionArn}
                              >
                                Unsubscribe
                              </Button>
                            </SpaceBetween>
                          ),
                        },
                      ]}
                      items={activeTopic.subscriptions || []}
                      empty={
                        <Box
                          textAlign="center"
                          color="inherit"
                          padding={{ vertical: "m" }}
                        >
                          <SpaceBetween size="s">
                            <b>No subscribers yet</b>
                            <p>
                              Subscribe an SQS queue, Lambda function, or HTTPS
                              webhook to receive topic messages.
                            </p>
                            <Button
                              onClick={() => {
                                setModalError("");
                                setSubscribeModalOpen(true);
                              }}
                            >
                              Create subscription
                            </Button>
                          </SpaceBetween>
                        </Box>
                      }
                    />
                    {inspected && (
                      <Container
                        header={
                          <Header variant="h3">Subscription details</Header>
                        }
                      >
                        <SpaceBetween size="m">
                          <KeyValuePairs
                            columns={2}
                            minColumnWidth={280}
                            items={[
                              { label: "Endpoint", value: inspected.Endpoint },
                              {
                                label: "Status",
                                value: subscriptionState(inspected),
                              },
                              { label: "Protocol", value: inspected.Protocol },
                              {
                                label: "Owner",
                                value: inspected.Owner || "Not reported",
                              },
                              {
                                label: "Raw message delivery",
                                value:
                                  inspected.attributes?.RawMessageDelivery ??
                                  "Not reported",
                              },
                              {
                                label: "Filter scope",
                                value:
                                  inspected.attributes?.FilterPolicyScope ||
                                  "Not reported",
                              },
                              {
                                label: "Subscription ARN",
                                value: inspected.SubscriptionArn,
                              },
                            ]}
                          />
                          <Header variant="h3">Filter policy</Header>
                          <CodeSnippet
                            language="json"
                            code={prettySnsPolicy(
                              inspected.attributes?.FilterPolicy,
                            )}
                          />
                          <Header variant="h3">Redrive policy</Header>
                          <CodeSnippet
                            language="json"
                            code={prettySnsPolicy(
                              inspected.attributes?.RedrivePolicy,
                            )}
                          />
                        </SpaceBetween>
                      </Container>
                    )}
                  </SpaceBetween>
                ),
              },
              {
                id: "details",
                label: "Configuration",
                content: (
                  <SpaceBetween size="l">
                    <KeyValuePairs
                      columns={2}
                      minColumnWidth={280}
                      items={[
                        { label: "Name", value: activeTopic.TopicName },
                        { label: "ARN", value: activeTopic.TopicArn },
                        {
                          label: "Type",
                          value: activeTopic.Type || "Standard",
                        },
                        {
                          label: "Display name",
                          value: activeTopic.DisplayName || "-",
                        },
                        {
                          label: "Topic owner",
                          value: activeTopic.attributes?.Owner || "-",
                        },
                        {
                          label: "Subscriptions confirmed",
                          value: String(
                            activeTopic.SubscriptionsConfirmed ?? 0,
                          ),
                        },
                        {
                          label: "Subscriptions pending",
                          value: String(activeTopic.SubscriptionsPending ?? 0),
                        },
                        {
                          label: "Encryption",
                          value: activeTopic.attributes?.KmsMasterKeyId
                            ? `Enabled (${activeTopic.attributes.KmsMasterKeyId})`
                            : "Disabled",
                        },
                        {
                          label: "Content-based deduplication",
                          value:
                            activeTopic.Type === "FIFO"
                              ? activeTopic.attributes?.ContentBasedDeduplication === "true"
                                ? "Enabled"
                                : "Disabled"
                              : "-",
                        },
                        {
                          label: "Delivery retry policy",
                          value: prettySnsPolicy(
                            activeTopic.attributes?.EffectiveDeliveryPolicy ||
                              activeTopic.attributes?.DeliveryPolicy,
                          ),
                        },
                      ]}
                    />
                  </SpaceBetween>
                ),
              },
              {
                id: "filtering",
                label: "Filter policies",
                content: (
                  <SpaceBetween size="m">
                    <Table
                      header={
                        <Header variant="h3" description="Subscription filter policies evaluate message attributes or the message body, so each subscriber receives only matching messages.">
                          Subscription filter policies
                        </Header>
                      }
                      items={activeTopic.subscriptions || []}
                      columnDefinitions={[
                        {
                          id: "endpoint",
                          header: "Endpoint",
                          cell: (sub) => (
                            <span style={{ overflowWrap: "anywhere" }}>
                              {sub.Endpoint || "Not reported"}
                            </span>
                          ),
                        },
                        {
                          id: "raw",
                          header: "Raw delivery",
                          cell: (sub) =>
                            sub.attributes?.RawMessageDelivery ??
                            "Not reported",
                        },
                        {
                          id: "scope",
                          header: "Filter scope",
                          cell: (sub) =>
                            sub.attributes?.FilterPolicyScope || "Not reported",
                        },
                        {
                          id: "filter",
                          header: "Filter policy",
                          cell: (sub) => (
                            <code
                              style={{
                                whiteSpace: "pre-wrap",
                                overflowWrap: "anywhere",
                              }}
                            >
                              {sub.attributes?.FilterPolicy || "Not reported"}
                            </code>
                          ),
                        },
                      ]}
                      empty={
                        <span>No subscription delivery policies reported.</span>
                      }
                    />
                  </SpaceBetween>
                ),
              },
              {
                id: "policy",
                label: "Access policy",
                content: (
                  <SpaceBetween size="m">
                    <Header variant="h3" description="Defines who can access the topic. By default, only the topic owner can publish or subscribe."
                      actions={policyDraft === null
                        ? <Button onClick={() => { setPolicyDraft(prettySnsPolicy(activeTopic.Policy || "") || ""); setPolicyError(""); }}>Edit</Button>
                        : <SpaceBetween direction="horizontal" size="xs">
                            <Button variant="link" disabled={savingPolicy} onClick={() => setPolicyDraft(null)}>Cancel</Button>
                            <Button variant="primary" loading={savingPolicy} onClick={savePolicy}>Save changes</Button>
                          </SpaceBetween>}>Access policy</Header>
                    {policyError && <Alert type="error">{policyError}</Alert>}
                    <PolicySummary document={activeTopic.Policy || undefined} />
                    {policyDraft !== null && (
                      <FormField label="Policy JSON" description="Replaces the topic's access policy (SetTopicAttributes Policy).">
                        <Textarea ariaLabel="Topic access policy JSON" rows={14} value={policyDraft} onChange={({ detail }) => setPolicyDraft(detail.value)} />
                      </FormField>
                    )}
                    {policyDraft === null && <textarea
                      readOnly
                      rows={8}
                      value={
                        activeTopic.Policy ||
                        "No access policy reported for this topic."
                      }
                      style={{
                        width: "100%",
                        boxSizing: "border-box",
                        fontFamily: "monospace",
                        fontSize: "12px",
                        background: "#1b2a3a",
                        color: "#4af",
                        padding: "12px",
                        borderRadius: "4px",
                        border: "1px solid #23395b",
                      }}
                    />}
                  </SpaceBetween>
                ),
              },
            ]}
          />
        </Container>
      )}

      <Modal
        visible={!!unsubscribeTarget}
        onDismiss={() => {
          if (!deletingSubArn) setUnsubscribeTarget(null);
        }}
        header="Unsubscribe destination"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                disabled={!!deletingSubArn}
                onClick={() => setUnsubscribeTarget(null)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={!!deletingSubArn}
                onClick={() =>
                  unsubscribeTarget &&
                  handleDeleteSubscription(unsubscribeTarget.SubscriptionArn)
                }
              >
                Unsubscribe
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {modalError && <Alert type="error">{modalError}</Alert>}
          <Alert type="warning">
            Remove subscription for {unsubscribeTarget?.Endpoint}? This
            destination will stop receiving new messages from the topic.
          </Alert>
        </SpaceBetween>
      </Modal>
      <Modal
        visible={!!settingsTarget}
        onDismiss={() => {
          if (!savingSetting) setSettingsTarget(null);
        }}
        header="Edit subscription setting"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                disabled={savingSetting}
                onClick={() => setSettingsTarget(null)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={savingSetting}
                onClick={saveSetting}
              >
                Save setting
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {modalError && <Alert type="error">{modalError}</Alert>}
          <FormField label="Setting">
            <Select
              selectedOption={{ label: settingName, value: settingName }}
              options={[
                "FilterPolicy",
                "FilterPolicyScope",
                ...(["sqs", "http", "https"].includes(
                  settingsTarget?.Protocol || "",
                )
                  ? ["RawMessageDelivery"]
                  : []),
              ].map((value) => ({ label: value, value }))}
              onChange={({ detail }) => {
                const name = detail.selectedOption.value!;
                setSettingName(name);
                setSettingValue(
                  settingsTarget?.attributes?.[name] ||
                    (name === "FilterPolicy"
                      ? "{}"
                      : name === "FilterPolicyScope"
                        ? "MessageAttributes"
                        : "false"),
                );
              }}
            />
          </FormField>
          {settingName === "FilterPolicy" ? (
            <FormField label="Filter policy">
              <Textarea
                rows={6}
                value={settingValue}
                onChange={({ detail }) => setSettingValue(detail.value)}
              />
            </FormField>
          ) : (
            <FormField label="Value">
              <Select
                selectedOption={{ label: settingValue, value: settingValue }}
                options={(settingName === "FilterPolicyScope"
                  ? ["MessageAttributes", "MessageBody"]
                  : ["true", "false"]
                ).map((value) => ({ label: value, value }))}
                onChange={({ detail }) =>
                  setSettingValue(detail.selectedOption.value!)
                }
              />
            </FormField>
          )}
        </SpaceBetween>
      </Modal>
      {/* Create Topic Modal */}
      <Modal
        visible={createModalOpen}
        onDismiss={() => setCreateModalOpen(false)}
        header="Create topic"
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateModalOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={!newTopicName.trim()}
                onClick={handleCreateTopic}
                loading={creating}
              >
                Create topic
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {modalError && <Alert type="error">{modalError}</Alert>}
          <FormField
            label="Name"
            constraintText="Maximum 256 characters. Can include alphanumeric characters, hyphens (-) and underscores (_). FIFO topic names get the .fifo suffix."
          >
            <Input
              value={newTopicName}
              onChange={({ detail }) => setNewTopicName(detail.value)}
              placeholder="e.g. order-notifications"
            />
          </FormField>
          <FormField
            label="Display name - optional"
            description="To use this topic with SMS subscriptions, enter a display name. Only the first 10 characters are displayed in an SMS message."
          >
            <Input
              value={displayName}
              onChange={({ detail }) => setDisplayName(detail.value)}
              placeholder="e.g. Order Alerts"
            />
          </FormField>
          <Checkbox
            checked={isFifo}
            onChange={({ detail }) => setIsFifo(detail.checked)}
            description="Strictly-preserved message ordering, exactly-once delivery, and subscription protocols limited to SQS. The type can't be changed after creation."
          >
            FIFO (first-in, first-out)
          </Checkbox>
          {isFifo && (
            <Checkbox
              checked={contentDedup}
              onChange={({ detail }) => setContentDedup(detail.checked)}
              description="SNS uses a SHA-256 hash of the message body as the deduplication ID. Off by default."
            >
              Content-based message deduplication
            </Checkbox>
          )}
        </SpaceBetween>
      </Modal>

      {/* Create Subscription Modal */}
      <Modal
        visible={subscribeModalOpen}
        onDismiss={() => setSubscribeModalOpen(false)}
        header="Create subscription"
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                onClick={() => setSubscribeModalOpen(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={!subEndpoint.trim()}
                onClick={handleCreateSubscription}
                loading={subscribing}
              >
                Create subscription
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {modalError && <Alert type="error">{modalError}</Alert>}
          <FormField label="Protocol">
            <Select
              selectedOption={subProtocol}
              onChange={({ detail }) => {
                setSubProtocol(detail.selectedOption as any);
                setRawDelivery(false);
              }}
              options={PROTOCOL_OPTIONS}
            />
          </FormField>
          <FormField
            label="Endpoint"
            description={
              subProtocol.value === "sqs"
                ? "Enter the ARN of the target SQS queue (e.g. arn:aws:sqs:us-east-1:000000000000:my-queue)"
                : subProtocol.value === "lambda"
                  ? "Enter the ARN of the Lambda function"
                  : subProtocol.value === "email"
                    ? "Enter the destination email address"
                    : "Enter the webhook URL (e.g. https://api.example.com/webhooks/sns)"
            }
          >
            <Input
              value={subEndpoint}
              onChange={({ detail }) => setSubEndpoint(detail.value)}
              placeholder="Enter endpoint ARN, URL, or email..."
            />
          </FormField>
          <FormField
            label="Subscription filter policy - optional"
            description="Leave as {} to receive all messages published to the topic."
          >
            <Textarea
              rows={4}
              value={filterPolicy}
              onChange={({ detail }) => setFilterPolicy(detail.value)}
            />
          </FormField>
          <FormField label="Filter policy scope">
            <Select
              selectedOption={{ label: filterScope, value: filterScope }}
              options={["MessageAttributes", "MessageBody"].map((value) => ({
                label: value,
                value,
              }))}
              onChange={({ detail }) =>
                setFilterScope(detail.selectedOption.value!)
              }
            />
          </FormField>
          {["sqs", "http", "https"].includes(subProtocol.value) && (
            <Checkbox
              checked={rawDelivery}
              onChange={({ detail }) => setRawDelivery(detail.checked)}
            >
              Raw message delivery (send the message body without the SNS
              envelope)
            </Checkbox>
          )}
        </SpaceBetween>
      </Modal>

      {/* Publish Message Modal */}
      <Modal
        visible={publishModalOpen}
        onDismiss={() => setPublishModalOpen(false)}
        header="Publish message to topic"
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setPublishModalOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={
                  !messageBody.length ||
                  (activeTopic?.Type === "FIFO" &&
                    (!messageGroupId.trim() ||
                      (activeTopic.attributes.ContentBasedDeduplication !==
                        "true" &&
                        !messageDeduplicationId.trim())))
                }
                onClick={handlePublishMessage}
                loading={publishing}
              >
                Publish message
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {modalError && <Alert type="error">{modalError}</Alert>}
          <FormField
            label="Subject - optional"
            description="Used as the Subject line for email subscriptions and included in the JSON envelope for others."
          >
            <Input
              value={subject}
              onChange={({ detail }) => setSubject(detail.value)}
              placeholder="e.g. New Order Available"
            />
          </FormField>
          <FormField
            label="Message body"
            description="Enter JSON payload or plaintext notification."
          >
            <Textarea
              value={messageBody}
              onChange={({ detail }) => setMessageBody(detail.value)}
              rows={6}
              placeholder="Enter message body..."
            />
          </FormField>
          <FormField
            label="Message attributes (JSON)"
            description={
              'Use typed values such as {"eventType":{"DataType":"String","StringValue":"order.created"}}. Subscription filters can evaluate these attributes.'
            }
          >
            <Textarea
              rows={4}
              value={messageAttributes}
              onChange={({ detail }) => setMessageAttributes(detail.value)}
            />
          </FormField>
          {activeTopic?.Type === "FIFO" && (
            <ColumnLayout columns={2}>
              <FormField
                label="Message group ID"
                description="Messages in the same group are ordered together."
              >
                <Input
                  value={messageGroupId}
                  onChange={({ detail }) => setMessageGroupId(detail.value)}
                  placeholder="e.g. group-1"
                />
              </FormField>
              <FormField
                label="Deduplication ID"
                description={
                  activeTopic?.attributes.ContentBasedDeduplication === "true"
                    ? "Optional: SNS can deduplicate using a hash of the message body."
                    : "Required: content-based deduplication is not enabled."
                }
              >
                <Input
                  value={messageDeduplicationId}
                  onChange={({ detail }) =>
                    setMessageDeduplicationId(detail.value)
                  }
                  placeholder="e.g. dedup-tx-1002"
                />
              </FormField>
            </ColumnLayout>
          )}
        </SpaceBetween>
      </Modal>

      <ConfirmActionModal
        visible={deleteModalOpen}
        header={`Delete topic: ${activeTopic?.TopicName || ""}`}
        confirmLabel="Delete"
        confirmationText="delete me"
        loading={deletingTopic}
        onConfirm={handleDeleteTopic}
        onDismiss={() => setDeleteModalOpen(false)}
      >
        {modalError ? `${modalError} ` : ""}Deleting this topic also deletes all of its subscriptions. Messages published before deletion may still be delivered.
      </ConfirmActionModal>
    </SpaceBetween>
  );
};
