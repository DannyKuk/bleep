// Shared by every scene of the Bleep README video (SPEC.md).
//
// A scene is written once, dark: its root holds `.half.dark` with the captures as
// `<img data-shot="key">` and the drawn parts. `BF.split(root)` copies that half into a light
// one on top, so the film plays in light; the end scene reveals its dark half when the cursor
// clicks the theme toggle. Captures are 2x screenshots, 2880 px wide; `BF.cam` frames a point of
// them.
(() => {
  const W = 1920;
  const H = 1080;

  function shotSrc(key, theme) {
    return `assets/shots/${key}.${theme}.jpg`;
  }

  function split(root) {
    const dark = root.querySelector(".half.dark");
    for (const img of dark.querySelectorAll("img[data-shot]")) {
      img.src = shotSrc(img.dataset.shot, "dark");
    }
    const light = dark.cloneNode(true);
    light.classList.replace("dark", "light");
    for (const img of light.querySelectorAll("img[data-shot]")) {
      img.src = shotSrc(img.dataset.shot, "light");
    }
    dark.after(light);
    return { all: (sel) => [...root.querySelectorAll(sel)] };
  }

  // The camera's transform that puts capture point (x, y) at the frame's centre with `w`
  // capture pixels across the frame; `dx`, `dy` move that point off centre, in frame pixels.
  function view({ x, y, w, dx = 0, dy = 0 }) {
    const scale = W / w;
    return { x: W / 2 + dx - x * scale, y: H / 2 + dy - y * scale, scale };
  }

  // Where capture point (px, py) lands on the frame under view `v`.
  function onFrame(v, px, py) {
    const t = view(v);
    return { x: t.x + px * t.scale, y: t.y + py * t.scale };
  }

  function cam(tl, targets, v, at, duration = 0, ease = "power3.inOut") {
    const to = { ...view(v), transformOrigin: "0 0" };
    if (duration === 0) tl.set(targets, to, at);
    else tl.to(targets, { ...to, duration, ease }, at);
  }

  // A crossfade from one capture of a layout to the next.
  function swap(tl, show, hide, at, duration = 0.2) {
    tl.fromTo(show, { opacity: 0 }, { opacity: 1, duration, ease: "none" }, at);
    if (hide) tl.to(hide, { opacity: 0, duration: 0.01 }, at + duration);
  }

  // The cursor travels to (x, y) on the frame and clicks at `click`.
  function cursorTo(tl, cursor, from, to, start, click) {
    tl.fromTo(
      cursor,
      { x: from.x, y: from.y, opacity: 1 },
      { x: to.x, y: to.y, duration: click - start - 0.15, ease: "power3.inOut" },
      start,
    );
    const press = cursor.map((c) => c.querySelector(".press"));
    tl.to(press, { scale: 0.86, duration: 0.08, ease: "power2.in" }, click - 0.08);
    tl.to(press, { scale: 1, duration: 0.14, ease: "power2.out" }, click);
    const ring = cursor.map((c) => c.querySelector(".ring"));
    tl.fromTo(
      ring,
      { scale: 0.3, opacity: 0.9 },
      { scale: 1.6, opacity: 0, duration: 0.45, ease: "power2.out" },
      click,
    );
  }


  // Bleep's message html (phone.py) as a lock-screen notification: its bold head and first line.
  function note(html) {
    const [head, ...rest] = html.split("<br>");
    const el = document.createElement("div");
    el.className = "pnote";
    el.innerHTML =
      `<div class="pnote-head"><span>Bleep</span><span>now</span></div>` +
      `<div class="pnote-title">${head.replace(/<\/?b>/g, "")}</div><div class="pnote-text">${rest.join(" ")}</div>` +
      `<div class="touch"></div>`;
    return el;
  }

  // The phone: a lock screen and the chat with Bleep, built from window.BLEEP_PHONE.
  function phone(parent) {
    const el = document.createElement("div");
    el.className = "phone";
    el.setAttribute("data-layout-ignore", "");
    el.innerHTML = `<div class="phone-screen"><div class="phone-island"></div>
      <div class="lock"><div class="lock-time">21:03</div><div class="lock-date">Sunday, 15 November</div><div class="notes"></div></div>
      <div class="chatview"><div class="phone-bar">
        <svg viewBox="0 0 120 120" aria-hidden="true"><rect x="10" y="18" width="100" height="84" rx="38" fill="#b9afff"/><rect x="22" y="36" width="76" height="44" rx="22" fill="#27234A"/><path d="M45 58h0 M75 58h0" fill="none" stroke="#7CF2B8" stroke-width="20" stroke-linecap="round"/></svg>
        <div><div class="phone-name">Bleep</div><div class="phone-sub">bot</div></div></div>
        <div class="chat"></div></div></div>`;
    const chat = el.querySelector(".chat");
    window.BLEEP_PHONE.forEach((m, i) => {
      const msg = document.createElement("div");
      msg.className = `msg msg-${i}`;
      const rows = m.buttons
        .map((row) => `<div class="button-row">${row.map((b) => `<div class="button">${b}<div class="touch"></div></div>`).join("")}</div>`)
        .join("");
      msg.innerHTML = `<div class="bubble">${m.html}</div><div class="buttons">${rows}</div>`;
      chat.appendChild(msg);
    });
    parent.appendChild(el);
    return el;
  }

  // A finger's tap: the touch ring appears, presses and fades.
  function tap(tl, touch, at) {
    tl.fromTo(touch, { opacity: 0, scale: 1.4 }, { opacity: 1, scale: 0.85, duration: 0.18, ease: "power2.out" }, at - 0.18);
    tl.to(touch, { scale: 1.5, opacity: 0, duration: 0.35, ease: "power2.out" }, at + 0.05);
  }

  window.BF = { W, H, split, view, onFrame, cam, swap, cursorTo, note, phone, tap };
})();
