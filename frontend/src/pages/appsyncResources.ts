export type AppSyncApi = Record<string, any> & {
  apiId: string;
  name: string;
  data_sources: any[];
  resolvers: any[];
  api_keys: any[];
  functions: any[];
  issues: string[];
};

export function appSyncApis(inventory: any): AppSyncApi[] {
  return (
    Array.isArray(inventory.graphql_apis) ? inventory.graphql_apis : []
  ).map((api: any) => {
    const id = api.apiId || api.id;
    const issues: string[] = [];
    const collection = (key: string) => {
      if (api[key]?.error)
        issues.push(`${key.replace(/_/g, " ")}: ${api[key].error}`);
      if (Array.isArray(api[key])) return api[key];
      return (Array.isArray(inventory[key]) ? inventory[key] : []).filter(
        (item: any) => (item.apiId || item.api_id) === id,
      );
    };
    return {
      ...api,
      apiId: id,
      name: api.name || id,
      data_sources: collection("data_sources"),
      resolvers: collection("resolvers"),
      api_keys: collection("api_keys"),
      functions: collection("functions"),
      issues,
    };
  });
}

export function appSyncExpiry(value: unknown): string {
  if (value === undefined || value === null || value === "")
    return "Not reported";
  const timestamp = Number(value) * 1000;
  return Number.isFinite(timestamp) &&
    !Number.isNaN(new Date(timestamp).valueOf())
    ? new Date(timestamp).toISOString()
    : "Not reported";
}

export function appSyncKeyState(key: any): string {
  if (
    key.expires === undefined ||
    key.expires === null ||
    key.expires === "" ||
    !Number.isFinite(Number(key.expires))
  )
    return "Not reported";
  return Number(key.expires) * 1000 <= Date.now() ? "Expired" : "Active";
}
