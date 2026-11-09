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
import type { SettingsView } from "@/lib/settings-types";
import { usableZone, type Zone } from "@/lib/zone";

interface TimeZone {
  zone: Zone;
  /** After the settings page saved a zone (an IANA name, or null for none). */
  setZone: (name: string | null) => void;
}

const TimeZoneContext = createContext<TimeZone>({ zone: undefined, setZone: () => {} });

/**
 * Bleep's time zone for every time on the dashboard: the settings' `timezone`, read once when the
 * app loads and again when the settings page saves one. Until it is read, or when none is chosen,
 * times are in the browser's own zone.
 */
export function TimeZoneProvider({ children }: { children: ReactNode }) {
  const [zone, setZoneState] = useState<Zone>(undefined);
  useEffect(() => {
    const controller = new AbortController();
    apiFetch<SettingsView>("/api/settings", { signal: controller.signal })
      .then((view) => setZoneState(usableZone(view.timezone)))
      .catch(() => {}); // the browser's zone, as before one is chosen
    return () => controller.abort();
  }, []);
  const setZone = useCallback((name: string | null) => setZoneState(usableZone(name)), []);
  const value = useMemo<TimeZone>(() => ({ zone, setZone }), [zone, setZone]);
  return <TimeZoneContext.Provider value={value}>{children}</TimeZoneContext.Provider>;
}

/** The zone to show times in; undefined for the browser's own (also outside the provider). */
export const useTimeZone = (): Zone => useContext(TimeZoneContext).zone;

export const useSetTimeZone = () => useContext(TimeZoneContext).setZone;
