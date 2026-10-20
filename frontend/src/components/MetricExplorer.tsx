import { useEffect, useState } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Select from '@cloudscape-design/components/select';
import FormField from '@cloudscape-design/components/form-field';
import SpaceBetween from '@cloudscape-design/components/space-between';
import LineChart from '@cloudscape-design/components/line-chart';
import Alert from '@cloudscape-design/components/alert';
import Button from '@cloudscape-design/components/button';

export type Metric = { Namespace: string; MetricName: string; Dimensions?: { Name: string; Value: string }[] };
export function MetricExplorer({ metrics, preferredMetric }: { metrics: Metric[]; preferredMetric?: Metric | null }) {
  const [selected, setSelected] = useState('0');
  const [hours, setHours] = useState('1');
  const [revision, setRevision] = useState(0);
  const [points, setPoints] = useState<{ x: Date; y: number }[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [unit, setUnit] = useState('');
  const metric = metrics[Number(selected)] ?? metrics[0];
  const metricKey = JSON.stringify(metric);
  const preferredKey = JSON.stringify(preferredMetric);
  useEffect(() => {
    if (!preferredMetric) return;
    const index = metrics.findIndex(item => item.Namespace === preferredMetric.Namespace && item.MetricName === preferredMetric.MetricName
      && JSON.stringify(item.Dimensions || []) === JSON.stringify(preferredMetric.Dimensions || []));
    if (index >= 0) setSelected(String(index));
  }, [preferredKey]);
  useEffect(() => {
    let alive = true;
    setPoints([]);
    setError('');
    if (!metric) { setLoading(false); return; }
    setLoading(true);
    const params = new URLSearchParams({ namespace: metric.Namespace, metric_name: metric.MetricName, dimensions: JSON.stringify(metric.Dimensions || []), hours });
    fetch(`/api/cloudwatch/metric-statistics/?${params}`).then(async response => {
      const result = await response.json();
      if (!response.ok || result.error) throw new Error(result.error || 'Metric data unavailable');
      return result;
    }).then(result => {
      if (!alive) return;
      setUnit(result.datapoints?.[0]?.Unit || '');
      setPoints((result.datapoints || []).filter((point: any) => Number.isFinite(point.Average) && Number.isFinite(Date.parse(point.Timestamp))).map((point: any) => ({ x: new Date(point.Timestamp), y: point.Average })));
    }).catch(err => { if (alive) setError(err.message); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [metricKey, hours, revision]);
  return <Container header={<Header variant="h2" description="Average values from the active local CloudWatch endpoint." actions={<Button iconName="refresh" onClick={() => setRevision(revision + 1)} loading={loading}>Refresh chart</Button>}>Metric monitoring</Header>}>
    <SpaceBetween size="m">
      <SpaceBetween direction="horizontal" size="m">
        <FormField label="Metric"><Select ariaLabel="Metric" selectedOption={metric ? { value: selected, label: `${metric.Namespace} / ${metric.MetricName}` } : null}
          options={metrics.map((item, index) => ({ value: String(index), label: `${item.Namespace} / ${item.MetricName}`, description: item.Dimensions?.map(d => `${d.Name}=${d.Value}`).join(', ') }))}
          onChange={({ detail }) => setSelected(detail.selectedOption.value!)} placeholder="Select a metric" /></FormField>
        <FormField label="Time range"><Select ariaLabel="Time range" selectedOption={{ value: hours, label: `Last ${hours} hour${hours === '1' ? '' : 's'}` }} options={['1', '3', '12', '24'].map(value => ({ value, label: `Last ${value} hour${value === '1' ? '' : 's'}` }))} onChange={({ detail }) => setHours(detail.selectedOption.value!)} /></FormField>
      </SpaceBetween>
      {error ? <Alert type="error">{error}</Alert> : <LineChart
        ariaLabel="CloudWatch metric chart" hideFilter height={250} xScaleType="time" yTitle={unit || 'Average'}
        statusType={loading ? 'loading' : 'finished'}
        series={metric && points.length ? [{ title: metric.MetricName, type: 'line', data: points }] : []}
        xTickFormatter={date => date.toLocaleTimeString()}
        empty={<div>No metric samples in this time range. Publish metric data or select another time range.</div>}
        noMatch={<div>No matching data.</div>}
        loadingText="Loading metric samples" errorText="Metric samples unavailable" recoveryText="Retry"
        onRecoveryClick={() => setRevision(revision + 1)}
      />}
    </SpaceBetween>
  </Container>;
}
