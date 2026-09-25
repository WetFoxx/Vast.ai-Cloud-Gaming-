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
  return LANG === "ru" ? `${m} мин ${s} с` : `${m} min ${s} s`;
}

function show(screen) {
  for (const s of ["main", "offers", "progress", "games", "setup", "history", "wizard"]) $("screen-" + s).hidden = s !== screen;
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

function render(data) {
  $("updated").textContent = t("app.updated", {
    time: new Date().toLocaleTimeString(LANG, { hour: "2-digit", minute: "2-digit" }),
  });
  if (data.error) { showError("error", data.error); return; }
  showError("error", null);
  current = data;
  if (data.game_names) GAME_NAMES = data.game_names;

  $("balance").textContent = money(data.balance);
  const sub = [];
  if (data.hours_left != null) sub.push(t("balance.hours", { hours: num(data.hours_left) }));
  if (data.low_balance) sub.push(t("balance.low"));
  $("balance-sub").textContent = sub.join(" · ");
  $("balance-card").classList.toggle("low", !!data.low_balance);

  const st = data.status;
  $("dot").className = "dot " + st.state;
  $("status-title").textContent = t("status.title." + st.state);
  $("status-text").textContent = statusText(st);
  const m = st.machine;
  $("machine").hidden = !m;
  if (m) $("machine").textContent = t("machine.line", { gpu: m.gpu, country: m.country, price: money(m.price), id: m.id });
  // Сколько идёт сессия и сколько она уже стоит (по своей записи: начало и цена часа)
  $("session-line").hidden = !data.session;
  if (data.session) $("session-line").textContent = t("session.running", { time: hm(data.session.elapsed), cost: money(data.session.cost) });
  const li = st.links;
  $("links").hidden = !li || st.state === "starting";
  if (li) $("moonlight").textContent = li.moonlight;
  if (li && st.state !== "starting") { loadMoonlightApps(li.moonlight); loadStreamSettings(); }

  $("play").disabled = st.state !== "none" || !!data.busy || (setupData && !setupData.ready);
  // «Закончить» — когда машина работает (не во время запуска); аварийное удаление — пока машина есть
  $("stop").disabled = !m || !["ready", "syncing", "error"].includes(st.state) || !!data.busy;
  $("emergency").hidden = !m || !!data.busy;
}

async function refresh() {
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
  $("stream-set").hidden = false;
  showStreamHint();
}
function showStreamHint() {
  const native = $("stream-res").value === "native";
  $("stream-res").classList.toggle("native", native);
  $("stream-hint").hidden = false;
  $("stream-hint").textContent = t(native ? "stream.hint.native" : "stream.hint.other");
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

// ---------------------------------------------------------------- выбор машины
$("play").addEventListener("click", () => openOffers());

let offersReq = 0;   // номер последнего запроса списка: ответ на устаревший (другой диск) не показывать
async function openOffers(disk) {
  const reload = disk != null;       // смена диска: тумблеры синхронизации не сбрасывать
  show("offers");
  offersData = null; selected = null;
  $("rent").disabled = true;
  $("offers-summary").textContent = t("offers.loading");
  $("offers-table").querySelector("tbody").innerHTML = "";
  showError("offers-error", null);
  const my = ++offersReq;
  const data = DEMO ? demoOffers(disk) : await api().offers(disk ?? null);
  if (my !== offersReq) return;
  if (data.error) { showError("offers-error", data.error); return; }
  offersData = data;
  const slider = $("disk");
  slider.min = data.disk_min; slider.max = data.disk_max; slider.value = data.disk;
  if (!reload) for (const k of SYNC_KEYS) $("sync-" + k).checked = !!(data.sync || {})[k];
  renderOffers();
}

// Ползунок диска: число меняется сразу, список машин (цены зависят от диска) — когда отпустили
$("disk").addEventListener("input", () => { $("disk-value").textContent = t("disk.value", { gb: $("disk").value }); });
$("disk").addEventListener("change", () => openOffers(Number($("disk").value)));

const SYNC_KEYS = ["steam_games", "other_games", "saves"];
function syncChoice() { return Object.fromEntries(SYNC_KEYS.map((k) => [k, $("sync-" + k).checked])); }

// ---- Фильтры списка машин: страны, скорость интернета, пинг, сортировка (запоминаются в Python)
let filters = null, page = 0;
const SPEEDS = [0, 100, 300, 500, 1000];          // Мбит/с — не ниже (меньшая из скоростей ↓/↑)
const PINGS = [0, 50, 80, 100, 150, 200];         // мс — не выше
function netSpeed(o) { return Math.min(o.net_down, o.net_up); }
function countryName(cc) {
  try { return new Intl.DisplayNames([LANG], { type: "region" }).of(cc) || cc; } catch (e) { return cc; }
}
function saveFilters() { if (!DEMO) api().save_offer_filters(filters); }
function filtersActive() { return filters.countries.length || filters.speed || filters.ping; }

// Сортировка — нажатием на заголовок колонки; повторное нажатие переворачивает. Первое нажатие —
// самый полезный порядок: цена и пинг от меньшего, интернет и надёжность от большего.
const SORT_VALUE = { price: (o) => o.price, ping: (o) => o.ping.ms, net: netSpeed, rel: (o) => o.reliability };
const SORT_FIRST = { price: "asc", ping: "asc", net: "desc", rel: "desc" };

function applyFilters(list) {
  const f = filters;
  const r = list.filter((o) => (!f.countries.length || f.countries.includes(o.cc))
    && (!f.speed || netSpeed(o) >= f.speed)
    && (!f.ping || (o.ping.ms != null && o.ping.ms <= f.ping)));
  // Цена — только аренда: трафик по реальным списаниям почти не берётся. Неизвестное (пинг «—») — всегда в конце
  const val = SORT_VALUE[f.sort] || SORT_VALUE.price, dir = f.dir === "desc" ? -1 : 1;
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
  fillSelect($("f-speed"), SPEEDS, (v) => v ? t("filter.speed.from", { n: v }) : t("filter.any"), filters.speed);
  fillSelect($("f-ping"), PINGS, (v) => v ? t("filter.ping.to", { n: v }) : t("filter.any"), filters.ping);
  for (const th of document.querySelectorAll("#offers-table th.sortable")) {
    const on = th.dataset.sort === (filters.sort || "price");
    th.classList.toggle("sorted", on);
    th.title = t("sort.hint");
    th.querySelector(".arrow").textContent = on ? (filters.dir === "desc" ? "▼" : "▲") : "";
  }
  const names = filters.countries.map(countryName);
  $("f-countries-btn").textContent = !names.length ? t("filter.countries.any")
    : names.slice(0, 2).join(", ") + (names.length > 2 ? ` +${names.length - 2}` : "");
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
}

for (const [id, key] of [["f-speed", "speed"], ["f-ping", "ping"]]) {
  $(id).addEventListener("change", () => {
    filters[key] = Number($(id).value);
    page = 0; saveFilters(); renderOffers();
  });
}
$("f-countries-btn").addEventListener("click", () => { $("f-countries").hidden = !$("f-countries").hidden; });
$("f-countries-all").addEventListener("click", () => { filters.countries = []; page = 0; saveFilters(); renderOffers(); });
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
  if (!filters) filters = { ...d.filters, countries: [...(d.filters.countries || [])] };
  if (!SORT_VALUE[filters.sort]) filters.sort = "price";
  renderFilters(d);
  const list = offerList(d);
  $("offers-summary").textContent = t("offers.summary", { count: d.count, rel: num(d.min_rel * 100, 0), disk: d.disk })
    + (filtersActive() ? " · " + t("offers.filtered", { shown: list.length }) : "");
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
      <td class="num"></td><td class="num"></td><td class="num"></td><td class="num"></td>`;
    const c = tr.children;
    c[0].firstChild.checked = o.id === selected;
    c[1].textContent = o.gpu;
    const addTag = (cls, text, title) => {
      const tag = document.createElement("span");
      tag.className = cls; tag.textContent = text; if (title) tag.title = title;
      c[1].appendChild(tag);
    };
    if (o.last) addTag("tag", t("offers.last.tag"));
    if (o.start_min && !o.failed) addTag("tag", t("offers.start.tag", { min: o.start_min }), t("offers.start.hint"));
    if (o.failed) addTag("tag warn", t("offers.failed.tag"), o.failed);   // раньше не запустилась — причина во всплывающей подсказке
    c[2].textContent = o.country;
    pingCell(c[3], o.ping);
    c[4].textContent = t("unit.mbps", { n: netSpeed(o) });
    c[4].title = t("net.both", { down: o.net_down, up: o.net_up });
    c[5].textContent = money(o.price);
    c[6].textContent = num(o.reliability * 100) + "%";
    tr.addEventListener("click", () => { selected = o.id; renderOffers(); });
    body.appendChild(tr);
  }
  $("rent").disabled = false;
}

$("offers-cancel").addEventListener("click", () => { show("main"); refresh(); });

$("rent").addEventListener("click", async () => {
  if (selected == null) return;
  $("rent").disabled = true;
  $("offers-summary").textContent = t("confirm.rent.checking");
  const disk = offersData.disk;
  const o = DEMO ? demoCheck() : await api().check_offer(selected, disk);   // свежая цена прямо перед «да»
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
  const sync = syncChoice();
  const on = SYNC_KEYS.filter((k) => sync[k]).map((k) => t("sync." + k).toLowerCase());
  text += "\n\n" + t("confirm.rent.sync", { list: on.join(", ") || t("sync.none") });
  if (!sync.saves) text += " " + t("confirm.rent.saves_off");
  if (o.bad_reason) text += "\n\n" + t("confirm.badhost", { reason: o.bad_reason });
  const yes = await ask({ title: t("confirm.rent.title"), text, yes: t("confirm.rent.yes"), no: t("btn.no") });
  if (!yes) { $("rent").disabled = false; return; }
  if (DEMO) { startProgress(); return; }
  // Запасные — следующие машины из списка, как он сейчас отфильтрован и отсортирован: если Vast
  // не запустит выбранную за 10 минут, фоновый процесс сам возьмёт следующую
  const spare = offerList(offersData).map((x) => x.id).filter((id) => id !== o.id).slice(0, 10);
  const r = await api().play(o.id, sync, disk, LANG, spare);   // язык — для уведомления в конце
  if (r.error) { showError("offers-error", r.error); $("rent").disabled = false; return; }
  startProgress();
});

// ---------------------------------------------------------------- «Закончить» и аварийное удаление
$("stop").addEventListener("click", async () => {
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
  $("setup-summary").textContent = t("setup.loading");
  $("setup-steps").innerHTML = "";
  showError("setup-error", null);
  const d = await loadSetup();
  if (d.error) { $("setup-summary").textContent = ""; showError("setup-error", d.error); return; }
  renderSetup();
}

function renderSetup() {
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
  const login = el("div", { className: "actions" });
  login.appendChild(button(t("setup.act.sunshine_login"), async () => {
    const r = DEMO ? { user: "vastgame", password: "demo" } : await api().sunshine_login();
    stepMsg(li, r.user ? t("setup.sunshine_login", { user: r.user, password: r.password }) : t("setup.sunshine_login.none"));
  }));
  li.querySelector(".body").appendChild(login);
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
    if (setupData.platform === "darwin" && (id !== "rclone" || setupData.brew)) {
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
  if (s.platform !== "darwin") {            // пока только Mac умеет ставить сам — остальным ссылки
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

// ---------------------------------------------------------------- история и траты
let historyData = null, historyDays = 30, historyAll = false;
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
  const d = DEMO ? demoHistory(days, all) : await api().history(days, all);
  if (days !== historyDays || all !== historyAll) return;   // пока считали, выбрали другой период
  if (d.error) { $("history-summary").hidden = true; showError("history-error", d.error); return; }
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
let gamesData = null;
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
  const d = DEMO ? demoGames() : await api().games();
  if (d.error) { $("games-summary").textContent = ""; showError("games-error", d.error); return; }
  gamesData = d;
  renderGames();
}

// Номер игры не из Steam, добавленной в Steam ярлыком, — большой (старший бит установлен)
const SHORTCUT_APPID = 2147483648;
function gameTitle(g) {
  if (g.kind !== "pfx") return g.title;
  if (g.title) return `${g.title} (appid ${g.appid})`;
  return Number(g.appid) >= SHORTCUT_APPID ? t("games.pfx_shortcut", { appid: g.appid }) : `appid ${g.appid}`;
}

$("games-search").addEventListener("input", renderGames);

function renderGames() {
  const d = gamesData;
  if (!d) return;
  const total = d.games.reduce((a, g) => a + g.bytes, 0);
  $("games-summary").textContent = d.games.length ? t("games.total", { size: size(total), count: d.games.length }) : t("games.empty");
  $("games-service").textContent = d.service.names.length ? t("games.service", { size: size(d.service.bytes) }) : "";
  $("games-card").hidden = !d.games.length;
  $("games-search").hidden = d.games.length < 8;          // поиск нужен, когда игр много
  const q = $("games-search").hidden ? "" : $("games-search").value.trim().toLowerCase();
  const order = { sgame: 0, game: 1, pfx: 2 };
  const shown = d.games
    .filter((g) => !q || gameTitle(g).toLowerCase().includes(q) || g.name.toLowerCase().includes(q))
    .sort((a, b) => order[a.kind] - order[b.kind] || b.bytes - a.bytes);
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
    if (g.kind !== group) {                                  // заголовок группы: тип, сколько, сколько места
      group = g.kind;
      const same = shown.filter((x) => x.kind === group);
      const tr = document.createElement("tr");
      tr.className = "group";
      tr.innerHTML = '<td colspan="5"></td>';
      tr.firstChild.textContent = t("games.group", { kind: t("games.kind." + group), count: same.length,
        size: size(same.reduce((a, x) => a + x.bytes, 0)) });
      body.appendChild(tr);
    }
    const tr = document.createElement("tr");
    tr.style.cursor = "default";
    tr.innerHTML = '<td><span class="kind"></span></td><td></td><td class="num"></td><td class="num"></td><td class="act"><button></button></td>';
    const c = tr.children;
    c[0].firstChild.textContent = t("games.kind." + g.kind);
    c[0].firstChild.classList.add(g.kind);
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
  let text = t("games.confirm.text", { kind: t("games.kind." + g.kind), title: gameTitle(g), size: size(g.bytes) });
  if (g.kind === "pfx") text = t("games.confirm.saves") + "\n\n" + text;
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

function startProgress(kind = "play") {
  show("progress");
  prog = { since: 0, log: [], shownQ: 0, kind };
  $("progress-title").textContent = t("progress.title." + kind);
  $("progress-note").textContent = t(kind === "stop" ? "progress.background_stop" : "progress.background");
  $("tailscale-box").hidden = true;
  $("progress-alert").hidden = true;
  $("progress-log").textContent = "";
  $("progress-result").hidden = true;
  $("progress-back").hidden = true;
  $("progress-offers").hidden = true;
  $("progress-cancel").hidden = kind !== "play";
  $("progress-cancel").disabled = false;
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

function renderArchives(archives) {
  const names = Object.keys(archives || {}).filter((n) => n !== "boot").sort();
  $("archives-card").hidden = !names.length;
  const ul = $("archives");
  ul.innerHTML = "";
  for (const n of names) {
    const [state] = archives[n];
    const li = document.createElement("li");
    li.innerHTML = '<span></span><span class="st"></span>';
    li.firstChild.textContent = archiveName(n);
    li.lastChild.textContent = t("arch." + state);
    li.lastChild.classList.add(state);
    ul.appendChild(li);
  }
}

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
    $("progress-title").textContent = t("progress.title." + p.kind);
    $("progress-note").textContent = t(p.kind === "stop" ? "progress.background_stop" : "progress.background");
    $("progress-cancel").hidden = p.kind !== "play";
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
    // Машина не запустилась и её заменили — на видном месте, а не только в «Подробностях»
    if (["core.fail_timeout", "core.fail_host", "core.fail_state", "core.switching"].includes(ev.key)) {
      $("progress-alert").hidden = false;
      $("progress-alert").textContent = ev.key === "core.switching"
        ? [prog.lastFail, eventText(ev)].filter(Boolean).join("\n") : (prog.lastFail = eventText(ev));
    }
    if (ev.key === "core.tailscale_online") $("progress-alert").hidden = true;
    if (ev.key === "core.ssh_ok") $("tailscale-box").hidden = true;
  }
  prog.since = p.next;
  $("progress-log").textContent = prog.log.map(eventText).join("\n");
  $("progress-elapsed").textContent = t("progress.elapsed", { time: duration(p.elapsed) });
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
    $("progress-note").hidden = true;
    $("progress-back").hidden = false;
    if (p.error) showError("progress-error", p.error);
    $("progress-offers").hidden = !(p.error && p.error.key === "core.offer_gone");
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
  // Адреса — тот же блок, что на главном экране. Приложение Sunshine, разрешение и битрейт — как
  // выбраны на главном; id у копии убираем, чтобы на странице не было двух одинаковых
  const links = $("links").cloneNode(true);
  const part = (id) => links.querySelector("#" + id);
  links.hidden = false;
  part("moonlight").textContent = r.links.moonlight;
  for (const id of ["ml-app", "stream-set", "stream-hint"]) part(id).remove();
  const btn = part("ml-start"), note = part("ml-note");
  btn.onclick = async () => {
    btn.disabled = true;
    note.hidden = false;
    note.textContent = t("moonlight.starting");
    const m = DEMO ? { installed: true, selected: "Desktop" } : await api().moonlight_apps(r.links.moonlight);
    if (!m.installed) { btn.disabled = false; note.textContent = t("moonlight.missing"); return; }
    launchMoonlight(btn, note, r.links.moonlight, m.selected);
  };
  part("copy-moonlight").onclick = () => navigator.clipboard && navigator.clipboard.writeText(r.links.moonlight);
  part("open-sunshine").onclick = () => !DEMO && api().open_url(r.links.sunshine);
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
$("progress-back").addEventListener("click", () => { if (!DEMO) api().session_seen(); show("main"); refresh(); });

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
  if (!$("screen-offers").hidden) renderOffers();
  if (!$("screen-games").hidden) renderGames();
  if (!$("screen-setup").hidden) renderSetup();
  if (!$("screen-history").hidden) openHistory(historyDays);
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
    ready: { ...base, state: "ready", kind: "ready" },
    syncing: { ...base, state: "syncing", kind: "syncing", busy: ["pfx--3751260"] },
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
                  price, traffic: 0.01, total: price + 0.01, reliability: 0.97 + (i % 3) / 100,
                  net_down: 90 + (i * 131 % 1800), net_up: 80 + (i * 97 % 1500),
                  ping: { ms: kind === "estimate" ? est : est + 40, kind },
                  failed: i === 2 ? "Машина перешла в состояние «exited» и не запустится." : null });
  }
  offers.sort((a, b) => a.total - b.total);
  const busy = new URLSearchParams(location.search).get("last") === "busy";
  return { disk, disk_min: 50, disk_max: 1000, min_rel: 0.97, count: offers.length, balance: 3.18, page_size: 15,
           filters: { countries: [], speed: 0, ping: 0, sort: "price", dir: "asc" },
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
  const arch = demoTick < 5 ? {} : { identity: ["ok", ""], "steam-state": ["ok", ""], "steam-client": [demoTick < 8 ? "down" : "ok", ""], "pfx--3751260": [demoTick < 7 ? "down" : "ok", ""] };
  const events = demoTick === 1 ? [{ level: "stage", key: "stage.vast" }, { level: "detail", key: "core.vast_status", params: { status: "loading", msg: "" } }] : [];
  const done = demoTick >= 9;
  return { elapsed: demoTick * 40, stage, archives: arch, events, next: 0, question: null, done,
           result: done ? { minutes: 7, links: { moonlight: "vastai-gaming-4.tail3d42c9.ts.net", sunshine: "https://x:47990" }, notes: [] } : null,
           error: null, game_names: { 3751260: "The Blood of Dawnwalker" } };
}

function demoGames() {
  const g = (name, kind, title, appid, mb, mtime) => ({ name, kind, title, appid, bytes: mb * 1e6, mtime });
  return { games: [g("sgame--Steamworks Shared", "sgame", "Steamworks Shared", null, 25, "2026-09-23"),
                   g("game--3751260", "game", "3751260", null, 184, "2026-09-22"),
                   g("pfx--3751260", "pfx", "The Blood of Dawnwalker", "3751260", 141, "2026-09-23"),
                   g("pfx--1493710", "pfx", "", "1493710", 109, "2026-09-23"),
                   g("pfx--3001234567", "pfx", "", "3001234567", 12, "2026-09-20"),
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

// ---------------------------------------------------------------- старт
async function start() {
  let saved = null;
  if (DEMO) saved = new URLSearchParams(location.search).get("lang");
  else saved = (await api().settings()).language;
  LANG = I18N[saved] ? saved : detectLanguage();
  fillLanguages();
  applyStatic();
  if (DEMO === "offers") { openOffers(); return; }
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
