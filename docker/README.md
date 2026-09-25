# vastgame-desktop (experimental)

Docker image for cloud gaming on a regular (non-KVM) Vast.ai instance: XFCE desktop, Steam + Proton,
Sunshine (NVENC + NvFBC), PulseAudio, Tailscale (userspace), gamepad without `/dev/uinput` (`vgpad/`).
Used by the vastgame app ("Docker, experimental" tab). The KVM setup (`wolf-setup.sh`) stays the main one.

Образ для облачной игры на обычном (не KVM) Docker-инстансе Vast.ai: рабочий стол XFCE, Steam и Proton,
Sunshine (NVENC + NvFBC), звук, Tailscale без /dev/net/tun, геймпад без /dev/uinput (`vgpad/`).
Пользователь `user` (uid 1000, `/home/user`) — как на KVM, чтобы архивы в облаке были общими.

- `Dockerfile` — сборка (GitHub Actions → `ghcr.io/wetfoxx/vastgame-desktop`).
- `rootfs/opt/vastgame/entrypoint.sh` — запуск: Tailscale, драйвер под хост (`nvidia-setup.sh`), экран
  (`display-setup.sh`, `wolf-res`), звук, геймпад (`vgpadd.py`), Sunshine (`sunshine-start.sh`), XFCE, Steam.
- `rootfs/usr/bin/steam`, `steam-patch.sh` — Steam без user namespaces (заплатки ставятся сами).
- `vgpad/` — виртуальный геймпад (LD_PRELOAD) и посредник.

Environment: `RES`, `TS_HOSTNAME`, `TAILSCALE_AUTHKEY`, `SUNSHINE_PASSWORD`, `STEAM_AUTOSTART`,
`VASTGAME_DEBUG_TOKEN` (see `entrypoint.sh`). Requires an NVIDIA driver ≥ 580 on the host (NVENC in Sunshine).
