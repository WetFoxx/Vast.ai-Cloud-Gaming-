# vastgame-desktop — игровой рабочий стол для обычного (не KVM) Docker-контейнера Vast.ai.
# Всё, что проверено разведкой 2026-09-25 (ветка docker-poc, poc/RESULTS.md): Xorg на GPU хоста, Tailscale без
# /dev/net/tun, Sunshine с NVENC и NvFBC, звук, геймпад без /dev/uinput (vgpad), Steam и Proton без user
# namespaces (заплатки), рабочий стол XFCE. Пользователь user (uid 1000, /home/user) — как на KVM-машинах,
# чтобы архивы в облаке (пути внутри них) были общими.
# Под драйвер конкретного хоста (модуль X, 32-битные библиотеки, NvFBC) — при запуске, nvidia-setup.sh.

# ---------------------------------------------------------------- vgpad (64 и 32 бита)
FROM ubuntu:24.04 AS vgpad
RUN apt-get update -qq && apt-get install -yq --no-install-recommends gcc gcc-multilib libc6-dev libc6-dev-i386 \
    && rm -rf /var/lib/apt/lists/*
COPY vgpad/vgpad.c /src/vgpad.c
RUN mkdir -p /out/x86_64 /out/i386 \
    && F="-shared -fPIC -O2 -Wall -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0" \
    && gcc $F -o /out/x86_64/libvgpad.so /src/vgpad.c -ldl -lpthread \
    && gcc -m32 $F -o /out/i386/libvgpad.so /src/vgpad.c -ldl -lpthread

# ---------------------------------------------------------------- образ
FROM ubuntu:24.04
LABEL org.opencontainers.image.source="https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-" \
      org.opencontainers.image.description="vastgame-desktop: cloud gaming desktop for Vast.ai Docker instances"
ARG SUNSHINE_VERSION=2026.914.233613
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 \
    NVIDIA_DRIVER_CAPABILITIES=all NVIDIA_VISIBLE_DEVICES=all

# Рабочий стол, звук, 32-битные библиотеки для Steam, инструменты (без хранителя экрана и блокировки)
RUN dpkg --add-architecture i386 && apt-get update -qq && apt-get install -yq --no-install-recommends \
      xserver-xorg-core x11-xserver-utils x11-utils x11-apps xauth mesa-utils netpbm \
      xfce4-session xfwm4 xfce4-panel xfdesktop4 xfce4-settings xfce4-terminal thunar xfconf \
      adwaita-icon-theme hicolor-icon-theme fonts-dejavu-core fonts-liberation dbus dbus-x11 at-spi2-core \
      libnotify-bin xfce4-notifyd \
      pulseaudio pulseaudio-utils \
      curl ca-certificates gnupg python3 procps iproute2 pciutils psmisc less nano zstd unzip xz-utils file \
      zenity xdg-utils libibus-1.0-5 libevdev2 libsdl2-2.0-0 evtest \
      libc6:i386 libgl1:i386 libgl1-mesa-dri:i386 libegl1:i386 libgbm1:i386 \
    && rm -rf /var/lib/apt/lists/*

# Пользователь как на KVM: user, uid 1000, основная группа users (в образе Ubuntu uid 1000 занят ubuntu)
RUN userdel -r ubuntu 2>/dev/null; useradd -m -u 1000 -g users -G audio,video -s /bin/bash user

# Sunshine (закреплённая версия: с ней проверены связка, NvFBC и vgpad)
RUN curl -fsSL -o /tmp/sunshine.deb \
      "https://github.com/LizardByte/Sunshine/releases/download/v${SUNSHINE_VERSION}/sunshine_${SUNSHINE_VERSION}-1%2Bubuntu24.04_amd64.deb" \
    && apt-get update -qq && apt-get install -yq /tmp/sunshine.deb \
    && rm -rf /tmp/sunshine.deb /var/lib/apt/lists/*

# Steam: запускатель + библиотеки из репозитория Valve (иначе steamdeps ждёт sudo в xterm).
# /usr/bin/steam (его зовут меню, Big Picture, Sunshine) отводим — на его место обёртка с заплатками (rootfs/usr/bin/steam).
RUN curl -fsSL -o /tmp/steam.deb https://cdn.akamai.steamstatic.com/client/installer/steam.deb \
    && apt-get update -qq && apt-get install -yq /tmp/steam.deb \
    && apt-get update -qq && apt-get install -yq --no-install-recommends steam-libs-amd64:amd64 steam-libs-i386:i386 \
    && rm -rf /tmp/steam.deb /var/lib/apt/lists/* \
    && dpkg-divert --add --rename --divert /usr/bin/steam.real /usr/bin/steam \
    && mkdir -p /opt/vastgame/real && ln -sfn /usr/lib/steam/bin_steam.sh /opt/vastgame/real/steam

# Tailscale (официальный репозиторий) и rclone (официальный установщик)
RUN curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg -o /usr/share/keyrings/tailscale-archive-keyring.gpg \
    && curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list -o /etc/apt/sources.list.d/tailscale.list \
    && apt-get update -qq && apt-get install -yq tailscale \
    && curl -fsSL https://rclone.org/install.sh | bash \
    && rm -rf /var/lib/apt/lists/*

COPY --from=vgpad /out/x86_64/libvgpad.so /usr/lib/x86_64-linux-gnu/libvgpad.so
COPY --from=vgpad /out/i386/libvgpad.so /usr/lib/i386-linux-gnu/libvgpad.so
COPY vgpad/vgpadd.py /opt/vastgame/vgpadd.py
COPY rootfs/ /

# Настройки XFCE по умолчанию (без вопроса о панели при первом входе, без композитора — меньше задержка),
# папка игр как на KVM (~/Downloads/Games), ярлык на неё на рабочем столе
RUN chmod 755 /opt/vastgame/*.sh /usr/local/bin/wolf-res /usr/bin/steam \
    && ln -sfn /usr/bin/steam /usr/games/steam \
    && mkdir -p /home/user/.config/xfce4/xfconf/xfce-perchannel-xml /home/user/Downloads/Games /home/user/Desktop \
    && cp /etc/xdg/xfce4/panel/default.xml /home/user/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml \
    && cp /opt/vastgame/xfwm4.xml /home/user/.config/xfce4/xfconf/xfce-perchannel-xml/xfwm4.xml \
    && ln -sfn /home/user/Downloads/Games /home/user/Desktop/Games \
    && chown -R user:users /home/user

ENTRYPOINT ["/opt/vastgame/entrypoint.sh"]
