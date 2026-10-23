import type { ReactNode } from 'react';
import Alert from '@cloudscape-design/components/alert';

/**
 * Says where Floci stores a setting but doesn't apply it, so the console doesn't read as if
 * the behavior happens. Keep the text to what Floci does, without version numbers.
 */
export function FlociNote({ children, header = 'How Floci emulates this' }: { children: ReactNode; header?: string }) {
  return <Alert type="info" header={header} data-testid="floci-note">{children}</Alert>;
}

/** Floci evaluates resource policies only for S3 buckets, SNS topics and Lambda functions. */
export const RESOURCE_POLICY_NOT_ENFORCED = 'Floci stores this policy and returns it, but doesn’t evaluate it: a Deny here doesn’t block any request. Floci enforces resource policies only for S3 buckets, SNS topics and Lambda functions.';
