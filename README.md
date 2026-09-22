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
