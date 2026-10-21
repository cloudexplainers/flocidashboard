export type StackItem = Record<string, any> & {
  StackName: string;
  StackId: string;
  StackStatus: string;
  Resources: any[];
  Events: any[];
  Outputs: any[];
  Parameters: any[];
  ChangeSets: any[];
  Template: string;
  issues: string[];
};
export function cfnStatus(
  status?: string,
): "success" | "error" | "in-progress" | "stopped" | "not-started" {
  if (!status) return "not-started";
  if (status.includes("IN_PROGRESS") || status.endsWith("_PENDING"))
    return "in-progress";
  if (status.includes("FAILED") || status.includes("ROLLBACK")) return "error";
  if (
    status === "DELETE_COMPLETE" ||
    status === "OBSOLETE" ||
    status === "UNAVAILABLE"
  )
    return "stopped";
  if (status.endsWith("_COMPLETE") || status === "AVAILABLE") return "success";
  return "not-started";
}
export const cfnTime = (value: unknown) => {
  if (value === undefined || value === null || value === "")
    return "Not reported";
  const date = new Date(String(value));
  return Number.isNaN(date.valueOf()) ? "Not reported" : date.toISOString();
};
export const canExecuteChangeSet = (change: any) =>
  change?.Status === "CREATE_COMPLETE" &&
  change?.ExecutionStatus === "AVAILABLE";
export function cloudformationStacks(payload: any): StackItem[] {
  return (Array.isArray(payload.stacks) ? payload.stacks : []).map(
    (raw: any) => {
      const issues: string[] = [];
      const list = (name: string, alternate: string) => {
        const value = raw[name] ?? raw[alternate];
        if (value?.error)
          issues.push(`${name.replace(/_/g, " ")}: ${value.error}`);
        return Array.isArray(value) ? value : [];
      };
      const resources = list("resources", "StackResources");
      const fallbackResources = list(
        "resource_summaries",
        "StackResourceSummaries",
      );
      let template = raw.template ?? raw.TemplateBody;
      if (template?.error) {
        issues.push(`template: ${template.error}`);
        template = "";
      }
      if (
        template &&
        typeof template === "object" &&
        "TemplateBody" in template
      )
        template = template.TemplateBody;
      if (raw.details?.error) issues.push(raw.details.error);
      const events = list("events", "StackEvents")
        .slice()
        .sort((a, b) => {
          const at = Date.parse(a.Timestamp ?? a.EventTime ?? ""),
            bt = Date.parse(b.Timestamp ?? b.EventTime ?? "");
          return (
            (Number.isFinite(bt) ? bt : 0) - (Number.isFinite(at) ? at : 0)
          );
        });
      return {
        ...raw,
        StackName: raw.name || raw.StackName || raw.stack_name || "",
        StackId: raw.id || raw.StackId || "",
        StackStatus: raw.status || raw.StackStatus || raw.stack_status || "",
        StackStatusReason: raw.status_reason || raw.StackStatusReason || "",
        CreationTime: raw.created ?? raw.CreationTime ?? raw.creation_time,
        UpdatedTime: raw.updated ?? raw.LastUpdatedTime,
        Description: raw.description || raw.Description || "",
        Resources: resources.length ? resources : fallbackResources,
        Events: events,
        Outputs: list("outputs", "Outputs"),
        Parameters: list("parameters", "Parameters"),
        ChangeSets: list("change_sets", "Summaries"),
        Template:
          typeof template === "string"
            ? template
            : template
              ? JSON.stringify(template, null, 2)
              : "",
        DisableRollback: raw.disable_rollback ?? raw.DisableRollback,
        Capabilities: list("capabilities", "Capabilities"),
        Tags: list("tags", "Tags"),
        issues,
      };
    },
  );
}
export function previousStackParameters(stack: StackItem) {
  return JSON.stringify(
    stack.Parameters.map((parameter) => ({
      ParameterKey: parameter.ParameterKey ?? parameter.key,
      UsePreviousValue: true,
    })),
    null,
    2,
  );
}
export function parseStackParameters(
  value: string,
): any[] | Record<string, unknown> {
  const parsed = JSON.parse(value || "[]");
  if (!parsed || typeof parsed !== "object")
    throw new Error("Parameters must be a JSON object or array.");
  return parsed;
}
