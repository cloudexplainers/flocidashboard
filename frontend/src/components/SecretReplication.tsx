import { useEffect, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Checkbox from "@cloudscape-design/components/checkbox";
import Container from "@cloudscape-design/components/container";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Modal from "@cloudscape-design/components/modal";
import Multiselect from "@cloudscape-design/components/multiselect";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "./ResourceTable";
import ConfirmActionModal from "./ConfirmActionModal";
import { FlociNote } from "./FlociNote";
import { serviceRequest } from "../api/client";

export interface Replica {
  region: string;
  status?: string;
  status_message?: string;
  kms_key_id?: string;
}

const REGIONS = [
  "us-east-1", "us-east-2", "us-west-1", "us-west-2", "ca-central-1", "eu-west-1", "eu-west-2", "eu-west-3",
  "eu-central-1", "eu-north-1", "ap-northeast-1", "ap-northeast-2", "ap-southeast-1", "ap-southeast-2", "ap-south-1", "sa-east-1",
];

/** Secrets Manager multi-Region replication: add and remove replica Regions, promote a replica. */
export function SecretReplication({ secretId }: { secretId: string }) {
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const [primary, setPrimary] = useState("");
  const [replicas, setReplicas] = useState<Replica[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [regions, setRegions] = useState<string[]>([]);
  const [overwrite, setOverwrite] = useState(false);
  const [confirm, setConfirm] = useState<{ kind: "remove" | "promote"; region: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const path = `secrets/${encodeURIComponent(secretId)}/replication/`;

  const apply = (data: any) => {
    setPrimary(data.primary_region || "");
    setReplicas(Array.isArray(data.replicas) ? data.replicas : []);
  };
  const load = async () => {
    setLoading(true);
    try {
      const data = await serviceRequest("secretsmanager", path);
      if (!alive.current) return;
      apply(data);
      setError("");
    } catch (err: any) {
      if (alive.current) setError(err.message || "Unable to load replication");
    } finally {
      if (alive.current) setLoading(false);
    }
  };
  useEffect(() => { load(); }, [secretId]);

  const run = async (work: () => Promise<string>) => {
    setBusy(true);
    setError("");
    try {
      setNotice(await work());
    } catch (err: any) {
      setError(err.message || "Request failed");
    } finally {
      setBusy(false);
      setAddOpen(false);
      setConfirm(null);
    }
  };

  const available = REGIONS.filter((region) => region !== primary && !replicas.some((replica) => replica.region === region));

  return (
    <Container
      header={
        <Header
          variant="h2"
          description={primary ? `Primary Region: ${primary}` : "Copies of this secret kept in other Regions."}
          actions={<Button disabled={loading} onClick={() => { setRegions([]); setOverwrite(false); setAddOpen(true); }}>Replicate secret to other Regions</Button>}
        >
          Replicate secret
        </Header>
      }
    >
      <SpaceBetween size="m">
        <FlociNote>
          Floci replicates as AWS does: each replica has the same name and a
          Region-specific ARN, new values in the primary reach it, and it
          refuses changes of its own until you promote it to a standalone
          secret.
        </FlociNote>
        {notice && <Alert type="success" dismissible onDismiss={() => setNotice("")}>{notice}</Alert>}
        {error && <Alert type="error">{error}</Alert>}
        <Table
          variant="embedded"
          loading={loading}
          loadingText="Loading replicas"
          items={replicas}
          trackBy="region"
          columnDefinitions={[
            { id: "region", header: "Region", cell: (item) => item.region },
            { id: "status", header: "Replication status", cell: (item) => <StatusIndicator type={item.status === "InSync" ? "success" : item.status === "Failed" ? "error" : "in-progress"}>{item.status || "Unknown"}</StatusIndicator> },
            { id: "message", header: "Status message", cell: (item) => item.status_message || "-" },
            { id: "kms", header: "Encryption key", cell: (item) => item.kms_key_id || "aws/secretsmanager" },
            {
              id: "actions",
              header: "Actions",
              cell: (item) => (
                <SpaceBetween direction="horizontal" size="xs">
                  <Button variant="inline-link" ariaLabel={`Promote ${item.region}`} onClick={() => setConfirm({ kind: "promote", region: item.region })}>Promote to standalone secret</Button>
                  <Button variant="inline-link" ariaLabel={`Remove ${item.region}`} onClick={() => setConfirm({ kind: "remove", region: item.region })}>Remove</Button>
                </SpaceBetween>
              ),
            },
          ]}
          empty={<Box textAlign="center">This secret isn&apos;t replicated to other Regions.</Box>}
        />
      </SpaceBetween>
      <Modal
        visible={addOpen}
        onDismiss={() => !busy && setAddOpen(false)}
        header="Replicate secret to other Regions"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={busy} onClick={() => setAddOpen(false)}>Cancel</Button>
              <Button
                variant="primary"
                loading={busy}
                disabled={!regions.length}
                onClick={() =>
                  run(async () => {
                    apply(await serviceRequest("secretsmanager", path, "POST", { regions, force_overwrite: overwrite }));
                    return `Replicated to ${regions.join(", ")}.`;
                  })
                }
              >
                Replicate
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Regions" description="Each replica is encrypted with that Region's aws/secretsmanager key.">
            <Multiselect
              selectedOptions={regions.map((region) => ({ label: region, value: region }))}
              options={available.map((region) => ({ label: region, value: region }))}
              placeholder="Choose Regions"
              onChange={({ detail }) => setRegions(detail.selectedOptions.map((option) => option.value as string))}
            />
          </FormField>
          <Checkbox checked={overwrite} onChange={({ detail }) => setOverwrite(detail.checked)} description="Otherwise replication fails where a secret with this name already exists.">
            Overwrite a secret with the same name in those Regions
          </Checkbox>
        </SpaceBetween>
      </Modal>
      <ConfirmActionModal
        visible={!!confirm}
        header={confirm?.kind === "promote" ? "Promote replica" : "Remove replica"}
        confirmLabel={confirm?.kind === "promote" ? "Promote replica" : "Remove replica"}
        loading={busy}
        onDismiss={() => setConfirm(null)}
        onConfirm={() =>
          confirm &&
          run(async () => {
            if (confirm.kind === "promote") {
              await serviceRequest("secretsmanager", `${path}promote/`, "POST", { region: confirm.region });
              await load();
              return `The replica in ${confirm.region} is now a standalone secret.`;
            }
            apply(await serviceRequest("secretsmanager", path, "DELETE", { regions: [confirm.region] }));
            return `Replica in ${confirm.region} removed.`;
          })
        }
      >
        {confirm?.kind === "promote"
          ? `The secret in ${confirm.region} stops receiving updates from ${primary || "the primary Region"} and can be changed on its own.`
          : `The replica secret in ${confirm?.region} is deleted.`}
      </ConfirmActionModal>
    </Container>
  );
}
