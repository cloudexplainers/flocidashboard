import { useEffect, useState } from 'react';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Box from '@cloudscape-design/components/box';
import { INVENTORY_NOTES_EVENT, InventoryNotes, inventoryNotes, requestInventoryNotes } from '../api/loadErrors';

/**
 * The service inventory's emulation notes (what Floci simulates and what it accepts without
 * simulating), shown once per console and collapsed so pages stay quiet. The fetch hook in
 * api/loadErrors.ts collects them from GET /api/<service>/, whichever page made the request.
 */
export function InventoryNotesSection({ service }: { service?: string }) {
  const [notes, setNotes] = useState<InventoryNotes | undefined>(() => service ? inventoryNotes(service) : undefined);
  useEffect(() => {
    setNotes(service ? inventoryNotes(service) : undefined);
    if (!service) return;
    const update = (event: Event) => {
      if ((event as CustomEvent<string>).detail === service) setNotes(inventoryNotes(service));
    };
    window.addEventListener(INVENTORY_NOTES_EVENT, update);
    // Consoles that don't load their inventory get it fetched once, after their own requests.
    const timer = window.setTimeout(() => requestInventoryNotes(service), 1500);
    return () => { window.removeEventListener(INVENTORY_NOTES_EVENT, update); window.clearTimeout(timer); };
  }, [service]);
  if (!notes) return null;
  const count = notes.notes.length + notes.notEmulated.length;
  return <div data-testid="inventory-notes" style={{ marginBottom: 16 }}>
    <ExpandableSection
      variant="container"
      headerText="How Floci emulates this service"
      headerDescription={`${count} ${count === 1 ? 'note' : 'notes'} on what Floci simulates, and what it accepts without simulating.`}
    >
      <SpaceBetween size="s">
        {notes.notes.length > 0 && <ul style={{ margin: 0, paddingLeft: 20 }}>{notes.notes.map(note => <li key={note}>{note}</li>)}</ul>}
        {notes.notEmulated.length > 0 && <div>
          <Box variant="h4" padding={{ top: 'n', bottom: 'xxs' }}>Not emulated</Box>
          <ul style={{ margin: 0, paddingLeft: 20 }}>{notes.notEmulated.map(item => <li key={item}>{item}</li>)}</ul>
        </div>}
      </SpaceBetween>
    </ExpandableSection>
  </div>;
}
