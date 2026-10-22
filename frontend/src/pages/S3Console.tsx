import { useSession } from '../components/SessionContext';
import React, { useState, useEffect, useRef } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Table from '../components/ResourceTable';
import Button from '@cloudscape-design/components/button';
import ButtonDropdown from '@cloudscape-design/components/button-dropdown';
import SpaceBetween from '@cloudscape-design/components/space-between';
import TextFilter from '@cloudscape-design/components/text-filter';
import Tabs from '@cloudscape-design/components/tabs';
import Modal from '@cloudscape-design/components/modal';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Textarea from '@cloudscape-design/components/textarea';
import Select from '@cloudscape-design/components/select';
import Multiselect from '@cloudscape-design/components/multiselect';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Grid from '@cloudscape-design/components/grid';
import Box from '@cloudscape-design/components/box';
import Alert from '@cloudscape-design/components/alert';
import Link from '@cloudscape-design/components/link';
import Checkbox from '@cloudscape-design/components/checkbox';
import BreadcrumbGroup from '@cloudscape-design/components/breadcrumb-group';
import FileUpload from '@cloudscape-design/components/file-upload';
import ConfirmActionModal from '../components/ConfirmActionModal';
import { formatAwsDate, formatBytes } from '../format';
import {
  fetchServiceInventory,
  fetchS3Objects,
  createS3Bucket,
  deleteS3Bucket,
  emptyS3Bucket,
  uploadS3Object,
  s3DownloadUrl,
  presignS3Object,
  fetchS3Website,
  putS3Website,
  deleteS3Website,
  fetchS3Notifications,
  putS3Notifications,
  deleteS3Object,
  createS3Folder,
  fetchS3BucketPolicy,
  putS3BucketPolicy,
  deleteS3BucketPolicy,
  fetchS3BucketCors,
  putS3BucketCors,
  deleteS3BucketCors,
  fetchS3BucketVersioning,
  putS3BucketVersioning,
  fetchS3BucketPublicAccessBlock,
  putS3BucketPublicAccessBlock,
  fetchS3BucketEncryption,
  fetchS3BucketTags,
  fetchS3ObjectTags,
  putS3ObjectTags,
  fetchS3ObjectHead,
  S3ObjectListing,
  S3PublicAccessBlock,
} from '../api/client';

interface BucketItem {
  Name: string;
  CreationDate?: string;
  Region?: string;
  Access?: string;
  ObjectLock?: boolean;
}

interface S3ConsoleProps {
  resourcePath?: string;
  onNavigate?: (path: string) => void;
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

type VersioningStatus = 'Enabled' | 'Suspended' | 'Disabled' | 'Unavailable';

const CORS_EXAMPLE = JSON.stringify(
  [{ AllowedHeaders: ['*'], AllowedMethods: ['GET', 'HEAD'], AllowedOrigins: ['http://localhost:3000'], ExposeHeaders: ['ETag'], MaxAgeSeconds: 3000 }],
  null,
  2,
);

const EVENT_TYPE_OPTIONS = [
  { label: 'All object create events', value: 's3:ObjectCreated:*' },
  { label: 'Put', value: 's3:ObjectCreated:Put' },
  { label: 'Post', value: 's3:ObjectCreated:Post' },
  { label: 'Copy', value: 's3:ObjectCreated:Copy' },
  { label: 'Multipart upload completed', value: 's3:ObjectCreated:CompleteMultipartUpload' },
  { label: 'All object removal events', value: 's3:ObjectRemoved:*' },
  { label: 'Permanently deleted', value: 's3:ObjectRemoved:Delete' },
  { label: 'Delete marker created', value: 's3:ObjectRemoved:DeleteMarkerCreated' },
  { label: 'All restore object events', value: 's3:ObjectRestore:*' },
  { label: 'Object tagging (all)', value: 's3:ObjectTagging:*' },
];

const DESTINATION_OPTIONS = [
  { label: 'Lambda function', value: 'lambda' },
  { label: 'SNS topic', value: 'sns' },
  { label: 'SQS queue', value: 'sqs' },
];

/** S3 bucket naming rules for general purpose buckets. Returns an error message or ''. */
export function validateBucketName(name: string): string {
  if (name.length < 3 || name.length > 63) return 'Bucket names must be between 3 and 63 characters long.';
  if (!/^[a-z0-9.-]+$/.test(name)) return 'Bucket names can consist only of lowercase letters, numbers, dots (.), and hyphens (-).';
  if (!/^[a-z0-9].*[a-z0-9]$/.test(name)) return 'Bucket names must begin and end with a letter or number.';
  if (name.includes('..')) return 'Bucket names must not contain two adjacent periods.';
  if (/^\d+\.\d+\.\d+\.\d+$/.test(name)) return 'Bucket names must not be formatted as an IP address.';
  if (name.startsWith('xn--') || name.startsWith('sthree-')) return 'Bucket names must not start with the prefix "xn--" or "sthree-".';
  if (name.endsWith('-s3alias') || name.endsWith('--ol-s3') || name.endsWith('.mrap') || name.endsWith('--x-s3')) return 'Bucket names must not end with a reserved suffix such as "-s3alias" or "--ol-s3".';
  return '';
}

function policyAllowsPublicAccess(policy: any): boolean {
  const statements = policy?.Statement ? (Array.isArray(policy.Statement) ? policy.Statement : [policy.Statement]) : [];
  return statements.some((statement: any) => {
    if (statement.Effect !== 'Allow' || statement.Condition) return false;
    const principal = statement.Principal;
    return principal === '*' || principal?.AWS === '*' || (Array.isArray(principal?.AWS) && principal.AWS.includes('*'));
  });
}

/** Approximates the S3 console "Access" column from Block Public Access and the bucket policy. */
function bucketAccess(bucket: any): string | undefined {
  if (!('public_access_block' in bucket)) return undefined;
  const pab: S3PublicAccessBlock = bucket.public_access_block || {};
  if (pab.BlockPublicAcls && pab.IgnorePublicAcls && pab.BlockPublicPolicy && pab.RestrictPublicBuckets) return 'Bucket and objects not public';
  if (policyAllowsPublicAccess(bucket.policy) && !pab.RestrictPublicBuckets) return 'Public';
  return 'Objects can be public';
}

function describeEncryption(configuration: any): string {
  const rule = configuration?.Rules?.[0]?.ApplyServerSideEncryptionByDefault;
  if (!rule) return 'None';
  if (rule.SSEAlgorithm === 'AES256') return 'Server-side encryption with Amazon S3 managed keys (SSE-S3)';
  if (rule.SSEAlgorithm === 'aws:kms') return `Server-side encryption with AWS Key Management Service keys (SSE-KMS)${rule.KMSMasterKeyID ? ` · ${rule.KMSMasterKeyID}` : ''}`;
  if (rule.SSEAlgorithm === 'aws:kms:dsse') return 'Dual-layer server-side encryption with AWS KMS keys (DSSE-KMS)';
  return rule.SSEAlgorithm;
}

function objectType(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot + 1) : '-';
}

interface NotificationRow {
  id: string;
  kind: 'QueueConfigurations' | 'TopicConfigurations' | 'LambdaFunctionConfigurations';
  destinationType: string;
  destination: string;
  events: string[];
  index: number;
}

function notificationRows(config: any): NotificationRow[] {
  const rows: NotificationRow[] = [];
  const add = (kind: NotificationRow['kind'], destinationType: string, arnField: string) => {
    (config?.[kind] || []).forEach((item: any, index: number) => rows.push({
      id: item.Id || '-', kind, destinationType, destination: item[arnField], events: item.Events || [], index,
    }));
  };
  add('LambdaFunctionConfigurations', 'Lambda function', 'LambdaFunctionArn');
  add('TopicConfigurations', 'SNS topic', 'TopicArn');
  add('QueueConfigurations', 'SQS queue', 'QueueArn');
  return rows;
}

export const S3Console: React.FC<S3ConsoleProps> = ({ activeTab, onTabChange, resourcePath, onNavigate }) => {
  const { identity } = useSession();
  const [inventoryError, setInventoryError] = useState('');
  const requestedResource = new URLSearchParams((resourcePath || '').split('?')[1] || '').get('bucket');
  const detailGeneration = useRef(0);
  const [buckets, setBuckets] = useState<BucketItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedBuckets, setSelectedBuckets] = useState<BucketItem[]>([]);
  const [filterText, setFilterText] = useState('');
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error' | 'info'; text: string } | null>(null);
  const [selectedTabId, setSelectedTabId] = useState(activeTab || 'objects');
  const navigateResource = (name: string, tab = selectedTabId) => {
    if (onNavigate) onNavigate(`s3/${tab}?${new URLSearchParams({ bucket: name })}`);
    else onTabChange?.(tab);
  };

  useEffect(() => {
    if (activeTab) setSelectedTabId(activeTab);
  }, [activeTab]);

  // Bucket-level dialogs
  const [createBucketOpen, setCreateBucketOpen] = useState(false);
  const [newBucketName, setNewBucketName] = useState('');
  const [creatingBucket, setCreatingBucket] = useState(false);
  const [deleteBucketOpen, setDeleteBucketOpen] = useState(false);
  const [emptyBucketOpen, setEmptyBucketOpen] = useState(false);
  const [bucketBusy, setBucketBusy] = useState(false);

  // Objects
  const [prefix, setPrefix] = useState('');
  const [listing, setListing] = useState<S3ObjectListing | null>(null);
  const [objectsError, setObjectsError] = useState('');
  const [selectedObjects, setSelectedObjects] = useState<any[]>([]);
  const [loadingObjects, setLoadingObjects] = useState(false);
  const [objectFilter, setObjectFilter] = useState('');
  const [deleteObjectOpen, setDeleteObjectOpen] = useState(false);
  const [deletingObject, setDeletingObject] = useState(false);

  // Object details
  const [inspectModalOpen, setInspectModalOpen] = useState(false);
  const [inspectObject, setInspectObject] = useState<any | null>(null);
  const [objectHeadData, setObjectHeadData] = useState<any | null>(null);
  const [objectTags, setObjectTags] = useState<Array<{ Key: string; Value: string }>>([]);
  const [newTagKey, setNewTagKey] = useState('');
  const [newTagValue, setNewTagValue] = useState('');
  const [savingTags, setSavingTags] = useState(false);

  // Upload
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [uploadKey, setUploadKey] = useState('');
  const [uploadContent, setUploadContent] = useState('');
  const [uploadContentType, setUploadContentType] = useState('text/plain');
  const [uploading, setUploading] = useState(false);

  // Folder
  const [folderModalOpen, setFolderModalOpen] = useState(false);
  const [folderName, setFolderName] = useState('');
  const [creatingFolder, setCreatingFolder] = useState(false);

  // Presign
  const [presignModalOpen, setPresignModalOpen] = useState(false);
  const [presignExpires, setPresignExpires] = useState({ label: '1 hour', value: '3600' });
  const [presignedUrl, setPresignedUrl] = useState('');
  const [generatingPresign, setGeneratingPresign] = useState(false);
  const [copiedPresign, setCopiedPresign] = useState(false);

  // Properties
  const [versioningStatus, setVersioningStatus] = useState<VersioningStatus>('Disabled');
  const [updatingVersioning, setUpdatingVersioning] = useState(false);
  const [encryption, setEncryption] = useState<any | null | undefined>(undefined);
  const [bucketTags, setBucketTags] = useState<Array<{ Key: string; Value: string }>>([]);

  // Permissions
  const [bucketPolicyText, setBucketPolicyText] = useState('');
  const [savedPolicy, setSavedPolicy] = useState('');
  const [savingPolicy, setSavingPolicy] = useState(false);
  const [deletePolicyOpen, setDeletePolicyOpen] = useState(false);
  const [corsText, setCorsText] = useState('');
  const [savedCors, setSavedCors] = useState(false);
  const [savingCors, setSavingCors] = useState(false);
  const [pab, setPab] = useState<S3PublicAccessBlock | null>(null);
  const [pabDraft, setPabDraft] = useState<Required<S3PublicAccessBlock>>({ BlockPublicAcls: false, IgnorePublicAcls: false, BlockPublicPolicy: false, RestrictPublicBuckets: false });
  const [savingPab, setSavingPab] = useState(false);
  const [confirmPabOpen, setConfirmPabOpen] = useState(false);

  // Website
  const [websiteConfig, setWebsiteConfig] = useState<any | null>(null);
  const [websiteEndpoint, setWebsiteEndpoint] = useState<string | null>(null);
  const [websiteIndexDoc, setWebsiteIndexDoc] = useState('index.html');
  const [websiteErrorDoc, setWebsiteErrorDoc] = useState('');
  const [savingWebsite, setSavingWebsite] = useState(false);

  // Notifications
  const [notificationsConfig, setNotificationsConfig] = useState<any>({});
  const [addNotificationOpen, setAddNotificationOpen] = useState(false);
  const [notifName, setNotifName] = useState('');
  const [notifTargetType, setNotifTargetType] = useState(DESTINATION_OPTIONS[2]);
  const [notifTargetArn, setNotifTargetArn] = useState('');
  const [notifEvents, setNotifEvents] = useState<readonly { label?: string; value?: string }[]>([EVENT_TYPE_OPTIONS[0]]);
  const [notifPrefix, setNotifPrefix] = useState('');
  const [notifSuffix, setNotifSuffix] = useState('');
  const [savingNotifications, setSavingNotifications] = useState(false);
  const [notificationToDelete, setNotificationToDelete] = useState<NotificationRow | null>(null);

  const loadBuckets = async () => {
    setLoading(true);
    setInventoryError('');
    try {
      const data = await fetchServiceInventory('s3');
      if (data.error) throw new Error(data.error);
      const list: BucketItem[] = (data.buckets || data.items || []).map((b: any) => ({
        Name: typeof b === 'string' ? b : b.Name || b.name,
        CreationDate: b.CreationDate || b.created,
        Region: b.Region || b.region || b.location || undefined,
        Access: typeof b === 'string' ? undefined : bucketAccess(b),
        ObjectLock: Boolean(b.object_lock?.ObjectLockEnabled === 'Enabled'),
      }));
      setBuckets(list);
      if (list.length > 0 && selectedBuckets.length === 0 && !requestedResource) {
        setSelectedBuckets([list[0]]);
      }
    } catch (err) {
      setInventoryError(err instanceof Error ? err.message : 'Inventory unavailable');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadBuckets();
  }, []);

  useEffect(() => {
    if (requestedResource) setSelectedBuckets(buckets.filter(item => item.Name === requestedResource));
  }, [requestedResource, buckets]);

  const activeBucket = selectedBuckets.length > 0 ? selectedBuckets[0] : null;

  const loadObjects = async (bucket: string, nextPrefix: string, generation = detailGeneration.current) => {
    setLoadingObjects(true);
    setObjectsError('');
    setSelectedObjects([]);
    try {
      const data = await fetchS3Objects(bucket, nextPrefix);
      if (generation !== detailGeneration.current) return;
      setListing(data);
    } catch (err) {
      if (generation !== detailGeneration.current) return;
      setListing(null);
      setObjectsError(err instanceof Error ? err.message : 'Failed to list objects');
    } finally {
      if (generation === detailGeneration.current) setLoadingObjects(false);
    }
  };

  /** Runs one bucket read and ignores the result if the user has moved to another bucket. */
  const settle = async <T,>(generation: number, load: () => Promise<T>, apply: (value: T) => void, fail: (message: string) => void) => {
    try {
      const value = await load();
      if (generation === detailGeneration.current) apply(value);
    } catch (err) {
      if (generation === detailGeneration.current) fail(err instanceof Error ? err.message : 'Request failed');
    }
  };

  const loadBucketDetails = async (bucket: BucketItem) => {
    const generation = ++detailGeneration.current;
    setInspectModalOpen(false);
    setListing(null);
    setPrefix('');
    const name = bucket.Name;
    await Promise.all([
      loadObjects(name, '', generation),
      settle(generation, () => fetchS3Website(name), ws => {
        setWebsiteConfig(ws.configuration);
        setWebsiteEndpoint(ws.endpoint);
        setWebsiteIndexDoc(ws.configuration?.IndexDocument?.Suffix || 'index.html');
        setWebsiteErrorDoc(ws.configuration?.ErrorDocument?.Key || '');
      }, () => { setWebsiteConfig(null); setWebsiteEndpoint(null); }),
      settle(generation, () => fetchS3Notifications(name), setNotificationsConfig, () => setNotificationsConfig({})),
      settle(generation, () => fetchS3BucketVersioning(name), v => setVersioningStatus(v.status), () => setVersioningStatus('Unavailable')),
      settle(generation, () => fetchS3BucketPolicy(name), text => { setBucketPolicyText(text); setSavedPolicy(text); }, () => { setBucketPolicyText(''); setSavedPolicy(''); }),
      settle(generation, () => fetchS3BucketCors(name), rules => { setCorsText(rules.length ? JSON.stringify(rules, null, 2) : ''); setSavedCors(rules.length > 0); }, () => { setCorsText(''); setSavedCors(false); }),
      settle(generation, () => fetchS3BucketPublicAccessBlock(name), config => {
        setPab(config);
        setPabDraft({
          BlockPublicAcls: Boolean(config?.BlockPublicAcls),
          IgnorePublicAcls: Boolean(config?.IgnorePublicAcls),
          BlockPublicPolicy: Boolean(config?.BlockPublicPolicy),
          RestrictPublicBuckets: Boolean(config?.RestrictPublicBuckets),
        });
      }, () => setPab(null)),
      settle(generation, () => fetchS3BucketEncryption(name), setEncryption, () => setEncryption(undefined)),
      settle(generation, () => fetchS3BucketTags(name), setBucketTags, () => setBucketTags([])),
    ]);
  };

  useEffect(() => {
    if (activeBucket) loadBucketDetails(activeBucket);
    return () => { detailGeneration.current += 1; };
  }, [activeBucket?.Name]);

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

  const bucketNameError = newBucketName ? validateBucketName(newBucketName) : '';

  const handleCreateBucket = async () => {
    const name = newBucketName.trim();
    if (!name || validateBucketName(name)) return;
    setCreatingBucket(true);
    const ok = await run(async () => {
      await createS3Bucket(name, identity?.region || undefined);
      return `Successfully created bucket "${name}".`;
    }, 'Failed to create bucket');
    setCreatingBucket(false);
    if (ok) {
      setCreateBucketOpen(false);
      setNewBucketName('');
      await loadBuckets();
    }
  };

  const handleDeleteBucket = async () => {
    if (!activeBucket) return;
    setBucketBusy(true);
    const ok = await run(async () => {
      await deleteS3Bucket(activeBucket.Name);
      return `Successfully deleted bucket "${activeBucket.Name}".`;
    }, 'Failed to delete bucket');
    setBucketBusy(false);
    setDeleteBucketOpen(false);
    if (ok) {
      setSelectedBuckets([]);
      await loadBuckets();
    }
  };

  const handleEmptyBucket = async () => {
    if (!activeBucket) return;
    setBucketBusy(true);
    await run(async () => {
      const result = await emptyS3Bucket(activeBucket.Name);
      return `Successfully emptied bucket "${activeBucket.Name}" (${result.deleted_count} objects deleted).`;
    }, 'Failed to empty bucket');
    setBucketBusy(false);
    setEmptyBucketOpen(false);
    await loadObjects(activeBucket.Name, prefix);
  };

  const handleUploadObject = async () => {
    if (!activeBucket) return;
    const file = uploadFiles[0];
    const key = `${prefix}${uploadKey.trim() || file?.name || ''}`;
    if (!key || key === prefix) return;
    setUploading(true);
    const ok = await run(async () => {
      await uploadS3Object(activeBucket.Name, key, file ? { file } : { text: uploadContent, contentType: uploadContentType });
      return `Upload succeeded: ${key}`;
    }, 'Upload failed');
    setUploading(false);
    if (ok) {
      setUploadModalOpen(false);
      setUploadKey('');
      setUploadContent('');
      setUploadFiles([]);
      await loadObjects(activeBucket.Name, prefix);
    }
  };

  const handleCreateFolder = async () => {
    if (!activeBucket || !folderName.trim()) return;
    if (folderName.includes('/')) {
      setActionMessage({ type: 'error', text: 'Folder names can\'t contain "/".' });
      return;
    }
    setCreatingFolder(true);
    const ok = await run(async () => {
      await createS3Folder(activeBucket.Name, `${prefix}${folderName.trim()}/`);
      return `Successfully created folder "${folderName.trim()}".`;
    }, 'Failed to create folder');
    setCreatingFolder(false);
    if (ok) {
      setFolderModalOpen(false);
      setFolderName('');
      await loadObjects(activeBucket.Name, prefix);
    }
  };

  const handleDeleteObject = async () => {
    if (!activeBucket || !selectedObjects.length) return;
    const obj = selectedObjects[0];
    setDeletingObject(true);
    const ok = await run(async () => {
      await deleteS3Object(activeBucket.Name, obj.key);
      return versioningStatus === 'Enabled'
        ? `Deleted "${obj.key}". Versioning is enabled, so S3 added a delete marker and kept the previous versions.`
        : `Successfully deleted "${obj.key}".`;
    }, 'Failed to delete object');
    setDeletingObject(false);
    setDeleteObjectOpen(false);
    if (ok) {
      setInspectObject(null);
      await loadObjects(activeBucket.Name, prefix);
    }
  };

  const handleInspectObject = async (obj: any) => {
    setInspectObject(obj);
    setObjectHeadData(null);
    setObjectTags([]);
    setInspectModalOpen(true);
    if (!activeBucket) return;
    try {
      const [head, tags] = await Promise.all([
        fetchS3ObjectHead(activeBucket.Name, obj.key),
        fetchS3ObjectTags(activeBucket.Name, obj.key),
      ]);
      setObjectHeadData(head);
      setObjectTags(tags);
    } catch (err) {
      setActionMessage({ type: 'error', text: err instanceof Error ? err.message : 'Failed to read object' });
    }
  };

  const saveObjectTags = async (updatedTags: Array<{ Key: string; Value: string }>, message: string) => {
    if (!activeBucket || !inspectObject) return;
    setSavingTags(true);
    const ok = await run(async () => {
      await putS3ObjectTags(activeBucket.Name, inspectObject.key, updatedTags);
      return message;
    }, 'Failed to update tags');
    if (ok) setObjectTags(updatedTags);
    setSavingTags(false);
  };

  const handleAddObjectTag = async () => {
    const key = newTagKey.trim();
    if (!key) return;
    const updated = [...objectTags.filter(t => t.Key !== key), { Key: key, Value: newTagValue.trim() }];
    if (updated.length > 10) {
      setActionMessage({ type: 'error', text: 'An object can have up to 10 tags.' });
      return;
    }
    await saveObjectTags(updated, 'Successfully edited object tags.');
    setNewTagKey('');
    setNewTagValue('');
  };

  const handleSetVersioning = async (status: 'Enabled' | 'Suspended') => {
    if (!activeBucket) return;
    setUpdatingVersioning(true);
    const ok = await run(async () => {
      await putS3BucketVersioning(activeBucket.Name, status);
      return `Successfully edited Bucket Versioning: ${status}.`;
    }, 'Failed to update versioning');
    if (ok) setVersioningStatus(status);
    setUpdatingVersioning(false);
  };

  const handleSavePolicy = async () => {
    if (!activeBucket || !bucketPolicyText.trim()) return;
    setSavingPolicy(true);
    const ok = await run(async () => {
      await putS3BucketPolicy(activeBucket.Name, bucketPolicyText.trim());
      return 'Successfully edited bucket policy.';
    }, 'Failed to save bucket policy');
    if (ok) setSavedPolicy(bucketPolicyText.trim());
    setSavingPolicy(false);
  };

  const handleDeletePolicy = async () => {
    if (!activeBucket) return;
    setSavingPolicy(true);
    const ok = await run(async () => {
      await deleteS3BucketPolicy(activeBucket.Name);
      return 'Successfully deleted bucket policy.';
    }, 'Failed to delete bucket policy');
    if (ok) { setBucketPolicyText(''); setSavedPolicy(''); }
    setSavingPolicy(false);
    setDeletePolicyOpen(false);
  };

  const handleSaveCors = async () => {
    if (!activeBucket) return;
    setSavingCors(true);
    const ok = await run(async () => {
      if (!corsText.trim()) {
        await deleteS3BucketCors(activeBucket.Name);
        return 'Successfully deleted the CORS configuration.';
      }
      let parsed: unknown;
      try {
        parsed = JSON.parse(corsText.trim());
      } catch {
        throw new Error('CORS configuration is not valid JSON.');
      }
      await putS3BucketCors(activeBucket.Name, parsed);
      return 'Successfully edited cross-origin resource sharing (CORS).';
    }, 'Failed to save CORS configuration');
    if (ok) setSavedCors(Boolean(corsText.trim()));
    setSavingCors(false);
  };

  const pabTurnsSomethingOff = Boolean(pab) && (Object.keys(pabDraft) as Array<keyof S3PublicAccessBlock>).some(key => pab?.[key] && !pabDraft[key]);

  const handleSavePab = async () => {
    if (!activeBucket) return;
    setSavingPab(true);
    const ok = await run(async () => {
      await putS3BucketPublicAccessBlock(activeBucket.Name, pabDraft);
      return 'Successfully edited Block Public Access settings for this bucket.';
    }, 'Failed to update Block Public Access');
    if (ok) setPab({ ...pabDraft });
    setSavingPab(false);
    setConfirmPabOpen(false);
  };

  const handleGeneratePresignedUrl = async () => {
    if (!activeBucket || !selectedObjects.length) return;
    setGeneratingPresign(true);
    setCopiedPresign(false);
    try {
      const res = await presignS3Object(activeBucket.Name, selectedObjects[0].key, Number(presignExpires.value));
      setPresignedUrl(res.url);
    } catch (err) {
      setActionMessage({ type: 'error', text: err instanceof Error ? err.message : 'Failed to generate presigned URL' });
    } finally {
      setGeneratingPresign(false);
    }
  };

  const handleSaveWebsite = async (enabled: boolean) => {
    if (!activeBucket) return;
    if (enabled && !websiteIndexDoc.trim()) {
      setActionMessage({ type: 'error', text: 'Index document is required.' });
      return;
    }
    setSavingWebsite(true);
    const ok = await run(async () => {
      if (enabled) {
        await putS3Website(activeBucket.Name, {
          IndexDocument: { Suffix: websiteIndexDoc.trim() },
          ...(websiteErrorDoc.trim() ? { ErrorDocument: { Key: websiteErrorDoc.trim() } } : {}),
        });
        return 'Successfully edited static website hosting.';
      }
      await deleteS3Website(activeBucket.Name);
      return 'Static website hosting disabled.';
    }, 'Failed to update website configuration');
    setSavingWebsite(false);
    if (ok) {
      const ws = await fetchS3Website(activeBucket.Name).catch(() => ({ configuration: null, endpoint: null }));
      setWebsiteConfig(ws.configuration);
      setWebsiteEndpoint(ws.endpoint);
    }
  };

  const saveNotifications = async (config: any, message: string) => {
    if (!activeBucket) return false;
    setSavingNotifications(true);
    const ok = await run(async () => {
      await putS3Notifications(activeBucket.Name, config);
      return message;
    }, 'Failed to update event notifications');
    setSavingNotifications(false);
    if (ok) setNotificationsConfig(await fetchS3Notifications(activeBucket.Name).catch(() => config));
    return ok;
  };

  const handleAddNotification = async () => {
    const events = notifEvents.map(option => option.value!).filter(Boolean);
    if (!notifName.trim() || !notifTargetArn.trim() || !events.length) {
      setActionMessage({ type: 'error', text: 'Event name, at least one event type, and a destination ARN are required.' });
      return;
    }
    const rules = [
      ...(notifPrefix.trim() ? [{ Name: 'prefix', Value: notifPrefix.trim() }] : []),
      ...(notifSuffix.trim() ? [{ Name: 'suffix', Value: notifSuffix.trim() }] : []),
    ];
    const entry: any = { Id: notifName.trim(), Events: events, ...(rules.length ? { Filter: { Key: { FilterRules: rules } } } : {}) };
    const config = { ...notificationsConfig };
    if (notifTargetType.value === 'sqs') config.QueueConfigurations = [...(config.QueueConfigurations || []), { ...entry, QueueArn: notifTargetArn.trim() }];
    else if (notifTargetType.value === 'sns') config.TopicConfigurations = [...(config.TopicConfigurations || []), { ...entry, TopicArn: notifTargetArn.trim() }];
    else config.LambdaFunctionConfigurations = [...(config.LambdaFunctionConfigurations || []), { ...entry, LambdaFunctionArn: notifTargetArn.trim() }];
    if (await saveNotifications(config, `Successfully created event notification "${notifName.trim()}".`)) {
      setAddNotificationOpen(false);
      setNotifName('');
      setNotifTargetArn('');
      setNotifPrefix('');
      setNotifSuffix('');
    }
  };

  const handleDeleteNotification = async () => {
    if (!notificationToDelete) return;
    const config = { ...notificationsConfig };
    config[notificationToDelete.kind] = (config[notificationToDelete.kind] || []).filter((_: any, index: number) => index !== notificationToDelete.index);
    await saveNotifications(config, `Successfully deleted event notification "${notificationToDelete.id}".`);
    setNotificationToDelete(null);
  };

  const filteredBuckets = buckets.filter(b => b.Name.toLowerCase().includes(filterText.toLowerCase()));
  const objectRows = [
    ...(listing?.folders || []).map(folder => ({ ...folder, key: folder.prefix, type: 'folder' as const })),
    ...(listing?.objects || []).map(object => ({ ...object, type: 'file' as const })),
  ].filter(row => row.name.toLowerCase().includes(objectFilter.toLowerCase()));
  const selectedObject = selectedObjects[0]?.type === 'file' ? selectedObjects[0] : null;
  const prefixParts = prefix.split('/').filter(Boolean);
  const openPrefix = (next: string) => {
    setPrefix(next);
    setObjectFilter('');
    if (activeBucket) loadObjects(activeBucket.Name, next);
  };
  const notifications = notificationRows(notificationsConfig);
  const versioningIndicator = versioningStatus === 'Enabled' ? 'success' : versioningStatus === 'Suspended' ? 'warning' : versioningStatus === 'Unavailable' ? 'error' : 'stopped';

  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header
            variant="h1"
            description="Amazon S3 stores data as objects within buckets. Floci emulates general purpose buckets locally."
            actions={<Button iconName="refresh" onClick={loadBuckets} loading={loading} ariaLabel="Refresh buckets">Refresh</Button>}
          >
            Amazon S3
          </Header>
        }
      >
        <SpaceBetween size="m">
          {actionMessage && (
            <Alert type={actionMessage.type} dismissible onDismiss={() => setActionMessage(null)}>
              {actionMessage.text}
            </Alert>
          )}
          {inventoryError && <Alert type="error" header="Unable to list buckets">{inventoryError}</Alert>}
          <KeyValuePairs
            columns={3}
            items={[
              { label: 'General purpose buckets', value: loading ? <StatusIndicator type="loading">Loading</StatusIndicator> : String(buckets.length) },
              { label: 'Region', value: identity?.region || '-' },
              { label: 'Endpoint', value: identity?.endpoint || '-' },
            ]}
          />
        </SpaceBetween>
      </Container>

      <Table
        header={
          <Header
            variant="h2"
            counter={`(${buckets.length})`}
            description="Buckets are containers for data stored in S3."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button disabled={!activeBucket} onClick={() => setEmptyBucketOpen(true)}>Empty</Button>
                <Button disabled={!activeBucket} onClick={() => setDeleteBucketOpen(true)}>Delete</Button>
                <Button variant="primary" onClick={() => setCreateBucketOpen(true)}>Create bucket</Button>
              </SpaceBetween>
            }
          >
            General purpose buckets
          </Header>
        }
        filter={
          <TextFilter
            filteringText={filterText}
            filteringPlaceholder="Find buckets by name"
            onChange={({ detail }) => setFilterText(detail.filteringText)}
          />
        }
        columnDefinitions={[
          {
            id: 'name',
            header: 'Name',
            cell: item => (
              <Button variant="inline-link" onClick={() => { setSelectedBuckets([item]); navigateResource(item.Name); }}>
                {item.Name}
              </Button>
            ),
            sortingField: 'Name',
          },
          { id: 'region', header: 'AWS Region', cell: item => item.Region || '-', width: 160 },
          { id: 'access', header: 'Access', cell: item => item.Access || '-', width: 260 },
          { id: 'creationDate', header: 'Creation date', cell: item => formatAwsDate(item.CreationDate), width: 280, sortingField: 'CreationDate' },
        ]}
        items={filteredBuckets}
        loading={loading}
        loadingText="Loading buckets"
        selectionType="single"
        selectedItems={selectedBuckets}
        onSelectionChange={({ detail }) => { setSelectedBuckets(detail.selectedItems); if (detail.selectedItems[0]) navigateResource(detail.selectedItems[0].Name); }}
        empty={
          <Box textAlign="center" color="inherit">
            <b>No buckets</b>
            <p>You don't have any buckets.</p>
          </Box>
        }
      />

      {requestedResource && !loading && !activeBucket && <Alert type="warning">Bucket “{requestedResource}” was not found. Select an available bucket.</Alert>}
      {activeBucket && (
        <Container
          header={
            <Header variant="h2" description={`arn:aws:s3:::${activeBucket.Name}`} info={activeBucket.Region ? <Box color="text-status-inactive">{activeBucket.Region}</Box> : undefined}>
              Bucket: {activeBucket.Name}
            </Header>
          }
        >
          <Tabs
            activeTabId={selectedTabId}
            onChange={({ detail }) => {
              setSelectedTabId(detail.activeTabId);
              navigateResource(activeBucket.Name, detail.activeTabId);
            }}
            tabs={[
              {
                label: 'Objects',
                id: 'objects',
                content: (
                  <Table
                    header={
                      <Header
                        variant="h3"
                        counter={listing ? `(${(listing.folders || []).length + (listing.objects || []).length})` : undefined}
                        description="Objects are the fundamental entities stored in Amazon S3. Prefixes ending in / are shown as folders."
                        actions={
                          <SpaceBetween direction="horizontal" size="xs">
                            <Button iconName="refresh" ariaLabel="Refresh objects" onClick={() => loadObjects(activeBucket.Name, prefix)} />
                            <Button disabled={!selectedObject} iconName="download" onClick={() => selectedObject && window.open(s3DownloadUrl(activeBucket.Name, selectedObject.key), '_blank')}>Download</Button>
                            <ButtonDropdown
                              disabled={!selectedObject}
                              items={[
                                { id: 'details', text: 'View details and tags' },
                                { id: 'presign', text: 'Share with a presigned URL' },
                              ]}
                              onItemClick={({ detail }) => {
                                if (!selectedObject) return;
                                if (detail.id === 'details') handleInspectObject(selectedObject);
                                if (detail.id === 'presign') { setPresignedUrl(''); setPresignModalOpen(true); }
                              }}
                            >
                              Actions
                            </ButtonDropdown>
                            <Button disabled={!selectedObject} onClick={() => setDeleteObjectOpen(true)}>Delete</Button>
                            <Button onClick={() => setFolderModalOpen(true)}>Create folder</Button>
                            <Button variant="primary" iconName="upload" onClick={() => setUploadModalOpen(true)}>Upload</Button>
                          </SpaceBetween>
                        }
                      >
                        Objects
                      </Header>
                    }
                    filter={
                      <SpaceBetween size="xs">
                        <BreadcrumbGroup
                          ariaLabel="Object prefix"
                          items={[{ text: activeBucket.Name, href: '#' }, ...prefixParts.map((part, index) => ({ text: part, href: `#${prefixParts.slice(0, index + 1).join('/')}/` }))]}
                          onFollow={event => {
                            event.preventDefault();
                            const href = event.detail.href;
                            openPrefix(href === '#' ? '' : href.slice(1));
                          }}
                        />
                        <TextFilter
                          filteringText={objectFilter}
                          filteringPlaceholder="Find objects by prefix"
                          onChange={({ detail }) => setObjectFilter(detail.filteringText)}
                        />
                      </SpaceBetween>
                    }
                    columnDefinitions={[
                      {
                        id: 'name',
                        header: 'Name',
                        cell: item => item.type === 'folder'
                          ? <Button variant="inline-link" iconName="folder" onClick={() => openPrefix(item.key)}>{item.name}/</Button>
                          : <Button variant="inline-link" onClick={() => handleInspectObject(item)}>{item.name}</Button>,
                      },
                      { id: 'type', header: 'Type', cell: item => item.type === 'folder' ? 'Folder' : objectType(item.name), width: 110 },
                      { id: 'modified', header: 'Last modified', cell: item => item.type === 'folder' ? '-' : formatAwsDate((item as any).last_modified), width: 280 },
                      { id: 'size', header: 'Size', cell: item => item.type === 'folder' ? '-' : formatBytes((item as any).size), width: 110 },
                      { id: 'storageClass', header: 'Storage class', cell: item => item.type === 'folder' ? '-' : ((item as any).storage_class || '-'), width: 150 },
                    ]}
                    items={objectRows}
                    loading={loadingObjects}
                    loadingText="Loading objects"
                    trackBy="key"
                    selectionType="single"
                    selectedItems={selectedObjects}
                    onSelectionChange={({ detail }) => setSelectedObjects(detail.selectedItems)}
                    empty={
                      objectsError
                        ? <StatusIndicator type="error">{objectsError}</StatusIndicator>
                        : <Box textAlign="center" color="inherit"><b>No objects</b><p>You don't have any objects in this {prefix ? 'folder' : 'bucket'}.</p></Box>
                    }
                  />
                ),
              },
              {
                label: 'Properties',
                id: 'properties',
                content: (
                  <SpaceBetween size="l">
                    <Container header={<Header variant="h3">Bucket overview</Header>}>
                      <KeyValuePairs
                        columns={3}
                        items={[
                          { label: 'AWS Region', value: activeBucket.Region || '-' },
                          { label: 'Amazon Resource Name (ARN)', value: `arn:aws:s3:::${activeBucket.Name}` },
                          { label: 'Creation date', value: formatAwsDate(activeBucket.CreationDate) },
                        ]}
                      />
                    </Container>

                    <Container
                      header={
                        <Header
                          variant="h3"
                          description="Versioning keeps multiple variants of an object in the same bucket. Once enabled, it can be suspended but not disabled."
                          actions={
                            versioningStatus === 'Enabled'
                              ? <Button loading={updatingVersioning} onClick={() => handleSetVersioning('Suspended')}>Suspend</Button>
                              : <Button loading={updatingVersioning} disabled={versioningStatus === 'Unavailable'} onClick={() => handleSetVersioning('Enabled')}>Enable</Button>
                          }
                        >
                          Bucket Versioning
                        </Header>
                      }
                    >
                      <KeyValuePairs columns={2} items={[{ label: 'Bucket Versioning', value: <StatusIndicator type={versioningIndicator}>{versioningStatus}</StatusIndicator> }]} />
                    </Container>

                    <Container header={<Header variant="h3" counter={`(${bucketTags.length})`} description="Bucket tags help you track storage cost and organize buckets.">Tags</Header>}>
                      {bucketTags.length ? (
                        <KeyValuePairs columns={3} items={bucketTags.map(tag => ({ label: tag.Key, value: tag.Value || '-' }))} />
                      ) : <Box color="text-status-inactive">No tags associated with this resource.</Box>}
                    </Container>

                    <Container header={<Header variant="h3" description="Server-side encryption is automatically applied to new objects stored in this bucket.">Default encryption</Header>}>
                      <KeyValuePairs
                        columns={2}
                        items={[
                          { label: 'Encryption type', value: encryption === undefined ? <StatusIndicator type="error">Unavailable</StatusIndicator> : describeEncryption(encryption) },
                          { label: 'Bucket Key', value: encryption?.Rules?.[0] ? (encryption.Rules[0].BucketKeyEnabled ? 'Enabled' : 'Disabled') : '-' },
                        ]}
                      />
                    </Container>

                    <Container header={<Header variant="h3" description="Object Lock prevents objects from being deleted or overwritten. It can only be enabled when the bucket is created.">Object Lock</Header>}>
                      <KeyValuePairs columns={2} items={[{ label: 'Object Lock', value: activeBucket.ObjectLock ? 'Enabled' : 'Disabled' }]} />
                    </Container>

                    <Table
                      header={
                        <Header
                          variant="h3"
                          counter={`(${notifications.length})`}
                          description="Send a notification when specific events occur in your bucket."
                          actions={<Button onClick={() => setAddNotificationOpen(true)}>Create event notification</Button>}
                        >
                          Event notifications
                        </Header>
                      }
                      columnDefinitions={[
                        { id: 'name', header: 'Name', cell: row => row.id },
                        { id: 'events', header: 'Event types', cell: row => row.events.join(', ') },
                        { id: 'destType', header: 'Destination type', cell: row => row.destinationType },
                        { id: 'dest', header: 'Destination', cell: row => <Box variant="code">{row.destination}</Box> },
                        { id: 'delete', header: 'Actions', cell: row => <Button variant="inline-link" onClick={() => setNotificationToDelete(row)}>Delete</Button> },
                      ]}
                      items={notifications}
                      trackBy={row => `${row.kind}-${row.index}`}
                      empty={<Box textAlign="center" color="inherit"><b>No event notifications</b><p>Choose Create event notification to be notified when a specific event occurs.</p></Box>}
                    />

                    <Container
                      header={
                        <Header
                          variant="h3"
                          description="Use this bucket to host a website. Floci serves the website endpoint locally."
                          actions={
                            websiteConfig
                              ? <SpaceBetween direction="horizontal" size="xs">
                                  <Button loading={savingWebsite} onClick={() => handleSaveWebsite(false)}>Disable</Button>
                                  <Button variant="primary" loading={savingWebsite} onClick={() => handleSaveWebsite(true)}>Save changes</Button>
                                </SpaceBetween>
                              : <Button variant="primary" loading={savingWebsite} onClick={() => handleSaveWebsite(true)}>Enable</Button>
                          }
                        >
                          Static website hosting
                        </Header>
                      }
                    >
                      <SpaceBetween size="m">
                        <KeyValuePairs
                          columns={2}
                          items={[
                            { label: 'Static website hosting', value: websiteConfig ? <StatusIndicator type="success">Enabled</StatusIndicator> : <StatusIndicator type="stopped">Disabled</StatusIndicator> },
                            { label: 'Bucket website endpoint', value: websiteConfig && websiteEndpoint ? <Link href={websiteEndpoint} external>{websiteEndpoint}</Link> : '-' },
                          ]}
                        />
                        <Grid gridDefinition={[{ colspan: { default: 12, m: 6 } }, { colspan: { default: 12, m: 6 } }]}>
                          <FormField label="Index document" description="Specify the home or default page of the website.">
                            <Input value={websiteIndexDoc} onChange={({ detail }) => setWebsiteIndexDoc(detail.value)} placeholder="index.html" />
                          </FormField>
                          <FormField label={<>Error document <i>- optional</i></>} description="This is returned when an error occurs.">
                            <Input value={websiteErrorDoc} onChange={({ detail }) => setWebsiteErrorDoc(detail.value)} placeholder="error.html" />
                          </FormField>
                        </Grid>
                        {websiteConfig && <Box variant="small">For your customers to access content at the website endpoint, you must make all your content publicly readable through Block Public Access and a bucket policy.</Box>}
                      </SpaceBetween>
                    </Container>
                  </SpaceBetween>
                ),
              },
              {
                label: 'Permissions',
                id: 'permissions',
                content: (
                  <SpaceBetween size="l">
                    <Container
                      header={
                        <Header
                          variant="h3"
                          description="Public access is granted to buckets and objects through access control lists (ACLs) and bucket policies."
                          actions={
                            <Button variant="primary" loading={savingPab} onClick={() => (pabTurnsSomethingOff ? setConfirmPabOpen(true) : handleSavePab())}>
                              Save changes
                            </Button>
                          }
                        >
                          Block public access (bucket settings)
                        </Header>
                      }
                    >
                      <SpaceBetween size="s">
                        {!pab && <Alert type="info">This bucket has no Block Public Access configuration, so every setting is Off.</Alert>}
                        <Checkbox
                          checked={pabDraft.BlockPublicAcls && pabDraft.IgnorePublicAcls && pabDraft.BlockPublicPolicy && pabDraft.RestrictPublicBuckets}
                          indeterminate={!(Object.values(pabDraft).every(Boolean) || Object.values(pabDraft).every(value => !value))}
                          onChange={({ detail }) => setPabDraft({ BlockPublicAcls: detail.checked, IgnorePublicAcls: detail.checked, BlockPublicPolicy: detail.checked, RestrictPublicBuckets: detail.checked })}
                        >
                          <strong>Block all public access</strong>
                        </Checkbox>
                        <Box padding={{ left: 'l' }}>
                          <SpaceBetween size="xs">
                            <Checkbox checked={pabDraft.BlockPublicAcls} onChange={({ detail }) => setPabDraft({ ...pabDraft, BlockPublicAcls: detail.checked })} description="BlockPublicAcls">
                              Block public access to buckets and objects granted through new access control lists (ACLs)
                            </Checkbox>
                            <Checkbox checked={pabDraft.IgnorePublicAcls} onChange={({ detail }) => setPabDraft({ ...pabDraft, IgnorePublicAcls: detail.checked })} description="IgnorePublicAcls">
                              Block public access to buckets and objects granted through any access control lists (ACLs)
                            </Checkbox>
                            <Checkbox checked={pabDraft.BlockPublicPolicy} onChange={({ detail }) => setPabDraft({ ...pabDraft, BlockPublicPolicy: detail.checked })} description="BlockPublicPolicy">
                              Block public access to buckets and objects granted through new public bucket or access point policies
                            </Checkbox>
                            <Checkbox checked={pabDraft.RestrictPublicBuckets} onChange={({ detail }) => setPabDraft({ ...pabDraft, RestrictPublicBuckets: detail.checked })} description="RestrictPublicBuckets">
                              Block public and cross-account access to buckets and objects through any public bucket or access point policies
                            </Checkbox>
                          </SpaceBetween>
                        </Box>
                      </SpaceBetween>
                    </Container>

                    <Container
                      header={
                        <Header
                          variant="h3"
                          description="The bucket policy, written in JSON, provides access to the objects stored in the bucket."
                          actions={
                            <SpaceBetween direction="horizontal" size="xs">
                              <Button disabled={!savedPolicy} onClick={() => setDeletePolicyOpen(true)}>Delete</Button>
                              <Button variant="primary" loading={savingPolicy} disabled={!bucketPolicyText.trim()} onClick={handleSavePolicy}>Save changes</Button>
                            </SpaceBetween>
                          }
                        >
                          Bucket policy
                        </Header>
                      }
                    >
                      <SpaceBetween size="s">
                        {!savedPolicy && <Box color="text-status-inactive">No policy to display.</Box>}
                        <FormField label="Policy" description={`Bucket ARN: arn:aws:s3:::${activeBucket.Name}`}>
                          <Textarea
                            rows={12}
                            value={bucketPolicyText}
                            onChange={({ detail }) => setBucketPolicyText(detail.value)}
                            placeholder={JSON.stringify({ Version: '2012-10-17', Statement: [{ Sid: 'Statement1', Effect: 'Allow', Principal: { AWS: 'arn:aws:iam::000000000000:root' }, Action: 's3:GetObject', Resource: `arn:aws:s3:::${activeBucket.Name}/*` }] }, null, 2)}
                          />
                        </FormField>
                      </SpaceBetween>
                    </Container>

                    <Container
                      header={
                        <Header
                          variant="h3"
                          description="The CORS configuration, written in JSON, defines a way for client web applications loaded in one domain to interact with resources in a different domain."
                          actions={<Button variant="primary" loading={savingCors} onClick={handleSaveCors}>Save changes</Button>}
                        >
                          Cross-origin resource sharing (CORS)
                        </Header>
                      }
                    >
                      <SpaceBetween size="s">
                        {!savedCors && <Box color="text-status-inactive">No configurations to display. Saving an empty editor deletes the CORS configuration.</Box>}
                        <Textarea rows={10} value={corsText} onChange={({ detail }) => setCorsText(detail.value)} placeholder={CORS_EXAMPLE} />
                      </SpaceBetween>
                    </Container>
                  </SpaceBetween>
                ),
              },
            ]}
          />
        </Container>
      )}

      <Modal
        visible={createBucketOpen}
        onDismiss={() => setCreateBucketOpen(false)}
        header="Create bucket"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateBucketOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={creatingBucket} disabled={!newBucketName || Boolean(bucketNameError)} onClick={handleCreateBucket}>Create bucket</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="AWS Region">
            <Input value={identity?.region || 'us-east-1'} disabled />
          </FormField>
          <FormField label="Bucket type"><Input value="General purpose" disabled /></FormField>
          <FormField
            label="Bucket name"
            errorText={bucketNameError}
            constraintText="Bucket names must be 3 to 63 characters and unique within the global namespace. Bucket names must also begin and end with a letter or number. Valid characters are a-z, 0-9, periods (.), and hyphens (-)."
          >
            <Input value={newBucketName} onChange={({ detail }) => setNewBucketName(detail.value)} placeholder="myawsbucket" />
          </FormField>
          <Box variant="small">New buckets get the AWS defaults: ACLs disabled, Block all public access on, and SSE-S3 default encryption.</Box>
        </SpaceBetween>
      </Modal>

      <ConfirmActionModal
        visible={deleteBucketOpen}
        header="Delete bucket"
        confirmLabel="Delete bucket"
        confirmationText={activeBucket?.Name}
        confirmationPrompt="To confirm deletion, enter the name of the bucket in the text input field."
        loading={bucketBusy}
        onConfirm={handleDeleteBucket}
        onDismiss={() => setDeleteBucketOpen(false)}
      >
        Deleting a bucket can't be undone. A bucket must be empty before it can be deleted; use Empty first if it still contains objects.
      </ConfirmActionModal>

      <ConfirmActionModal
        visible={emptyBucketOpen}
        header="Empty bucket"
        confirmLabel="Empty"
        confirmationText="permanently delete"
        loading={bucketBusy}
        onConfirm={handleEmptyBucket}
        onDismiss={() => setEmptyBucketOpen(false)}
      >
        Emptying the bucket deletes all current objects in {activeBucket?.Name}, and this action can't be undone. In a versioned bucket, S3 adds delete markers and keeps previous versions.
      </ConfirmActionModal>

      <ConfirmActionModal
        visible={deleteObjectOpen}
        header="Delete objects"
        confirmLabel="Delete objects"
        confirmationText={versioningStatus === 'Enabled' ? 'delete' : 'permanently delete'}
        loading={deletingObject}
        onConfirm={handleDeleteObject}
        onDismiss={() => setDeleteObjectOpen(false)}
      >
        {versioningStatus === 'Enabled'
          ? <>Versioning is enabled, so deleting <strong>{selectedObject?.key}</strong> adds a delete marker. Previous versions remain in the bucket.</>
          : <>Deleting <strong>{selectedObject?.key}</strong> permanently removes it. This action can't be undone.</>}
      </ConfirmActionModal>

      <ConfirmActionModal
        visible={deletePolicyOpen}
        header="Delete bucket policy?"
        confirmLabel="Delete"
        confirmationText="delete"
        loading={savingPolicy}
        onConfirm={handleDeletePolicy}
        onDismiss={() => setDeletePolicyOpen(false)}
      >
        Deleting the bucket policy removes every permission it grants or denies.
      </ConfirmActionModal>

      <ConfirmActionModal
        visible={confirmPabOpen}
        header="Edit Block public access (bucket settings)"
        confirmLabel="Confirm"
        confirmationText="confirm"
        loading={savingPab}
        onConfirm={handleSavePab}
        onDismiss={() => setConfirmPabOpen(false)}
      >
        Updating these settings can make this bucket and its objects public. Turning off Block Public Access is not recommended unless your use case requires public access, such as static website hosting.
      </ConfirmActionModal>

      <ConfirmActionModal
        visible={Boolean(notificationToDelete)}
        header="Delete event notification"
        confirmLabel="Delete"
        loading={savingNotifications}
        onConfirm={handleDeleteNotification}
        onDismiss={() => setNotificationToDelete(null)}
      >
        Delete event notification <strong>{notificationToDelete?.id}</strong>? S3 stops sending these events to {notificationToDelete?.destination}.
      </ConfirmActionModal>

      <Modal
        visible={inspectModalOpen}
        onDismiss={() => setInspectModalOpen(false)}
        header={inspectObject?.key || 'Object'}
        size="large"
        footer={<Box float="right"><Button variant="primary" onClick={() => setInspectModalOpen(false)}>Close</Button></Box>}
      >
        <SpaceBetween size="m">
          <Container header={<Header variant="h3">Object overview</Header>}>
            <KeyValuePairs
              columns={2}
              items={[
                { label: 'Key', value: inspectObject?.key },
                { label: 'S3 URI', value: activeBucket && inspectObject ? `s3://${activeBucket.Name}/${inspectObject.key}` : '-' },
                { label: 'Amazon Resource Name (ARN)', value: activeBucket && inspectObject ? `arn:aws:s3:::${activeBucket.Name}/${inspectObject.key}` : '-' },
                { label: 'Entity tag (Etag)', value: objectHeadData?.etag?.replace(/"/g, '') || inspectObject?.etag?.replace(/"/g, '') || '-' },
                { label: 'Size', value: formatBytes(objectHeadData?.content_length ?? inspectObject?.size) },
                { label: 'Type', value: objectHeadData?.content_type || '-' },
                { label: 'Last modified', value: formatAwsDate(objectHeadData?.last_modified || inspectObject?.last_modified) },
                { label: 'Storage class', value: objectHeadData?.storage_class || inspectObject?.storage_class || 'Standard' },
                { label: 'Version ID', value: objectHeadData?.versioning || 'null' },
              ]}
            />
          </Container>

          {objectHeadData?.metadata && Object.keys(objectHeadData.metadata).length > 0 && (
            <Container header={<Header variant="h3" description="User-defined metadata (x-amz-meta-*).">Metadata</Header>}>
              <KeyValuePairs columns={2} items={Object.entries(objectHeadData.metadata).map(([key, value]) => ({ label: `x-amz-meta-${key}`, value: String(value) }))} />
            </Container>
          )}

          <Table
            header={<Header variant="h3" counter={`(${objectTags.length})`} description="Track storage cost or other criteria by tagging your objects. Up to 10 tags per object.">Tags</Header>}
            columnDefinitions={[
              { id: 'key', header: 'Key', cell: t => t.Key },
              { id: 'val', header: 'Value', cell: t => t.Value || '-' },
              { id: 'act', header: 'Actions', cell: t => <Button variant="inline-link" disabled={savingTags} onClick={() => saveObjectTags(objectTags.filter(tag => tag.Key !== t.Key), 'Successfully removed tag.')}>Remove</Button> },
            ]}
            items={objectTags}
            trackBy="Key"
            empty={<Box textAlign="center">No tags associated with this resource.</Box>}
          />

          <Grid gridDefinition={[{ colspan: { default: 12, s: 5 } }, { colspan: { default: 12, s: 5 } }, { colspan: { default: 12, s: 2 } }]}>
            <FormField label="Key">
              <Input value={newTagKey} onChange={({ detail }) => setNewTagKey(detail.value)} placeholder="Enter key" />
            </FormField>
            <FormField label={<>Value <i>- optional</i></>}>
              <Input value={newTagValue} onChange={({ detail }) => setNewTagValue(detail.value)} placeholder="Enter value" />
            </FormField>
            <Box margin={{ top: 'l' }}>
              <Button loading={savingTags} disabled={!newTagKey.trim()} onClick={handleAddObjectTag}>Add tag</Button>
            </Box>
          </Grid>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={uploadModalOpen}
        onDismiss={() => setUploadModalOpen(false)}
        header="Upload"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setUploadModalOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={uploading} disabled={!uploadFiles.length && !uploadKey.trim()} onClick={handleUploadObject}>Upload</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box>Destination: <Box variant="code">s3://{activeBucket?.Name}/{prefix}</Box></Box>
          <FormField label="File" description="Choose a file to upload. Files larger than 5 MB use multipart upload.">
            <FileUpload
              value={uploadFiles}
              onChange={({ detail }) => setUploadFiles(detail.value)}
              i18nStrings={{
                uploadButtonText: () => 'Choose file',
                dropzoneText: () => 'Drop file to upload',
                removeFileAriaLabel: index => `Remove file ${index + 1}`,
                limitShowFewer: 'Show fewer files',
                limitShowMore: 'Show more files',
                errorIconAriaLabel: 'Error',
                warningIconAriaLabel: 'Warning',
              }}
              showFileSize
              showFileLastModified
            />
          </FormField>
          <FormField label={<>Object key <i>- optional with a file</i></>} description="Name of the object within the current folder. Defaults to the file name.">
            <Input value={uploadKey} onChange={({ detail }) => setUploadKey(detail.value)} placeholder={uploadFiles[0]?.name || 'index.html'} />
          </FormField>
          {!uploadFiles.length && (
            <>
              <FormField label="Content-Type" description="Stored as the object's Content-Type system metadata.">
                <Input value={uploadContentType} onChange={({ detail }) => setUploadContentType(detail.value)} placeholder="text/html" />
              </FormField>
              <FormField label="Text content" description="Without a file, the object body is this text.">
                <Textarea rows={6} value={uploadContent} onChange={({ detail }) => setUploadContent(detail.value)} placeholder="<h1>Hello from S3</h1>" />
              </FormField>
            </>
          )}
        </SpaceBetween>
      </Modal>

      <Modal
        visible={folderModalOpen}
        onDismiss={() => setFolderModalOpen(false)}
        header="Create folder"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setFolderModalOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={creatingFolder} disabled={!folderName.trim()} onClick={handleCreateFolder}>Create folder</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Folder name" constraintText={'Folder names can\'t contain "/".'} description="S3 has a flat structure; a folder is a zero-byte object whose key ends in /, used to group objects by prefix.">
          <Input value={folderName} onChange={({ detail }) => setFolderName(detail.value)} placeholder="images" />
        </FormField>
      </Modal>

      <Modal
        visible={presignModalOpen}
        onDismiss={() => setPresignModalOpen(false)}
        header="Share with a presigned URL"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setPresignModalOpen(false)}>Close</Button>
              <Button variant="primary" loading={generatingPresign} onClick={handleGeneratePresignedUrl}>Create presigned URL</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box>Object: <Box variant="code">{selectedObject?.key}</Box></Box>
          <FormField label="Time interval until the presigned URL expires" description="Anyone with the URL can download the object until it expires. The console allows up to 12 hours.">
            <Select
              selectedOption={presignExpires}
              onChange={({ detail }) => setPresignExpires(detail.selectedOption as any)}
              options={[
                { label: '15 minutes', value: '900' },
                { label: '1 hour', value: '3600' },
                { label: '12 hours', value: '43200' },
              ]}
            />
          </FormField>
          {presignedUrl && (
            <Container header={<Header variant="h3">Presigned URL</Header>}>
              <SpaceBetween size="s">
                <Box variant="code"><span style={{ wordBreak: 'break-all' }}>{presignedUrl}</span></Box>
                <Button
                  iconName="copy"
                  onClick={() => {
                    navigator.clipboard.writeText(presignedUrl);
                    setCopiedPresign(true);
                    setTimeout(() => setCopiedPresign(false), 2000);
                  }}
                >
                  {copiedPresign ? 'Copied' : 'Copy presigned URL'}
                </Button>
              </SpaceBetween>
            </Container>
          )}
        </SpaceBetween>
      </Modal>

      <Modal
        visible={addNotificationOpen}
        onDismiss={() => setAddNotificationOpen(false)}
        header="Create event notification"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setAddNotificationOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={savingNotifications} onClick={handleAddNotification}>Save changes</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Event name">
            <Input value={notifName} onChange={({ detail }) => setNotifName(detail.value)} placeholder="Event name" />
          </FormField>
          <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
            <FormField label={<>Prefix <i>- optional</i></>}><Input value={notifPrefix} onChange={({ detail }) => setNotifPrefix(detail.value)} placeholder="images/" /></FormField>
            <FormField label={<>Suffix <i>- optional</i></>}><Input value={notifSuffix} onChange={({ detail }) => setNotifSuffix(detail.value)} placeholder=".jpg" /></FormField>
          </Grid>
          <FormField label="Event types">
            <Multiselect selectedOptions={notifEvents} onChange={({ detail }) => setNotifEvents(detail.selectedOptions)} options={EVENT_TYPE_OPTIONS} placeholder="Choose event types" />
          </FormField>
          <FormField label="Destination">
            <Select selectedOption={notifTargetType} onChange={({ detail }) => setNotifTargetType(detail.selectedOption as any)} options={DESTINATION_OPTIONS} />
          </FormField>
          <FormField label={`${notifTargetType.label} ARN`} description="The destination's resource policy must allow s3.amazonaws.com to send events to it.">
            <Input value={notifTargetArn} onChange={({ detail }) => setNotifTargetArn(detail.value)} placeholder={`arn:aws:${notifTargetType.value}:${identity?.region || 'us-east-1'}:000000000000:${notifTargetType.value === 'lambda' ? 'function:my-function' : 'my-destination'}`} />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
