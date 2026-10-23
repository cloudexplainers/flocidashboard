// Inventory endpoints (GET /api/<service>/) fill optional lists with an empty list when the
// call fails, and name each failed call in `load_errors`. This hook watches every inventory
// response, so all consoles can say which lists Floci did not return instead of showing
// them as empty.
//
// The same responses carry `notes`, `not_supported` and `out_of_scope`: what Floci accepts
// without simulating it. The hook keeps the latest of those per service, so the layout can
// show them once on every console, custom or generic.

export interface LoadError {
  operation: string;
  code?: string | null;
  error: string;
  unsupported?: boolean;
}

export interface PartialLoadDetail {
  service: string;
  errors: LoadError[];
}

export const PARTIAL_LOAD_EVENT = 'floci_partial_load';
export const INVENTORY_NOTES_EVENT = 'floci_inventory_notes';

export interface InventoryNotes {
  notes: string[];
  notEmulated: string[];
}

const notesByService = new Map<string, InventoryNotes>();
const seenServices = new Set<string>();
const requestedServices = new Set<string>();
// Marks the notes section's own inventory request, whose load errors describe lists the
// console doesn't show, so they don't raise the "Some lists are incomplete" warning.
const NOTES_ONLY_HEADER = 'X-Floci-Notes-Only';

export function inventoryNotes(service: string): InventoryNotes | undefined {
  return notesByService.get(service);
}

const strings = (value: unknown) => Array.isArray(value)
  ? value.filter((item): item is string => typeof item === 'string' && item.trim() !== '')
  : [];

/** Reads the emulation notes from an inventory response; undefined if it has none. */
export function readInventoryNotes(data: any): InventoryNotes | undefined {
  if (!data || typeof data !== 'object') return undefined;
  const notes = strings(data.notes)
    // Release-changelog lines ("Floci 1.5.20 adds ...") describe old versions, not this emulator.
    .filter(note => !/^Floci \d+\.\d+\.\d+ /.test(note));
  const notEmulated = [...new Set([...strings(data.not_supported), ...strings(data.out_of_scope)])];
  return notes.length || notEmulated.length ? { notes, notEmulated } : undefined;
}

/**
 * Some custom consoles never request GET /api/<service>/. Once a console has had time to load,
 * this fetches the inventory once per page load so its notes can be shown.
 */
export function requestInventoryNotes(service: string) {
  if (seenServices.has(service) || requestedServices.has(service)) return;
  requestedServices.add(service);
  fetch(`/api/${service}/`, { headers: { [NOTES_ONLY_HEADER]: '1' } }).catch(() => undefined);
}

function recordInventoryNotes(service: string, data: any) {
  seenServices.add(service);
  // Notes are fixed per service, so a failed or note-less response keeps the last ones.
  const found = readInventoryNotes(data);
  if (!found || JSON.stringify(found) === JSON.stringify(notesByService.get(service))) return;
  notesByService.set(service, found);
  window.dispatchEvent(new CustomEvent<string>(INVENTORY_NOTES_EVENT, { detail: service }));
}

const INVENTORY_PATH = /^\/api\/([a-z0-9-]+)\/$/;

export function installLoadErrorHook() {
  const original = window.fetch.bind(window);
  if ((original as any).__flociLoadErrors) return;
  const wrapped = async (input: RequestInfo | URL, init?: RequestInit) => {
    const response = await original(input, init);
    try {
      const method = (init?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
      const url = new URL(input instanceof Request ? input.url : String(input), window.location.origin);
      const match = method === 'GET' && url.origin === window.location.origin ? INVENTORY_PATH.exec(url.pathname) : null;
      if (match && response.ok) {
        response.clone().json().then(data => {
          recordInventoryNotes(match[1], data);
          if (new Headers(init?.headers).has(NOTES_ONLY_HEADER)) return;
          const errors = Array.isArray(data?.load_errors) ? data.load_errors as LoadError[] : [];
          if (errors.length) window.dispatchEvent(new CustomEvent<PartialLoadDetail>(PARTIAL_LOAD_EVENT, { detail: { service: match[1], errors } }));
        }).catch(() => undefined);
      }
    } catch {
      // Never let the hook break the request itself.
    }
    return response;
  };
  (wrapped as any).__flociLoadErrors = true;
  window.fetch = wrapped as typeof window.fetch;
}

export function describeLoadErrors(errors: LoadError[]): { unsupported: string[]; failed: string[] } {
  const unsupported = [...new Set(errors.filter(error => error.unsupported).map(error => error.operation))];
  const text = (error: LoadError) => String(error.error || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 160);
  const failed = errors
    .filter(error => !error.unsupported)
    // Truncated: the dashboard showed only part of a list Floci returned.
    .map(error => error.code === 'Truncated'
      ? `${error.operation}: ${text(error)}`
      : `${error.operation}${error.code ? ` (${error.code})` : ''}: ${text(error)}`);
  return { unsupported, failed };
}
