import { useSession } from "../components/SessionContext";
import { StackOverview } from "../components/StackOverview";
import { ChangeSetReview } from "../components/ChangeSetReview";
import {
  cloudformationStacks,
  cfnStatus,
  cfnTime,
  StackItem,
  previousStackParameters,
  parseStackParameters,
  canExecuteChangeSet,
} from "./cloudformationResources";
import Checkbox from "@cloudscape-design/components/checkbox";
import React, { useState, useEffect, useRef } from "react";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Table from "../components/ResourceTable";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Textarea from "@cloudscape-design/components/textarea";
import Modal from "@cloudscape-design/components/modal";
import Box from "@cloudscape-design/components/box";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Alert from "@cloudscape-design/components/alert";
import TextFilter from "@cloudscape-design/components/text-filter";
import Tabs from "@cloudscape-design/components/tabs";
import Badge from "@cloudscape-design/components/badge";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import {
  createCloudFormationStack,
  updateCloudFormationStack,
  deleteCloudFormationStack,
  validateCloudFormationTemplate,
  createCloudFormationChangeSet,
  executeCloudFormationChangeSet,
  deleteCloudFormationChangeSet,
} from "../api/client";
import { FlociNote } from "../components/FlociNote";

const SAMPLE_TEMPLATE = `AWSTemplateFormatVersion: '2010-09-09'
Description: Floci Serverless Application Stack
Parameters:
  EnvironmentName:
    Type: String
    Default: dev
    Description: Deployment target environment
Resources:
  ApplicationBucket:
    Type: 'AWS::S3::Bucket'
    Properties:
      BucketName: !Sub 'floci-\${EnvironmentName}-app-assets'
  EventQueue:
    Type: 'AWS::SQS::Queue'
    Properties:
      QueueName: !Sub 'floci-\${EnvironmentName}-events'
      VisibilityTimeout: 60
  NotificationTopic:
    Type: 'AWS::SNS::Topic'
    Properties:
      TopicName: !Sub 'floci-\${EnvironmentName}-alerts'
Outputs:
  BucketArn:
    Description: 'ARN of created S3 bucket'
    Value: !GetAtt ApplicationBucket.Arn
  QueueUrl:
    Description: 'URL of created SQS queue'
    Value: !Ref EventQueue
  TopicArn:
    Description: 'ARN of created SNS topic'
    Value: !Ref NotificationTopic
`;

type Props = { resourcePath?: string; onNavigate?: (path: string) => void };
export const CloudFormationConsole: React.FC<Props> = (props) => {
  const { identity, details, loading } = useSession();
  if (!identity && loading)
    return (
      <StatusIndicator type="loading">Loading session context</StatusIndicator>
    );
  const stack = new URLSearchParams(
    props.resourcePath?.split("?")[1] || "",
  ).get("stack");
  return (
    <StackWorkspace
      key={JSON.stringify([
        stack,
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
const StackWorkspace: React.FC<Props> = ({
  resourcePath = "cloudformation",
  onNavigate,
}) => {
  const query = new URLSearchParams(resourcePath.split("?")[1] || "");
  const requested = query.get("stack");
  const requestedChangeSet = query.get("changeSet");
  const stackPath = (stack: string, tab = "overview", changeSet?: string) =>
    `cloudformation/${tab}?${new URLSearchParams({ stack, ...(changeSet ? { changeSet } : {}) })}`;
  const alive = useRef(true);
  const generation = useRef(0);
  const validationRevision = useRef(0);
  const mutationLock = useRef(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      generation.current++;
      validationRevision.current++;
    };
  }, []);
  const [stacks, setStacks] = useState<StackItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterText, setFilterText] = useState("");
  const [selectedStacks, setSelectedStacks] = useState<any[]>([]);
  const [actionAlert, setActionAlert] = useState<{
    type: "success" | "error" | "info";
    message: string;
  } | null>(null);
  const activeTabId = resourcePath.split("?")[0].split("/")[1] || "overview";
  const setActiveTabId = (tab: string) =>
    activeStack && onNavigate?.(stackPath(activeStack.StackName, tab));
  const [parameters, setParameters] = useState("[]");
  const [capabilities, setCapabilities] = useState("");
  const [disableRollback, setDisableRollback] = useState(false);
  const [changeSetTemplateBody, setChangeSetTemplateBody] = useState("");
  const [confirmation, setConfirmation] = useState<{
    kind: "execute" | "delete";
    name: string;
  } | null>(null);
  const [changeSetBusy, setChangeSetBusy] = useState(false);

  // Create Stack Modal
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [stackName, setStackName] = useState("");
  const [templateBody, setTemplateBody] = useState("");
  const [creating, setCreating] = useState(false);
  const [validating, setValidating] = useState(false);
  const [validationResult, setValidationResult] = useState<any | null>(null);

  // Update Stack Modal
  const [updateModalOpen, setUpdateModalOpen] = useState(false);
  const [updateTemplateBody, setUpdateTemplateBody] = useState("");
  const [updating, setUpdating] = useState(false);

  // Change Set Modal
  const [changeSetModalOpen, setChangeSetModalOpen] = useState(false);
  const [changeSetName, setChangeSetName] = useState("");
  const [creatingChangeSet, setCreatingChangeSet] = useState(false);

  // Delete Modal
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const loadData = async () => {
    const current = ++generation.current;
    setLoading(true);
    try {
      const response = await fetch("/api/cloudformation/");
      const payload = await response.json();
      if (!response.ok || payload.error)
        throw new Error(payload.error || "Unable to load stacks.");
      if (!alive.current || current !== generation.current) return;
      setStacks(cloudformationStacks(payload));
    } catch (err: any) {
      if (!alive.current || current !== generation.current) return;
      setStacks([]);
      setActionAlert({
        type: "error",
        message: err.message || "Failed to load CloudFormation stacks.",
      });
    } finally {
      if (alive.current && current === generation.current) setLoading(false);
    }
  };
  useEffect(() => {
    void loadData();
  }, []);
  const activeStack = requested
    ? stacks.find(
        (stack) => stack.StackName === requested || stack.StackId === requested,
      ) || null
    : null;
  const readCapabilities = () =>
    capabilities
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
  const prepareEdit = (changeSet = false) => {
    if (!activeStack) return;
    setParameters(previousStackParameters(activeStack));
    setCapabilities(activeStack.Capabilities.join(", "));
    setUpdateTemplateBody(activeStack.Template);
    setChangeSetTemplateBody(activeStack.Template);
    setActionAlert(null);
    if (changeSet) {
      setChangeSetName("");
      setChangeSetModalOpen(true);
    } else setUpdateModalOpen(true);
  };
  const operationFields = (
    <SpaceBetween size="m">
      <FormField
        label="Parameters (JSON)"
        description="Use an object of values or an array of ParameterKey / ParameterValue entries. UsePreviousValue: true keeps an existing value on update."
      >
        <Textarea
          rows={5}
          value={parameters}
          onChange={({ detail }) => setParameters(detail.value)}
        />
      </FormField>
      <FormField
        label="Capabilities (comma-separated)"
        description="Enter the capabilities your template requires: CAPABILITY_IAM, CAPABILITY_NAMED_IAM, or CAPABILITY_AUTO_EXPAND."
      >
        <Input
          value={capabilities}
          onChange={({ detail }) => setCapabilities(detail.value)}
        />
      </FormField>
    </SpaceBetween>
  );

  const handleValidateTemplate = async () => {
    const version = ++validationRevision.current;
    setValidating(true);
    setValidationResult(null);
    try {
      const res = await validateCloudFormationTemplate(templateBody);
      if (!alive.current || version !== validationRevision.current) return;
      setValidationResult({
        valid: true,
        description: res.validation?.Description,
        params: res.validation?.Parameters || [],
        capabilities: res.validation?.Capabilities || [],
      });
    } catch (err: any) {
      if (!alive.current || version !== validationRevision.current) return;
      setValidationResult({
        valid: false,
        error: err.message || "Template validation failed.",
      });
    } finally {
      if (alive.current && version === validationRevision.current)
        setValidating(false);
    }
  };

  const handleCreateStack = async () => {
    if (!stackName.trim() || !templateBody.trim() || mutationLock.current)
      return;
    mutationLock.current = true;
    setCreating(true);
    setActionAlert(null);
    try {
      await createCloudFormationStack(
        stackName.trim(),
        templateBody,
        parseStackParameters(parameters),
        readCapabilities(),
        disableRollback,
      );
      if (!alive.current) return;
      setActionAlert({
        type: "success",
        message: `Stack "${stackName.trim()}" creation requested. Monitor stack events for completion.`,
      });
      setCreateModalOpen(false);
      setStackName("");
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to create stack.",
      });
    } finally {
      mutationLock.current = false;
      if (alive.current) setCreating(false);
    }
  };

  const handleUpdateStack = async () => {
    if (!activeStack || !updateTemplateBody.trim() || mutationLock.current)
      return;
    mutationLock.current = true;
    setUpdating(true);
    setActionAlert(null);
    try {
      await updateCloudFormationStack(
        activeStack.StackName,
        updateTemplateBody,
        parseStackParameters(parameters),
        readCapabilities(),
      );
      if (!alive.current) return;
      setActionAlert({
        type: "success",
        message: `Stack "${activeStack.StackName}" update initiated.`,
      });
      setUpdateModalOpen(false);
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to update stack.",
      });
    } finally {
      mutationLock.current = false;
      if (alive.current) setUpdating(false);
    }
  };

  const handleCreateChangeSet = async () => {
    if (
      !activeStack ||
      !changeSetName.trim() ||
      !changeSetTemplateBody.trim() ||
      mutationLock.current
    )
      return;
    mutationLock.current = true;
    setCreatingChangeSet(true);
    setActionAlert(null);
    try {
      await createCloudFormationChangeSet(
        activeStack.StackName,
        changeSetName.trim(),
        changeSetTemplateBody,
        "UPDATE",
        parseStackParameters(parameters),
        readCapabilities(),
      );
      setActionAlert({
        type: "success",
        message: `Change set "${changeSetName}" creation requested. Review its status and resource changes before execution.`,
      });
      if (!alive.current) return;
      setChangeSetModalOpen(false);
      onNavigate?.(
        stackPath(activeStack.StackName, "change_sets", changeSetName.trim()),
      );
      setChangeSetName("");
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to create change set.",
      });
    } finally {
      mutationLock.current = false;
      if (alive.current) setCreatingChangeSet(false);
    }
  };

  const handleExecuteChangeSet = async (csName: string) => {
    if (!activeStack || mutationLock.current) return;
    mutationLock.current = true;
    setChangeSetBusy(true);
    try {
      await executeCloudFormationChangeSet(activeStack.StackName, csName);
      if (!alive.current) return;
      setConfirmation(null);
      onNavigate?.(stackPath(activeStack.StackName, "events"));
      setActionAlert({
        type: "success",
        message: `Execution requested for change set "${csName}". Monitor stack events for completion.`,
      });
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to execute change set.",
      });
    } finally {
      mutationLock.current = false;
      if (alive.current) setChangeSetBusy(false);
    }
  };

  const handleDeleteChangeSet = async (csName: string) => {
    if (!activeStack || mutationLock.current) return;
    mutationLock.current = true;
    setChangeSetBusy(true);
    try {
      await deleteCloudFormationChangeSet(activeStack.StackName, csName);
      if (!alive.current) return;
      setConfirmation(null);
      onNavigate?.(stackPath(activeStack.StackName, "change_sets"));
      setActionAlert({
        type: "success",
        message: `Change set "${csName}" deleted.`,
      });
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to delete change set.",
      });
    } finally {
      mutationLock.current = false;
      if (alive.current) setChangeSetBusy(false);
    }
  };

  const handleDeleteStack = async () => {
    if (!activeStack || mutationLock.current) return;
    mutationLock.current = true;
    setDeleting(true);
    try {
      await deleteCloudFormationStack(activeStack.StackName);
      if (!alive.current) return;
      setActionAlert({
        type: "success",
        message: `Deletion requested for stack "${activeStack.StackName}". Monitor its status until deletion completes.`,
      });
      setDeleteModalOpen(false);
      setSelectedStacks([]);
      onNavigate?.("cloudformation");
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to delete stack.",
      });
    } finally {
      mutationLock.current = false;
      if (alive.current) setDeleting(false);
    }
  };

  const filteredStacks = stacks.filter((s: any) => {
    const text =
      `${s.StackName} ${s.StackStatus} ${s.Description}`.toLowerCase();
    return text.includes(filterText.toLowerCase());
  });

  return (
    <SpaceBetween size="l">
      {actionAlert &&
        !confirmation &&
        !createModalOpen &&
        !updateModalOpen &&
        !changeSetModalOpen &&
        !deleteModalOpen && (
          <Alert
            type={actionAlert.type}
            dismissible
            onDismiss={() => setActionAlert(null)}
            header={
              actionAlert.type === "error"
                ? "CloudFormation Error"
                : "CloudFormation Notification"
            }
          >
            {actionAlert.message}
          </Alert>
        )}

      <Header
        variant="h1"
        description={
          activeStack
            ? activeStack.Description || undefined
            : "Provision resources from templates, inspect deployment events, and review proposed changes."
        }
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button iconName="refresh" onClick={loadData} loading={loading}>
              Refresh
            </Button>
            {activeStack && (
              <Button onClick={() => prepareEdit(true)}>
                Create change set
              </Button>
            )}
            {activeStack && (
              <Button onClick={() => prepareEdit()}>Update Stack</Button>
            )}
            {activeStack && (
              <Button onClick={() => setDeleteModalOpen(true)}>
                Delete
              </Button>
            )}
            {!requested && (
              <Button
                variant="primary"
                onClick={() => {
                  setParameters("[]");
                  setCapabilities("");
                  setDisableRollback(false);
                  setValidationResult(null);
                  setCreateModalOpen(true);
                }}
              >
                Create stack
              </Button>
            )}
          </SpaceBetween>
        }
      >
        {requested
          ? `Stack: ${activeStack?.StackName || requested}`
          : "AWS CloudFormation"}
      </Header>
      {requested && (
        <Button
          variant="link"
          iconName="arrow-left"
          onClick={() => onNavigate?.("cloudformation")}
        >
          Stacks
        </Button>
      )}
      {requested && !loading && !activeStack && (
        <Alert type="warning" header="Stack not found">
          This stack is not reported in the current session. Select an available
          stack.
        </Alert>
      )}
      {activeStack?.issues.length ? (
        <Alert type="warning" header="Some stack data could not be loaded">
          {activeStack.issues.join(". ")}
        </Alert>
      ) : null}
      {/* Stacks Table */}
      {!requested && (
        <Table
          header={
            <Header
              variant="h2"
              description="CloudFormation stacks provisioned in Floci."
              counter={`(${stacks.length})`}
            >
              Stacks
            </Header>
          }
          columnDefinitions={[
            {
              id: "name",
              header: "Stack name",
              cell: (item) => (
                <Button
                  variant="inline-link"
                  onClick={() => onNavigate?.(stackPath(item.StackName))}
                >
                  <strong>{item.StackName}</strong>
                </Button>
              ),
              sortingField: "StackName",
              isRowHeader: true,
            },
            {
              id: "status",
              header: "Status",
              cell: (item) => (
                <StatusIndicator type={cfnStatus(item.StackStatus)}>
                  {item.StackStatus || "Not reported"}
                </StatusIndicator>
              ),
            },
            {
              id: "resources",
              header: "Resources",
              cell: (item) => (
                <Badge color="blue">
                  {item.Resources?.length || 0} resources
                </Badge>
              ),
            },
            {
              id: "created",
              header: "Created time",
              cell: (item) => (
                <span style={{ color: "#879596", fontSize: "12px" }}>
                  {cfnTime(item.CreationTime)}
                </span>
              ),
            },
            {
              id: "description",
              header: "Description",
              cell: (item) => item.Description,
            },
          ]}
          items={filteredStacks}
          loading={loading}
          loadingText="Loading CloudFormation stacks..."
          selectionType="single"
          selectedItems={selectedStacks}
          onSelectionChange={({ detail }) => {
            setSelectedStacks(detail.selectedItems);
            if (detail.selectedItems[0])
              onNavigate?.(stackPath(detail.selectedItems[0].StackName));
          }}
          filter={
            <TextFilter
              filteringText={filterText}
              filteringPlaceholder="Filter stacks by name, status, or description..."
              onChange={({ detail }) => setFilterText(detail.filteringText)}
            />
          }
          empty={
            <Box textAlign="center" color="inherit" padding={{ vertical: "l" }}>
              <SpaceBetween size="m">
                <b>No CloudFormation stacks found</b>
                <p>
                  Deploy a CloudFormation YAML or JSON template to provision
                  resources.
                </p>
                <Button
                  variant="primary"
                  onClick={() => setCreateModalOpen(true)}
                >
                  Create stack
                </Button>
              </SpaceBetween>
            </Box>
          }
        />
      )}
      {/* Stack Inspector Workspace */}
      {activeStack && (
        <Container>
          <Tabs
            activeTabId={activeTabId}
            onChange={({ detail }) => setActiveTabId(detail.activeTabId)}
            tabs={[
              {
                id: "overview",
                label: "Overview",
                content: <StackOverview stack={activeStack} />,
              },
              {
                id: "events",
                label: `Events (${activeStack.Events?.length || 0})`,
                content: (
                  <SpaceBetween size="m">
                    <Table
                      header={<Header variant="h3">Deployment events</Header>}
                      columnDefinitions={[
                        {
                          id: "timestamp",
                          header: "Timestamp",
                          cell: (ev: any) => (
                            <span
                              style={{ fontSize: "11px", color: "#879596" }}
                            >
                              {cfnTime(ev.Timestamp ?? ev.EventTime)}
                            </span>
                          ),
                          width: 140,
                        },
                        {
                          id: "logicalId",
                          header: "Logical ID",
                          cell: (ev: any) => (
                            <strong>
                              {ev.LogicalResourceId || "Not reported"}
                            </strong>
                          ),
                        },
                        {
                          id: "type",
                          header: "Resource Type",
                          cell: (ev: any) => (
                            <Badge color="grey">
                              {ev.ResourceType || "Not reported"}
                            </Badge>
                          ),
                        },
                        {
                          id: "status",
                          header: "Resource Status",
                          cell: (ev: any) => {
                            const st = ev.ResourceStatus || "";
                            return (
                              <StatusIndicator type={cfnStatus(st)}>
                                {st || "Not reported"}
                              </StatusIndicator>
                            );
                          },
                        },
                        {
                          id: "reason",
                          header: "Status Reason",
                          cell: (ev: any) => ev.ResourceStatusReason || "—",
                        },
                      ]}
                      items={activeStack.Events || []}
                      empty={
                        <Box textAlign="center" padding={{ vertical: "m" }}>
                          No events recorded for this stack.
                        </Box>
                      }
                    />
                  </SpaceBetween>
                ),
              },
              {
                id: "resources",
                label: `Resources (${activeStack.Resources?.length || 0})`,
                content: (
                  <SpaceBetween size="m">
                    <Table
                      header={
                        <Header variant="h3">
                          Provisioned Stack Resources
                        </Header>
                      }
                      columnDefinitions={[
                        {
                          id: "logicalId",
                          header: "Logical ID",
                          cell: (r: any) => (
                            <strong>
                              {r.LogicalResourceId || r.logical_resource_id}
                            </strong>
                          ),
                        },
                        {
                          id: "physicalId",
                          header: "Physical ID / Resource Name",
                          cell: (r: any) => (
                            <code style={{ fontSize: "11px" }}>
                              {r.PhysicalResourceId ||
                                r.physical_resource_id ||
                                "—"}
                            </code>
                          ),
                        },
                        {
                          id: "type",
                          header: "Type",
                          cell: (r: any) => (
                            <Badge color="blue">
                              {r.ResourceType || r.resource_type}
                            </Badge>
                          ),
                        },
                        {
                          id: "status",
                          header: "Status",
                          cell: (r: any) => (
                            <StatusIndicator
                              type={cfnStatus(
                                r.ResourceStatus || r.resource_status,
                              )}
                            >
                              {r.ResourceStatus ||
                                r.resource_status ||
                                "Not reported"}
                            </StatusIndicator>
                          ),
                        },
                      ]}
                      items={activeStack.Resources || []}
                      empty={
                        <Box textAlign="center" padding={{ vertical: "m" }}>
                          No resources tracked in this stack.
                        </Box>
                      }
                    />
                  </SpaceBetween>
                ),
              },
              {
                id: "outputs_params",
                label: "Outputs",
                content: (
                  <SpaceBetween size="l">
                    <Container
                      header={
                        <Header variant="h3">
                          Stack Parameters (
                          {activeStack.Parameters?.length || 0})
                        </Header>
                      }
                    >
                      <Table
                        columnDefinitions={[
                          {
                            id: "key",
                            header: "Parameter Key",
                            cell: (p: any) => (
                              <strong>{p.ParameterKey || p.key}</strong>
                            ),
                          },
                          {
                            id: "value",
                            header: "Parameter Value",
                            cell: (p: any) => (
                              <code>
                                {String(
                                  p.ParameterValue ?? p.value ?? "Not reported",
                                )}
                              </code>
                            ),
                          },
                        ]}
                        items={activeStack.Parameters || []}
                        empty={
                          <Box textAlign="center">
                            No input parameters configured for this stack.
                          </Box>
                        }
                      />
                    </Container>

                    <Container
                      header={
                        <Header variant="h3">
                          Stack Outputs ({activeStack.Outputs?.length || 0})
                        </Header>
                      }
                    >
                      <Table
                        columnDefinitions={[
                          {
                            id: "key",
                            header: "Output Key",
                            cell: (i: any) => (
                              <strong>{i.OutputKey || i.key}</strong>
                            ),
                          },
                          {
                            id: "value",
                            header: "Output Value",
                            cell: (i: any) => (
                              <code>
                                {String(
                                  i.OutputValue ?? i.value ?? "Not reported",
                                )}
                              </code>
                            ),
                          },
                          {
                            id: "desc",
                            header: "Description",
                            cell: (i: any) =>
                              i.Description || i.description || "—",
                          },
                        ]}
                        items={activeStack.Outputs || []}
                        empty={
                          <Box textAlign="center">
                            No outputs declared in this stack template.
                          </Box>
                        }
                      />
                    </Container>
                  </SpaceBetween>
                ),
              },
              {
                id: "change_sets",
                label: `Change sets (${activeStack.ChangeSets?.length || 0})`,
                content: (
                  <SpaceBetween size="m">
                    <Table
                      header={
                        <Header
                          variant="h3"
                          actions={
                            <Button
                              variant="primary"
                              iconName="add-plus"
                              onClick={() => prepareEdit(true)}
                            >
                              Create change set
                            </Button>
                          }
                        >
                          Change sets
                        </Header>
                      }
                      columnDefinitions={[
                        {
                          id: "name",
                          header: "Change set name",
                          cell: (cs: any) => (
                            <Button
                              variant="inline-link"
                              onClick={() =>
                                onNavigate?.(
                                  stackPath(
                                    activeStack.StackName,
                                    "change_sets",
                                    cs.ChangeSetName || cs.change_set_name,
                                  ),
                                )
                              }
                            >
                              {cs.ChangeSetName || cs.change_set_name}
                            </Button>
                          ),
                        },
                        {
                          id: "status",
                          header: "Status",
                          cell: (cs: any) => (
                            <StatusIndicator type={cfnStatus(cs.Status)}>
                              {cs.Status || "Not reported"}
                            </StatusIndicator>
                          ),
                        },
                        {
                          id: "created",
                          header: "Creation Time",
                          cell: (cs: any) => cfnTime(cs.CreationTime),
                        },
                        {
                          id: "actions",
                          header: "Actions",
                          cell: (cs: any) => (
                            <SpaceBetween direction="horizontal" size="xs">
                              <Button
                                onClick={() =>
                                  onNavigate?.(
                                    stackPath(
                                      activeStack.StackName,
                                      "change_sets",
                                      cs.ChangeSetName || cs.change_set_name,
                                    ),
                                  )
                                }
                              >
                                Review changes
                              </Button>
                              <Button
                                onClick={() =>
                                  setConfirmation({
                                    kind: "delete",
                                    name:
                                      cs.ChangeSetName || cs.change_set_name,
                                  })
                                }
                              >
                                Delete
                              </Button>
                            </SpaceBetween>
                          ),
                        },
                      ]}
                      items={activeStack.ChangeSets || []}
                      empty={
                        <Box textAlign="center" padding={{ vertical: "m" }}>
                          No change sets created for this stack.
                        </Box>
                      }
                    />
                    {requestedChangeSet && (
                      <ChangeSetReview
                        key={requestedChangeSet}
                        stack={activeStack.StackName}
                        name={requestedChangeSet}
                        onExecute={(change) => {
                          if (canExecuteChangeSet(change))
                            setConfirmation({
                              kind: "execute",
                              name: requestedChangeSet,
                            });
                        }}
                        onDelete={(name) =>
                          setConfirmation({ kind: "delete", name })
                        }
                      />
                    )}
                  </SpaceBetween>
                ),
              },
              {
                id: "template",
                label: "Template",
                content: (
                  <SpaceBetween size="m">
                    <KeyValuePairs
                      columns={3}
                      items={[
                        {
                          label: "Stack ID",
                          value: activeStack.StackId || "Not reported",
                        },
                        {
                          label: "Disable Rollback",
                          value:
                            activeStack.DisableRollback === undefined
                              ? "Not reported"
                              : activeStack.DisableRollback
                                ? "Yes"
                                : "No",
                        },
                        {
                          label: "Status Reason",
                          value:
                            activeStack.StackStatusReason || "None reported",
                        },
                      ]}
                    />

                    <Header variant="h3">Template Body</Header>
                    <Textarea
                      readOnly
                      rows={18}
                      value={activeStack.Template || "Template not reported"}
                    />
                  </SpaceBetween>
                ),
              },
            ]}
          />
        </Container>
      )}

      {/* Create Stack Modal */}
      <Modal
        visible={createModalOpen}
        onDismiss={() => !creating && setCreateModalOpen(false)}
        header="Create CloudFormation Stack"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateModalOpen(false)}>
                Cancel
              </Button>
              <Button
                disabled={!templateBody.trim()}
                onClick={handleValidateTemplate}
                loading={validating}
              >
                Validate Template
              </Button>
              <Button
                variant="primary"
                disabled={!stackName.trim() || !templateBody.trim()}
                loading={creating}
                onClick={handleCreateStack}
              >
                Deploy Stack
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FlociNote>
            Validate Template accepts any template and returns no parameters,
            so a mistake shows up only when the stack is created. Resource
            types Floci doesn&rsquo;t support still reach CREATE_COMPLETE but
            nothing is created for them; the stack events say which ones.
          </FlociNote>
          {validationResult && (
            <Alert
              type={validationResult.valid ? "success" : "error"}
              header={
                validationResult.valid ? "Template Valid" : "Validation Error"
              }
            >
              {validationResult.valid
                ? validationResult.description || "Floci accepted the template."
                : validationResult.error}
            </Alert>
          )}

          <Button
            onClick={() => {
              validationRevision.current++;
              setValidationResult(null);
              setTemplateBody(SAMPLE_TEMPLATE);
            }}
          >
            Load example template
          </Button>
          <FormField
            label="Stack name"
            description="Unique alphanumeric identifier for the CloudFormation stack."
          >
            <Input
              value={stackName}
              onChange={({ detail }) => setStackName(detail.value)}
              placeholder="e.g. dev-infrastructure"
            />
          </FormField>

          <FormField
            label="Template Body (YAML or JSON)"
            description="Paste your CloudFormation template definition."
          >
            <Textarea
              rows={12}
              value={templateBody}
              onChange={({ detail }) => {
                validationRevision.current++;
                setValidationResult(null);
                setValidating(false);
                setTemplateBody(detail.value);
              }}
              placeholder="AWSTemplateFormatVersion: '2010-09-09'..."
            />
          </FormField>
          {operationFields}
          {createModalOpen && actionAlert?.type === "error" && (
            <Alert type="error">{actionAlert.message}</Alert>
          )}
          <Checkbox
            checked={disableRollback}
            onChange={({ detail }) => setDisableRollback(detail.checked)}
          >
            Disable rollback on creation failure
          </Checkbox>
          {validationResult?.valid && (
            <KeyValuePairs
              columns={2}
              minColumnWidth={260}
              items={[
                {
                  label: "Template parameters",
                  value:
                    validationResult.params
                      ?.map((parameter: any) => parameter.ParameterKey)
                      .join(", ") || "None reported",
                },
                {
                  label: "Required capabilities",
                  value:
                    validationResult.capabilities?.join(", ") ||
                    "None reported",
                },
              ]}
            />
          )}
        </SpaceBetween>
      </Modal>

      {/* Update Stack Modal */}
      <Modal
        visible={updateModalOpen}
        onDismiss={() => !updating && setUpdateModalOpen(false)}
        header={`Update Stack: ${activeStack?.StackName}`}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setUpdateModalOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={updating}
                onClick={handleUpdateStack}
              >
                Update Stack
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Updated Template Body (YAML or JSON)">
            <Textarea
              rows={14}
              value={updateTemplateBody}
              onChange={({ detail }) => setUpdateTemplateBody(detail.value)}
            />
          </FormField>
          {operationFields}
          {updateModalOpen && actionAlert?.type === "error" && (
            <Alert type="error">{actionAlert.message}</Alert>
          )}
        </SpaceBetween>
      </Modal>

      {/* Create Change Set Modal */}
      <Modal
        visible={changeSetModalOpen}
        onDismiss={() => !creatingChangeSet && setChangeSetModalOpen(false)}
        header={`Create change set for ${activeStack?.StackName}`}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                onClick={() => setChangeSetModalOpen(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={
                  !changeSetName.trim() || !changeSetTemplateBody.trim()
                }
                loading={creatingChangeSet}
                onClick={handleCreateChangeSet}
              >
                Create change set
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Change set name">
            <Input
              value={changeSetName}
              onChange={({ detail }) => setChangeSetName(detail.value)}
              placeholder="e.g. update-queues-cs"
            />
          </FormField>
          <FormField
            label="Proposed template (YAML or JSON)"
            description="Edit the proposed template to describe the update. Creating a change set does not apply it."
          >
            <Textarea
              rows={14}
              value={changeSetTemplateBody}
              onChange={({ detail }) => setChangeSetTemplateBody(detail.value)}
            />
          </FormField>
          {operationFields}
          {changeSetModalOpen && actionAlert?.type === "error" && (
            <Alert type="error">{actionAlert.message}</Alert>
          )}
        </SpaceBetween>
      </Modal>

      <Modal
        visible={confirmation !== null}
        onDismiss={() => !changeSetBusy && setConfirmation(null)}
        header={
          confirmation?.kind === "execute"
            ? "Execute change set"
            : "Delete change set"
        }
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                disabled={changeSetBusy}
                onClick={() => setConfirmation(null)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={changeSetBusy}
                onClick={() =>
                  confirmation &&
                  (confirmation.kind === "execute"
                    ? handleExecuteChangeSet(confirmation.name)
                    : handleDeleteChangeSet(confirmation.name))
                }
              >
                {confirmation?.kind === "execute" ? "Execute" : "Delete"}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        {confirmation && (
          <SpaceBetween size="m">
            <Box>
              {confirmation.kind === "execute" ? "Apply" : "Delete"} change set{" "}
              <strong>{confirmation.name}</strong> for stack{" "}
              <strong>{activeStack?.StackName}</strong>?{" "}
              {confirmation.kind === "execute"
                ? "Execution applies the proposed resource changes."
                : "Deleting a change set does not delete stack resources."}
            </Box>
            {actionAlert?.type === "error" && (
              <Alert type="error">{actionAlert.message}</Alert>
            )}
          </SpaceBetween>
        )}
      </Modal>
      {/* Delete Confirmation Modal */}
      <Modal
        visible={deleteModalOpen}
        onDismiss={() => !deleting && setDeleteModalOpen(false)}
        header={`Delete stack ${activeStack?.StackName}?`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setDeleteModalOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={deleting}
                onClick={handleDeleteStack}
              >
                Delete
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        {deleteModalOpen && actionAlert?.type === "error" && (
          <Alert type="error">{actionAlert.message}</Alert>
        )}
        <Alert type="warning" header="Resource Deletion">
          Are you sure you want to delete stack{" "}
          <strong>{activeStack?.StackName}</strong>? CloudFormation will delete
          stack resources according to their deletion policies. Retained
          resources can remain.
        </Alert>
      </Modal>
    </SpaceBetween>
  );
};
