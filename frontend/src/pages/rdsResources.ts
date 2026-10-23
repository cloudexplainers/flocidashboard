export type RdsInstance = Record<string, any> & {
  name: string;
  engine: string;
  status: string;
  class?: string;
};
export type RdsCluster = Record<string, any> & {
  name: string;
  engine: string;
  status: string;
  members: any[];
};
export type RdsParameterGroup = Record<string, any> & {
  name: string;
  family?: string;
  description?: string;
};
export type RdsClusterSnapshot = Record<string, any> & {
  name: string;
  cluster?: string;
  status: string;
  type?: string;
  engine?: string;
};
export type RdsSnapshot = Record<string, any> & {
  name: string;
  instance?: string;
  status: string;
  type?: string;
  engine?: string;
};
const array = (value: unknown): any[] => (Array.isArray(value) ? value : []);
export function rdsInventory(payload: any) {
  const instances: RdsInstance[] = array(
    payload.instances ?? payload.DBInstances ?? payload.databases,
  ).map((db) => ({
    ...db,
    name: db.name || db.DBInstanceIdentifier || db.identifier || "",
    arn: db.arn ?? db.DBInstanceArn,
    engine: db.engine ?? db.Engine ?? "",
    engine_version: db.engine_version ?? db.EngineVersion,
    status: db.status ?? db.DBInstanceStatus ?? "",
    class: db.class ?? db.DBInstanceClass,
    allocated_storage: db.allocated_storage ?? db.AllocatedStorage,
    storage_type: db.storage_type ?? db.StorageType,
    master_username: db.master_username ?? db.MasterUsername,
    connect_host:
      db.connect_host ?? db.endpoint?.Address ?? db.Endpoint?.Address,
    connect_port: db.connect_port ?? db.endpoint?.Port ?? db.Endpoint?.Port,
    db_name: db.db_name ?? db.DBName,
    iam_authentication:
      db.iam_authentication ?? db.IAMDatabaseAuthenticationEnabled,
    created: db.created ?? db.InstanceCreateTime,
    cluster_identifier: db.cluster_identifier ?? db.DBClusterIdentifier,
    vpc_security_groups:
      db.security_groups ?? db.vpc_security_groups ?? db.VpcSecurityGroups,
    subnet_group: db.subnet_group ?? db.DBSubnetGroup,
    publicly_accessible: db.publicly_accessible ?? db.PubliclyAccessible,
    multi_az: db.multi_az ?? db.MultiAZ,
    availability_zone: db.availability_zone ?? db.AvailabilityZone,
    secondary_availability_zone:
      db.secondary_availability_zone ?? db.SecondaryAvailabilityZone,
    storage_encrypted: db.storage_encrypted ?? db.StorageEncrypted,
    deletion_protection: db.deletion_protection ?? db.DeletionProtection,
    backup_retention_period:
      db.backup_retention_period ?? db.BackupRetentionPeriod,
    latest_restorable_time:
      db.latest_restorable_time ?? db.LatestRestorableTime,
    read_replica_source:
      db.read_replica_source ?? db.ReadReplicaSourceDBInstanceIdentifier,
    read_replicas: array(
      db.read_replicas ?? db.ReadReplicaDBInstanceIdentifiers,
    ),
    status_infos: array(db.status_infos ?? db.StatusInfos),
    pending_modifications: db.pending_modifications ?? db.PendingModifiedValues,
    parameter_groups: db.parameter_groups ?? db.DBParameterGroups,
  }));
  const clusters: RdsCluster[] = array(
    payload.clusters ?? payload.DBClusters,
  ).map((cluster) => ({
    ...cluster,
    name: cluster.name || cluster.DBClusterIdentifier || "",
    arn: cluster.arn ?? cluster.DBClusterArn,
    engine: cluster.engine ?? cluster.Engine ?? "",
    engine_version: cluster.engine_version ?? cluster.EngineVersion,
    status: cluster.status ?? cluster.Status ?? "",
    database_name: cluster.database_name ?? cluster.DatabaseName,
    master_username: cluster.master_username ?? cluster.MasterUsername,
    endpoint: cluster.endpoint ?? cluster.Endpoint,
    reader_endpoint: cluster.reader_endpoint ?? cluster.ReaderEndpoint,
    port: cluster.port ?? cluster.Port,
    created: cluster.created ?? cluster.ClusterCreateTime,
    members: array(cluster.members ?? cluster.DBClusterMembers),
    iam_authentication:
      cluster.iam_authentication ?? cluster.IAMDatabaseAuthenticationEnabled,
    storage_encrypted: cluster.storage_encrypted ?? cluster.StorageEncrypted,
    deletion_protection:
      cluster.deletion_protection ?? cluster.DeletionProtection,
    multi_az: cluster.multi_az ?? cluster.MultiAZ,
    availability_zones: cluster.availability_zones ?? cluster.AvailabilityZones,
    subnet_group: cluster.subnet_group ?? cluster.DBSubnetGroup,
    vpc_security_groups: cluster.security_groups ?? cluster.VpcSecurityGroups,
    parameter_group: cluster.parameter_group ?? cluster.DBClusterParameterGroup,
    backup_retention_period:
      cluster.backup_retention_period ?? cluster.BackupRetentionPeriod,
    latest_restorable_time:
      cluster.latest_restorable_time ?? cluster.LatestRestorableTime,
  }));
  const groups: RdsParameterGroup[] = array(
    payload.parameter_groups ?? payload.DBParameterGroups,
  ).map((group) => ({
    ...group,
    name: group.name || group.DBParameterGroupName || "",
    arn: group.arn ?? group.DBParameterGroupArn,
    family: group.family ?? group.DBParameterGroupFamily,
    description: group.description ?? group.Description,
    parameter_count: group.parameters?.error
      ? undefined
      : (group.parameter_count ??
        (Array.isArray(group.parameters)
          ? group.parameters.length
          : undefined)),
  }));
  const snapshots: RdsSnapshot[] = array(
    payload.snapshots ?? payload.DBSnapshots,
  ).map((snapshot) => ({
    ...snapshot,
    name: snapshot.name || snapshot.DBSnapshotIdentifier || "",
    arn: snapshot.arn ?? snapshot.DBSnapshotArn,
    instance: snapshot.instance ?? snapshot.DBInstanceIdentifier,
    status: snapshot.status ?? snapshot.Status ?? "",
    type: snapshot.type ?? snapshot.SnapshotType,
    engine: snapshot.engine ?? snapshot.Engine,
    engine_version: snapshot.engine_version ?? snapshot.EngineVersion,
    created: snapshot.created ?? snapshot.SnapshotCreateTime,
    allocated_storage: snapshot.allocated_storage ?? snapshot.AllocatedStorage,
  }));
  const clusterSnapshots: RdsClusterSnapshot[] = array(
    payload.cluster_snapshots ?? payload.DBClusterSnapshots,
  ).map((snapshot) => ({
    ...snapshot,
    name: snapshot.name || snapshot.DBClusterSnapshotIdentifier || "",
    arn: snapshot.arn ?? snapshot.DBClusterSnapshotArn,
    cluster: snapshot.cluster ?? snapshot.DBClusterIdentifier,
    status: snapshot.status ?? snapshot.Status ?? "",
    type: snapshot.type ?? snapshot.SnapshotType,
    engine: snapshot.engine ?? snapshot.Engine,
    created: snapshot.created ?? snapshot.SnapshotCreateTime,
  }));
  return { instances, clusters, groups, snapshots, clusterSnapshots };
}
export function rdsStatus(
  status: string,
): "success" | "error" | "stopped" | "in-progress" | "not-started" {
  if (status === "available") return "success";
  if (
    [
      "failed",
      "incompatible-restore",
      "incompatible-network",
      "incompatible-parameters",
      "inaccessible-encryption-credentials",
      "storage-full",
    ].includes(status)
  )
    return "error";
  if (["stopped", "deleting", "deleted"].includes(status)) return "stopped";
  if (
    [
      "creating",
      "modifying",
      "rebooting",
      "starting",
      "stopping",
      "backing-up",
      "configuring-enhanced-monitoring",
      "upgrading",
      "resetting-master-credentials",
      "maintenance",
      "renaming",
    ].includes(status)
  )
    return "in-progress";
  return "not-started";
}
export const rdsBoolean = (value: unknown) =>
  value === true ? "Yes" : value === false ? "No" : "Not reported";
export const rdsTime = (value: unknown) => {
  if (!value) return "Not reported";
  const date = new Date(String(value));
  return Number.isNaN(date.valueOf()) ? "Not reported" : date.toISOString();
};
export const rdsEndpoint = (host?: string, port?: number) =>
  host
    ? `${host}${port !== undefined && port !== null ? `:${port}` : ""}`
    : "Not reported";
export function clusterMembers(cluster: RdsCluster, instances: RdsInstance[]) {
  const members = cluster.members.map((member) => ({
    ...member,
    instance: instances.find((db) => db.name === member.DBInstanceIdentifier),
  }));
  for (const instance of instances.filter(
    (db) => db.cluster_identifier === cluster.name,
  )) {
    if (
      !members.some((member) => member.DBInstanceIdentifier === instance.name)
    )
      members.push({ DBInstanceIdentifier: instance.name, instance });
  }
  return members;
}
export const memberRole = (member: any) =>
  member.IsClusterWriter === true
    ? "Writer"
    : member.IsClusterWriter === false
      ? "Reader"
      : "Not reported";
const shell = (value: unknown) => `'${String(value).replace(/'/g, `'"'"'`)}'`;
export function rdsConnection(db: RdsInstance) {
  const postgres = ["postgres", "aurora-postgresql"].includes(db.engine);
  const mysql = ["mysql", "mariadb", "aurora-mysql", "aurora"].includes(
    db.engine,
  );
  if (!postgres && !mysql)
    return {
      reason:
        "Connection examples are available for reported PostgreSQL, MySQL, and MariaDB engines.",
    };
  if (
    !db.connect_host ||
    !Number.isInteger(db.connect_port) ||
    db.connect_port <= 0 ||
    !db.master_username ||
    (postgres && !db.db_name)
  )
    return {
      reason: `Connection examples need a reported endpoint, valid port, username${postgres ? ", and database name" : ""}.`,
    };
  const host = shell(db.connect_host),
    port = shell(db.connect_port),
    username = shell(db.master_username),
    database = shell(db.db_name);
  const cli = postgres
    ? `psql --host=${host} --port=${port} --username=${username} --dbname=${database} --password`
    : `${db.engine === "mariadb" ? "mariadb" : "mysql"} --host=${host} --port=${port} --user=${username}${db.db_name ? ` --database=${database}` : ""} --password`;
  const args = `host=${JSON.stringify(db.connect_host)},\n    port=${db.connect_port},\n    user=${JSON.stringify(db.master_username)},\n${db.db_name ? `    ${postgres ? "dbname" : "database"}=${JSON.stringify(db.db_name)},\n` : ""}    password=getpass("Database password: ")`;
  return {
    cli,
    python: `from getpass import getpass\nimport ${postgres ? "psycopg2" : "pymysql"}\n\nconnection = ${postgres ? "psycopg2" : "pymysql"}.connect(\n    ${args}\n)`,
  };
}

/** Status as the RDS console shows it: "available" -> "Available", "backing-up" -> "Backing-up". */
export function rdsStatusLabel(status?: string): string {
  if (!status) return "Not reported";
  return status.charAt(0).toUpperCase() + status.slice(1);
}

const RDS_ENGINE_LABELS: Record<string, string> = {
  postgres: "PostgreSQL",
  mysql: "MySQL",
  mariadb: "MariaDB",
  "aurora-postgresql": "Aurora PostgreSQL",
  "aurora-mysql": "Aurora MySQL",
  aurora: "Aurora MySQL",
  "oracle-ee": "Oracle Enterprise Edition",
  "oracle-se2": "Oracle Standard Edition Two",
  "sqlserver-ex": "SQL Server Express Edition",
  "sqlserver-se": "SQL Server Standard Edition",
  "sqlserver-ee": "SQL Server Enterprise Edition",
  "sqlserver-web": "SQL Server Web Edition",
  "neptune": "Neptune",
  "docdb": "DocumentDB",
};

/** Engine names as the RDS console lists them (the API returns engine identifiers such as aurora-postgresql). */
export function rdsEngineLabel(engine?: string): string {
  if (!engine) return "Not reported";
  return RDS_ENGINE_LABELS[engine] || engine;
}

/** The writer a cluster reports, or undefined when no member says it is the writer. */
export function clusterWriter(cluster: RdsCluster): string | undefined {
  return cluster.members.find((member) => member.IsClusterWriter === true)
    ?.DBInstanceIdentifier;
}

/** Read replication status of a replica ("replicating", "terminated", ...), from StatusInfos. */
export function replicationStatus(db: RdsInstance): string | undefined {
  const info = (db.status_infos || []).find(
    (item: any) => item?.StatusType === "read replication",
  );
  return info?.Status;
}
