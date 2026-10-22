import { ReactNode, useEffect, useState } from 'react';
import Modal from '@cloudscape-design/components/modal';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Alert from '@cloudscape-design/components/alert';

interface ConfirmActionModalProps {
  visible: boolean;
  header: string;
  /** Explains what the action does and what cannot be undone. */
  children?: ReactNode;
  /** Label of the confirm button, e.g. "Delete bucket". */
  confirmLabel: string;
  /**
   * Text the user must type before the action is enabled, as in the AWS console
   * ("To confirm deletion, enter the bucket name"). Omit for a plain confirmation.
   */
  confirmationText?: string;
  confirmationPrompt?: string;
  loading?: boolean;
  onConfirm: () => void;
  onDismiss: () => void;
}

/** Confirmation dialog for destructive actions, following the AWS console pattern. */
export default function ConfirmActionModal({
  visible,
  header,
  children,
  confirmLabel,
  confirmationText,
  confirmationPrompt,
  loading,
  onConfirm,
  onDismiss,
}: ConfirmActionModalProps) {
  const [typed, setTyped] = useState('');
  useEffect(() => { if (visible) setTyped(''); }, [visible]);
  const ready = !confirmationText || typed === confirmationText;
  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      header={header}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" disabled={!ready} loading={loading} onClick={onConfirm}>{confirmLabel}</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        {children && <Alert type="warning">{children}</Alert>}
        {confirmationText && (
          <FormField label={confirmationPrompt || <>To confirm, enter <strong>{confirmationText}</strong> in the text input field.</>}>
            <Input value={typed} placeholder={confirmationText} ariaLabel="Confirmation text" onChange={({ detail }) => setTyped(detail.value)} />
          </FormField>
        )}
      </SpaceBetween>
    </Modal>
  );
}
