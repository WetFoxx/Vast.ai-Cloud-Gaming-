// pad.js — [2026-10-15] управление окном как в Steam Big Picture: стрелки и геймпад ведут белую рамку к ближайшей кнопке
// в том направлении, Enter / A — нажать, Esc / B — назад; звуки наведения, нажатия и переключения (свои, синтез WebAudio —
// не звуки Valve). Мышь работает как раньше: рамка с клавиатуры прячется при движении мыши.

(() => {
  // ------------------------------------------------------------ звуки
  let ac = null, master = null;
  let soundsOn = true;
  function ctx() {
    if (!soundsOn) return null;
    if (!ac) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      ac = new AC();
      master = ac.createGain();
      master.gain.value = 0.55;
      master.connect(ac.destination);
    }
    if (ac.state === "suspended") ac.resume();
    return ac;
  }
  // Короткая нота: частота (или скольжение от → до), форма, громкость, длительность, задержка
  function note(f0, f1, type, vol, dur, at = 0) {
    const a = ctx();
    if (!a) return;
    const t0 = a.currentTime + at;
    const o = a.createOscillator(), g = a.createGain();
    o.type = type;
    o.frequency.setValueAtTime(f0, t0);
    if (f1 !== f0) o.frequency.exponentialRampToValueAtTime(f1, t0 + dur * 0.8);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(vol, t0 + 0.006);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(g); g.connect(master);
    o.start(t0); o.stop(t0 + dur + 0.02);
  }
  function whoosh(dur, vol) {
    const a = ctx();
    if (!a) return;
    const len = Math.floor(a.sampleRate * dur), buf = a.createBuffer(1, len, a.sampleRate), d = buf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / len);
    const src = a.createBufferSource(), f = a.createBiquadFilter(), g = a.createGain(), t0 = a.currentTime;
    src.buffer = buf;
    f.type = "bandpass"; f.Q.value = 0.9;
    f.frequency.setValueAtTime(300, t0); f.frequency.exponentialRampToValueAtTime(3800, t0 + dur);
    g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(vol, t0 + dur * 0.6); g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    src.connect(f); f.connect(g); g.connect(master);
    src.start(t0);
  }
  let lastMove = 0;
  const sfx = {
    move() {                                       // наведение — тихий высокий щелчок
      const now = performance.now();
      if (now - lastMove < 45) return;
      lastMove = now;
      note(1500 + Math.random() * 120, 1250, "sine", 0.05, 0.045);
    },
    select() { note(620, 620, "triangle", 0.09, 0.07); note(930, 930, "triangle", 0.08, 0.09, 0.045); },
    back() { note(930, 930, "triangle", 0.07, 0.06); note(560, 560, "triangle", 0.07, 0.09, 0.045); },
    toggle(on) { note(on ? 700 : 880, on ? 1050 : 600, "sine", 0.08, 0.08); },
    error() { note(240, 180, "square", 0.04, 0.16); },
    launch() {                                     // запуск трансляции — нарастающий аккорд и «вжух»
      whoosh(0.9, 0.08);
      [523.25, 659.25, 783.99, 1046.5].forEach((f, i) => note(f, f, "triangle", 0.07, 1.1 - i * 0.12, 0.12 + i * 0.09));
      note(1567.98, 1567.98, "sine", 0.035, 1.2, 0.5);
    },
  };
  window.sfx = sfx;
  window.setSounds = (on) => { soundsOn = !!on; };

  // ------------------------------------------------------------ навигация
  const FOCUSABLE = "button, select, input:not([type=hidden]), textarea, a[href], .game-tile, [data-pad]";
  let cur = null, padMode = false;

  function visible(e) {
    if (e.disabled || e.closest("[hidden]")) return false;
    const r = e.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    const st = getComputedStyle(e);
    return st.visibility !== "hidden" && st.display !== "none" && st.pointerEvents !== "none";
  }
  function scope() {
    const modal = document.getElementById("modal");
    if (modal && !modal.hidden) return [modal];
    const fx = document.getElementById("launch-fx");
    if (fx && !fx.hidden) return [fx];
    const screen = [...document.querySelectorAll("body > main")].find((m) => !m.hidden);
    return [document.getElementById("rail"), screen].filter(Boolean);
  }
  function items() {
    return scope().flatMap((root) => [...root.querySelectorAll(FOCUSABLE)])
      .filter((e) => !e.closest(".game-tile") || e.classList.contains("game-tile"))   // кнопки внутри обложки — сама обложка
      .filter(visible);
  }
  function setFocus(e, sound = true) {
    if (!e || e === cur) return;
    if (cur) cur.classList.remove("pad-focus");
    cur = e;
    if (padMode) e.classList.add("pad-focus");
    e.dispatchEvent(new CustomEvent("padfocus", { bubbles: true }));   // экран может показать выбранное (картинка игры)
    if (["INPUT", "TEXTAREA"].includes(e.tagName) && !["checkbox", "radio", "range"].includes(e.type)) e.focus({ preventScroll: true });
    else if (document.activeElement && document.activeElement !== document.body && document.activeElement !== e) document.activeElement.blur();
    reveal(e);
    if (sound) sfx.move();
  }
  // Своя плавная прокрутка (scrollBy smooth в окне не всегда едет, а прерванная оставляла страницу на полпути).
  // Новая — сначала доводит прежнюю до конца, потом считает от честного положения.
  const anims = new Map();
  function glide(el, prop, to) {
    const prev = anims.get(el);
    if (prev) { cancelAnimationFrame(prev.raf); setPos(el, prop, prev.to); }
    const from = getPos(el, prop);
    to = Math.max(0, Math.min(to, maxPos(el, prop)));
    if (Math.abs(to - from) < 1) { anims.delete(el); return; }
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) { setPos(el, prop, to); return; }
    const t0 = performance.now(), ms = 230, a = { to };
    const step = (now) => {
      const k = Math.min(1, (now - t0) / ms), e = 1 - Math.pow(1 - k, 3);
      setPos(el, prop, from + (to - from) * e);
      if (k < 1) a.raf = requestAnimationFrame(step); else anims.delete(el);
    };
    a.raf = requestAnimationFrame(step);
    anims.set(el, a);
  }
  const page = () => document.scrollingElement;
  function getPos(el, prop) { return el === window ? page().scrollTop : el[prop]; }
  function setPos(el, prop, v) { if (el === window) page().scrollTop = v; else el[prop] = v; }
  function maxPos(el, prop) { return el === window ? page().scrollHeight - innerHeight : el.scrollWidth - el.clientWidth; }
  function settle() { for (const [el, a] of anims) { cancelAnimationFrame(a.raf); setPos(el, el === window ? "" : "scrollLeft", a.to); } anims.clear(); }

  // Показать выбранное: у верхних кнопок экрана — страница до самого верха (заголовок и фильтры видны), у нижних — до
  // низа, иначе — чтобы не пряталось под машиной/балансом сверху и подсказками снизу. Полка в ряд — своей прокруткой.
  function reveal(e) {
    if (e.closest("#rail, #modal, #launch-fx")) return;
    settle();
    const row = e.closest(".lib-strip");
    if (row && row.scrollWidth > row.clientWidth) {
      const q = e.getBoundingClientRect(), w = row.getBoundingClientRect();
      if (q.left < w.left + 10) glide(row, "scrollLeft", row.scrollLeft + q.left - w.left - 30);
      else if (q.right > w.right - 10) glide(row, "scrollLeft", row.scrollLeft + q.right - w.right + 30);
    }
    const r = e.getBoundingClientRect(), y0 = page().scrollTop, full = page().scrollHeight;
    const hints = (document.getElementById("pad-hints") || {}).offsetHeight || 0;
    const top = 90, bottom = innerHeight - hints - 24;
    if (r.top + y0 < innerHeight * 0.4) glide(window, "", 0);
    else if (full - (r.bottom + y0) < innerHeight * 0.3) glide(window, "", full);
    else if (r.top < top) glide(window, "", y0 + r.top - top - 16);
    else if (r.bottom > bottom) glide(window, "", y0 + r.bottom - bottom + 16);
  }
  function move(dx, dy) {
    const list = items();
    if (!list.length) return;
    if (!cur || !list.includes(cur)) {               // первый шаг — главная кнопка экрана или первая
      const main = list.find((e) => e.classList.contains("primary") && !e.closest("#rail")) || list.find((e) => !e.closest("#rail")) || list[0];
      return setFocus(main);
    }
    const r = cur.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    // Рамка ушла за край (листали стиком или колесом) — начать с того, что сейчас на экране, ближе к середине
    if (!cur.closest("#rail") && !anims.size && (r.bottom < 40 || r.top > innerHeight - 20)) {
      const mid = innerHeight / 2;
      const seen = list.filter((e) => { const q = e.getBoundingClientRect(); return !e.closest("#rail") && q.bottom > 80 && q.top < innerHeight - 60; });
      const near = seen.sort((a, b) => Math.abs(a.getBoundingClientRect().top - mid) - Math.abs(b.getBoundingClientRect().top - mid))[0];
      if (near) return setFocus(near);
    }
    let best = null, bs = Infinity;
    const inRail = !!cur.closest("#rail");
    for (const e of list) {
      if (e === cur) continue;
      if (dy && !!e.closest("#rail") !== inRail) continue;     // вверх-вниз — не перескакивать между колонкой и экраном
      const q = e.getBoundingClientRect(), x = q.left + q.width / 2, y = q.top + q.height / 2;
      // по направлению — от края до края (длинные строки и узкие кнопки), поперёк — расстояние центров
      const along = dx ? (dx > 0 ? q.left - r.right : r.left - q.right) : (dy > 0 ? q.top - r.bottom : r.top - q.bottom);
      const ahead = dx ? (x - cx) * dx : (y - cy) * dy;
      if (ahead <= 2 || along < -Math.min(r.width, r.height) / 2) continue;
      const across = dx ? Math.max(0, Math.abs(y - cy) - (r.height + q.height) / 4) : Math.max(0, Math.abs(x - cx) - (r.width + q.width) / 4);
      const s = Math.max(0, along) + across * 2.5;
      if (s < bs) { bs = s; best = e; }
    }
    if (best) setFocus(best); else sfx.error();
  }
  function press() {
    const e = cur;
    if (!e || !visible(e)) return;
    if (e.tagName === "SELECT") {                   // список — следующий вариант по кругу (как переключатель в Big Picture)
      const opts = [...e.options].filter((o) => !o.disabled);
      const i = opts.findIndex((o) => o.value === e.value);
      e.value = opts[(i + 1) % opts.length].value;
      e.dispatchEvent(new Event("change", { bubbles: true }));
      sfx.toggle(true);
      return;
    }
    if (e.tagName === "INPUT" && !["checkbox", "radio", "button", "submit", "range"].includes(e.type)) { e.focus(); return; }
    e.click();
  }
  const BACK = { offers: "offers-cancel", setup: "setup-back", library: "library-back", history: "history-back", games: "games-back" };
  function back() {
    const modal = document.getElementById("modal");
    if (modal && !modal.hidden) { document.getElementById("modal-no").click(); return; }
    const fx = document.getElementById("lfx-close");
    if (fx && !fx.closest("[hidden]")) { fx.click(); return; }
    const a = document.activeElement;
    if (a && ["INPUT", "TEXTAREA"].includes(a.tagName)) { a.blur(); return; }
    const screen = document.body.dataset.screen || "main";
    let id = BACK[screen];
    if (screen === "progress") id = document.getElementById("progress-home").hidden ? "progress-back" : "progress-home";
    const b = id && document.getElementById(id);
    if (b && !b.hidden && !b.disabled) { b.click(); return; }
    if (screen !== "main") document.getElementById("brand").click();
  }

  // Клавиатура: стрелки — по кнопкам; в поле ввода и в открытом списке — как обычно
  addEventListener("keydown", (ev) => {
    const a = document.activeElement, typing = a && (["TEXTAREA"].includes(a.tagName) ||
      (a.tagName === "INPUT" && !["checkbox", "radio", "button", "range"].includes(a.type)));
    const dir = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[ev.key];
    if (dir && !(typing && dir[0])) {
      if (a && a.tagName === "SELECT" && !padMode) return;
      ev.preventDefault();
      padOn();
      move(...dir);
    } else if ((ev.key === "Enter" || ev.key === " ") && padMode && cur && !typing) {
      ev.preventDefault();
      press();
    } else if (ev.key === "Escape") {
      if (padMode || typing) { ev.preventDefault(); back(); }
    }
  }, true);
  function padOn() {
    if (padMode) return;
    padMode = true;
    document.body.classList.add("pad-mode");
    try { window.pywebview && window.pywebview.api.hide_cursor(); } catch (e) { /* в браузере (демо) — только стилем */ }
    if (cur && visible(cur)) cur.classList.add("pad-focus"); else cur = null;
  }
  // Мышь — обычный режим: рамку убрать, при наведении на кнопку — тихий звук
  addEventListener("mousemove", (ev) => {
    // прокрутка под неподвижной мышью тоже даёт mousemove — без сдвига; его не считаем
    if (!padMode || (!ev.movementX && !ev.movementY)) return;
    padMode = false;
    document.body.classList.remove("pad-mode");
    if (cur) cur.classList.remove("pad-focus");
  }, { passive: true });
  let hovered = null;
  addEventListener("mouseover", (ev) => {
    const e = ev.target.closest && ev.target.closest(FOCUSABLE);
    if (e === hovered) return;
    hovered = e;
    if (e && !e.disabled && !padMode) { cur = e; sfx.move(); }
  });
  // Звук нажатия — у всех кнопок; «Назад» и «Отмена» — свой; переключатели — «включил/выключил»
  addEventListener("click", (ev) => {
    const e = ev.target.closest && ev.target.closest("button, .game-tile, [data-pad]");
    if (!e || e.disabled) return;
    if (/-back$|-cancel$|^modal-no$/.test(e.id || "")) sfx.back(); else sfx.select();
  }, true);
  addEventListener("change", (ev) => {
    const e = ev.target;
    if (e && e.type === "checkbox") sfx.toggle(e.checked);
  }, true);
  // Экран сменился — рамка на главную кнопку нового экрана (при управлении с геймпада)
  new MutationObserver(() => {
    if (cur && !visible(cur)) { cur.classList.remove("pad-focus"); cur = null; if (padMode) setTimeout(() => !cur && move(0, 1), 60); }
  }).observe(document.body, { subtree: true, attributes: true, attributeFilter: ["hidden"] });

  // ------------------------------------------------------------ геймпад
  let raf = 0, last = 0, held = {}, repeatAt = 0;
  function poll(t) {
    const pad = [...(navigator.getGamepads ? navigator.getGamepads() : [])].find(Boolean);
    if (!pad) { raf = 0; return; }
    const b = (i) => !!(pad.buttons[i] && pad.buttons[i].pressed);
    const ax = pad.axes[0] || 0, ay = pad.axes[1] || 0;
    const dx = b(15) || ax > 0.5 ? 1 : b(14) || ax < -0.5 ? -1 : 0, dy = b(13) || ay > 0.5 ? 1 : b(12) || ay < -0.5 ? -1 : 0;
    if (dx || dy) {
      const first = !held.dir;
      if (first || t > repeatAt) { padOn(); move(dx, dy); repeatAt = t + (first ? 380 : 120); }
      held.dir = true;
    } else held.dir = false;
    // Правый стик — плавно листать страницу (скорость — от наклона)
    const ry = pad.axes[3] || 0, dz = 0.18;
    if (Math.abs(ry) > dz && last) {
      const k = (Math.abs(ry) - dz) / (1 - dz), dt = Math.min(0.05, (t - last) / 1000);
      settle();
      page().scrollTop += Math.sign(ry) * k * k * 1600 * dt;
    }
    if (b(0) && !held[0]) { padOn(); press(); }
    if (b(1) && !held[1]) { padOn(); back(); }
    held[0] = b(0); held[1] = b(1);
    last = t;
    raf = requestAnimationFrame(poll);
  }
  addEventListener("gamepadconnected", () => { document.body.classList.add("has-pad"); if (!raf) raf = requestAnimationFrame(poll); });
  addEventListener("gamepaddisconnected", () => { if (![...navigator.getGamepads()].some(Boolean)) document.body.classList.remove("has-pad"); });
})();
