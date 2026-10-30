# VastGame

**English** · [Español](README.es.md) · [Русский](README.ru.md)

> **Beta.** People are already using it, and it is still changing. If something breaks, write in [Issues](https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/issues).

VastGame is for playing on a rented [Vast.ai](https://vast.ai) machine without setting that machine up again every time. You press a game in your Steam library, and the machine list opens. You pick the machine yourself and confirm the price. After the rental, Steam on the machine downloads the game and starts it, and Moonlight opens on its own. Saves, the Steam session, mods and programs stay in your Google Drive and come back on the next machine. Finish uploads all of that first and deletes the machine only after the upload is confirmed. Nothing is billed while no machine is running.

The app picks a different machine on its own only when the one you chose fails to start. It deletes that one and takes the next from your list that is no worse in GPU, price and ping. While your machine is starting normally, nothing is substituted for it.

![VastGame home screen](docs/home-en.jpg)

**[Download for Mac](https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/releases/latest/download/vastgame-mac.dmg)**
(Apple Silicon, M1 and newer)
·
**[Download for Windows](https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/releases/latest/download/VastGame-Setup.exe)**
(Windows 10/11, 64-bit)

The builds are not signed by Apple or Microsoft yet, so the system asks for permission on the first launch. How to allow it is in Installation below.

## A session

Setup comes first. The app asks, step by step, for a Vast.ai key, a Google sign-in and Tailscale. It installs Steam, Moonlight and Steam Link itself when they are missing.

You sign in to Steam on Steam’s own page, with a QR code or with a login and password. VastGame never sees the password. You can link several accounts, including a family library.

Pressing a game on the shelf or in the library opens the machine list. On the game card, before you rent, you choose With mods or Without mods. Machine without a game rents a desktop and does not launch a game.

You then pick a machine and confirm the price. The app rents it, restores what is in the cloud, signs in to the right Steam account, and Steam downloads and starts the game. Only one machine runs at a time. Another game on a machine that is already up is launched in Steam through Moonlight. To take a different machine, press Finish first.

## Library

The home screen shows a shelf of recent games. Full library lists every game on the linked accounts, even when the Steam profile is private. Family games are marked, and there is search and a filter by account.

The game card shows which account will launch it, whether mods are on, and what is already in the cloud. If the Steam sign-in has expired, the game list stays, and the account is marked so you can sign in again.

## The machine list

Each row shows the GPU, the hourly price, the country, the ping and the internet speed. Filters and sort order are remembered. Disk size is a slider from 50 to 1000 GB, and the hourly price includes that amount.

The Docker tab is the default. There are more machines, and they are usually ready in one to three minutes. The gamepad does not rumble, and Vast bills stream traffic separately. The Traffic filter keeps hosts where that traffic is cheap.

KVM is there when you want rumble or nearly free traffic. There are few of those machines, and a start more often takes five to ten minutes.

Until you have played on a particular machine, the ping is an estimate from the distance between your city and the host’s region. After the first session the app remembers a real measurement over Tailscale and shows that from then on.

If Vast has not brought the chosen machine up within ten minutes, or the screen or the encoder fails to start on Docker, the app deletes it and takes the next suitable one from your list. If there is none, you return to the list. Cancel start deletes the machine if it has already been rented.

## The stream

The default resolution is your monitor’s native resolution, and the bitrate is Auto, which Moonlight calculates. Both settings live under Stream settings below the launch button, and they are remembered.

Steam Link is the button next to it. The first time this account is not paired with your computer, the Steam Link window opens and VastGame asks for the four-digit PIN. It sends the code to Steam on the machine and stores the pairing in the cloud. If the client is missing, the Mac App Store opens, and on Windows the app installs Steam Link itself.

You can move around the VastGame window with a gamepad and the arrow keys. While Moonlight is bringing the stream up, the screen is covered by the game’s title card.

## Mods

What goes to the cloud is not the game itself. It is the difference between the game folder and a clean Steam install: files you added, changed or removed while the game was closed. Logs and cache are left out. Steam downloads the game again on every machine.

There is one switch, and it can be changed only before you rent. During a session it is locked, with the note that it changes before renting.

With mods, the archive from the cloud is applied on top of the clean game before launch. Changes in the folder after you leave the game are saved and come back on the next machine.

Without mods, you get a clean Steam install. The archive in the cloud stays where it is and is not applied, and new mods from this session are not saved. Game saves are still uploaded as usual.

To install mods, choose With mods and rent a machine. Wait until Steam has installed the game, then close it. Leave the folder alone while the game is running, because the snapshot of the clean install is taken right after installation. Put the files in the game folder. You can download them with Chrome on the machine’s desktop. The browser profile, passwords and bookmarks are not uploaded. When you leave the game or press Finish, the mods go to Google Drive. Next time Steam downloads a clean game again, and VastGame applies the mods on top. The home screen says it is applying mods while that happens.

Mod Organizer, Vortex and other managers are installed as programs, not as mod files. That is the next section.

If Steam updates the game during the same session, mods you added in that session are not uploaded, so old files are not laid onto the new version. On an already updated game they save as usual.

Mods are removed in Games in the cloud, in the mods group, with Remove. The file stays in the Google Drive trash for thirty days and can be restored. The game and its saves remain.

## Programs

This covers trainers, WeMod, Mod Organizer, SKSE, Mod Engine, XIVLauncher and anything else that should open beside the game or instead of it.

Where you put a program decides whether it comes back on the next machine.

| Where you put it | What is kept |
|---|---|
| In the Programs folder on the desktop, each program in its own folder | Always uploaded, and it will be on the next machine |
| Directly in the game folder | Kept with the mods, and only when you play With mods |
| Through Install into the game | Lands in the game’s Proton prefix and is kept with the saves |

The machine has to be running and the game already downloaded. Download the file with Chrome on the desktop and double-click the program, archive or installer. VastGame works out which game it belongs to, puts the file in Programs or installs it into the prefix, and asks whether to start it with the game. You can also do it by hand: make a folder inside Programs and put the program there. Within about half a minute it shows up in Programs of the game on the game card. If the list is empty, that same place tells you where to put files.

Start opens the program in the same Moonlight session. Alt+Tab switches to its window. A trainer needs the game to be running already. A mod manager can start in the prefix even while the game itself is closed.

With game turns on launching it together with the game, on this machine and on later ones.

Game launch makes Steam start the chosen program instead of the game’s exe. That is how SKSE, Mod Engine and XIVLauncher start. The Proton prefix, the saves and the account stay the same. It works in With mods, and the choice is remembered. Steam cannot be restarted while the game is already running, so the new launch takes effect after you leave the game or on the next machine.

Install into the game runs the installer once inside that game’s Proton. The game has to be closed and already installed in Steam. The installer window is in Moonlight. If the game should then start through that program, choose it under Game launch. What was installed is stored with the saves.

The free edition of FINAL FANTASY XIV has its own switch, Russian translation. When it is on, the machine installs XIVLauncher, Dalamud, Penumbra and the translation, and on later rentals it updates the translation when a new version is out. The game starts through XIVLauncher. You sign in to the Square Enix account yourself. All of this is stored with the saves. When the switch is off, the game uses the normal Square Enix launcher, and a translation already in the cloud is not deleted.

Keep the game in the cloud, next to it, is worth turning on for games that have their own launcher, such as FF14 and other MMOs. Files the launcher downloaded then come along to the next machine, and you do not download the game from scratch. A normal Steam game gains nothing from that switch. Mods are stored separately either way.

## Games that are not from Steam

The machine desktop has a Games folder. That is where a game bought outside Steam goes. When a new folder appears, VastGame asks, above every window, whether to take it to the cloud. Yes uploads it to Google Drive and downloads it on later machines. No leaves it only on this machine, and it disappears with the machine. If you do not answer, a new game is uploaded anyway. Something already in the cloud is not removed just because you delete the folder on the machine. You remove it in Games in the cloud, and it again sits in the Drive trash for thirty days.

## Saves

Saves go to the cloud both while you play and when you press Finish. That includes Steam saves and files the game writes into its own folder. On the next machine they are already there before launch.

You can delete a save in Games in the cloud. If there is no copy in Steam Cloud, the game will not bring it back, and the app warns you about that separately.

Signing out of a Steam account on the machine removes that account’s local data from the cloud session, so it does not come back next time. The save archives of the games are left alone.

## When you finish

Finish closes the game, uploads everything that should be kept, waits for confirmation, and only then deletes the machine. If the upload is not confirmed, the machine stays, and Vast keeps billing.

If the Mac shuts down or the connection drops after you have pressed the button, the machine finishes the upload itself and deletes itself. When the connection returns, VastGame shows that the session is already closed.

Delete without uploading is in the stream settings and asks twice. The machine is deleted immediately, and anything that had not reached the cloud is gone.

## What is in the cloud

Games in the cloud shows what is already in your Google Drive and how much space it takes. That is games, saves, mods, programs and launcher files. Steam’s own files, such as Proton and the shared libraries, are folded away on their own. They are needed to start games. If you delete them, Steam downloads them again.

Remove does not erase a file at once. It stays in the Drive trash for thirty days. If a machine is running, the deletion goes through it, so it does not upload the file back. If no machine is running, the file goes straight to the trash.

## What you need

You need a Mac with Apple Silicon, M1 or newer, or 64-bit Windows 10/11. You need a [Vast.ai](https://vast.ai) account with money on it. Five to ten dollars is enough to start. You need a Google account for the saves, mods and programs. A separate account for games is more convenient than your main one. You need [Tailscale](https://tailscale.com). It is free, and you can sign in with Google. Your own key is optional. You approve the first machine once, from a link in the browser, and after that it comes up from the saved identity. A connection of 30–50 Mbit/s or better is the practical floor, and it is worth keeping the ping under 80–100 ms.

Top up only opens Vast’s billing page. The app never sees the card and never charges it.

## What it costs

The app itself is free. You pay the machine’s owner on Vast.ai for the time it is rented. The owners set the prices. Right now they are roughly these.

| GPU | Price per hour |
|---|---|
| RTX 3060 | $0.07–0.15 |
| RTX 3080 | $0.13–0.25 |
| RTX 4090 | $0.40–0.60 |

On the Docker tab, traffic is added to the hourly price. The Traffic filter caps what that traffic may cost. What was actually charged is in History, from Vast’s bills.

## Installation

On a Mac, download [vastgame-mac.dmg](https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/releases/latest/download/vastgame-mac.dmg), open it and drag VastGame to Applications. There is no Apple signature, so the system will refuse the app the first time. Open System Settings, go to Privacy & Security, and at the bottom press Open Anyway. That is once. Then go through setup in the window.

On Windows, download [VastGame-Setup.exe](https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/releases/latest/download/VastGame-Setup.exe) and run it. SmartScreen will warn about an unknown publisher. Press More info, then Run anyway. WebView2 is usually already installed on Windows 11. Setup from there is the same as on a Mac.

After that the app updates itself. The home screen shows that a version is available, with an Update button next to it. The file is checked against a signature, and an update that does not match is not installed.

## Data

VastGame has no server of its own and collects no statistics. Everything runs on your computer. Keys are kept in the macOS Keychain or in Windows Credential Manager. In Google Drive the app can see only the folder it created.

A rented machine is someone else’s computer, and the host’s owner can reach it while it is running. Do not leave anything personal on it. For games it is calmer to use a separate Google account and, if you would rather not use your main one, a separate Steam account.

More of this is in the [privacy policy](https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/blob/main/PRIVACY.md).

## License

The files in this repository are under the [MIT](https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/blob/main/LICENSE) license. That is the machine script, the Docker image and these documents. The Mac and Windows application is distributed on the same terms, and its source code is not in this repository. The license allows use, copying and modification. The application and the script are provided as is, without warranty.

---

---

## The script on the machine

Below is the existing guide to `wolf-setup.sh`, for people who rent a machine by hand, without the application.

# wolf-setup.sh

**English** · [Español](README.es.md) · [Русский](README.ru.md)

A cloud gaming PC on a rented Vast.ai GPU that doesn't disappear with the instance.

Rent a machine, play, destroy it. Next time — on a different machine, maybe a week later — Moonlight connects to the same address, Steam is still signed in, and your saves are where you left them. The script turns a fresh KVM instance into a desktop with Steam and Sunshine, joins it to your Tailscale network under a fixed name, restores your Steam session, settings, Proton prefixes and saves from Google Drive, and keeps uploading changes while you play.

```
   Vast.ai (KVM, NVIDIA)                  Google Drive            your devices
┌─────────────────────────┐          ┌──────────────────┐      ┌──────────────────┐
│  Steam + Proton         │◄────────►│  identity        │      │  Moonlight       │
│  Sunshine ──────────────┼──────────┼─ steam-state     │      │  + Tailscale     │
│  wolf (sync engine)     │          │  steam-cache     │      │  (phone, PC,     │
│  Tailscale ─────────────┼──────────┼─ pfx--<appid>    │      │   laptop, TV)    │
└──────────┬──────────────┘          │  game--<folder>  │      └────────▲─────────┘
           │                         └──────────────────┘               │
           └──────────────── Tailscale (all stream traffic) ────────────┘
```

---

## Who this is for

This is not a one-click service. You'll create accounts on three sites, copy two keys into Vast's settings, and now and then open a terminal on the instance. If you've ever set up Plex, a home server or a Linux desktop, you'll manage. If you just want to press Play, GeForce NOW and Boosteroid are built for that.

What you get in exchange: your own Steam library, mods and non-Steam games, on a GPU you pay for by the hour and only while you play.

---

## What it does

- **A fixed address.** Tailscale's node state lives in the cloud, so every new instance comes up as the same node: same name, same IP. You add the PC to Moonlight once.
- **Steam stays signed in.** `machine-id`, `loginusers.vdf`, `ConnectCache`, `ssfn*` and `userdata/` are preserved, so the session survives a change of machine. Offline mode works too: the license cache (`appcache`) goes to the cloud as well.
- **Incremental sync.** Only what changed is uploaded. A game save is an archive of a few kilobytes, not a re-upload of the whole prefix.
- **Upload the moment you quit.** A background service notices when a game closes and uploads its saves within about 10 seconds, then shows a desktop notification.
- **Resolution follows your device.** The desktop starts at 1920×1200 on a GPU with no monitor attached, switches to your device's native resolution when Moonlight connects, and switches back when you disconnect.
- **Isolation.** Sunshine and the status page accept connections only over Tailscale. Secrets never reach the games or the process list.

---

## Why not just keep a disk on Vast?

Vast bills storage for every hour an instance exists, running or stopped — typically $0.09–0.20 per GB per month, some hosts far more. A 500 GB disk for a game library costs roughly $45–100 a month before you've played a minute. Vast's own Cloud Sync doesn't work on KVM instances, and gaming needs KVM: a container can't run its own display server on the GPU.

This setup keeps on Google Drive only what can't be re-downloaded — saves, Proton prefixes, the Steam session, the network identity. That's usually a few gigabytes, often within Google's free 15 GB. You destroy the instance when you're done and pay only for the hours you play: on an RTX 3060 at about $0.11 an hour, four hours every day comes to about $13.60 a month all-in, a couple of evenings a week to about $2–3.

The trade-off: every new instance re-downloads your Steam games, usually in 10–20 minutes.

---

## Quick start

About half an hour the first time. You'll need:

| | |
|---|---|
| a **Vast.ai** account | with some credit |
| a **Tailscale** account | the free tier is enough |
| a **Google** account for Drive | a separate one, not your main — see [Security](#security) |
| **rclone** on your computer | once, to get the Drive token |
| **Tailscale** and **Moonlight** on every device you'll play from | phone, laptop, TV box |

### 1. Tailscale

1. In [login.tailscale.com/admin/settings/keys](https://login.tailscale.com/admin/settings/keys) click **Generate auth key**:
   - **Reusable — on.** The key is used on every launch.
   - **Ephemeral — off.** Ephemeral nodes are deleted when they go offline, and you'd lose the fixed name.
   - Expiration — up to 90 days. When it runs out, generate a new one and update the variable in Vast.

   The `tskey-auth-...` string is your `TAILSCALE_AUTHKEY`.
2. Under **DNS**, enable **MagicDNS**. The same page shows your tailnet name, something like `tail3d42c9.ts.net`. You'll need it for Moonlight.
3. Install Tailscale on every device you'll play from and sign in with the same account. Moonlight reaches the instance only through Tailscale.

### 2. Google Drive token

Install rclone ([rclone.org/install](https://rclone.org/install/); on macOS `brew install rclone`, on Windows `winget install Rclone.Rclone`) and run:

```bash
rclone config
```

- `n` for a new remote, name it **gdrive**
- storage type: **drive**
- `client_id` and `client_secret`: just press Enter for both
- scope: **1** (Full access)
- press Enter through the rest; answer **y** to `Use auto config?` — a browser opens; sign in with the Google account you're using for this and allow access

Then:

```bash
rclone config show gdrive
```

In the `token = {...}` line find `"refresh_token":"1//0...."`. Copy what's inside the quotes, without the quotes — that's your `RCLONE_REFRESH_TOKEN`. It's long and starts with `1//`.

You don't need to create anything on Drive: on first launch the script creates a `vastai-cloud-games` folder at the root of My Drive and works only inside it.

Leaving `client_id` empty uses rclone's own client. It's slower than a client of your own, but plenty for this setup, and its tokens don't expire. Read [Your own Google OAuth client](#your-own-google-oauth-client) before changing that.

### 3. Save both keys in Vast

On Vast, open **Settings** and scroll to **Environment Variables**. Add:

| Key | Value |
|---|---|
| `RCLONE_REFRESH_TOKEN` | the token from step 2 |
| `TAILSCALE_AUTHKEY` | the key from step 1 |

Press **+** after each, then **Save Edits**. These are account-wide and reach every instance you rent. Never put them into a template.

### 4. Rent from the template

**[Open the template on Vast.ai](https://cloud.vast.ai?ref_id=688160&template_id=49faada2e6e7fe3e15bef0ea1999420a)**

Pick a machine:

- **an NVIDIA gaming card** — RTX or GTX, which have the NVENC encoder. Datacenter GPUs such as the A100 and H100 have no display output and won't work.
- **close to you** — latency depends on distance.
- **enough disk** for the games you'll install, plus about 20 GB.
- **no bandwidth charge.** Hover over the price: the breakdown should list only GPU and disk. Most hosts don't charge for traffic, but it's up to each host, and a stream uses roughly 10–20 GB an hour.

Then **Rent**. Installation takes a few minutes.

For the curious, this is everything the template runs on start. It downloads the script and executes it:

```bash
#!/bin/bash
URL='https://gist.githubusercontent.com/WetFoxx/792f2333664d95c442fa451063b16436/raw/787acea52dc0d909adbf76bdf847b15a28ace4a9/gistfile1.txt'
for _ in {1..30}; do
  curl -fsSL "$URL" -o /root/setup.sh && [ -s /root/setup.sh ] && break
  sleep 5
done
exec bash /root/setup.sh
```

The URL points at one fixed revision of the script, so what runs can't change behind your back.

### 5. Connect Moonlight

Once the instance is up, a `vastai-gaming` node appears under [Machines](https://login.tailscale.com/admin/machines) in the Tailscale console. While you're there, open **⋯ → Disable key expiry** on it, or it will drop off in six months.

The node's full name is its name plus your tailnet name:

```
vastai-gaming.tail3d42c9.ts.net
```

If it shows up as `vastai-gaming-2` or similar, those numbers come from earlier nodes with the same name. Delete the stale ones and use the exact name you see.

In Moonlight, add the PC **manually, by that name — not by IP address.** When it asks for a PIN, open `https://vastai-gaming.tail3d42c9.ts.net:47990` in a browser. The certificate warning is expected. The first time, Sunshine asks you to create a username and password; then enter the PIN in the PIN section.

The pairing is saved to the cloud, so you do this once.

Set Moonlight's resolution to **Native** to get your screen's resolution; the desktop adapts to whatever the client asks for.

### 6. Set up Steam once

1. Sign in to Steam.
2. Install your games.
3. Switch Steam to **offline mode** (Steam → Go Offline). Without it, every new instance asks for a Steam Guard code.
4. Play for 10–15 minutes so everything reaches the cloud.

That's the whole setup. From now on it's: rent from the template, connect, play.

---

## Every session

Rent from the template, wait a few minutes, connect Moonlight. Your Steam games re-download each time, usually in 10–20 minutes; saves and settings are already there.

**When you're done: quit the game, wait for the desktop notification, then destroy the instance.** The notification — titled «Сохранения в облаке», the script's messages are in Russian — appears only after your saves have reached Drive, so it is your confirmation.

Destroy, don't stop: a stopped instance keeps billing for its disk.

Steam settings and the offline cache sync every five minutes. If you changed something right before leaving, open a terminal on the instance and run `sudo wolf shutdown` first: it closes Steam and uploads everything immediately.

Other commands, from a terminal on the instance or over SSH:

```bash
sudo wolf shutdown          # close Steam and upload everything right now
sudo wolf state             # upload state right now
sudo wolf games             # upload non-Steam games right now
sudo wolf restore NAME...   # restore archives (FORCE=1 to restore even if current)
sudo wolf display           # re-apply the display config (restarts X — not mid-game)
sudo wolf firewall          # re-apply port isolation
sudo wolf-res 2560 1440 60  # change resolution by hand; no arguments = back to base
```

Logs and status:

| | |
|---|---|
| `/var/log/wolf-setup.log` | installation; environment problems are marked `!!!` |
| `/var/log/wolf.log` | sync, network, display |
| `/var/log/wolf-res.log` | resolution changes |
| `http://vastai-gaming.<tailnet>.ts.net:8099` | status page, over Tailscale only; `/json` for the same data machine-readable |

---

## Advanced

### Your own Google OAuth client

Only worth it if you turn on `SYNC_STEAM_GAMES=1` or sync large non-Steam games: rclone's shared client is rate-limited, and a client of your own is faster for big transfers. Instructions: [rclone.org/drive/#making-your-own-client-id](https://rclone.org/drive/#making-your-own-client-id).

**You must publish it.** A new client starts in **Testing** status, and Google gives Testing clients refresh tokens that expire after 7 days — sync would quietly stop a week after setup. In Google Cloud Console open the OAuth consent screen (in the newer console: Google Auth Platform → Audience) and set the publishing status to **In production**. No verification is needed for personal use; when you sign in, Google shows an "unverified app" warning once — choose Advanced and continue.

Then repeat step 2 entering your `client_id` and `client_secret`, and add `RCLONE_CLIENT_ID` and `RCLONE_CLIENT_SECRET` to Vast's Environment Variables next to the new token.

### ZeroTier

If you want a second network, put its Network ID from [my.zerotier.com](https://my.zerotier.com) in `ZT_NETWORK_ID`. Without that variable ZeroTier is neither installed nor started. Tailscale alone is enough for streaming.

### Your own template

The script is bigger than the 16 KB Vast allows in the on-start field, which is why the template holds only a loader. For a template of your own: image `docker.io/vastai/kvm:ubuntu_desktop_22.04`, the loader from step 4 in **On-start script**, disk sized for your games. If you host your own copy of the script, point the URL at a fixed revision — a release tag or a commit — rather than a branch; otherwise every change ships instantly to every instance that starts.

### Variables

All of these go in Vast's Environment Variables and override the script's defaults.

**Secrets**

| | |
|---|---|
| `RCLONE_REFRESH_TOKEN` | Google Drive token |
| `TAILSCALE_AUTHKEY` | the `tskey-auth-...` key |
| `RCLONE_CLIENT_ID`, `RCLONE_CLIENT_SECRET` | your own Google OAuth client |
| `ZT_NETWORK_ID` | ZeroTier network; empty means no ZeroTier |

**Behaviour**

| | Default | |
|---|---|---|
| `STEAM_FREEZE` | `1` | don't let the Steam client update itself on launch |
| `SYNC_STEAM_GAMES` | `0` | sync the Steam games themselves |
| `AUTO_RES` | `1` | manage resolution |
| `RES` | `1920x1200` | base desktop resolution |
| `STEAM_LANG` | empty | force a Steam language (`english`, `german`, …); empty means Steam uses its own setting |
| `TAILSCALE_SSH` | `1` | bring up Tailscale SSH — see [Security](#security) |
| `NOTIFY_SAVES` | `1` | notify when saves are uploaded |
| `NOTIFY_SEC` | `8` | how long a notification stays on screen, seconds |

**Fine tuning**

| | Default | |
|---|---|---|
| `R_REMOTE` | `gdrive:vastai-cloud-games` | folder on Drive |
| `TAILSCALE_HOSTNAME` | `vastai-gaming` | node name |
| `TAILSCALE_EXTRA_ARGS` | — | extra `tailscale up` flags |
| `GAMES_DIR` | `~/Downloads/Games` | non-Steam games |
| `STATE_SYNC_MIN` | `5` | state sync interval, minutes |
| `GAMES_SYNC_MIN` | `15` | game sync interval, minutes |
| `PAR` | `4` | archives in parallel |
| `ZSTD_STATE` / `ZSTD_GAMES` | `3` / `1` | compression level |
| `WOLF_PORT` | `8099` | status page port |
| `DESKTOP_USER` | uid 1000 | desktop user |

---

## What is synced, and when

| Archive | Contents | Uploaded |
|---|---|---|
| `identity` | `machine-id`, Tailscale state, ZeroTier keys, Sunshine certificates | every 5 min |
| `steam-state` | Steam login, settings, `userdata/` | every 5 min |
| `steam-client` | the Steam client without caches or games | every 5 min, once it settles |
| `steam-cache` | `appcache` for offline mode | once unchanged across two passes, or at shutdown if Steam is closed |
| `pfx--<appid>` | the Proton prefix, which is where saves live | every 5 min, and right after you quit the game |
| `game--<folder>` | a non-Steam game, a subfolder of `~/Downloads/Games` | every 15 min, and after you quit |
| `sgame--<folder>` | a Steam game, only with `SYNC_STEAM_GAMES=1` | every 15 min, and after you quit |

Steam games themselves are **not** synced by default: re-downloading from Steam is usually faster than from Drive and doesn't eat your Google quota (750 GB of uploads per day). Turn on `SYNC_STEAM_GAMES=1` only for games you've modified or that are gone from the store — and set up [your own OAuth client](#your-own-google-oauth-client) if you do.

Put non-Steam games in subfolders of `~/Downloads/Games`; each subfolder becomes its own archive.

---

## Security

What the script does:

- Sunshine's ports (47984–48010, including the 47990 web UI) and the status page accept traffic only from the `tailscale0` interface and loopback; everything else is dropped. The INPUT policy itself is untouched, so SSH and Vast's own management keep working.
- The Drive token lives only in `/root/.config/rclone/rclone.conf` (600, root); the Tailscale key lives in `/etc/wolf/tskey` (600, root) and is passed as `file:`, so it never appears in the process list. Both are removed from `/etc/environment`, and Steam and games start through `env -i`, inheriting no secrets.
- The `identity` archive is never unpacked into `/`. It is downloaded to a temporary file, checked for absolute paths and `..`, extracted into a staging directory, every symlink is verified with `realpath`, and only a fixed allowlist of files is copied into place. User archives are always extracted as the unprivileged desktop user, never as root.
- Uploads to Drive are atomic: the file is written as `NAME.part` and replaces the live one only on a successful `moveto`. Overwritten versions go to Drive's trash, so they can be recovered.

What is up to you:

- **Use a separate Google account.** The Drive token is readable by anything running on the instance, and its scope is full access to Drive. A separate account limits the damage to the games folder.
- **The instance is rented from a stranger.** The machine's owner has root on the host, and your Steam session and Drive token will sit there. That's not paranoia, it's what renting means — decide what you're willing to put there.
- **Tailscale SSH is on by default.** Any device on your tailnet gets root on the instance. Convenient on a personal tailnet; set `TAILSCALE_SSH=0` if you share it.
- **Never share the Drive folder.** The archives contain a live Steam session.

---

## Limitations

- **One instance at a time.** Instances share the same archives and Tailscale identity. The script notices the version mismatch and stops uploading, but don't rely on it.
- **Anti-cheat games** (EAC, BattlEye) may refuse to run in a virtual machine, or get your account banned. Your risk.
- **NVIDIA only.** On other GPUs resolution management switches itself off; everything else works.
- Tested on `docker.io/vastai/kvm:ubuntu_desktop_22.04` with the SDDM display manager. Other images may need adjustments.
- The script's log messages, notifications and status page are in Russian.

---

## Troubleshooting

**Moonlight can't find the PC; the status page won't open.** The device you're using isn't on Tailscale, or MagicDNS is off. Check that Tailscale is running there and signed in to the same account.

**Sync stopped working about a week after setup.** You're using your own Google OAuth client in Testing status — see [Your own Google OAuth client](#your-own-google-oauth-client). Either publish it and get a new token, or delete `RCLONE_CLIENT_ID` and `RCLONE_CLIENT_SECRET` from Vast and get a new token with rclone's own client.

**Steam loads forever.** Offline mode is on but there's no license cache yet. The script detects this and starts Steam online instead, with a hint on the status page. Sign in, switch back to offline mode, and let the instance run 10–15 minutes.

**Moonlight asks for a PIN on every new instance.** The `identity` archive isn't being restored. Check `grep identity /var/log/wolf.log` for an `ok` line.

**Nodes with numeric suffixes keep piling up in Tailscale.** Same cause: the Tailscale state isn't coming back from the cloud, so each launch registers a new node. Fix `identity` first, then delete the extras.

**Black screen with only a cursor.** The client's resolution didn't match what Sunshine captures. Set Moonlight to exactly 1920×1200; if the picture appears, the resolution switch is at fault — see `/var/log/wolf-res.log`.

**The log says `tailscale0` wasn't found.** Tailscale didn't come up, so Sunshine's ports and the status page were left open. Check the key and run `tailscale status`.

**Sync stopped with «в облаке другая версия» ("another version in the cloud").** A second instance was running. Decide which copy is newer and run `sudo wolf restore NAME` on the machine you're keeping.

---

## License

MIT.
