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
function duration(sec) {
  const m = Math.floor(sec / 60), s = sec % 60;
  return LANG === "ru" ? `${m} мин ${s} с` : `${m} min ${s} s`;
}

function show(screen) {
  for (const s of ["main", "offers", "progress"]) $("screen-" + s).hidden = s !== screen;
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
  const li = st.links;
  $("links").hidden = !li || st.state === "starting";
  if (li) $("moonlight").textContent = li.moonlight;

  $("play").disabled = st.state !== "none" || !!data.busy;
}

async function refresh() {
  try {
    render(DEMO ? demoState(DEMO) : await api().state());
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

$("open-sunshine").addEventListener("click", () => {
  if (current && current.status.links && !DEMO) api().open_url(current.status.links.sunshine);
});

// ---------------------------------------------------------------- выбор машины
$("play").addEventListener("click", openOffers);

async function openOffers() {
  show("offers");
  offersData = null; selected = null;
  $("rent").disabled = true;
  $("offers-summary").textContent = t("offers.loading");
  $("offers-table").querySelector("tbody").innerHTML = "";
  showError("offers-error", null);
  const data = DEMO ? demoOffers() : await api().offers();
  if (data.error) { showError("offers-error", data.error); return; }
  offersData = data;
  renderOffers();
}

function renderOffers() {
  const d = offersData;
  if (!d) return;
  $("offers-summary").textContent = t("offers.summary", { count: d.count, rel: num(d.min_rel * 100, 0), disk: d.disk });
  const body = $("offers-table").querySelector("tbody");
  body.innerHTML = "";
  if (!d.offers.length) { showError("offers-error", { key: "offers.none" }); return; }
  if (selected == null) selected = d.offers[0].id;
  for (const o of d.offers) {
    const tr = document.createElement("tr");
    tr.className = o.id === selected ? "selected" : "";
    tr.innerHTML = `<td><input type="radio" name="offer"></td><td></td><td></td>
      <td class="num"></td><td class="num"></td><td class="num"></td>`;
    const c = tr.children;
    c[0].firstChild.checked = o.id === selected;
    c[1].textContent = o.gpu;
    c[2].textContent = o.country;
    c[3].textContent = money(o.price);
    c[4].textContent = money(o.traffic);
    c[5].textContent = num(o.reliability * 100) + "%";
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
  const o = DEMO ? demoCheck() : await api().check_offer(selected);   // свежая цена прямо перед «да»
  renderOffers();
  if (o.error) { showError("offers-error", o.error); $("rent").disabled = false; return; }
  let text = t("confirm.rent.text", {
    gpu: o.gpu, country: o.country, price: money(o.price), balance: money(o.balance), hours: num(o.hours || 0),
  });
  if (o.bad_reason) text += "\n\n" + t("confirm.badhost", { reason: o.bad_reason });
  const yes = await ask({ title: t("confirm.rent.title"), text, yes: t("confirm.rent.yes"), no: t("btn.no") });
  if (!yes) { $("rent").disabled = false; return; }
  if (DEMO) { startProgress(); return; }
  const r = await api().play(o.id);
  if (r.error) { showError("offers-error", r.error); $("rent").disabled = false; return; }
  startProgress();
});

// ---------------------------------------------------------------- ход запуска
const PLAY_STAGES = ["vast", "tailscale", "restore", "ready"];

function startProgress() {
  show("progress");
  prog = { since: 0, log: [], shownQ: 0 };
  $("progress-log").textContent = "";
  $("progress-result").hidden = true;
  $("progress-back").hidden = true;
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
  const idx = PLAY_STAGES.indexOf(stage);
  PLAY_STAGES.forEach((s, i) => {
    const li = document.createElement("li");
    li.innerHTML = '<span class="mark"></span><span></span>';
    li.lastChild.textContent = t("stage." + s);
    if (i < idx || (s === "ready" && stage === "ready")) li.className = "done";
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
  const p = DEMO ? demoProgress() : await api().progress(prog.since);
  if (!p) return;
  if (p.game_names) GAME_NAMES = p.game_names;
  for (const ev of p.events) prog.log.push(ev);
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
    $("progress-note").hidden = true;
    $("progress-back").hidden = false;
    if (p.error) showError("progress-error", p.error);
    if (p.result) renderResult(p.result);
  }
}

function renderResult(r) {
  const box = $("progress-result");
  box.hidden = false;
  box.innerHTML = "";
  const ok = document.createElement("div");
  ok.className = "result-ok";
  ok.textContent = t("progress.ready", { min: r.minutes });
  box.appendChild(ok);
  // Адреса — тот же блок, что на главном экране
  const links = $("links").cloneNode(true);
  links.hidden = false;
  links.removeAttribute("id");
  links.querySelector("code").textContent = r.links.moonlight;
  links.querySelector("button").onclick = () => navigator.clipboard && navigator.clipboard.writeText(r.links.moonlight);
  links.querySelectorAll("button")[1].onclick = () => !DEMO && api().open_url(r.links.sunshine);
  box.appendChild(links);
  for (const n of r.notes || []) {
    const w = document.createElement("div");
    w.className = "warn-line";
    w.textContent = n.key ? t(n.key, n.params) : n.text;
    box.appendChild(w);
  }
}

$("progress-back").addEventListener("click", () => { show("main"); refresh(); });

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
           game_names: { 3751260: "The Blood of Dawnwalker" } };
}
function demoOffers() {
  const rows = [["RTX 3060", "Pennsylvania, US", 0.113, 0.039, 0.989], ["RTX 3070", "British Columbia, CA", 0.119, 0.039, 0.999],
    ["RTX 3090", "Latvia, LV", 0.204, 0.0, 0.989], ["RTX 4090", "Iceland, IS", 0.421, 0.0, 0.999], ["RTX 3090", "Czechia, CZ", 0.471, 0.0, 0.995]];
  return { disk: 100, min_rel: 0.97, count: 37, balance: 3.18,
           offers: rows.map((r, i) => ({ id: 100 + i, gpu: r[0], country: r[1], price: r[2], traffic: r[3], total: r[2] + r[3], reliability: r[4] })) };
}
function demoCheck() {
  const o = offersData.offers.find((x) => x.id === selected);
  return { ...o, balance: 3.18, hours: 3.18 / o.price, bad_reason: null };
}
let demoTick = 0;
function demoProgress() {
  demoTick++;
  const stage = demoTick < 3 ? "vast" : demoTick < 5 ? "tailscale" : demoTick < 9 ? "restore" : "ready";
  const arch = demoTick < 5 ? {} : { identity: ["ok", ""], "steam-state": ["ok", ""], "steam-client": [demoTick < 8 ? "down" : "ok", ""], "pfx--3751260": [demoTick < 7 ? "down" : "ok", ""] };
  const events = demoTick === 1 ? [{ level: "stage", key: "stage.vast" }, { level: "detail", key: "core.vast_status", params: { status: "loading", msg: "" } }] : [];
  const done = demoTick >= 9;
  return { elapsed: demoTick * 40, stage, archives: arch, events, next: 0, question: null, done,
           result: done ? { minutes: 7, links: { moonlight: "vastai-gaming-4.tail3d42c9.ts.net", sunshine: "https://x:47990" }, notes: [] } : null,
           error: null, game_names: { 3751260: "The Blood of Dawnwalker" } };
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
  await refresh();
  // Окно открыли заново, а операция ещё идёт — показать её ход
  if (!DEMO && current && current.busy) startProgress();
  if (!DEMO) setInterval(() => { if (!$("screen-main").hidden) refresh(); }, REFRESH_SEC * 1000);
}

if (DEMO) start();
else window.addEventListener("pywebviewready", start);   // pywebview подключает api чуть позже загрузки страницы
