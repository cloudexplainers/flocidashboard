import { useEffect, useState } from 'react';

// Lab completion keyed by "service:labKey". The labs page owns updates; other
// parts of the UI (like the sidebar) subscribe so they stay in sync.
export type LabCompletionMap = Record<string, boolean>;

const STORAGE_KEY = 'floci_completed_labs';
const CHANGE_EVENT = 'floci:lab-completion-changed';

export function readLabCompletion(): LabCompletionMap {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved ? JSON.parse(saved) : {};
  } catch {
    return {};
  }
}

export function writeLabCompletion(map: LabCompletionMap): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    // Storage can be unavailable (private mode); the in-memory event still fires.
  }
  window.dispatchEvent(new CustomEvent<LabCompletionMap>(CHANGE_EVENT, { detail: map }));
}

export function useLabCompletion(): LabCompletionMap {
  const [map, setMap] = useState<LabCompletionMap>(readLabCompletion);
  useEffect(() => {
    const onChange = (event: Event) => setMap((event as CustomEvent<LabCompletionMap>).detail);
    window.addEventListener(CHANGE_EVENT, onChange);
    return () => window.removeEventListener(CHANGE_EVENT, onChange);
  }, []);
  return map;
}
