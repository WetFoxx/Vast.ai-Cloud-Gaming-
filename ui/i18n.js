// i18n.js — тексты окна на трёх языках. Внутри фигурных скобок — строгий JSON (его проверяет
// автотест tests/test_i18n.py: одинаковые ключи и подстановки {имя} во всех языках).
// Подстановки: {time}, {hours}... — заменяются значениями при показе.
const I18N = {
  "ru": {
    "app.loading": "загрузка…",
    "app.updated": "обновлено {time}",
    "app.error_state": "Не удалось получить состояние: {error}",
    "balance.label": "Баланс Vast",
    "balance.hours": "≈ {hours} ч на этой машине",
    "balance.low": "Баланс заканчивается — пополни на сайте Vast",
    "status.title.none": "Машины нет",
    "status.title.starting": "Запускается",
    "status.title.ready": "Готова к игре",
    "status.title.syncing": "Синхронизация",
    "status.title.stopping": "Выключается",
    "status.title.error": "Ошибка",
    "status.text.none": "Нажми «Играть», чтобы подобрать и арендовать машину",
    "status.text.many": "Машин несколько ({count}) — разберись в консоли Vast",
    "status.text.vast_error": "Vast не смог запустить машину: {status} {detail}",
    "status.text.vast": "Vast запускает машину",
    "status.text.tailscale": "Машина запущена, жду её в Tailscale",
    "status.text.restore": "Восстановление из Google Drive: готово {done}, в работе {busy}",
    "status.text.boot_error": "Восстановление с ошибками: {detail}",
    "status.text.sync_error": "Ошибка синхронизации: {names}",
    "status.text.syncing": "Выгружается: {names}",
    "status.text.ready": "Сохранения и сессия Steam на месте — подключайся через Moonlight",
    "machine.line": "{gpu} · {country} · {price}/ч · инстанс {id}",
    "links.moonlight": "Moonlight — добавь ПК по имени",
    "links.sunshine": "Кабинет Sunshine — PIN для Moonlight",
    "btn.copy": "Копировать",
    "btn.copied": "Скопировано",
    "btn.open": "Открыть",
    "btn.play": "Играть",
    "btn.stop": "Закончить",
    "btn.soon": "Появится на следующем шаге",
    "archive.identity": "сетевая личность",
    "archive.steam-state": "сессия Steam",
    "archive.steam-client": "клиент Steam",
    "archive.steam-cache": "кэш Steam",
    "archive.compatdata": "префиксы Proton",
    "archive.pfx": "сохранения {name}",
    "archive.game": "игра {name}",
    "archive.sgame": "игра Steam {name}"
  },
  "en": {
    "app.loading": "loading…",
    "app.updated": "updated {time}",
    "app.error_state": "Couldn't get the status: {error}",
    "balance.label": "Vast balance",
    "balance.hours": "≈ {hours} h on this machine",
    "balance.low": "Balance is running low — top up on the Vast website",
    "status.title.none": "No machine",
    "status.title.starting": "Starting",
    "status.title.ready": "Ready to play",
    "status.title.syncing": "Syncing",
    "status.title.stopping": "Shutting down",
    "status.title.error": "Error",
    "status.text.none": "Press “Play” to pick and rent a machine",
    "status.text.many": "There are several machines ({count}) — sort it out in the Vast console",
    "status.text.vast_error": "Vast couldn't start the machine: {status} {detail}",
    "status.text.vast": "Vast is starting the machine",
    "status.text.tailscale": "The machine is up, waiting for it in Tailscale",
    "status.text.restore": "Restoring from Google Drive: {done} done, {busy} in progress",
    "status.text.boot_error": "Restore finished with errors: {detail}",
    "status.text.sync_error": "Sync error: {names}",
    "status.text.syncing": "Uploading: {names}",
    "status.text.ready": "Your saves and Steam session are in place — connect with Moonlight",
    "machine.line": "{gpu} · {country} · {price}/h · instance {id}",
    "links.moonlight": "Moonlight — add the PC by name",
    "links.sunshine": "Sunshine web UI — PIN for Moonlight",
    "btn.copy": "Copy",
    "btn.copied": "Copied",
    "btn.open": "Open",
    "btn.play": "Play",
    "btn.stop": "Finish",
    "btn.soon": "Coming in the next step",
    "archive.identity": "network identity",
    "archive.steam-state": "Steam session",
    "archive.steam-client": "Steam client",
    "archive.steam-cache": "Steam cache",
    "archive.compatdata": "Proton prefixes",
    "archive.pfx": "{name} saves",
    "archive.game": "game {name}",
    "archive.sgame": "Steam game {name}"
  },
  "es": {
    "app.loading": "cargando…",
    "app.updated": "actualizado a las {time}",
    "app.error_state": "No se pudo obtener el estado: {error}",
    "balance.label": "Saldo de Vast",
    "balance.hours": "≈ {hours} h en esta máquina",
    "balance.low": "El saldo se está agotando: recarga en la web de Vast",
    "status.title.none": "Sin máquina",
    "status.title.starting": "Iniciando",
    "status.title.ready": "Lista para jugar",
    "status.title.syncing": "Sincronizando",
    "status.title.stopping": "Apagando",
    "status.title.error": "Error",
    "status.text.none": "Pulsa «Jugar» para elegir y alquilar una máquina",
    "status.text.many": "Hay varias máquinas ({count}): revísalo en la consola de Vast",
    "status.text.vast_error": "Vast no pudo iniciar la máquina: {status} {detail}",
    "status.text.vast": "Vast está iniciando la máquina",
    "status.text.tailscale": "La máquina está en marcha, esperándola en Tailscale",
    "status.text.restore": "Restaurando desde Google Drive: {done} listos, {busy} en curso",
    "status.text.boot_error": "La restauración terminó con errores: {detail}",
    "status.text.sync_error": "Error de sincronización: {names}",
    "status.text.syncing": "Subiendo: {names}",
    "status.text.ready": "Tus partidas guardadas y la sesión de Steam están listas: conéctate con Moonlight",
    "machine.line": "{gpu} · {country} · {price}/h · instancia {id}",
    "links.moonlight": "Moonlight: añade el PC por nombre",
    "links.sunshine": "Panel de Sunshine: PIN para Moonlight",
    "btn.copy": "Copiar",
    "btn.copied": "Copiado",
    "btn.open": "Abrir",
    "btn.play": "Jugar",
    "btn.stop": "Terminar",
    "btn.soon": "Llegará en el próximo paso",
    "archive.identity": "identidad de red",
    "archive.steam-state": "sesión de Steam",
    "archive.steam-client": "cliente de Steam",
    "archive.steam-cache": "caché de Steam",
    "archive.compatdata": "prefijos de Proton",
    "archive.pfx": "partidas guardadas de {name}",
    "archive.game": "juego {name}",
    "archive.sgame": "juego de Steam {name}"
  }
};

const LANGUAGES = { ru: "Русский", en: "English", es: "Español" };
let LANG = "en";

// Язык системы: первый из предпочтительных, который у нас есть; иначе английский
function detectLanguage() {
  for (const l of navigator.languages || [navigator.language || "en"]) {
    const code = String(l).slice(0, 2).toLowerCase();
    if (I18N[code]) return code;
  }
  return "en";
}

// Перевод по ключу с подстановками: t("app.updated", {time: "12:00"})
function t(key, params = {}) {
  const text = (I18N[LANG] && I18N[LANG][key]) || I18N.en[key] || key;
  return text.replace(/\{(\w+)\}/g, (m, name) => (name in params ? params[name] : m));
}

// Статичные тексты страницы: <span data-i18n="btn.play">, title — data-i18n-title
function applyStatic() {
  document.documentElement.lang = LANG;
  document.querySelectorAll("[data-i18n]").forEach((el) => (el.textContent = t(el.dataset.i18n)));
  document.querySelectorAll("[data-i18n-title]").forEach((el) => (el.title = t(el.dataset.i18nTitle)));
}

// Понятное имя архива wolf: identity → «сетевая личность», pfx--1086940 → «сохранения Baldur's Gate 3»
function archiveName(name, gameNames = {}) {
  if (I18N.en["archive." + name]) return t("archive." + name);
  const m = /^(pfx|game|sgame)--(.+)$/.exec(name);
  if (!m) return name;
  const shown = m[1] === "pfx" ? gameNames[m[2]] || "appid " + m[2] : m[2];
  return t("archive." + m[1], { name: shown });
}
