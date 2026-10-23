import { useSession } from "../components/SessionContext";
import { RdsDatabaseDetails } from "../components/RdsDatabaseDetails";
import { RdsClusterDetails } from "../components/RdsClusterDetails";
import {
  rdsInventory,
  rdsStatus,
  rdsStatusLabel,
  rdsEngineLabel,
  rdsEndpoint,
  rdsTime,
  RdsInstance,
  RdsSnapshot,
  RdsClusterSnapshot,
  clusterWriter,
  RdsCluster,
  RdsParameterGroup,
} from "./rdsResources";
import React, { useState, useEffect, useRef } from "react";
import Header from "@cloudscape-design/components/header";
import Table from "../components/ResourceTable";
import Button from "@cloudscape-design/components/button";
import ButtonDropdown from "@cloudscape-design/components/button-dropdown";
import RadioGroup from "@cloudscape-design/components/radio-group";
import SpaceBetween from "@cloudscape-design/components/space-between";
import TextFilter from "@cloudscape-design/components/text-filter";
import Tabs from "@cloudscape-design/components/tabs";
import Modal from "@cloudscape-design/components/modal";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Select from "@cloudscape-design/components/select";
import Checkbox from "@cloudscape-design/components/checkbox";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Box from "@cloudscape-design/components/box";
import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import {
  createRdsInstance,
  modifyRdsInstance,
  rebootRdsInstance,
  deleteRdsInstance,
  createRdsCluster,
  deleteRdsCluster,
  createRdsParameterGroup,
  deleteRdsParameterGroup,
  createRdsSnapshot,
  deleteRdsSnapshot,
  restoreRdsSnapshot,
  restoreRdsToPointInTime,
  createRdsReadReplica,
  promoteRdsReadReplica,
  addRdsClusterInstance,
  failoverRdsCluster,
  createRdsClusterSnapshot,
  deleteRdsClusterSnapshot,
  restoreRdsClusterSnapshot,
} from "../api/client";
import ConfirmActionModal from "../components/ConfirmActionModal";
import { FlociNote } from '../components/FlociNote';

type DBInstanceItem = RdsInstance;
type DBClusterItem = RdsCluster;
type ParameterGroupItem = RdsParameterGroup;

const ENGINE_OPTIONS = [
  { label: "PostgreSQL", value: "postgres", description: "Port 5432" },
  { label: "MySQL", value: "mysql", description: "Port 3306" },
  { label: "MariaDB", value: "mariadb", description: "Port 3306" },
  {
    label: "Aurora PostgreSQL",
    value: "aurora-postgresql",
    description: "Clustered PostgreSQL",
  },
  {
    label: "Aurora MySQL",
    value: "aurora-mysql",
    description: "Clustered MySQL",
  },
];

type Props = { resourcePath?: string; onNavigate?: (path: string) => void };
const dbPath = (name: string, tab = "overview") =>
  `rds/instances?${new URLSearchParams({ db: name, tab })}`;
const clusterPath = (name: string, tab = "overview") =>
  `rds/clusters?${new URLSearchParams({ cluster: name, tab })}`;
/** Success message for a delete, warning when the requested final snapshot was not created. */
function deletionAlert(message: string, result: any): { type: "success" | "warning"; message: string } {
  const snapshot = result?.final_snapshot;
  if (snapshot && snapshot.found == null && snapshot.error)
    return {
      type: "warning",
      message: `${message} The final snapshot "${snapshot.identifier}" could not be checked: ${snapshot.error}`,
    };
  if (snapshot && !snapshot.found)
    return {
      type: "warning",
      message: `${message} Floci accepted the final snapshot request but did not create snapshot "${snapshot.identifier}", so the data is not kept. In AWS the snapshot would appear under Snapshots.`,
    };
  return { type: "success", message: snapshot ? `${message} Final snapshot "${snapshot.identifier}" is being created.` : message };
}

export const RDSConsole: React.FC<Props> = (props) => {
  const { identity, details, loading } = useSession();
  if (!identity && loading)
    return (
      <StatusIndicator type="loading">Loading session context</StatusIndicator>
    );
  const query = new URLSearchParams(props.resourcePath?.split("?")[1] || "");
  return (
    <RdsWorkspace
      key={JSON.stringify([
        query.get("db"),
        query.get("cluster"),
        identity?.arn,
        identity?.account_id,
        identity?.region,
        identity?.endpoint,
        identity?.expires_at,
        details?.session_identity?.access_key_id,
      ])}
      {...props}
    />
  );
};
const RdsWorkspace: React.FC<Props> = ({
  resourcePath = "rds",
  onNavigate,
}) => {
  const query = new URLSearchParams(resourcePath.split("?")[1] || "");
  const requestedDb = query.get("db"),
    requestedCluster = query.get("cluster");
  const requested = requestedDb || requestedCluster;
  const detailTab = query.get("tab") || "overview";
  const topTabId = resourcePath.split("?")[0].split("/")[1] || "instances";
  const setTopTabId = (tab: string) => onNavigate?.(`rds/${tab}`);
  const alive = useRef(true),
    generation = useRef(0),
    mutationLock = useRef(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      generation.current++;
    };
  }, []);
  const [loadError, setLoadError] = useState("");
  const [issues, setIssues] = useState<string[]>([]);
  const [applyImmediately, setApplyImmediately] = useState(true);
  const [skipFinalSnapshot, setSkipFinalSnapshot] = useState(true);
  const [finalSnapshotId, setFinalSnapshotId] = useState("");
  const [deleteConfirmText, setDeleteConfirmText] = useState("");
  const [deleteResource, setDeleteResource] = useState<{
    kind: "cluster" | "group";
    name: string;
  } | null>(null);
  const [resourceDeleting, setResourceDeleting] = useState(false);
  const [instances, setInstances] = useState<DBInstanceItem[]>([]);
  const [clusters, setClusters] = useState<DBClusterItem[]>([]);
  const [paramGroups, setParamGroups] = useState<ParameterGroupItem[]>([]);
  const [snapshots, setSnapshots] = useState<RdsSnapshot[]>([]);
  const [selectedSnapshots, setSelectedSnapshots] = useState<RdsSnapshot[]>([]);
  const [takeSnapshotOpen, setTakeSnapshotOpen] = useState(false);
  const [newSnapshotId, setNewSnapshotId] = useState("");
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [restoreDbId, setRestoreDbId] = useState("");
  const [restoreClass, setRestoreClass] = useState("");
  const [deleteSnapshotOpen, setDeleteSnapshotOpen] = useState(false);
  const [snapshotBusy, setSnapshotBusy] = useState(false);
  // Resilience: read replicas, point-in-time restore, cluster failover and cluster snapshots
  const [replicaOpen, setReplicaOpen] = useState(false);
  const [replicaId, setReplicaId] = useState("");
  const [replicaClass, setReplicaClass] = useState("");
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [promoteRetention, setPromoteRetention] = useState("1");
  const [pitrOpen, setPitrOpen] = useState(false);
  const [pitrTarget, setPitrTarget] = useState("");
  const [pitrMode, setPitrMode] = useState<"latest" | "custom">("latest");
  const [pitrTime, setPitrTime] = useState("");
  const [pitrClass, setPitrClass] = useState("");
  const [addInstanceOpen, setAddInstanceOpen] = useState(false);
  const [addInstanceId, setAddInstanceId] = useState("");
  const [addInstanceClass, setAddInstanceClass] = useState("db.r6g.large");
  const [failoverOpen, setFailoverOpen] = useState(false);
  const [failoverTarget, setFailoverTarget] = useState("");
  const [clusterSnapshotOpen, setClusterSnapshotOpen] = useState(false);
  const [newClusterSnapshotId, setNewClusterSnapshotId] = useState("");
  const [clusterSnapshots, setClusterSnapshots] = useState<RdsClusterSnapshot[]>([]);
  const [selectedClusterSnapshots, setSelectedClusterSnapshots] = useState<RdsClusterSnapshot[]>([]);
  const [restoreClusterOpen, setRestoreClusterOpen] = useState(false);
  const [restoreClusterId, setRestoreClusterId] = useState("");
  const [deleteClusterSnapshotOpen, setDeleteClusterSnapshotOpen] = useState(false);
  const [loading, setLoading] = useState(true);

  // Selected items
  const [selectedInstances, setSelectedInstances] = useState<DBInstanceItem[]>(
    [],
  );
  const [selectedClusters, setSelectedClusters] = useState<DBClusterItem[]>([]);
  const [selectedParamGroups, setSelectedParamGroups] = useState<
    ParameterGroupItem[]
  >([]);
  const [filterText, setFilterText] = useState("");

  // Modals
  const [createInstanceOpen, setCreateInstanceOpen] = useState(false);
  const [modifyInstanceOpen, setModifyInstanceOpen] = useState(false);
  const [rebootInstanceOpen, setRebootInstanceOpen] = useState(false);
  const [deleteInstanceOpen, setDeleteInstanceOpen] = useState(false);
  const [createClusterOpen, setCreateClusterOpen] = useState(false);
  const [createParamGroupOpen, setCreateParamGroupOpen] = useState(false);

  // Create Instance form state
  const [newDbId, setNewDbId] = useState("");
  const [newEngine, setNewEngine] = useState(ENGINE_OPTIONS[0]);
  const [newDbClass, setNewDbClass] = useState("db.t3.micro");
  const [newStorage, setNewStorage] = useState("20");
  const [newUsername, setNewUsername] = useState("postgres");
  const [newPassword, setNewPassword] = useState("");
  const [newDbName, setNewDbName] = useState("");
  const [newEnableIam, setNewEnableIam] = useState(false);
  const [submittingInstance, setSubmittingInstance] = useState(false);

  // Modify Instance form state
  const [modifyClass, setModifyClass] = useState("");
  const [modifyStorage, setModifyStorage] = useState("");
  const [modifyPassword, setModifyPassword] = useState("");
  const [submittingModify, setSubmittingModify] = useState(false);

  // Create Cluster form state
  const [clusterId, setClusterId] = useState("");
  const [clusterEngine, setClusterEngine] = useState(ENGINE_OPTIONS[3]);
  const [clusterUsername, setClusterUsername] = useState("postgres");
  const [clusterPassword, setClusterPassword] = useState("");
  const [clusterDbName, setClusterDbName] = useState("");
  const [submittingCluster, setSubmittingCluster] = useState(false);

  // Create Parameter Group form state
  const [pgName, setPgName] = useState("");
  const [pgFamily, setPgFamily] = useState("postgres15");
  const [pgDesc, setPgDesc] = useState("Custom parameter group");
  const [submittingPg, setSubmittingPg] = useState(false);

  // Action status / alerts
  const [actionAlert, setActionAlert] = useState<{
    type: "success" | "error" | "info" | "warning";
    message: string;
  } | null>(null);
  const [rebooting, setRebooting] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const activeInstance = requestedDb
    ? instances.find(
        (db) => db.name === requestedDb || db.arn === requestedDb,
      ) || null
    : null;
  const activeCluster = requestedCluster
    ? clusters.find(
        (cluster) =>
          cluster.name === requestedCluster || cluster.arn === requestedCluster,
      ) || null
    : null;
  const prepareModify = () => {
    setModifyClass("");
    setModifyStorage("");
    setModifyPassword("");
    setApplyImmediately(true);
    setActionAlert(null);
    setModifyInstanceOpen(true);
  };
  const prepareDeleteInstance = () => {
    // The console creates a final snapshot by default, named after the instance.
    setSkipFinalSnapshot(false);
    setFinalSnapshotId(activeInstance ? `${activeInstance.name}-final-snapshot` : "");
    setDeleteConfirmText("");
    setActionAlert(null);
    setDeleteInstanceOpen(true);
  };
  const prepareDeleteResource = (kind: "cluster" | "group", name: string) => {
    setSkipFinalSnapshot(kind !== "cluster");
    setFinalSnapshotId(kind === "cluster" ? `${name}-final-snapshot` : "");
    setDeleteConfirmText("");
    setActionAlert(null);
    setDeleteResource({ kind, name });
  };
  const snapshotFields = (
    <SpaceBetween size="m">
      <Checkbox
        checked={!skipFinalSnapshot}
        onChange={({ detail }) => setSkipFinalSnapshot(!detail.checked)}
        description="Creates a manual DB snapshot of all data before deletion. Without it, the data can't be recovered."
      >
        Create final snapshot
      </Checkbox>
      {!skipFinalSnapshot && (
        <FormField label="Final snapshot name">
          <Input
            value={finalSnapshotId}
            onChange={({ detail }) => setFinalSnapshotId(detail.value)}
          />
        </FormField>
      )}
      <FormField label={<>To confirm deletion, type <i>delete me</i> into the field.</>}>
        <Input
          ariaLabel="Confirmation text"
          value={deleteConfirmText}
          placeholder="delete me"
          onChange={({ detail }) => setDeleteConfirmText(detail.value)}
        />
      </FormField>
    </SpaceBetween>
  );

  const loadData = async () => {
    const current = ++generation.current;
    setLoading(true);
    setLoadError("");
    try {
      const response = await fetch("/api/rds/");
      const payload = await response.json();
      if (!response.ok || payload.error)
        throw new Error(payload.error || "Unable to load RDS inventory.");
      if (!alive.current || current !== generation.current) return;
      const inventory = rdsInventory(payload);
      setInstances(inventory.instances);
      setClusters(inventory.clusters);
      setParamGroups(inventory.groups);
      setSnapshots(inventory.snapshots);
      setSelectedSnapshots((selected) =>
        inventory.snapshots.filter((s) => s.name === selected[0]?.name),
      );
      setClusterSnapshots(inventory.clusterSnapshots);
      setSelectedClusterSnapshots((selected) =>
        inventory.clusterSnapshots.filter((s) => s.name === selected[0]?.name),
      );
      setIssues(Array.isArray(payload.issues) ? payload.issues : []);
    } catch (err: any) {
      if (!alive.current || current !== generation.current) return;
      setInstances([]);
      setClusters([]);
      setParamGroups([]);
      setSnapshots([]);
      setClusterSnapshots([]);
      setIssues([]);
      setLoadError(err.message || "Failed to load RDS databases.");
    } finally {
      if (alive.current && current === generation.current) setLoading(false);
    }
  };
  useEffect(() => {
    void loadData();
  }, []);

  const runSnapshotAction = async (work: () => Promise<string>, close: () => void) => {
    if (mutationLock.current) return;
    mutationLock.current = true;
    setSnapshotBusy(true);
    setActionAlert(null);
    try {
      const message = await work();
      close();
      setActionAlert({ type: "success", message });
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: "error", message: err.message || "RDS operation failed" });
      close();
    } finally {
      mutationLock.current = false;
      setSnapshotBusy(false);
    }
  };
  const handleTakeSnapshot = () => {
    if (!activeInstance || !newSnapshotId.trim()) return;
    const name = newSnapshotId.trim();
    void runSnapshotAction(async () => {
      const result = await createRdsSnapshot(activeInstance.name, name);
      const status = result?.snapshot?.Status;
      return `Snapshot "${name}" of ${activeInstance.name} ${status === "available" ? "is available" : `reported status ${status || "not reported"}`}. Find it under Snapshots.`;
    }, () => setTakeSnapshotOpen(false));
  };
  const selectedSnapshot = selectedSnapshots[0];
  const handleRestoreSnapshot = () => {
    if (!selectedSnapshot || !restoreDbId.trim()) return;
    const name = restoreDbId.trim();
    void runSnapshotAction(async () => {
      const result = await restoreRdsSnapshot(selectedSnapshot.name, name, restoreClass.trim());
      return `Restoring snapshot "${selectedSnapshot.name}" to new DB instance ${name} (status ${result?.instance?.DBInstanceStatus || "not reported"}).`;
    }, () => setRestoreOpen(false));
  };
  const handleDeleteSnapshot = () => {
    if (!selectedSnapshot) return;
    const name = selectedSnapshot.name;
    void runSnapshotAction(async () => {
      await deleteRdsSnapshot(name);
      setSelectedSnapshots([]);
      return `Snapshot "${name}" deleted.`;
    }, () => setDeleteSnapshotOpen(false));
  };

  const isPitrTimeValid = (value: string) =>
    !Number.isNaN(new Date(value.trim()).valueOf()) && value.trim() !== "";
  const handleRestorePointInTime = () => {
    if (!activeInstance || !pitrTarget.trim()) return;
    const source = activeInstance.name;
    const target = pitrTarget.trim();
    const time = pitrMode === "custom" ? new Date(pitrTime.trim()).toISOString() : "";
    void runSnapshotAction(async () => {
      const result = await restoreRdsToPointInTime(source, target, time, pitrClass.trim());
      return `Restoring ${source} ${time ? `to ${time}` : "to its latest restorable time"} as new DB instance ${target} (status ${result?.instance?.DBInstanceStatus || "not reported"}).`;
    }, () => setPitrOpen(false));
  };
  const handleCreateReplica = () => {
    if (!activeInstance || !replicaId.trim()) return;
    const source = activeInstance.name;
    const name = replicaId.trim();
    void runSnapshotAction(async () => {
      const result = await createRdsReadReplica(source, name, replicaClass.trim());
      return `Creating read replica ${name} of ${source} (status ${result?.instance?.DBInstanceStatus || "not reported"}).`;
    }, () => setReplicaOpen(false));
  };
  const handlePromote = () => {
    if (!activeInstance) return;
    const name = activeInstance.name;
    const retention = promoteRetention.trim();
    void runSnapshotAction(async () => {
      if (retention && (!/^\d+$/.test(retention) || Number(retention) > 35))
        throw new Error("Backup retention period must be a whole number of days from 0 to 35.");
      const result = await promoteRdsReadReplica(name, retention ? Number(retention) : undefined);
      const instance = result?.instance || {};
      return `Promoted ${name} to a standalone DB instance (backup retention ${instance.BackupRetentionPeriod ?? "not reported"} days).`;
    }, () => setPromoteOpen(false));
  };
  const handleAddClusterInstance = () => {
    if (!activeCluster || !addInstanceId.trim()) return;
    const cluster = activeCluster.name;
    const name = addInstanceId.trim();
    void runSnapshotAction(async () => {
      const result = await addRdsClusterInstance(cluster, name, addInstanceClass.trim() || "db.r6g.large");
      return `Adding DB instance ${name} to cluster ${cluster} (status ${result?.instance?.DBInstanceStatus || "not reported"}).`;
    }, () => setAddInstanceOpen(false));
  };
  const handleFailover = () => {
    if (!activeCluster) return;
    const cluster = activeCluster.name;
    void runSnapshotAction(async () => {
      const result = await failoverRdsCluster(cluster, failoverTarget);
      return result?.writer
        ? `Failover of ${cluster} complete: ${result.writer} is now the writer.`
        : `Failover of ${cluster} requested; Floci reported no writer in its answer.`;
    }, () => setFailoverOpen(false));
  };
  const handleTakeClusterSnapshot = () => {
    if (!activeCluster || !newClusterSnapshotId.trim()) return;
    const cluster = activeCluster.name;
    const name = newClusterSnapshotId.trim();
    void runSnapshotAction(async () => {
      const result = await createRdsClusterSnapshot(cluster, name);
      const status = result?.snapshot?.Status;
      return `Cluster snapshot "${name}" of ${cluster} ${status === "available" ? "is available" : `reported status ${status || "not reported"}`}. Find it under Snapshots.`;
    }, () => setClusterSnapshotOpen(false));
  };
  const selectedClusterSnapshot = selectedClusterSnapshots[0];
  const handleRestoreClusterSnapshot = () => {
    if (!selectedClusterSnapshot || !restoreClusterId.trim()) return;
    const snapshot = selectedClusterSnapshot;
    const name = restoreClusterId.trim();
    void runSnapshotAction(async () => {
      const result = await restoreRdsClusterSnapshot(snapshot.name, name, snapshot.engine || "");
      return `Restoring cluster snapshot "${snapshot.name}" to new DB cluster ${name} (status ${result?.cluster?.Status || "not reported"}). Add instances to it to connect through them.`;
    }, () => setRestoreClusterOpen(false));
  };
  const handleDeleteClusterSnapshot = () => {
    if (!selectedClusterSnapshot) return;
    const name = selectedClusterSnapshot.name;
    void runSnapshotAction(async () => {
      await deleteRdsClusterSnapshot(name);
      setSelectedClusterSnapshots([]);
      return `Cluster snapshot "${name}" deleted.`;
    }, () => setDeleteClusterSnapshotOpen(false));
  };
  const activeWriter = activeCluster ? clusterWriter(activeCluster) : undefined;
  const failoverReaders = activeCluster
    ? activeCluster.members
        .filter((member) => member.IsClusterWriter === false)
        .map((member) => String(member.DBInstanceIdentifier))
    : [];

  const handleCreateInstance = async () => {
    if (
      !newDbId.trim() ||
      !newUsername.trim() ||
      !newPassword.trim() ||
      mutationLock.current
    )
      return;
    mutationLock.current = true;
    setSubmittingInstance(true);
    setActionAlert(null);
    try {
      if (!Number.isInteger(Number(newStorage)) || Number(newStorage) <= 0)
        throw new Error("Allocated storage must be a positive whole number.");
      await createRdsInstance(
        newDbId.trim(),
        newEngine.value,
        newUsername.trim(),
        newPassword.trim(),
        newDbClass,
        Number(newStorage),
        newDbName.trim() || undefined,
        undefined,
        newEnableIam,
      );
      setActionAlert({
        type: "success",
        message: `Database instance "${newDbId}" creation requested. Refresh to follow its status.`,
      });
      if (!alive.current) return;
      setNewPassword("");
      setCreateInstanceOpen(false);
      setNewDbId("");
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to create database instance.",
      });
    } finally {
      mutationLock.current = false;
      if (alive.current) setSubmittingInstance(false);
    }
  };

  const handleModifyInstance = async () => {
    if (!activeInstance || mutationLock.current) return;
    mutationLock.current = true;
    setSubmittingModify(true);
    setActionAlert(null);
    try {
      if (
        modifyStorage &&
        (!Number.isInteger(Number(modifyStorage)) || Number(modifyStorage) <= 0)
      )
        throw new Error("Allocated storage must be a positive whole number.");
      await modifyRdsInstance(
        activeInstance.name,
        modifyClass || undefined,
        modifyStorage ? Number(modifyStorage) : undefined,
        modifyPassword || undefined,
        applyImmediately,
      );
      setActionAlert({
        type: "success",
        message: `Database "${activeInstance.name}" modification requested. Refresh to inspect status and pending modifications.`,
      });
      setModifyInstanceOpen(false);
      setModifyClass("");
      setModifyStorage("");
      setModifyPassword("");
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to modify database instance.",
      });
    } finally {
      mutationLock.current = false;
      if (alive.current) setSubmittingModify(false);
    }
  };

  const handleRebootInstance = async () => {
    if (!activeInstance || mutationLock.current) return;
    mutationLock.current = true;
    setRebooting(true);
    setActionAlert(null);
    try {
      await rebootRdsInstance(activeInstance.name);
      setActionAlert({
        type: "success",
        message: `Rebooting database "${activeInstance.name}"...`,
      });
      setRebootInstanceOpen(false);
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to reboot database.",
      });
    } finally {
      mutationLock.current = false;
      if (alive.current) setRebooting(false);
    }
  };

  const handleDeleteInstance = async () => {
    if (!activeInstance || mutationLock.current) return;
    mutationLock.current = true;
    setDeleting(true);
    setActionAlert(null);
    try {
      const result = await deleteRdsInstance(
        activeInstance.name,
        skipFinalSnapshot,
        finalSnapshotId,
      );
      setActionAlert(deletionAlert(`Deletion requested for database "${activeInstance.name}".`, result));
      setDeleteInstanceOpen(false);
      setSelectedInstances([]);
      onNavigate?.("rds/instances");
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to delete database.",
      });
    } finally {
      mutationLock.current = false;
      if (alive.current) setDeleting(false);
    }
  };

  const handleCreateCluster = async () => {
    if (
      !clusterId.trim() ||
      !clusterUsername.trim() ||
      !clusterPassword.trim() ||
      mutationLock.current
    )
      return;
    mutationLock.current = true;
    setSubmittingCluster(true);
    setActionAlert(null);
    try {
      await createRdsCluster(
        clusterId.trim(),
        clusterEngine.value,
        clusterUsername.trim(),
        clusterPassword.trim(),
        clusterDbName.trim() || undefined,
      );
      setActionAlert({
        type: "success",
        message: `Creation requested for DB cluster "${clusterId}".`,
      });
      if (!alive.current) return;
      setClusterPassword("");
      setCreateClusterOpen(false);
      setClusterId("");
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to create DB cluster.",
      });
    } finally {
      mutationLock.current = false;
      if (alive.current) setSubmittingCluster(false);
    }
  };

  const handleCreateParamGroup = async () => {
    if (
      !pgName.trim() ||
      !pgFamily.trim() ||
      !pgDesc.trim() ||
      mutationLock.current
    )
      return;
    mutationLock.current = true;
    setSubmittingPg(true);
    setActionAlert(null);
    try {
      await createRdsParameterGroup(
        pgName.trim(),
        pgFamily.trim(),
        pgDesc.trim(),
      );
      setActionAlert({
        type: "success",
        message: `Parameter group "${pgName}" created.`,
      });
      setCreateParamGroupOpen(false);
      setPgName("");
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to create parameter group.",
      });
    } finally {
      mutationLock.current = false;
      if (alive.current) setSubmittingPg(false);
    }
  };

  const handleDeleteResource = async () => {
    if (!deleteResource || mutationLock.current) return;
    mutationLock.current = true;
    setResourceDeleting(true);
    setActionAlert(null);
    try {
      const result = deleteResource.kind === "cluster"
        ? await deleteRdsCluster(
            deleteResource.name,
            skipFinalSnapshot,
            finalSnapshotId,
          )
        : await deleteRdsParameterGroup(deleteResource.name);
      if (!alive.current) return;
      setActionAlert(deletionAlert(
        `${deleteResource.kind === "cluster" ? "Cluster deletion requested" : "Parameter group deleted"}: ${deleteResource.name}.`,
        result,
      ));
      setDeleteResource(null);
      setSelectedClusters([]);
      setSelectedParamGroups([]);
      if (activeCluster) onNavigate?.("rds/clusters");
      else await loadData();
    } catch (err: any) {
      if (alive.current)
        setActionAlert({
          type: "error",
          message: err.message || "Unable to delete RDS resource.",
        });
    } finally {
      mutationLock.current = false;
      if (alive.current) setResourceDeleting(false);
    }
  };

  const filteredInstances = instances.filter(
    (i) =>
      i.name.toLowerCase().includes(filterText.toLowerCase()) ||
      i.engine.toLowerCase().includes(filterText.toLowerCase()),
  );

  return (
    <SpaceBetween size="l">
      {actionAlert &&
        !createInstanceOpen &&
        !modifyInstanceOpen &&
        !rebootInstanceOpen &&
        !deleteInstanceOpen &&
        !createClusterOpen &&
        !createParamGroupOpen &&
        !deleteResource && (
          <Alert
            type={actionAlert.type}
            dismissible
            onDismiss={() => setActionAlert(null)}
            header={
              actionAlert.type === "error"
                ? "RDS Operation Error"
                : "RDS Notification"
            }
          >
            {actionAlert.message}
          </Alert>
        )}

      <Header
        variant="h1"
        description={
          requested
            ? "Reported resource configuration in the current session."
            : "Inspect relational databases, cluster membership, and engine configuration."
        }
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button iconName="refresh" loading={loading} onClick={loadData}>
              Refresh
            </Button>
            {activeInstance && (
              <Button
                disabled={activeInstance.status !== "available"}
                onClick={() => {
                  setActionAlert(null);
                  setRebootInstanceOpen(true);
                }}
              >
                Reboot
              </Button>
            )}
            {activeInstance && <Button onClick={prepareModify}>Modify</Button>}
            {activeInstance && (
              <Button
                disabled={activeInstance.status !== "available"}
                onClick={() => {
                  setActionAlert(null);
                  setNewSnapshotId(`${activeInstance.name}-snapshot`);
                  setTakeSnapshotOpen(true);
                }}
              >
                Take snapshot
              </Button>
            )}
            {activeInstance && (
              <ButtonDropdown
                disabled={snapshotBusy}
                items={[
                  {
                    id: "create-read-replica",
                    text: "Create read replica",
                    disabled: activeInstance.status !== "available" || Boolean(activeInstance.cluster_identifier),
                  },
                  {
                    id: "promote",
                    text: "Promote",
                    disabled: !activeInstance.read_replica_source,
                  },
                  {
                    id: "restore-point-in-time",
                    text: "Restore to point in time",
                    disabled: Boolean(activeInstance.cluster_identifier),
                  },
                ]}
                onItemClick={({ detail }) => {
                  setActionAlert(null);
                  if (detail.id === "create-read-replica") {
                    setReplicaId(`${activeInstance.name}-replica`);
                    setReplicaClass("");
                    setReplicaOpen(true);
                  } else if (detail.id === "promote") {
                    setPromoteRetention("1");
                    setPromoteOpen(true);
                  } else {
                    setPitrTarget(`${activeInstance.name}-restored`);
                    setPitrMode("latest");
                    setPitrTime("");
                    setPitrClass("");
                    setPitrOpen(true);
                  }
                }}
              >
                Actions
              </ButtonDropdown>
            )}
            {activeInstance && (
              <Button onClick={prepareDeleteInstance}>Delete</Button>
            )}
            {activeCluster && (
              <ButtonDropdown
                disabled={snapshotBusy}
                items={[
                  { id: "add-instance", text: "Add reader" },
                  {
                    id: "failover",
                    text: "Failover",
                    disabled: failoverReaders.length === 0,
                  },
                  { id: "take-snapshot", text: "Take snapshot" },
                ]}
                onItemClick={({ detail }) => {
                  setActionAlert(null);
                  if (detail.id === "add-instance") {
                    const base = activeCluster.members.length === 0 ? "writer" : `reader-${activeCluster.members.length}`;
                    setAddInstanceId(`${activeCluster.name}-${base}`);
                    setAddInstanceClass("db.r6g.large");
                    setAddInstanceOpen(true);
                  } else if (detail.id === "failover") {
                    setFailoverTarget("");
                    setFailoverOpen(true);
                  } else {
                    setNewClusterSnapshotId(`${activeCluster.name}-snapshot`);
                    setClusterSnapshotOpen(true);
                  }
                }}
              >
                Actions
              </ButtonDropdown>
            )}
            {activeCluster && (
              <Button
                onClick={() =>
                  prepareDeleteResource("cluster", activeCluster.name)
                }
              >
                Delete cluster
              </Button>
            )}
          </SpaceBetween>
        }
      >
        {requestedDb
          ? `Database: ${activeInstance?.name || requestedDb}`
          : requestedCluster
            ? `Cluster: ${activeCluster?.name || requestedCluster}`
            : "Amazon RDS"}
      </Header>
      {requested && (
        <Button
          variant="link"
          iconName="arrow-left"
          onClick={() =>
            onNavigate?.(requestedDb ? "rds/instances" : "rds/clusters")
          }
        >
          {requestedDb ? "Databases" : "Clusters"}
        </Button>
      )}
      {loadError && (
        <Alert type="error" header="Unable to load RDS">
          {loadError}
        </Alert>
      )}
      {issues.length > 0 && (
        <Alert type="warning" header="Some RDS data could not be loaded">
          {issues.join(". ")}
        </Alert>
      )}
      {requested && loading && !activeInstance && !activeCluster && (
        <StatusIndicator type="loading">
          Loading resource workspace
        </StatusIndicator>
      )}
      {requested &&
        !loading &&
        !loadError &&
        !activeInstance &&
        !activeCluster && (
          <Alert type="warning" header="Resource not found">
            This database or cluster is not reported in the current session.
            Choose an available resource.
          </Alert>
        )}
      {activeInstance && (
        <RdsDatabaseDetails
          db={activeInstance}
          cluster={clusters.find(
            (cluster) => cluster.name === activeInstance.cluster_identifier,
          )}
          tab={detailTab}
          onTabChange={(tab) => onNavigate?.(dbPath(activeInstance.name, tab))}
          onCluster={(name) => onNavigate?.(clusterPath(name))}
          onInstance={(name) => onNavigate?.(dbPath(name))}
        />
      )}
      {activeCluster && (
        <RdsClusterDetails
          cluster={activeCluster}
          instances={instances}
          tab={detailTab}
          onTabChange={(tab) =>
            onNavigate?.(clusterPath(activeCluster.name, tab))
          }
          onInstance={(name) => onNavigate?.(dbPath(name))}
        />
      )}
      {!requested && (
        <Tabs
          activeTabId={topTabId}
          onChange={({ detail }) => setTopTabId(detail.activeTabId)}
          tabs={[
            {
              id: "instances",
              label: `Databases (${instances.length})`,
              content: (
                <SpaceBetween size="l">
                  <Table
                    header={
                      <Header
                        variant="h2"
                        counter={`(${instances.length})`}
                        description="Relational database instances (PostgreSQL, MySQL, MariaDB, Aurora) running in Floci."
                        actions={
                          <SpaceBetween direction="horizontal" size="xs">
                            <Button
                              variant="primary"
                              iconName="add-plus"
                              onClick={() => setCreateInstanceOpen(true)}
                            >
                              Create database
                            </Button>
                          </SpaceBetween>
                        }
                      >
                        Databases
                      </Header>
                    }
                    columnDefinitions={[
                      {
                        id: "name",
                        header: "DB identifier",
                        cell: (item) => (
                          <Button
                            variant="inline-link"
                            onClick={() => onNavigate?.(dbPath(item.name))}
                          >
                            <strong>{item.name}</strong>
                          </Button>
                        ),
                        sortingField: "name",
                        isRowHeader: true,
                      },
                      {
                        id: "status",
                        header: "Status",
                        cell: (item) => (
                          <StatusIndicator type={rdsStatus(item.status)}>
                            {rdsStatusLabel(item.status)}
                          </StatusIndicator>
                        ),
                      },
                      {
                        id: "engine",
                        header: "Engine",
                        cell: (item) => (
                          <span>{rdsEngineLabel(item.engine)}</span>
                        ),
                      },
                      {
                        id: "class",
                        header: "Size",
                        cell: (item) => item.class || "Not reported",
                      },
                      {
                        id: "endpoint",
                        header: "Endpoint and port",
                        cell: (item) => (
                          <code>
                            {rdsEndpoint(item.connect_host, item.connect_port)}
                          </code>
                        ),
                      },
                      {
                        id: "storage",
                        header: "Storage",
                        cell: (item) =>
                          item.allocated_storage == null
                            ? "Not reported"
                            : `${item.allocated_storage} GiB${item.storage_type ? ` (${item.storage_type})` : ""}`,
                      },
                    ]}
                    items={filteredInstances}
                    loading={loading}
                    loadingText="Loading RDS databases..."
                    selectionType="single"
                    selectedItems={selectedInstances}
                    onSelectionChange={({ detail }) => {
                      setSelectedInstances(detail.selectedItems);
                      if (detail.selectedItems[0])
                        onNavigate?.(dbPath(detail.selectedItems[0].name));
                    }}
                    filter={
                      <TextFilter
                        filteringText={filterText}
                        filteringPlaceholder="Filter databases by identifier or engine..."
                        onChange={({ detail }) =>
                          setFilterText(detail.filteringText)
                        }
                      />
                    }
                    empty={
                      <Box
                        textAlign="center"
                        color="inherit"
                        padding={{ vertical: "l" }}
                      >
                        <SpaceBetween size="m">
                          <b>No databases found</b>
                          <p>
                            Launch a PostgreSQL, MySQL, or MariaDB database
                            instance in Floci.
                          </p>
                          <Button
                            variant="primary"
                            onClick={() => setCreateInstanceOpen(true)}
                          >
                            Create database
                          </Button>
                        </SpaceBetween>
                      </Box>
                    }
                  />
                </SpaceBetween>
              ),
            },
            {
              id: "clusters",
              label: `DB Clusters (${clusters.length})`,
              content: (
                <SpaceBetween size="l">
                  <Table
                    header={
                      <Header
                        variant="h2"
                        counter={`(${clusters.length})`}
                        description="Multi-AZ Aurora and clustered database topologies."
                        actions={
                          <Button
                            variant="primary"
                            iconName="add-plus"
                            onClick={() => setCreateClusterOpen(true)}
                          >
                            Create DB cluster
                          </Button>
                        }
                      >
                        Aurora & Multi-AZ DB Clusters
                      </Header>
                    }
                    columnDefinitions={[
                      {
                        id: "name",
                        header: "Cluster identifier",
                        cell: (c) => (
                          <Button
                            variant="inline-link"
                            onClick={() => onNavigate?.(clusterPath(c.name))}
                          >
                            {c.name}
                          </Button>
                        ),
                      },
                      {
                        id: "status",
                        header: "Status",
                        cell: (c) => (
                          <StatusIndicator type={rdsStatus(c.status)}>
                            {rdsStatusLabel(c.status)}
                          </StatusIndicator>
                        ),
                      },
                      {
                        id: "engine",
                        header: "Engine",
                        cell: (c) => (
                          <span>{rdsEngineLabel(c.engine)}</span>
                        ),
                      },
                      {
                        id: "endpoint",
                        header: "Cluster endpoint",
                        cell: (c) => (
                          <code>{rdsEndpoint(c.endpoint, c.port)}</code>
                        ),
                      },
                      {
                        id: "actions",
                        header: "Action",
                        cell: (c) => (
                          <Button
                            iconName="remove"
                            onClick={() =>
                              prepareDeleteResource("cluster", c.name)
                            }
                          >
                            Delete
                          </Button>
                        ),
                      },
                    ]}
                    items={clusters}
                    selectionType="single"
                    selectedItems={selectedClusters}
                    onSelectionChange={({ detail }) => {
                      setSelectedClusters(detail.selectedItems);
                      if (detail.selectedItems[0])
                        onNavigate?.(clusterPath(detail.selectedItems[0].name));
                    }}
                    empty={
                      <Box textAlign="center" padding={{ vertical: "m" }}>
                        No DB clusters configured.
                      </Box>
                    }
                  />
                </SpaceBetween>
              ),
            },
            {
              id: "parameter-groups",
              label: `Parameter Groups (${paramGroups.length})`,
              content: (
                <SpaceBetween size="l">
                  <Table
                    header={
                      <Header
                        variant="h2"
                        counter={`(${paramGroups.length})`}
                        description="Manage database engine configuration parameters and server flags."
                        actions={
                          <Button
                            variant="primary"
                            iconName="add-plus"
                            onClick={() => setCreateParamGroupOpen(true)}
                          >
                            Create parameter group
                          </Button>
                        }
                      >
                        DB Parameter Groups
                      </Header>
                    }
                    columnDefinitions={[
                      {
                        id: "name",
                        header: "Group name",
                        cell: (pg) => <strong>{pg.name}</strong>,
                      },
                      {
                        id: "family",
                        header: "Family",
                        cell: (pg) => (
                          <Badge color="grey">
                            {pg.family || "Not reported"}
                          </Badge>
                        ),
                      },
                      {
                        id: "desc",
                        header: "Description",
                        cell: (pg) => pg.description || "Not reported",
                      },
                      {
                        id: "count",
                        header: "Parameters",
                        cell: (pg) =>
                          pg.parameter_count === undefined
                            ? "Not reported"
                            : `${pg.parameter_count} parameters`,
                      },
                      {
                        id: "actions",
                        header: "Action",
                        cell: (pg) => (
                          <Button
                            iconName="remove"
                            onClick={() =>
                              prepareDeleteResource("group", pg.name)
                            }
                          >
                            Delete
                          </Button>
                        ),
                      },
                    ]}
                    items={paramGroups}
                    selectionType="single"
                    selectedItems={selectedParamGroups}
                    onSelectionChange={({ detail }) =>
                      setSelectedParamGroups(detail.selectedItems)
                    }
                    empty={
                      <Box textAlign="center" padding={{ vertical: "m" }}>
                        No custom parameter groups found.
                      </Box>
                    }
                  />
                </SpaceBetween>
              ),
            },
            {
              id: "snapshots",
              label: `Snapshots (${snapshots.length + clusterSnapshots.length})`,
              content: (
                <SpaceBetween size="l">
                  <FlociNote>
                    A Floci DB snapshot holds a dump of the database, and restoring it
                    creates a new DB instance with that data. Floci snapshots PostgreSQL
                    instances only; it refuses MySQL and MariaDB. Automated backups
                    produce no system snapshots here.
                  </FlociNote>
                  <Table
                    header={
                      <Header
                        variant="h2"
                        counter={`(${snapshots.length})`}
                        description="Manual DB snapshots. To take one, open a database and choose Take snapshot."
                        actions={
                          <SpaceBetween direction="horizontal" size="xs">
                            <Button
                              disabled={!selectedSnapshot || snapshotBusy}
                              onClick={() => {
                                setActionAlert(null);
                                setDeleteSnapshotOpen(true);
                              }}
                            >
                              Delete snapshot
                            </Button>
                            <Button
                              variant="primary"
                              disabled={!selectedSnapshot || selectedSnapshot.status !== "available" || snapshotBusy}
                              onClick={() => {
                                setActionAlert(null);
                                setRestoreDbId(selectedSnapshot ? `${selectedSnapshot.name}-restored` : "");
                                setRestoreClass("");
                                setRestoreOpen(true);
                              }}
                            >
                              Restore snapshot
                            </Button>
                          </SpaceBetween>
                        }
                      >
                        Snapshots
                      </Header>
                    }
                    columnDefinitions={[
                      { id: "name", header: "Snapshot name", cell: (s) => <strong>{s.name}</strong> },
                      { id: "instance", header: "DB instance", cell: (s) => s.instance || "Not reported" },
                      {
                        id: "status",
                        header: "Status",
                        cell: (s) => (
                          <StatusIndicator type={rdsStatus(s.status)}>
                            {rdsStatusLabel(s.status)}
                          </StatusIndicator>
                        ),
                      },
                      { id: "type", header: "Snapshot type", cell: (s) => s.type || "Not reported" },
                      { id: "engine", header: "Engine", cell: (s) => (s.engine ? rdsEngineLabel(s.engine) : "Not reported") },
                      { id: "created", header: "Snapshot creation time", cell: (s) => rdsTime(s.created) },
                      {
                        id: "storage",
                        header: "Allocated storage",
                        cell: (s) => (s.allocated_storage == null ? "Not reported" : `${s.allocated_storage} GiB`),
                      },
                    ]}
                    items={snapshots}
                    loading={loading}
                    loadingText="Loading snapshots..."
                    selectionType="single"
                    selectedItems={selectedSnapshots}
                    onSelectionChange={({ detail }) => setSelectedSnapshots(detail.selectedItems)}
                    trackBy="name"
                    empty={
                      <Box textAlign="center" padding={{ vertical: "m" }}>
                        No DB snapshots found.
                      </Box>
                    }
                  />
                  <FlociNote>
                    A cluster snapshot holds a dump of the cluster&apos;s database.
                    Floci snapshots Aurora PostgreSQL clusters only; it refuses
                    Aurora MySQL.
                  </FlociNote>
                  <Table
                    header={
                      <Header
                        variant="h2"
                        counter={`(${clusterSnapshots.length})`}
                        description="Manual DB cluster snapshots. To take one, open a DB cluster and choose Actions, Take snapshot."
                        actions={
                          <SpaceBetween direction="horizontal" size="xs">
                            <Button
                              disabled={!selectedClusterSnapshot || snapshotBusy}
                              onClick={() => {
                                setActionAlert(null);
                                setDeleteClusterSnapshotOpen(true);
                              }}
                            >
                              Delete cluster snapshot
                            </Button>
                            <Button
                              variant="primary"
                              disabled={!selectedClusterSnapshot || selectedClusterSnapshot.status !== "available" || snapshotBusy}
                              onClick={() => {
                                setActionAlert(null);
                                setRestoreClusterId(selectedClusterSnapshot ? `${selectedClusterSnapshot.name}-restored` : "");
                                setRestoreClusterOpen(true);
                              }}
                            >
                              Restore cluster snapshot
                            </Button>
                          </SpaceBetween>
                        }
                      >
                        Cluster snapshots
                      </Header>
                    }
                    columnDefinitions={[
                      { id: "name", header: "Snapshot name", cell: (s) => <strong>{s.name}</strong> },
                      { id: "cluster", header: "DB cluster", cell: (s) => s.cluster || "Not reported" },
                      {
                        id: "status",
                        header: "Status",
                        cell: (s) => (
                          <StatusIndicator type={rdsStatus(s.status)}>
                            {rdsStatusLabel(s.status)}
                          </StatusIndicator>
                        ),
                      },
                      { id: "type", header: "Snapshot type", cell: (s) => s.type || "Not reported" },
                      { id: "engine", header: "Engine", cell: (s) => (s.engine ? rdsEngineLabel(s.engine) : "Not reported") },
                      { id: "created", header: "Snapshot creation time", cell: (s) => rdsTime(s.created) },
                    ]}
                    items={clusterSnapshots}
                    loading={loading}
                    loadingText="Loading cluster snapshots..."
                    selectionType="single"
                    selectedItems={selectedClusterSnapshots}
                    onSelectionChange={({ detail }) => setSelectedClusterSnapshots(detail.selectedItems)}
                    trackBy="name"
                    ariaLabels={{ tableLabel: "Cluster snapshots" }}
                    empty={
                      <Box textAlign="center" padding={{ vertical: "m" }}>
                        No DB cluster snapshots found.
                      </Box>
                    }
                  />
                </SpaceBetween>
              ),
            },
          ]}
        />
      )}
      {/* Create Instance Modal */}
      <Modal
        visible={createInstanceOpen}
        onDismiss={() => !submittingInstance && setCreateInstanceOpen(false)}
        header="Create RDS Database Instance"
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                onClick={() => setCreateInstanceOpen(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={
                  !newDbId.trim() ||
                  !newPassword.trim() ||
                  !newUsername.trim() ||
                  !newDbClass.trim() ||
                  !newStorage.trim()
                }
                onClick={handleCreateInstance}
                loading={submittingInstance}
              >
                Create database
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {createInstanceOpen && actionAlert?.type === "error" && (
            <Alert type="error">{actionAlert.message}</Alert>
          )}
          <FormField
            label="DB instance identifier"
            description="Unique alphanumeric identifier."
          >
            <Input
              value={newDbId}
              onChange={({ detail }) => setNewDbId(detail.value)}
              placeholder="e.g. analytics-db"
            />
          </FormField>
          <FormField label="Database engine">
            <Select
              selectedOption={newEngine}
              onChange={({ detail }) =>
                setNewEngine(detail.selectedOption as any)
              }
              options={ENGINE_OPTIONS.slice(0, 3)}
            />
          </FormField>
          <ColumnLayout columns={2}>
            <FormField label="DB instance class">
              <Input
                value={newDbClass}
                onChange={({ detail }) => setNewDbClass(detail.value)}
                placeholder="e.g. db.t3.micro"
              />
            </FormField>
            <FormField label="Allocated storage (GiB)">
              <Input
                value={newStorage}
                onChange={({ detail }) => setNewStorage(detail.value)}
                type="number"
              />
            </FormField>
          </ColumnLayout>
          <ColumnLayout columns={2}>
            <FormField label="Master username">
              <Input
                value={newUsername}
                onChange={({ detail }) => setNewUsername(detail.value)}
              />
            </FormField>
            <FormField label="Master password">
              <Input
                value={newPassword}
                onChange={({ detail }) => setNewPassword(detail.value)}
                type="password"
              />
            </FormField>
          </ColumnLayout>
          <FormField label="Initial database name (optional)">
            <Input
              value={newDbName}
              onChange={({ detail }) => setNewDbName(detail.value)}
              placeholder="e.g. production"
            />
          </FormField>
          <Checkbox
            checked={newEnableIam}
            onChange={({ detail }) => setNewEnableIam(detail.checked)}
          >
            Enable IAM database authentication
          </Checkbox>
        </SpaceBetween>
      </Modal>

      {/* Modify Instance Modal */}
      <Modal
        visible={modifyInstanceOpen}
        onDismiss={() => !submittingModify && setModifyInstanceOpen(false)}
        header={`Modify Database: ${activeInstance?.name}`}
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                onClick={() => setModifyInstanceOpen(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={
                  !modifyClass.trim() &&
                  !modifyStorage.trim() &&
                  !modifyPassword
                }
                onClick={handleModifyInstance}
                loading={submittingModify}
              >
                Save modifications
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {modifyInstanceOpen && actionAlert?.type === "error" && (
            <Alert type="error">{actionAlert.message}</Alert>
          )}
          <FormField
            label="New DB instance class (optional)"
            description="Leave blank to keep current class."
          >
            <Input
              value={modifyClass}
              onChange={({ detail }) => setModifyClass(detail.value)}
              placeholder={activeInstance?.class || "e.g. db.m5.large"}
            />
          </FormField>
          <FormField label="New allocated storage in GiB (optional)">
            <Input
              value={modifyStorage}
              onChange={({ detail }) => setModifyStorage(detail.value)}
              type="number"
              placeholder={
                activeInstance?.allocated_storage == null
                  ? "Enter allocated storage"
                  : String(activeInstance.allocated_storage)
              }
            />
          </FormField>
          <FormField label="New master user password (optional)">
            <Input
              value={modifyPassword}
              onChange={({ detail }) => setModifyPassword(detail.value)}
              type="password"
              placeholder="Enter new password..."
            />
          </FormField>
          <Checkbox
            checked={applyImmediately}
            onChange={({ detail }) => setApplyImmediately(detail.checked)}
          >
            Apply immediately
          </Checkbox>
          <Box>
            When unchecked, eligible changes are scheduled for the next
            maintenance window. Applying changes immediately can interrupt
            connections.
          </Box>
          <FlociNote>
            Floci runs no maintenance window, so it applies every change at
            once whether or not Apply immediately is checked.
          </FlociNote>
        </SpaceBetween>
      </Modal>

      {/* Take Snapshot Modal */}
      <Modal
        visible={takeSnapshotOpen}
        onDismiss={() => !snapshotBusy && setTakeSnapshotOpen(false)}
        header="Take DB snapshot"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={snapshotBusy} onClick={() => setTakeSnapshotOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={snapshotBusy} disabled={!newSnapshotId.trim()} onClick={handleTakeSnapshot}>
                Take snapshot
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box>
            Creates a point-in-time snapshot of {activeInstance?.name}.
          </Box>
          <FormField label="Snapshot name" constraintText="Unique in the Region. Letters, digits and hyphens; starts with a letter.">
            <Input value={newSnapshotId} onChange={({ detail }) => setNewSnapshotId(detail.value)} />
          </FormField>
          <FlociNote>
            Floci snapshots PostgreSQL instances only. For MySQL and MariaDB it
            returns InvalidDBInstanceState, which is shown here.
          </FlociNote>
        </SpaceBetween>
      </Modal>
      {/* Restore Snapshot Modal */}
      <Modal
        visible={restoreOpen}
        onDismiss={() => !snapshotBusy && setRestoreOpen(false)}
        header="Restore snapshot"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={snapshotBusy} onClick={() => setRestoreOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={snapshotBusy} disabled={!restoreDbId.trim()} onClick={handleRestoreSnapshot}>
                Restore DB instance
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box>
            Restoring creates a new DB instance from snapshot {selectedSnapshot?.name}; the
            original instance is not changed.
          </Box>
          <FormField label="DB instance identifier" description="Name of the new DB instance.">
            <Input value={restoreDbId} onChange={({ detail }) => setRestoreDbId(detail.value)} />
          </FormField>
          <FormField label={<>DB instance class <i>- optional</i></>} description="Leave blank to use the class of the snapshot's original instance.">
            <Input value={restoreClass} placeholder="db.t3.micro" onChange={({ detail }) => setRestoreClass(detail.value)} />
          </FormField>
        </SpaceBetween>
      </Modal>
      <ConfirmActionModal
        visible={deleteSnapshotOpen}
        header="Delete snapshot"
        confirmLabel="Delete"
        confirmationText="delete me"
        loading={snapshotBusy}
        onConfirm={handleDeleteSnapshot}
        onDismiss={() => setDeleteSnapshotOpen(false)}
      >
        Delete snapshot {selectedSnapshot?.name}? Its saved data is deleted too. This can&apos;t be undone.
      </ConfirmActionModal>
      {/* Create read replica */}
      <Modal
        visible={replicaOpen}
        onDismiss={() => !snapshotBusy && setReplicaOpen(false)}
        header="Create read replica"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={snapshotBusy} onClick={() => setReplicaOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={snapshotBusy} disabled={!replicaId.trim()} onClick={handleCreateReplica}>
                Create read replica
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box>
            Creates a read-only copy of {activeInstance?.name} that RDS keeps up to date
            asynchronously. Use it to scale reads; promote it to make it a standalone
            instance. The source needs automated backups.
          </Box>
          <FormField label="Replica source">
            <Input value={activeInstance?.name || ""} readOnly />
          </FormField>
          <FormField label="DB instance identifier" description="Name of the read replica.">
            <Input value={replicaId} onChange={({ detail }) => setReplicaId(detail.value)} />
          </FormField>
          <FormField label={<>DB instance class <i>- optional</i></>} description="Leave blank to use the source's class.">
            <Input value={replicaClass} placeholder={activeInstance?.class || "db.t3.micro"} onChange={({ detail }) => setReplicaClass(detail.value)} />
          </FormField>
          <FlociNote>
            Floci copies the source into the replica once, when it is created: later
            writes to the source are not replicated, and the replica accepts writes.
            Floci makes replicas of PostgreSQL instances only and refuses MySQL and
            MariaDB sources.
          </FlociNote>
        </SpaceBetween>
      </Modal>
      {/* Promote read replica */}
      <Modal
        visible={promoteOpen}
        onDismiss={() => !snapshotBusy && setPromoteOpen(false)}
        header="Promote read replica"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={snapshotBusy} onClick={() => setPromoteOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={snapshotBusy} onClick={handlePromote}>
                Promote read replica
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="warning">
            Promoting {activeInstance?.name} stops replication from{" "}
            {activeInstance?.read_replica_source || "its source"} and makes it a standalone
            DB instance that accepts writes. The instance reboots. This can&apos;t be undone.
          </Alert>
          <FormField label="Backup retention period" description="Days of automated backups for the promoted instance (0-35). Leave blank for 1 day.">
            <Input value={promoteRetention} type="number" onChange={({ detail }) => setPromoteRetention(detail.value)} />
          </FormField>
        </SpaceBetween>
      </Modal>
      {/* Restore to point in time */}
      <Modal
        visible={pitrOpen}
        onDismiss={() => !snapshotBusy && setPitrOpen(false)}
        header="Restore to point in time"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={snapshotBusy} onClick={() => setPitrOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={snapshotBusy}
                disabled={!pitrTarget.trim() || (pitrMode === "custom" && !isPitrTimeValid(pitrTime))}
                onClick={handleRestorePointInTime}
              >
                Restore to point in time
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box>
            Creates a new DB instance from {activeInstance?.name} as it was at the time you
            choose; the source is not changed. The source needs automated backups.
          </Box>
          <FormField label="Restore time">
            <RadioGroup
              value={pitrMode}
              onChange={({ detail }) => setPitrMode(detail.value as "latest" | "custom")}
              items={[
                {
                  value: "latest",
                  label: "Latest restorable time",
                  description: activeInstance?.latest_restorable_time
                    ? rdsTime(activeInstance.latest_restorable_time)
                    : "Not reported: the source may have automated backups off.",
                },
                { value: "custom", label: "Custom date and time" },
              ]}
            />
          </FormField>
          {pitrMode === "custom" && (
            <FormField
              label="Date and time (UTC)"
              constraintText="ISO 8601, for example 2026-10-23T10:30:00Z. It must be inside the restorable window."
              errorText={pitrTime.trim() && !isPitrTimeValid(pitrTime) ? "Enter a valid date and time." : undefined}
            >
              <Input ariaLabel="Custom restore time" value={pitrTime} placeholder="2026-10-23T10:30:00Z" onChange={({ detail }) => setPitrTime(detail.value)} />
            </FormField>
          )}
          <FormField label="DB instance identifier" description="Name of the new DB instance.">
            <Input value={pitrTarget} onChange={({ detail }) => setPitrTarget(detail.value)} />
          </FormField>
          <FormField label={<>DB instance class <i>- optional</i></>} description="Leave blank to use the source's class.">
            <Input value={pitrClass} placeholder={activeInstance?.class || "db.t3.micro"} onChange={({ detail }) => setPitrClass(detail.value)} />
          </FormField>
          <FlociNote>
            Floci checks that the time is inside the restorable window, but it keeps no
            transaction log: the new instance gets a copy of the source as it is now,
            whatever time you choose. Floci restores PostgreSQL instances only.
          </FlociNote>
        </SpaceBetween>
      </Modal>
      {/* Add instance to cluster */}
      <Modal
        visible={addInstanceOpen}
        onDismiss={() => !snapshotBusy && setAddInstanceOpen(false)}
        header="Add reader"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={snapshotBusy} onClick={() => setAddInstanceOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={snapshotBusy} disabled={!addInstanceId.trim() || !addInstanceClass.trim()} onClick={handleAddClusterInstance}>
                Add reader
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box>
            Adds a DB instance to cluster {activeCluster?.name}. The cluster&apos;s first
            instance becomes the writer; the others are readers that serve reads and are
            failover targets.
          </Box>
          <FormField label="DB instance identifier">
            <Input value={addInstanceId} onChange={({ detail }) => setAddInstanceId(detail.value)} />
          </FormField>
          <FormField label="DB instance class">
            <Input value={addInstanceClass} onChange={({ detail }) => setAddInstanceClass(detail.value)} />
          </FormField>
          <FlociNote>
            Floci runs every instance of a cluster against one database and places them
            all in the same Availability Zone.
          </FlociNote>
        </SpaceBetween>
      </Modal>
      {/* Failover cluster */}
      <Modal
        visible={failoverOpen}
        onDismiss={() => !snapshotBusy && setFailoverOpen(false)}
        header={`Failover DB cluster: ${activeCluster?.name || ""}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={snapshotBusy} onClick={() => setFailoverOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={snapshotBusy} onClick={handleFailover}>
                Failover
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box>
            Promotes a reader to writer; the current writer{activeWriter ? ` (${activeWriter})` : ""} becomes a
            reader. In AWS the cluster endpoint then points at the new writer and open
            connections drop.
          </Box>
          <FormField label="Target reader" description="Leave on the default to let RDS choose a reader.">
            <Select
              selectedOption={{ label: failoverTarget || "Let RDS choose", value: failoverTarget }}
              onChange={({ detail }) => setFailoverTarget(detail.selectedOption.value || "")}
              options={[
                { label: "Let RDS choose", value: "" },
                ...failoverReaders.map((name) => ({ label: name, value: name })),
              ]}
            />
          </FormField>
          <FlociNote>
            Floci moves only the writer role. The cluster&apos;s instances share one
            database, so no data moves and connections are not dropped.
          </FlociNote>
        </SpaceBetween>
      </Modal>
      {/* Take cluster snapshot */}
      <Modal
        visible={clusterSnapshotOpen}
        onDismiss={() => !snapshotBusy && setClusterSnapshotOpen(false)}
        header="Take DB cluster snapshot"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={snapshotBusy} onClick={() => setClusterSnapshotOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={snapshotBusy} disabled={!newClusterSnapshotId.trim()} onClick={handleTakeClusterSnapshot}>
                Take snapshot
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box>Creates a manual snapshot of DB cluster {activeCluster?.name}.</Box>
          <FormField label="Snapshot name" constraintText="Unique in the Region. Letters, digits and hyphens; starts with a letter.">
            <Input value={newClusterSnapshotId} onChange={({ detail }) => setNewClusterSnapshotId(detail.value)} />
          </FormField>
          <FlociNote>
            Floci snapshots Aurora PostgreSQL clusters only. For Aurora MySQL it returns
            InvalidDBClusterStateFault, which is shown here.
          </FlociNote>
        </SpaceBetween>
      </Modal>
      {/* Restore cluster snapshot */}
      <Modal
        visible={restoreClusterOpen}
        onDismiss={() => !snapshotBusy && setRestoreClusterOpen(false)}
        header="Restore cluster snapshot"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={snapshotBusy} onClick={() => setRestoreClusterOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={snapshotBusy} disabled={!restoreClusterId.trim()} onClick={handleRestoreClusterSnapshot}>
                Restore DB cluster
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box>
            Restoring creates a new DB cluster from snapshot {selectedClusterSnapshot?.name}.
            As in AWS, the new cluster has no instances: add a writer to connect to it.
          </Box>
          <FormField label="DB cluster identifier" description="Name of the new DB cluster.">
            <Input value={restoreClusterId} onChange={({ detail }) => setRestoreClusterId(detail.value)} />
          </FormField>
        </SpaceBetween>
      </Modal>
      <ConfirmActionModal
        visible={deleteClusterSnapshotOpen}
        header="Delete cluster snapshot"
        confirmLabel="Delete"
        confirmationText="delete me"
        loading={snapshotBusy}
        onConfirm={handleDeleteClusterSnapshot}
        onDismiss={() => setDeleteClusterSnapshotOpen(false)}
      >
        Delete cluster snapshot {selectedClusterSnapshot?.name}? Its saved data is deleted too. This can&apos;t be undone.
      </ConfirmActionModal>
      {/* Reboot Instance Modal */}
      <Modal
        visible={rebootInstanceOpen}
        onDismiss={() => !rebooting && setRebootInstanceOpen(false)}
        header={`Reboot Database: ${activeInstance?.name}`}
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                onClick={() => setRebootInstanceOpen(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                onClick={handleRebootInstance}
                loading={rebooting}
              >
                Reboot instance
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {rebootInstanceOpen && actionAlert?.type === "error" && (
            <Alert type="error">{actionAlert.message}</Alert>
          )}
          <p>
            Are you sure you want to reboot database instance{" "}
            <strong>{activeInstance?.name}</strong>? Connections may experience
            a momentary restart.
          </p>
        </SpaceBetween>
      </Modal>

      {/* Delete Instance Modal */}
      <Modal
        visible={deleteInstanceOpen}
        onDismiss={() => !deleting && setDeleteInstanceOpen(false)}
        header={`Delete ${activeInstance?.name} instance?`}
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                onClick={() => setDeleteInstanceOpen(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={(!skipFinalSnapshot && !finalSnapshotId.trim()) || deleteConfirmText !== "delete me"}
                onClick={handleDeleteInstance}
                loading={deleting}
              >
                Delete database
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {deleteInstanceOpen && actionAlert?.type === "error" && (
            <Alert type="error">{actionAlert.message}</Alert>
          )}
          <Alert type="warning">
            Deleting <strong>{activeInstance?.name}</strong> removes its storage. Automated backups are deleted with the instance; only a final snapshot keeps the data.
          </Alert>
          {snapshotFields}
        </SpaceBetween>
      </Modal>

      {/* Create Cluster Modal */}
      <Modal
        visible={createClusterOpen}
        onDismiss={() => !submittingCluster && setCreateClusterOpen(false)}
        header="Create DB Cluster"
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                onClick={() => setCreateClusterOpen(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={
                  !clusterId.trim() ||
                  !clusterUsername.trim() ||
                  !clusterPassword.trim()
                }
                onClick={handleCreateCluster}
                loading={submittingCluster}
              >
                Create cluster
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {createClusterOpen && actionAlert?.type === "error" && (
            <Alert type="error">{actionAlert.message}</Alert>
          )}
          <FormField label="DB cluster identifier">
            <Input
              value={clusterId}
              onChange={({ detail }) => setClusterId(detail.value)}
              placeholder="e.g. aurora-main-cluster"
            />
          </FormField>
          <FormField label="Engine">
            <Select
              selectedOption={clusterEngine}
              onChange={({ detail }) =>
                setClusterEngine(detail.selectedOption as any)
              }
              options={[ENGINE_OPTIONS[3], ENGINE_OPTIONS[4]]}
            />
          </FormField>
          <ColumnLayout columns={2}>
            <FormField label="Master username">
              <Input
                value={clusterUsername}
                onChange={({ detail }) => setClusterUsername(detail.value)}
              />
            </FormField>
            <FormField label="Master password">
              <Input
                value={clusterPassword}
                onChange={({ detail }) => setClusterPassword(detail.value)}
                type="password"
              />
            </FormField>
          </ColumnLayout>
          <FormField label="Initial database name (optional)">
            <Input
              value={clusterDbName}
              onChange={({ detail }) => setClusterDbName(detail.value)}
              placeholder="clusterdb"
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Create Parameter Group Modal */}
      <Modal
        visible={createParamGroupOpen}
        onDismiss={() => !submittingPg && setCreateParamGroupOpen(false)}
        header="Create DB Parameter Group"
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                onClick={() => setCreateParamGroupOpen(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={!pgName.trim() || !pgFamily.trim() || !pgDesc.trim()}
                onClick={handleCreateParamGroup}
                loading={submittingPg}
              >
                Create group
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {createParamGroupOpen && actionAlert?.type === "error" && (
            <Alert type="error">{actionAlert.message}</Alert>
          )}
          <FormField label="Parameter group name">
            <Input
              value={pgName}
              onChange={({ detail }) => setPgName(detail.value)}
              placeholder="e.g. pg15-optimized"
            />
          </FormField>
          <FormField label="DB parameter group family">
            <Input
              value={pgFamily}
              onChange={({ detail }) => setPgFamily(detail.value)}
              placeholder="e.g. postgres15, mysql8.0"
            />
          </FormField>
          <FormField label="Description">
            <Input
              value={pgDesc}
              onChange={({ detail }) => setPgDesc(detail.value)}
            />
          </FormField>
        </SpaceBetween>
      </Modal>
      <Modal
        visible={deleteResource !== null}
        onDismiss={() => !resourceDeleting && setDeleteResource(null)}
        header={
          deleteResource?.kind === "cluster"
            ? "Delete DB cluster"
            : "Delete parameter group"
        }
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                disabled={resourceDeleting}
                onClick={() => setDeleteResource(null)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={
                  deleteResource?.kind === "cluster" &&
                  ((!skipFinalSnapshot && !finalSnapshotId.trim()) || deleteConfirmText !== "delete me")
                }
                loading={resourceDeleting}
                onClick={handleDeleteResource}
              >
                Delete
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        {deleteResource && (
          <SpaceBetween size="m">
            <Box>
              Delete{" "}
              {deleteResource.kind === "cluster"
                ? "DB cluster"
                : "parameter group"}{" "}
              <strong>{deleteResource.name}</strong>?
            </Box>
            {deleteResource.kind === "cluster" && snapshotFields}
            {actionAlert?.type === "error" && (
              <Alert type="error">{actionAlert.message}</Alert>
            )}
          </SpaceBetween>
        )}
      </Modal>
    </SpaceBetween>
  );
};
