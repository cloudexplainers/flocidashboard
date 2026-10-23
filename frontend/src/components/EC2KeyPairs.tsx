import { useEffect, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Modal from "@cloudscape-design/components/modal";
import RadioGroup from "@cloudscape-design/components/radio-group";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Textarea from "@cloudscape-design/components/textarea";
import Table from "./ResourceTable";
import ConfirmActionModal from "./ConfirmActionModal";
import { FlociNote } from "./FlociNote";
import { serviceRequest } from "../api/client";

export interface KeyPair {
  key_name: string;
  key_pair_id?: string;
  key_type?: string;
  fingerprint?: string;
  public_key?: string;
  create_time?: string;
}

function saveFile(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "application/x-pem-file" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

/** EC2 key pairs: list, create (private key shown once), import a public key, delete. */
export function EC2KeyPairs() {
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const [pairs, setPairs] = useState<KeyPair[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState<KeyPair[]>([]);
  const [mode, setMode] = useState<"create" | "import" | null>(null);
  const [name, setName] = useState("");
  const [keyType, setKeyType] = useState("rsa");
  const [publicKey, setPublicKey] = useState("");
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<{ key_name: string; key_material: string; requested_key_type: string; returned_key_type: string } | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const data = await serviceRequest("ec2", "key-pairs/");
      if (!alive.current) return;
      const next: KeyPair[] = Array.isArray(data.key_pairs) ? data.key_pairs : [];
      setPairs(next);
      setSelected((previous) => next.filter((item) => previous.some((p) => p.key_name === item.key_name)));
      setError("");
    } catch (err: any) {
      if (alive.current) setError(err.message || "Unable to load key pairs");
    } finally {
      if (alive.current) setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const submit = async () => {
    setBusy(true);
    setFormError("");
    try {
      if (mode === "create") {
        const result = await serviceRequest("ec2", "key-pairs/", "POST", { key_name: name.trim(), key_type: keyType });
        setCreated(result);
      } else {
        await serviceRequest("ec2", "key-pairs/import/", "POST", { key_name: name.trim(), public_key_material: publicKey.trim() });
        setNotice(`Key pair "${name.trim()}" imported.`);
      }
      setMode(null);
      await load();
    } catch (err: any) {
      setFormError(err.message || "Request failed");
    } finally {
      setBusy(false);
    }
  };

  const active = selected[0];
  return (
    <SpaceBetween size="m">
      <FlociNote>
        Floci generates real key pairs and puts the public key on instances
        launched with them. It always creates RSA keys: choosing ED25519 still
        returns an RSA private key, and the key type isn&apos;t reported.
      </FlociNote>
      {notice && <Alert type="success" dismissible onDismiss={() => setNotice("")}>{notice}</Alert>}
      {error && <Alert type="error" header="Key pairs unavailable">{error}</Alert>}
      <Table
        loading={loading}
        loadingText="Loading key pairs"
        items={pairs}
        trackBy="key_name"
        selectionType="single"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        header={
          <Header
            variant="h2"
            counter={`(${pairs.length})`}
            description="SSH key pairs for EC2 instances. AWS keeps only the public key."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" ariaLabel="Refresh key pairs" onClick={load} loading={loading} />
                <Button disabled={!active} onClick={() => setDeleteOpen(true)}>Delete</Button>
                <Button onClick={() => { setName(""); setPublicKey(""); setFormError(""); setMode("import"); }}>Import key pair</Button>
                <Button variant="primary" onClick={() => { setName(""); setKeyType("rsa"); setFormError(""); setMode("create"); }}>Create key pair</Button>
              </SpaceBetween>
            }
          >
            Key pairs
          </Header>
        }
        columnDefinitions={[
          { id: "name", header: "Name", cell: (item) => item.key_name },
          { id: "type", header: "Type", cell: (item) => item.key_type || "Not reported" },
          { id: "fingerprint", header: "Fingerprint", cell: (item) => item.fingerprint || "-" },
          { id: "id", header: "ID", cell: (item) => item.key_pair_id || "-" },
          { id: "created", header: "Created", cell: (item) => (item.create_time ? new Date(item.create_time).toLocaleString() : "-") },
        ]}
        empty={<Box textAlign="center">No key pairs. Create one to SSH into instances.</Box>}
      />
      <Modal
        visible={!!mode}
        onDismiss={() => !busy && setMode(null)}
        header={mode === "import" ? "Import key pair" : "Create key pair"}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={busy} onClick={() => setMode(null)}>Cancel</Button>
              <Button variant="primary" loading={busy} disabled={!name.trim() || (mode === "import" && !publicKey.trim())} onClick={submit}>
                {mode === "import" ? "Import key pair" : "Create key pair"}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {formError && <Alert type="error">{formError}</Alert>}
          <FormField label="Name" constraintText="Up to 255 ASCII characters.">
            <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="web-admin" />
          </FormField>
          {mode === "create" ? (
            <FormField label="Key pair type">
              <RadioGroup
                value={keyType}
                onChange={({ detail }) => setKeyType(detail.value)}
                items={[
                  { value: "rsa", label: "RSA", description: "RSA 2048-bit, .pem private key." },
                  { value: "ed25519", label: "ED25519", description: "Not for Windows instances. Floci returns an RSA key instead." },
                ]}
              />
            </FormField>
          ) : (
            <FormField label="Public key" description="An OpenSSH public key, such as the contents of ~/.ssh/id_ed25519.pub.">
              <Textarea rows={4} value={publicKey} onChange={({ detail }) => setPublicKey(detail.value)} placeholder="ssh-ed25519 AAAA... you@example.com" />
            </FormField>
          )}
        </SpaceBetween>
      </Modal>
      <Modal
        visible={!!created}
        onDismiss={() => setCreated(null)}
        header={`Key pair ${created?.key_name || ""} created`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setCreated(null)}>Done</Button>
              <Button variant="primary" iconName="download" onClick={() => created && saveFile(`${created.key_name}.pem`, created.key_material)}>Download .pem file</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="warning">
            This is the only time the private key is shown. Download it now and keep it with
            <code> chmod 400</code>; the console doesn&apos;t store it.
          </Alert>
          {created && created.requested_key_type !== created.returned_key_type && created.returned_key_type && (
            <Alert type="info">You asked for {created.requested_key_type.toUpperCase()}, and Floci returned an {created.returned_key_type.toUpperCase()} key.</Alert>
          )}
          <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 240, overflow: "auto", margin: 0 }}>{created?.key_material}</pre>
        </SpaceBetween>
      </Modal>
      <ConfirmActionModal
        visible={deleteOpen}
        header="Delete key pair"
        confirmLabel="Delete key pair"
        confirmationText={active?.key_name}
        loading={busy}
        onDismiss={() => setDeleteOpen(false)}
        onConfirm={async () => {
          if (!active) return;
          setBusy(true);
          try {
            await serviceRequest("ec2", "key-pairs/", "DELETE", { key_name: active.key_name });
            setNotice(`Key pair "${active.key_name}" deleted.`);
            setSelected([]);
            await load();
          } catch (err: any) {
            setError(err.message || "Unable to delete the key pair");
          } finally {
            setBusy(false);
            setDeleteOpen(false);
          }
        }}
      >
        Instances launched with {active?.key_name} keep its public key, but you can&apos;t launch new ones with it.
      </ConfirmActionModal>
    </SpaceBetween>
  );
}
