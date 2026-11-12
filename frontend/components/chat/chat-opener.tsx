"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
} from "react";

type Open = (text: string) => void;

interface Opener {
  /** The panel says how it opens; the returned function takes that back. */
  register: (open: Open) => () => void;
  ask: Open;
}

const OpenerContext = createContext<Opener | null>(null);

/**
 * Lets a page open the chat panel with words already typed in its input (#478). It wraps the
 * app layout; the panel registers how it opens, and a page asks through `useAskBleep`.
 */
export function ChatOpenerProvider({ children }: { children: ReactNode }) {
  const openRef = useRef<Open | null>(null);
  const opener = useMemo<Opener>(
    () => ({
      register(open) {
        openRef.current = open;
        return () => {
          if (openRef.current === open) openRef.current = null;
        };
      },
      ask(text) {
        openRef.current?.(text);
      },
    }),
    [],
  );
  return <OpenerContext.Provider value={opener}>{children}</OpenerContext.Provider>;
}

/** The panel's side: `open` opens it with the text typed in, not sent. */
export function useRegisterChatOpener(open: Open) {
  const opener = useContext(OpenerContext);
  useEffect(() => opener?.register(open), [opener, open]);
}

/** Opens the chat panel with `text` typed in its input for the person to send. Without the
 * provider or the panel, as in a page's own tests, it does nothing. */
export function useAskBleep(): Open {
  const opener = useContext(OpenerContext);
  return useCallback((text: string) => opener?.ask(text), [opener]);
}
