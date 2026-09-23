// app.js — поведение главного экрана. Данные берём у Python: window.pywebview.api.state().
// Демо-режим для разработки в обычном браузере: index.html?demo=ready (none, starting, ready,
// syncing, error, low) — поддельные данные, ничего не запрашивается.

const REFRESH_SEC = 15;
const $ = (id) => document.getElementById(id);

const TITLES = {
  none: "Машины нет",
  starting: "Запускается",
  ready: "Готова к игре",
  syncing: "Синхронизация",
  stopping: "Выключается",
  error: "Ошибка",
};
// Подсказка под заголовком; для остальных состояний — текст от ядра (этап запуска, что с ошибкой)
const HINTS = {
  none: "Нажми «Играть», чтобы подобрать и арендовать машину",
  ready: "Сохранения и сессия Steam на месте — подключайся через Moonlight",
};

let current = null;   // последнее состояние — для кнопок

function money(x) { return "$" + Number(x).toFixed(2); }

function render(data) {
  $("updated").textContent = "обновлено " + new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
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
  if (data.hours_left != null) sub.push(`≈ ${data.hours_left.toFixed(1)} ч на этой машине`);
  if (data.low_balance) sub.push("Баланс заканчивается — пополни на сайте Vast");
  $("balance-sub").textContent = sub.join(" · ");
  $("balance-card").classList.toggle("low", !!data.low_balance);

  // Машина
  const st = data.status;
  $("dot").className = "dot " + st.state;
  $("status-title").textContent = TITLES[st.state];
  $("status-text").textContent = HINTS[st.state] || st.text;   // при запуске и ошибке — подробности от ядра

  const m = st.machine;
  $("machine").hidden = !m;
  if (m) $("machine").textContent = `${m.gpu} · ${m.country} · ${money(m.price)}/ч · инстанс ${m.id}`;

  const li = st.links;
  $("links").hidden = !li || st.state === "starting";
  if (li) $("moonlight").textContent = li.moonlight;
}

async function refresh() {
  try {
    render(DEMO ? demoState(DEMO) : await window.pywebview.api.state());
  } catch (e) {
    render({ error: "Не удалось получить состояние: " + e });
  }
}

// Копировать имя для Moonlight
$("copy-moonlight").addEventListener("click", async () => {
  const text = $("moonlight").textContent;
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const r = document.createRange(); r.selectNodeContents($("moonlight"));
    const s = getSelection(); s.removeAllRanges(); s.addRange(r); document.execCommand("copy");
  }
  $("copy-moonlight").textContent = "Скопировано";
  setTimeout(() => ($("copy-moonlight").textContent = "Копировать"), 1500);
});

$("open-sunshine").addEventListener("click", () => {
  if (current && current.status.links && !DEMO) window.pywebview.api.open_url(current.status.links.sunshine);
});

// ---- Демо-данные (только для разработки)
const DEMO = new URLSearchParams(location.search).get("demo");
function demoState(kind) {
  const m = { id: 52246512, gpu: "RTX 3090", country: "Latvia, LV", price: 0.204 };
  const li = { moonlight: "vastai-gaming-4.tail3d42c9.ts.net", sunshine: "https://vastai-gaming-4.tail3d42c9.ts.net:47990" };
  const s = {
    none: { state: "none", text: "Машины нет", machine: null, links: null },
    starting: { state: "starting", stage: "restore", text: "Восстановление из Google Drive: готово 3, в работе 2", machine: m, links: li },
    ready: { state: "ready", text: "Готова к игре", machine: m, links: li },
    syncing: { state: "syncing", text: "Синхронизация: pfx--3751260", machine: m, links: li },
    error: { state: "error", text: "Ошибка синхронизации: steam-client", machine: m, links: li },
    low: { state: "ready", text: "Готова к игре", machine: m, links: li },
  }[kind] || { state: "none", text: "", machine: null, links: null };
  const balance = kind === "low" ? 0.74 : 3.18;
  return { balance, low_balance: balance < 1, hours_left: s.machine ? balance / s.machine.price : null, status: s };
}

if (DEMO) {
  refresh();
} else {
  // pywebview подключает window.pywebview.api чуть позже загрузки страницы
  window.addEventListener("pywebviewready", () => {
    refresh();
    setInterval(refresh, REFRESH_SEC * 1000);
  });
}
