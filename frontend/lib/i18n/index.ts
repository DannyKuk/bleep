import { useSyncExternalStore } from "react";

import { de } from "@/lib/i18n/de";
import { en } from "@/lib/i18n/en";
import { LANGUAGE_KEY as KEY } from "@/lib/i18n/script";
import type { Language } from "@/lib/settings-types";

/** The shape every language's messages have: English's keys, with any words in each string. */
export type Messages = Widen<typeof en>;

type Widen<T> = T extends string
  ? string
  : T extends (...args: infer A) => infer R
    ? (...args: A) => Widen<R>
    : T extends readonly [string, string]
      ? [string, string]
      : { [K in keyof T]: Widen<T[K]> };

export const MESSAGES: Record<Language, Messages> = { en, de };

/** The locale for dates and times; English keeps the browser's own, as before #376. */
export const DATE_LOCALE: Record<Language, string | undefined> = { en: undefined, de: "de-DE" };

/** The locale for a short day ("2 Nov", "Tue") and for counts ("1,234"). */
export const FIXED_LOCALE: Record<Language, string> = { en: "en-GB", de: "de-DE" };

const listeners = new Set<() => void>();
let held: Language | null = null;

function stored(): Language | null {
  try {
    const value = localStorage.getItem(KEY);
    return value === "en" || value === "de" ? value : null;
  } catch {
    return null;
  }
}

/** The language in force on this page: the last one the settings said, kept in localStorage so
 * the next load (and the login page) starts in it; English until any is known. */
export function readLanguage(): Language {
  return held ?? stored() ?? "en";
}

/** Switches every message on the page to `language` and keeps it for the next load. */
export function setLanguage(language: Language): void {
  held = language;
  try {
    localStorage.setItem(KEY, language);
  } catch {
    // private mode: the language lasts until reload, and the settings say it again then
  }
  document.documentElement.lang = language;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** The language in force. The server renders English; the stored language follows right after. */
export function useLanguage(): Language {
  return useSyncExternalStore(subscribe, readLanguage, () => "en");
}

/** The messages of the language in force. */
export function useT(): Messages {
  return MESSAGES[useLanguage()];
}
