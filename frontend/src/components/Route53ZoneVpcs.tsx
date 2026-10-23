import { useEffect, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Modal from "@cloudscape-design/components/modal";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "./ResourceTable";
import ConfirmActionModal from "./ConfirmActionModal";
import { FlociNote } from "./FlociNote";
import { serviceRequest } from "../api/client";

export interface VpcChoice {
  vpc_id: string;
  cidr_block?: string;
  name?: string;
  is_default?: boolean;
}

export interface ZoneVpc {
  VPCId: string;
  VPCRegion?: string;
}

export function vpcOption(vpc: VpcChoice) {
  const description = [vpc.cidr_block, vpc.is_default ? "default VPC" : ""].filter(Boolean).join(" · ");
  return { value: vpc.vpc_id, label: vpc.name ? `${vpc.vpc_id} (${vpc.name})` : vpc.vpc_id, description };
}

/** Loads the VPCs in the dashboard's Region, for private hosted zones. */
export function useVpcChoices(enabled: boolean) {
  const [state, setState] = useState<{ region: string; vpcs: VpcChoice[]; error: string; loading: boolean }>({ region: "", vpcs: [], error: "", loading: false });
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    setState((previous) => ({ ...previous, loading: true, error: "" }));
    serviceRequest("route53", "vpcs/")
      .then((data: any) => {
        if (alive) setState({ region: data?.region || "", vpcs: Array.isArray(data?.vpcs) ? data.vpcs : [], error: "", loading: false });
      })
      .catch((err: any) => {
        if (alive) setState({ region: "", vpcs: [], error: err.message || "Unable to load VPCs", loading: false });
      });
    return () => { alive = false; };
  }, [enabled]);
  return state;
}

/** VPC associations of a private hosted zone: associate another VPC, disassociate one. */
export function Route53ZoneVpcs({ zoneId, vpcs, onChanged }: { zoneId: string; vpcs: ZoneVpc[]; onChanged: () => Promise<void> | void }) {
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const [associateOpen, setAssociateOpen] = useState(false);
  const [vpcId, setVpcId] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [removing, setRemoving] = useState<ZoneVpc | null>(null);
  const choices = useVpcChoices(associateOpen);
  const available = choices.vpcs.filter((vpc) => !vpcs.some((item) => item.VPCId === vpc.vpc_id));
  const zonePath = `hosted-zones/${encodeURIComponent(zoneId)}/vpcs/`;

  const associate = async () => {
    setBusy(true);
    setFormError("");
    try {
      await serviceRequest("route53", zonePath, "POST", { vpc_id: vpcId, vpc_region: choices.region });
      if (!alive.current) return;
      setNotice(`${vpcId} associated. Resources in it can now resolve the zone's records.`);
      setAssociateOpen(false);
      await onChanged();
    } catch (err: any) {
      if (alive.current) setFormError(err.message || "Unable to associate the VPC");
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  return (
    <SpaceBetween size="m">
      <FlociNote>
        Floci records which VPCs are associated, but its DNS server answers the
        zone&apos;s A records to any client, whether or not it is in one of these
        VPCs. Floci also lets the last VPC go; the console keeps at least one,
        as AWS does.
      </FlociNote>
      {notice && <Alert type="success" dismissible onDismiss={() => setNotice("")}>{notice}</Alert>}
      {error && <Alert type="error" dismissible onDismiss={() => setError("")}>{error}</Alert>}
      <Table
        items={vpcs}
        trackBy="VPCId"
        header={
          <Header
            variant="h3"
            counter={`(${vpcs.length})`}
            description="Only DNS queries from these VPCs resolve this private zone."
            actions={<Button variant="primary" onClick={() => { setVpcId(""); setFormError(""); setAssociateOpen(true); }}>Associate VPC</Button>}
          >
            Associated VPCs
          </Header>
        }
        columnDefinitions={[
          { id: "vpc", header: "VPC ID", cell: (item) => <code>{item.VPCId}</code> },
          { id: "region", header: "Region", cell: (item) => item.VPCRegion || "Not reported" },
          {
            id: "actions",
            header: "Actions",
            cell: (item) => (
              <Button
                disabled={vpcs.length <= 1}
                ariaLabel={`Disassociate ${item.VPCId}`}
                onClick={() => setRemoving(item)}
              >
                Disassociate
              </Button>
            ),
          },
        ]}
        empty={<Box textAlign="center">No VPCs are associated.</Box>}
      />
      {vpcs.length === 1 && <Box variant="small">A private hosted zone keeps at least one VPC. Associate another VPC before you disassociate this one.</Box>}
      <Modal
        visible={associateOpen}
        onDismiss={() => !busy && setAssociateOpen(false)}
        header="Associate VPC with hosted zone"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={busy} onClick={() => setAssociateOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={busy} disabled={!vpcId} onClick={associate}>Associate VPC</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {formError && <Alert type="error">{formError}</Alert>}
          {choices.error && <Alert type="error">{choices.error}</Alert>}
          <FormField label="VPC" description={choices.region ? `VPCs in ${choices.region} that aren't associated yet.` : undefined}>
            <Select
              ariaLabel="VPC to associate"
              statusType={choices.loading ? "loading" : "finished"}
              placeholder="Choose a VPC"
              empty="No other VPCs in this Region"
              selectedOption={available.map(vpcOption).find((option) => option.value === vpcId) || null}
              options={available.map(vpcOption)}
              onChange={({ detail }) => setVpcId(detail.selectedOption.value || "")}
            />
          </FormField>
        </SpaceBetween>
      </Modal>
      <ConfirmActionModal
        visible={!!removing}
        header="Disassociate VPC"
        confirmLabel="Disassociate"
        loading={busy}
        onDismiss={() => setRemoving(null)}
        onConfirm={async () => {
          if (!removing) return;
          setBusy(true);
          try {
            await serviceRequest("route53", zonePath, "DELETE", { vpc_id: removing.VPCId, vpc_region: removing.VPCRegion || "" });
            if (!alive.current) return;
            setNotice(`${removing.VPCId} disassociated.`);
            await onChanged();
          } catch (err: any) {
            if (alive.current) setError(err.message || "Unable to disassociate the VPC");
          } finally {
            if (alive.current) { setBusy(false); setRemoving(null); }
          }
        }}
      >
        Disassociate <strong>{removing?.VPCId}</strong>? Resources in that VPC stop resolving this zone&apos;s records.
      </ConfirmActionModal>
    </SpaceBetween>
  );
}
