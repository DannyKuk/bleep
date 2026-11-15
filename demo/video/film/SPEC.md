# Bleep README video: spec

GOAL:    a visitor who never heard of Bleep sees, in 22 s, a service break, Bleep find the cause,
         ask for a yes, fix it and check it, and leaves knowing what Bleep is and where it lives.
OUTPUT:  HyperFrames, 1920 x 1080, 30 fps.
TIME:    24.7 s x 30 = 741 frames.
LOOK:    the dashboard itself, in the light theme; at the end the cursor clicks the dashboard's
         theme toggle and the dark capture of the same screen grows out of it in a circle. (A
         diagonal light/dark split on every frame was tried and dropped: it pulled the eye away.)
         Grounds are Bleep's own: light #f3f3fa, dark #14132a. Accent #5847e0 (light),
         #a99eff (dark), only for captions, the cursor ring and the end card's link.
         Type: Nunito (the dashboard's face). Captions 60 px / 800, tracking -1%, white on the
         accent, radius 28 px, 28 px x 44 px padding. Phone text 22 px / 600.
         The medium is product UI: real captures of one real run (2x, 2880 px wide), never
         redrawn. Only the phone (a generic chat app) and the end card are drawn.
OBJECTS: the dashboard (captures), a cursor (macOS-style arrow, 64 px, white with ink
         outline), a phone (470 x 960, 58 px corner radius, 14 px bezel #0e0d1c, the chat in
         the theme's colours, messages rendered by Bleep's own Notifier), Bleep the robot
         (the dashboard's own SVG, all_good pose, waving).
MOTION:  camera moves 0.5-0.9 s on power3.inOut; slow drifts on sine.inOut; state swaps are
         6-frame crossfades between captures of the same layout; entrances power3.out
         0.4 s; the phone slides 0.6 s on expo.out; clicks scale the cursor 1 -> 0.86 -> 1
         over 6 frames.
SHOTS:
  1 overview   0.0-3.3   All quiet -> "Watching shop" (0.9) -> "shop is down. I am on it." (1.9);
                         slow push to the hero and the host card; cursor clicks Follow along
                         (3.0). Caption "shop-db just stopped." Sound: whoosh on the cut.
  2 look       3.3-6.7   incident page, the camera drops from the hero to "How I got there"
                         while six captures fill it with real tool calls. Caption "Bleep reads
                         the logs, like you would."
  3 found      6.7-9.7   "What I found": 98% sure, dependency, the cause. Push in. Caption
                         "It finds the cause."
  4 decide     9.7-17.7  the plan card at left, the phone slides in at right with Bleep's
                         plan message (Approve / Reject). Cursor clicks "Yes, go ahead" (12.3):
                         "You said yes on the dashboard", the phone's buttons go. Captures go
                         Checking -> Check 1: passed (14.6), the phone gets "Fixed in 1 min"
                         (15.4). Captions "Nothing changes until you say yes." then "Fixed,
                         and checked before it says so."
  5 end        17.7-24.7 overview All quiet; the cursor clicks the theme toggle (19.2) and the
                         page turns dark from it; then Bleep waves, the wordmark, the README's
                         tagline and github.com/DannyKuk/bleep, held 3.2 s.
BANNED:  a dark void with floating elements; glow, gradients, glass; a title over a dimmed
         screenshot; text that moves while it is read; three cards in a row; the logo docked
         in a corner; any number or label not from the run (or the staged history).
QA:      frames at 1.0, 2.9, 4.5, 6.4, 8.5, 11.0, 12.5, 15.0, 16.5, 19.0, 21.5 and the
         transitions at 3.3, 6.7, 9.7, 17.7, the toggle at 19.2-20.1: captions inside title-safe, nothing clipped, readable at 830 px wide.
