// app.js — поведение окна: главный экран, выбор машины, ход запуска, вопросы да/нет.
// Данные — у Python: window.pywebview.api.<функция>() (см. gui.py). Тексты — t(ключ) из i18n.js.
// Демо-режим для разработки в браузере: index.html?demo=ready&lang=en
// (demo: none, starting, ready, syncing, error, low, offers, progress) — поддельные данные.

const REFRESH_SEC = 15;
const $ = (id) => document.getElementById(id);
const DEMO = new URLSearchParams(location.search).get("demo");
const api = () => window.pywebview.api;

let current = null;      // последние данные главного экрана
let offersData = null;   // список машин на экране выбора
let selected = null;     // выбранная машина (id)
let prog = null;         // ход операции: {since, log[], shownQ}
let progTimer = null;

// ---------------------------------------------------------------- помощники
function money(x) { return "$" + Number(x).toFixed(2); }
function num(x, digits = 1) {
  return new Intl.NumberFormat(LANG, { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(x);
}
function errText(e) { return e && e.key ? t(e.key, e.params || {}) : (e && e.text) || String(e); }
function showError(id, e) { const el = $(id); el.hidden = !e; el.textContent = e ? errText(e) : ""; }
// Размер: «340 МБ» или «62,1 ГБ» — сохранения обычно весят мегабайты
function size(bytes) {
  return bytes >= 1e9 ? t("unit.gb", { n: num(bytes / 1e9, 1) }) : t("unit.mb", { n: num(bytes / 1e6, 0) });
}
function hm(sec) {
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  return h ? t("unit.hm", { h, m }) : t("unit.m", { m });
}
function duration(sec) {
  const m = Math.floor(sec / 60), s = sec % 60;
  return t("unit.min_sec", { m, s });
}

function show(screen) {
  for (const s of ["main", "offers", "progress", "games", "setup", "history", "wizard", "library"]) $("screen-" + s).hidden = s !== screen;
  for (const s of ["library", "games", "history", "setup"]) $("open-" + s).classList.toggle("active", s === screen);
}

// Вопрос да/нет в окне. Возвращает Promise<boolean>: true — только нажатие «да»
function ask({ title, text, yes, no }) {
  return new Promise((resolve) => {
    $("modal-title").textContent = title;
    $("modal-text").textContent = text;
    $("modal-yes").textContent = yes || t("btn.yes");
    $("modal-no").textContent = no || t("btn.no");
    $("modal").hidden = false;
    const done = (v) => { $("modal").hidden = true; $("modal-yes").onclick = $("modal-no").onclick = null; resolve(v); };
    $("modal-yes").onclick = () => done(true);
    $("modal-no").onclick = () => done(false);
  });
}

// Закрытие окна во время операции: Python отменил закрытие и спрашивает здесь
async function askClose() {
  const yes = await ask({ title: t("close.confirm.title"), text: t("close.confirm.text"), yes: t("close.confirm.yes"), no: t("btn.cancel") });
  if (yes) api().force_close();
}

// ---------------------------------------------------------------- главный экран
function statusText(st) {
  const p = {
    count: st.count, status: st.vast_status || "", detail: st.detail || "",
    done: st.done, busy: (st.busy || []).length, names: st.state === "error" ? st.errors || [] : st.busy || [],
  };
  return t("status.text." + st.kind, p);
}

// Баланс «докручивается» до нового числа (при открытии — от нуля); «Уменьшить движение» в macOS — сразу
let shownBalance = null;
function setBalance(value) {
  const el = $("balance"), to = Number(value), from = shownBalance === null ? 0 : shownBalance;
  shownBalance = to;
  if (from === to || matchMedia("(prefers-reduced-motion: reduce)").matches) { el.textContent = money(to); return; }
  const t0 = performance.now(), ms = 900;
  const step = (now) => {
    const k = Math.min(1, (now - t0) / ms), eased = 1 - Math.pow(1 - k, 3);
    el.textContent = money(from + (to - from) * eased);
    if (k < 1 && shownBalance === to) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function render(data) {
  if (data.error) { showError("error", data.error); return; }
  showError("error", null);
  current = data;
  if (data.game_names) GAME_NAMES = data.game_names;
  if (data.play_now !== undefined && !playGame) playNow = data.play_now;   // окно открыли заново — что запускали

  setBalance(data.balance);
  const st = data.status, m = st.machine;
  // Машины нет — крупно баланс и «Играть»; есть — баланс справа, под состоянием — ярлыки машины и сессии
  $("machine-card").classList.toggle("empty", !m);
  const sub = [];
  if (data.hours_left != null) sub.push(t("balance.hours.short", { hours: num(data.hours_left) }));
  if (!m) sub.push(t("balance.label"));
  if (data.low_balance) sub.push(t("balance.low"));
  $("balance-sub").textContent = sub.join(" · ");
  $("balance-card").classList.toggle("low", !!data.low_balance);

  $("dot").className = "dot " + st.state;
  $("status-title").textContent = t("status.title." + st.state);
  // Подробности состояния — только когда их не видно ниже (ход архивов, строка запуска)
  const task0 = data.task && data.task.active;
  $("status-text").hidden = ["ready", "syncing"].includes(st.state) || !!task0 || !m;
  $("status-text").textContent = statusText(st);
  const facts = $("facts");
  facts.hidden = !m;
  facts.innerHTML = "";
  if (m) {
    const chip = (text, cls = "") => facts.appendChild(el("span", { className: "chip " + cls }, text));
    chip(`${m.gpu} · ${m.country}`, "strong");
    chip(t("unit.per_hour", { price: money(m.price) }));
    // Сколько идёт сессия и сколько она уже стоит (по своей записи: начало и цена часа)
    if (data.session) chip(t("session.running", { time: hm(data.session.elapsed), cost: money(data.session.cost) }), "muted");
    facts.title = t("machine.instance", { id: m.id });
  }
  const li = st.links;
  $("links").hidden = !li || st.state === "starting";
  if (li) $("moonlight").textContent = li.moonlight;
  if (li && st.state !== "starting") { loadMoonlightApps(li.moonlight); loadStreamSettings(); }

  // Идёт запуск или выключение (фоновый процесс) — строка с этапом и «Показать ход»
  const task = data.task && data.task.active ? data.task : null;
  $("task-row").hidden = !task;
  $("task-replace").hidden = !task || task.kind !== "play";
  if (task) $("task-text").textContent = t("task." + task.kind) + (st.stage ? " · " + t("stage." + st.stage) : "");
  renderLive(liveFromStatus(st));
  renderAsks(st.asks || []);
  if (st.play) renderPlay(st.play, playNowGame(), null, st.progs); else renderPlay(null);
  renderProgs(st.progs);
  const playing = st.play && !["ok", "err"].includes(st.play.state);
  liveLoop(!!(task || playing || ["starting", "syncing"].includes(st.state)));

  $("play").hidden = !!m;
  $("play").disabled = st.state !== "none" || !!data.busy || (setupData && !setupData.ready);
  // «Закончить» — когда машина работает (не во время запуска); аварийное удаление — пока машина есть
  $("foot").hidden = !m;
  $("stop").disabled = !m || !["ready", "syncing", "error"].includes(st.state) || !!data.busy;
  $("emergency").hidden = !m || !!data.busy;
}

// ---- Живой ход на главном: архивы в работе (с процентами и скоростью) и вопросы машины. Главный экран обновляется
// раз в 15 с (баланс и состояние — через Vast), а это — прямо со страницы машины раз в 3 с, пока что-то идёт.
function liveFromStatus(st) {
  const out = {};
  for (const [n, v] of Object.entries(st.archives || {})) out[n] = [v[1], v[2], (st.progress || {})[n] || null];
  return out;
}
function renderLive(archives) {
  const names = sortedArchives(archives).filter((n) => ["down", "up", "err"].includes(archives[n][0]));
  const ul = $("live-archives");
  $("live").hidden = !names.length;
  ul.innerHTML = "";
  for (const n of names) ul.appendChild(archiveRow(n, archives[n][0], archives[n][2]));
}
let liveTimer = null;
function liveLoop(on) {
  if (on && !liveTimer) liveTimer = setInterval(pollLive, 3000);
  if (!on && liveTimer) { clearInterval(liveTimer); liveTimer = null; }
}
async function pollLive() {
  if ($("screen-main").hidden || !current || !current.status.machine || DEMO) return;
  const d = await api().live();
  if (!d || d.error) return;
  if (d.game_names) GAME_NAMES = d.game_names;
  renderLive(d.archives);
  renderAsks(d.asks);
  if (d.play_game) playNow = d.play_game;
  renderPlay(d.play, playNowGame(), d.streaming, d.progs);
  renderProgs(d.progs);
}
let playNow = null;          // игра, с которой арендована машина (Python помнит её: окно могли закрыть)
function playNowGame() { return playNow || (playGame && { ...playGame }); }

// Вопросы машины на главном: ответить можно здесь (карточка поверх всех окон — та же, её можно было закрыть)
let asksBusy = false;
function renderAsks(list) {
  if (asksBusy) return;
  $("asks-card").hidden = !list.length;
  const ul = $("asks-list");
  ul.innerHTML = "";
  for (const a of list) {
    const li = el("li");
    const text = el("div", { className: "ask-text" });
    text.append(el("b", {}, t(`ask.${a.kind}.q`, { name: a.name })), el("span", { className: "sub" }, t(`ask.${a.kind}.hint`)));
    const row = el("div", { className: "ask-buttons" });
    const note = el("div", { className: "sub ask-note" });
    const go = async (yes) => {
      asksBusy = true;
      row.querySelectorAll("button").forEach((b) => (b.disabled = true));
      note.textContent = t(yes && a.kind === "del" ? "ask.deleting" : "ask.sending");
      const r = DEMO ? { ok: true } : await api().ask_answer(a.kind, a.archive, yes);
      asksBusy = false;
      if (r && r.error) { note.textContent = errText(r.error); row.querySelectorAll("button").forEach((b) => (b.disabled = false)); return; }
      li.remove();
      $("asks-card").hidden = !ul.children.length;
      if (current) current.status.asks = (current.status.asks || []).filter((x) => x.archive !== a.archive || x.kind !== a.kind);
    };
    row.append(button(t(`ask.${a.kind}.yes`), () => go(true), true), button(t(`ask.${a.kind}.no`), () => go(false)));
    li.append(text, row, note);
    ul.appendChild(li);
  }
}

$("task-show").addEventListener("click", () => { if (current && current.task) startProgress(current.task.kind); });

async function refresh() {
  renderMainLibrary();
  try {
    render(DEMO ? demoState(DEMO) : await api().state(LANG));
  } catch (e) {
    render({ error: { text: t("app.error_state", { error: e }) } });
  }
}

$("copy-moonlight").addEventListener("click", async () => {
  const text = $("moonlight").textContent;
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const r = document.createRange(); r.selectNodeContents($("moonlight"));
    const s = getSelection(); s.removeAllRanges(); s.addRange(r); document.execCommand("copy");
  }
  $("copy-moonlight").textContent = t("btn.copied");
  setTimeout(() => ($("copy-moonlight").textContent = t("btn.copy")), 1500);
});

// ---- Moonlight: список приложений Sunshine приходит с самой машины (один раз на машину)
let mlHost = null;
async function loadMoonlightApps(host) {
  if (host === mlHost) return;
  mlHost = host;
  const sel = $("ml-app");
  sel.innerHTML = "<option>Desktop</option>";
  const r = DEMO ? { installed: true, apps: ["Desktop", "Steam Big Picture"], selected: "Desktop" } : await api().moonlight_apps(host);
  if (!r.installed) {
    $("ml-start").disabled = true;
    $("ml-note").hidden = false;
    $("ml-note").textContent = t("moonlight.missing");
    return;
  }
  sel.innerHTML = "";
  for (const a of r.apps) { const o = document.createElement("option"); o.value = o.textContent = a; sel.appendChild(o); }
  sel.value = r.selected;
}

// Запуск трансляции с любой кнопки (главный экран, экран «Готово»). Связка в первый раз идёт до
// минуты — кнопку не нажать второй раз (две трансляции мешают друг другу); уже идёт — так и сказать
async function launchMoonlight(btn, note, host, app) {
  btn.disabled = true;
  note.hidden = false;
  note.textContent = t("moonlight.starting");
  try {
    const r = DEMO ? { ok: true } : await api().open_moonlight(host, app);
    if (r.busy) { note.textContent = t("moonlight.busy"); return; }
    note.textContent = r.error ? errText(r.error) : "";
    note.hidden = !r.error;
  } finally {
    btn.disabled = false;
  }
}
function startMoonlight(host, app) { return launchMoonlight($("ml-start"), $("ml-note"), host, app); }

// ---- Разрешение и битрейт: по умолчанию — родное разрешение экрана и «авто» (подбирает Moonlight)
let streamLoaded = false;
async function loadStreamSettings() {
  if (streamLoaded) return;
  streamLoaded = true;
  const s = DEMO ? demoStream() : await api().stream_settings();
  if (!s || s.error) { streamLoaded = false; return; }
  const res = $("stream-res");
  res.innerHTML = "";
  const native = s.resolutions.filter((o) => o.native || o.value === "moonlight");
  const other = s.resolutions.filter((o) => !native.includes(o));
  const group = (label, list) => {
    const g = el("optgroup", { label });
    for (const o of list) {
      const text = o.value === "moonlight" ? t("stream.res.moonlight")
        : o.native ? t("stream.res.native", { w: o.w, h: o.h }) : `${o.w} × ${o.h}`;
      g.appendChild(el("option", { value: o.value }, text));
    }
    res.appendChild(g);
  };
  if (native.length) group(t(native[0].native ? "stream.group.native" : "stream.group.default"), native);
  group(t("stream.group.other"), other);
  res.value = s.resolution;
  const br = $("stream-bitrate");
  br.innerHTML = "";
  br.appendChild(el("option", { value: 0 }, t("stream.bitrate.auto")));
  for (const b of s.bitrates) br.appendChild(el("option", { value: b }, t("stream.bitrate.mbps", { n: b })));
  br.value = s.bitrate;
  streamOptions = s.resolutions;
  $("stream-set").hidden = false;
  showStreamHint();
}
let streamOptions = [];
// «Трансляция: 2560 × 1600 · авто» — выбор одной строкой; сами списки — в «Параметрах»
function streamSummary() {
  const o = streamOptions.find((x) => x.value === $("stream-res").value);
  const res = !o ? "—" : o.value === "moonlight" ? t("stream.res.moonlight") : `${o.w} × ${o.h}`;
  const br = Number($("stream-bitrate").value);
  $("stream-summary").innerHTML = "";
  $("stream-summary").append(t("stream.summary") + " ", el("b", {}, res + " · " + (br ? t("stream.bitrate.mbps", { n: br }) : t("stream.bitrate.auto.short"))));
}
function showStreamHint() {
  const native = $("stream-res").value === "native";
  $("stream-res").classList.toggle("native", native);
  $("stream-hint").hidden = false;
  $("stream-hint").textContent = t(native ? "stream.hint.native" : "stream.hint.other");
  streamSummary();
}
for (const id of ["stream-res", "stream-bitrate"]) {
  $(id).addEventListener("change", () => {
    showStreamHint();
    if (!DEMO) api().save_stream_settings($("stream-res").value, Number($("stream-bitrate").value));
  });
}

$("ml-start").addEventListener("click", () => {
  if (current && current.status.links) startMoonlight(current.status.links.moonlight, $("ml-app").value || "Desktop");
});

$("open-sunshine").addEventListener("click", () => {
  if (current && current.status.links && !DEMO) api().open_url(current.status.links.sunshine);
});

// «Параметры ›»: разрешение и битрейт; ручное подключение (адрес, кабинет Sunshine, его логин)
$("params-toggle").addEventListener("click", () => {
  $("params").hidden = !$("params").hidden;
  $("params-toggle").textContent = t($("params").hidden ? "params.open" : "params.close");
});
$("show-login").addEventListener("click", async () => {
  const box = $("sunshine-login");
  if (!box.hidden) { box.hidden = true; $("show-login").textContent = t("params.login.show"); return; }
  const r = DEMO ? { user: "vastgame", password: "demo-password" } : await api().sunshine_login();
  box.textContent = r && r.user ? t("setup.sunshine_login", { user: r.user, password: r.password }) : t("setup.sunshine_login.none");
  box.hidden = false;
  $("show-login").textContent = t("params.login.hide");
});

// ---------------------------------------------------------------- выбор машины
$("play").addEventListener("click", () => { replaceMode = false; replaceBlocked = false; playGame = null; openOffers(); });

// ---- «Сменить машину»: каталог во время запуска. Две машины сразу — никогда: выбранную арендуем только после того,
// как запуск текущей отменён, она удалена и Vast её больше не показывает (Api.replace). Готовую — только через «Закончить».
let replaceMode = false;      // каталог во время запуска: «Заменить машину» вместо «Арендовать»
let replaceBlocked = false;   // пока смотрели каталог, машина стала готовой — заменять уже нельзя
let replaceTimer = null;
function openReplace() {
  replaceMode = true;
  replaceBlocked = false;
  clearInterval(progTimer);
  openOffers();
  clearInterval(replaceTimer);
  replaceTimer = setInterval(watchLaunch, 5000);
}
async function watchLaunch() {
  if ($("screen-offers").hidden || !replaceMode) { clearInterval(replaceTimer); replaceTimer = null; return; }
  if (DEMO) return;
  const p = await api().session_progress(1e9);
  if (!p || !p.done) return;
  if (!p.error) replaceBlocked = true;          // машина готова
  else replaceMode = false;                     // запуск не удался или отменён — машины нет, обычная аренда
  renderReplace();
  renderOffers();
}
function renderReplace() {
  const box = $("offers-current"), d = offersData;
  rentLabel();
  box.hidden = !replaceMode && !replaceBlocked;
  if (box.hidden) return;
  if (replaceBlocked) { box.textContent = t("replace.ready"); return; }
  const c = d && d.current;
  box.textContent = c ? t("replace.current", { machine: `${c.gpu} · ${c.country} · ${money(c.price)}`,
                                               traffic: offersDocker && c.tb_up != null ? " · " + t("traffic.short", { price: trafficText(c) }) : "",
                                               ping: c.ping && c.ping.ms != null ? (c.ping.kind === "estimate" ? "≈ " : "") + t("unit.ms", { n: c.ping.ms }) : "—" })
                      : t("replace.current_unknown");
}
function betterTags(o, addTag) {
  // Чем машина лучше той, что запускается: дешевле и/или пинг меньше (одинаковое — без метки)
  const c = offersData && offersData.current;
  if (!replaceMode || !c) return;
  const cheaper = (c.price || 0) - o.price;
  if (cheaper >= 0.005) addTag("tag good", t("replace.cheaper", { d: money(cheaper) }), t("replace.better.hint"));
  if (c.ping && c.ping.ms != null && o.ping && o.ping.ms != null && c.ping.ms - o.ping.ms >= 10)
    addTag("tag good", t("replace.faster", { ms: Math.round(c.ping.ms - o.ping.ms) }), t("replace.better.hint"));
}
$("task-replace").addEventListener("click", openReplace);
$("progress-replace").addEventListener("click", openReplace);

let offersReq = 0;   // номер последнего запроса списка: ответ на устаревший (другой диск) не показывать
let offersStale = false;   // [0.4.5] на экране прошлый список (свежий ещё идёт) — арендовать нельзя

// [0.4.5] Прошлый ответ медленного экрана (snapshot.py) — показать сразу, пока идёт свежий; пометка «обновляю…»
async function snapshotOf(name) {
  if (DEMO) return null;
  try { return await api().snapshot(name); } catch (e) { return null; }
}
function setStale(id, on) { $(id).classList.toggle("stale", !!on); }
let offersDocker = null;   // вкладка: false — KVM, true — Docker (основная); null — как в прошлый раз
async function openOffers(disk, docker) {
  const reload = disk != null;       // смена диска: тумблеры синхронизации не сбрасывать
  if (docker != null) offersDocker = docker;
  show("offers");
  renderOffersGame();
  offersData = null; selected = null; offersStale = false;
  setStale("screen-offers", false);
  $("rent").disabled = true;
  $("offers-refresh").disabled = true;
  $("offers-summary").textContent = t("offers.loading");
  $("offers-table").querySelector("tbody").innerHTML = "";
  showError("offers-error", null);
  const my = ++offersReq;
  showTabs();
  const fresh = DEMO ? null : api().offers(disk ?? null, offersDocker, replaceMode);
  if (!replaceMode) {
    const snap = await snapshotOf("offers:" + (offersDocker == null ? "auto" : offersDocker ? "docker" : "kvm"));
    if (my === offersReq && !offersData && snap && Array.isArray(snap.offers) && (disk == null || snap.disk === disk)) {
      offersData = snap; offersStale = true;
      offersDocker = !!snap.docker;
      showTabs();
      setStale("screen-offers", true);
      $("disk").min = snap.disk_min; $("disk").max = snap.disk_max; $("disk").value = snap.disk;
      renderOffers();
    }
  }
  const data = DEMO ? { ...demoOffers(disk), docker: !!offersDocker } : await fresh;
  if (my !== offersReq) return;
  $("offers-refresh").disabled = false;
  offersStale = false;
  setStale("screen-offers", false);
  if (data.error && replaceMode && data.error.key === "core.running") {   // пока открывали, машина стала готовой
    replaceBlocked = true; replaceMode = false; renderReplace();
  }
  if (data.error) {
    offersData = null;                       // прошлый список не оставлять: по нему не арендовать
    $("offers-table").querySelector("tbody").innerHTML = "";
    $("offers-pager").hidden = true;
    $("rent").disabled = true;
    showError("offers-error", data.error);
    return;
  }
  offersData = data;
  if (replaceMode && !data.replace) replaceMode = false;       // запуск успел закончиться неудачно — обычная аренда
  renderReplace();
  offersDocker = !!data.docker;
  showTabs();
  const slider = $("disk");
  slider.min = data.disk_min; slider.max = data.disk_max; slider.value = data.disk;
  renderOffers();
}

function showTabs() {
  $("offers-table").classList.toggle("kvm", !offersDocker);       // у KVM трафик Vast не списывает — колонки нет
  $("tab-kvm").classList.toggle("active", !offersDocker);
  $("tab-docker").classList.toggle("active", !!offersDocker);
  $("offers-docker-note").textContent = t(offersDocker ? "offers.mode.docker_note" : "offers.mode.kvm_note");
  $("offers-docker-note").hidden = false;
  $("f-traffic").hidden = !offersDocker;
}
// Вкладка — другой список машин (диск тот же): Docker без KVM, но с драйвером от 580
$("tab-kvm").addEventListener("click", () => { if (offersDocker) openOffers(offersData ? offersData.disk : null, false); });
$("tab-docker").addEventListener("click", () => { if (!offersDocker) openOffers(offersData ? offersData.disk : null, true); });
// «Обновить» — свежий список машин той же вкладки и диска (выбор игр не сбрасывается)
$("offers-refresh").addEventListener("click", () => openOffers(offersData ? offersData.disk : Number($("disk").value), offersDocker));

// Ползунок диска: число меняется сразу, список машин (цены зависят от диска) — когда отпустили
$("disk").addEventListener("input", () => { $("disk-value").textContent = t("disk.value", { gb: $("disk").value }); });
$("disk").addEventListener("change", () => openOffers(Number($("disk").value)));

// ---- Фильтры списка машин: страны, скорость интернета, пинг, сортировка (запоминаются в Python)
let filters = null, page = 0;
const SPEEDS = [0, 100, 300, 500, 1000];          // Мбит/с — не ниже (меньшая из скоростей ↓/↑)
const PINGS = [0, 50, 80, 100, 150, 200];         // мс — не выше
const RELS = [97, 95, 90];                        // надёжность хоста, % — не ниже (97 — как было до 0.3.2)
function relOk(o, rel) { return o.reliability * 100 >= (rel || 97) - 1e-9; }
const TRAFFICS = [0, 0.1, 0.5, 1, 2, 5];          // $ за ТБ — не дороже; 0.1 — «бесплатный»; только Docker (у KVM не списывается)
function trafficOk(o, lim) { return !lim || (lim === 0.1 ? o.traffic_tb < 0.1 : o.traffic_tb <= lim); }
function netSpeed(o) { return Math.min(o.net_down, o.net_up); }
function countryName(cc) {
  try { return new Intl.DisplayNames([LANG], { type: "region" }).of(cc) || cc; } catch (e) { return cc; }
}
function saveFilters() { if (!DEMO) api().save_offer_filters(filters); }
function filtersActive() {
  return filters.countries.length || filters.gpus.length || filters.speed || filters.ping
    || (offersDocker && filters.traffic);   // надёжность — отдельно: у неё своё значение по умолчанию (97%)
}

// Сортировка — нажатием на заголовок колонки; повторное нажатие переворачивает. Первое нажатие —
// самый полезный порядок: цена и пинг от меньшего, интернет и надёжность от большего.
// Модель видеокарты — по номеру серии («RTX 4070» → 4070), внутри серии: обычная < Super < Ti < Ti Super;
// ноутбучная — ниже настольной с тем же номером. GTX 1660 < RTX 2060 < … < RTX 5090. Первое нажатие — старшие сверху
// Профессиональные видеокарты — место среди игровых примерно по силе (для сортировки и замены «не хуже»)
const PRO_RANK = { "RTX A2000": 30500, "RTX A4000": 30602, "RTX A4500": 30700, "RTX A5000": 30800, "RTX A6000": 30900,
  "Q RTX 4000": 20700, "Q RTX 5000": 20800, "Q RTX 6000": 20802, "Q RTX 8000": 20802, "Titan RTX": 20803,
  "RTX 4000Ada": 40602, "RTX 4500Ada": 40700, "RTX 5000Ada": 40800, "RTX 5880Ada": 40900, "RTX 6000Ada": 40900,
  "RTX PRO 4000": 50700, "RTX PRO 4500": 50702, "RTX PRO 5000": 50800, "RTX PRO 6000 WS": 50900, "RTX PRO 6000 Max-Q": 50895 };
function gpuRank(o) {
  if (PRO_RANK[o.gpu]) return PRO_RANK[o.gpu];
  const m = /^(?:RTX|GTX)\s*(\d{3,4})\s*(.*)$/i.exec(o.gpu || "");
  if (!m) return null;
  const rest = m[2].toLowerCase(), ti = /\bti\b/.test(rest), sup = /\bs\b|super/.test(rest);
  return Number(m[1]) * 10 + (ti ? 2 : 0) + (sup ? 1 : 0) - (/laptop|mobile|max-q/.test(rest) ? 5 : 0);
}
// Трафик сортируется по ↑ (с машины к тебе): за час игры уходит ~30 ГБ трансляции — это основная часть трат
const SORT_VALUE = { price: (o) => o.price, ping: (o) => o.ping.ms, net: netSpeed, traffic: (o) => o.tb_up ?? o.traffic_tb, gpu: gpuRank };
const SORT_FIRST = { price: "asc", ping: "asc", net: "desc", traffic: "asc", gpu: "desc" };
const STREAM_TB_HOUR = 0.03;   // ТБ за час трансляции (~30 ГБ)
// Цена трафика: «$3.91» (в обе стороны одинаково), «↓ $4.00 / ↑ $5.33», «бесплатно»
function trafficText(o, sep = " / ") {
  const d = o.tb_down || 0, u = o.tb_up || 0;
  if (d < 0.1 && u < 0.1) return t("traffic.free");
  return Math.abs(d - u) < 0.005 ? money(d) : `↓ ${money(d)}${sep}↑ ${money(u)}`;
}

// Замены выбранной машины (решение 2026-09-27): одновременно арендуется только она; если Vast её не запустит (10 минут
// или ошибка хоста), фоновый процесс возьмёт следующую из этого списка — в порядке и с фильтрами человека, но только
// «не хуже»: видеокарта не слабее, цена в час не выше (в Docker — и трафик), пинг не больше (пинг выбранной неизвестен —
// только из той же страны). Цену фоновый процесс перепроверяет ещё раз по свежим данным.
// [2026-10-15] Замена — «похожая», а не «не хуже»: видеокарта не слабее, цена и трафик до +20%, пинг до +25% и 20 мс
// (раньше после сбоя часто не оставалось ни одной замены). Тот же запас +20% по цене сверяет фоновый процесс (jobs.py)
const SPARE_MARGIN = 1.2;
function notWorse(x, o) {
  const gx = gpuRank(x), go = gpuRank(o);
  if (gx == null || go == null || gx < go || x.price > o.price * SPARE_MARGIN + 1e-9) return false;
  if (offersDocker && x.traffic_tb > o.traffic_tb * SPARE_MARGIN + 1e-9) return false;
  if (o.ping.ms == null) return x.cc === o.cc;
  return x.ping.ms != null && x.ping.ms <= o.ping.ms * 1.25 + 20;
}
function spareFor(id) {
  const list = offerList(offersData), o = list.find((x) => x.id === id);
  return o ? list.filter((x) => x.id !== id && notWorse(x, o)).map((x) => x.id).slice(0, 10) : [];
}

function applyFilters(list) {
  const f = filters;
  const r = list.filter((o) => (!f.countries.length || f.countries.includes(o.cc))
    && (!f.gpus.length || f.gpus.includes(o.gpu))
    && (!offersDocker || trafficOk(o, f.traffic))
    && (!f.speed || netSpeed(o) >= f.speed)
    && (!f.ping || (o.ping.ms != null && o.ping.ms <= f.ping))
    && relOk(o, f.rel));
  // Цена — только аренда: трафик по реальным списаниям почти не берётся. Неизвестное (пинг «—») — всегда в конце
  const key = f.sort === "traffic" && !offersDocker ? "price" : f.sort;   // на KVM колонки трафика нет
  const val = SORT_VALUE[key] || SORT_VALUE.price, dir = f.dir === "desc" ? -1 : 1;
  r.sort((a, b) => {
    const x = val(a), y = val(b);
    if (x == null || y == null) return (x == null) - (y == null) || a.price - b.price;
    return (x - y) * dir || a.price - b.price;
  });
  return r;
}

for (const th of document.querySelectorAll("#offers-table th.sortable")) {
  th.addEventListener("click", () => {
    const key = th.dataset.sort;
    if (filters.sort === key) filters.dir = filters.dir === "asc" ? "desc" : "asc";
    else { filters.sort = key; filters.dir = SORT_FIRST[key]; }
    page = 0; saveFilters(); renderOffers();
  });
}

// Машины в таблице: последняя (если свободна и проходит фильтры) — первой строкой с пометкой
function offerList(d) {
  const last = d.last && d.last.offer;
  const rest = applyFilters(d.offers.filter((o) => !last || o.id !== last.id));
  return last && applyFilters([last]).length ? [{ ...last, last: true }, ...rest] : rest;
}

function fillSelect(sel, values, label, value) {
  sel.innerHTML = "";
  for (const v of values) sel.appendChild(el("option", { value: v }, label(v)));
  sel.value = value;
}

function renderFilters(d) {
  const chip = (key, v) => t(key) + ": " + v;
  fillSelect($("f-speed"), SPEEDS, (v) => chip("filter.speed", v ? t("filter.speed.from", { n: v }) : t("filter.any")), filters.speed);
  fillSelect($("f-ping"), PINGS, (v) => chip("filter.ping", v ? t("filter.ping.to", { n: v }) : t("filter.any")), filters.ping);
  fillSelect($("f-rel"), RELS, (v) => chip("filter.rel", t("filter.rel.from", { n: v })), filters.rel || 97);
  $("f-rel").classList.toggle("on", (filters.rel || 97) !== 97);
  fillSelect($("f-traffic"), TRAFFICS, (v) => chip("filter.traffic", !v ? t("filter.any") : v === 0.1 ? t("filter.traffic.free")
    : t("filter.traffic.to", { n: v })), filters.traffic ?? 1);
  // включённый фильтр — цветом
  $("f-speed").classList.toggle("on", !!filters.speed);
  $("f-ping").classList.toggle("on", !!filters.ping);
  $("f-traffic").classList.toggle("on", !!(filters.traffic ?? 1));
  $("f-countries-btn").classList.toggle("on", filters.countries.length > 0);
  $("f-gpus-btn").classList.toggle("on", filters.gpus.length > 0);
  for (const th of document.querySelectorAll("#offers-table th.sortable")) {
    const on = th.dataset.sort === (filters.sort || "price");
    th.classList.toggle("sorted", on);
    th.title = t("sort.hint");
    th.querySelector(".arrow").textContent = on ? (filters.dir === "desc" ? "▼" : "▲") : "";
  }
  const names = filters.countries.map(countryName);
  $("f-countries-btn").textContent = (!names.length ? t("filter.countries.any")
    : names.slice(0, 2).join(", ") + (names.length > 2 ? ` +${names.length - 2}` : "")) + " ▾";
  // Страны — только те, что есть в списке, с числом машин
  const counts = {};
  for (const o of d.offers) counts[o.cc] = (counts[o.cc] || 0) + 1;
  const list = $("f-countries-list");
  list.innerHTML = "";
  for (const cc of Object.keys(counts).sort((a, b) => countryName(a).localeCompare(countryName(b), LANG))) {
    const box = el("input", { type: "checkbox", checked: filters.countries.includes(cc) });
    box.addEventListener("change", () => {
      filters.countries = box.checked ? [...filters.countries, cc] : filters.countries.filter((c) => c !== cc);
      page = 0; saveFilters(); renderOffers();
    });
    const lab = el("label");
    lab.append(box, el("span", {}, countryName(cc)), el("span", { className: "count" }, String(counts[cc])));
    list.appendChild(lab);
  }
  // Видеокарты — так же: модели из списка с числом машин, от старших к младшим
  const g = filters.gpus;
  $("f-gpus-btn").textContent = (!g.length ? t("filter.gpus.any") : g.slice(0, 2).join(", ") + (g.length > 2 ? ` +${g.length - 2}` : "")) + " ▾";
  const gcounts = {};
  for (const o of d.offers) gcounts[o.gpu] = (gcounts[o.gpu] || 0) + 1;
  const glist = $("f-gpus-list");
  glist.innerHTML = "";
  const rank = (name) => gpuRank({ gpu: name }) ?? -1;
  for (const name of Object.keys(gcounts).sort((a, b) => rank(b) - rank(a) || a.localeCompare(b))) {
    const box = el("input", { type: "checkbox", checked: g.includes(name) });
    box.addEventListener("change", () => {
      filters.gpus = box.checked ? [...filters.gpus, name] : filters.gpus.filter((x) => x !== name);
      page = 0; saveFilters(); renderOffers();
    });
    const lab = el("label");
    lab.append(box, el("span", {}, name), el("span", { className: "count" }, String(gcounts[name])));
    glist.appendChild(lab);
  }
}

for (const [id, key] of [["f-speed", "speed"], ["f-ping", "ping"], ["f-traffic", "traffic"], ["f-rel", "rel"]]) {
  $(id).addEventListener("change", () => {
    filters[key] = Number($(id).value);
    page = 0; saveFilters(); renderOffers();
  });
}
// Панели под строкой фильтров: открыта одна
function togglePop(id) {
  for (const x of ["f-disk", "f-countries", "f-gpus"]) $(x).hidden = x !== id || !$(x).hidden;
}
$("f-disk-btn").addEventListener("click", () => togglePop("f-disk"));
$("f-countries-btn").addEventListener("click", () => togglePop("f-countries"));
$("f-countries-all").addEventListener("click", () => { filters.countries = []; page = 0; saveFilters(); renderOffers(); });
$("f-gpus-btn").addEventListener("click", () => togglePop("f-gpus"));
$("f-gpus-all").addEventListener("click", () => { filters.gpus = []; page = 0; saveFilters(); renderOffers(); });
$("page-prev").addEventListener("click", () => { page--; renderOffers(); });
$("page-next").addEventListener("click", () => { page++; renderOffers(); });

function pingCell(td, p) {
  td.classList.add("ping", p.ms == null ? "unknown" : p.kind);
  td.textContent = p.ms == null ? "—" : (p.kind === "estimate" ? "≈ " : "") + t("unit.ms", { n: p.ms });
  td.title = t("ping.kind." + (p.ms == null ? "unknown" : p.kind));
}

function renderOffers() {
  const d = offersData;
  if (!d) return;
  if (!filters) filters = { ...d.filters, countries: [...(d.filters.countries || [])], gpus: [...(d.filters.gpus || [])] };
  if (!SORT_VALUE[filters.sort]) filters.sort = "price";
  renderFilters(d);
  const list = offerList(d);
  // «Подходящих» — с выбранной надёжностью (машины ниже порога приходят, но без фильтра не показываются)
  $("offers-summary").textContent = t("offers.summary", { count: d.offers.filter((o) => relOk(o, filters.rel)).length,
                                                        rel: filters.rel || 97, disk: d.disk })
    + (filtersActive() ? " · " + t("offers.filtered", { shown: list.length }) : "") + " · " + t("offers.latency.short");
  $("offers-summary").title = t("offers.latency");
  $("disk-value").textContent = t("disk.value", { gb: d.disk });
  const L = d.last;
  $("offers-last").hidden = !L;
  if (L) {
    const when = L.start ? new Date(L.start * 1000).toLocaleDateString(LANG, { day: "numeric", month: "short" }) : "—";
    $("offers-last").textContent = t(L.offer ? "offers.last.free" : "offers.last.busy",
      { machine: `${L.gpu || "?"} · ${L.country || "?"}`, date: when });
  }
  const body = $("offers-table").querySelector("tbody");
  body.innerHTML = "";
  showError("offers-error", null);
  if (!list.length) {
    $("offers-pager").hidden = true;
    $("rent").disabled = true;
    showError("offers-error", { key: d.offers.length ? "offers.none_filtered" : "offers.none" });
    return;
  }
  // По умолчанию — первая в текущем порядке (самая выгодная или ближняя), не «последняя»
  if (!list.some((o) => o.id === selected)) selected = (list.find((o) => !o.last) || list[0]).id;
  const size = d.page_size || 15, pages = Math.ceil(list.length / size);
  page = Math.max(0, Math.min(page, pages - 1));
  $("offers-pager").hidden = pages <= 1;
  $("page-info").textContent = t("offers.page", { page: page + 1, pages });
  $("page-prev").disabled = page === 0;
  $("page-next").disabled = page >= pages - 1;
  for (const o of list.slice(page * size, (page + 1) * size)) {
    const tr = document.createElement("tr");
    tr.className = o.id === selected ? "selected" : "";
    tr.innerHTML = `<td><input type="radio" name="offer"></td><td></td><td></td>
      <td class="num"></td><td class="num"></td><td class="num col-traffic traffic"></td><td class="num"></td>`;
    const c = tr.children;
    c[0].firstChild.checked = o.id === selected;
    c[1].textContent = o.gpu;
    const addTag = (cls, text, title) => {
      const tag = document.createElement("span");
      tag.className = cls; tag.textContent = text; if (title) tag.title = title;
      c[1].appendChild(tag);
    };
    if (o.gpus === 2) addTag("tag", "×2", t("offers.pair.hint"));   // сдаётся только парой: работает одна, цена — за обе
    if (o.last) addTag("tag", t("offers.last.tag"));
    if (o.start_min && !o.failed) addTag("tag", t("offers.start.tag", { min: o.start_min }), t("offers.start.hint"));
    if (o.reliability < 0.97) addTag("tag warn", t("offers.rel.tag", { rel: num(o.reliability * 100, 0) }), t("offers.rel.risk"));
    // раньше не запустилась — причина во всплывающей подсказке, на языке окна (старые записи без ключа — общая фраза)
    if (o.failed) addTag("tag warn", t("offers.failed.tag"), o.failed_why ? errText(o.failed_why) : t("offers.failed.hint"));
    betterTags(o, addTag);
    c[2].textContent = o.country;
    c[2].title = t("offers.rel.hint", { rel: num(o.reliability * 100) });   // надёжность — подсказкой, колонки больше нет
    pingCell(c[3], o.ping);
    c[4].textContent = t("unit.mbps", { n: netSpeed(o) });
    c[4].title = t("net.both", { down: o.net_down, up: o.net_up });
    c[5].innerHTML = "";
    const tt = trafficText(o, "\n");
    for (const [i, line] of tt.split("\n").entries()) { if (i) c[5].appendChild(el("br")); c[5].append(line); }
    c[5].classList.toggle("free", tt === t("traffic.free"));
    c[5].title = t("traffic.hint");
    c[6].textContent = money(o.price);
    tr.addEventListener("click", () => { selected = o.id; renderOffers(); });
    body.appendChild(tr);
  }
  $("rent").disabled = replaceBlocked || offersStale;
  rentLabel();
}

// «Арендовать RTX 3070 · $0.10/ч» (в замене — «Заменить на …»)
function rentLabel() {
  const o = offersData && filters && offerList(offersData).find((x) => x.id === selected);   // фильтры — после renderOffers
  const key = replaceMode ? "offers.replace" : "offers.rent";
  $("rent").textContent = o ? t(key + ".to", { gpu: o.gpu, price: t("unit.per_hour", { price: money(o.price) }) }) : t(key);
}

$("offers-cancel").addEventListener("click", () => { show("main"); refresh(); });

$("rent").addEventListener("click", async () => {
  if (selected == null) return;
  $("rent").disabled = true;
  $("offers-summary").textContent = t("confirm.rent.checking");
  const disk = offersData.disk;
  const docker = !!offersData.docker;
  const o = DEMO ? demoCheck() : await api().check_offer(selected, disk, docker);   // свежая цена прямо перед «да»
  renderOffers();
  if (o.error && o.error.key === "core.offer_gone") {   // заняли, пока выбирали — показать свежий список
    await openOffers(disk);
    showError("offers-error", o.error);
    return;
  }
  if (o.error) { showError("offers-error", o.error); $("rent").disabled = false; return; }
  let text = t("confirm.rent.text", {
    gpu: o.gpu, country: o.country, price: money(o.price), balance: money(o.balance), hours: num(o.hours || 0),
  });
  text += "\n" + t("confirm.rent.disk", { gb: disk });
  if (playGame && !replaceMode) {
    text += "\n\n" + t("confirm.rent.game", { name: playGame.name, account: accName(playGame.account) });
    text += " " + t(playGame.mods ? "confirm.rent.mods" : "confirm.rent.clean");
  }
  const select = null;                       // [v5.7] выбора игр нет: свои игры из облака — все, сохранения — всегда
  if (docker) {
    // Трафик в деньгах: час трансляции (~30 ГБ ↑)
    const gamesBytes = 0;
    text += "\n\n" + (o.tb_down < 0.1 && o.tb_up < 0.1 ? t("confirm.rent.traffic_free")
      : t("confirm.rent.traffic", { price: trafficText(o), hour: money((o.tb_up || 0) * STREAM_TB_HOUR) })
        + (gamesBytes ? t("confirm.rent.traffic_games", { size: size(gamesBytes), cost: money((o.tb_down || 0) * gamesBytes / 1e12) }) : "") + ".");
  }
  if (o.bad_reason) text += "\n\n" + t("confirm.badhost", { reason: o.bad_reason });
  // Замена, если выбранная не запустится: только «не хуже» (сколько таких — в тексте подтверждения)
  const spare = spareFor(selected);
  text += "\n\n" + (spare.length ? t("confirm.rent.spare", { n: spare.length }) : t("confirm.rent.nospare"));
  if (docker) text += "\n\n" + t("confirm.rent.docker");
  if (replaceMode) {
    const c = offersData.current;
    text = t("replace.confirm.text", { cur: c ? `${c.gpu} (${c.country})` : t("replace.cur_machine"), new: `${o.gpu} (${o.country})` })
      + "\n\n" + text;
  }
  const yes = await ask({ title: t(replaceMode ? "replace.confirm.title" : "confirm.rent.title"), text,
                          yes: t(replaceMode ? "replace.confirm.yes" : "confirm.rent.yes"), no: t("btn.no") });
  if (!yes) { $("rent").disabled = false; return; }
  if (DEMO) { startProgress(); return; }
  if (replaceMode) {
    // Замена: отменить запуск, удалить машину, дождаться, что у Vast её нет, — и арендовать выбранную
    const r = await api().replace(o.id, disk, LANG, spare, docker, select, null);
    if (r.error) { showError("offers-error", r.error); $("rent").disabled = false; return; }
    $("offers-cancel").disabled = true;
    const p = await waitJob((ev) => ($("offers-summary").textContent = eventText(ev)));
    $("offers-cancel").disabled = false;
    if (p.error) {
      showError("offers-error", p.error);
      if (p.error.key === "replace.ready") { replaceBlocked = true; replaceMode = false; renderReplace(); }
      $("rent").disabled = replaceBlocked;
      return;
    }
    replaceMode = false;
    startProgress();
    return;
  }
  const game = playGame ? { appid: playGame.appid, account: playGame.account, mods: !!playGame.mods,
                            gdata: !!playGame.gdata, ...(playGame.mods ? { recipe: !!playGame.recipe } : {}) } : null;
  const r = await api().play(o.id, null, disk, LANG, spare, docker, select, game);   // язык — для уведомления в конце
  if (r.error) { showError("offers-error", r.error); $("rent").disabled = false; return; }
  playNow = playGame ? { ...playGame } : null;
  for (const k of Object.keys(playOpened)) delete playOpened[k];
  startProgress();
});

// ---------------------------------------------------------------- игра из библиотеки
// Нажал на игру → выбор машины с карточкой игры (аккаунт — если игра есть на нескольких) → аренда с игрой: машина
// (wolf-setup.sh v4.8) входит в Steam нужным аккаунтом, Steam сам качает и запускает игру. Ход — на главном
// (запись play): вход в Steam → скачивание → запуск. Moonlight открывается сам: когда нужно войти и когда игра
// запустилась — один раз и только если трансляция ещё не идёт (вторая оборвала бы обе).
let playGame = null;      // выбранная игра: {appid, name, cap, account, choices: [{id, family}]}
const playOpened = {};    // авто-открытие Moonlight: состояние → уже открывали

function startGame(g) {
  if (current && current.status && current.status.machine) {
    ask({ title: t("play.busy.title"), text: t("play.busy.text"), yes: t("btn.back"), no: t("btn.cancel") });
    return;
  }
  const choices = g.accounts.map((id) => ({ id, family: false }))
    .concat(g.family.filter((id) => !g.accounts.includes(id)).map((id) => ({ id, family: true })))
    .filter((c) => !((libData && libData.accounts) || []).find((a) => a.id === c.id && a.expired));
  if (!choices.length) { steamLogin(null); return; }
  const mods = (libData && libData.mods && libData.mods[g.appid]) || null;
  playGame = { appid: g.appid, name: g.name || "#" + g.appid, cap: g.cap || g.header || "", hero: g.hero || "",
               header: g.header || "", account: choices[0].id, choices, modsInfo: mods, mods: modsSaveOn(g.appid),
               gdataInfo: (libData && libData.lgame && libData.lgame[g.appid]) || null, gdata: gdataOn(g.appid),
               recipe: recipeOn(g.appid) };
  replaceMode = false; replaceBlocked = false;
  openOffers();
}

function renderOffersGame() {
  const box = $("offers-game");
  box.hidden = !playGame || replaceMode;
  box.innerHTML = "";
  if (box.hidden) return;
  if (playGame.cap) {
    const img = el("img", { alt: "", src: playGame.cap });
    img.onerror = () => img.remove();
    box.appendChild(img);
  }
  const main = el("div", { className: "og-main" });
  main.append(el("div", { className: "title" }, t("play.offers.title", { name: playGame.name })),
              el("div", { className: "sub" }, t("play.offers.sub")));
  if (playGame.choices.length > 1) {
    const seg = el("div", { className: "seg" });
    seg.appendChild(el("span", { className: "sub" }, t("play.offers.account")));
    for (const c of playGame.choices) {
      const label = c.family ? t("play.offers.family", { name: accName(c.id) }) : accName(c.id);
      const b = el("button", { className: "chip" + (c.id === playGame.account ? " on" : "") }, label);
      b.addEventListener("click", () => { playGame.account = c.id; renderOffersGame(); });
      seg.appendChild(b);
    }
    main.appendChild(seg);
  }
  // [v5.7] «С модами | Без модов» — когда у игры есть что выбирать: моды в облаке, запуск через программу, рецепт (перевод
  // FF14 — часть «С модами»). Иначе блока нет: игра «с модами» — поставишь мод при закрытой игре, он сохранится сам
  const via = ((libData && libData.launch_sel) || {})[playGame.appid];
  const recipeBox = el("div");
  const drawRecipe = () => {
    recipeBox.innerHTML = "";
    if (recipeOf(playGame.appid) && playGame.mods) recipeBox.appendChild(recipeBlock(playGame.appid, (on) => { playGame.recipe = on; }));
  };
  if (playGame.modsInfo || (via && via.id) || recipeOf(playGame.appid)) {
    main.appendChild(modsChoiceBlock(playGame.appid, playGame.modsInfo, via, (on) => { playGame.mods = on; drawRecipe(); }));
  }
  main.appendChild(gdataBlock(playGame.appid, playGame.gdataInfo, (on) => { playGame.gdata = on; }));
  drawRecipe();
  main.appendChild(recipeBox);
  // [v5.4] программы, которые запустятся вместе с игрой (выбраны раньше на главном)
  const auto = ((libData && libData.progs_sel) || {})[playGame.appid] || [];
  if (auto.length) main.appendChild(el("div", { className: "og-progs" },
    t("progs.with_game", { list: auto.map((i) => i.split("/").pop()).join(", ") })));
  const none = el("button", { className: "link" }, t("play.offers.none"));
  none.addEventListener("click", () => { playGame = null; renderOffersGame(); });
  main.appendChild(none);
  box.appendChild(main);
}

// [v5.7] «С модами | Без модов» вместо тумблеров «Моды» и «Запуск через» (решение Алексея 2026-10-10). С модами — моды из
// облака поверх чистой игры из Steam, запуск через выбранную программу, изменения при закрытой игре сохраняются как
// моды; без модов — чистая игра, моды в облаке не трогаются. Данные игры (её сохранения в папке) — в обоих режимах.
// По умолчанию — с модами (выбор «Без модов» запоминается для игры). Выбирается перед арендой.
function modsSaveOn(appid) {
  const v = libData && libData.mods_save ? libData.mods_save[appid] : undefined;
  return v === undefined || v === null ? true : !!v;
}
function modsChoiceBlock(appid, info, via, onChange) {
  const box = el("div", { className: "mods-choice" });
  const seg = el("div", { className: "seg" });
  const sub = el("div", { className: "sub" });
  const draw = () => {
    const on = modsSaveOn(appid);
    for (const b of seg.children) b.classList.toggle("on", (b.dataset.on === "1") === on);
    const parts = [];
    if (on) {
      if (info) parts.push(t("mods.with.cloud", { size: size(info.bytes), date: info.mtime || "" }));
      if (via && via.id) parts.push(t("mods.with.via", { name: via.id.split("/").pop() }));
      parts.push(t("mods.with.save"));
    } else {
      parts.push(t("mods.without.sub"));
    }
    sub.textContent = parts.join(" · ");
  };
  for (const [on, key] of [[true, "mods.with"], [false, "mods.without"]]) {
    const b = el("button", { className: "chip" }, t(key));
    b.dataset.on = on ? "1" : "0";
    b.addEventListener("click", async () => {
      if (modsSaveOn(appid) === on) return;
      if (libData) libData.mods_save = { ...(libData.mods_save || {}), [appid]: on };
      draw();
      if (onChange) onChange(on);
      if (!DEMO) await api().set_mods_save(appid, on);
    });
    seg.appendChild(b);
  }
  box.append(seg, sub);
  draw();
  return box;
}

// [v5.6] «Хранить игру в облаке» — для игр, которые после Steam докачивает их собственный лаунчер (FF14, MMO): его
// загрузка медленнее Drive, поэтому файлы игры (без модов) сохраняются архивом lgame-- и на следующей машине
// восстанавливаются; лаунчеру остаётся докачать новое. Обычной игре Steam ничего не меняет. Вид — как у «Моды».
function gdataOn(appid) { return !!(libData && libData.gdata && libData.gdata[appid]); }
function gdataBlock(appid, info, onChange, locked = false, state = null) {
  const on = state === null ? gdataOn(appid) : state;
  const box = el("label", { className: "mods-save" + (on ? " on" : "") + (locked ? " locked" : "") });
  const cb = el("input", { type: "checkbox", checked: on, disabled: locked });
  const txt = el("div");
  const head = el("b", {}, t("gdata.toggle"));
  if (info) head.appendChild(el("span", { className: "mods-cloud" }, " · " + t("mods.cloud", { size: size(info.bytes), date: info.mtime || "" })));
  txt.append(head, el("span", { className: "sub" }, t(on ? "gdata.on" : "gdata.off") + (locked ? " " + t("mods.locked") : "")));
  box.append(txt, cb);
  if (locked) return box;
  cb.addEventListener("change", async () => {
    if (libData) libData.gdata = { ...(libData.gdata || {}), [appid]: cb.checked };
    box.classList.toggle("on", cb.checked);
    txt.lastChild.textContent = t(cb.checked ? "gdata.on" : "gdata.off");
    if (onChange) onChange(cb.checked);
    if (!DEMO) await api().set_gdata(appid, cb.checked);
  });
  return box;
}

// [v5.6] Рецепт игры — готовый набор модов одним переключателем (у FF14 — русский перевод xivrus.ru: XIVLauncher,
// Dalamud, Penumbra и перевод ставит машина сама, на следующих машинах — только обновляет перевод). Вид — как у «Моды».
function recipeOf(appid) { return (libData && libData.recipe_apps && libData.recipe_apps[appid]) || null; }
function recipeOn(appid) { return !!(recipeOf(appid) && libData.recipes && libData.recipes[appid]); }
function recipeBlock(appid, onChange, locked = false, state = null) {
  const r = recipeOf(appid) || "ff14-ru";
  const on = state === null ? recipeOn(appid) : state;
  const box = el("label", { className: "mods-save" + (on ? " on" : "") + (locked ? " locked" : "") });
  const cb = el("input", { type: "checkbox", checked: on, disabled: locked });
  const txt = el("div");
  txt.append(el("b", {}, t(`recipe.${r}.toggle`)),
             el("span", { className: "sub" }, t(on ? `recipe.${r}.on` : `recipe.${r}.off`) + (locked ? " " + t("mods.locked") : "")));
  box.append(txt, cb);
  if (locked) return box;
  cb.addEventListener("change", async () => {
    if (libData) libData.recipes = { ...(libData.recipes || {}), [appid]: cb.checked };
    box.classList.toggle("on", cb.checked);
    txt.lastChild.textContent = t(cb.checked ? `recipe.${r}.on` : `recipe.${r}.off`);
    if (onChange) onChange(cb.checked);
    if (!DEMO) await api().set_recipe(appid, cb.checked);
  });
  return box;
}

// Ход игры на главном (из status.play или live().play)
function renderPlay(play, game, streaming, progs) {
  const box = $("play-box");
  box.hidden = !play;
  if (!play) return;
  const name = (game && game.name) || "";
  let key = play.state === "wait" ? (play.text === "login" ? "login" : play.text === "other" ? "other" : "steam")
    : play.state === "down" ? "download"
    : play.state === "up" ? (["mods", "game", "recipe"].includes(play.text) ? play.text : "launch")
    : play.state === "ok" ? "running" : "err";
  // запись машины «running» остаётся после выхода из игры — закрыта ли она, видно по списку программ (v5.4)
  const pr = game && progs && progs[game.appid];
  if (key === "running" && pr && pr.running === false) key = "closed";
  box.innerHTML = "";
  box.classList.toggle("warn", key === "err" || key === "other");
  if (game && game.cap) {
    const img = el("img", { alt: "", src: game.cap });
    img.onerror = () => img.remove();
    box.appendChild(img);
  }
  const main = el("div", { className: "pb-main" });
  main.appendChild(el("div", { className: "pb-title" },
    t("play.state." + key, { name, account: game ? accName(game.account) : "" })));
  const pg = play.progress;
  if (key === "download" && pg && pg.total > 0) {
    const pct = Math.min(100, Math.floor((pg.done / pg.total) * 100));
    const bar = el("div", { className: "bar" });
    const fill = el("span");
    fill.style.width = pct + "%";
    bar.append(fill);
    const sp = avgSpeed("play", pg);
    main.append(bar, el("div", { className: "sub" }, `${pct}% · ${size(pg.done)} / ${size(pg.total)}`
      + (sp ? " · " + t("arch.speed", { speed: size(sp) }) + (sp > 0 ? " · " + leftText((pg.total - pg.done) / sp) : "") : "")));
  }
  // [v5.7] с модами или без — одной строкой (только когда было что выбирать: моды в облаке или выбрано «Без модов»)
  if (game && game.appid && (!game.mods || (libData && libData.mods && libData.mods[game.appid]))) {
    main.appendChild(el("div", { className: "og-progs" }, t(game.mods ? "mods.playing.with" : "mods.playing.without")));
  }
  if (game && game.appid && game.gdata) main.appendChild(gdataBlock(game.appid, null, null, true, true));
  if (game && game.appid && game.recipe && game.mods) main.appendChild(recipeBlock(game.appid, null, true, true));
  if (["login", "other", "err", "running", "closed"].includes(key) && current && current.status.links) {
    main.appendChild(button(t("play.open"), () => startMoonlight(current.status.links.moonlight, $("ml-app").value || "Desktop"), key !== "running"));
  }
  box.appendChild(main);
  // Moonlight — сам: войти в Steam и когда игра пошла; трансляция уже идёт — не трогаем
  if (["login", "other", "running"].includes(key) && !playOpened[key] && streaming === false && current && current.status.links && !DEMO) {
    playOpened[key] = true;
    if (key !== "running") playOpened.running = playOpened.running || false;
    startMoonlight(current.status.links.moonlight, $("ml-app").value || "Desktop");
  }
}


// [v5.4] Программы игры (запись progs машины): программы, добавленные в папку игры, и папка Programs.
// «Запустить» — рядом с игрой, в её Proton (игра должна быть запущена); «Вместе с игрой» — сама при каждом старте игры,
// на этой и следующих машинах (выбор запоминается). Ответ кнопки держим 20 с — блок перерисовывается при обновлении.
const progNotes = {};
function renderProgs(progs) {
  const box = $("progs-box");
  const games = Object.entries(progs || {});          // игра на машине — блок виден всегда (пустой — с подсказкой)
  box.hidden = !games.length;
  box.innerHTML = "";
  for (const [appid, g] of games) {
    box.appendChild(el("div", { className: "pg-title" }, g.name ? t("progs.title.game", { name: g.name }) : t("progs.title")));
    if (!(g.progs || []).length) box.appendChild(el("div", { className: "pg-hint" }, t("progs.empty")));
    box.appendChild(launchRow(appid, g, progs));
    for (const p of g.progs || []) {
      const row = el("div", { className: "pg-row" });
      const nm = el("div", { className: "pg-name" });
      nm.append(el("b", { title: p.name }, p.name), p.where === "pfx"     // [v5.6] коротко; подробности — подсказкой
        ? el("span", { className: "sub", title: progWhere(p) }, t("progs.where.pfx_short")) : el("span", { className: "sub" }, progWhere(p)));
      const note = progNotes[appid + p.id];
      if (note && Date.now() - note.at < 20000) nm.appendChild(el("span", { className: "pg-note" }, note.text));
      if (p.setup) {                                       // [v5.6] установщик — «Установить в игру», один раз
        const inst = button(t("progs.install"), async () => {
          inst.disabled = true;
          progNotes[appid + p.id] = { at: Date.now(), text: t("progs.install.starting") };
          renderProgs(progs);
          const r = DEMO ? { result: "installing" } : await api().prog_install(appid, p.id);
          progNotes[appid + p.id] = { at: Date.now() + 40000, text: r.error ? errText(r.error) : t("progs.result." + (r.result || "failed")) };
          renderProgs(progs);
        }, true);
        inst.classList.add("small");
        inst.disabled = g.running || !g.installed;
        inst.title = g.running ? t("progs.install.need_closed") : !g.installed ? t("progs.install.need_installed") : t("progs.install.hint");
        row.append(nm, inst);
        box.appendChild(row);
        continue;
      }
      const auto = el("label", { className: "pg-auto", title: t("progs.auto.hint") });
      const cb = el("input", { type: "checkbox", checked: (g.auto || []).includes(p.id) });
      cb.addEventListener("change", async () => {
        g.auto = cb.checked ? [...(g.auto || []), p.id] : (g.auto || []).filter((x) => x !== p.id);
        if (!DEMO) await api().prog_auto(appid, p.id, cb.checked);
      });
      auto.append(cb, t("progs.auto"));
      const run = button(t("progs.run"), async () => {
        run.disabled = true;
        progNotes[appid + p.id] = { at: Date.now(), text: t("progs.starting") };
        renderProgs(progs);
        const r = DEMO ? { result: "ok" } : await api().prog_run(appid, p.id);
        progNotes[appid + p.id] = { at: Date.now(), text: r.error ? errText(r.error) : t("progs.result." + (r.result || "failed")) };
        renderProgs(progs);
      }, true);
      run.classList.add("small");
      run.disabled = !g.running;
      if (!g.running) run.title = t("progs.need_game");
      row.append(nm, auto, run);
      box.appendChild(row);
    }
  }
}

function progWhere(p) {
  return p.where === "game" ? t("progs.where.game") : p.where === "pfx" ? t("progs.where.pfx") : t("progs.where.folder", { folder: p.where });
}

// [v5.6] «Запуск игры»: как обычно или через программу (загрузчик модов, лаунчер) — Steam запускает её вместо .exe игры
// тем же Proton. Выбор запоминается (уходит машинам при аренде) и сразу передаётся этой машине.
function launchRow(appid, g, progs) {
  const wrap = el("div", { className: "pg-launch" });
  const cur = (g.launch && g.launch.id) || "-";
  const sel = el("select", {});
  sel.appendChild(el("option", { value: "-" }, t("progs.launch.normal")));
  const choices = (g.progs || []).filter((p) => !p.setup);
  if (cur !== "-" && !choices.some((p) => p.id === cur)) choices.push({ id: cur, name: cur.split("/").pop(), where: "" });
  const short = (p) => p.where === "pfx" ? t("progs.where.pfx_short") : p.where === "game" ? t("progs.where.game_short")
    : t("progs.where.folder", { folder: p.where });
  for (const p of choices) sel.appendChild(el("option", { value: p.id }, p.where ? `${p.name} (${short(p)})` : p.name));
  sel.value = cur;
  const args = el("input", { type: "text", placeholder: t("progs.launch.args"), value: (g.launch && g.launch.args) || "" });
  const save = button(t("progs.launch.save"), async () => {
    save.disabled = true;
    progNotes[appid + ":launch"] = { at: Date.now() + 60000, text: t("progs.launch.saving") };
    renderProgs(progs);
    const id = sel.value, a = id === "-" ? "" : args.value.trim();
    const r = DEMO ? { result: "ok" } : await api().prog_launch(appid, id, a);
    if (!r.error) g.launch = id === "-" ? null : { id, args: a };
    progNotes[appid + ":launch"] = { at: Date.now(), text: r.error ? errText(r.error) : t("progs.launch.result." + (r.result || "failed")) };
    renderProgs(progs);
  }, true);
  save.classList.add("small");
  const sync = () => { args.hidden = sel.value === "-"; save.disabled = sel.value === cur && (args.value.trim() === ((g.launch && g.launch.args) || "")); };
  sel.addEventListener("change", sync);
  args.addEventListener("input", sync);
  sync();
  const line = el("div", { className: "pg-launch-line" });
  line.append(el("span", { className: "pg-launch-label", title: t("progs.launch.hint") }, t("progs.launch")), sel, args, save);
  wrap.appendChild(line);
  const note = progNotes[appid + ":launch"];
  if (note && Date.now() - note.at < 20000) wrap.appendChild(el("div", { className: "pg-note" }, note.text));
  else wrap.appendChild(el("div", { className: "pg-hint" }, t("progs.launch.hint")));
  return wrap;
}

// ---------------------------------------------------------------- «Закончить» и аварийное удаление
$("stop").addEventListener("click", async () => {
  // Машина ждёт ответа о новой / удалённой игре — спросить ещё раз (карточку могли закрыть)
  const asks = DEMO ? (current.status.asks || []) : ((await api().machine_asks()).asks || []);
  renderAsks(asks);
  if (asks.length) {
    const answer = await ask({ title: t("ask.stop.title"),
                               text: asks.map((a) => "• " + t(`ask.${a.kind}.q`, { name: a.name })).join("\n") + "\n\n" + t("ask.stop.text"),
                               yes: t("ask.stop.answer"), no: t("ask.stop.skip") });
    if (answer) { $("asks-card").scrollIntoView({ behavior: "smooth", block: "center" }); return; }
  }
  const yes = await ask({ title: t("confirm.stop.title"), text: t("confirm.stop.text"), yes: t("confirm.stop.yes"), no: t("btn.cancel") });
  if (!yes) return;
  if (DEMO) { startProgress("stop"); return; }
  const r = await api().stop(LANG);
  if (r.error) { showError("error", r.error); return; }
  startProgress("stop");
});

$("emergency").addEventListener("click", async () => {
  const m = current && current.status.machine;
  if (!m) return;
  // Два вопроса: что пропадёт — и что это нельзя отменить
  if (!(await ask({ title: t("emergency.title1"), text: t("emergency.text1"), yes: t("btn.emergency"), no: t("btn.cancel") }))) return;
  if (!(await ask({ title: t("emergency.title2"), text: t("emergency.text2", { id: m.id }), yes: t("btn.emergency"), no: t("btn.cancel") }))) return;
  $("emergency").disabled = true;
  $("status-text").textContent = t("emergency.working");
  const r = DEMO ? { ok: true, gone: true } : await api().emergency_delete(m.id);
  $("emergency").disabled = false;
  if (r.error) { showError("error", r.error); return; }
  showError("error", { text: r.gone ? t("emergency.done", { id: m.id }) : t("core.delete_pending", { id: m.id }) });
  refresh();
});

// ---------------------------------------------------------------- настройка
let setupData = null;   // последний ответ setup_status: {steps, ready}
const SETUP_ICON = { ok: "✓", warn: "!", missing: "✗" };

$("open-setup").addEventListener("click", openSetup);
$("setup-banner-open").addEventListener("click", () => openWizard());
$("setup-recheck").addEventListener("click", openSetup);
$("setup-back").addEventListener("click", () => { show("main"); refresh(); });

async function loadSetup() {
  let d;
  try { d = DEMO ? demoSetup() : await api().setup_status(); }
  catch (e) { d = { error: { key: null, text: String(e) } }; }   // ответ не пришёл — показать, а не висеть
  if (!d.error) setupData = d;
  $("setup-banner").hidden = !setupData || setupData.ready;
  if (current) render(current);
  return d;
}

async function openSetup() {
  show("setup");
  showError("setup-error", null);
  const fresh = loadSetup();
  if (!setupData) {
    $("setup-summary").textContent = t("setup.loading");
    $("setup-steps").innerHTML = "";
    const snap = await snapshotOf("setup");
    if (!setupData && snap && Array.isArray(snap.steps)) setupData = snap;
  }
  if (setupData) { setStale("screen-setup", true); renderSetup(); }    // прошлая проверка — сразу
  const d = await fresh;
  setStale("screen-setup", false);
  if (d.error) { $("setup-summary").textContent = ""; showError("setup-error", d.error); return; }
  renderSetup();
}

function renderSetup() {
  renderSteamAccounts($("setup-steam"));
  const d = setupData;
  if (!d) return;
  $("setup-summary").textContent = t(d.ready ? "setup.ready" : "setup.not_ready");
  const ul = $("setup-steps");
  ul.innerHTML = "";
  for (const st of d.steps) {
    const li = document.createElement("li");
    li.dataset.step = st.id;
    li.innerHTML = '<span class="icon"></span><div class="body"><b></b><span class="sub"></span><div class="actions"></div></div>';
    li.querySelector(".icon").textContent = SETUP_ICON[st.state];
    li.querySelector(".icon").classList.add(st.state);
    li.querySelector("b").textContent = t("setup.step." + st.id);
    const p = { ...st.params };
    if ("lang" in p && !p.lang) p.lang = t("setup.lang_default");
    li.querySelector(".sub").textContent = t(st.key, p);
    setupActions(st, li);
    ul.appendChild(li);
  }
  // Подсказка про первое подключение Moonlight — всегда внизу
  const li = document.createElement("li");
  li.innerHTML = '<span class="icon info">i</span><div class="body"><b></b><span class="sub pre"></span></div>';
  li.querySelector("b").textContent = t("setup.pair.title");
  li.querySelector(".sub").textContent = t("setup.pair.text");
  ul.appendChild(li);
}

// Сброс vastgame на этом Mac: два вопроса — что удалится и что это не отменить
$("setup-reset").addEventListener("click", async () => {
  if (!(await ask({ title: t("setup.reset.confirm1.title"), text: t("setup.reset.confirm1.text"),
                    yes: t("setup.reset.button"), no: t("btn.cancel") }))) return;
  if (!(await ask({ title: t("setup.reset.confirm2.title"), text: t("setup.reset.confirm2.text"),
                    yes: t("setup.reset.confirm2.yes"), no: t("btn.cancel") }))) return;
  if (DEMO) return;
  const r = await api().reset_local();
  if (r.error) { showError("setup-error", r.error); return; }
  setupData = null;
  // Что-то в Связке ключей не стёрлось — сказать прямо, иначе мастер решит, что это ещё настроено
  if (r.left) { await openSetup(); showError("setup-error", { key: "setup.reset.left", params: { n: r.left } }); return; }
  openWizard();                      // теперь — как после первой установки
});

// ---- Действия шагов настройки
const DOWNLOAD = { rclone: "https://rclone.org/downloads/", tailscale_app: "https://tailscale.com/download",
                   moonlight: "https://moonlight-stream.org" };
const KEYS_URL = { vast: "https://cloud.vast.ai/manage-keys/", tailscale_key: "https://login.tailscale.com/admin/settings/keys" };
const DNS_URL = "https://login.tailscale.com/admin/dns";
const TOOL_NAME = { rclone: "rclone", tailscale_app: "Tailscale", moonlight: "Moonlight" };

function el(tag, attrs = {}, text) {
  const e = document.createElement(tag);
  Object.assign(e, attrs);
  if (text != null) e.textContent = text;
  return e;
}
function button(label, onclick, primary = false) {
  const b = el("button", { className: "small" + (primary ? " primary" : "") }, label);
  b.addEventListener("click", onclick);
  return b;
}
function openUrl(url) { if (!DEMO) api().open_url(url); }

// Сообщение под шагом (ход действия или ошибка)
function stepMsg(li, text, isError = false) {
  let m = li.querySelector(".step-msg");
  if (!m) { m = el("div", { className: "step-msg" }); li.querySelector(".body").appendChild(m); }
  m.className = "step-msg" + (isError ? " err" : "");
  m.textContent = text;
}

// Быстрое действие: вызвать, при успехе — перепроверить всё; при ошибке — показать под шагом
async function act(li, call) {
  stepMsg(li, t("setup.working"));
  const r = DEMO ? { ok: true } : await call();
  if (r && r.error) { stepMsg(li, errText(r.error), true); return; }
  openSetup();
}

// Долгое действие в фоне (установка, вход в Google): ждём конца через progress()
async function actBackground(li, start, waitText) {
  const r = DEMO ? { ok: true } : await start();
  if (r.error) { stepMsg(li, errText(r.error), true); return; }
  // Пока действие идёт, кнопки шага и его прежний текст («не установлен») только путают — прячем
  li.querySelector(".actions").hidden = true;
  const sub = li.querySelector(".sub");
  sub.hidden = true;
  stepMsg(li, waitText);
  if (DEMO) return;
  let shownKey = null;
  const timer = setInterval(async () => {
    const p = await api().progress(0);
    if (!p) return;
    if (!p.done) {
      const last = (p.events || []).filter((e) => e.key).pop();
      if (last && last.key !== shownKey) {
        shownKey = last.key;
        stepMsg(li, eventText(last));
        if (last.key === "setup.ts_login.waiting") {       // вкладка не загрузилась или закрыта — открыть снова
          const b = button(t("setup.act.ts_reopen"), () => api().tailscale_reopen_login());
          li.querySelector(".step-msg").appendChild(el("div", { className: "actions" })).appendChild(b);
        }
      }
      return;
    }
    clearInterval(timer);
    if (p.error) { li.querySelector(".actions").hidden = false; sub.hidden = false; stepMsg(li, errText(p.error), true); }
    else openSetup();
  }, 1500);
}

function field(placeholder, type = "text", value = "") {
  return el("input", { type, placeholder, value, className: "field", autocomplete: "off", spellcheck: false });
}

function setupActions(st, li) {
  const box = li.querySelector(".actions");
  const hint = (key) => li.querySelector(".body").insertBefore(el("span", { className: "sub hint" }, t(key)), box);
  const id = st.id, key = st.key;
  if (key.endsWith(".needs_vast")) return;

  if (id === "autostart" || id === "open_at_login") {
    const name = id === "autostart" ? "resume" : "app";
    const on = id === "autostart" ? st.state === "ok" : key === "setup.open_at_login.on";
    if (on) box.appendChild(button(t("setup.act.disable"), () => act(li, () => api().autostart_uninstall(name))));
    else box.appendChild(button(t("setup.act.enable"), () => act(li, () => api().autostart_install(name)), id === "autostart"));
    return;
  }

  const tsInstalled = key === "setup.tailscale_app.offline" || key === "setup.tailscale_app.no_answer";
  if (id in TOOL_NAME && st.state === "missing" && !tsInstalled) {
    if ((setupData.platform === "darwin" && (id !== "rclone" || setupData.brew)) ||
        (setupData.platform === "win32" && id !== "rclone")) {
      box.appendChild(button(t("setup.act.install"), async () => {
        const name = TOOL_NAME[id];
        if (!(await ask({ title: t("setup.confirm.install.title", { name }), text: t("setup.confirm.install.text"),
                           yes: t("setup.act.install"), no: t("btn.cancel") }))) return;
        actBackground(li, () => api().install_tool(id), t("setup.working"));
      }, true));
    }
    box.appendChild(button(t("setup.act.download"), () => openUrl(DOWNLOAD[id])));
  }
  if (key === "setup.tailscale_app.offline")
    box.appendChild(button(t("setup.act.ts_login"), () => actBackground(li, () => api().tailscale_login(), t("setup.ts_login.waiting")), true));
  if (tsInstalled) box.appendChild(button(t("setup.act.open_tailscale"), () => !DEMO && api().open_tailscale(),
                                          key === "setup.tailscale_app.no_answer"));
  if (key === "setup.tailscale_app.no_magicdns") box.appendChild(button(t("setup.act.open_dns"), () => openUrl(DNS_URL)));

  if (id === "vast" && st.state === "missing") {
    hint("setup.hint.vast");
    const f = field(t("setup.field.vast_key"), "password");
    box.append(f, button(t("setup.act.save"), () => act(li, () => api().save_vast_key(f.value)), true),
               button(t("setup.act.keys_page"), () => openUrl(KEYS_URL.vast)));
  }
  if (key === "setup.sshkey.missing")
    box.appendChild(button(t("setup.act.add_sshkey"), () => act(li, () => api().add_ssh_key()), true));
  if (key === "setup.vast.file") {
    box.appendChild(button(t("setup.act.move_key"), async () => {
      if (await ask({ title: t("setup.confirm.move.title"), text: t("setup.confirm.move.text") })) act(li, () => api().move_vast_key());
    }, true));
  }

  if (key === "setup.google.no_client") {
    hint("setup.hint.client");
    const a = field(t("setup.field.client_id")), b = field(t("setup.field.client_secret"), "password");
    box.append(a, b, button(t("setup.act.save"), () => act(li, () => api().save_google_client(a.value, b.value)), true));
  } else if (["setup.google.missing", "setup.google.bad", "setup.google.old"].includes(key)) {
    hint("setup.hint.google");
    box.appendChild(button(t("setup.act.login"), () => actBackground(li, () => api().google_login(), t("setup.login_wait")), true));
  } else if (key === "setup.google.not_in_vast") {
    box.appendChild(button(t("setup.act.send_vast"), () => act(li, () => api().send_google()), true));
  } else if (key === "setup.google.ok") {
    box.appendChild(button(t("setup.act.relogin"), () => actBackground(li, () => api().google_login(), t("setup.login_wait"))));
  }

  if (id === "tailscale_key" && (st.state === "missing" || key === "setup.tailscale_key.expiring"
                                 || key === "setup.tailscale_key.optional")) {
    hint("setup.hint.ts");
    const f = field(t("setup.field.ts_key"), "password");
    box.append(f, button(t("setup.act.save"), () => act(li, () => api().save_tailscale_key(f.value)), true),
               button(t("setup.act.keys_page"), () => openUrl(KEYS_URL.tailscale_key)));
  }

  if (id === "template") {
    const form = () => {
      box.innerHTML = "";
      const p = st.params || {};
      const disk = field(t("setup.field.disk"), "number", p.disk || 100);
      const lang = field(t("setup.field.lang"), "text", p.lang || "");
      const res = field(t("setup.field.res"), "text", p.res || "1920x1200");
      box.append(disk, lang, res, button(t(key === "setup.template.missing" ? "setup.act.create_template" : "setup.act.save"),
        () => act(li, () => api().save_template(disk.value, lang.value, res.value)), true));
    };
    if (key === "setup.template.missing") form();
    else if (key === "setup.template.outdated") box.appendChild(button(t("setup.act.update_template"), () => act(li, () => api().refresh_template()), true));
    else box.appendChild(button(t("setup.act.edit"), form));
  }
}

// ---------------------------------------------------------------- простая настройка
// Один экран — одно действие. Что уже готово (проверяет Python: wizard_state) — пропускаем, после
// каждого действия сами идём дальше. Порядок: программы → Vast → Google → Tailscale (самый трудный —
// последним: токен Google уходит в аккаунт Vast, значит Vast раньше) → остальное само → «Всё готово».
const WIZ_STEPS = ["programs", "vast", "google", "tailscale", "finish"];
const VAST_BILLING = "https://cloud.vast.ai/billing/";
let wiz = null;          // {step, state, gen, auto: {что уже сделали сами один раз}, timer}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function wizAlive(g) { return wiz && wiz.gen === g && !$("screen-wizard").hidden; }
function wizStop() { if (wiz && wiz.timer) clearTimeout(wiz.timer); }

function wizIsDone(step, s) {
  if (step === "programs") return s.programs.tailscale && s.programs.moonlight;
  if (step === "vast") return s.vast.ok;
  if (step === "google") return s.google.ok;
  if (step === "tailscale") return s.tailscale.phase === "ready";
  return false;
}
function wizNext(after) {
  const rest = WIZ_STEPS.slice(WIZ_STEPS.indexOf(after) + 1);
  return rest.find((k) => !wizIsDone(k, wiz.state)) || "finish";
}
async function wizRefreshState() {
  const s = DEMO ? demoWizState() : await api().wizard_state();
  if (s && !s.error) wiz.state = s;
  return s;
}

async function openWizard() {
  wizStop();
  show("wizard");
  wiz = { step: null, state: null, gen: (wiz ? wiz.gen : 0) + 1, auto: {} };
  const g = wiz.gen;
  wizScreen({ title: t("wizard.checking") });
  const s = await wizRefreshState();
  if (!wizAlive(g)) return;
  if (s.error) { wizScreen({ title: t("wizard.error.title"), status: errText(s.error), err: true, retry: openWizard }); return; }
  // Совсем с нуля — сначала приветствие; иначе сразу к первому неготовому шагу
  const fresh = !s.programs.tailscale && !s.programs.moonlight && !s.vast.ok;
  goWiz(fresh ? "welcome" : WIZ_STEPS.find((k) => !wizIsDone(k, s)));
}

function goWiz(step) {
  wizStop();
  wiz.step = step;
  wiz.gen++;
  ({ welcome: wizWelcome, programs: wizPrograms, vast: wizVast, google: wizGoogle,
     tailscale: wizTailscale, finish: wizFinish, done: wizFinished })[step]();
}

// Экран шага: заголовок, текст, элементы, строка состояния, кнопка «Дальше» (или «Повторить»)
function wizScreen({ title, text = "", body = [], status = "", err = false, ok = false, next = null, retry = null }) {
  const steps = $("wiz-steps");
  steps.innerHTML = "";
  steps.hidden = !wiz || !wiz.step || wiz.step === "welcome";
  for (const k of WIZ_STEPS) {
    const done = wiz && wiz.state && (wiz.step === "done" || (k !== "finish" && wizIsDone(k, wiz.state)));
    steps.appendChild(el("li", { className: k === (wiz && wiz.step) ? "current" : done ? "done" : "" }, t("wizard.step." + k)));
  }
  $("wiz-title").textContent = title;
  $("wiz-text").textContent = text;
  $("wiz-body").replaceChildren(...body);
  wizStatus(status, err, ok);
  const nb = $("wiz-next");
  const action = next || (retry && { label: t("wizard.retry"), onclick: retry });
  nb.hidden = !action;
  if (action) { nb.textContent = action.label; nb.onclick = action.onclick; nb.disabled = false; }
}
function wizStatus(text, err = false, ok = false) {
  const st = $("wiz-status");
  st.hidden = !text;
  st.textContent = text || "";
  st.className = "wiz-status" + (err ? " err" : ok ? " ok" : "");
}
function wizRow(...items) { const r = el("div", { className: "row" }); r.append(...items); return r; }
function bigButton(label, onclick, primary = true) { const b = button(label, onclick, primary); b.classList.add("big"); return b; }

// Дождаться конца короткой операции окна (установка, вход) — события отдаём onEvent
async function waitJob(onEvent) {
  if (DEMO) { await sleep(1500); return { done: true }; }
  let seen = 0;
  for (;;) {
    await sleep(1000);
    const p = await api().progress(0);
    if (!p) continue;
    const evs = p.events || [];
    if (onEvent) for (const e of evs.slice(seen)) onEvent(e);
    seen = evs.length;
    if (p.done) return p;
  }
}

function wizWelcome() {
  wizScreen({ title: t("wizard.welcome.title"), text: t("wizard.welcome.text"),
              next: { label: t("wizard.start"), onclick: () => goWiz(WIZ_STEPS.find((k) => !wizIsDone(k, wiz.state))) } });
}

function wizPrograms() {
  const g = wiz.gen, s = wiz.state;
  const list = el("ul", { className: "wiz-check" });
  for (const [name, ok] of [["Tailscale", s.programs.tailscale], ["Moonlight", s.programs.moonlight]])
    list.appendChild(el("li", { className: ok ? "done" : "" }, t("wizard.programs.item." + name.toLowerCase())));
  if (s.platform !== "darwin" && s.platform !== "win32") {   // сам ставит на Mac и Windows — остальным ссылки
    wizScreen({ title: t("wizard.programs.title"), text: t("wizard.programs.text_other"), body: [list,
      wizRow(button(t("setup.act.download") + " Tailscale", () => openUrl(DOWNLOAD.tailscale_app)),
             button(t("setup.act.download") + " Moonlight", () => openUrl(DOWNLOAD.moonlight)))],
      retry: async () => { await wizRefreshState(); goWiz(wizIsDone("programs", wiz.state) ? wizNext("programs") : "programs"); } });
    return;
  }
  const install = bigButton(t("wizard.programs.install"), async () => {
    install.disabled = true;
    const r = DEMO ? { ok: true } : await api().install_programs();
    if (r.error) { wizStatus(errText(r.error), true); install.disabled = false; return; }
    wizStatus(t("wizard.programs.working"));
    const p = await waitJob((e) => e.key && wizAlive(g) && wizStatus(eventText(e)));
    if (!wizAlive(g)) return;
    if (p.error) { wizStatus(errText(p.error), true); install.disabled = false; return; }
    demoWizMark("programs");
    await wizRefreshState();
    if (wizAlive(g)) goWiz(wizIsDone("programs", wiz.state) ? wizNext("programs") : "programs");
  });
  wizScreen({ title: t("wizard.programs.title"), text: t("wizard.programs.text"), body: [list, install] });
}

function wizVast(replace = false) {
  const g = wiz.gen, s = wiz.state.vast;
  if (s.ok && !replace) {
    const low = s.balance != null && s.balance < 1;
    const body = [];
    if (low) body.push(button(t("wizard.vast.topup"), () => openUrl(VAST_BILLING), true));
    body.push(button(t("wizard.vast.replace"), () => wizVast(true)));
    // Итог — текстом сверху, кнопки («пополнить», «другой ключ») — под ним
    wizScreen({ title: t("wizard.vast.title"), body,
                text: t("wizard.vast.ok", { balance: money(s.balance || 0) }) + (low ? "\n\n" + t("wizard.vast.low") : ""),
                next: { label: t("wizard.next"), onclick: () => goWiz(wizNext("vast")) } });
    return;
  }
  const f = field(t("setup.field.vast_key"), "password");
  const check = button(t("wizard.vast.check"), async () => {
    if (!f.value.trim()) { f.focus(); return; }
    check.disabled = true;
    wizStatus(t("setup.working"));
    const r = DEMO ? { ok: true, result: 7.5 } : await api().save_vast_key(f.value);
    if (!wizAlive(g)) return;
    check.disabled = false;
    if (r.error) { wizStatus(errText(r.error), true); return; }
    wiz.state.vast = { ok: true, balance: r.result };
    demoWizMark("vast");
    wizVast();
    if (r.result >= 1) { await sleep(1500); if (wizAlive(g)) goWiz(wizNext("vast")); }   // денег хватает — сразу дальше
  }, true);
  f.addEventListener("keydown", (e) => { if (e.key === "Enter") check.click(); });
  wizScreen({ title: t("wizard.vast.title"), text: t("wizard.vast.text"),
              body: [bigButton(t("wizard.vast.open"), () => openUrl(KEYS_URL.vast), false), wizRow(f, check)] });
  f.focus();
}

function wizGoogle() {
  const g = wiz.gen, s = wiz.state.google;
  const login = bigButton(t(s.ok ? "wizard.google.relogin" : "wizard.google.login"), async () => {
    login.disabled = true;
    const r = DEMO ? { ok: true } : await api().google_login();
    if (r.error) { wizStatus(errText(r.error), true); login.disabled = false; return; }
    wizStatus(t("wizard.google.waiting"));
    const p = await waitJob();
    if (!wizAlive(g)) return;
    if (p.error) { wizStatus(errText(p.error), true); login.disabled = false; return; }
    demoWizMark("google");
    await wizRefreshState();
    if (wizAlive(g)) goWiz(wizIsDone("google", wiz.state) ? wizNext("google") : "google");
  }, !s.ok);
  if (s.ok) login.classList.remove("big");        // уже вошли — «другим аккаунтом» не главное действие
  wizScreen({ title: t("wizard.google.title"), body: [login],
              text: s.ok ? t("wizard.google.ok", { email: s.email || "" }) : t("wizard.google.text"),
              next: s.ok ? { label: t("wizard.next"), onclick: () => goWiz(wizNext("google")) } : null });
}

// Tailscale: раз в 2 секунды спрашиваем, на каком он этапе, и показываем ровно одно действие.
// Что можно сделать за человека — делаем сами один раз (открыть Tailscale, настройки, страницу входа)
function wizTailscale() {
  const g = wiz.gen;
  let last = null;
  wizScreen({ title: t("wizard.ts.title"), text: t("wizard.ts.intro") });
  const tick = async () => {
    if (!wizAlive(g)) return;
    const p = DEMO ? demoTsPhase() : await api().tailscale_phase();
    if (!wizAlive(g)) return;
    if (p && !p.error && p.phase !== last) { last = p.phase; wizTsPhase(p, g); }
    if (p && p.phase === "ready") {
      wiz.state.tailscale = p;
      await sleep(1200);
      if (wizAlive(g)) goWiz(wizNext("tailscale"));
      return;
    }
    wiz.timer = setTimeout(tick, 2000);
  };
  tick();
}

function wizTsPhase(p, g) {
  const once = (what, fn) => { if (!wiz.auto[what]) { wiz.auto[what] = true; if (!DEMO) fn(); } };
  const openTs = () => { if (!DEMO) api().open_tailscale(); };
  let body = [], status = "";
  switch (p.phase) {
    case "missing":
      body = [bigButton(t("wizard.programs.install"), async (e) => {
        e.target.disabled = true;
        const r = DEMO ? { ok: true } : await api().install_programs();
        if (r.error) { wizStatus(errText(r.error), true); e.target.disabled = false; return; }
        const j = await waitJob((ev) => ev.key && wizAlive(g) && wizStatus(eventText(ev)));
        if (j.error && wizAlive(g)) { wizStatus(errText(j.error), true); e.target.disabled = false; }
      })];
      break;
    case "start":
      once("open", openTs);                        // установлен, но не запущен — открыть самим
      body = [button(t("wizard.ts.open"), openTs)];
      status = t("wizard.ts.waiting");
      break;
    case "extension":
      once("settings", () => api().open_extension_settings());
      body = [bigButton(t("wizard.ts.settings"), () => !DEMO && api().open_extension_settings())];
      break;
    case "vpn":
      body = [button(t("wizard.ts.show_again"), openTs)];
      break;
    case "login":
      once("login", () => api().tailscale_login());   // ссылку входа Python откроет в браузере сам
      body = [button(t("wizard.ts.login_again"), async () => {
        const r = DEMO ? { ok: true } : await api().tailscale_reopen_login();
        if (r && r.error && !DEMO) api().tailscale_login();     // прошлый вход закончился — начать заново
      })];
      break;
    case "stopped":
      body = [bigButton(t("wizard.ts.turn_on"), async () => {
        const r = DEMO ? { ok: true } : await api().tailscale_up();
        if (r.error) wizStatus(errText(r.error), true);
      })];
      break;
    case "magicdns":
      body = [bigButton(t("wizard.ts.dns"), () => openUrl(DNS_URL))];
      break;
    case "ready":
      status = t("wizard.ts.ready");
      break;
  }
  wizScreen({ title: t("wizard.ts.title"), text: t("wizard.ts." + p.phase), body, status, ok: p.phase === "ready" });
}

async function wizFinish() {
  const g = wiz.gen;
  const items = ["template", "sshkey", "google", "autostart"];
  const list = el("ul", { className: "wiz-check" });
  for (const k of items) list.appendChild(el("li", {}, t("wizard.finish.item." + k)));
  const mark = (upto) => [...list.children].forEach((li, i) => { li.className = i < upto ? "done" : i === upto ? "active" : ""; });
  wizScreen({ title: t("wizard.finish.title"), text: t("wizard.finish.text"), body: [list] });
  const r = DEMO ? { ok: true } : await api().wizard_finish();
  if (!wizAlive(g)) return;
  if (r.error) { wizScreen({ title: t("wizard.finish.title"), body: [list], status: errText(r.error), err: true, retry: () => goWiz("finish") }); return; }
  mark(0);
  const p = await waitJob((e) => { if (wizAlive(g) && e.key && e.key.startsWith("wizard.finish.")) mark(items.indexOf(e.key.slice(14))); });
  if (!wizAlive(g)) return;
  if (p.error) { wizStatus(errText(p.error), true); $("wiz-next").hidden = false; $("wiz-next").textContent = t("wizard.retry"); $("wiz-next").onclick = () => goWiz("finish"); return; }
  mark(items.length);
  await sleep(700);
  if (wizAlive(g)) goWiz("done");
}

function wizFinished() {
  wizScreen({ title: t("wizard.done.title"), text: t("wizard.done.text"),
    next: { label: t("wizard.done.play"), onclick: async () => {
      $("wiz-next").disabled = true;
      if (!DEMO) { await loadSetup(); await refresh(); }
      openOffers();
    } } });
}

$("wiz-advanced").addEventListener("click", () => { wizStop(); openSetup(); });
$("setup-wizard").addEventListener("click", openWizard);

// ---------------------------------------------------------------- библиотека Steam
// Игры всех привязанных аккаунтов Steam — прямо из Steam (машина не нужна). Сначала прошлый список (сразу),
// потом свежий; при возврате в окно — снова свежий (купил игру на сайте — она уже тут).
let libData = null, libFilter = "all", libLoading = false, libAt = 0;
const LIB_STALE_MS = 30000;   // при возврате в окно — обновлять, если список старше

$("open-library").addEventListener("click", openLibrary);
$("library-back").addEventListener("click", () => { show("main"); refresh(); renderMainLibrary(true); });
$("library-refresh").addEventListener("click", () => loadLibrary(true));
$("library-login").addEventListener("click", () => steamLogin($("library-login-msg")));
$("library-search").addEventListener("input", renderLibrary);
window.addEventListener("focus", () => {
  if (!$("screen-library").hidden && libData && libData.accounts.length && Date.now() - libAt > LIB_STALE_MS) loadLibrary(true);
});

async function openLibrary() {
  const login = !$("screen-main").hidden && libData && !libData.accounts.length;
  show("library");
  showError("library-error", null);
  if (!libData) $("library-summary").textContent = t("library.loading");
  await loadLibrary(false);
  if (libData && libData.accounts.length) loadLibrary(true);
  else if (login) steamLogin($("library-login-msg"));
}

async function loadLibrary(fresh) {
  if (libLoading && fresh) return;
  libLoading = libLoading || fresh;
  $("library-refresh").disabled = libLoading;
  try {
    const d = DEMO ? demoLibrary() : await api().library(fresh);
    if (d.error) { showError("library-error", d.error); return; }
    libData = d;
    if (fresh) libAt = Date.now();
    renderLibrary();
  } finally {
    if (fresh) libLoading = false;
    $("library-refresh").disabled = libLoading;
  }
}

function accName(id) {
  const a = ((libData && libData.accounts) || []).find((x) => x.id === id);
  return a ? a.name : "…" + id.slice(-4);
}

function renderLibrary() {
  const d = libData;
  if (!d) return;
  const none = !d.accounts.length;
  $("library-empty").hidden = !none;
  for (const id of ["library-search", "library-refresh", "library-soon"]) $(id).hidden = none;
  $("library-chips").innerHTML = "";
  $("library-grid").innerHTML = "";
  if (none) { $("library-summary").textContent = ""; return; }
  const games = d.games || [];
  const when = d.at ? new Date(d.at * 1000).toLocaleTimeString(LANG, { hour: "2-digit", minute: "2-digit" }) : "";
  $("library-summary").textContent = t("library.summary", { n: games.length, accounts: d.accounts.length })
    + (when ? " · " + t("library.updated", { time: when }) : "");
  // Ярлыки: все, по аккаунтам (истёкший — «войди заново»), семья, «+ Войти в Steam»
  const chips = $("library-chips");
  const chip = (label, key, cls = "") => {
    const b = el("button", { className: "chip " + cls + (libFilter === key ? " on" : "") }, label);
    b.addEventListener("click", () => { libFilter = key; renderLibrary(); });
    chips.appendChild(b);
  };
  chip(t("library.all", { n: games.length }), "all");
  for (const a of d.accounts) {
    if (a.expired) {
      const b = el("button", { className: "chip warn" }, "⚠ " + t("steam.expired_chip", { name: a.name }));
      b.addEventListener("click", () => steamLogin(null));
      chips.appendChild(b);
    } else {
      chip(`${a.name} · ${games.filter((g) => g.accounts.includes(a.id)).length}`, a.id);
    }
  }
  const fam = games.filter((g) => !g.accounts.length && g.family.length).length;
  if (fam) chip(t("library.family_chip", { n: fam }), "family");
  const more = el("button", { className: "chip" }, t("steam.login_more"));
  more.addEventListener("click", () => steamLogin(null));
  chips.appendChild(more);
  // Плитки
  const q = $("library-search").value.trim().toLowerCase();
  const shown = games.filter((g) =>
    (libFilter === "all" || (libFilter === "family" ? !g.accounts.length && g.family.length : g.accounts.includes(libFilter)))
    && (!q || (g.name || "").toLowerCase().includes(q)));
  const grid = $("library-grid");
  if (!shown.length) { grid.appendChild(el("div", { className: "note" }, t("library.nothing"))); return; }
  for (const g of shown) {
    const tile = gameTile(g);
    tile.addEventListener("click", () => startGame(g));
    grid.appendChild(tile);
  }
}

function gameTile(g) {
  const tile = el("div", { className: "game-tile", title: g.name || g.appid });
  tile.appendChild(el("div", { className: "ph" }, g.name || "#" + g.appid));
  // Обложка 600x900; нет её — широкая картинка (вписана), нет и её — градиент с названием
  const pics = [[g.cap, ""], [g.header, "wide"]].filter(([u]) => u);
  const tryPic = (i) => {
    if (i >= pics.length) return;
    const img = el("img", { alt: "", loading: "lazy", src: pics[i][0], className: pics[i][1] });
    img.onerror = () => { img.remove(); tryPic(i + 1); };
    tile.appendChild(img);
  };
  tryPic(0);
  const badges = el("div", { className: "badges" });
  for (const id of g.accounts) badges.appendChild(el("span", { className: "badge" }, accName(id)));
  if (!g.accounts.length && g.family.length) badges.appendChild(el("span", { className: "badge family" }, t("library.family")));
  if (libData && libData.mods && libData.mods[g.appid]) badges.appendChild(el("span", { className: "badge mods" }, t("library.mods")));
  if (g.demo) badges.appendChild(el("span", { className: "badge demo" }, t("library.demo")));
  tile.appendChild(badges);
  tile.appendChild(el("div", { className: "name" }, g.name || "#" + g.appid));
  return tile;
}

// Карточка «Твои игры» на главном: обложки последних игр из запомненного списка (Steam не спрашиваем — это делает
// сама библиотека); Steam не привязан — приглашение войти. Обновляется не чаще раза в минуту и после библиотеки.
const LIB_STRIP = 7;
let libCardAt = 0;
$("lib-card").addEventListener("click", (e) => {
  if (e.target.closest("button") && e.target.id !== "lib-card-open") return;
  openLibrary();
});
async function renderMainLibrary(force = false) {
  if (!force && Date.now() - libCardAt < 60000) return;
  libCardAt = Date.now();
  let d = libData;
  if (!d) {
    try { d = DEMO ? demoLibrary() : await api().library(false); } catch (e) { return; }
    if (!d || d.error) return;
    if (!libData) libData = d;
  }
  const card = $("lib-card"), strip = $("lib-strip");
  card.hidden = false;
  strip.innerHTML = "";
  const none = !d.accounts.length;
  $("lib-card-open").textContent = none ? t("steam.login") : t("library.main.all");
  $("lib-card-sub").textContent = none ? t("library.main.login")
    : d.games.length ? t("library.main.count", { n: d.games.length }) : t("library.main.empty");
  strip.hidden = none || !d.games.length;
  for (const g of d.games.slice(0, LIB_STRIP)) {
    const tile = gameTile(g);
    tile.addEventListener("click", (e) => { e.stopPropagation(); startGame(g); });
    strip.appendChild(tile);
  }
}

// Войти в Steam: окно со страницей самой Steam (QR или логин с паролем). msgEl — где писать ход (может не быть)
async function steamLogin(msgEl) {
  const say = (text) => { if (msgEl) { msgEl.hidden = !text; msgEl.textContent = text || ""; } };
  say(t("steam.login.wait"));
  showError("library-error", null);
  const r = DEMO ? { ok: true, account: { id: "76561198000000003", name: "Demo" } } : await api().steam_login(t("steam.login.window"));
  if (r.error) { say(""); showError(!$("screen-setup").hidden ? "setup-error" : "library-error", r.error); return; }
  say(r.ok ? t("steam.login.done", { name: r.account.name }) : "");
  if (r.ok) {
    if (!$("screen-setup").hidden) renderSteamAccounts($("setup-steam"));
    if (!$("screen-library").hidden) { libFilter = "all"; await loadLibrary(false); }   // игры нового аккаунта уже в списке (steam_login)
  }
}

// Список аккаунтов с «Выйти» и кнопка «Войти в Steam» (экран «Настройка»)
async function renderSteamAccounts(box) {
  const d = DEMO ? { accounts: demoLibrary().accounts } : await api().steam_accounts();
  box.innerHTML = "";
  const msg = el("div", { className: "sub" });
  msg.hidden = true;
  if (!d.accounts || !d.accounts.length) box.appendChild(el("div", { className: "sub" }, t("steam.accounts.none")));
  for (const a of d.accounts || []) {
    const row = el("div", { className: "steam-acc" });
    const left = el("div");
    left.appendChild(el("div", {}, a.name));
    const date = a.until ? new Date(a.until * 1000).toLocaleDateString(LANG, { day: "numeric", month: "long", year: "numeric" }) : "";
    left.appendChild(el("div", { className: "sub" + (a.expired ? " warn" : "") }, a.expired ? t("steam.expired") : date ? t("steam.expires", { date }) : ""));
    row.appendChild(left);
    row.appendChild(a.expired ? button(t("steam.login"), () => steamLogin(msg), true) : button(t("steam.logout"), async () => {
      if (!await ask({ title: t("steam.logout.confirm.title", { name: a.name }), text: t("steam.logout.confirm.text"), yes: t("steam.logout"), no: t("btn.cancel") })) return;
      const r = DEMO ? { ok: true } : await api().steam_logout(a.id);
      if (r.error) { showError("setup-error", r.error); return; }
      libData = null; libCardAt = 0;
      renderSteamAccounts(box);
    }));
    box.appendChild(row);
  }
  const add = button(d.accounts && d.accounts.length ? t("steam.login_more") : t("steam.login"), () => steamLogin(msg), !(d.accounts && d.accounts.length));
  add.style.alignSelf = "flex-start";
  box.appendChild(add);
  box.appendChild(msg);
}

// ---------------------------------------------------------------- история и траты
let historyData = null, historyDays = 30, historyAll = false, historyReq = 0;
$("open-history").addEventListener("click", () => openHistory(historyDays));
$("history-back").addEventListener("click", () => { show("main"); refresh(); });
$("history-other-toggle").addEventListener("click", (e) => { e.preventDefault(); historyAll = !historyAll; openHistory(historyDays); });

async function openHistory(days) {
  show("history");
  historyDays = days;
  const tabs = $("history-tabs");
  tabs.innerHTML = "";
  for (const n of [7, 30, 90]) {
    const b = button(t("history.days", { n }), () => openHistory(n));
    if (n === days) b.classList.add("active");
    tabs.appendChild(b);
  }
  $("history-summary").hidden = false;
  $("history-summary").textContent = t("history.loading");
  $("history-stats").hidden = $("history-card").hidden = true;
  $("history-breakdown").textContent = "";
  $("history-other").hidden = true;
  showError("history-error", null);
  const all = historyAll;
  const my = ++historyReq;
  setStale("screen-history", false);
  const fresh = DEMO ? null : api().history(days, all);
  const snap = await snapshotOf(`history:${days}:${all ? "all" : "game"}`);
  if (my === historyReq && snap && snap.totals) {
    historyData = snap;
    setStale("screen-history", true);
    renderHistory();
  }
  const d = DEMO ? demoHistory(days, all) : await fresh;
  if (my !== historyReq) return;   // пока считали, выбрали другой период
  setStale("screen-history", false);
  if (d.error) {
    $("history-summary").hidden = true;
    $("history-stats").hidden = $("history-card").hidden = true;    // прошлые цифры не оставлять
    $("history-other").hidden = true;
    showError("history-error", d.error);
    return;
  }
  historyData = d;
  renderHistory();
}

function renderHistory() {
  const d = historyData;
  if (!d) return;
  const tt = d.totals;
  // Реальный период — от первой сессии до сегодня (выбранные «30 дней» могут начинаться раньше неё)
  const day = (ts) => new Date(ts * 1000).toLocaleDateString(LANG, { day: "numeric", month: "short" });
  $("history-summary").hidden = false;
  $("history-summary").textContent = tt.count ? t("history.range", { from: day(d.first), to: day(d.now) }) : t("history.empty");
  $("history-stats").hidden = !tt.count;
  $("h-spent").textContent = money(tt.amount);
  $("h-hours").textContent = num(tt.hours);
  $("h-count").textContent = tt.count;
  $("h-per-hour").textContent = tt.per_hour != null ? money(tt.per_hour) : "—";
  $("history-breakdown").textContent = tt.count ? t("history.breakdown", { gpu: money(tt.gpu), disk: money(tt.disk), traffic: money(tt.traffic) }) : "";
  $("history-card").hidden = !tt.count;
  const body = $("history-table").querySelector("tbody");
  body.innerHTML = "";
  for (const x of d.sessions) {
    const tr = document.createElement("tr");
    tr.style.cursor = "default";
    tr.innerHTML = '<td></td><td></td><td class="num"></td><td class="num"></td>';
    const c = tr.children;
    c[0].textContent = x.start ? new Date(x.start * 1000).toLocaleDateString(LANG, { day: "numeric", month: "short" }) : "—";
    c[1].textContent = x.gpu ? `${x.gpu} · ${x.country || "?"}` : t("history.unknown_machine", { id: x.iid });
    c[2].textContent = num(x.hours);
    c[3].textContent = money(x.amount);
    body.appendChild(tr);
  }
  // Неигровые машины аккаунта (без метки vastgame) по умолчанию не смешиваются с играми — но и не прячутся молча
  const o = d.other || { count: 0 };
  $("history-other").hidden = !o.count && !d.all;
  $("history-other").firstElementChild.textContent = d.all ? t("history.all_shown") : t("history.other", { count: o.count, amount: money(o.amount) });
  $("history-other-toggle").textContent = t(d.all ? "history.other_hide" : "history.other_show");
}

// ---------------------------------------------------------------- игры в облаке
let gamesData = null, gamesReq = 0;
let gamesTimer = null;

$("open-games").addEventListener("click", openGames);
$("games-back").addEventListener("click", () => { clearInterval(gamesTimer); show("main"); refresh(); });
$("games-ts-open").addEventListener("click", () => { if (gamesData && gamesData.tsUrl && !DEMO) api().open_url(gamesData.tsUrl); });

async function openGames() {
  show("games");
  gamesData = null;
  $("games-card").hidden = true;
  $("games-service").textContent = "";
  $("games-summary").textContent = t("games.loading");
  showError("games-error", null);
  const my = ++gamesReq;
  setStale("screen-games", false);
  const fresh = DEMO ? null : api().games();
  const snap = await snapshotOf("games");
  if (my === gamesReq && !gamesData && snap && Array.isArray(snap.games)) {
    gamesData = snap;
    setStale("screen-games", true);          // прошлый список: «Убрать» не нажимается, пока не придёт свежий
    renderGames();
  }
  const d = DEMO ? demoGames() : await fresh;
  if (my !== gamesReq) return;
  setStale("screen-games", false);
  if (d.error) { gamesData = null; $("games-card").hidden = true; $("games-summary").textContent = ""; showError("games-error", d.error); return; }
  gamesData = d;
  renderGames();
}

// Номер игры не из Steam, добавленной в Steam ярлыком, — большой (старший бит установлен)
const SHORTCUT_APPID = 2147483648;
function gameTitle(g) {
  if (g.kind === "mod" || g.kind === "lgame") return g.title || `appid ${g.appid}`;
  if (g.kind !== "pfx") return g.title;
  if (g.title) return `${g.title} (appid ${g.appid})`;
  return Number(g.appid) >= SHORTCUT_APPID ? t("games.pfx_shortcut", { appid: g.appid }) : `appid ${g.appid}`;
}

$("games-search").addEventListener("input", renderGames);

let gamesSystemOpen = false;   // группа «Системные файлы Steam» раскрыта
function renderGames() {
  const d = gamesData;
  if (!d) return;
  const total = d.games.reduce((a, g) => a + g.bytes, 0);
  $("games-summary").textContent = d.games.length ? t("games.total", { size: size(total), count: d.games.length }) : t("games.empty");
  $("games-service").textContent = d.service.names.length ? t("games.service", { size: size(d.service.bytes) }) : "";
  $("games-card").hidden = !d.games.length;
  $("games-search").hidden = d.games.length < 8;          // поиск нужен, когда игр много
  const q = $("games-search").hidden ? "" : $("games-search").value.trim().toLowerCase();
  // Служебное Steam (Proton, Runtime…) — отдельной свёрнутой группой в конце: нужно для запуска игр
  const order = { sgame: 0, game: 1, lgame: 2, pfx: 3, mod: 4, prog: 5, system: 6 };   // каждый вид — иначе NaN и группы рвутся
  const grp = (g) => (g.system ? "system" : g.kind);
  const shown = d.games
    .filter((g) => !q || gameTitle(g).toLowerCase().includes(q) || g.name.toLowerCase().includes(q))
    .sort((a, b) => order[grp(a)] - order[grp(b)] || b.bytes - a.bytes);
  const body = $("games-table").querySelector("tbody");
  body.innerHTML = "";
  if (!shown.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="5" class="sub"></td>';
    tr.firstChild.textContent = t("games.nothing_found");
    body.appendChild(tr);
  }
  let group = null;
  for (const g of shown) {
    if (grp(g) !== group) {                                  // заголовок группы: тип, сколько, сколько места
      group = grp(g);
      const same = shown.filter((x) => grp(x) === group);
      const tr = document.createElement("tr");
      tr.className = "group";
      tr.innerHTML = '<td colspan="5"></td>';
      tr.firstChild.textContent = t("games.group", { kind: t("games.kind." + group), count: same.length,
        size: size(same.reduce((a, x) => a + x.bytes, 0)) });
      if (group === "system") {                              // свёрнута, пока не раскроют (или не ищут)
        tr.classList.add("system");
        tr.firstChild.textContent = (gamesSystemOpen || q ? "▾ " : "▸ ") + tr.firstChild.textContent + " — " + t("games.system.hint");
        tr.style.cursor = "pointer";
        tr.addEventListener("click", () => { gamesSystemOpen = !gamesSystemOpen; renderGames(); });
      }
      body.appendChild(tr);
    }
    if (g.system && !gamesSystemOpen && !q) continue;
    const tr = document.createElement("tr");
    tr.style.cursor = "default";
    tr.innerHTML = '<td><span class="kind"></span></td><td></td><td class="num"></td><td class="num"></td><td class="act"><button></button></td>';
    const c = tr.children;
    c[0].firstChild.textContent = t("games.kind." + (g.system ? "system" : g.kind));
    c[0].firstChild.classList.add(g.system ? "system" : g.kind);
    c[1].textContent = gameTitle(g);
    c[2].textContent = size(g.bytes);
    c[3].textContent = g.mtime;
    const b = c[4].firstChild;
    b.textContent = t("games.remove");
    b.disabled = !!d.removing;
    b.addEventListener("click", () => removeGame(g));
    body.appendChild(tr);
  }
}

async function removeGame(g) {
  let text = g.kind === "mod" ? t("games.confirm.mod", { title: gameTitle(g), size: size(g.bytes) })
    : t("games.confirm.text", { kind: t("games.kind." + g.kind), title: gameTitle(g), size: size(g.bytes) });
  if (g.system) text = t("games.confirm.system") + "\n\n" + text;
  else if (g.kind === "pfx") text = t("games.confirm.saves") + "\n\n" + text;
  if (!(await ask({ title: t("games.confirm.title"), text, yes: t("games.remove"), no: t("btn.cancel") }))) return;
  showError("games-error", null);
  const r = DEMO ? { ok: true } : await api().games_remove(g.name);
  if (r.error) { showError("games-error", r.error); return; }
  gamesData.removing = g.name;
  renderGames();
  $("games-status").hidden = false;
  $("games-status").textContent = t("games.removing", { title: gameTitle(g) });
  clearInterval(gamesTimer);
  gamesTimer = setInterval(async () => {
    const p = DEMO ? { events: [], done: true, result: { how: "drive" } } : await api().progress(0);
    if (!p) return;
    const ts = p.events.filter((e) => e.level === "tailscale" && e.url).pop();
    if (ts) { gamesData.tsUrl = ts.url; $("games-ts").hidden = false; }
    if (!p.done) return;
    clearInterval(gamesTimer);
    $("games-ts").hidden = true;
    if (p.error) { $("games-status").hidden = true; showError("games-error", p.error); gamesData.removing = null; renderGames(); return; }
    const msg = t("games.removed", { title: gameTitle(g) }) + (p.result.how === "machine" ? " " + t("games.removed_machine") : "");
    await openGames();
    $("games-status").hidden = false;
    $("games-status").textContent = msg;
  }, 1000);
}

// ---------------------------------------------------------------- ход операции
const STAGES = { play: ["vast", "tailscale", "restore", "ready"], stop: ["shutdown", "verify", "deleted"] };

// Баннер игры на экране запуска: широкая картинка (нет — обложка), на ней «Запуск: название» и время
function renderBanner(kind) {
  const g = kind === "play" && playNow;
  const pic = g && (g.hero || g.header || g.cap);
  $("progress-banner").hidden = !g;
  $("progress-title").hidden = $("progress-elapsed").hidden = !!g;
  if (!g) return;
  const img = $("progress-banner-img");
  img.hidden = !pic;
  if (pic && img.getAttribute("src") !== pic) { img.src = pic; img.onerror = () => (img.hidden = true); }
  $("progress-banner-title").textContent = g.name;
  $("progress-banner-sub").textContent = $("progress-elapsed").textContent;
}

function startProgress(kind = "play") {
  show("progress");
  prog = { since: 0, log: [], shownQ: 0, kind };
  $("progress-title").textContent = kind === "play" && playNow ? t("progress.title.play_game", { name: playNow.name })
    : t("progress.title." + kind);
  renderBanner(kind);
  $("progress-note").textContent = t(kind === "stop" ? "progress.background_stop" : "progress.background");
  $("tailscale-box").hidden = true;
  $("progress-alert").hidden = true;
  $("progress-log").textContent = "";
  $("progress-result").hidden = true;
  $("progress-back").hidden = true;
  $("progress-offers").hidden = true;
  $("progress-cancel").hidden = kind !== "play";
  $("progress-cancel").disabled = false;
  $("progress-home").hidden = false;
  $("progress-replace").hidden = kind !== "play";
  $("progress-note").hidden = false;
  showError("progress-error", null);
  renderStages(null, false);
  clearInterval(progTimer);
  progTimer = setInterval(pollProgress, 1000);
  pollProgress();
}

function renderStages(stage, failed) {
  const list = $("stages");
  list.innerHTML = "";
  const stages = STAGES[(prog && prog.kind) || "play"];
  const idx = stages.indexOf(stage);
  const last = stages[stages.length - 1];
  stages.forEach((s, i) => {
    const li = document.createElement("li");
    li.innerHTML = '<span class="mark"></span><span></span>';
    li.lastChild.textContent = t("stage." + s);
    if (i < idx || (s === last && stage === last)) li.className = "done";
    else if (i === idx || (idx < 0 && i === 0)) li.className = failed ? "failed" : "active";
    list.appendChild(li);
  });
}

// Архивы по порядку: сначала идущие (скачивается / выгружается), потом ошибки, ждущие и готовые
const ARCH_ORDER = { down: 0, up: 0, err: 1, wait: 2, ok: 3 };
function sortedArchives(archives) {
  return Object.keys(archives || {}).filter((n) => n !== "boot")
    .sort((a, b) => (ARCH_ORDER[archives[a][0]] ?? 2) - (ARCH_ORDER[archives[b][0]] ?? 2) || archiveName(a).localeCompare(archiveName(b)));
}

// Честная скорость: сколько байт реально прибавилось за последние ~20 с (машина присылает скорость отдельных кусков —
// она завышена в разы: между кусками бывают простои; живой тест 2026-10-01). Меньше 5 с наблюдений — ещё не знаем.
const SPEED_WINDOW = 20, SPEED_MIN = 5;
const speedHist = {};   // имя архива → [[время, сделано], …]
function avgSpeed(name, pg) {
  const now = Date.now() / 1000;
  let h = speedHist[name] || [];
  if (h.length && pg.done < h[h.length - 1][1]) h = [];          // архив начали заново
  h.push([now, pg.done]);
  while (h.length > 2 && now - h[1][0] >= SPEED_WINDOW) h.shift();
  speedHist[name] = h;
  const [t0, d0] = h[0], span = now - t0;
  return span >= SPEED_MIN ? (pg.done - d0) / span : null;
}
function leftText(sec) {
  return sec < 60 ? t("arch.left.soon") : t("arch.left", { time: hm(sec) });
}

// Строка архива: имя и состояние; пока он качается или выгружается (wolf-setup.sh v4.6) — полоса,
// «45% · 12,3 из 27 ГБ» и средняя скорость с «осталось ~N мин»
function archiveRow(name, state, pg) {
  const li = el("li", { className: "arch " + state });
  const head = el("div", { className: "arch-head" });
  head.append(el("span", { className: "arch-name" }, archiveName(name)), el("span", { className: "st " + state }, t("arch." + state)));
  li.append(head);
  if (pg && (state === "down" || state === "up") && pg.total > 0) {
    const pct = Math.min(100, Math.floor((pg.done / pg.total) * 100));
    const bar = el("div", { className: "bar" });
    const fill = el("span");
    fill.style.width = pct + "%";
    bar.append(fill);
    const line = el("div", { className: "arch-line" });
    const sp = avgSpeed(name, pg);
    const speed = sp == null ? "" : (state === "down" ? "↓ " : "↑ ") + t("arch.speed", { speed: size(sp) })
      + (sp > 0 ? " · " + leftText((pg.total - pg.done) / sp) : "");
    line.append(el("span", {}, t("arch.progress", { pct, done: size(pg.done), total: size(pg.total) })),
                el("span", { className: "speed" }, speed));
    li.append(bar, line);
  }
  return li;
}

function renderArchives(archives) {
  const names = sortedArchives(archives);
  $("archives-card").hidden = !names.length;
  const ul = $("archives");
  ul.innerHTML = "";
  for (const n of names) {
    const [state, , pg] = archives[n];
    ul.appendChild(archiveRow(n, state, pg));
  }
}

const TO_LIST = ["core.offer_gone", "core.no_spare", "core.tries_exhausted", "core.tries_exhausted_docker"];
const RACE_KEYS = ["core.race_start", "core.race_added", "core.race_slow", "core.race_broken", "core.race_won",
                   "core.race_rent_failed", "core.fail_timeout", "core.fail_host", "core.fail_state"];
function eventText(ev) { return ev.key ? t(ev.key, ev.params || {}) : ev.text; }

async function pollProgress() {
  // Опросы не накладываются: пока ждём ответа Python или человека (вопрос), новый не начинаем
  if (prog.polling) return;
  prog.polling = true;
  try { await pollOnce(); } finally { prog.polling = false; }
}

async function pollOnce() {
  // Ход «Играть»/«Закончить» — из фонового процесса (он живёт и без окна)
  const p = DEMO ? demoProgress() : await api().session_progress(prog.since);
  if (!p) return;
  if (p.game_names) GAME_NAMES = p.game_names;
  if (p.kind && p.kind !== prog.kind) {          // окно открыли заново посреди операции — узнаём какой
    prog.kind = p.kind;
    $("progress-title").textContent = p.kind === "play" && playNow ? t("progress.title.play_game", { name: playNow.name })
      : t("progress.title." + p.kind);
    $("progress-note").textContent = t(p.kind === "stop" ? "progress.background_stop" : "progress.background");
    $("progress-cancel").hidden = p.kind !== "play";
    $("progress-replace").hidden = p.kind !== "play";
  }
  for (const ev of p.events) {
    prog.log.push(ev);
    // Tailscale просит подтвердить вход — заметный блок со ссылкой; после «SSH работает» — убрать
    const tsUrl = ev.url || (ev.params || {}).url;
    if (ev.level === "tailscale" && tsUrl) {
      prog.tsUrl = tsUrl;
      const approve = ev.key === "core.ts_approve";      // первая машина: одобрить её в сети Tailscale
      $("tailscale-box").querySelector(".title").textContent = t(approve ? "tailscale.approve.title" : "tailscale.title");
      $("tailscale-box").querySelector(".sub").textContent = t(approve ? "tailscale.approve.text" : "tailscale.text");
      $("tailscale-box").hidden = false;
    }
    if (ev.key === "core.tailscale_online") $("tailscale-box").hidden = true;
    // Забег машин: какие запущены, какая первой дошла, какие убраны — на видном месте, а не только
    // в «Подробностях»
    if (RACE_KEYS.includes(ev.key)) {
      prog.alerts = [...(prog.alerts || []), eventText(ev)].slice(-5);
      $("progress-alert").hidden = false;
      $("progress-alert").textContent = prog.alerts.join("\n");
    }
    if (ev.key === "core.ssh_ok") $("tailscale-box").hidden = true;
  }
  prog.since = p.next;
  $("progress-log").textContent = prog.log.map(eventText).join("\n");
  $("progress-elapsed").textContent = t("progress.elapsed", { time: duration(p.elapsed) });
  renderBanner(p.kind);
  renderStages(p.stage, !!p.error);
  renderArchives(p.archives);

  // Ядру нужно «да/нет» (например, удалить машину, которую хост не смог запустить)
  if (p.question && p.question.id !== prog.shownQ) {
    prog.shownQ = p.question.id;
    const q = p.question;
    const recent = prog.log.slice(-4).filter((e) => e.level === "error" || e.level === "warn").map(eventText).join("\n");
    const yes = await ask({ title: t("progress.question"), text: (recent ? recent + "\n\n" : "") + eventText(q) });
    if (!DEMO) await api().answer(q.id, yes);
  }

  if (p.done) {
    clearInterval(progTimer);
    $("progress-cancel").hidden = true;
    $("progress-home").hidden = true;
    $("progress-replace").hidden = true;
    $("progress-note").hidden = true;
    $("progress-back").hidden = false;
    if (p.error) showError("progress-error", p.error);
    // К списку машин: выбранную заняли, или она не запустилась, а замены «не хуже» нет — выбирает человек
    $("progress-offers").hidden = !(p.error && TO_LIST.includes(p.error.key));
    $("tailscale-box").hidden = true;
    if (p.result) (prog.kind === "stop" ? renderStopResult : renderResult)(p.result);
  }
}

// Отменить запуск: машина долго запускается или зависла — удалить её (на ней ещё ничего нет)
$("progress-cancel").addEventListener("click", async () => {
  if (!(await ask({ title: t("progress.cancel.confirm.title"), text: t("progress.cancel.confirm.text"),
                    yes: t("progress.cancel.yes"), no: t("btn.no") }))) return;
  if (DEMO) return;
  $("progress-cancel").disabled = true;
  $("progress-note").textContent = t("progress.cancelling");
  const r = await api().cancel_start();
  if (r && r.error) { showError("progress-error", r.error); $("progress-cancel").disabled = false; }
});

$("tailscale-open").addEventListener("click", () => { if (prog && prog.tsUrl && !DEMO) api().open_url(prog.tsUrl); });

function renderStopResult(r) {
  const box = $("progress-result");
  box.hidden = false;
  box.innerHTML = "";
  const ok = document.createElement("div");
  ok.className = "result-ok";
  ok.textContent = r.gone ? t("progress.stop_gone") : r.deleted ? t("progress.stopped") : t("core.delete_pending", { id: r.iid });
  box.appendChild(ok);
  if (r.gone) return;
  const up = document.createElement("div");
  up.className = "sub";
  up.textContent = r.uploaded.length ? t("core.uploaded", { names: r.uploaded }) : t("core.uploaded_none");
  box.appendChild(up);
}

function renderResult(r) {
  const box = $("progress-result");
  box.hidden = false;
  box.innerHTML = "";
  const ok = document.createElement("div");
  ok.className = "result-ok";
  ok.textContent = t("progress.ready", { min: r.minutes });
  box.appendChild(ok);
  if (playNow) {
    // Игра из библиотеки: дальше всё на главном (вход в Steam, скачивание, запуск) — туда и уходим
    box.appendChild(el("div", { className: "sub" }, t("play.result", { name: playNow.name })));
    setTimeout(() => {
      if ($("screen-progress").hidden) return;
      clearInterval(progTimer);
      if (!DEMO) api().session_seen();
      show("main");
      refresh();
    }, 2500);
    return;
  }
  // Адреса — тот же блок, что на главном экране. Приложение Sunshine, разрешение и битрейт — как
  // выбраны на главном; id у копии убираем, чтобы на странице не было двух одинаковых
  const links = $("links").cloneNode(true);
  const part = (id) => links.querySelector("#" + id);
  links.hidden = false;
  for (const id of ["ml-app", "params-row", "params"]) part(id).remove();   // выбор и параметры — на главном
  const btn = part("ml-start"), note = part("ml-note");
  btn.onclick = async () => {
    btn.disabled = true;
    note.hidden = false;
    note.textContent = t("moonlight.starting");
    const m = DEMO ? { installed: true, selected: "Desktop" } : await api().moonlight_apps(r.links.moonlight);
    if (!m.installed) { btn.disabled = false; note.textContent = t("moonlight.missing"); return; }
    launchMoonlight(btn, note, r.links.moonlight, m.selected);
  };
  links.removeAttribute("id");
  links.querySelectorAll("[id]").forEach((e) => e.removeAttribute("id"));
  box.appendChild(links);
  for (const n of r.notes || []) {
    const w = document.createElement("div");
    w.className = "warn-line";
    w.textContent = n.key ? t(n.key, n.params) : n.text;
    box.appendChild(w);
  }
}

$("progress-offers").addEventListener("click", () => { if (!DEMO) api().session_seen(); openOffers(); });
// Запуск идёт в фоновом процессе — можно уйти на главный (посмотреть баланс, другую машину); ход — там же кнопкой
$("progress-home").addEventListener("click", () => { clearInterval(progTimer); show("main"); refresh(); });
$("progress-back").addEventListener("click", () => { if (!DEMO) api().session_seen(); show("main"); refresh(); });

// Логотип и надпись VastGame — на главный экран. Тем же путём, что кнопка «Назад» открытого экрана: она же
// прибирает за собой (таймеры, «просмотрено»). Кнопка скрыта или неактивна (идёт аренда) — остаёмся.
$("brand").addEventListener("click", () => {
  if (!$("modal").hidden) return;
  const cur = ["main", "offers", "progress", "games", "setup", "history", "wizard", "library"].find((s) => !$("screen-" + s).hidden);
  if (cur === "main" || cur === "wizard") { show("main"); refresh(); return; }
  const back = { offers: "offers-cancel", setup: "setup-back", library: "library-back", history: "history-back",
                 games: "games-back", progress: $("progress-home").hidden ? "progress-back" : "progress-home" }[cur];
  const b = back && $(back);
  if (b && !b.hidden && !b.disabled) b.click();
});

// ---------------------------------------------------------------- язык
function fillLanguages() {
  const sel = $("lang");
  sel.innerHTML = "";
  for (const [code, label] of Object.entries(LANGUAGES)) {
    const o = document.createElement("option");
    o.value = code; o.textContent = label; sel.appendChild(o);
  }
  sel.value = LANG;
}

$("lang").addEventListener("change", (e) => {
  LANG = e.target.value;
  applyStatic();
  if (current) render(current);
  renderUpdate(updateInfo);
  $("app-version").title = t("app.version", { version: $("app-version").textContent });
  if (!$("screen-offers").hidden) renderOffers();
  if (!$("screen-games").hidden) renderGames();
  if (!$("screen-setup").hidden) renderSetup();
  if (!$("screen-history").hidden) openHistory(historyDays);
  if (!$("screen-library").hidden) renderLibrary();
  if (!$("screen-progress").hidden && prog) $("progress-log").textContent = prog.log.map(eventText).join("\n");
  if (!DEMO) api().set_language(LANG, true);   // выбор запоминается
});

// ---------------------------------------------------------------- демо-данные (только разработка)
function demoState(kind) {
  const m = { id: 52246512, gpu: "RTX 3090", country: "Latvia, LV", price: 0.204 };
  const li = { moonlight: "vastai-gaming-4.tail3d42c9.ts.net", sunshine: "https://vastai-gaming-4.tail3d42c9.ts.net:47990" };
  const base = { machine: m, links: li, busy: [], errors: [], done: 0, count: 1 };
  const s = {
    none: { state: "none", kind: "none", machine: null, links: null, count: 0 },
    starting: { ...base, state: "starting", kind: "restore", stage: "restore", done: 3, busy: ["steam-client", "pfx--3751260"] },
    ready: { ...base, state: "ready", kind: "ready",
             progs: { "3751260": { name: "The Blood of Dawnwalker", running: true, auto: ["game:Dawnwalker/Binaries/Win64/treiner.exe"],
                                   progs: [{ id: "game:Dawnwalker/Binaries/Win64/treiner.exe", name: "treiner.exe", where: "game" },
                                           { id: "prog:WeMod/WeMod.exe", name: "WeMod.exe", where: "WeMod" }] } } },
    // [v5.6] ?demo=launch — FF14 закрыта: установщик XIVLauncher в Programs, установленный лаунчер выбран для запуска
    launch: { ...base, state: "ready", kind: "ready",
              progs: { "312060": { name: "FINAL FANTASY XIV Online Free Trial", running: false, installed: true, auto: [],
                                   launch: { id: "pfx:users/steamuser/AppData/Local/XIVLauncher/XIVLauncher.exe", args: "" },
                                   progs: [{ id: "prog:XIVLauncher/Setup.exe", name: "Setup.exe", where: "XIVLauncher", setup: true },
                                           { id: "pfx:users/steamuser/AppData/Local/XIVLauncher/XIVLauncher.exe",
                                             name: "XIVLauncher.exe", where: "pfx" }] } } },
    syncing: { ...base, state: "syncing", kind: "syncing", busy: ["pfx--3751260", "sgame--Hades II"],
               archives: { "pfx--3751260": [0, "up", ""], "sgame--Hades II": [0, "up", ""], "steam-state": [0, "ok", ""] },
               progress: { "sgame--Hades II": { done: 4.1e9, total: 11.8e9, speed: 48e6 } },
               asks: [{ kind: "new", archive: "sgame--Hades II", name: "Hades II" },
                      { kind: "del", archive: "sgame--The Blood of Dawnwalker", name: "The Blood of Dawnwalker" }] },
    error: { ...base, state: "error", kind: "sync_error", errors: ["steam-client"] },
    low: { ...base, state: "ready", kind: "ready" },
  }[kind] || { state: "none", kind: "none", machine: null, links: null };
  const balance = kind === "low" ? 0.74 : 3.18;
  return { balance, low_balance: balance < 1, hours_left: s.machine ? balance / s.machine.price : null, status: s,
           session: s.machine ? { elapsed: 4870, cost: 0.276 } : null,
           game_names: { 3751260: "The Blood of Dawnwalker" } };
}
function demoOffers(disk) {
  // 40 машин из разных стран: страницы, фильтры, пинг трёх видов (измерен / по региону / оценка)
  const places = [["Pennsylvania, US", 165], ["Quebec, CA", 153], ["Japan, JP", 82], ["South Korea, KR", 67], [", CN", 72],
    ["Czechia, CZ", 95], ["Iceland, IS", 120], ["California, US", 161], ["United Kingdom, GB", 110], ["Finland, FI", 85]];
  const gpus = ["RTX 3060", "RTX 3070", "RTX 3080", "RTX 3090", "RTX 4070", "RTX 4090"];
  disk = disk || 100;
  const k = 1 + (disk - 100) / 2000;     // в демо цена чуть растёт с диском
  const offers = [];
  for (let i = 0; i < 40; i++) {
    const [country, est] = places[i % places.length], price = (0.09 + (i * 37 % 40) / 100) * k;
    const kind = i === 3 ? "measured" : i % 10 === 1 ? "region" : "estimate";
    offers.push({ id: 100 + i, gpu: gpus[i * 7 % gpus.length], country, cc: country.split(",").pop().trim(),
                  price, traffic: 0.01, total: price + 0.01, reliability: i % 7 === 3 ? 0.92 : 0.97 + (i % 3) / 100,
                  net_down: 90 + (i * 131 % 1800), net_up: 80 + (i * 97 % 1500), traffic_tb: [0, 0.4, 1, 3, 5.33][i % 5], tb_down: [0, 0.4, 1, 3, 4][i % 5], tb_up: [0, 0.4, 1, 3, 5.33][i % 5],
                  ping: { ms: kind === "estimate" ? est : est + 40, kind },
                  failed: i === 2 ? "Машина перешла в состояние «exited» и не запустится." : null });
  }
  offers.sort((a, b) => a.total - b.total);
  const busy = new URLSearchParams(location.search).get("last") === "busy";
  const current = DEMO === "replace" ? { gpu: "RTX 3090", country: "Latvia, LV", price: 0.204, ping: { ms: 95, kind: "estimate" } } : null;
  return { replace: !!current, current, disk, disk_min: 50, disk_max: 1000, min_rel: 0.97, count: offers.length, balance: 3.18, page_size: 15,
           filters: { countries: [], gpus: [], speed: 0, ping: 0, sort: "price", dir: "asc" },
           sync: { steam_games: false, other_games: true, saves: true }, offers,
           last: { gpu: "RTX 3080", country: "Ohio, US", start: Date.now() / 1000 - 86400 * 2,
                   offer: busy ? null : { id: 90, gpu: "RTX 3080", country: "Ohio, US", cc: "US", price: 0.162, traffic: 0.05, total: 0.212,
                                          reliability: 0.991, net_down: 900, net_up: 700, ping: { ms: 190, kind: "measured" } } } };
}
function demoCheck() {
  const o = offerList(offersData).find((x) => x.id === selected);
  return { ...o, balance: 3.18, hours: 3.18 / o.price, bad_reason: null };
}
let demoTick = 0;
function demoProgress() {
  demoTick++;
  if (prog.kind === "stop") {
    const stage = demoTick < 2 ? null : demoTick < 6 ? "shutdown" : demoTick < 7 ? "verify" : "deleted";
    const events = demoTick === 1 ? [{ level: "tailscale", text: "To authenticate, visit: https://login.tailscale.com/a/x", url: "https://login.tailscale.com/a/x" }]
      : demoTick === 3 ? [{ level: "ok", key: "core.ssh_ok" }] : [];
    const arch = demoTick < 3 ? {} : { "steam-state": [demoTick < 5 ? "up" : "ok", ""], "pfx--3751260": [demoTick < 6 ? "up" : "ok", ""] };
    const events2 = demoTick === 7 ? [{ level: "ok", key: "core.all_uploaded" }, { level: "info", key: "core.deleting", params: { id: 52246512 } }] : [];
    const q = null;
    const done = demoTick >= 8;
    return { kind: "stop", elapsed: demoTick * 20, stage, archives: arch, events: events.concat(events2), next: 0, question: q, done, error: null,
             result: done ? { deleted: true, iid: 52246512, uploaded: ["steam-state", "pfx--3751260"] } : null,
             game_names: { 3751260: "The Blood of Dawnwalker" } };
  }
  const stage = demoTick < 3 ? "vast" : demoTick < 5 ? "tailscale" : demoTick < 9 ? "restore" : "ready";
  const pg = (k) => ({ done: Math.min(53.2e9, k * 9.4e9), total: 53.2e9, speed: 92e6 });
  const arch = demoTick < 5 ? {} : { identity: ["ok", ""], "steam-state": ["ok", ""], "steam-client": [demoTick < 8 ? "down" : "ok", "", { done: 0.6e9, total: 1.2e9, speed: 71e6 }],
    "pfx--3751260": [demoTick < 7 ? "down" : "ok", ""], "sgame--The Blood of Dawnwalker": [demoTick < 9 ? "down" : "ok", "", pg(demoTick - 4)],
    "sgame--Hades II": ["wait", ""] };
  const events = demoTick === 1 ? [{ level: "stage", key: "stage.vast" }, { level: "detail", key: "core.vast_status", params: { status: "loading", msg: "" } }] : [];
  const done = demoTick >= 9;
  return { elapsed: demoTick * 40, stage, archives: arch, events, next: 0, question: null, done,
           result: done ? { minutes: 7, links: { moonlight: "vastai-gaming-4.tail3d42c9.ts.net", sunshine: "https://x:47990" }, notes: [] } : null,
           error: null, game_names: { 3751260: "The Blood of Dawnwalker" } };
}

function demoLibrary() {
  const A = "76561198000000001", B = "76561198000000002";
  const g = (appid, name, accounts, family = [], last = 0) => ({ appid: String(appid), name, accounts, family, minutes: 600, last,
    cap: `https://shared.cloudflare.steamstatic.com/store_item_assets/steam/apps/${appid}/library_600x900.jpg`, header: "", hero: "" });
  return {
    accounts: new URLSearchParams(location.search).get("lib") === "none" ? [] :
      [{ id: A, name: "Alex", until: 1809000000, expired: false }, { id: B, name: "Bob", until: 1790000000, expired: new URLSearchParams(location.search).get("lib") === "expired" }],
    games: [g(1245620, "ELDEN RING", [A, B], [], 9), g(1091500, "Cyberpunk 2077", [A], [], 8), g(1145350, "Hades II", [A], [], 7),
      g(1086940, "Baldur's Gate 3", [B], [], 6), g(1174180, "Red Dead Redemption 2", [], [A]), g(292030, "The Witcher 3", [A]),
      g(570, "Dota 2", [A, B]), g(2050650, "Resident Evil 4", [B]), g(312060, "FINAL FANTASY XIV Online Free Trial", [A], [], 5),
      g(9999999, "Неизвестная игра", [A])],
    errors: [], at: Math.floor(Date.now() / 1000),
    mods: { "1245620": { name: "mod--1245620--ELDEN RING", bytes: 340e6, mtime: "2026-09-28" } }, mods_save: { "1245620": true },
    lgame: { "312060": { name: "lgame--312060--FINAL FANTASY XIV Online Free Trial", bytes: 86.5e9, mtime: "2026-10-08" } },
    gdata: { "312060": true }, recipes: { "312060": true }, recipe_apps: { "312060": "ff14-ru", "39210": "ff14-ru" } };
}

function demoGames() {
  const g = (name, kind, title, appid, mb, mtime) => ({ name, kind, title, appid, bytes: mb * 1e6, mtime,
    system: /^sgame--(Steamworks|Proton)/.test(name) || appid === "1493710" });
  return { games: [g("sgame--Steamworks Shared", "sgame", "Steamworks Shared", null, 25, "2026-09-23"),
                   g("sgame--Proton - Experimental", "sgame", "Proton - Experimental", null, 1100, "2026-09-23"),
                   g("game--3751260", "game", "3751260", null, 184, "2026-09-22"),
                   g("pfx--3751260", "pfx", "The Blood of Dawnwalker", "3751260", 141, "2026-09-23"),
                   g("pfx--1493710", "pfx", "", "1493710", 109, "2026-09-23"),
                   g("pfx--3001234567", "pfx", "", "3001234567", 12, "2026-09-20"),
                   g("mod--1245620--ELDEN RING", "mod", "ELDEN RING", "1245620", 340, "2026-09-28"),
                   ...Array.from({ length: 12 }, (_, i) => g("sgame--Game " + (i + 1), "sgame", "Game " + (i + 1), null, 1000 * (i + 3), "2026-09-1" + (i % 10)))],
           service: { names: ["identity", "steam-cache", "steam-client", "steam-state"], bytes: 1.4e9 } };
}

function demoSetup() {
  const st = (id, state, key, params = {}) => ({ id, state, key, params });
  return { ready: false, brew: true, platform: "darwin", steps: [
    st("rclone", "ok", "setup.rclone.ok"), st("tailscale_app", "ok", "setup.tailscale_app.ok", { tailnet: "tail3d42c9.ts.net" }),
    st("moonlight", "missing", "setup.moonlight.missing"), st("vast", "ok", "setup.vast.ok", { balance: 3.15 }),
    st("google", "warn", "setup.google.old"), st("sshkey", "missing", "setup.sshkey.missing"), st("tailscale_key", "missing", "setup.tailscale_key.missing"),
    st("template", "ok", "setup.template.ok", { disk: 100, res: "1920x1200", lang: "", version: "v3.11" }),
    st("autostart", "warn", "setup.autostart.off"), st("open_at_login", "ok", "setup.open_at_login.off")] };
}

// Простая настройка в демо: ?demo=wizard&wiz=new|ts|done — с нуля, до Tailscale, всё готово
function demoStream() {
  return { native: { w: 2560, h: 1600, fps: 60 }, resolution: "native", bitrate: 0, bitrates: [5, 10, 20, 30, 40, 50, 80, 100, 150],
           resolutions: [{ value: "native", w: 2560, h: 1600, native: true }, { value: "2560x1440", w: 2560, h: 1440 },
                         { value: "1920x1200", w: 1920, h: 1200 }, { value: "1920x1080", w: 1920, h: 1080 }, { value: "1280x720", w: 1280, h: 720 }] };
}

let demoWiz = null;      // в демо нажатое считается сделанным (demoWizMark)
function demoWizState() {
  if (!demoWiz) {
    const w = new URLSearchParams(location.search).get("wiz") || "new";
    const past = w !== "new";
    demoWiz = { platform: "darwin", programs: { tailscale: past, moonlight: past }, vast: { ok: past, balance: 0.42 },
                google: { ok: past, email: "player@gmail.com" }, tailscale: { phase: w === "done" ? "ready" : "start" } };
  }
  return JSON.parse(JSON.stringify(demoWiz));
}
function demoWizMark(step) {
  if (!DEMO) return;
  demoWizState();
  if (step === "programs") demoWiz.programs = { tailscale: true, moonlight: true };
  if (step === "google") demoWiz.google.ok = true;
  if (step === "vast") demoWiz.vast = { ok: true, balance: 7.5 };
}
let demoTsTick = 0;
function demoTsPhase() {        // этапы сменяются сами, как будто человек всё нажимает
  const seq = ["start", "start", "extension", "extension", "extension", "vpn", "vpn", "login", "login", "login", "ready"];
  return { phase: seq[Math.min(demoTsTick++, seq.length - 1)] };
}

function demoHistory(days, all) {
  const now = Date.now() / 1000;
  const sessions = Array.from({ length: 8 }, (_, i) => ({ iid: 52264565 - i, start: now - i * 86400 * 1.5,
    gpu: i % 3 ? "RTX 3060" : "RTX 3090", country: i % 3 ? "Pennsylvania, US" : "Latvia, LV", hours: 1 + (i % 4) * 0.7,
    amount: 0.12 + i * 0.05 }));
  return { days, all, other: all ? { count: 0, amount: 0 } : { count: 3, amount: 1.27 }, sessions, first: sessions[sessions.length - 1].start, now, totals: { amount: 3.64, hours: 17.2, count: 8, per_hour: 0.212, gpu: 2.9, disk: 0.73, traffic: 0.01 } };
}

// ---------------------------------------------------------------- обновление
// Python проверяет GitHub в фоне (updater.Checker); страница лишь спрашивает, что он уже узнал
let updateInfo = null;    // {current, available, latest, page}
let updateReady = null;   // {version, app} — новая версия скачана и проверена, можно ставить
let updateNotice = null;  // окно открылось в конце обновления: {updated: {from, to}} или {failed: версия}
function renderUpdate(u) {
  updateInfo = u && !u.error ? u : null;
  const on = !!(updateInfo && updateInfo.available);
  $("update-banner").hidden = !on && !updateNotice;
  $("update-go").hidden = !on;
  $("update-notes").hidden = !on && !(updateNotice && updateNotice.updated);
  $("update-go").textContent = t(updateReady ? "update.install" : "update.button");
  if (on) $("update-title").textContent = t("update.available", { version: updateInfo.latest });
  else if (updateNotice) $("update-title").textContent = updateNotice.updated
    ? t("update.done", { version: updateNotice.updated.to }) : t("update.failed", { version: updateNotice.failed });
  $("update-title").classList.toggle("err", !on && !!(updateNotice && updateNotice.failed));
}
async function refreshUpdate() {
  try { renderUpdate(DEMO ? demoUpdate() : await api().update_status()); } catch (e) { renderUpdate(null); }
}
$("update-notes").addEventListener("click", () => {
  const v = updateInfo && updateInfo.available ? updateInfo.latest : updateNotice && updateNotice.updated && updateNotice.updated.to;
  if (v) openUrl("https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/releases/tag/vastgame-v" + v);
});

// «Обновить»: Python скачивает новую версию и проверяет её (ход — под надписью), потом — спросить и поставить:
// окно закроется, новую версию откроет сторож (updater.install)
function updateState(s) {
  const st = $("update-state");
  st.hidden = !s;
  st.classList.toggle("err", !!(s && s.error));
  st.textContent = !s ? "" : s.error ? errText(s.error) : s.ready ? t("update.ready", { version: s.ready.version }) : s.text;
  $("update-go").disabled = !!(s && s.text);
  $("update-go").textContent = t(updateReady ? "update.install" : "update.button");
}
$("update-go").addEventListener("click", async () => {
  if (!updateInfo || $("update-go").disabled) return;
  if (!updateReady) {
    updateState({ text: t("update.checking") });
    if (DEMO) { await sleep(800); updateReady = { version: updateInfo.latest }; }
    else {
      const r = await api().update_prepare();
      if (r && r.error) { updateState({ error: r.error }); return; }
      const p = await waitJob((e) => e.key && updateState({ text: t(e.key, e.params || {}) }));
      if (p.error) { updateState({ error: p.error }); return; }
      updateReady = p.result;
    }
    updateState({ ready: updateReady });
  }
  const v = updateReady.version;
  const yes = await ask({ title: t("update.confirm.title", { version: v }), text: t("update.confirm.text"),
                          yes: t("update.button"), no: t("update.later") });
  if (!yes) return;
  updateState({ text: t("update.installing", { version: v }) });
  if (DEMO) return;
  const r = await api().update_install();
  if (r && r.error) { updateState({ error: r.error }); return; }
  const p = await waitJob((e) => e.key && updateState({ text: t(e.key, e.params || {}) }));
  if (p.error) updateState({ error: p.error });            // иначе окно уже закрывается
});
function demoUpdate() {
  const v = new URLSearchParams(location.search).get("update");   // ?demo=ready&update=0.3.0
  return v ? { current: "0.2.0", available: true, latest: v, page: "https://github.com/" } : { current: "0.2.0", available: false };
}

// ---------------------------------------------------------------- старт
async function start() {
  let saved = null, version = "0.2.0", notice = null;
  if (DEMO) {
    const q = new URLSearchParams(location.search);
    saved = q.get("lang");
    PLATFORM = q.get("platform") || "";                         // ?platform=win32 — тексты для Windows
    if (q.get("updated")) notice = q.get("updated") === "failed" ? { failed: "0.2.1" } : { updated: { from: "0.2.0", to: q.get("updated") } };
  } else ({ language: saved, version, update: notice, platform: PLATFORM = "" } = await api().settings());
  // Окно открылось в конце обновления — сказать итог (минуту, потом надпись живёт как обычно)
  if (notice) { updateNotice = notice; setTimeout(() => { updateNotice = null; renderUpdate(updateInfo); }, 60 * 1000); }
  LANG = I18N[saved] ? saved : detectLanguage();
  fillLanguages();
  applyStatic();
  $("app-version").textContent = version || "";
  renderUpdate(null);
  $("app-version").title = t("app.version", { version });
  // Есть ли версия новее: первый вопрос запускает проверку в фоне, ответ — через несколько секунд
  refreshUpdate();
  setTimeout(refreshUpdate, 5000);
  if (!DEMO) setInterval(refreshUpdate, 60 * 1000);
  if (DEMO === "offers") { openOffers(); return; }
  if (DEMO === "replace") { openReplace(); return; }
  if (DEMO === "progress") { startProgress(); return; }
  if (DEMO === "stop") { startProgress("stop"); return; }
  if (DEMO === "games") { openGames(); return; }
  if (DEMO === "setup") { openSetup(); return; }
  if (DEMO === "history") { openHistory(30); return; }
  if (DEMO === "wizard") { openWizard(); return; }
  await refresh();
  // Окно открыли заново: запуск/завершение ещё идёт в фоне — показать ход; закончилось, пока окно
  // было закрыто, — показать результат (особенно важно, если что-то не получилось)
  const task = !DEMO && current && current.task;
  if (task && (task.active || task.unseen)) { startProgress(task.kind); loadSetup(); }
  else if (current === null && !DEMO) openWizard();          // первый запуск (нет ключа Vast) — простая настройка
  else loadSetup().then((d) => {
    // Что-то не настроено — простая настройка, если человек ещё на главном экране
    if (d && !d.error && !d.ready && !DEMO && !$("screen-main").hidden) openWizard();
  });
  if (!DEMO) setInterval(() => { if (!$("screen-main").hidden) refresh(); }, REFRESH_SEC * 1000);
}

if (DEMO) start();
else window.addEventListener("pywebviewready", start);   // pywebview подключает api чуть позже загрузки страницы
