export const athenaRows = (value: unknown): any[] =>
  Array.isArray(value) ? value : [];
export const queryState = (query: any) => query?.Status?.State || query?.state;
export const queryId = (query: any) => query.QueryExecutionId || query.id;
export function athenaQueries(data: any): any[] {
  return athenaRows(data.query_executions || data.queries).map((query) => ({
    ...query,
    QueryExecutionId: queryId(query),
    Query: query.Query ?? query.query,
    Status: query.Status || {
      State: query.state,
      StateChangeReason: query.state_change_reason,
      SubmissionDateTime: query.submission_time,
    },
    WorkGroup: query.WorkGroup || query.workgroup,
    QueryExecutionContext: query.QueryExecutionContext || {
      Database: query.database,
      Catalog: query.catalog,
    },
    Statistics: query.Statistics || query.statistics || {},
    ResultConfiguration:
      query.ResultConfiguration || query.result_configuration || {},
  }));
}
export function athenaWorkgroups(data: any): any[] {
  return athenaRows(data.workgroups).map((group) => ({
    ...group,
    Name: group.Name || group.name,
    State: group.State || group.state,
    Description: group.Description ?? group.description,
    Configuration:
      group.Configuration ||
      group.configuration ||
      group.details?.Configuration ||
      {},
    CreationTime: group.CreationTime || group.creation_time,
  }));
}
export type AthenaColumn = { id: string; name: string; type?: string };
export function athenaResultPage(
  result: any,
  first: boolean,
  previous: AthenaColumn[] = [],
) {
  const set = result.result_set || result.ResultSet || {};
  const metadata = athenaRows(set.ResultSetMetadata?.ColumnInfo);
  const columns: AthenaColumn[] = metadata.length
    ? metadata.map((column, index) => ({
        id: String(index),
        name: column.Label || column.Name || `Column ${index + 1}`,
        type: column.Type,
      }))
    : previous;
  let rows: any[] = athenaRows(set.Rows);
  // Athena SELECT results include a header on the first page. SHOW and
  // continuation pages can contain only data; never discard a row blindly.
  if (
    first &&
    columns.length &&
    rows.length &&
    columns.every((column, index) => {
      const value = rows[0].Data?.[index]?.VarCharValue;
      return value === column.name || value === metadata[index]?.Name;
    })
  )
    rows = rows.slice(1);
  const width =
    columns.length || Math.max(0, ...rows.map((row) => row.Data?.length || 0));
  const resolved = columns.length
    ? columns
    : Array.from({ length: width }, (_, index) => ({
        id: String(index),
        name: `Column ${index + 1}`,
      }));
  return {
    columns: resolved,
    rows: rows.map((row) =>
      resolved.map((_, index) => row.Data?.[index]?.VarCharValue ?? null),
    ),
    next: result.next_token || result.NextToken || "",
    updateCount:
      result.update_count ?? result.UpdateCount ?? result.response?.UpdateCount,
  };
}
export function athenaBytes(value?: number) {
  if (value == null) return "Not reported";
  if (value < 1024) return `${value} B`;
  return value < 1024 * 1024
    ? `${(value / 1024).toFixed(1)} KB`
    : `${(value / (1024 * 1024)).toFixed(1)} MB`;
}
export function athenaDate(value?: string | number) {
  if (value == null) return "—";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}
