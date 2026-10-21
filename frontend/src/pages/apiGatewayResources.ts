export type GatewayApi = Record<string, any> & {
  id: string;
  name: string;
  api_type: string;
};
export const gatewayRows = (value: unknown): any[] =>
  Array.isArray(value) ? value : [];
export const gatewayStageName = (stage: any): string =>
  stage.StageName || stage.stageName || stage.name;
export const gatewayPath = (id: string, tab = "overview") =>
  `apigateway/${tab}?${new URLSearchParams({ api: id })}`;
export function gatewayApis(data: any): GatewayApi[] {
  const normalize = (item: any, type?: string): GatewayApi => ({
    ...item,
    id: item.id || item.ApiId || item.Id,
    name: item.name || item.Name,
    api_type:
      type ||
      String(item.protocol_type || item.ProtocolType || "HTTP").toLowerCase(),
    description: item.description ?? item.Description,
    created: item.created ?? item.CreatedDate ?? item.createdDate,
    tags: item.tags || item.Tags || {},
  });
  return [
    ...gatewayRows(data.rest_apis).map((item) => normalize(item, "rest")),
    ...gatewayRows(data.http_apis).map((item) => normalize(item, "http")),
    ...gatewayRows(data.apis || data.items).map((item) => normalize(item)),
  ];
}
export function gatewayDate(value?: string | number) {
  if (value == null) return "—";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}
export function gatewayInvokeUrl(api: GatewayApi, stage: string): string {
  if (!stage) return "";
  const pattern = api.execute_url_pattern;
  if (pattern)
    return pattern
      .replace("{stage}", encodeURIComponent(stage))
      .replace("{path}", "");
  const endpoint = api.api_endpoint || api.ApiEndpoint;
  return endpoint
    ? `${endpoint.replace(/\/$/, "")}${stage === "$default" ? "" : `/${encodeURIComponent(stage)}`}`
    : "";
}
