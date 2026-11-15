# Bleep README video: spec

GOAL:    a visitor who never heard of Bleep sees, in 22 s, a service break, Bleep find the cause,
         ask for a yes, fix it and check it, and leaves knowing what Bleep is and where it lives.
OUTPUT:  HyperFrames, 1920 x 1080, 30 fps.
TIME:    25.1 s x 30 = 753 frames; music bgm 107 bpm, its lift on the yes click (12.3 s).
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
  5 end        17.7-25.1 overview All quiet; the cursor clicks the theme toggle (19.2) and the
                         page turns dark from it; then Bleep waves, the wordmark, the README's
                         tagline and github.com/DannyKuk/bleep, held 3.6 s.
BANNED:  a dark void with floating elements; glow, gradients, glass; a title over a dimmed
         screenshot; text that moves while it is read; three cards in a row; the logo docked
         in a corner; any number or label not from the run (or the staged history).
QA:      frames at 1.0, 2.9, 4.5, 6.4, 8.5, 11.0, 12.5, 15.0, 16.5, 19.0, 21.5 and the
         transitions at 3.3, 6.7, 9.7, 17.7, the toggle at 19.2-20.1: captions inside title-safe, nothing clipped, readable at 830 px wide.

## Round 2 (2026-11-15): the 60 s cut

Owner's note on the 25 s cut: hard to understand what Bleep does; it jumps through and undersells.
Causes: Bleep was named only in the last 4 s; each scene was ~3 s of dense UI, so the proof (tool
calls, the cause, the checks) flew past unread; the captions were vague and left out the run's own
numbers. Owner's answer: longer is fine, and the key parts may be taken out of the dashboard and
drawn large, as long as the dashboard is still shown.

So the rule "captures only, never redrawn" is dropped for the beats that must be read. Every drawn
word is the run's own (events.json, incident.json, phone.json); only the short headline lines and
captions are written for the film.

TIME:    60.7 s. Music: HeyGen track 310662767a134b208b6bf1eb21f03efa, 122 bpm, its drop on "Meet
         Bleep" (8.0 s; the track's drop is at 7.875 s).
STAGES:  from 16.8 s to 47.8 s the incident page's own stage bar (Gather ... Report) sits on top,
         drawn large, and moves on with the run.
SHOTS:
  1 hook        0.0-8.0   web-01's six containers; shop-db goes exited, shop-web unhealthy;
                          "The shop's database just stopped."; Bleep's real first message lands.
  2 intro       8.0-13.5  the bot (working pose), "Meet Bleep", "An on-call agent for your Docker
                          hosts.", "This is what it did next, in a real run."
  3 overview   13.5-16.8  the dashboard (captures); cursor clicks Follow along.
  4 investigate 16.8-27.3 the incident page's capture scrolls in a window at left; five of the
                          run's tool calls as cards at right, each with the line that mattered;
                          a counter to 8 tool calls.
  5 cause      27.3-35.8  shop-proxy -> shop-web -x- shop-db; "dependency", 98% sure; the
                          report's cause sentence.
  6 decide     35.8-42.8  the plan card and the phone (captures and phone.py), the yes click.
  7 checks     42.8-47.8  "Ran docker.start shop-db"; the four checks tick; "Fixed and verified".
  8 numbers    47.8-53.3  54 s, 8 tool calls, $0.003, 1 yes from you.
  9 end        53.3-60.7  overview all quiet, the theme toggle, the end card (as before).
