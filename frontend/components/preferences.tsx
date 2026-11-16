"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { apiFetch } from "@/lib/api";
import { setLanguage, useLanguage } from "@/lib/i18n";
import type { SettingsView } from "@/lib/settings-types";
import { usableZone, type Zone } from "@/lib/zone";

interface TimeZone {
  zone: Zone;
  /** After the settings page saved a zone (an IANA name, or null for none). */
  setZone: (name: string | null) => void;
}

const TimeZoneContext = createContext<TimeZone>({ zone: undefined, setZone: () => {} });
const LoginContext = createContext<boolean | undefined>(undefined);

/**
 * Bleep's time zone and language for the whole dashboard: the settings' `timezone` and `language`,
 * read once when the app loads and again when the settings page saves one. Until the zone is
 * read, or when none is chosen, times are in the browser's own zone; until the language is read,
 * the page speaks the one it last knew (`readLanguage`), so the dashboard and Telegram match.
 * Also whether Bleep's own login is on, which is unknown until the settings say.
 */
export function PreferencesProvider({ children }: { children: ReactNode }) {
  const [zone, setZoneState] = useState<Zone>(undefined);
  const [loginEnabled, setLoginEnabled] = useState<boolean | undefined>(undefined);
  const language = useLanguage();
  useEffect(() => {
    const controller = new AbortController();
    apiFetch<SettingsView>("/api/settings", { signal: controller.signal })
      .then((view) => {
        setZoneState(usableZone(view.timezone));
        if (view.language === "en" || view.language === "de") setLanguage(view.language);
        setLoginEnabled(view.login_enabled !== false);
      })
      .catch(() => {}); // the browser's zone and the last language known, as before
    return () => controller.abort();
  }, []);
  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);
  const setZone = useCallback((name: string | null) => setZoneState(usableZone(name)), []);
  const value = useMemo<TimeZone>(() => ({ zone, setZone }), [zone, setZone]);
  return (
    <LoginContext.Provider value={loginEnabled}>
      <TimeZoneContext.Provider value={value}>{children}</TimeZoneContext.Provider>
    </LoginContext.Provider>
  );
}

/** The zone to show times in; undefined for the browser's own (also outside the provider). */
export const useTimeZone = (): Zone => useContext(TimeZoneContext).zone;

export const useSetTimeZone = () => useContext(TimeZoneContext).setZone;

/** False when BLEEP_LOGIN_ENABLED is false (#611): there is no login to log out of. Undefined
 * until the settings are read. */
export const useLoginEnabled = (): boolean | undefined => useContext(LoginContext);
