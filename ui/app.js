// app.js — поведение главного экрана. Данные берём у Python: window.pywebview.api.state().
// Тексты — через t(ключ) из i18n.js (ru, en, es).
// Демо-режим для разработки в обычном браузере: index.html?demo=ready&lang=en
// (demo: none, starting, ready, syncing, error, low) — поддельные данные, ничего не запрашивается.

const REFRESH_SEC = 15;
const $ = (id) => document.getElementById(id);
const DEMO = new URLSearchParams(location.search).get("demo");
const api = () => window.pywebview.api;

let current = null;   // последние данные — для кнопок и повторной отрисовки при смене языка

function money(x) { return "$" + Number(x).toFixed(2); }
function num(x, digits = 1) {
  return new Intl.NumberFormat(LANG, { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(x);
}
function names(list) { return list.map((n) => archiveName(n)).join(", "); }

// Фраза под заголовком — по виду состояния (kind) и данным от ядра
function statusText(st) {
  const p = {
    count: st.count, status: st.vast_status || "", detail: st.detail || "",
    done: st.done, busy: (st.busy || []).length, names: names(st.state === "error" ? st.errors || [] : st.busy || []),
  };
  return t("status.text." + st.kind, p);
}

function render(data) {
  $("updated").textContent = t("app.updated", {
    time: new Date().toLocaleTimeString(LANG, { hour: "2-digit", minute: "2-digit" }),
  });
  const err = $("error");
  if (data.error) {
    err.hidden = false;
    err.textContent = data.error;
    return;
  }
  err.hidden = true;
  current = data;

  // Баланс
  $("balance").textContent = money(data.balance);
  const sub = [];
  if (data.hours_left != null) sub.push(t("balance.hours", { hours: num(data.hours_left) }));
  if (data.low_balance) sub.push(t("balance.low"));
  $("balance-sub").textContent = sub.join(" · ");
  $("balance-card").classList.toggle("low", !!data.low_balance);

  // Машина
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
}

async function refresh() {
  try {
    render(DEMO ? demoState(DEMO) : await api().state());
  } catch (e) {
    render({ error: t("app.error_state", { error: e }) });
  }
}

// ---- Язык: сохранённый выбор → иначе язык системы
function fillLanguages() {
  const sel = $("lang");
  sel.innerHTML = "";
  for (const [code, label] of Object.entries(LANGUAGES)) {
    const o = document.createElement("option");
    o.value = code; o.textContent = label; sel.appendChild(o);
  }
  sel.value = LANG;
}

function setLanguage(lang) {
  LANG = lang;
  applyStatic();
  $("lang").value = LANG;
  if (current) render(current);
}

$("lang").addEventListener("change", (e) => {
  setLanguage(e.target.value);
  if (!DEMO) api().set_language(LANG, true);   // выбор запоминается
});

// ---- Кнопки
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

// ---- Демо-данные (только для разработки)
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
  return { balance, low_balance: balance < 1, hours_left: s.machine ? balance / s.machine.price : null, status: s };
}

// ---- Старт
async function start() {
  let saved = null;
  if (DEMO) saved = new URLSearchParams(location.search).get("lang");
  else saved = (await api().settings()).language;
  LANG = I18N[saved] ? saved : detectLanguage();
  if (!DEMO) api().set_language(LANG, false);   // Python переводит свои сообщения на этот язык
  fillLanguages();
  applyStatic();
  await refresh();
  if (!DEMO) setInterval(refresh, REFRESH_SEC * 1000);
}

if (DEMO) start();
else window.addEventListener("pywebviewready", start);   // pywebview подключает api чуть позже загрузки страницы
