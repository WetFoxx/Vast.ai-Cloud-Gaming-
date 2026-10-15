"""fxwin.py — [2026-10-15] анимация запуска трансляции на весь экран (Mac) отдельным процессом («app_main.py fx»).

Зачем отдельный процесс: окно должно быть поверх всего — строки меню, Dock и самого Moonlight, пока тот грузится (в
том числе его полноэкранного стола). На чужой полноэкранный стол macOS пускает окна только «фонового» приложения без
значка в Dock — как карточку вопроса машины (gui.card_main). Главное окно пишет, что показать и чем кончилось, в
fx.json (папка настроек); этот процесс читает его и журнал Moonlight: гаснет, когда пришёл первый кадр видео
(«Received first video packet»), показывает ошибку, если Moonlight оборвал соединение. Клик, Esc или минута ожидания
кадра — закрыть: экран не держим, если что-то пошло не так."""
import glob
import json
import os
import subprocess
import sys
import time

import filelock
import paths
from settings import config_dir

FX_FILE = config_dir() / "fx.json"
FX_LOCK = config_dir() / "fx.lock"
VIDEO_WAIT = 60          # с после ответа «Moonlight запущен» — ждём первый кадр не дольше
VIDEO_OK = "Received first video packet"
VIDEO_FAIL = ("Connection terminated", "No video traffic was ever received")


def _write(d):
    tmp = FX_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, FX_FILE)


def read():
    try:
        return json.loads(FX_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def show(data):
    """Главное окно: показать анимацию (запустить процесс; уже идёт — он подхватит новые данные)."""
    _write({"data": data if isinstance(data, dict) else {}, "t0": time.time(), "done": None, "why": "", "at": None})
    subprocess.Popen(paths.self_cmd("fx"), cwd=paths.HERE, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def done(ok, why=""):
    """Главное окно: Moonlight запущен (ok) или нет (причина)."""
    d = read()
    if d is not None:
        d.update(done=bool(ok), why=str(why or "")[:400], at=time.time())
        _write(d)


def video_state(t0, logs=None):
    """По журналам Moonlight, созданным после t0: "video" — пришёл первый кадр, (False, строка) — оборвалось,
    None — ещё грузится. logs — для тестов."""
    files = logs if logs is not None else [f for f in glob.glob("/tmp/Moonlight-*.log") if os.path.getmtime(f) >= t0 - 5]
    for f in sorted(files, key=lambda x: os.path.getmtime(x), reverse=True):
        try:
            text = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if VIDEO_OK in text:
            return "video"
        bad = next((ln for ln in text.splitlines() if any(m in ln for m in VIDEO_FAIL) and "pin" not in ln.lower()), None)
        if bad:
            return (False, bad.split(" - ", 1)[-1][-200:])
    return None


class FxApi:
    """Для страницы ui/launch.html."""

    def __init__(self):
        self.window = None

    def fx_state(self):
        d = read() or {}
        if not d.get("data"):
            return {"closed": True}
        out = {"data": d["data"], "done": d.get("done"), "why": d.get("why") or "", "video": False}
        if d.get("done"):
            v = video_state(d.get("t0") or 0)
            if v == "video" or time.time() - (d.get("at") or time.time()) > VIDEO_WAIT:
                out["video"] = True
            elif isinstance(v, tuple):
                out.update(done=False, why=v[1])
        return out

    def fx_done(self, ok, why=""):            # страница сама не вызывает; оставлено для совместимости с окном
        done(ok, why)

    def fx_close(self):
        try:
            FX_FILE.unlink()
        except OSError:
            pass
        w = self.window
        if w:
            import threading
            threading.Timer(0.05, w.destroy).start()
        return {"ok": True}


def _cover(window):
    """На весь экран с мышью, выше всего (уровень «щита» экрана + 1 — как карточка поверх полноэкранного Moonlight),
    на всех столах. Без рамки, чтобы macOS не прижимала окно под строку меню."""
    import AppKit
    import Quartz
    from PyObjCTools import AppHelper

    def go():
        w = window.native
        if w is None:
            return
        mouse = AppKit.NSEvent.mouseLocation()
        screen = next((sc for sc in AppKit.NSScreen.screens() if AppKit.NSPointInRect(mouse, sc.frame())),
                      AppKit.NSScreen.mainScreen())
        w.setStyleMask_(AppKit.NSWindowStyleMaskBorderless)
        w.setLevel_(Quartz.CGShieldingWindowLevel() + 1)
        w.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                                 | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
                                 | AppKit.NSWindowCollectionBehaviorIgnoresCycle)
        w.setHidesOnDeactivate_(False)
        w.setFrame_display_(screen.frame(), True)
        w.orderFrontRegardless()

    AppHelper.callAfter(go)


def main():
    """«app_main.py fx»: одно окно анимации, выход, когда оно закрылось. Уже открыто — ничего (подхватит fx.json)."""
    if sys.platform != "darwin":
        return
    with open(FX_LOCK, "a") as lock:
        try:
            if not filelock.try_lock(lock):
                return
        except OSError:
            return
        import webview.platforms.cocoa  # noqa: F401 — загрузить первым: он ставит обычный режим (см. gui.card_main)
        import AppKit
        import webview
        AppKit.NSApplication.sharedApplication().setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        api = FxApi()
        w = webview.create_window("VastGame", str(paths.HERE / "ui" / "launch.html"), js_api=api, width=900, height=600,
                                  frameless=True, easy_drag=False, on_top=True, hidden=True, background_color="#05070B")
        api.window = w
        w.events.loaded += lambda: _cover(w)
        webview.start()
