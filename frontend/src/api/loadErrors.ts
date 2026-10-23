// Inventory endpoints (GET /api/<service>/) fill optional lists with an empty list when the
// call fails, and name each failed call in `load_errors`. This hook watches every inventory
// response, so all consoles can say which lists Floci did not return instead of showing
// them as empty.

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
