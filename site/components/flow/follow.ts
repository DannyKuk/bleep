/** The sticky header's height, which the band below starts under. */
const HEADER = 64;
/** The band, as shares of the screen's height, that Bleep's step may move in without a scroll. */
const BAND_TOP = 0.1;
const BAND_BOTTOM = 0.9;

interface Box {
  top: number;
  bottom: number;
}

/**
 * How far to scroll so the walk's step stays on screen, or 0 while it is inside the band. A step
 * that leaves the band is put back at the band's top, so the next few steps fit below it and the
 * page moves once every few steps instead of with each one. Nothing moves when the diagram is off
 * screen: the visitor has scrolled away and is not pulled back.
 */
export function followBy(step: Box, diagram: Box, screen: number): number {
  if (diagram.bottom < HEADER || diagram.top > screen) return 0;
  const top = HEADER + screen * BAND_TOP;
  const bottom = screen * BAND_BOTTOM;
  if (step.top >= top && step.bottom <= bottom) return 0;
  return step.top - top;
}
