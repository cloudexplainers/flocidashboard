import { useEffect, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Modal from "@cloudscape-design/components/modal";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "./ResourceTable";
import ConfirmActionModal from "./ConfirmActionModal";
import { FlociNote } from "./FlociNote";
import { serviceRequest } from "../api/client";

export interface MachineVersion { arn: string; version: string; description?: string; creation_date?: string }
export interface MachineAlias { arn: string; name: string; description?: string; routing: { version_arn: string; weight: number }[]; update_date?: string }

const versionOf = (arn = "") => arn.split(":").pop() || arn;
const when = (value?: string) => (value ? new Date(value).toLocaleString() : "-");

type AliasForm = { arn?: string; name: string; description: string; primary: string; secondary: string; secondaryWeight: string };

/** Step Functions versions (immutable snapshots) and aliases that route executions between them. */
export function StepFunctionsAliases({ stateMachineArn }: { stateMachineArn: string }) {
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const [versions, setVersions] = useState<MachineVersion[]>([]);
  const [aliases, setAliases] = useState<MachineAlias[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [publishOpen, setPublishOpen] = useState(false);
  const [publishDescription, setPublishDescription] = useState("");
  const [aliasForm, setAliasForm] = useState<AliasForm | null>(null);
  const [confirm, setConfirm] = useState<{ kind: "version" | "alias"; arn: string; label: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const data = await serviceRequest("stepfunctions", `versions-aliases/?${new URLSearchParams({ state_machine_arn: stateMachineArn })}`);
      if (!alive.current) return;
      setVersions(Array.isArray(data.versions) ? data.versions : []);
      setAliases(Array.isArray(data.aliases) ? data.aliases : []);
      setError("");
    } catch (err: any) {
      if (alive.current) setError(err.message || "Unable to load versions and aliases");
    } finally {
      if (alive.current) setLoading(false);
    }
  };
  useEffect(() => { load(); }, [stateMachineArn]);

  const run = async (work: () => Promise<string>, close: () => void) => {
    setBusy(true);
    setFormError("");
    try {
      const text = await work();
      close();
      setNotice(text);
      await load();
    } catch (err: any) {
      setFormError(err.message || "Request failed");
    } finally {
      setBusy(false);
    }
  };

  const versionOptions = versions.map((item) => ({ label: `Version ${item.version}`, value: item.arn, description: item.description || undefined }));
  const saveAlias = () => {
    if (!aliasForm) return;
    const weight = aliasForm.secondary ? Number(aliasForm.secondaryWeight) : 0;
    const routing = [{ version_arn: aliasForm.primary, weight: 100 - weight }];
    if (aliasForm.secondary) routing.push({ version_arn: aliasForm.secondary, weight });
    run(async () => {
      if (aliasForm.arn) {
        await serviceRequest("stepfunctions", "aliases/", "PUT", { alias_arn: aliasForm.arn, routing, description: aliasForm.description });
        return `Alias "${aliasForm.name}" updated.`;
      }
      await serviceRequest("stepfunctions", "aliases/", "POST", { name: aliasForm.name.trim(), routing, description: aliasForm.description });
      return `Alias "${aliasForm.name.trim()}" created.`;
    }, () => setAliasForm(null));
  };

  return (
    <SpaceBetween size="l">
      <FlociNote>
        Floci splits executions started through an alias between its versions by
        weight, records the version and alias on each execution, and refuses to
        delete a version an alias still routes to.
      </FlociNote>
      {notice && <Alert type="success" dismissible onDismiss={() => setNotice("")}>{notice}</Alert>}
      {error && <Alert type="error">{error}</Alert>}
      <Table
        loading={loading}
        loadingText="Loading versions"
        items={versions}
        trackBy="arn"
        header={
          <Header
            variant="h3"
            counter={`(${versions.length})`}
            description="Read-only snapshots of the definition. Publishing an unchanged definition returns the latest version."
            actions={<Button onClick={() => { setPublishDescription(""); setFormError(""); setPublishOpen(true); }}>Publish version</Button>}
          >
            Versions
          </Header>
        }
        columnDefinitions={[
          { id: "version", header: "Version", cell: (item) => item.version },
          { id: "description", header: "Description", cell: (item) => item.description || "-" },
          { id: "created", header: "Created", cell: (item) => when(item.creation_date) },
          { id: "actions", header: "Actions", cell: (item) => <Button variant="inline-link" ariaLabel={`Delete version ${item.version}`} onClick={() => { setFormError(""); setConfirm({ kind: "version", arn: item.arn, label: `version ${item.version}` }); }}>Delete</Button> },
        ]}
        empty={<Box textAlign="center">No versions. Publish one to point an alias at it.</Box>}
      />
      <Table
        loading={loading}
        loadingText="Loading aliases"
        items={aliases}
        trackBy="arn"
        header={
          <Header
            variant="h3"
            counter={`(${aliases.length})`}
            description="Start executions with an alias ARN to shift traffic between versions."
            actions={
              <Button
                disabled={!versions.length}
                onClick={() => { setFormError(""); setAliasForm({ name: "", description: "", primary: versions[0]?.arn || "", secondary: "", secondaryWeight: "10" }); }}
              >
                Create alias
              </Button>
            }
          >
            Aliases
          </Header>
        }
        columnDefinitions={[
          { id: "name", header: "Alias", cell: (item) => item.name },
          { id: "routing", header: "Routing", cell: (item) => item.routing.map((route) => `Version ${versionOf(route.version_arn)}: ${route.weight}%`).join(", ") },
          { id: "arn", header: "Alias ARN", cell: (item) => item.arn },
          { id: "updated", header: "Updated", cell: (item) => when(item.update_date) },
          {
            id: "actions",
            header: "Actions",
            cell: (item) => (
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  variant="inline-link"
                  ariaLabel={`Edit alias ${item.name}`}
                  onClick={() => {
                    setFormError("");
                    const [first, second] = [...item.routing].sort((a, b) => b.weight - a.weight);
                    setAliasForm({ arn: item.arn, name: item.name, description: item.description || "", primary: first?.version_arn || "", secondary: second?.version_arn || "", secondaryWeight: String(second?.weight ?? 10) });
                  }}
                >
                  Edit
                </Button>
                <Button variant="inline-link" ariaLabel={`Delete alias ${item.name}`} onClick={() => { setFormError(""); setConfirm({ kind: "alias", arn: item.arn, label: `alias ${item.name}` }); }}>Delete</Button>
              </SpaceBetween>
            ),
          },
        ]}
        empty={<Box textAlign="center">No aliases.</Box>}
      />
      <Modal
        visible={publishOpen}
        onDismiss={() => !busy && setPublishOpen(false)}
        header="Publish version"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={busy} onClick={() => setPublishOpen(false)}>Cancel</Button>
              <Button
                variant="primary"
                loading={busy}
                onClick={() =>
                  run(async () => {
                    const result = await serviceRequest("stepfunctions", "state-machine-versions/publish/", "POST", { state_machine_arn: stateMachineArn, description: publishDescription });
                    return `Published version ${versionOf(result.state_machine_version_arn)}.`;
                  }, () => setPublishOpen(false))
                }
              >
                Publish
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {formError && <Alert type="error">{formError}</Alert>}
          <FormField label="Description" description="Optional. Describe what changed in this version.">
            <Input value={publishDescription} onChange={({ detail }) => setPublishDescription(detail.value)} />
          </FormField>
        </SpaceBetween>
      </Modal>
      <Modal
        visible={!!aliasForm}
        onDismiss={() => !busy && setAliasForm(null)}
        header={aliasForm?.arn ? `Edit alias ${aliasForm.name}` : "Create alias"}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={busy} onClick={() => setAliasForm(null)}>Cancel</Button>
              <Button variant="primary" loading={busy} disabled={!aliasForm?.name.trim() || !aliasForm?.primary} onClick={saveAlias}>Save alias</Button>
            </SpaceBetween>
          </Box>
        }
      >
        {aliasForm && (
          <SpaceBetween size="m">
            {formError && <Alert type="error">{formError}</Alert>}
            <FormField label="Alias name">
              <Input value={aliasForm.name} disabled={!!aliasForm.arn} onChange={({ detail }) => setAliasForm({ ...aliasForm, name: detail.value })} placeholder="prod" />
            </FormField>
            <FormField label="Description">
              <Input value={aliasForm.description} onChange={({ detail }) => setAliasForm({ ...aliasForm, description: detail.value })} />
            </FormField>
            <FormField label="Version" description="Receives the rest of the traffic.">
              <Select
                selectedOption={versionOptions.find((option) => option.value === aliasForm.primary) || null}
                options={versionOptions}
                onChange={({ detail }) => setAliasForm({ ...aliasForm, primary: detail.selectedOption.value || "" })}
              />
            </FormField>
            <FormField label="Additional version" description="Optional. Send part of the executions to a second version, for a canary release.">
              <Select
                selectedOption={versionOptions.find((option) => option.value === aliasForm.secondary) || { label: "None", value: "" }}
                options={[{ label: "None", value: "" }, ...versionOptions.filter((option) => option.value !== aliasForm.primary)]}
                onChange={({ detail }) => setAliasForm({ ...aliasForm, secondary: detail.selectedOption.value || "" })}
              />
            </FormField>
            {aliasForm.secondary && (
              <FormField label="Weight of the additional version (%)" constraintText={`Version ${versionOf(aliasForm.primary)} gets ${100 - Number(aliasForm.secondaryWeight || 0)}%.`}>
                <Input type="number" value={aliasForm.secondaryWeight} onChange={({ detail }) => setAliasForm({ ...aliasForm, secondaryWeight: detail.value })} />
              </FormField>
            )}
          </SpaceBetween>
        )}
      </Modal>
      <ConfirmActionModal
        visible={!!confirm}
        header={confirm?.kind === "alias" ? "Delete alias" : "Delete version"}
        confirmLabel="Delete"
        loading={busy}
        onDismiss={() => setConfirm(null)}
        onConfirm={() =>
          confirm &&
          run(async () => {
            if (confirm.kind === "alias") await serviceRequest("stepfunctions", "aliases/", "DELETE", { alias_arn: confirm.arn });
            else await serviceRequest("stepfunctions", "state-machine-versions/delete/", "DELETE", { state_machine_version_arn: confirm.arn });
            return `Deleted ${confirm.label}.`;
          }, () => setConfirm(null))
        }
      >
        {confirm?.kind === "alias" ? "Executions started with this alias ARN will fail." : "A version that an alias routes to can't be deleted."}
        {formError && <Box color="text-status-error">{formError}</Box>}
      </ConfirmActionModal>
    </SpaceBetween>
  );
}
