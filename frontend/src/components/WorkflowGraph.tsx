import { useId, useState } from 'react';
import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import { CodeSnippet } from './CodeSnippet';

type StateDefinition = { Type?: string; Next?: string; Default?: string; End?: boolean; Choices?: { Next?: string; [key: string]: unknown }[]; Catch?: { Next: string }[]; Branches?: Definition[]; Iterator?: Definition; ItemProcessor?: Definition; [key: string]: unknown };
type Definition = { StartAt?: string; States?: Record<string, StateDefinition> };
export function WorkflowGraph({ definition, events = [] }: { definition: string | Definition; events?: any[] }) {
  const [selected, setSelected] = useState('');
  const marker = useId().replace(/:/g, '');
  let parsed: Definition;
  try { parsed = typeof definition === 'string' ? JSON.parse(definition) : definition; }
  catch { return <Alert type="error">The state machine definition is invalid JSON.</Alert>; }
  if (!parsed?.States) return <Box>No state machine definition available.</Box>;
  const statuses: Record<string, string> = {};
  let currentState = '';
  for (const event of events) {
    if (event.stateEnteredEventDetails?.name) { currentState = event.stateEnteredEventDetails.name; statuses[currentState] = 'Entered'; }
    if (event.stateExitedEventDetails?.name) statuses[event.stateExitedEventDetails.name] = 'Completed';
    if (currentState && /(Failed|TimedOut|Aborted)$/.test(event.type || '')) statuses[currentState] = 'Failed';
  }
  const states = Object.entries(parsed.States);
  const selectedState = parsed.States[selected];
  const incoming = events.filter(event => event.stateEnteredEventDetails?.name === selected || event.stateExitedEventDetails?.name === selected);
  const width = 760, rowHeight = 110, nodeWidth = 310, height = states.length * rowHeight + 40;
  return <SpaceBetween size="m">
    <Box variant="small">Select a state to inspect its definition. Arrows follow Next, Choice, Default, and Catch transitions.</Box>
    <div className="workflow-graph" style={{ overflow: 'auto', maxHeight: 600 }}>
      <svg role="img" aria-label="State machine transitions" viewBox={`0 0 ${width} ${height}`} style={{ minWidth: 600, width: '100%', height: Math.max(180, height) }}>
        <defs><marker id={marker} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" fill="currentColor" /></marker></defs>
        {states.flatMap(([name, state], index) => {
          const transitions = [
            ...(state.Next ? [{ target: state.Next, label: 'Next' }] : []),
            ...(state.Choices || []).map((choice, i) => ({ target: choice.Next, label: `Choice ${i + 1}` })),
            ...(state.Default ? [{ target: state.Default, label: 'Default' }] : []),
            ...(state.Catch || []).map(catchState => ({ target: catchState.Next, label: 'Catch' })),
          ];
          return transitions.map((edge, edgeIndex) => {
            const targetIndex = states.findIndex(([target]) => target === edge.target);
            if (targetIndex < 0) return null;
            const y1 = 50 + index * rowHeight, y2 = 50 + targetIndex * rowHeight;
            const offset = 400 + (edgeIndex % 4) * 55;
            return <g key={`${name}-${edgeIndex}`}><path d={targetIndex === index ? `M${nodeWidth + 20},${y1 - 15} C${offset + 80},${y1 - 70} ${offset + 80},${y1 + 70} ${nodeWidth + 20},${y1 + 15}` : `M${nodeWidth + 20},${y1} C${offset},${y1} ${offset},${y2} ${nodeWidth + 20},${y2}`} fill="none" stroke="currentColor" strokeWidth="2" markerEnd={`url(#${marker})`} /><text x={offset - 15} y={(y1 + y2) / 2 - 5} fill="currentColor" fontSize="12">{edge.label}</text><title>{name} → {edge.target} ({edge.label})</title></g>;
          });
        })}
        {states.map(([name, state], index) => {
          const status = statuses[name] || '';
          return <foreignObject key={name} x="20" y={20 + index * rowHeight} width={nodeWidth} height="80">
            <button className={`workflow-state ${selected === name ? 'selected' : ''}`} style={status ? { borderColor: status === 'Failed' ? '#d91515' : status === 'Completed' ? '#037f0c' : '#0972d3' } : undefined} onClick={() => setSelected(name)} aria-pressed={selected === name}>
              <span>{name === parsed.StartAt ? 'Start · ' : ''}{state.Type}{state.End ? ' · End' : ''}{status ? ` · ${status}` : ''}</span><strong>{name}</strong>
            </button>
          </foreignObject>;
        })}
      </svg>
    </div>
    {selectedState && <SpaceBetween size="m">
      <Header variant="h3">State: {selected}</Header>
      <CodeSnippet language="json" code={JSON.stringify(selectedState, null, 2)} />
      {incoming.length > 0 && <><Header variant="h3">State input / output</Header><CodeSnippet language="json" code={JSON.stringify(incoming.map(event => event.stateEnteredEventDetails || event.stateExitedEventDetails), null, 2)} /></>}
      {selectedState.Branches?.map((branch, index) => <div key={index}><Header variant="h3">Parallel branch {index + 1}</Header><WorkflowGraph definition={branch} events={events} /></div>)}
      {(selectedState.Iterator || selectedState.ItemProcessor) && <WorkflowGraph definition={(selectedState.Iterator || selectedState.ItemProcessor)!} events={events} />}
    </SpaceBetween>}
  </SpaceBetween>;
}
