# wolf-setup.sh

A cloud gaming PC on a rented Vast.ai GPU that doesn't disappear along with the instance.

The script turns a fresh KVM machine into a desktop with Steam and Sunshine, brings it up on your Tailscale network under the same node name every time, and restores your Steam login, settings, Proton prefixes and saves from Google Drive — then keeps pushing changes back to the cloud while you play. Destroy the instance, rent a different machine a week later, and Moonlight connects to the same address, Steam doesn't ask for a password, and your saves are where you left them.

```
   Vast.ai (KVM, NVIDIA)                  Google Drive            your devices
┌─────────────────────────┐          ┌──────────────────┐      ┌──────────────────┐
│  Steam + Proton         │◄────────►│  identity        │      │  Moonlight       │
│  Sunshine ──────────────┼──────────┼─ steam-state     │      │  (phone, PC,     │
│  wolf (sync engine)     │          │  steam-cache     │      │   laptop, TV)    │
│  Tailscale ─────────────┼──────────┼─ pfx--<appid>    │      │        ▲         │
└──────────┬──────────────┘          │  game--<folder>  │      └────────┼─────────┘
           │                         └──────────────────┘               │
           └──────────────── Tailscale (all stream traffic) ────────────┘
```

---

## What it does

- **A stable address.** Tailscale's node state lives in the cloud, so a new instance comes up as the same node: same name, same IP. You add the PC to Moonlight once.
- **Steam without logging in again.** `machine-id`, `loginusers.vdf`, `ConnectCache`, `ssfn*` and `userdata/` are preserved, so the session survives a change of instance. Offline mode works too: the license cache (`appcache`) goes to the cloud as well, without which offline Steam spins on the splash screen forever.
- **Incremental sync.** Manifests are compared and only the delta is uploaded. A save in a game is an archive of a few kilobytes, not a re-upload of the whole prefix.
- **Upload right after you quit a game.** A separate service watches running games and uploads their saves about 10 seconds after they close, without waiting for a timer. A desktop notification confirms it.
- **Resolution follows the client.** A base mode of 1920×1200 on a GPU with no monitor attached (generated EDID plus `xorg.conf`), and when Moonlight connects, the resolution switches to the device's native one and switches back on disconnect.
- **Isolation.** Sunshine's ports and the status page accept traffic only from the Tailscale interface; everything else is dropped. Secrets never reach the games' environment or the process list.

---

## What you'll need

| | |
|---|---|
| A **Vast.ai** account | able to rent a KVM instance |
| A **Google** account | for Drive; use a separate one, not your main — see [Security](#security) |
| A **Tailscale** account | the free tier is enough |
| **Moonlight** | on the devices you'll play from |
| **rclone** locally | once, to obtain the Drive token |

Picking an instance:

- image `docker.io/vastai/kvm:ubuntu_desktop_22.04` — it has to be KVM, not a container;
- **NVIDIA** with an NVENC hardware encoder, so a consumer RTX/GTX card. Datacenter accelerators such as the A100 or H100 won't do: they have no display engine;
- disk sized for your games plus about 20 GB for temporary archives;
- a host geographically close to you — latency depends on it;
- **check the host's bandwidth rate.** Hover over the price in the search results: the breakdown should list GPU and disk only. Most hosts don't charge for traffic, but the rate is set per host, and a Moonlight stream runs roughly 18 GB per hour at 40 Mbit/s, so a paid one would dominate your bill.

---

## Setup

### Step 1. Your own Google OAuth client (recommended)

You can skip this and use rclone's shared client, but it is heavily rate-limited and downloads from Drive will be noticeably slower. Creating your own takes ten minutes: [rclone.org/drive/#making-your-own-client-id](https://rclone.org/drive/#making-your-own-client-id).

You end up with a `client_id` / `client_secret` pair, used in the next step and in the `RCLONE_CLIENT_ID` / `RCLONE_CLIENT_SECRET` variables.

### Step 2. The Google Drive token

On your own computer (a browser is required):

```bash
rclone config
```

- `n` — new remote, name it **gdrive**
- storage type: **drive**
- `client_id` / `client_secret` — from step 1, or Enter for the shared client
- scope: **1** (Full access)
- Enter through the rest; answer **y** to `Use auto config?` and a browser will open

Then print the configuration:

```bash
rclone config show gdrive
```

In the `token = {...}` line find `"refresh_token":"1//0...."`. The value inside the quotes is your `RCLONE_REFRESH_TOKEN`. It is long and starts with `1//`.

The script creates the `vastai-cloud-games` folder on Drive itself and works only inside it.

### Step 3. The Tailscale key

[login.tailscale.com/admin/settings/keys](https://login.tailscale.com/admin/settings/keys) → **Generate auth key**:

- **Reusable — on.** The key is used on every instance launch.
- **Ephemeral — off.** Ephemeral nodes are removed as soon as they go offline, and you won't get a stable name.
- Expiration — up to 90 days. When it expires, generate a new one and update the variable.

The resulting `tskey-auth-...` string is your `TAILSCALE_AUTHKEY`.

Two more settings in the admin console, without which this gets awkward:

- **DNS → MagicDNS — enable it.** The same page shows your tailnet name, something like `tail3d42c9.ts.net`. You'll need it for Moonlight.
- After the first launch: **Machines → your node → ⋯ → Disable key expiry.** Otherwise the node drops off mid-game six months from now.

### Step 4. ZeroTier (optional)

If you want a second network, take the Network ID from [my.zerotier.com](https://my.zerotier.com) and put it in `ZT_NETWORK_ID`. Without that variable ZeroTier is neither installed nor started. Tailscale alone is enough for streaming.

### Step 5. The Vast.ai template

The script is larger than 16 KB and the on-start field is capped. So the field holds a tiny loader that fetches the script:

```bash
#!/bin/bash
URL='https://raw.githubusercontent.com/USER/REPO/vX.Y/wolf-setup.sh'
for _ in {1..30}; do curl -fsSL "$URL" -o /root/setup.sh && [ -s /root/setup.sh ] && break; sleep 5; done
exec bash /root/setup.sh
```

Point the URL at a **release tag**, not at a branch: otherwise every commit ships immediately to every instance that starts.

In the template set:

- **Image:** `docker.io/vastai/kvm:ubuntu_desktop_22.04`
- **On-start script:** the loader above
- **Disk space:** sized for your games

Don't put secrets in the template. They belong in **Account → Environment Variables**, from where they arrive as environment variables and the script immediately moves them into files with mode `600` and strips them from `/etc/environment`:

| Variable | |
|---|---|
| `RCLONE_REFRESH_TOKEN` | required |
| `TAILSCALE_AUTHKEY` | required |
| `RCLONE_CLIENT_ID`, `RCLONE_CLIENT_SECRET` | if you made your own client |
| `ZT_NETWORK_ID` | if you want ZeroTier |

You don't need to forward Sunshine's ports in the Vast settings: the stream goes over Tailscale.

### Step 6. First launch

Rent the instance and watch the install:

```bash
tail -f /var/log/wolf-setup.log    # package installation
tail -f /var/log/wolf.log          # restore and sync
```

Anything the script finds wrong with the environment — not Ubuntu 22.04, no NVIDIA, a missing token — is printed near the top of `wolf-setup.log` with a `!!!` prefix.

Status page: `http://<node-name>:8099` — a table of archives and the tail of the log, refreshed every five seconds.

With an empty Drive there is nothing to restore, so the first launch is just ordinary setup from scratch:

1. Wait for the `boot: ok` line.
2. Connect with Moonlight (next step) and sign in to Steam.
3. Install your games.
4. **Switch Steam to offline mode** (Steam → Go Offline). Without it, every new instance will ask for Steam Guard confirmation.
5. Play for 10–15 minutes so the saves and the cache reach the cloud. Check with `grep steam-cache /var/log/wolf.log` — you want a line saying it was uploaded.

From here you can destroy and re-create instances as often as you like.

### Step 7. Moonlight

You work out the node name once. In the [Tailscale admin console → Machines](https://login.tailscale.com/admin/machines) find the `vastai-gaming` node and read its full name, which is the node name plus your tailnet name from the DNS page:

```
vastai-gaming.tail3d42c9.ts.net
```

The name may come with a suffix — `vastai-gaming-2`, `-3` and so on. Those are leftovers from earlier nodes: Tailscale won't give two machines the same name and appends a number. Delete the stale nodes in the console, keep the working one, and note its exact name. It won't change after that, because the node's state is restored from the cloud.

In Moonlight, add the PC **manually by that name, not by IP address.** The 100.x.y.z address is usually stable, but it changes if the node is ever recreated; the name doesn't.

When it asks for a PIN, open `https://vastai-gaming.tail3d42c9.ts.net:47990`. Your browser will complain about the self-signed certificate, which is expected. On the first visit Sunshine asks you to create a username and password for its web UI. Enter the PIN in the PIN section.

The Moonlight pairing is kept in the `identity` archive, so you won't have to confirm it again when the instance changes.

**Resolution.** To get your device's native resolution, select **Native** in Moonlight rather than a fixed 1080p. The script applies exactly what the client sends. Landscape orientation works on every kind of device; portrait sometimes fails NVIDIA's timing validation, in which case the stream simply runs at the base 1920×1200 and `/var/log/wolf-res.log` records the failure.

---

## Day-to-day use

```bash
sudo wolf shutdown          # close Steam and upload everything — BEFORE destroying the instance
sudo wolf state             # upload state right now
sudo wolf games             # upload games right now
sudo wolf restore NAME...   # restore archives (FORCE=1 to restore even if current)
sudo wolf display           # re-apply EDID/xorg and return to the base mode
sudo wolf firewall          # re-apply port isolation
sudo wolf-res 2560 1440 60  # change resolution by hand; no arguments means the base mode
```

**`sudo wolf shutdown` before destroying an instance is the one rule that matters.** Destruction on Vast is immediate and the system gets no chance to shut down cleanly. Without this command you lose the last interval: up to 5 minutes of state and saves, up to 15 minutes of game files.

Logs and status:

| | |
|---|---|
| `/var/log/wolf-setup.log` | installation |
| `/var/log/wolf.log` | sync, network, display |
| `/var/log/wolf-res.log` | resolution changes |
| `http://<node-name>:8099` | status page; `/json` for the same data machine-readable |

---

## What is synced, and when

| Archive | Contents | Uploaded |
|---|---|---|
| `identity` | `machine-id`, Tailscale state, ZeroTier keys, Sunshine certificates | every 5 min |
| `steam-state` | Steam login, settings, `userdata/` | every 5 min |
| `steam-client` | the Steam client without caches or games | every 5 min, once it settles |
| `steam-cache` | `appcache` for offline mode | once unchanged across two passes, or at shutdown if Steam is already closed |
| `pfx--<appid>` | the Proton prefix, which is where saves live | every 5 min, and right after you quit the game |
| `game--<folder>` | a non-Steam game, a subfolder of `~/Downloads/Games` | every 15 min, and after you quit |
| `sgame--<folder>` | a Steam game, only with `SYNC_STEAM_GAMES=1` | every 15 min, and after you quit |

Steam games themselves are **not** synced by default: re-downloading them from Steam is usually faster than from Drive and doesn't eat your Google quota (750 GB per day of uploads). Turn on `SYNC_STEAM_GAMES=1` only for games you've modified locally or that are no longer on the store.

Put non-Steam games in subfolders of `~/Downloads/Games`; each subfolder becomes its own archive.

---

## Variables

All of these go in Environment Variables on Vast and override the defaults in the script.

**Secrets**

| | |
|---|---|
| `RCLONE_REFRESH_TOKEN` | Google Drive token |
| `TAILSCALE_AUTHKEY` | the `tskey-auth-...` key |
| `RCLONE_CLIENT_ID`, `RCLONE_CLIENT_SECRET` | your own Google OAuth client |
| `ZT_NETWORK_ID` | ZeroTier network; empty means ZeroTier is unused |

**Behaviour**

| | Default | |
|---|---|---|
| `STEAM_FREEZE` | `1` | don't let the Steam client update itself on launch |
| `SYNC_STEAM_GAMES` | `0` | sync the Steam games themselves |
| `AUTO_RES` | `1` | manage resolution |
| `RES` | `1920x1200` | base desktop resolution |
| `STEAM_LANG` | empty | force a Steam language (`english`, `german`, …); empty means no `-language` flag is passed and Steam uses its own setting |
| `TAILSCALE_SSH` | `1` | bring Tailscale SSH up — see [Security](#security) |
| `NOTIFY_SAVES` | `1` | notify when saves are uploaded |
| `NOTIFY_SEC` | `8` | how long a notification stays on screen |

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

## Security

What the script does:

- Sunshine's ports (47984–48010, including the 47990 web UI) and the status page accept traffic only from the `tailscale0` interface and from loopback; everything else is dropped. The INPUT policy itself is left alone, so SSH and Vast's own management keep working.
- The Drive token lives only in `/root/.config/rclone/rclone.conf` (600, root); the Tailscale key lives in `/etc/wolf/tskey` (600, root) and is passed as `file:`, so it never shows up in the process list. Both are stripped from `/etc/environment`, and Steam and games start through `env -i` so they inherit no secrets.
- The `identity` archive is never unpacked into `/`. It is downloaded to a temporary file, checked for absolute paths and `..`, extracted into a staging directory, every symlink is verified with `realpath`, and only then is a fixed allowlist of files copied into place. User archives are always extracted as the unprivileged desktop user, never as root.
- Uploads to Drive are atomic: the file is written as `NAME.part` and replaces the live one only on a successful `moveto`. Overwrites go to the trash, so they can be rolled back.

What is up to you:

- **Use a separate Google account for this.** The token from Environment Variables is readable by anything running on the instance, and its scope is full access to Drive. A separate account limits the damage to the games folder.
- **The instance is rented from a stranger.** The machine's owner has root on the host. Your Steam session and your Drive token will be sitting there. That isn't paranoia, it's what renting means — decide accordingly what you're willing to put there.
- **Tailscale SSH is on by default.** Any node on your tailnet gets root on the instance. Convenient on a personal tailnet; set `TAILSCALE_SSH=0` if the tailnet is shared.
- **Never share the Drive folder.** The archives contain a live Steam session.

---

## Limitations

- **One instance at a time.** They share the same archives and the same Tailscale identity. The script notices the version mismatch and stops uploading with an "another version in the cloud" error, but it's better not to get there.
- **Anti-cheat games** (EAC, BattlEye) may refuse to launch in a virtual machine, or get you banned. Your risk.
- **NVIDIA only.** On any other GPU, resolution management switches itself off; everything else still works.
- `wolf display` restarts X when the configuration changes, which closes any running game and the Steam session. Don't run it mid-game.
- Tested on `docker.io/vastai/kvm:ubuntu_desktop_22.04` with the SDDM display manager. Another image may need adjustments.
- The script's log messages and status page are in Russian.

---

## Troubleshooting

**Steam loads forever.** There's no license cache for offline mode. Since v3.6 such a launch automatically goes online and the status page shows a hint. Sign in, switch back to offline mode, and let the instance run for 10–15 minutes. Check with `grep steam-cache /var/log/wolf.log`.

**Moonlight asks for a PIN on every instance.** `identity` isn't being restored. Check `grep identity /var/log/wolf.log` for an `ok` line.

**Nodes with numeric suffixes pile up in the Tailscale console.** Same cause: the Tailscale state isn't arriving from the cloud, so every launch registers a new node. Fix `identity` first, then delete the extras.

**Black screen with only the cursor visible.** The client's resolution didn't match what Sunshine captures. Set Moonlight to exactly 1920×1200; if the picture appears, the problem is in the resolution switch — see `/var/log/wolf-res.log`.

**`firewall: tailscale0 not found` in the log.** Tailscale didn't come up, so Sunshine's ports and the status page were left open. Check the key and `tailscale status`.

**Sync stopped with "another version in the cloud".** A second instance was running somewhere. Decide which copy is newer and run `sudo wolf restore NAME` on the machine you're keeping.

---

## License

MIT.
