/**
 * The post-login target, or "/" when it is not a path on this site. The browser's own URL parser
 * decides: it drops tabs and newlines and reads "\" as "/", so "/\t/evil.com" and "/\evil.com"
 * resolve to another origin and are rejected, which a string prefix check would miss.
 */
export function safeNext(next: string): string {
  try {
    const origin = window.location.origin;
    const url = new URL(next, origin);
    if (url.origin !== origin) return "/";
    return url.pathname + url.search + url.hash;
  } catch {
    return "/";
  }
}
