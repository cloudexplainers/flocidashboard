export const comparisons: Record<string, string> = {
  GreaterThanThreshold: ">",
  GreaterThanOrEqualToThreshold: "≥",
  LessThanThreshold: "<",
  LessThanOrEqualToThreshold: "≤",
  LessThanLowerThreshold: "Below anomaly detection band",
  GreaterThanUpperThreshold: "Above anomaly detection band",
  LessThanLowerOrGreaterThanUpperThreshold: "Outside anomaly detection band",
};
export function alarmCondition(alarm: any): string {
  const operator = alarm.ComparisonOperator;
  if (
    operator &&
    operator.includes("Threshold") &&
    ![
      "GreaterThanThreshold",
      "GreaterThanOrEqualToThreshold",
      "LessThanThreshold",
      "LessThanOrEqualToThreshold",
    ].includes(operator)
  )
    return comparisons[operator] || operator;
  if (!operator || alarm.Threshold == null) return "Not reported";
  return `${comparisons[operator] || operator} ${alarm.Threshold}`;
}
export function cloudWatchAlarms(data: any): any[] {
  return (data.metric_alarms || data.alarms || []).map((alarm: any) => {
    const result = { ...alarm };
    for (const key of [
      "AlarmName",
      "AlarmArn",
      "AlarmDescription",
      "MetricName",
      "Namespace",
      "Dimensions",
      "StateValue",
      "StateReason",
      "StateUpdatedTimestamp",
      "Threshold",
      "ComparisonOperator",
      "Statistic",
      "ExtendedStatistic",
      "Period",
      "EvaluationPeriods",
      "DatapointsToAlarm",
      "TreatMissingData",
      "ActionsEnabled",
      "AlarmActions",
      "OKActions",
      "InsufficientDataActions",
      "Metrics",
    ]) {
      const snake = key
        .replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
        .replace(/([a-z])([A-Z])/g, "$1_$2")
        .toLowerCase();
      result[key] = alarm[key] ?? alarm[snake];
    }
    result.Dimensions ||= [];
    return result;
  });
}
export function monitoringDate(value?: string | number) {
  if (value == null) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}
export function retentionLabel(days?: number | null) {
  return days == null || days === 0 ? "Never expire" : `${days} days`;
}
