# vastgame-desktop

Docker image for cloud gaming on a regular (non-KVM) Vast.ai instance: XFCE desktop, Steam + Proton,
Sunshine (NVENC + NvFBC), PulseAudio, Tailscale (userspace), gamepad without `/dev/uinput` (`vgpad/`).
Used by the vastgame app ("Docker" tab — the main mode since 0.3.1; KVM via `wolf-setup.sh` is the other one).

Образ для облачной игры на обычном (не KVM) Docker-инстансе Vast.ai: рабочий стол XFCE, Steam и Proton,
Sunshine (NVENC + NvFBC), звук, Tailscale без /dev/net/tun, геймпад без /dev/uinput (`vgpad/`).
Пользователь `user` (uid 1000, `/home/user`) — как на KVM, чтобы архивы в облаке были общими.

- `Dockerfile` — сборка (GitHub Actions → `ghcr.io/wetfoxx/vastgame-desktop`).
- `rootfs/opt/vastgame/entrypoint.sh` — запуск: Tailscale, драйвер под хост (`nvidia-setup.sh`), экран
  (`display-setup.sh`, `wolf-res`), звук, геймпад (`vgpadd.py`), Sunshine (`sunshine-start.sh`), XFCE, Steam.
- `rootfs/usr/bin/steam`, `steam-patch.sh` — Steam без user namespaces (заплатки ставятся сами).
- `vgpad/` — виртуальный геймпад (LD_PRELOAD) и посредник.
- `rootfs/usr/bin/google-chrome-stable` — Google Chrome (0.4.4) с флагами для контейнера: без песочницы
  (в контейнере её не включить), без `/dev/shm`. Профиль браузера в облако не уезжает.
- **Агент (v4.0):** с `WOLF_SCRIPT_URL` образ поднимает драйвер, экран, звук, геймпад и рабочий стол, а
  Tailscale, Sunshine, Steam и облако — `wolf-setup.sh` с `WOLF_MODE=docker`: тот же агент и те же архивы
  в Google Drive, что на KVM (`tailscaled-start.sh` — Tailscale без /dev/net/tun). Заплатки Steam условные
  (переменная `VG` из обёртки `/usr/bin/steam`): файлы Steam уезжают в общее с KVM облако и там ведут себя
  как оригинал. Программы пользователя стартуют с чистым окружением (`env -i`), как на KVM.

Environment: `WOLF_SCRIPT_URL` (agent mode), `RES`, `TS_HOSTNAME`, `TAILSCALE_AUTHKEY`, `SUNSHINE_PASSWORD`,
`STEAM_AUTOSTART`, `VASTGAME_DEBUG_TOKEN` (see `entrypoint.sh`). Requires an NVIDIA driver ≥ 535 on the host: Sunshine 2026 (CUDA 13) for drivers ≥ 580, Sunshine 2025.924 (CUDA 12.9, `/opt/sunshine-cuda12`) for older ones — `sunshine-start.sh` picks one.
