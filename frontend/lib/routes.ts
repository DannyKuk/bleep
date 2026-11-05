/** A service's page. Its id holds a "/" and a ":" (`demo/compose:shop`), so it is encoded. */
export function serviceHref(serviceId: string): string {
  return `/services/${encodeURIComponent(serviceId)}`;
}

/** The id from a page's route segment, whether or not the router decoded it. */
export function idFromSegment(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment; // a lone "%" that was never encoded
  }
}

/** A host's page. */
export function hostHref(hostId: string): string {
  return `/hosts/${encodeURIComponent(hostId)}`;
}
