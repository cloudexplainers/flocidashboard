import { useEffect, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Checkbox from "@cloudscape-design/components/checkbox";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "./ResourceTable";
import ConfirmActionModal from "./ConfirmActionModal";
import { FlociNote } from "./FlociNote";
import { serviceRequest } from "../api/client";

export interface PasswordPolicy {
  MinimumPasswordLength?: number;
  RequireSymbols?: boolean;
  RequireNumbers?: boolean;
  RequireUppercaseCharacters?: boolean;
  RequireLowercaseCharacters?: boolean;
  AllowUsersToChangePassword?: boolean;
  ExpirePasswords?: boolean;
  MaxPasswordAge?: number;
  PasswordReusePrevention?: number;
  HardExpiry?: boolean;
}

type Form = {
  minimum_password_length: string;
  require_uppercase_characters: boolean;
  require_lowercase_characters: boolean;
  require_numbers: boolean;
  require_symbols: boolean;
  expire: boolean;
  max_password_age: string;
  reuse: boolean;
  password_reuse_prevention: string;
  allow_users_to_change_password: boolean;
  hard_expiry: boolean;
};

const formFrom = (policy: PasswordPolicy | null): Form => ({
  minimum_password_length: String(policy?.MinimumPasswordLength ?? 8),
  require_uppercase_characters: !!policy?.RequireUppercaseCharacters,
  require_lowercase_characters: !!policy?.RequireLowercaseCharacters,
  require_numbers: !!policy?.RequireNumbers,
  require_symbols: !!policy?.RequireSymbols,
  expire: !!policy?.MaxPasswordAge,
  max_password_age: String(policy?.MaxPasswordAge || 90),
  reuse: !!policy?.PasswordReusePrevention,
  password_reuse_prevention: String(policy?.PasswordReusePrevention || 5),
  allow_users_to_change_password: policy ? !!policy.AllowUsersToChangePassword : true,
  hard_expiry: !!policy?.HardExpiry,
});

const yes = (value?: boolean) => (value ? "Yes" : "No");

/** Account password policy: shown, edited and removed (back to the IAM default). */
export function PasswordPolicyPanel() {
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const [policy, setPolicy] = useState<PasswordPolicy | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<Form>(formFrom(null));
  const [busy, setBusy] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const data = await serviceRequest("iam", "password-policy/");
      if (!alive.current) return;
      setPolicy(data.policy || null);
      setError("");
    } catch (err: any) {
      if (alive.current) setError(err.message || "Unable to load the password policy");
    } finally {
      if (alive.current) setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const data = await serviceRequest("iam", "password-policy/", "PUT", {
        minimum_password_length: Number(form.minimum_password_length),
        require_uppercase_characters: form.require_uppercase_characters,
        require_lowercase_characters: form.require_lowercase_characters,
        require_numbers: form.require_numbers,
        require_symbols: form.require_symbols,
        max_password_age: form.expire ? Number(form.max_password_age) : 0,
        password_reuse_prevention: form.reuse ? Number(form.password_reuse_prevention) : 0,
        allow_users_to_change_password: form.allow_users_to_change_password,
        hard_expiry: form.expire && form.hard_expiry,
      });
      setPolicy(data.policy || null);
      setEditing(false);
      setNotice("Password policy saved.");
    } catch (err: any) {
      setError(err.message || "Unable to save the password policy");
    } finally {
      setBusy(false);
    }
  };

  const check = (key: keyof Form, label: string, description?: string) => (
    <Checkbox checked={form[key] as boolean} description={description} onChange={({ detail }) => setForm({ ...form, [key]: detail.checked })}>
      {label}
    </Checkbox>
  );

  return (
    <Container
      header={
        <Header
          variant="h2"
          description="Rules every IAM user's console password must follow."
          actions={
            !editing && (
              <SpaceBetween direction="horizontal" size="xs">
                <Button disabled={!policy || loading} onClick={() => setDeleteOpen(true)}>Delete</Button>
                <Button variant="primary" disabled={loading} onClick={() => { setForm(formFrom(policy)); setError(""); setEditing(true); }}>Edit</Button>
              </SpaceBetween>
            )
          }
        >
          Password policy
        </Header>
      }
    >
      <SpaceBetween size="m">
        <FlociNote>
          Floci enforces the length, character and reuse rules: creating or
          changing a login profile with a password that breaks them fails with
          PasswordPolicyViolation. Expiration and the change-password setting are
          stored only, because nobody signs in to Floci with these passwords.
        </FlociNote>
        {notice && <Alert type="success" dismissible onDismiss={() => setNotice("")}>{notice}</Alert>}
        {error && <Alert type="error">{error}</Alert>}
        {loading ? (
          <StatusIndicator type="loading">Loading password policy</StatusIndicator>
        ) : editing ? (
          <SpaceBetween size="m">
            <FormField label="Minimum password length" constraintText="6 to 128 characters.">
              <Input type="number" value={form.minimum_password_length} onChange={({ detail }) => setForm({ ...form, minimum_password_length: detail.value })} />
            </FormField>
            <FormField label="Password strength">
              <SpaceBetween size="xxs">
                {check("require_uppercase_characters", "Require at least one uppercase letter (A-Z)")}
                {check("require_lowercase_characters", "Require at least one lowercase letter (a-z)")}
                {check("require_numbers", "Require at least one number")}
                {check("require_symbols", "Require at least one non-alphanumeric character")}
              </SpaceBetween>
            </FormField>
            <FormField label="Other requirements">
              <SpaceBetween size="xs">
                {check("expire", "Turn on password expiration")}
                {form.expire && (
                  <FormField label="Expiration period (days)" constraintText="1 to 1,095 days.">
                    <Input type="number" value={form.max_password_age} onChange={({ detail }) => setForm({ ...form, max_password_age: detail.value })} />
                  </FormField>
                )}
                {form.expire && check("hard_expiry", "Password expiration requires administrator reset")}
                {check("allow_users_to_change_password", "Allow users to change their own password")}
                {check("reuse", "Prevent password reuse")}
                {form.reuse && (
                  <FormField label="Remember this many passwords" constraintText="1 to 24.">
                    <Input type="number" value={form.password_reuse_prevention} onChange={({ detail }) => setForm({ ...form, password_reuse_prevention: detail.value })} />
                  </FormField>
                )}
              </SpaceBetween>
            </FormField>
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={busy} onClick={() => setEditing(false)}>Cancel</Button>
              <Button variant="primary" loading={busy} onClick={save}>Save changes</Button>
            </SpaceBetween>
          </SpaceBetween>
        ) : policy ? (
          <ColumnLayout columns={3} variant="text-grid">
            <div><Box variant="awsui-key-label">Minimum length</Box><div>{policy.MinimumPasswordLength ?? "-"} characters</div></div>
            <div><Box variant="awsui-key-label">Uppercase letter</Box><div>{yes(policy.RequireUppercaseCharacters)}</div></div>
            <div><Box variant="awsui-key-label">Lowercase letter</Box><div>{yes(policy.RequireLowercaseCharacters)}</div></div>
            <div><Box variant="awsui-key-label">Number</Box><div>{yes(policy.RequireNumbers)}</div></div>
            <div><Box variant="awsui-key-label">Symbol</Box><div>{yes(policy.RequireSymbols)}</div></div>
            <div><Box variant="awsui-key-label">Expiration</Box><div>{policy.MaxPasswordAge ? `${policy.MaxPasswordAge} days${policy.HardExpiry ? ", administrator reset" : ""}` : "Never"}</div></div>
            <div><Box variant="awsui-key-label">Password reuse</Box><div>{policy.PasswordReusePrevention ? `Last ${policy.PasswordReusePrevention} remembered` : "Allowed"}</div></div>
            <div><Box variant="awsui-key-label">Users can change password</Box><div>{yes(policy.AllowUsersToChangePassword)}</div></div>
          </ColumnLayout>
        ) : (
          <Box>
            This account uses the IAM default policy: at least 8 characters with
            at least three of uppercase, lowercase, numbers and symbols. Floci
            checks no rules until you set a policy.
          </Box>
        )}
      </SpaceBetween>
      <ConfirmActionModal
        visible={deleteOpen}
        header="Delete password policy"
        confirmLabel="Delete policy"
        loading={busy}
        onDismiss={() => setDeleteOpen(false)}
        onConfirm={async () => {
          setBusy(true);
          try {
            await serviceRequest("iam", "password-policy/", "DELETE");
            setPolicy(null);
            setNotice("Password policy deleted. The account uses the IAM default.");
            setDeleteOpen(false);
          } catch (err: any) {
            setError(err.message || "Unable to delete the password policy");
            setDeleteOpen(false);
          } finally {
            setBusy(false);
          }
        }}
      >
        New passwords are no longer checked against these rules.
      </ConfirmActionModal>
    </Container>
  );
}

type ReportRow = Record<string, string>;
const REPORT_COLUMNS: [string, string][] = [
  ["user", "User"],
  ["password_enabled", "Password"],
  ["password_last_changed", "Password last changed"],
  ["mfa_active", "MFA"],
  ["access_key_1_active", "Access key 1 active"],
  ["access_key_1_last_rotated", "Access key 1 last rotated"],
  ["access_key_2_active", "Access key 2 active"],
  ["user_creation_time", "Created"],
];

/** IAM credential report: generate, show the CSV as a table, download it. */
export function CredentialReportPanel() {
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const [report, setReport] = useState<{ generated_time?: string; rows: ReportRow[]; csv: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const data = await serviceRequest("iam", "credential-report/");
      if (!alive.current) return;
      setReport(data.report ? { ...data.report, rows: Array.isArray(data.report.rows) ? data.report.rows : [] } : null);
      setError("");
    } catch (err: any) {
      if (alive.current) setError(err.message || "Unable to load the credential report");
    } finally {
      if (alive.current) setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const generate = async () => {
    setBusy(true);
    setError("");
    try {
      // Generation is asynchronous in AWS; ask until IAM reports COMPLETE.
      for (let attempt = 0; attempt < 10; attempt++) {
        const state = await serviceRequest("iam", "credential-report/", "POST");
        if (state.state === "COMPLETE") break;
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      await load();
    } catch (err: any) {
      setError(err.message || "Unable to generate the credential report");
    } finally {
      setBusy(false);
    }
  };

  const download = () => {
    if (!report) return;
    const url = URL.createObjectURL(new Blob([report.csv], { type: "text/csv" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "credential-report.csv";
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <SpaceBetween size="m">
      {error && <Alert type="error">{error}</Alert>}
      <Table
        loading={loading}
        loadingText="Loading credential report"
        items={report?.rows || []}
        trackBy="user"
        header={
          <Header
            variant="h2"
            counter={report ? `(${report.rows.length})` : undefined}
            description={
              report?.generated_time
                ? `Generated ${new Date(report.generated_time).toLocaleString()}. IAM reuses a report for 4 hours.`
                : "Lists every user with the state of their password, MFA and access keys."
            }
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button disabled={!report} iconName="download" onClick={download}>Download report</Button>
                <Button variant="primary" loading={busy} onClick={generate}>Generate report</Button>
              </SpaceBetween>
            }
          >
            Credential report
          </Header>
        }
        columnDefinitions={REPORT_COLUMNS.map(([key, label]) => ({ id: key, header: label, cell: (row: ReportRow) => row[key] || "-" }))}
        empty={<Box textAlign="center">{report ? "The report lists no users." : "No report yet. Generate one to see every user's credentials."}</Box>}
      />
    </SpaceBetween>
  );
}
