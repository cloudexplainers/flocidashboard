import { useEffect, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Modal from "@cloudscape-design/components/modal";
import RadioGroup from "@cloudscape-design/components/radio-group";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "./ResourceTable";
import ConfirmActionModal from "./ConfirmActionModal";
import { FlociNote } from "./FlociNote";
import { serviceRequest } from "../api/client";

export interface OriginAccessControl {
  Id: string;
  Name?: string;
  Description?: string;
  SigningProtocol?: string;
  SigningBehavior?: string;
  OriginAccessControlOriginType?: string;
  UsedBy?: string[];
}

const ORIGIN_TYPES = [
  { value: "s3", label: "S3" },
  { value: "mediastore", label: "MediaStore" },
  { value: "lambda", label: "Lambda function URL" },
  { value: "mediapackagev2", label: "MediaPackage V2" },
];

const SIGNING_BEHAVIORS = [
  { value: "always", label: "Sign requests (recommended)", description: "CloudFront signs every origin request and replaces any Authorization header the viewer sent." },
  { value: "no-override", label: "Do not override authorization header", description: "Signs only when the viewer request has no Authorization header." },
  { value: "never", label: "Do not sign requests", description: "Turns origin access off for this control." },
];

/** S3 bucket policy statement that lets one distribution read the bucket through OAC. */
export function bucketPolicyFor(bucket: string, distributionArn: string) {
  return JSON.stringify({
    Version: "2012-10-17",
    Statement: [{
      Sid: "AllowCloudFrontServicePrincipalReadOnly",
      Effect: "Allow",
      Principal: { Service: "cloudfront.amazonaws.com" },
      Action: "s3:GetObject",
      Resource: `arn:aws:s3:::${bucket || "BUCKET"}/*`,
      Condition: { StringEquals: { "AWS:SourceArn": distributionArn || "arn:aws:cloudfront::ACCOUNT_ID:distribution/DISTRIBUTION_ID" } },
    }],
  }, null, 2);
}

/** CloudFront origin access controls: list (with the distributions using each), create, delete. */
export function CloudFrontOriginAccessControls() {
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const [controls, setControls] = useState<OriginAccessControl[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState<OriginAccessControl[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [originType, setOriginType] = useState("s3");
  const [signing, setSigning] = useState("always");
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const data = await serviceRequest("cloudfront", "origin-access-controls/");
      if (!alive.current) return;
      const next: OriginAccessControl[] = Array.isArray(data.origin_access_controls) ? data.origin_access_controls : [];
      setControls(next);
      setSelected((previous) => next.filter((item) => previous.some((p) => p.Id === item.Id)));
      setError("");
    } catch (err: any) {
      if (alive.current) setError(err.message || "Unable to load origin access controls");
    } finally {
      if (alive.current) setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const create = async () => {
    setBusy(true);
    setFormError("");
    try {
      await serviceRequest("cloudfront", "origin-access-controls/", "POST", {
        name: name.trim(),
        description: description.trim(),
        origin_type: originType,
        signing_behavior: signing,
      });
      setNotice(`Origin access control "${name.trim()}" created. Choose it when you create a distribution with an S3 origin.`);
      setCreateOpen(false);
      await load();
    } catch (err: any) {
      setFormError(err.message || "Unable to create the origin access control");
    } finally {
      setBusy(false);
    }
  };

  const active = selected[0];
  const inUse = (active?.UsedBy || []).length > 0;
  return (
    <SpaceBetween size="m">
      <FlociNote>
        Floci stores origin access controls, keeps them on distributions, and
        refuses to delete one that a distribution still uses. It can&apos;t edit
        one (UpdateOriginAccessControl isn&apos;t supported), so create a new
        control to change settings. Floci doesn&apos;t check the bucket policy
        against the distribution, so confirm the policy below yourself.
      </FlociNote>
      {notice && <Alert type="success" dismissible onDismiss={() => setNotice("")}>{notice}</Alert>}
      {error && <Alert type="error" header="Origin access controls unavailable">{error}</Alert>}
      <Table
        loading={loading}
        loadingText="Loading origin access controls"
        items={controls}
        trackBy="Id"
        selectionType="single"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        header={
          <Header
            variant="h2"
            counter={`(${controls.length})`}
            description="Let a distribution read a private S3 bucket. Replaces origin access identities (OAI)."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" ariaLabel="Refresh origin access controls" onClick={load} loading={loading} />
                <Button disabled={!active} onClick={() => setDeleteOpen(true)}>Delete</Button>
                <Button variant="primary" onClick={() => { setName(""); setDescription(""); setOriginType("s3"); setSigning("always"); setFormError(""); setCreateOpen(true); }}>Create control setting</Button>
              </SpaceBetween>
            }
          >
            Origin access control
          </Header>
        }
        columnDefinitions={[
          { id: "name", header: "Name", cell: (item) => <strong>{item.Name || item.Id}</strong> },
          { id: "id", header: "ID", cell: (item) => <code>{item.Id}</code> },
          { id: "type", header: "Origin type", cell: (item) => item.OriginAccessControlOriginType || "Not reported" },
          { id: "signing", header: "Signing behavior", cell: (item) => item.SigningBehavior || "Not reported" },
          { id: "used", header: "Used by", cell: (item) => (item.UsedBy || []).length ? (item.UsedBy || []).join(", ") : "-" },
        ]}
        empty={<Box textAlign="center">No origin access controls.</Box>}
      />
      {active && (active.OriginAccessControlOriginType || "s3") === "s3" && (
        <FormField label="Bucket policy for an S3 origin" description="Add this to the bucket policy so only the distribution can read objects. Replace the bucket name and distribution ARN.">
          <pre data-testid="oac-bucket-policy" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{bucketPolicyFor("", "")}</pre>
        </FormField>
      )}
      <Modal
        visible={createOpen}
        onDismiss={() => !busy && setCreateOpen(false)}
        header="Create origin access control"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={busy} onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={busy} disabled={!name.trim()} onClick={create}>Create</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {formError && <Alert type="error">{formError}</Alert>}
          <FormField label="Name" constraintText="Up to 64 characters, unique in the account.">
            <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="assets-bucket-oac" />
          </FormField>
          <FormField label="Description - optional">
            <Input value={description} onChange={({ detail }) => setDescription(detail.value)} />
          </FormField>
          <FormField label="Origin type">
            <Select
              selectedOption={ORIGIN_TYPES.find((option) => option.value === originType) || ORIGIN_TYPES[0]}
              options={ORIGIN_TYPES}
              onChange={({ detail }) => setOriginType(detail.selectedOption.value!)}
            />
          </FormField>
          <FormField label="Signing behavior" description="Signing protocol is SigV4.">
            <RadioGroup value={signing} onChange={({ detail }) => setSigning(detail.value)} items={SIGNING_BEHAVIORS} />
          </FormField>
        </SpaceBetween>
      </Modal>
      <ConfirmActionModal
        visible={deleteOpen}
        header="Delete origin access control"
        confirmLabel="Delete"
        confirmationText={active?.Name || active?.Id}
        loading={busy}
        onDismiss={() => setDeleteOpen(false)}
        onConfirm={async () => {
          if (!active) return;
          setBusy(true);
          try {
            await serviceRequest("cloudfront", `origin-access-controls/${encodeURIComponent(active.Id)}/`, "DELETE");
            setNotice(`Origin access control "${active.Name || active.Id}" deleted.`);
            setSelected([]);
            await load();
          } catch (err: any) {
            setError(err.message || "Unable to delete the origin access control");
          } finally {
            setBusy(false);
            setDeleteOpen(false);
          }
        }}
      >
        {inUse
          ? `Distributions ${(active?.UsedBy || []).join(", ")} still use this control. Remove it from their origins first, or the delete is refused.`
          : "No distribution uses this control."}
      </ConfirmActionModal>
    </SpaceBetween>
  );
}
