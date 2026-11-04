"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";

export interface SaveLock {
  /** A save under this lock is in flight. */
  busy: boolean;
  /** Runs `task` unless another save holds the lock; resolves false when it did not run. */
  run: (task: () => Promise<void>) => Promise<boolean>;
}

function useLock(): SaveLock {
  // The ref refuses a second save in the same tick, before `busy` has re-rendered the buttons.
  const held = useRef(false);
  const [busy, setBusy] = useState(false);
  const run = useCallback(async (task: () => Promise<void>) => {
    if (held.current) return false;
    held.current = true;
    setBusy(true);
    try {
      await task();
      return true;
    } finally {
      held.current = false;
      setBusy(false);
    }
  }, []);
  return useMemo(() => ({ busy, run }), [busy, run]);
}

const SaveLockContext = createContext<SaveLock | null>(null);

/** One save in flight at a time for every form inside, so each is built from the last one's result. */
export function SaveLockProvider({ children }: { children: React.ReactNode }) {
  const lock = useLock();
  return <SaveLockContext value={lock}>{children}</SaveLockContext>;
}

/** The page's lock, or one of the component's own outside a provider. */
export function useSaveLock(): SaveLock {
  const own = useLock();
  return useContext(SaveLockContext) ?? own;
}
