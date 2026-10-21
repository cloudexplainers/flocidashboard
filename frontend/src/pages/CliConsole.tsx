import React, { useEffect, useRef, useState } from "react";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import FormField from "@cloudscape-design/components/form-field";
import Textarea, {
  TextareaProps,
} from "@cloudscape-design/components/textarea";
import Select from "@cloudscape-design/components/select";
import TextFilter from "@cloudscape-design/components/text-filter";
import Modal from "@cloudscape-design/components/modal";
import Box from "@cloudscape-design/components/box";
import Badge from "@cloudscape-design/components/badge";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Alert from "@cloudscape-design/components/alert";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import CopyToClipboard from "@cloudscape-design/components/copy-to-clipboard";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import Tabs from "@cloudscape-design/components/tabs";
import Toggle from "@cloudscape-design/components/toggle";
import Table from "../components/ResourceTable";
import { useSession } from "../components/SessionContext";
import { runCliCommand } from "../api/client";
import {
  CLI_PRESETS,
  CliExecution,
  CliResponse,
  readCliHistory,
  recordCliExecution,
  clearCliHistory,
  parseCliJson,
  cliArgsText,
} from "./cliResources";

const copy = (text: string, label: string) => (
  <CopyToClipboard
    variant="icon"
    textToCopy={text}
    copyButtonAriaLabel={label}
    copySuccessText="Copied"
    copyErrorText="Unable to copy"
  />
);
function TerminalText({
  text,
  label,
  wrap,
}: {
  text: string;
  label: string;
  wrap: boolean;
}) {
  return (
    <div className="cli-terminal-output">
      <div className="cli-output-toolbar">
        <span>{label}</span>
        {copy(text, `Copy ${label}`)}
      </div>
      <pre
        aria-label={label}
        style={{
          whiteSpace: wrap ? "pre-wrap" : "pre",
          overflowWrap: wrap ? "anywhere" : undefined,
        }}
      >
        {text}
      </pre>
    </div>
  );
}
export const CliConsole: React.FC<{ onNavigate?: (path: string) => void }> = ({
  onNavigate,
}) => {
  const session = useSession();
  const scope = JSON.stringify([
    session.identity?.arn,
    session.identity?.account_id,
    session.identity?.region,
    session.identity?.endpoint,
    session.identity?.expires_at,
    session.details?.session_identity?.access_key_id,
  ]);
  return (
    <CliWorkspace
      key={scope}
      scope={scope}
      session={session}
      onNavigate={onNavigate}
    />
  );
};
function CliWorkspace({
  scope,
  session,
  onNavigate,
}: {
  scope: string;
  session: ReturnType<typeof useSession>;
  onNavigate?: (path: string) => void;
}) {
  const { identity, loading: contextLoading } = session;
  const [command, setCommand] = useState("aws sts get-caller-identity");
  const [history, setHistory] = useState<CliExecution[]>(() =>
    readCliHistory(scope),
  );
  const [selected, setSelected] = useState<CliExecution | null>(
    () => readCliHistory(scope)[0] || null,
  );
  const [running, setRunning] = useState(false);
  const [pending, setPending] = useState<{
    command: string;
    warning: string;
  } | null>(null);
  const [filter, setFilter] = useState("");
  const [wrap, setWrap] = useState(true);
  const [historyIndex, setHistoryIndex] = useState<number | null>(null);
  const runningRef = useRef(false);
  const mounted = useRef(true);
  const inputRef = useRef<TextareaProps.Ref>(null);
  const draft = useRef("");
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const disabled = running || !!pending || contextLoading;
  const setDraft = (value: string) => {
    setCommand(value);
    setHistoryIndex(null);
  };
  const run = async (submittedCommand: string, confirmed = false) => {
    if (runningRef.current || contextLoading || !submittedCommand.trim())
      return;
    runningRef.current = true;
    setRunning(true);
    setSelected(null);
    const entry: CliExecution = {
      id: crypto.randomUUID(),
      command: submittedCommand.trim(),
      submittedAt: new Date().toISOString(),
      account: identity?.account_id || "Unknown",
      principal: identity?.arn || "Unavailable",
      endpoint: identity?.endpoint || "Unavailable",
      region: identity?.region || "Unknown",
    };
    try {
      const response = await runCliCommand(entry.command, confirmed);
      if (response.requires_confirmation) {
        if (mounted.current)
          setPending({
            command: entry.command,
            warning:
              response.message ||
              "This command may remove or stop local resources.",
          });
        return;
      }
      if (typeof response.exit_code !== "number")
        throw new Error(
          "The execution response did not include an exit code. Check the resource before retrying a command that changes it.",
        );
      entry.response = response;
      const next = recordCliExecution(scope, entry);
      if (mounted.current) {
        setHistory(next);
        setSelected(entry);
        setPending(null);
        setHistoryIndex(null);
      }
    } catch (error) {
      entry.error =
        error instanceof Error
          ? error.message
          : "Unable to execute the command.";
      const next = recordCliExecution(scope, entry);
      if (mounted.current) {
        setHistory(next);
        setSelected(entry);
        setPending(null);
      }
    } finally {
      runningRef.current = false;
      if (mounted.current) setRunning(false);
    }
  };
  const onKeyDown = (event: React.KeyboardEvent) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      if (!disabled) void run(command);
      return;
    }
    if (
      disabled ||
      !event.altKey ||
      !["ArrowUp", "ArrowDown"].includes(event.key) ||
      !history.length
    )
      return;
    event.preventDefault();
    if (event.key === "ArrowUp") {
      if (historyIndex === null) draft.current = command;
      const next = Math.min((historyIndex ?? -1) + 1, history.length - 1);
      setHistoryIndex(next);
      setCommand(history[next].command);
    } else if (historyIndex !== null) {
      const next = historyIndex - 1;
      setHistoryIndex(next < 0 ? null : next);
      setCommand(next < 0 ? draft.current : history[next].command);
    }
  };
  const response = selected?.response;
  const parsed =
    response && !response.stdout_truncated
      ? parseCliJson(response.stdout)
      : { valid: false, value: undefined };
  const resultStatus = (item: CliExecution) => (
    <StatusIndicator
      type={item.error || item.response?.exit_code !== 0 ? "error" : "success"}
    >
      {item.error ? "Request failed" : `Exit code ${item.response?.exit_code}`}
    </StatusIndicator>
  );
  const resultOutput = (result: CliResponse) => (
    <SpaceBetween size="m">
      {(result.stdout_truncated || result.stderr_truncated) && (
        <Alert type="warning">
          Output was truncated. Only the last{" "}
          {result.output_limit?.toLocaleString() || "available"} characters of
          each affected stream are shown.
        </Alert>
      )}
      {result.stdout ? (
        parsed.valid ? (
          <Tabs
            tabs={[
              {
                id: "stdout",
                label: "Standard output",
                content: (
                  <TerminalText
                    text={result.stdout}
                    label="stdout"
                    wrap={wrap}
                  />
                ),
              },
              {
                id: "json",
                label: "Formatted JSON",
                content: (
                  <TerminalText
                    text={JSON.stringify(parsed.value, null, 2)}
                    label="JSON output"
                    wrap={wrap}
                  />
                ),
              },
            ]}
          />
        ) : (
          <TerminalText text={result.stdout} label="stdout" wrap={wrap} />
        )
      ) : (
        <Box>No standard output returned.</Box>
      )}
      {result.stderr ? (
        <TerminalText text={result.stderr} label="stderr" wrap={wrap} />
      ) : (
        <Box color="text-status-inactive">No standard error output.</Box>
      )}
    </SpaceBetween>
  );
  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Run one AWS CLI command at a time using the dashboard session. Output appears when execution completes."
        actions={
          onNavigate ? (
            <Button onClick={() => onNavigate("environment")}>
              Manage session
            </Button>
          ) : undefined
        }
      >
        AWS CLI
      </Header>
      <Container header={<Header variant="h2">Session context</Header>}>
        <KeyValuePairs
          columns={3}
          minColumnWidth={220}
          items={[
            {
              label: "Account",
              value: identity?.resolved
                ? identity.account_id
                : contextLoading
                  ? "Loading…"
                  : "Unverified",
            },
            { label: "Region", value: identity?.region || "Unavailable" },
            { label: "Endpoint", value: identity?.endpoint || "Unavailable" },
            {
              label: "Principal ARN",
              value: identity?.resolved ? identity.arn : "Unverified",
            },
            { label: "Session", value: identity?.role || "Unavailable" },
          ]}
        />
      </Container>
      {!contextLoading && !identity?.resolved && (
        <Alert type="warning">
          The caller identity is unverified. Commands still use the dashboard's
          configured session credentials.
        </Alert>
      )}
      <Container header={<Header variant="h2">Command</Header>}>
        <SpaceBetween size="m">
          <FormField label="Command presets">
            <Select
              disabled={disabled}
              filteringType="auto"
              selectedOption={
                CLI_PRESETS.find((item) => item.value === command) || null
              }
              options={CLI_PRESETS}
              placeholder="Find a read-only AWS command"
              onChange={({ detail }) => {
                setDraft(detail.selectedOption.value || "");
                inputRef.current?.focus();
              }}
            />
          </FormField>
          <FormField
            label="AWS CLI command"
            description="Ctrl/Cmd+Enter to run. Alt+Up/Down to recall history. Shell operators and scripts are not supported."
          >
            <div className="cli-command-input" onKeyDown={onKeyDown}>
              <span className="cli-prompt" aria-hidden="true">
                $
              </span>
              <Textarea
                ref={inputRef}
                value={command}
                disabled={disabled}
                rows={3}
                spellcheck={false}
                onChange={({ detail }) => setDraft(detail.value)}
                placeholder="aws sts get-caller-identity"
              />
            </div>
          </FormField>
          <SpaceBetween direction="horizontal" size="xs">
            <Button
              variant="primary"
              iconName="caret-right-filled"
              loading={running}
              disabled={!!pending || contextLoading || !command.trim()}
              onClick={() => run(command)}
            >
              Run command
            </Button>
            <Button
              disabled={running || !selected}
              onClick={() => setSelected(null)}
            >
              Clear output
            </Button>
          </SpaceBetween>
        </SpaceBetween>
      </Container>
      {running && (
        <Container>
          <StatusIndicator type="loading">
            Running command… Commands time out after 30 seconds.
          </StatusIndicator>
        </Container>
      )}
      {selected && (
        <Container
          header={
            <Header
              variant="h2"
              actions={
                <SpaceBetween direction="horizontal" size="s">
                  {resultStatus(selected)}
                  {response?.duration_ms != null && (
                    <Badge color="grey">{response.duration_ms} ms</Badge>
                  )}
                </SpaceBetween>
              }
            >
              Execution result
            </Header>
          }
        >
          <SpaceBetween size="l">
            <TerminalText
              text={selected.command}
              label="Submitted command"
              wrap={wrap}
            />
            {selected.error ? (
              <Alert type="error" header="Command request failed">
                {selected.error}
              </Alert>
            ) : (
              response && (
                <>
                  <Toggle
                    checked={wrap}
                    onChange={({ detail }) => setWrap(detail.checked)}
                  >
                    Wrap output lines
                  </Toggle>
                  {resultOutput(response)}
                </>
              )
            )}
            <ExpandableSection headerText="Execution details">
              <SpaceBetween size="m">
                <KeyValuePairs
                  columns={3}
                  minColumnWidth={220}
                  items={[
                    {
                      label: "Submitted at",
                      value: new Date(selected.submittedAt).toLocaleString(),
                    },
                    {
                      label: "Region used",
                      value: response?.region || selected.region,
                    },
                    {
                      label: "Endpoint used",
                      value: response?.endpoint_url || selected.endpoint,
                    },
                    { label: "Account at submission", value: selected.account },
                    {
                      label: "Principal at submission",
                      value: selected.principal,
                    },
                    {
                      label: "Credential source",
                      value: response?.credential_source || "Unavailable",
                    },
                  ]}
                />
                {response?.executed_args && (
                  <TerminalText
                    text={cliArgsText(response.executed_args)}
                    label="Executed command"
                    wrap={wrap}
                  />
                )}
              </SpaceBetween>
            </ExpandableSection>
          </SpaceBetween>
        </Container>
      )}
      <Table
        trackBy="id"
        header={
          <Header
            variant="h2"
            counter={`(${history.length})`}
            description="The last 20 requests for this session context. History is kept in memory in this app tab and clears on reload."
            actions={
              <Button
                disabled={running || !!pending || !history.length}
                onClick={() => {
                  clearCliHistory(scope);
                  setHistory([]);
                  setSelected(null);
                  setHistoryIndex(null);
                }}
              >
                Clear history
              </Button>
            }
          >
            Command history
          </Header>
        }
        filter={
          <TextFilter
            filteringText={filter}
            filteringAriaLabel="Find command history"
            filteringPlaceholder="Find a command"
            onChange={({ detail }) => setFilter(detail.filteringText)}
          />
        }
        items={history.filter((item) =>
          item.command.toLowerCase().includes(filter.toLowerCase()),
        )}
        columnDefinitions={[
          {
            id: "command",
            minWidth: 280,
            header: "Command",
            cell: (item) => (
              <code className="cli-history-command">{item.command}</code>
            ),
          },
          {
            id: "submittedAt",
            minWidth: 150,
            header: "Submitted at",
            cell: (item) => new Date(item.submittedAt).toLocaleTimeString(),
          },
          { id: "status", header: "Result", cell: resultStatus },
          {
            id: "actions",
            minWidth: 280,
            header: "Actions",
            cell: (item) => (
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  disabled={disabled}
                  onClick={() => {
                    setDraft(item.command);
                    inputRef.current?.focus();
                  }}
                >
                  Load command
                </Button>
                <Button
                  disabled={running || !!pending}
                  onClick={() => setSelected(item)}
                >
                  View output
                </Button>
              </SpaceBetween>
            ),
          },
        ]}
        empty={
          <Box textAlign="center" padding="l">
            {filter
              ? "No matching commands."
              : "No commands run in this session context yet."}
          </Box>
        }
      />
      <Modal
        visible={!!pending}
        onDismiss={() => !running && setPending(null)}
        header="Confirm resource-changing command"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                disabled={running}
                onClick={() => setPending(null)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={running}
                onClick={() => pending && run(pending.command, true)}
              >
                Confirm and run
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="warning">{pending?.warning}</Alert>
          {pending && (
            <TerminalText
              text={pending.command}
              label="Command to confirm"
              wrap
            />
          )}
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
}
