import { useEffect, useState } from "react";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Button from "@cloudscape-design/components/button";
import Box from "@cloudscape-design/components/box";
import Alert from "@cloudscape-design/components/alert";
import Modal from "@cloudscape-design/components/modal";
import Tabs from "@cloudscape-design/components/tabs";
import TextFilter from "@cloudscape-design/components/text-filter";
import Table from "./ResourceTable";
import { CodeSnippet } from "./CodeSnippet";
import { clearSesMailbox } from "../api/client";
import { sesDate, sesMailboxMessage, sesRequest } from "../pages/sesResources";

export function SesLocalMailbox() {
  const [messages, setMessages] = useState<
    ReturnType<typeof sesMailboxMessage>[]
  >([]);
  const [selected, setSelected] = useState<ReturnType<
    typeof sesMailboxMessage
  > | null>(null);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    sesRequest("/api/inspector/ses/messages/")
      .then((result) => {
        if (alive) setMessages((result.messages || []).map(sesMailboxMessage));
      })
      .catch((err) => {
        if (alive) setError(err.message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [revision]);
  const visible = messages.filter((item) =>
    `${item.source || ""} ${item.to} ${item.subject || ""} ${item.id || ""}`
      .toLowerCase()
      .includes(filter.toLowerCase()),
  );
  return (
    <SpaceBetween size="l">
      <Alert type="info" header="Floci local test mailbox">
        This mailbox displays messages captured by the local emulator. It is
        separate from the AWS SES mailbox simulator and does not indicate
        external delivery.
      </Alert>
      {error && !confirm && (
        <Alert type="error" header="Mailbox request failed">
          {error}
        </Alert>
      )}
      <Table
        loading={loading}
        loadingText="Loading captured messages"
        trackBy={(item) => item.id || JSON.stringify(item.raw)}
        header={
          <Header
            variant="h2"
            counter={`(${messages.length})`}
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  iconName="refresh"
                  loading={loading}
                  onClick={() => setRevision((value) => value + 1)}
                >
                  Refresh mailbox
                </Button>
                <Button
                  disabled={loading || busy || !messages.length || !!error}
                  onClick={() => setConfirm(true)}
                >
                  Clear local mailbox
                </Button>
              </SpaceBetween>
            }
          >
            Captured messages
          </Header>
        }
        filter={
          <TextFilter
            filteringText={filter}
            filteringAriaLabel="Find captured messages"
            filteringPlaceholder="Find by sender, recipient, subject, or ID"
            onChange={({ detail }) => setFilter(detail.filteringText)}
          />
        }
        items={visible}
        columnDefinitions={[
          {
            id: "subject",
            header: "Subject",
            cell: (item) => (
              <Button variant="inline-link" onClick={() => setSelected(item)}>
                {item.subject || "(No subject)"}
              </Button>
            ),
          },
          {
            id: "source",
            header: "From",
            cell: (item) => item.source || "Unavailable",
          },
          { id: "to", header: "To", cell: (item) => item.to },
          {
            id: "timestamp",
            header: "Captured at",
            cell: (item) => sesDate(item.timestamp),
          },
          {
            id: "id",
            header: "Message ID",
            cell: (item) => item.id || "Unavailable",
          },
        ]}
        empty={
          <Box textAlign="center" padding="l">
            {error
              ? "Messages could not be loaded."
              : filter
                ? "No matching messages."
                : "No captured messages. Send a local test email to inspect it here."}
          </Box>
        }
      />
      <Modal
        visible={!!selected}
        onDismiss={() => setSelected(null)}
        header={selected?.subject || "Captured message"}
        size="large"
        footer={
          <Box float="right">
            <Button variant="primary" onClick={() => setSelected(null)}>
              Close
            </Button>
          </Box>
        }
      >
        {selected && (
          <Tabs
            tabs={[
              {
                id: "text",
                label: "Text body",
                content: (
                  <pre
                    style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                  >
                    {selected.text ?? "Text body not reported."}
                  </pre>
                ),
              },
              {
                id: "html",
                label: "HTML source",
                content: (
                  <pre
                    style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                  >
                    {selected.html ?? "HTML body not reported."}
                  </pre>
                ),
              },
              {
                id: "raw",
                label: "Message data",
                content: (
                  <CodeSnippet
                    language="json"
                    code={JSON.stringify(selected.raw, null, 2)}
                  />
                ),
              },
            ]}
          />
        )}
      </Modal>
      <Modal
        visible={confirm}
        onDismiss={() => !busy && setConfirm(false)}
        header="Clear local mailbox"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                disabled={busy}
                onClick={() => setConfirm(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={busy}
                onClick={async () => {
                  setBusy(true);
                  setError("");
                  try {
                    await clearSesMailbox();
                    setSelected(null);
                    setMessages([]);
                    setConfirm(false);
                    setRevision((value) => value + 1);
                  } catch (err) {
                    setError(
                      err instanceof Error
                        ? err.message
                        : "Unable to clear mailbox.",
                    );
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Clear mailbox
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="warning">
            Permanently remove all {messages.length} captured messages from the
            shared local SES mailbox?
          </Alert>
          {confirm && error && <Alert type="error">{error}</Alert>}
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
}
