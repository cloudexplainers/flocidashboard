import { useEffect, useState } from "react";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Button from "@cloudscape-design/components/button";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Modal from "@cloudscape-design/components/modal";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import CopyToClipboard from "@cloudscape-design/components/copy-to-clipboard";
import Table from "./ResourceTable";
import {
  SsmParameter,
  ssmRequest,
  parameterUrl,
  ssmDate,
} from "../pages/ssmResources";

export function SsmParameterDetails({
  parameter,
  tab,
  disabled,
}: {
  parameter: SsmParameter;
  tab: string;
  disabled: boolean;
}) {
  const [value, setValue] = useState<{ text: string; version?: number } | null>(
    null,
  );
  const [history, setHistory] = useState<any[]>([]);
  const [tags, setTags] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [tagOpen, setTagOpen] = useState(false);
  const [tagKey, setTagKey] = useState("");
  const [tagValue, setTagValue] = useState("");
  const [removeTag, setRemoveTag] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let alive = true;
    setValue(null);
    setError("");
    if (!["history", "tags"].includes(tab)) return;
    setBusy(true);
    ssmRequest(
      `/api/ssm/${tab === "history" ? "history" : "tags"}/?name=${encodeURIComponent(parameter.name)}`,
    )
      .then((result) => {
        if (alive) {
          if (tab === "history") setHistory(result.parameters || []);
          else setTags(result.tags || []);
        }
      })
      .catch((err) => {
        if (alive) setError(err.message);
      })
      .finally(() => {
        if (alive) setBusy(false);
      });
    return () => {
      alive = false;
    };
  }, [parameter.name, tab, reload]);
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Operation failed.");
    } finally {
      setBusy(false);
    }
  };
  const reveal = (version?: number) =>
    run(async () => {
      setValue(null);
      const result = await ssmRequest(
        parameterUrl(
          `${parameter.name}${version == null ? "" : `:${version}`}`,
        ),
      );
      if (typeof result.value !== "string")
        throw new Error("No parameter value was returned.");
      setValue({ text: result.value, version: result.version ?? version });
    });
  const output = value && (
    <Container
      header={
        <Header
          variant="h2"
          actions={<Button onClick={() => setValue(null)}>Hide value</Button>}
        >
          {value.version == null
            ? "Parameter value"
            : `Value · version ${value.version}`}
        </Header>
      }
    >
      <Box>
        <pre
          style={{
            whiteSpace: "pre-wrap",
            overflowWrap: "anywhere",
            margin: 0,
          }}
        >
          {value.text === "" ? "(Empty value)" : value.text}
        </pre>
        <CopyToClipboard
          variant="icon"
          textToCopy={value.text}
          copyButtonAriaLabel="Copy parameter value"
          copySuccessText="Copied"
          copyErrorText="Unable to copy"
        />
      </Box>
    </Container>
  );
  return (
    <SpaceBetween size="l">
      {error && !tagOpen && !removeTag && (
        <Alert type="error" header="Unable to complete parameter request">
          {error}
        </Alert>
      )}
      {tab === "details" && (
        <Container header={<Header variant="h2">Parameter value</Header>}>
          <SpaceBetween size="m">
            <Box>
              {parameter.type === "SecureString"
                ? "The value is encrypted with KMS. Retrieve it explicitly to decrypt and view it."
                : "Retrieve the current value when you need it."}
            </Box>
            <Button loading={busy} disabled={disabled} onClick={() => reveal()}>
              {parameter.type === "SecureString"
                ? "Reveal decrypted value"
                : "Retrieve value"}
            </Button>
          </SpaceBetween>
        </Container>
      )}
      {tab === "history" && (
        <Table
          loading={busy}
          loadingText="Loading version history"
          trackBy={(item) => String(item.Version)}
          header={
            <Header
              variant="h2"
              counter={`(${history.length})`}
              description="Values are retrieved only when you choose a version. SecureString values are decrypted on request."
            >
              Version history
            </Header>
          }
          items={history}
          columnDefinitions={[
            {
              id: "Version",
              header: "Version",
              sortingField: "Version",
              cell: (item) => item.Version ?? "Unavailable",
            },
            {
              id: "LastModifiedDate",
              header: "Last modified",
              cell: (item) => ssmDate(item.LastModifiedDate),
            },
            {
              id: "LastModifiedUser",
              header: "Modified by",
              cell: (item) => item.LastModifiedUser || "Unavailable",
            },
            {
              id: "Labels",
              header: "Labels",
              cell: (item) => item.Labels?.join(", ") || "None",
            },
            {
              id: "actions",
              header: "Actions",
              cell: (item) => (
                <Button
                  disabled={disabled || busy || item.Version == null}
                  onClick={() => reveal(item.Version)}
                >
                  View version {item.Version}
                </Button>
              ),
            },
          ]}
          empty={
            <Box textAlign="center" padding="l">
              {error
                ? "Version history could not be loaded."
                : "No version history returned."}
            </Box>
          }
        />
      )}
      {output}
      {tab === "tags" && (
        <Table
          loading={busy}
          loadingText="Loading tags"
          trackBy="Key"
          header={
            <Header
              variant="h2"
              counter={`(${tags.length})`}
              actions={
                <Button
                  variant="primary"
                  disabled={disabled || busy}
                  onClick={() => setTagOpen(true)}
                >
                  Add tag
                </Button>
              }
            >
              Tags
            </Header>
          }
          items={tags}
          columnDefinitions={[
            { id: "Key", header: "Key", cell: (item) => item.Key },
            { id: "Value", header: "Value", cell: (item) => item.Value },
            {
              id: "actions",
              header: "Actions",
              cell: (item) => (
                <Button
                  disabled={disabled || busy}
                  onClick={() => setRemoveTag(item.Key)}
                >
                  Remove tag
                </Button>
              ),
            },
          ]}
          empty={
            <Box textAlign="center" padding="l">
              {error
                ? "Tags could not be loaded."
                : "No tags associated with this parameter."}
            </Box>
          }
        />
      )}
      <Modal
        visible={tagOpen}
        onDismiss={() => !busy && setTagOpen(false)}
        header="Add parameter tag"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                disabled={busy}
                variant="link"
                onClick={() => setTagOpen(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={busy}
                disabled={disabled || !tagKey.trim()}
                onClick={() =>
                  run(async () => {
                    await ssmRequest("/api/ssm/tags/", "POST", {
                      name: parameter.name,
                      tags: [{ Key: tagKey.trim(), Value: tagValue }],
                    });
                    setTagOpen(false);
                    setTagKey("");
                    setTagValue("");
                    setReload((value) => value + 1);
                  })
                }
              >
                Save tag
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Tag key">
            <Input
              value={tagKey}
              onChange={({ detail }) => setTagKey(detail.value)}
            />
          </FormField>
          <FormField label="Tag value (optional)">
            <Input
              value={tagValue}
              onChange={({ detail }) => setTagValue(detail.value)}
            />
          </FormField>
          {tagOpen && error && <Alert type="error">{error}</Alert>}
        </SpaceBetween>
      </Modal>
      <Modal
        visible={removeTag !== null}
        onDismiss={() => !busy && setRemoveTag(null)}
        header="Remove tag"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                disabled={busy}
                onClick={() => setRemoveTag(null)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={busy}
                disabled={disabled}
                onClick={() =>
                  run(async () => {
                    await ssmRequest("/api/ssm/tags/", "DELETE", {
                      name: parameter.name,
                      tag_keys: [removeTag],
                    });
                    setRemoveTag(null);
                    setReload((value) => value + 1);
                  })
                }
              >
                Remove tag
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box>
            Remove tag "{removeTag}" from {parameter.name}?
          </Box>
          {removeTag !== null && error && <Alert type="error">{error}</Alert>}
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
}
