import React, { createContext, useContext, useEffect, useState } from 'react';
import { fetchIdentity } from '../api/client';
import { IdentityInfo } from '../types';

type SessionState = { identity: IdentityInfo | null; auth: Record<string, boolean> | null };
const SessionContext = createContext<SessionState>({ identity: null, auth: null });
export const useSession = () => useContext(SessionContext);
export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<SessionState>({ identity: null, auth: null });
  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      const [identity, settings] = await Promise.all([
        fetchIdentity(),
        fetch('/api/settings/').then(r => r.ok ? r.json() : null).catch(() => null),
      ]);
      if (alive) setState({ identity, auth: settings?.service_auth ?? null });
    };
    refresh();
    window.addEventListener('floci_session_changed', refresh);
    return () => { alive = false; window.removeEventListener('floci_session_changed', refresh); };
  }, []);
  return <SessionContext.Provider value={state}>{children}</SessionContext.Provider>;
}
