# cliui.py — «ui» для терминала: ядро (core.py) говорит через него, а он печатает и спрашивает.
# У окна приложения будет свой ui с теми же методами.


def ask(question):
    """Вопрос да/нет в терминале; True — только если человек напечатал «да»."""
    try:
        import readline  # noqa: F401 — стрелки и Backspace в ответе (на Windows модуля нет)
    except ImportError:
        pass
    return input(f"{question} [да/нет]: ").strip().lower() in ("да", "yes", "y", "д")


class ConsoleUI:
    def say(self, text, level="info", key=None, **params):
        print(text, flush=True)   # терминал — всегда русский текст; key/params нужны окну

    def stage(self, key, text):
        print(text, flush=True)

    def archive(self, name, state, text, progress=None):
        pg = f" {progress['done'] * 100 // max(progress['total'], 1)}%" if progress else ""
        print(f"  {name:<28} {state:<5} {text}{pg}", flush=True)

    def confirm(self, question, key=None, **params):
        return ask(question)

    def tailscale(self, text):
        print("  " + text, flush=True)
