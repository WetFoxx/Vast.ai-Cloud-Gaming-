# Перехват finalizelogin отдаёт пропуск в мост окна, а не в window.name.
# Живой WebKit стирает window.name при переходе на другой сайт (профиль Steam) —
# это проверяется отдельным запуском окна, не этим тестом.

import json
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

import steamlib

ROOT = Path(__file__).resolve().parent.parent


def jwt():
    import base64
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    sid = "76561198000000001"
    exp = int(time.time()) + 86400 * 200
    return f"{enc({'alg': 'EdDSA'})}.{enc({'sub': sid, 'exp': exp, 'aud': ['web', 'renew', 'derive']})}.c2lnbmF0dXJl"


TOKEN = jwt()

PAGE = r"""
const vm = require("vm");
const { URLSearchParams, Blob, FormData } = global;

function realm(hook, take) {
  const store = {};
  const calls = [];
  const context = {
    URL, URLSearchParams, Blob, FormData, Date, JSON, Promise, String, RegExp, parseInt, Math,
    location: { href: "https://login.steampowered.com/login/home/" },
  };
  context.window = {
    name: "untouched",
    fetch() { return Promise.resolve({ ok: true }); },
    pywebview: { api: { steam_nonce(attempt, token) { calls.push([attempt, token]); return true; } } },
  };
  context.sessionStorage = {
    getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem(k, v) { store[k] = String(v); },
    removeItem(k) { delete store[k]; },
  };
  context.navigator = { sendBeacon() { return true; } };
  context.XMLHttpRequest = class { open(m, u) { this.__u = u; } send() {} };
  vm.createContext(context);
  return {
    context, calls, store,
    install() { return vm.runInContext(hook, context); },
    take() { return vm.runInContext(take, context); },
  };
}

const TOKEN = process.env.VG_TOKEN;
const hook = process.env.VG_HOOK;
const take = process.env.VG_TAKE;
const attempt = process.env.VG_ATTEMPT;
const cases = {};

function post(body) {
  const r = realm(hook, take);
  r.install();
  r.context.window.fetch("https://login.steampowered.com/jwt/finalizelogin", { body });
  return r;
}

(async () => {
  const form = post(new URLSearchParams({ nonce: TOKEN }));
  cases.fetch_form = form.calls.length === 1 && form.calls[0][0] === attempt && form.calls[0][1] === TOKEN
    && form.context.window.name === "untouched" && form.take() === null ? "ok" : "bad";

  const xhr = realm(hook, take);
  xhr.install();
  const x = new xhr.context.XMLHttpRequest();
  x.open("POST", "/jwt/finalizelogin");
  x.send("nonce=" + encodeURIComponent(TOKEN));
  cases.xhr_relative = xhr.calls[0] && xhr.calls[0][1] === TOKEN ? "ok" : "bad";

  const js = post(JSON.stringify({ nonce: TOKEN }));
  cases.json_body = js.calls[0] && js.calls[0][1] === TOKEN ? "ok" : "bad";

  const req = realm(hook, take);
  req.install();
  req.context.window.fetch({ url: "https://login.steampowered.com/jwt/finalizelogin", body: new URLSearchParams({ nonce: TOKEN }) });
  cases.request_url = req.calls[0] && req.calls[0][1] === TOKEN ? "ok" : "bad";

  const beacon = realm(hook, take);
  beacon.install();
  beacon.context.navigator.sendBeacon("https://login.steampowered.com/jwt/finalizelogin",
    new Blob([new URLSearchParams({ nonce: TOKEN }).toString()]));
  const early = beacon.calls.length;
  await new Promise((resolve) => setTimeout(resolve, 30));
  cases.beacon_blob = early === 0 && beacon.calls[0] && beacon.calls[0][1] === TOKEN ? "ok" : "bad";

  const planted = realm(hook, take);
  planted.context.window.name = TOKEN;
  planted.context.sessionStorage.setItem("__vgNonce", TOKEN);
  cases.planted_storage_is_not_the_bridge = planted.calls.length === 0 && planted.take() === null
    && planted.context.window.name === TOKEN ? "ok" : "bad";

  function rejected(url) {
    const r = realm(hook, take);
    r.install();
    r.context.window.fetch(url, { body: new URLSearchParams({ nonce: TOKEN }) });
    return r.calls.length === 0;
  }
  cases.evil_host = rejected("https://evil.example/finalizelogin") ? "ok" : "bad";
  cases.http_not_https = rejected("http://login.steampowered.com/jwt/finalizelogin") ? "ok" : "bad";
  cases.lookalike_host = rejected("https://login.steampowered.com.evil/jwt/finalizelogin") ? "ok" : "bad";
  cases.extra_path = rejected("https://login.steampowered.com/jwt/finalizelogin/extra") ? "ok" : "bad";
  cases.query_still_matches = post(new URLSearchParams({ nonce: TOKEN })) && true;
  const q = realm(hook, take);
  q.install();
  q.context.window.fetch("https://login.steampowered.com/jwt/finalizelogin?redir=1", { body: new URLSearchParams({ nonce: TOKEN }) });
  cases.query_still_matches = q.calls.length === 1 ? "ok" : "bad";

  const csp = realm(hook, take);
  csp.context.window.webkit = { messageHandlers: { jsBridge: { postMessage(raw) {
    const body = JSON.parse(raw);
    if (body.funcName === "steam_nonce") csp.calls.push(body.params);
  }}}};
  delete csp.context.window.pywebview.api.steam_nonce;
  csp.install();
  const fd = new FormData();
  fd.append("nonce", TOKEN);
  fd.append("sessionid", "abc");
  csp.context.window.fetch("https://login.steampowered.com/jwt/finalizelogin", { method: "POST", body: fd });
  cases.csp_formdata_without_api = csp.calls.length === 1 && csp.calls[0][1] === TOKEN ? "ok" : "bad";

  const moved = realm(hook, take);
  moved.context.window.name = form.context.window.name;
  cases.name_is_not_a_channel = moved.take() === null && moved.calls.length === 0 ? "ok" : "bad";

  const other = realm(hook, take);
  other.install();
  other.context.window.fetch("https://steamcommunity.com/profiles/76561198000000001");
  cases.profile_does_not_call = other.calls.length === 0 && other.take() === null ? "ok" : "bad";

  process.stdout.write(JSON.stringify(cases));
})().catch((e) => { console.error(e); process.exit(1); });
"""


class TestSteamCapture(unittest.TestCase):
    def test_hook_delivers_to_the_bridge_not_window_name(self):
        attempt, hook, take = steamlib.begin_login(time.time() + 60)
        self.assertNotIn("window.name", hook)
        self.assertNotIn("window.name", take)
        self.assertNotIn(TOKEN, hook)
        proc = subprocess.run(
            ["node", "-e", PAGE],
            text=True,
            capture_output=True,
            timeout=20,
            cwd=ROOT,
            env={
                **{k: v for k, v in __import__("os").environ.items()},
                "VG_TOKEN": TOKEN,
                "VG_HOOK": hook,
                "VG_TAKE": take,
                "VG_ATTEMPT": attempt,
            },
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = json.loads(proc.stdout)
        self.assertEqual(got, {k: "ok" for k in (
            "fetch_form", "xhr_relative", "json_body", "request_url", "beacon_blob",
            "planted_storage_is_not_the_bridge", "name_is_not_a_channel", "profile_does_not_call",
            "evil_host", "http_not_https", "lookalike_host", "extra_path", "query_still_matches",
            "csp_formdata_without_api")})
        self.assertIn("messageHandlers.jsBridge.postMessage", hook)
        self.assertNotIn("eval(", hook)
        self.assertNotIn("new Function", hook)

    def test_trace_drops_secrets(self):
        attempt, _hook, _take = steamlib.begin_login(time.time() + 60)
        box = steamlib.LoginCapture(attempt, time.time() + 60)
        logs = []
        with mock.patch.object(steamlib.settings, "applog", logs.append):
            self.assertFalse(box.steam_trace(TOKEN))
            self.assertFalse(box.steam_trace("nonce=" + TOKEN))
            self.assertTrue(box.steam_trace("hook_installed"))
            self.assertTrue(box.steam_nonce(attempt, TOKEN))
        text = "\n".join(logs)
        self.assertNotIn(TOKEN, text)
        self.assertIn("steam-auth: hook_installed", text)
        self.assertIn("steam-auth: capture_accepted", text)
        self.assertIn("steam-auth: bridge_message_received", text)

    def test_capture_accepts_only_this_attempt(self):
        attempt, _hook, _take = steamlib.begin_login(time.time() + 60)
        box = steamlib.LoginCapture(attempt, time.time() + 60)
        self.assertFalse(box.steam_nonce("0" * len(attempt), TOKEN))
        self.assertFalse(box.steam_nonce(attempt, "https://steamcommunity.com/profiles/76561198000000001"))
        self.assertTrue(box.steam_nonce(attempt, TOKEN))
        self.assertEqual(box.token, TOKEN)
        other = TOKEN[:-1] + ("A" if TOKEN[-1] != "A" else "B")
        self.assertTrue(box.steam_nonce(attempt, other))
        self.assertEqual(box.pending(), [TOKEN, other])   # подмена не стирает первый, оба ждут проверки Steam
        self.assertTrue(box.steam_nonce(attempt, TOKEN))   # повтор того же пропуска не копится
        self.assertEqual(box.pending(), [TOKEN, other])
        box.seal()
        self.assertFalse(box.steam_nonce(attempt, other))
        self.assertEqual(box.pending(), [TOKEN, other])
        late = steamlib.LoginCapture(attempt, time.time() - 5)
        self.assertFalse(late.steam_nonce(attempt, TOKEN))
        self.assertIsNone(late.token)

    def test_refresh_shape_is_not_a_profile_url(self):
        self.assertEqual(steamlib.refresh_token(TOKEN), TOKEN)
        self.assertEqual(steamlib.refresh_token("  " + TOKEN + "\n"), TOKEN)
        self.assertEqual(steamlib.refresh_token("https://steamcommunity.com/profiles/76561198000000001"), "")
        self.assertEqual(steamlib.refresh_token("__vgNonce:" + TOKEN), "")
        self.assertEqual(steamlib.refresh_token(TOKEN.split(".")[1]), "")


if __name__ == "__main__":
    unittest.main()
