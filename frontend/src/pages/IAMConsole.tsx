import { PolicySummary } from '../components/PolicySummary';
import React, { useState, useEffect } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Table from '../components/ResourceTable';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import TextFilter from '@cloudscape-design/components/text-filter';
import Tabs from '@cloudscape-design/components/tabs';
import Modal from '@cloudscape-design/components/modal';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Textarea from '@cloudscape-design/components/textarea';
import Select from '@cloudscape-design/components/select';
import Autosuggest from '@cloudscape-design/components/autosuggest';
import Checkbox from '@cloudscape-design/components/checkbox';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Grid from '@cloudscape-design/components/grid';
import Box from '@cloudscape-design/components/box';
import Alert from '@cloudscape-design/components/alert';
import ConfirmActionModal from '../components/ConfirmActionModal';
import { formatAwsDate } from '../format';
import {
  fetchServiceInventory,
  createIamUser,
  deleteIamUser,
  createIamRole,
  deleteIamRole,
  createIamGroup,
  deleteIamGroup,
  simulateIamPolicy,
  createIamPolicy,
  deleteIamPolicy,
  fetchIamPolicyDocument,
  fetchIamInlinePolicyDocument,
  createIamUserAccessKey,
  deleteIamUserAccessKey,
  updateIamAccessKeyState,
  attachIamPolicy,
  detachIamPolicy,
  addUserToIamGroup,
  removeUserFromIamGroup,
} from '../api/client';

type PrincipalType = 'user' | 'role' | 'group';

interface PolicyRef { PolicyName: string; PolicyArn: string }

interface AccessKey { AccessKeyId: string; Status: string; CreateDate?: string; LastUsed?: { LastUsedDate?: string; ServiceName?: string; Region?: string } }

interface UserItem {
  UserName: string;
  Arn: string;
  CreateDate?: string;
  Path?: string;
  AttachedPolicies: PolicyRef[];
  InlinePolicies: string[];
  Groups: string[];
  AccessKeys: AccessKey[];
  ConsoleAccess: boolean;
  PermissionsBoundary?: string;
  Tags: Array<{ Key: string; Value: string }>;
}

interface RoleItem {
  RoleName: string;
  Arn: string;
  CreateDate?: string;
  Path?: string;
  AssumeRolePolicyDocument?: any;
  Description?: string;
  MaxSessionDuration?: number;
  AttachedPolicies: PolicyRef[];
  InlinePolicies: string[];
  PermissionsBoundary?: string;
  Tags: Array<{ Key: string; Value: string }>;
}

interface GroupItem {
  GroupName: string;
  Arn: string;
  CreateDate?: string;
  Users: string[];
  AttachedPolicies: PolicyRef[];
  InlinePolicies: string[];
}

interface PolicyItem {
  PolicyName: string;
  Arn: string;
  Path?: string;
  DefaultVersionId?: string;
  AttachmentCount?: number;
  CreateDate?: string;
  UpdateDate?: string;
  Description?: string;
}

const POLICY_TEMPLATES: Record<string, { label: string; doc: any }> = {
  s3read: { label: 'S3 read-only for one bucket', doc: { Version: '2012-10-17', Statement: [{ Effect: 'Allow', Action: ['s3:GetObject', 's3:ListBucket'], Resource: ['arn:aws:s3:::example-bucket', 'arn:aws:s3:::example-bucket/*'] }] } },
  sqssend: { label: 'SQS send to one queue', doc: { Version: '2012-10-17', Statement: [{ Effect: 'Allow', Action: 'sqs:SendMessage', Resource: 'arn:aws:sqs:us-east-1:000000000000:example-queue' }] } },
  dynamoread: { label: 'DynamoDB read one table', doc: { Version: '2012-10-17', Statement: [{ Effect: 'Allow', Action: ['dynamodb:GetItem', 'dynamodb:Query'], Resource: 'arn:aws:dynamodb:us-east-1:000000000000:table/example-table' }] } },
  empty: { label: 'Empty policy', doc: { Version: '2012-10-17', Statement: [] } },
};

const TRUST_TEMPLATES: Record<string, { label: string; doc: any }> = Object.fromEntries(
  [
    ['lambda', 'Lambda', 'lambda.amazonaws.com'],
    ['ec2', 'EC2', 'ec2.amazonaws.com'],
    ['ecs', 'Elastic Container Service (ECS tasks)', 'ecs-tasks.amazonaws.com'],
    ['apigateway', 'API Gateway', 'apigateway.amazonaws.com'],
    ['states', 'Step Functions', 'states.amazonaws.com'],
  ].map(([key, label, service]) => [key, { label: `AWS service: ${label}`, doc: { Version: '2012-10-17', Statement: [{ Effect: 'Allow', Principal: { Service: service }, Action: 'sts:AssumeRole' }] } }]),
);

const policyRefs = (items: any[] | undefined): PolicyRef[] => (items || []).map(item => ({
  PolicyName: item.PolicyName || item.name || String(item.arn || item.PolicyArn || '').split('/').pop(),
  PolicyArn: item.PolicyArn || item.arn,
}));

const parseDocument = (value: unknown) => {
  if (typeof value !== 'string') return value;
  try {
    return JSON.parse(decodeURIComponent(value));
  } catch {
    try { return JSON.parse(value); } catch { return value; }
  }
};

/** "Trusted entities" column text, as the Roles list shows it. */
export function trustedEntities(document: any): string {
  const doc = parseDocument(document) as any;
  const statements = Array.isArray(doc?.Statement) ? doc.Statement : doc?.Statement ? [doc.Statement] : [];
  const entities = statements.flatMap((statement: any) => {
    const principal = statement.Principal;
    if (!principal) return [];
    if (principal === '*') return ['Anyone'];
    return Object.entries(principal).flatMap(([kind, value]) => (Array.isArray(value) ? value : [value]).map(item => {
      if (kind === 'Service') return `AWS Service: ${String(item).replace('.amazonaws.com', '')}`;
      if (kind === 'Federated') return `Identity Provider: ${item}`;
      const account = String(item).match(/arn:aws:iam::(\d+):/)?.[1] || String(item);
      return `Account: ${account}`;
    }));
  });
  return [...new Set(entities)].join(', ') || '-';
}

const sessionHours = (seconds?: number) => seconds ? `${seconds / 3600} hour${seconds === 3600 ? '' : 's'}` : '-';

interface IAMConsoleProps {
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const IAMConsole: React.FC<IAMConsoleProps> = ({ activeTab, onTabChange }) => {
  const [users, setUsers] = useState<UserItem[]>([]);
  const [roles, setRoles] = useState<RoleItem[]>([]);
  const [groups, setGroups] = useState<GroupItem[]>([]);
  const [policies, setPolicies] = useState<PolicyItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error' | 'info'; text: string } | null>(null);
  const [selectedTabId, setSelectedTabId] = useState(activeTab || 'users');

  useEffect(() => {
    if (activeTab) setSelectedTabId(activeTab);
  }, [activeTab]);

  const [selectedUsers, setSelectedUsers] = useState<UserItem[]>([]);
  const [selectedRoles, setSelectedRoles] = useState<RoleItem[]>([]);
  const [selectedGroups, setSelectedGroups] = useState<GroupItem[]>([]);
  const [selectedPolicies, setSelectedPolicies] = useState<PolicyItem[]>([]);
  const [userFilter, setUserFilter] = useState('');
  const [roleFilter, setRoleFilter] = useState('');
  const [policyFilter, setPolicyFilter] = useState('');

  const [newAccessKeyResult, setNewAccessKeyResult] = useState<{ AccessKeyId: string; SecretAccessKey?: string } | null>(null);

  // Policy documents fetched on demand
  const [policyDocument, setPolicyDocument] = useState<{ arn: string; document: any; version?: string; error?: string } | null>(null);
  const [inlineDocument, setInlineDocument] = useState<{ title: string; document: any } | null>(null);

  // Attach policy
  const [attachTarget, setAttachTarget] = useState<{ type: PrincipalType; name: string } | null>(null);
  const [attachPolicyArn, setAttachPolicyArn] = useState('');
  const [attachingPolicy, setAttachingPolicy] = useState(false);

  // Group membership
  const [addToGroupOpen, setAddToGroupOpen] = useState(false);
  const [selectedGroupName, setSelectedGroupName] = useState('');
  const [addingToGroup, setAddingToGroup] = useState(false);

  // Create dialogs
  const [createUserOpen, setCreateUserOpen] = useState(false);
  const [newUserName, setNewUserName] = useState('');
  const [addBaselinePolicy, setAddBaselinePolicy] = useState(true);
  const [creatingUser, setCreatingUser] = useState(false);
  const [createRoleOpen, setCreateRoleOpen] = useState(false);
  const [newRoleName, setNewRoleName] = useState('');
  const [selectedTemplateKey, setSelectedTemplateKey] = useState({ label: TRUST_TEMPLATES.lambda.label, value: 'lambda' });
  const [customTrustDoc, setCustomTrustDoc] = useState(JSON.stringify(TRUST_TEMPLATES.lambda.doc, null, 2));
  const [creatingRole, setCreatingRole] = useState(false);
  const [createPolicyOpen, setCreatePolicyOpen] = useState(false);
  const [newPolicyName, setNewPolicyName] = useState('');
  const [policyTemplateKey, setPolicyTemplateKey] = useState({ label: POLICY_TEMPLATES.s3read.label, value: 's3read' });
  const [customPolicyDoc, setCustomPolicyDoc] = useState(JSON.stringify(POLICY_TEMPLATES.s3read.doc, null, 2));
  const [policyDescription, setPolicyDescription] = useState('');
  const [creatingPolicy, setCreatingPolicy] = useState(false);
  const [createGroupOpen, setCreateGroupOpen] = useState(false);
  const [newGroupName, setNewGroupName] = useState('');
  const [creatingGroup, setCreatingGroup] = useState(false);

  // Destructive actions
  const [pendingDelete, setPendingDelete] = useState<{ kind: 'user' | 'role' | 'group' | 'policy' | 'access key'; name: string; arn?: string } | null>(null);
  const [deleting, setDeleting] = useState(false);

  // Policy simulator
  const [simPrincipal, setSimPrincipal] = useState<{ label?: string; value?: string } | null>(null);
  const [simActions, setSimActions] = useState('s3:GetObject\nsqs:SendMessage');
  const [simResourceArn, setSimResourceArn] = useState('*');
  const [simResults, setSimResults] = useState<any[]>([]);
  const [simMessage, setSimMessage] = useState('');
  const [simulating, setSimulating] = useState(false);

  const loadData = async () => {
    setLoading(true);
    setLoadError('');
    try {
      const data = await fetchServiceInventory('iam');
      if (data.error) throw new Error(data.error);
      const groupList: GroupItem[] = (data.groups || data.Groups || []).map((g: any) => ({
        GroupName: g.GroupName || g.name,
        Arn: g.Arn || g.arn,
        CreateDate: g.CreateDate || g.created,
        Users: (g.Users || g.users || []).map((u: any) => (typeof u === 'string' ? u : u.UserName)),
        AttachedPolicies: policyRefs(g.AttachedPolicies || g.attached_policies),
        InlinePolicies: g.InlinePolicies || g.inline_policies || [],
      }));
      setGroups(groupList);
      const uList: UserItem[] = (data.users || data.Users || []).map((u: any) => {
        const name = u.UserName || u.name;
        return {
          UserName: name,
          Arn: u.Arn || u.arn,
          CreateDate: u.CreateDate || u.created,
          Path: u.Path || u.path,
          AttachedPolicies: policyRefs(u.AttachedPolicies || u.attached_policies),
          InlinePolicies: u.InlinePolicies || u.inline_policies || [],
          Groups: u.groups || groupList.filter(group => group.Users.includes(name)).map(group => group.GroupName),
          AccessKeys: (u.access_keys || []).map((key: any) => ({ AccessKeyId: key.id || key.AccessKeyId, Status: key.status || key.Status, CreateDate: key.created || key.CreateDate, LastUsed: key.last_used })),
          ConsoleAccess: Boolean(u.login_profile),
          PermissionsBoundary: u.permissions_boundary?.PermissionsBoundaryArn || u.PermissionsBoundary?.PermissionsBoundaryArn,
          Tags: u.Tags || u.tags || [],
        };
      });
      setUsers(uList);
      setSelectedUsers(previous => {
        const kept = previous.length ? uList.filter(user => user.UserName === previous[0].UserName) : [];
        return kept.length ? kept : uList.slice(0, 1);
      });

      const rList: RoleItem[] = (data.roles || data.Roles || []).map((r: any) => ({
        RoleName: r.RoleName || r.name,
        Arn: r.Arn || r.arn,
        CreateDate: r.CreateDate || r.created,
        Path: r.Path || r.path,
        AssumeRolePolicyDocument: parseDocument(r.AssumeRolePolicyDocument || r.trust_policy || r.assume_role_policy_document),
        Description: r.Description || r.description || undefined,
        MaxSessionDuration: r.MaxSessionDuration || r.max_session_duration,
        AttachedPolicies: policyRefs(r.AttachedPolicies || r.attached_policies),
        InlinePolicies: r.InlinePolicies || r.inline_policies || [],
        PermissionsBoundary: r.permissions_boundary?.PermissionsBoundaryArn || r.PermissionsBoundary?.PermissionsBoundaryArn,
        Tags: r.Tags || r.tags || [],
      }));
      setRoles(rList);
      setSelectedRoles(previous => {
        const kept = previous.length ? rList.filter(role => role.RoleName === previous[0].RoleName) : [];
        return kept.length ? kept : rList.slice(0, 1);
      });
      setSelectedGroups(previous => {
        const kept = previous.length ? groupList.filter(group => group.GroupName === previous[0].GroupName) : [];
        return kept.length ? kept : groupList.slice(0, 1);
      });

      const pList: PolicyItem[] = (data.policies || data.Policies || []).map((p: any) => ({
        PolicyName: p.PolicyName || p.name,
        Arn: p.Arn || p.arn,
        Path: p.Path || p.path,
        DefaultVersionId: p.DefaultVersionId || p.default_version,
        AttachmentCount: p.AttachmentCount ?? p.attachment_count,
        CreateDate: p.CreateDate || p.created,
        UpdateDate: p.UpdateDate || p.updated,
        Description: p.Description || p.description || undefined,
      }));
      setPolicies(pList);
      setSelectedPolicies(previous => {
        const kept = previous.length ? pList.filter(policy => policy.Arn === previous[0].Arn) : [];
        return kept.length ? kept : pList.slice(0, 1);
      });
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Unable to load IAM resources');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const activeUser = selectedUsers[0] || null;
  const activeRole = selectedRoles[0] || null;
  const activeGroup = selectedGroups[0] || null;
  const activePolicy = selectedPolicies[0] || null;

  useEffect(() => {
    if (!activePolicy) { setPolicyDocument(null); return; }
    let cancelled = false;
    setPolicyDocument({ arn: activePolicy.Arn, document: null });
    fetchIamPolicyDocument(activePolicy.Arn)
      .then(result => { if (!cancelled) setPolicyDocument({ arn: activePolicy.Arn, document: parseDocument(result.document), version: result.default_version }); })
      .catch(err => { if (!cancelled) setPolicyDocument({ arn: activePolicy.Arn, document: null, error: err instanceof Error ? err.message : 'Failed to read policy' }); });
    return () => { cancelled = true; };
  }, [activePolicy?.Arn]);

  const run = async (work: () => Promise<string>, fallback: string) => {
    setActionMessage(null);
    try {
      setActionMessage({ type: 'success', text: await work() });
      return true;
    } catch (err) {
      setActionMessage({ type: 'error', text: err instanceof Error ? err.message : fallback });
      return false;
    }
  };

  const handleCreateUser = async () => {
    const name = newUserName.trim();
    if (!name) return;
    setCreatingUser(true);
    const ok = await run(async () => {
      const result = await createIamUser(name, addBaselinePolicy);
      return result.baseline_policy
        ? `User ${name} created. The dashboard added inline policy ${result.baseline_policy} (sts:GetCallerIdentity) so you can switch to this user.`
        : `User ${name} created.`;
    }, 'Failed to create user');
    setCreatingUser(false);
    if (ok) {
      setCreateUserOpen(false);
      setNewUserName('');
      await loadData();
    }
  };

  const handleConfirmDelete = async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    const { kind, name, arn } = pendingDelete;
    const ok = await run(async () => {
      if (kind === 'user') await deleteIamUser(name);
      if (kind === 'role') await deleteIamRole(name);
      if (kind === 'group') await deleteIamGroup(name);
      if (kind === 'policy' && arn) await deleteIamPolicy(arn);
      if (kind === 'access key' && activeUser) await deleteIamUserAccessKey(activeUser.UserName, name);
      return `${kind.charAt(0).toUpperCase()}${kind.slice(1)} ${name} deleted.`;
    }, `Failed to delete ${kind}`);
    setDeleting(false);
    setPendingDelete(null);
    if (ok) {
      if (kind === 'access key') setNewAccessKeyResult(null);
      await loadData();
    }
  };

  const handleCreateRole = async () => {
    const name = newRoleName.trim();
    if (!name) return;
    let parsedDoc: unknown;
    try {
      parsedDoc = JSON.parse(customTrustDoc);
    } catch {
      setActionMessage({ type: 'error', text: 'Trust policy is not valid JSON.' });
      return;
    }
    setCreatingRole(true);
    const ok = await run(async () => {
      await createIamRole(name, selectedTemplateKey.value, parsedDoc);
      return `Role ${name} created.`;
    }, 'Failed to create role');
    setCreatingRole(false);
    if (ok) {
      setCreateRoleOpen(false);
      setNewRoleName('');
      await loadData();
    }
  };

  const handleCreatePolicy = async () => {
    const name = newPolicyName.trim();
    if (!name) return;
    setCreatingPolicy(true);
    const ok = await run(async () => {
      await createIamPolicy(name, customPolicyDoc, policyDescription.trim());
      return `Policy ${name} created.`;
    }, 'Failed to create policy');
    setCreatingPolicy(false);
    if (ok) {
      setCreatePolicyOpen(false);
      setNewPolicyName('');
      setPolicyDescription('');
      await loadData();
    }
  };

  const handleCreateAccessKey = async () => {
    if (!activeUser) return;
    if (activeUser.AccessKeys.length >= 2) {
      setActionMessage({ type: 'error', text: 'A user can have at most two access keys. Delete one before creating another.' });
      return;
    }
    await run(async () => {
      const res = await createIamUserAccessKey(activeUser.UserName);
      const key = res.access_key || res.AccessKey || res;
      setNewAccessKeyResult({ AccessKeyId: key.AccessKeyId || key.access_key_id, SecretAccessKey: key.SecretAccessKey || key.secret_access_key });
      return `Access key created for ${activeUser.UserName}.`;
    }, 'Failed to create access key');
    await loadData();
  };

  const handleToggleAccessKeyState = async (accessKeyId: string, currentStatus: string) => {
    if (!activeUser) return;
    const newStatus = currentStatus === 'Active' ? 'Inactive' : 'Active';
    await run(async () => {
      await updateIamAccessKeyState(activeUser.UserName, accessKeyId, newStatus);
      return `Access key ${accessKeyId} is now ${newStatus}.`;
    }, 'Failed to update access key');
    await loadData();
  };

  const handleAttachPolicy = async () => {
    if (!attachTarget || !attachPolicyArn.trim()) return;
    setAttachingPolicy(true);
    const ok = await run(async () => {
      await attachIamPolicy(attachTarget.type, attachTarget.name, attachPolicyArn.trim());
      return `Policy attached to ${attachTarget.type} ${attachTarget.name}.`;
    }, 'Failed to attach policy');
    setAttachingPolicy(false);
    if (ok) {
      setAttachTarget(null);
      setAttachPolicyArn('');
      await loadData();
    }
  };

  const handleDetachPolicy = async (type: PrincipalType, name: string, policyArn: string) => {
    await run(async () => {
      await detachIamPolicy(type, name, policyArn);
      return `Policy detached from ${type} ${name}.`;
    }, 'Failed to detach policy');
    await loadData();
  };

  const showInlinePolicy = async (type: PrincipalType, principal: string, policyName: string) => {
    try {
      const result = await fetchIamInlinePolicyDocument(type, principal, policyName);
      setInlineDocument({ title: `${policyName} (inline policy on ${type} ${principal})`, document: parseDocument(result.document) });
    } catch (err) {
      setActionMessage({ type: 'error', text: err instanceof Error ? err.message : 'Failed to read inline policy' });
    }
  };

  const handleCreateGroup = async () => {
    const name = newGroupName.trim();
    if (!name) return;
    setCreatingGroup(true);
    const ok = await run(async () => {
      await createIamGroup(name);
      return `User group ${name} created.`;
    }, 'Failed to create group');
    setCreatingGroup(false);
    if (ok) {
      setCreateGroupOpen(false);
      setNewGroupName('');
      await loadData();
    }
  };

  const handleAddUserToGroup = async () => {
    if (!activeUser || !selectedGroupName) return;
    setAddingToGroup(true);
    const ok = await run(async () => {
      await addUserToIamGroup(selectedGroupName, activeUser.UserName);
      return `${activeUser.UserName} added to ${selectedGroupName}.`;
    }, 'Failed to add user to group');
    setAddingToGroup(false);
    if (ok) {
      setAddToGroupOpen(false);
      await loadData();
    }
  };

  const handleRemoveFromGroup = async (groupName: string, userName: string) => {
    await run(async () => {
      await removeUserFromIamGroup(groupName, userName);
      return `${userName} removed from ${groupName}.`;
    }, 'Failed to remove user from group');
    await loadData();
  };

  const handleRunSimulation = async () => {
    const actions = simActions.split(/[\n,]/).map(a => a.trim()).filter(Boolean);
    if (!actions.length || !simPrincipal?.value) return;
    setSimulating(true);
    setSimMessage('');
    try {
      const res = await simulateIamPolicy(simPrincipal.value, actions, [simResourceArn.trim() || '*']);
      if (res.supported === false) {
        setSimResults([]);
        setSimMessage(res.message || 'SimulatePrincipalPolicy is not available in this Floci runtime.');
      } else {
        setSimResults(res.evaluations || res.EvaluationResults || []);
      }
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Policy simulation failed' });
    } finally {
      setSimulating(false);
    }
  };

  const filteredUsers = users.filter(u => u.UserName.toLowerCase().includes(userFilter.toLowerCase()));
  const filteredRoles = roles.filter(r => r.RoleName.toLowerCase().includes(roleFilter.toLowerCase()));
  const filteredPolicies = policies.filter(p => p.PolicyName.toLowerCase().includes(policyFilter.toLowerCase()));

  const permissionsTable = (type: PrincipalType, name: string, attached: PolicyRef[], inline: string[]) => {
    const rows = [
      ...attached.map(policy => ({ id: policy.PolicyArn, name: policy.PolicyName, type: policy.PolicyArn?.startsWith('arn:aws:iam::aws:') ? 'AWS managed' : 'Customer managed', arn: policy.PolicyArn, inline: false })),
      ...inline.map(policyName => ({ id: `inline:${policyName}`, name: policyName, type: 'Customer inline', arn: '', inline: true })),
    ];
    return (
      <Table
        header={
          <Header
            variant="h3"
            counter={`(${rows.length})`}
            description="Managed policies are attached by ARN; inline policies are embedded in this identity."
            actions={<Button onClick={() => { setAttachTarget({ type, name }); setAttachPolicyArn(''); }}>Add permissions</Button>}
          >
            Permissions policies
          </Header>
        }
        columnDefinitions={[
          { id: 'policy', header: 'Policy name', cell: row => row.inline ? <Button variant="inline-link" onClick={() => showInlinePolicy(type, name, row.name)}>{row.name}</Button> : row.name },
          { id: 'type', header: 'Type', cell: row => row.type, width: 170 },
          { id: 'arn', header: 'ARN', cell: row => row.arn || '-' },
          { id: 'act', header: 'Actions', cell: row => row.inline ? '-' : <Button variant="inline-link" onClick={() => handleDetachPolicy(type, name, row.arn)}>Detach</Button>, width: 110 },
        ]}
        items={rows}
        trackBy="id"
        empty={<Box textAlign="center">No permissions policies.</Box>}
      />
    );
  };

  const tagsTable = (tags: Array<{ Key: string; Value: string }>) => (
    <Table
      columnDefinitions={[{ id: 'k', header: 'Key', cell: tag => tag.Key }, { id: 'v', header: 'Value', cell: tag => tag.Value || '-' }]}
      items={tags}
      trackBy="Key"
      empty={<Box textAlign="center">No tags associated with the resource.</Box>}
    />
  );

  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header
            variant="h1"
            description="Manage access to AWS resources with users, groups, roles and policies."
            actions={<Button iconName="refresh" onClick={loadData} loading={loading}>Refresh</Button>}
          >
            Identity and Access Management (IAM)
          </Header>
        }
      >
        <SpaceBetween size="m">
          {actionMessage && <Alert type={actionMessage.type} dismissible onDismiss={() => setActionMessage(null)}>{actionMessage.text}</Alert>}
          {loadError && <Alert type="error" header="Unable to load IAM resources">{loadError}</Alert>}
          <KeyValuePairs
            columns={4}
            items={[
              { label: 'User groups', value: groups.length },
              { label: 'Users', value: users.length },
              { label: 'Roles', value: roles.length },
              { label: 'Customer managed policies', value: policies.length },
            ]}
          />
        </SpaceBetween>
      </Container>

      <Tabs
        activeTabId={selectedTabId}
        onChange={({ detail }) => {
          setSelectedTabId(detail.activeTabId);
          onTabChange?.(detail.activeTabId);
        }}
        tabs={[
          {
            label: `User groups (${groups.length})`,
            id: 'groups',
            content: (
              <SpaceBetween size="l">
                <Table
                  header={
                    <Header
                      variant="h2"
                      counter={`(${groups.length})`}
                      description="A user group is a collection of IAM users. Use groups to specify permissions for a collection of users."
                      actions={
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button disabled={!activeGroup} onClick={() => activeGroup && setPendingDelete({ kind: 'group', name: activeGroup.GroupName })}>Delete</Button>
                          <Button variant="primary" onClick={() => setCreateGroupOpen(true)}>Create group</Button>
                        </SpaceBetween>
                      }
                    >
                      User groups
                    </Header>
                  }
                  columnDefinitions={[
                    { id: 'name', header: 'Group name', cell: g => <Button variant="inline-link" onClick={() => setSelectedGroups([g])}>{g.GroupName}</Button> },
                    { id: 'users', header: 'Users', cell: g => g.Users.length, width: 100 },
                    { id: 'perms', header: 'Permissions', cell: g => (g.AttachedPolicies.length + g.InlinePolicies.length ? 'Defined' : 'Not defined'), width: 140 },
                    { id: 'created', header: 'Creation time', cell: g => formatAwsDate(g.CreateDate), width: 280 },
                  ]}
                  items={groups}
                  selectionType="single"
                  selectedItems={selectedGroups}
                  onSelectionChange={({ detail }) => setSelectedGroups(detail.selectedItems)}
                  trackBy="GroupName"
                  empty={<Box textAlign="center">No user groups.</Box>}
                />
                {activeGroup && (
                  <Container header={<Header variant="h2" description={activeGroup.Arn}>Group: {activeGroup.GroupName}</Header>}>
                    <Tabs tabs={[
                      {
                        id: 'group-users', label: `Users (${activeGroup.Users.length})`, content: (
                          <Table
                            columnDefinitions={[
                              { id: 'u', header: 'User name', cell: (u: string) => u },
                              { id: 'act', header: 'Actions', cell: (u: string) => <Button variant="inline-link" onClick={() => handleRemoveFromGroup(activeGroup.GroupName, u)}>Remove</Button>, width: 120 },
                            ]}
                            items={activeGroup.Users}
                            trackBy={(u: string) => u}
                            empty={<Box textAlign="center">No users in this group.</Box>}
                          />
                        ),
                      },
                      { id: 'group-perms', label: 'Permissions', content: permissionsTable('group', activeGroup.GroupName, activeGroup.AttachedPolicies, activeGroup.InlinePolicies) },
                    ]} />
                  </Container>
                )}
              </SpaceBetween>
            ),
          },
          {
            label: `Users (${users.length})`,
            id: 'users',
            content: (
              <SpaceBetween size="l">
                <Table
                  header={
                    <Header
                      variant="h2"
                      counter={`(${users.length})`}
                      description="An IAM user is an identity with long-term credentials that is used to interact with AWS in an account."
                      actions={
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button disabled={!activeUser} onClick={() => activeUser && setPendingDelete({ kind: 'user', name: activeUser.UserName })}>Delete</Button>
                          <Button variant="primary" onClick={() => setCreateUserOpen(true)}>Create user</Button>
                        </SpaceBetween>
                      }
                    >
                      Users
                    </Header>
                  }
                  filter={<TextFilter filteringText={userFilter} filteringPlaceholder="Search users" onChange={({ detail }) => setUserFilter(detail.filteringText)} />}
                  columnDefinitions={[
                    { id: 'name', header: 'User name', cell: item => <Button variant="inline-link" onClick={() => setSelectedUsers([item])}>{item.UserName}</Button> },
                    { id: 'path', header: 'Path', cell: item => item.Path || '/', width: 100 },
                    { id: 'groups', header: 'Groups', cell: item => item.Groups.length, width: 100 },
                    { id: 'key', header: 'Access key ID', cell: item => item.AccessKeys.map(key => `${key.Status} - ${key.AccessKeyId}`).join(', ') || '-' },
                    { id: 'console', header: 'Console access', cell: item => (item.ConsoleAccess ? 'Enabled' : 'Disabled'), width: 140 },
                    { id: 'created', header: 'Creation time', cell: item => formatAwsDate(item.CreateDate), width: 280 },
                  ]}
                  items={filteredUsers}
                  loading={loading}
                  selectionType="single"
                  selectedItems={selectedUsers}
                  onSelectionChange={({ detail }) => setSelectedUsers(detail.selectedItems)}
                  trackBy="UserName"
                  empty={<Box textAlign="center">No users.</Box>}
                />

                {activeUser && (
                  <Container header={<Header variant="h2">User: {activeUser.UserName}</Header>}>
                    <SpaceBetween size="l">
                      <KeyValuePairs
                        columns={3}
                        items={[
                          { label: 'ARN', value: activeUser.Arn },
                          { label: 'Created', value: formatAwsDate(activeUser.CreateDate) },
                          { label: 'Console access', value: activeUser.ConsoleAccess ? 'Enabled' : 'Disabled' },
                          { label: 'Permissions boundary', value: activeUser.PermissionsBoundary || 'Not set' },
                        ]}
                      />
                      <Tabs
                        tabs={[
                          { label: 'Permissions', id: 'perms', content: permissionsTable('user', activeUser.UserName, activeUser.AttachedPolicies, activeUser.InlinePolicies) },
                          {
                            label: `Groups (${activeUser.Groups.length})`,
                            id: 'groups',
                            content: (
                              <Table
                                header={<Header variant="h3" actions={<Button disabled={!groups.length} onClick={() => setAddToGroupOpen(true)}>Add user to groups</Button>}>User groups membership</Header>}
                                columnDefinitions={[
                                  { id: 'group', header: 'Group name', cell: (g: string) => g },
                                  { id: 'act', header: 'Actions', cell: (g: string) => <Button variant="inline-link" onClick={() => handleRemoveFromGroup(g, activeUser.UserName)}>Remove</Button>, width: 120 },
                                ]}
                                items={activeUser.Groups}
                                trackBy={(g: string) => g}
                                empty={<Box textAlign="center">This user is not a member of any groups.</Box>}
                              />
                            ),
                          },
                          {
                            label: 'Security credentials',
                            id: 'creds',
                            content: (
                              <SpaceBetween size="m">
                                {newAccessKeyResult && (
                                  <Alert type="warning" header="Retrieve access keys" dismissible onDismiss={() => setNewAccessKeyResult(null)}>
                                    <SpaceBetween size="xs">
                                      <span>This is the only time that the secret access key can be viewed or downloaded. You cannot recover it later.</span>
                                      <KeyValuePairs columns={2} items={[
                                        { label: 'Access key', value: newAccessKeyResult.AccessKeyId },
                                        { label: 'Secret access key', value: newAccessKeyResult.SecretAccessKey || '-' },
                                      ]} />
                                    </SpaceBetween>
                                  </Alert>
                                )}
                                <Table
                                  header={<Header variant="h3" counter={`(${activeUser.AccessKeys.length})`} description="Use access keys to send programmatic calls to AWS. You can have a maximum of two access keys at a time." actions={<Button onClick={handleCreateAccessKey}>Create access key</Button>}>Access keys</Header>}
                                  columnDefinitions={[
                                    { id: 'id', header: 'Access key ID', cell: k => k.AccessKeyId },
                                    { id: 'status', header: 'Status', cell: k => <StatusIndicator type={k.Status === 'Active' ? 'success' : 'stopped'}>{k.Status}</StatusIndicator>, width: 120 },
                                    { id: 'created', header: 'Created', cell: k => formatAwsDate(k.CreateDate), width: 280 },
                                    { id: 'used', header: 'Last used', cell: k => (k.LastUsed?.LastUsedDate ? `${formatAwsDate(k.LastUsed.LastUsedDate)} (${k.LastUsed.ServiceName})` : 'Never'), width: 220 },
                                    {
                                      id: 'actions',
                                      header: 'Actions',
                                      cell: k => (
                                        <SpaceBetween direction="horizontal" size="xs">
                                          <Button variant="inline-link" onClick={() => handleToggleAccessKeyState(k.AccessKeyId, k.Status)}>{k.Status === 'Active' ? 'Deactivate' : 'Activate'}</Button>
                                          <Button variant="inline-link" disabled={k.Status === 'Active'} onClick={() => setPendingDelete({ kind: 'access key', name: k.AccessKeyId })}>Delete</Button>
                                        </SpaceBetween>
                                      ),
                                      width: 200,
                                    },
                                  ]}
                                  items={activeUser.AccessKeys}
                                  trackBy="AccessKeyId"
                                  empty={<Box textAlign="center">No access keys.</Box>}
                                />
                              </SpaceBetween>
                            ),
                          },
                          { label: 'Tags', id: 'tags', content: tagsTable(activeUser.Tags) },
                        ]}
                      />
                    </SpaceBetween>
                  </Container>
                )}
              </SpaceBetween>
            ),
          },
          {
            label: `Roles (${roles.length})`,
            id: 'roles',
            content: (
              <SpaceBetween size="l">
                <Table
                  header={
                    <Header
                      variant="h2"
                      counter={`(${roles.length})`}
                      description="An IAM role is an identity you can create that has specific permissions with credentials that are valid for short durations. Roles can be assumed by entities that you trust."
                      actions={
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button disabled={!activeRole} onClick={() => activeRole && setPendingDelete({ kind: 'role', name: activeRole.RoleName })}>Delete</Button>
                          <Button variant="primary" onClick={() => setCreateRoleOpen(true)}>Create role</Button>
                        </SpaceBetween>
                      }
                    >
                      Roles
                    </Header>
                  }
                  filter={<TextFilter filteringText={roleFilter} filteringPlaceholder="Search roles" onChange={({ detail }) => setRoleFilter(detail.filteringText)} />}
                  columnDefinitions={[
                    { id: 'name', header: 'Role name', cell: item => <Button variant="inline-link" onClick={() => setSelectedRoles([item])}>{item.RoleName}</Button> },
                    { id: 'trusted', header: 'Trusted entities', cell: item => trustedEntities(item.AssumeRolePolicyDocument) },
                    { id: 'desc', header: 'Description', cell: item => item.Description || '-' },
                    { id: 'created', header: 'Creation time', cell: item => formatAwsDate(item.CreateDate), width: 280 },
                  ]}
                  items={filteredRoles}
                  loading={loading}
                  selectionType="single"
                  selectedItems={selectedRoles}
                  onSelectionChange={({ detail }) => setSelectedRoles(detail.selectedItems)}
                  trackBy="RoleName"
                  empty={<Box textAlign="center">No roles.</Box>}
                />

                {activeRole && (
                  <Container header={<Header variant="h2">Role: {activeRole.RoleName}</Header>}>
                    <SpaceBetween size="l">
                      <KeyValuePairs
                        columns={3}
                        items={[
                          { label: 'ARN', value: activeRole.Arn },
                          { label: 'Creation date', value: formatAwsDate(activeRole.CreateDate) },
                          { label: 'Maximum session duration', value: sessionHours(activeRole.MaxSessionDuration) },
                          { label: 'Description', value: activeRole.Description || '-' },
                          { label: 'Permissions boundary', value: activeRole.PermissionsBoundary || 'Not set' },
                          { label: 'Path', value: activeRole.Path || '/' },
                        ]}
                      />
                      <Tabs
                        tabs={[
                          { label: 'Permissions', id: 'role-perms', content: permissionsTable('role', activeRole.RoleName, activeRole.AttachedPolicies, activeRole.InlinePolicies) },
                          {
                            label: 'Trust relationships',
                            id: 'role-trust',
                            content: (
                              <Container header={<Header variant="h3" description="Entities that can assume this role under specified conditions.">Trusted entities</Header>}>
                                <SpaceBetween size="m">
                                  <PolicySummary document={activeRole.AssumeRolePolicyDocument} />
                                  <Textarea
                                    rows={10}
                                    readOnly
                                    ariaLabel="Trust policy"
                                    value={activeRole.AssumeRolePolicyDocument == null ? '' : typeof activeRole.AssumeRolePolicyDocument === 'string' ? activeRole.AssumeRolePolicyDocument : JSON.stringify(activeRole.AssumeRolePolicyDocument, null, 2)}
                                  />
                                </SpaceBetween>
                              </Container>
                            ),
                          },
                          { label: 'Tags', id: 'role-tags', content: tagsTable(activeRole.Tags) },
                        ]}
                      />
                    </SpaceBetween>
                  </Container>
                )}
              </SpaceBetween>
            ),
          },
          {
            label: `Policies (${policies.length})`,
            id: 'policies',
            content: (
              <SpaceBetween size="l">
                <Table
                  header={
                    <Header
                      variant="h2"
                      counter={`(${policies.length})`}
                      description="A policy is an object in AWS that defines permissions. This list shows customer managed policies in the account."
                      actions={
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button disabled={!activePolicy} onClick={() => activePolicy && setPendingDelete({ kind: 'policy', name: activePolicy.PolicyName, arn: activePolicy.Arn })}>Delete</Button>
                          <Button variant="primary" onClick={() => setCreatePolicyOpen(true)}>Create policy</Button>
                        </SpaceBetween>
                      }
                    >
                      Policies
                    </Header>
                  }
                  filter={<TextFilter filteringText={policyFilter} filteringPlaceholder="Filter policies by name" onChange={({ detail }) => setPolicyFilter(detail.filteringText)} />}
                  columnDefinitions={[
                    { id: 'name', header: 'Policy name', cell: item => <Button variant="inline-link" onClick={() => setSelectedPolicies([item])}>{item.PolicyName}</Button> },
                    { id: 'type', header: 'Type', cell: item => (item.Arn?.startsWith('arn:aws:iam::aws:') ? 'AWS managed' : 'Customer managed'), width: 170 },
                    { id: 'attachments', header: 'Attached entities', cell: item => item.AttachmentCount ?? '-', width: 150 },
                    { id: 'desc', header: 'Description', cell: item => item.Description || '-' },
                  ]}
                  items={filteredPolicies}
                  loading={loading}
                  selectionType="single"
                  selectedItems={selectedPolicies}
                  onSelectionChange={({ detail }) => setSelectedPolicies(detail.selectedItems)}
                  trackBy="Arn"
                  empty={<Box textAlign="center">No customer managed policies.</Box>}
                />

                {activePolicy && (
                  <Container header={<Header variant="h2">Policy: {activePolicy.PolicyName}</Header>}>
                    <SpaceBetween size="m">
                      <KeyValuePairs
                        columns={3}
                        items={[
                          { label: 'ARN', value: activePolicy.Arn },
                          { label: 'Type', value: activePolicy.Arn?.startsWith('arn:aws:iam::aws:') ? 'AWS managed' : 'Customer managed' },
                          { label: 'Default version', value: activePolicy.DefaultVersionId || '-' },
                          { label: 'Creation time', value: formatAwsDate(activePolicy.CreateDate) },
                          { label: 'Edited time', value: formatAwsDate(activePolicy.UpdateDate) },
                          { label: 'Description', value: activePolicy.Description || '-' },
                        ]}
                      />
                      <Container header={<Header variant="h3">Permissions defined in this policy</Header>}>
                        {policyDocument?.error ? <StatusIndicator type="error">{policyDocument.error}</StatusIndicator>
                          : !policyDocument?.document ? <StatusIndicator type="loading">Loading policy document</StatusIndicator>
                          : (
                            <SpaceBetween size="m">
                              <PolicySummary document={policyDocument.document} />
                              <Textarea rows={12} readOnly ariaLabel="Policy document" value={JSON.stringify(policyDocument.document, null, 2)} />
                            </SpaceBetween>
                          )}
                      </Container>
                    </SpaceBetween>
                  </Container>
                )}
              </SpaceBetween>
            ),
          },
          {
            label: 'Policy simulator',
            id: 'simulator',
            content: (
              <Container header={<Header variant="h2" description="Runs SimulatePrincipalPolicy for a user or role against the actions and resource you enter.">IAM policy simulator</Header>}>
                <SpaceBetween size="m">
                  <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
                    <FormField label="Principal">
                      <Select
                        selectedOption={simPrincipal}
                        onChange={({ detail }) => setSimPrincipal(detail.selectedOption)}
                        options={[
                          { label: 'Users', options: users.map(u => ({ label: u.UserName, value: u.Arn })) },
                          { label: 'Roles', options: roles.map(r => ({ label: r.RoleName, value: r.Arn })) },
                        ]}
                        placeholder="Choose a user or role"
                        filteringType="auto"
                      />
                    </FormField>
                    <FormField label="Resource ARN">
                      <Input value={simResourceArn} onChange={({ detail }) => setSimResourceArn(detail.value)} placeholder="*" />
                    </FormField>
                  </Grid>
                  <FormField label="Actions" description="One action per line, for example s3:GetObject.">
                    <Textarea rows={4} value={simActions} onChange={({ detail }) => setSimActions(detail.value)} />
                  </FormField>
                  <Button variant="primary" loading={simulating} disabled={!simPrincipal?.value} onClick={handleRunSimulation}>Run simulation</Button>
                  {simMessage && <Alert type="info">{simMessage}</Alert>}
                  {simResults.length > 0 && (
                    <Table
                      columnDefinitions={[
                        { id: 'action', header: 'Action', cell: r => r.EvalActionName || r.action },
                        { id: 'resource', header: 'Resource', cell: r => r.EvalResourceName || r.resource || '*' },
                        {
                          id: 'decision',
                          header: 'Permission',
                          cell: r => {
                            const decision = String(r.EvalDecision || r.decision || '');
                            const label = decision === 'allowed' ? 'Allowed' : decision === 'explicitDeny' ? 'Denied (explicit deny)' : decision === 'implicitDeny' ? 'Denied (implicitly denied)' : decision || '-';
                            return <StatusIndicator type={decision === 'allowed' ? 'success' : 'error'}>{label}</StatusIndicator>;
                          },
                          width: 230,
                        },
                        { id: 'matched', header: 'Matched statements', cell: r => (r.MatchedStatements || []).map((m: any) => m.SourcePolicyId).filter(Boolean).join(', ') || '-' },
                      ]}
                      items={simResults}
                      trackBy={r => `${r.EvalActionName}-${r.EvalResourceName}`}
                    />
                  )}
                </SpaceBetween>
              </Container>
            ),
          },
        ]}
      />

      <ConfirmActionModal
        visible={Boolean(pendingDelete)}
        header={`Delete ${pendingDelete?.kind}`}
        confirmLabel="Delete"
        confirmationText={pendingDelete?.name}
        confirmationPrompt={`To confirm deletion, enter the ${pendingDelete?.kind === 'access key' ? 'access key ID' : `${pendingDelete?.kind} name`} in the text input field.`}
        loading={deleting}
        onConfirm={handleConfirmDelete}
        onDismiss={() => setPendingDelete(null)}
      >
        {pendingDelete?.kind === 'user' && <>Deleting {pendingDelete.name} also removes its access keys, console password, attached and inline policies, and group memberships. This can't be undone.</>}
        {pendingDelete?.kind === 'role' && <>Deleting {pendingDelete.name} detaches its policies and removes it from instance profiles. Services that assume this role lose access.</>}
        {pendingDelete?.kind === 'group' && <>Deleting {pendingDelete.name} removes its users from the group and deletes its policies.</>}
        {pendingDelete?.kind === 'policy' && <>Deleting {pendingDelete.name} fails while it is attached to any identity or has non-default versions. Detach it first.</>}
        {pendingDelete?.kind === 'access key' && <>Applications using {pendingDelete.name} can no longer call AWS. This can't be undone.</>}
      </ConfirmActionModal>

      <Modal
        visible={Boolean(inlineDocument)}
        onDismiss={() => setInlineDocument(null)}
        header={inlineDocument?.title}
        size="large"
        footer={<Box float="right"><Button variant="primary" onClick={() => setInlineDocument(null)}>Close</Button></Box>}
      >
        {inlineDocument && (
          <SpaceBetween size="m">
            <PolicySummary document={inlineDocument.document} />
            <Textarea rows={12} readOnly ariaLabel="Inline policy document" value={typeof inlineDocument.document === 'string' ? inlineDocument.document : JSON.stringify(inlineDocument.document, null, 2)} />
          </SpaceBetween>
        )}
      </Modal>

      <Modal
        visible={createUserOpen}
        onDismiss={() => setCreateUserOpen(false)}
        header="Create user"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateUserOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={creatingUser} disabled={!newUserName.trim()} onClick={handleCreateUser}>Create user</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="User name" constraintText="Up to 64 characters. Valid characters: A-Z, a-z, 0-9, and + = , . @ _ - (hyphen).">
            <Input value={newUserName} onChange={({ detail }) => setNewUserName(detail.value)} placeholder="developer" />
          </FormField>
          <Checkbox checked={addBaselinePolicy} onChange={({ detail }) => setAddBaselinePolicy(detail.checked)} description="Not part of AWS. With Floci IAM enforcement on, the dashboard needs sts:GetCallerIdentity to show who you are after switching to this user.">
            Add the dashboard's GetCallerIdentity inline policy
          </Checkbox>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={createRoleOpen}
        onDismiss={() => setCreateRoleOpen(false)}
        header="Create role"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateRoleOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={creatingRole} disabled={!newRoleName.trim()} onClick={handleCreateRole}>Create role</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Trusted entity" description="Starts the trust policy below with the chosen service principal.">
            <Select
              selectedOption={selectedTemplateKey}
              onChange={({ detail }) => {
                const opt = detail.selectedOption as any;
                setSelectedTemplateKey(opt);
                if (TRUST_TEMPLATES[opt.value]) setCustomTrustDoc(JSON.stringify(TRUST_TEMPLATES[opt.value].doc, null, 2));
              }}
              options={Object.entries(TRUST_TEMPLATES).map(([key, val]) => ({ label: val.label, value: key }))}
            />
          </FormField>
          <FormField label="Trust policy">
            <Textarea rows={8} value={customTrustDoc} onChange={({ detail }) => setCustomTrustDoc(detail.value)} />
          </FormField>
          <FormField label="Role name" constraintText="Up to 64 characters. Use alphanumeric and '+=,.@-_' characters.">
            <Input value={newRoleName} onChange={({ detail }) => setNewRoleName(detail.value)} placeholder="MyLambdaRole" />
          </FormField>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={createPolicyOpen}
        onDismiss={() => setCreatePolicyOpen(false)}
        header="Create policy"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreatePolicyOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={creatingPolicy} disabled={!newPolicyName.trim()} onClick={handleCreatePolicy}>Create policy</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Start from an example" description="Least-privilege examples. Edit the JSON before creating the policy.">
            <Select
              selectedOption={policyTemplateKey}
              onChange={({ detail }) => {
                const opt = detail.selectedOption as any;
                setPolicyTemplateKey(opt);
                if (POLICY_TEMPLATES[opt.value]) setCustomPolicyDoc(JSON.stringify(POLICY_TEMPLATES[opt.value].doc, null, 2));
              }}
              options={Object.entries(POLICY_TEMPLATES).map(([key, val]) => ({ label: val.label, value: key }))}
            />
          </FormField>
          <FormField label="Policy editor">
            <Textarea rows={10} value={customPolicyDoc} onChange={({ detail }) => setCustomPolicyDoc(detail.value)} />
          </FormField>
          <FormField label="Policy name" constraintText="Maximum 128 characters. Use alphanumeric and '+=,.@-_' characters.">
            <Input value={newPolicyName} onChange={({ detail }) => setNewPolicyName(detail.value)} placeholder="MyAppPolicy" />
          </FormField>
          <FormField label={<>Description <i>- optional</i></>}>
            <Input value={policyDescription} onChange={({ detail }) => setPolicyDescription(detail.value)} />
          </FormField>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={Boolean(attachTarget)}
        onDismiss={() => setAttachTarget(null)}
        header={`Attach policies to ${attachTarget?.type} ${attachTarget?.name}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setAttachTarget(null)}>Cancel</Button>
              <Button variant="primary" loading={attachingPolicy} disabled={!attachPolicyArn.trim()} onClick={handleAttachPolicy}>Add permissions</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Policy ARN" description="Choose a customer managed policy, or enter an AWS managed policy ARN such as arn:aws:iam::aws:policy/ReadOnlyAccess.">
          <Autosuggest
            value={attachPolicyArn}
            onChange={({ detail }) => setAttachPolicyArn(detail.value)}
            options={policies.map(policy => ({ value: policy.Arn, label: policy.PolicyName, description: policy.Arn }))}
            placeholder="arn:aws:iam::aws:policy/ReadOnlyAccess"
            enteredTextLabel={value => `Use: "${value}"`}
            empty="No customer managed policies"
          />
        </FormField>
      </Modal>

      <Modal
        visible={addToGroupOpen}
        onDismiss={() => setAddToGroupOpen(false)}
        header={`Add ${activeUser?.UserName} to groups`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setAddToGroupOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={addingToGroup} disabled={!selectedGroupName} onClick={handleAddUserToGroup}>Add to groups</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="User group">
          <Select
            selectedOption={selectedGroupName ? { label: selectedGroupName, value: selectedGroupName } : null}
            onChange={({ detail }) => setSelectedGroupName(detail.selectedOption.value || '')}
            options={groups.filter(g => !activeUser?.Groups.includes(g.GroupName)).map(g => ({ label: g.GroupName, value: g.GroupName }))}
            placeholder="Choose a group"
          />
        </FormField>
      </Modal>

      <Modal
        visible={createGroupOpen}
        onDismiss={() => setCreateGroupOpen(false)}
        header="Create user group"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateGroupOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={creatingGroup} disabled={!newGroupName.trim()} onClick={handleCreateGroup}>Create user group</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="User group name" constraintText="Maximum 128 characters. Use alphanumeric and '+=,.@-_' characters.">
          <Input value={newGroupName} onChange={({ detail }) => setNewGroupName(detail.value)} placeholder="Developers" />
        </FormField>
      </Modal>
    </SpaceBetween>
  );
};
