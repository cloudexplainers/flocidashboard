import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { fetchIdentityDetail } from "../api/client";
import { IdentityInfo } from "../types";
import { normalizeSessionIdentity } from "../sessionIdentity";

type SessionState = {
  identity: IdentityInfo | null;
  details: any | null;
  auth: Record<string, boolean> | null;
  loading: boolean;
  error: string | null;
};
type SessionContextState = SessionState & {
  refresh: (payload?: any) => Promise<void>;
};
const SessionContext = createContext<SessionContextState>({
  identity: null,
  details: null,
  auth: null,
  loading: true,
  error: null,
  refresh: async () => {},
});
export const useSession = () => useContext(SessionContext);
export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<SessionState>({
    identity: null,
    details: null,
    auth: null,
    loading: true,
    error: null,
  });
  const revision = useRef(0);
  const alive = useRef(true);
  const refresh = useCallback(async (payload?: any) => {
    const version = ++revision.current;
    setState((previous) => ({ ...previous, loading: true, error: null }));
    const completePayload =
      payload?.endpoint_url &&
      (payload.caller_identity ||
        typeof payload.identity_resolved === "boolean");
    const [identityResult, settingsResult] = await Promise.allSettled([
      completePayload ? Promise.resolve(payload) : fetchIdentityDetail(),
      fetch("/api/settings/").then((response) =>
        response.ok ? response.json() : null,
      ),
    ]);
    if (!alive.current || version !== revision.current) return;
    const details =
      identityResult.status === "fulfilled" ? identityResult.value : null;
    const error =
      identityResult.status === "rejected"
        ? String(
            identityResult.reason?.message ||
              "Unable to load session identity.",
          )
        : null;
    setState({
      identity: normalizeSessionIdentity(
        details || { identity_resolved: false, identity_error: error },
      ),
      details,
      auth:
        settingsResult.status === "fulfilled"
          ? (settingsResult.value?.service_auth ?? null)
          : null,
      loading: false,
      error,
    });
  }, []);
  useEffect(() => {
    alive.current = true;
    const changed = (event: Event) => {
      void refresh((event as CustomEvent).detail);
    };
    void refresh();
    window.addEventListener("floci_session_changed", changed);
    return () => {
      alive.current = false;
      revision.current++;
      window.removeEventListener("floci_session_changed", changed);
    };
  }, [refresh]);
  return (
    <SessionContext.Provider value={{ ...state, refresh }}>
      {children}
    </SessionContext.Provider>
  );
}
