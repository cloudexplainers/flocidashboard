export interface CliResponse {
  requires_confirmation?: boolean;
  message?: string;
  command?: string;
  ok?: boolean;
  exit_code?: number;
  stdout?: string;
  stderr?: string;
  duration_ms?: number;
  executed_args?: string[];
  endpoint_url?: string;
  region?: string;
  credential_source?: string;
  identity_label?: string;
  identity_type?: string;
  identity_expires_at?: string;
  stdout_truncated?: boolean;
  stderr_truncated?: boolean;
  output_limit?: number;
}
export interface CliExecution {
  id: string;
  command: string;
  submittedAt: string;
  account: string;
  principal: string;
  endpoint: string;
  region: string;
  response?: CliResponse;
  error?: string;
}
export const CLI_PRESETS = [
  { label: "STS · Caller identity", value: "aws sts get-caller-identity" },
  { label: "S3 · List buckets", value: "aws s3 ls" },
  { label: "IAM · List users", value: "aws iam list-users" },
  { label: "IAM · List roles", value: "aws iam list-roles" },
  { label: "SQS · List queues", value: "aws sqs list-queues" },
  { label: "DynamoDB · List tables", value: "aws dynamodb list-tables" },
  { label: "Lambda · List functions", value: "aws lambda list-functions" },
  { label: "EC2 · Describe instances", value: "aws ec2 describe-instances" },
  { label: "SNS · List topics", value: "aws sns list-topics" },
  {
    label: "RDS · Describe DB instances",
    value: "aws rds describe-db-instances",
  },
  {
    label: "ECR · Describe repositories",
    value: "aws ecr describe-repositories",
  },
  { label: "SSM · Describe parameters", value: "aws ssm describe-parameters" },
];

// Memory only: commands and output never go to browser storage. Scope by session context.
const histories = new Map<string, CliExecution[]>();
export const readCliHistory = (scope: string) => histories.get(scope) || [];
export function recordCliExecution(scope: string, entry: CliExecution) {
  const history = [entry, ...readCliHistory(scope)].slice(0, 20);
  histories.delete(scope);
  histories.set(scope, history);
  while (histories.size > 5) histories.delete(histories.keys().next().value!);
  return history;
}
export const clearCliHistory = (scope: string) => histories.delete(scope);
export function parseCliJson(text?: string) {
  if (!text?.trim()) return { valid: false, value: undefined };
  try {
    return { valid: true, value: JSON.parse(text) };
  } catch {
    return { valid: false, value: undefined };
  }
}
export const cliArgsText = (args: string[]) =>
  args
    .map((arg) =>
      /^[\w./:=@,+-]+$/.test(arg) ? arg : `'${arg.replace(/'/g, `'"'"'`)}'`,
    )
    .join(" ");
