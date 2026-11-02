/** A service's page. Its id holds a "/" and a ":" (`demo/compose:shop`), so it is encoded. */
export function serviceHref(serviceId: string): string {
  return `/services/${encodeURIComponent(serviceId)}`;
}

/** The id from a service page's route segment, whether or not the router decoded it. */
export function serviceIdFrom(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment; // a lone "%" that was never encoded
  }
}
