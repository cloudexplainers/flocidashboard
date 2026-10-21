import { useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Checkbox from "@cloudscape-design/components/checkbox";
import Container from "@cloudscape-design/components/container";
import CopyToClipboard from "@cloudscape-design/components/copy-to-clipboard";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Tabs from "@cloudscape-design/components/tabs";
import Table from "./ResourceTable";

export function ApiTestResponse({ result }: { result: any }) {
  const [format, setFormat] = useState(false);
  const raw = result.body ?? "";
  let formatted = "";
  let isJson = false;
  try {
    formatted = JSON.stringify(JSON.parse(raw), null, 2);
    isJson = true;
  } catch {
    /* Plain text and empty responses are valid. */
  }
  const status = Number(result.status_code);
  return (
    <Container
      header={
        <Header variant="h2">Response · HTTP {result.status_code}</Header>
      }
    >
      <SpaceBetween size="m">
        <KeyValuePairs
          columns={3}
          items={[
            {
              label: "Status",
              value: (
                <StatusIndicator
                  type={
                    status >= 400 ? "error" : status >= 300 ? "info" : "success"
                  }
                >
                  {result.status_code}
                </StatusIndicator>
              ),
            },
            {
              label: "Duration",
              value:
                result.duration_ms == null
                  ? "Not reported"
                  : `${result.duration_ms} ms`,
            },
            {
              label: "Submitted request",
              value: `${result.submitted?.method || result.method || ""} ${result.submitted?.path || ""}`,
            },
            {
              label: "Stage",
              value: result.submitted?.stage || "Not reported",
            },
            { label: "Request URL", value: result.url || "Not reported" },
          ]}
        />
        {status >= 400 && (
          <Alert type="warning">
            The API returned HTTP {status}. Inspect its response body and
            headers for details.
          </Alert>
        )}
        <Tabs
          tabs={[
            {
              id: "body",
              label: "Response body",
              content: (
                <SpaceBetween size="m">
                  <SpaceBetween direction="horizontal" size="m">
                    <CopyToClipboard
                      textToCopy={raw}
                      copyButtonText="Copy body"
                      copySuccessText="Copied"
                      copyErrorText="Unable to copy"
                    />
                    {isJson && (
                      <Checkbox
                        checked={format}
                        onChange={({ detail }) => setFormat(detail.checked)}
                      >
                        Format JSON
                      </Checkbox>
                    )}
                  </SpaceBetween>
                  {raw === "" ? (
                    <Box color="text-body-secondary">
                      The response body is empty.
                    </Box>
                  ) : (
                    <pre className="gateway-response-body">
                      {format ? formatted : raw}
                    </pre>
                  )}
                </SpaceBetween>
              ),
            },
            {
              id: "headers",
              label: `Response headers (${Object.keys(result.headers || {}).length})`,
              content: (
                <Table
                  items={Object.entries(result.headers || {}).map(
                    ([name, value]) => ({ name, value: String(value) }),
                  )}
                  columnDefinitions={[
                    {
                      id: "name",
                      header: "Header",
                      cell: (item) => item.name,
                      minWidth: 180,
                    },
                    {
                      id: "value",
                      header: "Value",
                      cell: (item) => (
                        <span style={{ overflowWrap: "anywhere" }}>
                          {item.value}
                        </span>
                      ),
                      minWidth: 200,
                    },
                  ]}
                  empty="No response headers reported."
                />
              ),
            },
          ]}
        />
      </SpaceBetween>
    </Container>
  );
}
