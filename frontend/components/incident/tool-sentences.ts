import type { Messages } from "@/lib/i18n";

/**
 * What one of Bleep's read tools did, as Bleep says it (`reasoning.tools`), or null for a tool
 * the dashboard has no words for. The sentences never quote the call's arguments: the model chose
 * them, and they could carry text from a hostile log, which must not read as Bleep's own words.
 * The arguments stay beside the sentence in mono, as data.
 */
export function toolSentence(name: string, t: Messages): string | null {
  return Object.hasOwn(t.reasoning.tools, name) ? t.reasoning.tools[name] : null;
}
