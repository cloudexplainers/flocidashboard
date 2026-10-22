import { useEffect, useRef, useState } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Button from '@cloudscape-design/components/button';
import Select from '@cloudscape-design/components/select';
import Textarea from '@cloudscape-design/components/textarea';
import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import { deployLambdaSource, fetchLambdaSource } from '../api/client';
import { formatBytes } from '../format';

interface SourceFile { name: string; size: number; editable: boolean; content?: string }

/** The console's "Code source" editor: edit the package's text files and Deploy. */
export function LambdaCodeEditor({ functionName, onDeployed }: { functionName: string; onDeployed?: () => void }) {
  const [files, setFiles] = useState<SourceFile[]>([]);
  const [selected, setSelected] = useState('');
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [deploying, setDeploying] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const request = useRef(0);

  const load = async () => {
    const id = ++request.current;
    setLoading(true);
    setError('');
    try {
      const source = await fetchLambdaSource(functionName);
      if (id !== request.current) return;
      setFiles(source.files || []);
      setDrafts({});
      setSelected(source.handler_file || source.files?.find((file: SourceFile) => file.editable)?.name || '');
    } catch (err) {
      if (id === request.current) { setFiles([]); setError(err instanceof Error ? err.message : 'Unable to load the function code.'); }
    } finally {
      if (id === request.current) setLoading(false);
    }
  };
  useEffect(() => { setNotice(''); void load(); return () => { request.current++; }; }, [functionName]);

  const file = files.find(item => item.name === selected);
  const changed = Object.entries(drafts).filter(([name, content]) => files.find(item => item.name === name)?.content !== content);
  const deploy = async () => {
    setDeploying(true);
    setError('');
    try {
      await deployLambdaSource(functionName, Object.fromEntries(changed));
      setNotice(`Successfully updated the function ${functionName}.`);
      await load();
      onDeployed?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Deploy failed.');
    } finally {
      setDeploying(false);
    }
  };

  return (
    <Container header={<Header variant="h3" description="Edit the deployment package's text files, then deploy them. Other files in the package are kept."
      actions={<SpaceBetween direction="horizontal" size="xs">
        <Button iconName="refresh" ariaLabel="Reload code" disabled={deploying} onClick={load} />
        <Button variant="primary" loading={deploying} disabled={!changed.length} onClick={deploy}>Deploy</Button>
      </SpaceBetween>}>Code source</Header>}>
      <SpaceBetween size="s">
        {error && <Alert type="error">{error}</Alert>}
        {notice && <Alert type="success" dismissible onDismiss={() => setNotice('')}>{notice}</Alert>}
        {changed.length > 0 && <StatusIndicator type="warning">Changes not deployed</StatusIndicator>}
        {loading ? <StatusIndicator type="loading">Loading code</StatusIndicator> : files.length > 0 && (
          <>
            <Select ariaLabel="File" selectedOption={file ? { value: file.name, label: file.name } : null}
              options={files.map(item => ({ value: item.name, label: item.name, description: item.editable ? formatBytes(item.size) : `${formatBytes(item.size)} - binary or too large to edit`, disabled: !item.editable }))}
              onChange={({ detail }) => setSelected(detail.selectedOption.value || '')} />
            {file?.editable ? (
              <div style={{ fontFamily: 'monospace' }}>
                <Textarea ariaLabel={`Code for ${file.name}`} rows={16} spellcheck={false}
                  value={drafts[file.name] ?? file.content ?? ''}
                  onChange={({ detail }) => setDrafts(previous => ({ ...previous, [file.name]: detail.value }))} />
              </div>
            ) : <Box color="text-body-secondary">This file can't be edited in the console.</Box>}
          </>
        )}
      </SpaceBetween>
    </Container>
  );
}
