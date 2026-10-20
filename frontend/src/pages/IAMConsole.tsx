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
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Grid from '@cloudscape-design/components/grid';
import Box from '@cloudscape-design/components/box';
import Badge from '@cloudscape-design/components/badge';
import Alert from '@cloudscape-design/components/alert';
import {
  fetchServiceInventory,
  executeServiceAction,
  createIamRole,
  deleteIamRole,
  createIamGroup,
  simulateIamPolicy,
  createIamPolicy,
  deleteIamPolicy,
  fetchIamUserAccessKeys,
  createIamUserAccessKey,
  deleteIamUserAccessKey,
  updateIamAccessKeyState,
  attachIamUserPolicy,
  detachIamUserPolicy,
  attachIamRolePolicy,
  detachIamRolePolicy,
  addUserToIamGroup,
  removeUserFromIamGroup,
} from '../api/client';

interface UserItem {
  UserName: string;
  UserId: string;
  Arn: string;
  CreateDate: string;
  Path?: string;
  AttachedPolicies?: Array<{ PolicyName: string; PolicyArn: string }>;
}

interface RoleItem {
  RoleName: string;
  RoleId: string;
  Arn: string;
  CreateDate: string;
  AssumeRolePolicyDocument?: any;
  Description?: string;
  AttachedPolicies?: Array<{ PolicyName: string; PolicyArn: string }>;
}

interface PolicyItem {
  PolicyName: string;
  PolicyId?: string;
  Arn: string;
  Path?: string;
  DefaultVersionId?: string;
  AttachmentCount?: number;
  IsAttachable?: boolean;
  CreateDate?: string;
  Document?: any;
}

const POLICY_TEMPLATES: Record<string, { label: string; doc: any }> = {
  admin: {
    label: 'Administrator Access (Full Access)',
    doc: {
      Version: '2012-10-17',
      Statement: [{ Effect: 'Allow', Action: '*', Resource: '*' }],
    },
  },
  readonly: {
    label: 'Read Only Access (Global GET / List)',
    doc: {
      Version: '2012-10-17',
      Statement: [{ Effect: 'Allow', Action: ['*:Describe*', '*:Get*', '*:List*'], Resource: '*' }],
    },
  },
  s3full: {
    label: 'Amazon S3 Full Access',
    doc: {
      Version: '2012-10-17',
      Statement: [{ Effect: 'Allow', Action: 's3:*', Resource: '*' }],
    },
  },
  dynamodbfull: {
    label: 'Amazon DynamoDB Full Access',
    doc: {
      Version: '2012-10-17',
      Statement: [{ Effect: 'Allow', Action: 'dynamodb:*', Resource: '*' }],
    },
  },
  lambdafull: {
    label: 'AWS Lambda Full Access',
    doc: {
      Version: '2012-10-17',
      Statement: [{ Effect: 'Allow', Action: 'lambda:*', Resource: '*' }],
    },
  },
};

const TRUST_TEMPLATES: Record<string, { label: string; doc: any }> = {
  lambda: {
    label: 'AWS Lambda (Serverless functions)',
    doc: {
      Version: '2012-10-17',
      Statement: [
        {
          Effect: 'Allow',
          Principal: { Service: 'lambda.amazonaws.com' },
          Action: 'sts:AssumeRole',
        },
      ],
    },
  },
  ec2: {
    label: 'Amazon EC2 (Virtual servers & instances)',
    doc: {
      Version: '2012-10-17',
      Statement: [
        {
          Effect: 'Allow',
          Principal: { Service: 'ec2.amazonaws.com' },
          Action: 'sts:AssumeRole',
        },
      ],
    },
  },
  ecs: {
    label: 'Amazon ECS Tasks (Container tasks)',
    doc: {
      Version: '2012-10-17',
      Statement: [
        {
          Effect: 'Allow',
          Principal: { Service: 'ecs-tasks.amazonaws.com' },
          Action: 'sts:AssumeRole',
        },
      ],
    },
  },
  apigateway: {
    label: 'Amazon API Gateway (REST & HTTP APIs)',
    doc: {
      Version: '2012-10-17',
      Statement: [
        {
          Effect: 'Allow',
          Principal: { Service: 'apigateway.amazonaws.com' },
          Action: 'sts:AssumeRole',
        },
      ],
    },
  },
  states: {
    label: 'AWS Step Functions (State machines)',
    doc: {
      Version: '2012-10-17',
      Statement: [
        {
          Effect: 'Allow',
          Principal: { Service: 'states.amazonaws.com' },
          Action: 'sts:AssumeRole',
        },
      ],
    },
  },
};

interface IAMConsoleProps {
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const IAMConsole: React.FC<IAMConsoleProps> = ({ activeTab, onTabChange }) => {
  const [users, setUsers] = useState<UserItem[]>([]);
  const [roles, setRoles] = useState<RoleItem[]>([]);
  const [groups, setGroups] = useState<any[]>([]);
  const [policies, setPolicies] = useState<PolicyItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [selectedTabId, setSelectedTabId] = useState(activeTab || 'users');

  useEffect(() => {
    if (activeTab) {
      setSelectedTabId(activeTab);
    }
  }, [activeTab]);

  // Selected Items
  const [selectedUsers, setSelectedUsers] = useState<UserItem[]>([]);
  const [selectedRoles, setSelectedRoles] = useState<RoleItem[]>([]);
  const [selectedPolicies, setSelectedPolicies] = useState<PolicyItem[]>([]);
  const [userFilter, setUserFilter] = useState('');
  const [roleFilter, setRoleFilter] = useState('');
  const [policyFilter, setPolicyFilter] = useState('');

  // User Access Keys State
  const [userAccessKeys, setUserAccessKeys] = useState<any[]>([]);
  const [loadingAccessKeys, setLoadingAccessKeys] = useState(false);
  const [newAccessKeyResult, setNewAccessKeyResult] = useState<{ AccessKeyId: string; SecretAccessKey: string } | null>(null);

  // Attach Policy Modal
  const [attachPolicyOpen, setAttachPolicyOpen] = useState(false);
  const [attachPolicyArn, setAttachPolicyArn] = useState('arn:aws:iam::aws:policy/AdministratorAccess');
  const [attachingPolicy, setAttachingPolicy] = useState(false);
  const [attachTargetType, setAttachTargetType] = useState<'user' | 'role'>('user');

  // Add User to Group Modal
  const [addToGroupOpen, setAddToGroupOpen] = useState(false);
  const [selectedGroupName, setSelectedGroupName] = useState('');
  const [addingToGroup, setAddingToGroup] = useState(false);

  // Create User Modal
  const [createUserOpen, setCreateUserOpen] = useState(false);
  const [newUserName, setNewUserName] = useState('');
  const [creatingUser, setCreatingUser] = useState(false);

  // Create Role Modal
  const [createRoleOpen, setCreateRoleOpen] = useState(false);
  const [newRoleName, setNewRoleName] = useState('');
  const [selectedTemplateKey, setSelectedTemplateKey] = useState({ label: 'AWS Lambda (Serverless functions)', value: 'lambda' });
  const [customTrustDoc, setCustomTrustDoc] = useState(JSON.stringify(TRUST_TEMPLATES.lambda.doc, null, 2));
  const [creatingRole, setCreatingRole] = useState(false);

  // Create Policy Modal
  const [createPolicyOpen, setCreatePolicyOpen] = useState(false);
  const [newPolicyName, setNewPolicyName] = useState('');
  const [policyTemplateKey, setPolicyTemplateKey] = useState({ label: 'Administrator Access (Full Access)', value: 'admin' });
  const [customPolicyDoc, setCustomPolicyDoc] = useState(JSON.stringify(POLICY_TEMPLATES.admin.doc, null, 2));
  const [policyDescription, setPolicyDescription] = useState('Custom managed policy');
  const [creatingPolicy, setCreatingPolicy] = useState(false);

  // Create Group Modal
  const [createGroupOpen, setCreateGroupOpen] = useState(false);
  const [newGroupName, setNewGroupName] = useState('');
  const [creatingGroup, setCreatingGroup] = useState(false);

  // Policy Simulator State
  const [simPrincipal, setSimPrincipal] = useState({ label: 'floci-root (Administrator)', value: 'arn:aws:iam::000000000000:root' });
  const [simActions, setSimActions] = useState('s3:GetObject\nsqs:SendMessage\ndynamodb:PutItem');
  const [simResourceArn, setSimResourceArn] = useState('*');
  const [simResults, setSimResults] = useState<any[]>([]);
  const [simulating, setSimulating] = useState(false);

  const loadData = async () => {
    setLoading(true);
    try {
      const data = await fetchServiceInventory('iam');
      const uList = (data.users || data.Users || []).map((u: any) => ({
        UserName: u.UserName || u.name || 'Alice',
        UserId: u.UserId || u.id || 'AIDA0123456789EXAMPLE',
        Arn: u.Arn || `arn:aws:iam::000000000000:user/${u.UserName || 'Alice'}`,
        CreateDate: u.CreateDate || u.created || new Date().toISOString().split('T')[0],
        Path: u.Path || '/',
        AttachedPolicies: u.AttachedPolicies || [{ PolicyName: 'AdministratorAccess', PolicyArn: 'arn:aws:iam::aws:policy/AdministratorAccess' }],
      }));
      setUsers(uList);
      if (uList.length > 0 && selectedUsers.length === 0) {
        setSelectedUsers([uList[0]]);
      }

      const rList = (data.roles || data.Roles || []).map((r: any) => ({
        RoleName: r.RoleName || r.name,
        RoleId: r.RoleId || r.id,
        Arn: r.Arn || `arn:aws:iam::000000000000:role/${r.RoleName || r.name}`,
        CreateDate: r.CreateDate || r.created || new Date().toISOString().split('T')[0],
        AssumeRolePolicyDocument: r.AssumeRolePolicyDocument || r.assume_role_policy_document || TRUST_TEMPLATES.lambda.doc,
        Description: r.Description || r.description || '—',
        AttachedPolicies: r.AttachedPolicies || [{ PolicyName: 'AWSLambdaBasicExecutionRole', PolicyArn: 'arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole' }],
      }));
      setRoles(rList);
      if (rList.length > 0 && selectedRoles.length === 0) {
        setSelectedRoles([rList[0]]);
      }

      setGroups(data.groups || data.Groups || []);

      const pList: PolicyItem[] = (data.policies || data.Policies || []).map((p: any) => ({
        PolicyName: p.PolicyName || p.name || 'CustomPolicy',
        PolicyId: p.PolicyId || p.id,
        Arn: p.Arn || p.arn || `arn:aws:iam::000000000000:policy/${p.PolicyName || 'CustomPolicy'}`,
        Path: p.Path || '/',
        DefaultVersionId: p.DefaultVersionId || 'v1',
        AttachmentCount: p.AttachmentCount ?? 1,
        IsAttachable: p.IsAttachable ?? true,
        CreateDate: p.CreateDate || p.created || new Date().toISOString().split('T')[0],
        Document: p.Document || p.document || POLICY_TEMPLATES.admin.doc,
      }));

      // If empty, supply default policy library
      if (pList.length === 0) {
        pList.push(
          { PolicyName: 'AdministratorAccess', Arn: 'arn:aws:iam::aws:policy/AdministratorAccess', AttachmentCount: 2, IsAttachable: true, Document: POLICY_TEMPLATES.admin.doc },
          { PolicyName: 'ReadOnlyAccess', Arn: 'arn:aws:iam::aws:policy/ReadOnlyAccess', AttachmentCount: 0, IsAttachable: true, Document: POLICY_TEMPLATES.readonly.doc },
          { PolicyName: 'AmazonS3FullAccess', Arn: 'arn:aws:iam::aws:policy/AmazonS3FullAccess', AttachmentCount: 1, IsAttachable: true, Document: POLICY_TEMPLATES.s3full.doc },
          { PolicyName: 'AmazonDynamoDBFullAccess', Arn: 'arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess', AttachmentCount: 1, IsAttachable: true, Document: POLICY_TEMPLATES.dynamodbfull.doc },
          { PolicyName: 'AWSLambdaFullAccess', Arn: 'arn:aws:iam::aws:policy/AWSLambdaFullAccess', AttachmentCount: 1, IsAttachable: true, Document: POLICY_TEMPLATES.lambdafull.doc }
        );
      }
      setPolicies(pList);
      if (pList.length > 0 && selectedPolicies.length === 0) {
        setSelectedPolicies([pList[0]]);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const activeUser = selectedUsers[0] || null;
  const activeRole = selectedRoles[0] || null;
  const activePolicy = selectedPolicies[0] || null;

  const loadUserKeys = async (userName: string) => {
    if (!userName) return;
    setLoadingAccessKeys(true);
    try {
      const keys = await fetchIamUserAccessKeys(userName);
      setUserAccessKeys(keys || []);
    } catch (err) {
      console.error(err);
      setUserAccessKeys([]);
    } finally {
      setLoadingAccessKeys(false);
    }
  };

  useEffect(() => {
    if (activeUser) {
      loadUserKeys(activeUser.UserName);
    }
  }, [activeUser?.UserName]);

  const handleCreateUser = async () => {
    if (!newUserName.trim()) return;
    setCreatingUser(true);
    setActionMessage(null);
    try {
      await executeServiceAction('iam', 'create_user', { user_name: newUserName.trim() });
      setActionMessage({ type: 'success', text: `IAM User "${newUserName.trim()}" created successfully.` });
      setCreateUserOpen(false);
      setNewUserName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create user' });
    } finally {
      setCreatingUser(false);
    }
  };

  const handleDeleteUser = async () => {
    if (!selectedUsers.length) return;
    const user = selectedUsers[0];
    try {
      await executeServiceAction('iam', 'cleanup_user', { user_name: user.UserName, force: true });
      setActionMessage({ type: 'success', text: `User "${user.UserName}" deleted.` });
      setSelectedUsers([]);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete user' });
    }
  };

  const handleCreateRole = async () => {
    if (!newRoleName.trim()) return;
    setCreatingRole(true);
    setActionMessage(null);
    try {
      let parsedDoc = null;
      try {
        parsedDoc = JSON.parse(customTrustDoc);
      } catch (e) {}

      await createIamRole(newRoleName.trim(), selectedTemplateKey.value, parsedDoc);
      setActionMessage({ type: 'success', text: `IAM Role "${newRoleName.trim()}" created successfully.` });
      setCreateRoleOpen(false);
      setNewRoleName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create role' });
    } finally {
      setCreatingRole(false);
    }
  };

  const handleDeleteRole = async () => {
    if (!selectedRoles.length) return;
    const role = selectedRoles[0];
    try {
      await deleteIamRole(role.RoleName);
      setActionMessage({ type: 'success', text: `Role "${role.RoleName}" deleted.` });
      setSelectedRoles([]);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete role' });
    }
  };

  const handleCreatePolicy = async () => {
    if (!newPolicyName.trim()) return;
    setCreatingPolicy(true);
    setActionMessage(null);
    try {
      await createIamPolicy(newPolicyName.trim(), customPolicyDoc, policyDescription.trim());
      setActionMessage({ type: 'success', text: `Policy "${newPolicyName.trim()}" created.` });
      setCreatePolicyOpen(false);
      setNewPolicyName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create policy' });
    } finally {
      setCreatingPolicy(false);
    }
  };

  const handleDeletePolicy = async () => {
    if (!activePolicy) return;
    try {
      await deleteIamPolicy(activePolicy.Arn);
      setActionMessage({ type: 'success', text: `Policy "${activePolicy.PolicyName}" deleted.` });
      setSelectedPolicies([]);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete policy' });
    }
  };

  const handleCreateAccessKey = async () => {
    if (!activeUser) return;
    try {
      const res = await createIamUserAccessKey(activeUser.UserName);
      const keyData = res.access_key || res.AccessKey || res;
      setNewAccessKeyResult(keyData);
      setActionMessage({ type: 'success', text: `Created access key for user "${activeUser.UserName}".` });
      await loadUserKeys(activeUser.UserName);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create access key' });
    }
  };

  const handleDeleteAccessKey = async (accessKeyId: string) => {
    if (!activeUser) return;
    try {
      await deleteIamUserAccessKey(activeUser.UserName, accessKeyId);
      setActionMessage({ type: 'success', text: `Access key ${accessKeyId} deleted.` });
      await loadUserKeys(activeUser.UserName);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete access key' });
    }
  };

  const handleToggleAccessKeyState = async (accessKeyId: string, currentStatus: string) => {
    if (!activeUser) return;
    const newStatus = currentStatus === 'Active' ? 'Inactive' : 'Active';
    try {
      await updateIamAccessKeyState(activeUser.UserName, accessKeyId, newStatus);
      setActionMessage({ type: 'success', text: `Access key status changed to ${newStatus}.` });
      await loadUserKeys(activeUser.UserName);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to update access key status' });
    }
  };

  const handleAttachPolicy = async () => {
    if (!attachPolicyArn.trim()) return;
    setAttachingPolicy(true);
    try {
      if (attachTargetType === 'user' && activeUser) {
        await attachIamUserPolicy(activeUser.UserName, attachPolicyArn.trim());
        setActionMessage({ type: 'success', text: `Attached policy to user "${activeUser.UserName}".` });
      } else if (attachTargetType === 'role' && activeRole) {
        await attachIamRolePolicy(activeRole.RoleName, attachPolicyArn.trim());
        setActionMessage({ type: 'success', text: `Attached policy to role "${activeRole.RoleName}".` });
      }
      setAttachPolicyOpen(false);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to attach policy' });
    } finally {
      setAttachingPolicy(false);
    }
  };

  const handleDetachPolicy = async (policyArn: string) => {
    try {
      if (activeUser) {
        await detachIamUserPolicy(activeUser.UserName, policyArn);
        setActionMessage({ type: 'success', text: `Detached policy from user "${activeUser.UserName}".` });
      } else if (activeRole) {
        await detachIamRolePolicy(activeRole.RoleName, policyArn);
        setActionMessage({ type: 'success', text: `Detached policy from role "${activeRole.RoleName}".` });
      }
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to detach policy' });
    }
  };

  const handleCreateGroup = async () => {
    if (!newGroupName.trim()) return;
    setCreatingGroup(true);
    setActionMessage(null);
    try {
      await createIamGroup(newGroupName.trim());
      setActionMessage({ type: 'success', text: `IAM Group "${newGroupName.trim()}" created successfully.` });
      setCreateGroupOpen(false);
      setNewGroupName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create group' });
    } finally {
      setCreatingGroup(false);
    }
  };

  const handleAddUserToGroup = async () => {
    if (!activeUser || !selectedGroupName) return;
    setAddingToGroup(true);
    try {
      await addUserToIamGroup(selectedGroupName, activeUser.UserName);
      setActionMessage({ type: 'success', text: `User "${activeUser.UserName}" added to group "${selectedGroupName}".` });
      setAddToGroupOpen(false);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to add user to group' });
    } finally {
      setAddingToGroup(false);
    }
  };

  const handleRunSimulation = async () => {
    const actions = simActions
      .split(/[\n,]/)
      .map((a) => a.trim())
      .filter(Boolean);
    if (!actions.length) return;
    setSimulating(true);
    try {
      const res = await simulateIamPolicy(simPrincipal.value, actions, [simResourceArn.trim() || '*']);
      setSimResults(res.results || res.EvaluationResults || []);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Policy simulation failed' });
    } finally {
      setSimulating(false);
    }
  };

  const filteredUsers = users.filter((u) => u.UserName.toLowerCase().includes(userFilter.toLowerCase()));
  const filteredRoles = roles.filter((r) => r.RoleName.toLowerCase().includes(roleFilter.toLowerCase()));
  const filteredPolicies = policies.filter((p) => p.PolicyName.toLowerCase().includes(policyFilter.toLowerCase()));

  return (
    <SpaceBetween size="l">
      {/* Header Container */}
      <Container
        header={
          <Header
            variant="h1"
            description="Manage identity access, roles, customer-managed policies, access credentials, and evaluate security boundaries."
            actions={
              <Button iconName="refresh" onClick={loadData} loading={loading}>
                Refresh
              </Button>
            }
          >
            AWS Identity & Access Management (IAM)
          </Header>
        }
      >
        {actionMessage && (
          <Box margin={{ bottom: 'm' }}>
            <Alert type={actionMessage.type} dismissible onDismiss={() => setActionMessage(null)}>
              {actionMessage.text}
            </Alert>
          </Box>
        )}

        <Grid gridDefinition={[{ colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }]}>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">IAM Users</Box>
            <Box variant="h1" color="text-status-info">
              {users.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">IAM Roles</Box>
            <Box variant="h1" color="text-status-info">
              {roles.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">User Groups</Box>
            <Box variant="h1" color="text-status-info">
              {groups.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Managed Policies</Box>
            <Box variant="h1" color="text-status-info">
              {policies.length}
            </Box>
          </Box>
        </Grid>
      </Container>

      {/* Main Tabs */}
      <Tabs
        activeTabId={selectedTabId}
        onChange={({ detail }) => {
          setSelectedTabId(detail.activeTabId);
          onTabChange?.(detail.activeTabId);
        }}
        tabs={[
          {
            label: `Users (${users.length})`,
            id: 'users',
            content: (
              <SpaceBetween size="l">
                <Container
                  header={
                    <Header
                      variant="h2"
                      actions={
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button disabled={!selectedUsers.length} onClick={handleDeleteUser}>
                            Delete User
                          </Button>
                          <Button variant="primary" iconName="add-plus" onClick={() => setCreateUserOpen(true)}>
                            Add User
                          </Button>
                        </SpaceBetween>
                      }
                    >
                      IAM Users
                    </Header>
                  }
                >
                  <SpaceBetween size="m">
                    <TextFilter
                      filteringText={userFilter}
                      filteringPlaceholder="Find user by name..."
                      onChange={({ detail }) => setUserFilter(detail.filteringText)}
                    />

                    <Table
                      columnDefinitions={[
                        {
                          id: 'name',
                          header: 'User Name',
                          cell: (item) => (
                            <Button variant="inline-link" onClick={() => setSelectedUsers([item])}>
                              <strong>{item.UserName}</strong>
                            </Button>
                          ),
                        },
                        {
                          id: 'path',
                          header: 'Path',
                          cell: (item) => item.Path || '/',
                          width: 100,
                        },
                        {
                          id: 'arn',
                          header: 'User ARN',
                          cell: (item) => <code>{item.Arn}</code>,
                        },
                        {
                          id: 'created',
                          header: 'Created Time',
                          cell: (item) => item.CreateDate,
                          width: 140,
                        },
                      ]}
                      items={filteredUsers}
                      selectionType="single"
                      selectedItems={selectedUsers}
                      onSelectionChange={({ detail }) => setSelectedUsers(detail.selectedItems)}
                      empty={<Box textAlign="center">No IAM users found.</Box>}
                    />
                  </SpaceBetween>
                </Container>

                {activeUser && (
                  <Container header={<Header variant="h2">User: {activeUser.UserName}</Header>}>
                    <Tabs
                      tabs={[
                        {
                          label: 'Permissions Policies',
                          id: 'perms',
                          content: (
                            <SpaceBetween size="m">
                              <Box float="right">
                                <Button
                                  variant="primary"
                                  iconName="add-plus"
                                  onClick={() => {
                                    setAttachTargetType('user');
                                    setAttachPolicyOpen(true);
                                  }}
                                >
                                  Attach Policy
                                </Button>
                              </Box>
                              <Table
                                columnDefinitions={[
                                  { id: 'policy', header: 'Policy Name', cell: (i) => <strong>{i.PolicyName}</strong> },
                                  { id: 'arn', header: 'Policy ARN', cell: (i) => <code>{i.PolicyArn}</code> },
                                  { id: 'type', header: 'Type', cell: () => <Badge color="blue">AWS Managed</Badge>, width: 140 },
                                  {
                                    id: 'act',
                                    header: 'Action',
                                    cell: (i) => (
                                      <Button iconName="remove" onClick={() => handleDetachPolicy(i.PolicyArn)}>
                                        Detach
                                      </Button>
                                    ),
                                    width: 110,
                                  },
                                ]}
                                items={activeUser.AttachedPolicies || []}
                                empty={<Box textAlign="center">No policies attached to this user.</Box>}
                              />
                            </SpaceBetween>
                          ),
                        },
                        {
                          label: `Security Credentials (${userAccessKeys.length} Keys)`,
                          id: 'creds',
                          content: (
                            <SpaceBetween size="m">
                              <Box float="right">
                                <Button variant="primary" iconName="add-plus" onClick={handleCreateAccessKey}>
                                  Create Access Key
                                </Button>
                              </Box>

                              {newAccessKeyResult && (
                                <Alert
                                  type="success"
                                  header="New Access Key Created"
                                  dismissible
                                  onDismiss={() => setNewAccessKeyResult(null)}
                                >
                                  <KeyValuePairs
                                    columns={2}
                                    items={[
                                      { label: 'Access Key ID', value: newAccessKeyResult.AccessKeyId },
                                      { label: 'Secret Access Key', value: newAccessKeyResult.SecretAccessKey || '••••••••••••••••••••••••' },
                                    ]}
                                  />
                                </Alert>
                              )}

                              <Table
                                columnDefinitions={[
                                  { id: 'id', header: 'Access Key ID', cell: (k) => <strong>{k.AccessKeyId}</strong> },
                                  {
                                    id: 'status',
                                    header: 'Status',
                                    cell: (k) => (
                                      <StatusIndicator type={k.Status === 'Active' ? 'success' : 'stopped'}>
                                        {k.Status}
                                      </StatusIndicator>
                                    ),
                                    width: 130,
                                  },
                                  { id: 'created', header: 'Created', cell: (k) => k.CreateDate || 'Today', width: 150 },
                                  {
                                    id: 'actions',
                                    header: 'Actions',
                                    cell: (k) => (
                                      <SpaceBetween direction="horizontal" size="xs">
                                        <Button onClick={() => handleToggleAccessKeyState(k.AccessKeyId, k.Status)}>
                                          {k.Status === 'Active' ? 'Deactivate' : 'Activate'}
                                        </Button>
                                        <Button iconName="remove" onClick={() => handleDeleteAccessKey(k.AccessKeyId)}>
                                          Delete
                                        </Button>
                                      </SpaceBetween>
                                    ),
                                    width: 220,
                                  },
                                ]}
                                items={userAccessKeys}
                                loading={loadingAccessKeys}
                                empty={<Box textAlign="center">No access keys created for this user.</Box>}
                              />
                            </SpaceBetween>
                          ),
                        },
                        {
                          label: 'Groups',
                          id: 'groups',
                          content: (
                            <SpaceBetween size="m">
                              <Box float="right">
                                <Button variant="primary" iconName="add-plus" onClick={() => setAddToGroupOpen(true)}>
                                  Add User to Group
                                </Button>
                              </Box>
                              <Table
                                columnDefinitions={[
                                  { id: 'group', header: 'Group Name', cell: (g) => <strong>{g.GroupName || g.name}</strong> },
                                  { id: 'arn', header: 'ARN', cell: (g) => <code>{g.Arn || `arn:aws:iam::000000000000:group/${g.GroupName || g.name}`}</code> },
                                  {
                                    id: 'act',
                                    header: 'Action',
                                    cell: (g) => (
                                      <Button iconName="remove" onClick={() => removeUserFromIamGroup(g.GroupName || g.name, activeUser.UserName)}>
                                        Remove
                                      </Button>
                                    ),
                                    width: 120,
                                  },
                                ]}
                                items={groups.filter((g: any) => (g.Users || []).some((u: any) => (u.UserName || u) === activeUser.UserName))}
                                empty={<Box textAlign="center">User is not a member of any groups.</Box>}
                              />
                            </SpaceBetween>
                          ),
                        },
                      ]}
                    />
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
                <Container
                  header={
                    <Header
                      variant="h2"
                      description="An IAM role is an identity with permission policies that determine what the identity can and cannot do in AWS."
                      actions={
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button disabled={!selectedRoles.length} onClick={handleDeleteRole}>
                            Delete Role
                          </Button>
                          <Button variant="primary" iconName="add-plus" onClick={() => setCreateRoleOpen(true)}>
                            Create Role
                          </Button>
                        </SpaceBetween>
                      }
                    >
                      IAM Roles
                    </Header>
                  }
                >
                  <SpaceBetween size="m">
                    <TextFilter
                      filteringText={roleFilter}
                      filteringPlaceholder="Find role by name..."
                      onChange={({ detail }) => setRoleFilter(detail.filteringText)}
                    />

                    <Table
                      columnDefinitions={[
                        {
                          id: 'name',
                          header: 'Role Name',
                          cell: (item) => (
                            <Button variant="inline-link" onClick={() => setSelectedRoles([item])}>
                              <strong>{item.RoleName}</strong>
                            </Button>
                          ),
                        },
                        {
                          id: 'arn',
                          header: 'Role ARN',
                          cell: (item) => <code>{item.Arn}</code>,
                        },
                        {
                          id: 'desc',
                          header: 'Description',
                          cell: (item) => item.Description,
                        },
                        {
                          id: 'created',
                          header: 'Created Time',
                          cell: (item) => item.CreateDate,
                          width: 140,
                        },
                      ]}
                      items={filteredRoles}
                      selectionType="single"
                      selectedItems={selectedRoles}
                      onSelectionChange={({ detail }) => setSelectedRoles(detail.selectedItems)}
                      empty={<Box textAlign="center">No IAM roles found.</Box>}
                    />
                  </SpaceBetween>
                </Container>

                {activeRole && (
                  <Container header={<Header variant="h2">Role: {activeRole.RoleName}</Header>}>
                    <Tabs
                      tabs={[
                        {
                          label: 'Permissions Policies',
                          id: 'role-perms',
                          content: (
                            <SpaceBetween size="m">
                              <Box float="right">
                                <Button
                                  variant="primary"
                                  iconName="add-plus"
                                  onClick={() => {
                                    setAttachTargetType('role');
                                    setAttachPolicyOpen(true);
                                  }}
                                >
                                  Attach Policy
                                </Button>
                              </Box>
                              <Table
                                columnDefinitions={[
                                  { id: 'policy', header: 'Policy Name', cell: (i) => <strong>{i.PolicyName}</strong> },
                                  { id: 'arn', header: 'Policy ARN', cell: (i) => <code>{i.PolicyArn}</code> },
                                  {
                                    id: 'act',
                                    header: 'Action',
                                    cell: (i) => (
                                      <Button iconName="remove" onClick={() => handleDetachPolicy(i.PolicyArn)}>
                                        Detach
                                      </Button>
                                    ),
                                    width: 110,
                                  },
                                ]}
                                items={activeRole.AttachedPolicies || []}
                                empty={<Box textAlign="center">No policies attached to this role.</Box>}
                              />
                            </SpaceBetween>
                          ),
                        },
                        {
                          label: 'Trust Relationships',
                          id: 'role-trust',
                          content: (
                            <Container header={<Header variant="h3">Assume Role Policy Document (Trust Policy)</Header>}>
                              <Textarea
                                rows={10}
                                value={typeof activeRole.AssumeRolePolicyDocument === 'string' ? activeRole.AssumeRolePolicyDocument : JSON.stringify(activeRole.AssumeRolePolicyDocument, null, 2)}
                                readOnly
                              />
                            </Container>
                          ),
                        },
                      ]}
                    />
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
                <Container
                  header={
                    <Header
                      variant="h2"
                      description="Managed policies define permissions for identities and resources."
                      actions={
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button disabled={!selectedPolicies.length} onClick={handleDeletePolicy}>
                            Delete Policy
                          </Button>
                          <Button variant="primary" iconName="add-plus" onClick={() => setCreatePolicyOpen(true)}>
                            Create Policy
                          </Button>
                        </SpaceBetween>
                      }
                    >
                      IAM Policies
                    </Header>
                  }
                >
                  <SpaceBetween size="m">
                    <TextFilter
                      filteringText={policyFilter}
                      filteringPlaceholder="Find policy by name..."
                      onChange={({ detail }) => setPolicyFilter(detail.filteringText)}
                    />

                    <Table
                      columnDefinitions={[
                        {
                          id: 'name',
                          header: 'Policy Name',
                          cell: (item) => (
                            <Button variant="inline-link" onClick={() => setSelectedPolicies([item])}>
                              <strong>{item.PolicyName}</strong>
                            </Button>
                          ),
                        },
                        {
                          id: 'arn',
                          header: 'Policy ARN',
                          cell: (item) => <code>{item.Arn}</code>,
                        },
                        {
                          id: 'attachments',
                          header: 'Attached Entities',
                          cell: (item) => item.AttachmentCount ?? 0,
                          width: 150,
                        },
                      ]}
                      items={filteredPolicies}
                      selectionType="single"
                      selectedItems={selectedPolicies}
                      onSelectionChange={({ detail }) => setSelectedPolicies(detail.selectedItems)}
                      empty={<Box textAlign="center">No policies found.</Box>}
                    />
                  </SpaceBetween>
                </Container>

                {activePolicy && (
                  <Container header={<Header variant="h2">Policy: {activePolicy.PolicyName}</Header>}>
                    <SpaceBetween size="m">
                      <KeyValuePairs
                        columns={2}
                        items={[
                          { label: 'Policy ARN', value: activePolicy.Arn },
                          { label: 'Default Version', value: activePolicy.DefaultVersionId || 'v1' },
                        ]}
                      />
                      <Container header={<Header variant="h3">Policy Document (JSON)</Header>}>
                        <Textarea
                          rows={12}
                          value={typeof activePolicy.Document === 'string' ? activePolicy.Document : JSON.stringify(activePolicy.Document, null, 2)}
                          readOnly
                        />
                      </Container>
                    </SpaceBetween>
                  </Container>
                )}
              </SpaceBetween>
            ),
          },
          {
            label: `User Groups (${groups.length})`,
            id: 'groups',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setCreateGroupOpen(true)}>
                        Create Group
                      </Button>
                    }
                  >
                    User Groups
                  </Header>
                }
              >
                <Table
                  columnDefinitions={[
                    { id: 'name', header: 'Group Name', cell: (g) => <strong>{g.GroupName || g.name}</strong> },
                    { id: 'arn', header: 'ARN', cell: (g) => <code>{g.Arn || `arn:aws:iam::000000000000:group/${g.GroupName || g.name}`}</code> },
                    { id: 'created', header: 'Created Date', cell: (g) => g.CreateDate || 'Today', width: 150 },
                  ]}
                  items={groups}
                  empty={<Box textAlign="center">No IAM user groups found.</Box>}
                />
              </Container>
            ),
          },
          {
            label: 'Policy Simulator',
            id: 'simulator',
            content: (
              <Container header={<Header variant="h2">IAM Policy Simulator</Header>}>
                <SpaceBetween size="m">
                  <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
                    <FormField label="Principal Entity">
                      <Select
                        selectedOption={simPrincipal}
                        onChange={({ detail }) => setSimPrincipal(detail.selectedOption as any)}
                        options={[
                          { label: 'floci-root (Administrator)', value: 'arn:aws:iam::000000000000:root' },
                          ...users.map((u) => ({ label: `User: ${u.UserName}`, value: u.Arn })),
                          ...roles.map((r) => ({ label: `Role: ${r.RoleName}`, value: r.Arn })),
                        ]}
                      />
                    </FormField>
                    <FormField label="Resource ARN">
                      <Input value={simResourceArn} onChange={({ detail }) => setSimResourceArn(detail.value)} placeholder="*" />
                    </FormField>
                  </Grid>

                  <FormField label="Action Names (one per line)">
                    <Textarea rows={4} value={simActions} onChange={({ detail }) => setSimActions(detail.value)} />
                  </FormField>

                  <Button variant="primary" loading={simulating} onClick={handleRunSimulation}>
                    Run Policy Simulation
                  </Button>

                  {simResults.length > 0 && (
                    <Table
                      columnDefinitions={[
                        { id: 'action', header: 'Action', cell: (r) => <strong>{r.EvalActionName || r.action}</strong> },
                        { id: 'resource', header: 'Resource', cell: (r) => r.EvalResourceName || r.resource || '*' },
                        {
                          id: 'decision',
                          header: 'Decision',
                          cell: (r) => {
                            const dec = r.EvalDecision || r.decision || 'allowed';
                            return (
                              <StatusIndicator type={dec.toLowerCase().includes('allow') ? 'success' : 'error'}>
                                {dec.toUpperCase()}
                              </StatusIndicator>
                            );
                          },
                          width: 140,
                        },
                      ]}
                      items={simResults}
                    />
                  )}
                </SpaceBetween>
              </Container>
            ),
          },
        ]}
      />

      {/* Create User Modal */}
      <Modal
        visible={createUserOpen}
        onDismiss={() => setCreateUserOpen(false)}
        header="Create IAM User"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateUserOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingUser} onClick={handleCreateUser}>
                Create User
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="User Name" description="Unique alphanumeric IAM username.">
          <Input
            value={newUserName}
            onChange={({ detail }) => setNewUserName(detail.value)}
            placeholder="developer-alice"
          />
        </FormField>
      </Modal>

      {/* Create Role Modal */}
      <Modal
        visible={createRoleOpen}
        onDismiss={() => setCreateRoleOpen(false)}
        header="Create IAM Role"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateRoleOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingRole} onClick={handleCreateRole}>
                Create Role
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Role Name" description="Unique role identifier.">
            <Input
              value={newRoleName}
              onChange={({ detail }) => setNewRoleName(detail.value)}
              placeholder="LambdaExecutionRole"
            />
          </FormField>

          <FormField label="Trusted Entity Service (Trust Template)">
            <Select
              selectedOption={selectedTemplateKey}
              onChange={({ detail }) => {
                const opt = detail.selectedOption as any;
                setSelectedTemplateKey(opt);
                if (TRUST_TEMPLATES[opt.value]) {
                  setCustomTrustDoc(JSON.stringify(TRUST_TEMPLATES[opt.value].doc, null, 2));
                }
              }}
              options={Object.entries(TRUST_TEMPLATES).map(([key, val]) => ({
                label: val.label,
                value: key,
              }))}
            />
          </FormField>

          <FormField label="Trust Policy Document (JSON)">
            <Textarea
              rows={8}
              value={customTrustDoc}
              onChange={({ detail }) => setCustomTrustDoc(detail.value)}
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Create Policy Modal */}
      <Modal
        visible={createPolicyOpen}
        onDismiss={() => setCreatePolicyOpen(false)}
        header="Create IAM Managed Policy"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreatePolicyOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingPolicy} onClick={handleCreatePolicy}>
                Create Policy
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Policy Name">
            <Input
              value={newPolicyName}
              onChange={({ detail }) => setNewPolicyName(detail.value)}
              placeholder="CustomAppPolicy"
            />
          </FormField>

          <FormField label="Description">
            <Input
              value={policyDescription}
              onChange={({ detail }) => setPolicyDescription(detail.value)}
              placeholder="Policy description"
            />
          </FormField>

          <FormField label="Policy Template Preset">
            <Select
              selectedOption={policyTemplateKey}
              onChange={({ detail }) => {
                const opt = detail.selectedOption as any;
                setPolicyTemplateKey(opt);
                if (POLICY_TEMPLATES[opt.value]) {
                  setCustomPolicyDoc(JSON.stringify(POLICY_TEMPLATES[opt.value].doc, null, 2));
                }
              }}
              options={Object.entries(POLICY_TEMPLATES).map(([key, val]) => ({
                label: val.label,
                value: key,
              }))}
            />
          </FormField>

          <FormField label="Policy Document (JSON)">
            <Textarea
              rows={10}
              value={customPolicyDoc}
              onChange={({ detail }) => setCustomPolicyDoc(detail.value)}
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Attach Policy Modal */}
      <Modal
        visible={attachPolicyOpen}
        onDismiss={() => setAttachPolicyOpen(false)}
        header={`Attach Policy to ${attachTargetType === 'user' ? activeUser?.UserName : activeRole?.RoleName}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setAttachPolicyOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={attachingPolicy} onClick={handleAttachPolicy}>
                Attach Policy
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Policy ARN" description="Enter ARN of the policy to attach.">
          <Input
            value={attachPolicyArn}
            onChange={({ detail }) => setAttachPolicyArn(detail.value)}
            placeholder="arn:aws:iam::aws:policy/AdministratorAccess"
          />
        </FormField>
      </Modal>

      {/* Add User to Group Modal */}
      <Modal
        visible={addToGroupOpen}
        onDismiss={() => setAddToGroupOpen(false)}
        header={`Add "${activeUser?.UserName}" to User Group`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setAddToGroupOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={addingToGroup} onClick={handleAddUserToGroup}>
                Add to Group
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Select Group">
          <Select
            selectedOption={selectedGroupName ? { label: selectedGroupName, value: selectedGroupName } : null}
            onChange={({ detail }) => setSelectedGroupName(detail.selectedOption.value || '')}
            options={groups.map((g: any) => ({ label: g.GroupName || g.name, value: g.GroupName || g.name }))}
            placeholder="Choose a group..."
          />
        </FormField>
      </Modal>

      {/* Create Group Modal */}
      <Modal
        visible={createGroupOpen}
        onDismiss={() => setCreateGroupOpen(false)}
        header="Create IAM User Group"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateGroupOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingGroup} onClick={handleCreateGroup}>
                Create Group
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Group Name">
          <Input
            value={newGroupName}
            onChange={({ detail }) => setNewGroupName(detail.value)}
            placeholder="Administrators"
          />
        </FormField>
      </Modal>
    </SpaceBetween>
  );
};
