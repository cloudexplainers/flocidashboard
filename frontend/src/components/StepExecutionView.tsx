import { useEffect, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import Modal from "@cloudscape-design/components/modal";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Tabs from "@cloudscape-design/components/tabs";
import { stepFunctionsRequest } from "../api/client";
import { CodeSnippet } from "./CodeSnippet";
import Table from "./ResourceTable";
import { WorkflowGraph, workflowEventState } from "./WorkflowGraph";

export function executionDate(value?: string | number) {
  if (value == null) return "—";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}
export function ExecutionPayload({ value }: { value: unknown }) {
  if (value === undefined)
    return <Box color="text-body-secondary">Not reported</Box>;
  let text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  try {
    text = JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    /* Keep plain text intact. */
  }
  return <CodeSnippet language="json" code={text} />;
}
export function ExecutionStatus({ status }: { status?: string }) {
  return (
    <StatusIndicator
      type={
        status === "SUCCEEDED"
          ? "success"
          : status === "RUNNING"
            ? "in-progress"
            : status === "ABORTED"
              ? "stopped"
              : status === "FAILED" || status === "TIMED_OUT"
                ? "error"
                : "not-started"
      }
    >
      {status || "Not reported"}
    </StatusIndicator>
  );
}
export function StepExecutionView({
  arn,
  machineArn,
  definition,
  onBack,
  onChanged,
}: {
  arn: string;
  machineArn: string;
  definition: string;
  onBack: () => void;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<any>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [next, setNext] = useState("");
  const [error, setError] = useState("");
  const [historyError, setHistoryError] = useState("");
  const [loading, setLoading] = useState(true);
  const [moreLoading, setMoreLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const [event, setEvent] = useState<any>(null);
  const [stopOpen, setStopOpen] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [stopError, setStopError] = useState("");
  const generation = useRef(0);
  const moreLock = useRef(false);
  const stopLock = useRef(false);
  useEffect(() => {
    const current = ++generation.current;
    let alive = true;
    setDetail(null);
    setEvents([]);
    setNext("");
    setEvent(null);
    setError("");
    setHistoryError("");
    setLoading(true);
    const encoded = encodeURIComponent(arn);
    void (async () => {
      try {
        const data = await stepFunctionsRequest(
          `executions/detail/${encoded}/`,
        );
        if (!alive || current !== generation.current) return;
        if (data.stateMachineArn && data.stateMachineArn !== machineArn)
          throw new Error(
            "This execution belongs to another state machine. Open it from its workflow.",
          );
        setDetail(data);
      } catch (failure: any) {
        if (alive && current === generation.current) setError(failure.message);
        return;
      }
      try {
        const data = await stepFunctionsRequest(
          `executions/history/${encoded}/`,
        );
        if (alive && current === generation.current) {
          setEvents(data.events || []);
          setNext(data.next_token || "");
        }
      } catch (failure: any) {
        if (alive && current === generation.current)
          setHistoryError(failure.message);
      } finally {
        if (alive && current === generation.current) setLoading(false);
      }
    })().finally(() => {
      if (alive && current === generation.current) setLoading(false);
    });
    return () => {
      alive = false;
      generation.current++;
    };
  }, [arn, machineArn, revision]);
  const more = async () => {
    if (!next || moreLock.current) return;
    moreLock.current = true;
    setMoreLoading(true);
    setHistoryError("");
    const current = generation.current;
    try {
      const data = await stepFunctionsRequest(
        `executions/history/${encodeURIComponent(arn)}/?next_token=${encodeURIComponent(next)}`,
      );
      if (current === generation.current) {
        setEvents((previous) => [...previous, ...(data.events || [])]);
        setNext(data.next_token || "");
      }
    } catch (failure: any) {
      if (current === generation.current) setHistoryError(failure.message);
    } finally {
      moreLock.current = false;
      if (current === generation.current) setMoreLoading(false);
    }
  };
  const stop = async () => {
    if (stopLock.current) return;
    stopLock.current = true;
    setStopping(true);
    setStopError("");
    const current = generation.current;
    try {
      await stepFunctionsRequest("executions/stop/", "POST", {
        execution_arn: arn,
        error: "UserCancelled",
        cause: "Stopped from the execution console",
      });
      if (current === generation.current) {
        setStopOpen(false);
        setRevision((value) => value + 1);
        onChanged();
      }
    } catch (failure: any) {
      if (current === generation.current) setStopError(failure.message);
    } finally {
      stopLock.current = false;
      if (current === generation.current) setStopping(false);
    }
  };
  return (
    <SpaceBetween size="l">
      <Button variant="link" iconName="arrow-left" onClick={onBack}>
        Back to executions
      </Button>
      <Header
        variant="h1"
        description={arn}
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button
              iconName="refresh"
              loading={loading}
              onClick={() => setRevision((value) => value + 1)}
            >
              Refresh execution
            </Button>
            <Button
              disabled={detail?.status !== "RUNNING" || !!error}
              onClick={() => {
                setStopError("");
                setStopOpen(true);
              }}
            >
              Stop execution
            </Button>
          </SpaceBetween>
        }
      >
        Execution: {detail?.name || arn.split(":").pop()}
      </Header>
      {error && <Alert type="error">{error}</Alert>}
      {detail && (
        <Container header={<Header variant="h2">Execution details</Header>}>
          <KeyValuePairs
            columns={2}
            minColumnWidth={280}
            items={[
              {
                label: "Status",
                value: <ExecutionStatus status={detail.status} />,
              },
              {
                label: "State machine ARN",
                value: detail.stateMachineArn || machineArn,
              },
              { label: "Started", value: executionDate(detail.startDate) },
              { label: "Stopped", value: executionDate(detail.stopDate) },
              { label: "Execution ARN", value: arn },
              {
                label: "Trace header",
                value: detail.traceHeader || "Not reported",
              },
            ]}
          />
        </Container>
      )}
      {detail?.error && (
        <Alert type="error" header={detail.error}>
          {detail.cause || "No cause was reported."}
        </Alert>
      )}
      {detail && (
        <Tabs
          tabs={[
            {
              id: "graph",
              label: "Graph and history",
              content: (
                <SpaceBetween size="l">
                  <Alert type="info">
                    This graph uses the current state machine definition. The
                    execution may have started from an earlier revision. Node
                    status reflects the history events loaded below.
                  </Alert>
                  <Container
                    header={<Header variant="h2">Execution state map</Header>}
                  >
                    <WorkflowGraph
                      key={arn}
                      definition={definition}
                      events={events}
                      highlightedState={
                        event
                          ? workflowEventState(
                              event,
                              new Map(events.map((item) => [item.id, item])),
                            )
                          : undefined
                      }
                    />
                  </Container>
                  {historyError && (
                    <Alert type="error" header="History unavailable">
                      <span>{historyError}</span>
                      <Box>
                        <Button
                          onClick={() => setRevision((value) => value + 1)}
                        >
                          Retry history
                        </Button>
                      </Box>
                    </Alert>
                  )}
                  <Table
                    header={
                      <Header
                        variant="h2"
                        counter={`(${events.length} loaded)`}
                      >
                        Execution history
                      </Header>
                    }
                    loading={loading}
                    items={events}
                    columnDefinitions={[
                      {
                        id: "id",
                        header: "Event ID",
                        minWidth: 100,
                        cell: (item) => (
                          <Button
                            variant="inline-link"
                            onClick={() => setEvent(item)}
                          >
                            {item.id}
                          </Button>
                        ),
                      },
                      {
                        id: "type",
                        header: "Event type",
                        minWidth: 210,
                        cell: (item) => item.type,
                      },
                      {
                        id: "timestamp",
                        header: "Timestamp",
                        minWidth: 180,
                        cell: (item) => executionDate(item.timestamp),
                      },
                      {
                        id: "previousEventId",
                        header: "Previous event",
                        minWidth: 130,
                        cell: (item) => item.previousEventId ?? "—",
                      },
                    ]}
                    empty="No history events reported."
                  />
                  {next && (
                    <Button loading={moreLoading} onClick={more}>
                      Load more events
                    </Button>
                  )}
                  {event && (
                    <Container
                      header={
                        <Header variant="h2">
                          Event {event.id}: {event.type}
                        </Header>
                      }
                    >
                      <ExecutionPayload
                        value={Object.fromEntries(
                          Object.entries(event).filter(([key]) =>
                            /EventDetails$/.test(key),
                          ),
                        )}
                      />
                    </Container>
                  )}
                </SpaceBetween>
              ),
            },
            {
              id: "input-output",
              label: "Input and output",
              content: (
                <SpaceBetween size="l">
                  <Container
                    header={<Header variant="h2">Execution input</Header>}
                  >
                    <ExecutionPayload value={detail.input} />
                  </Container>
                  <Container
                    header={<Header variant="h2">Execution output</Header>}
                  >
                    <ExecutionPayload value={detail.output} />
                  </Container>
                  {detail.cause && (
                    <Container
                      header={<Header variant="h2">Failure cause</Header>}
                    >
                      <ExecutionPayload value={detail.cause} />
                    </Container>
                  )}
                </SpaceBetween>
              ),
            },
          ]}
        />
      )}
      <Modal
        visible={stopOpen}
        onDismiss={() => {
          if (!stopping) setStopOpen(false);
        }}
        header="Stop execution"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button disabled={stopping} onClick={() => setStopOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={stopping} onClick={stop}>
                Stop execution
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {stopError && <Alert type="error">{stopError}</Alert>}
          <Alert type="warning">
            Stop {detail?.name || arn}? This aborts the running workflow.
            Completed work is retained in execution history.
          </Alert>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
}
