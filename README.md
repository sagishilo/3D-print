# PrintFarm Ops: local Docker stack (Windows + Docker Desktop)

One PC runs everything in Docker: **NATS JetStream**, **PostgreSQL**, the **FastAPI gateway**, the **go2rtc** camera relay, **Caddy** (HTTPS) and the **agents**: coordinator, personalization, fleet, inventory, telemetry and **finance** (the ledger). Optional extras: **bambu-cam** for Bambu P1/A1 cameras (`--profile p1a1`) and **simulated printers** for trying everything without hardware (`--profile sim`).

Only Caddy is reachable from your LAN (ports 80/443). NATS, Postgres and go2rtc are bound to `127.0.0.1` (this PC only). The gateway and the agents have no published ports at all.

```
browser ──HTTPS──> caddy ──> gateway ──> postgres <──── agents ──> printers (LAN: MQTT / Moonraker)
                               │  └────> go2rtc ──> printer cameras (LAN)
                               └─────> nats <──── finance (ledger)
                                         ^──────── agents
```

Every command below is **PowerShell**, run from the repository folder. Each step lists the output you should see and what to do if you see something else.
Working on the code? See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

**מדריך למשתמש בעברית (Hebrew user guide):** [docs/USER_GUIDE_HE.md](docs/USER_GUIDE_HE.md). How to sign in and use every screen, step by step. It is also built into the dashboard: **User guide · מדריך** at the bottom of the sidebar, or `https://<this PC>/help`.

---

## Step 0: Prerequisites (once)

1. Install **Docker Desktop for Windows** with the **WSL 2** backend, then start it (whale icon in the system tray, status "Engine running").
2. Check it:

```powershell
docker version --format "{{.Server.Version}}"
docker compose version
```

**Expected:** a version number such as `29.x.x`, then `Docker Compose version v2.x` or newer.
**If you see** `error during connect` or `cannot find the file specified`: Docker Desktop isn't running. Start it, wait for "Engine running", and run the commands again.

## Step 1: Put the files in place

Clone the repository (or copy it) to a short path such as `C:\printfarm`, then:

```powershell
git clone https://github.com/sagishilo/3D-print.git C:\printfarm
cd C:\printfarm
dir
```

**Expected:** folders including `bambu-cam config db docs finance gateway infra scripts src web` and files `docker-compose.yml`, `Dockerfile`, `.env.example`, `cameras.env.example`, `README.md`.

## Step 2: Give this PC a fixed IP

Phones and other computers reach the dashboard at this PC's address, so it must not change.

```powershell
ipconfig | Select-String "IPv4"
```

**Expected:** a line such as `IPv4 Address. . . : 192.168.50.10`. Write the number down.
On your router, create a **DHCP reservation** for this PC with that address. Do the same for **every printer** (see step 10). The menu name varies by router: "Address reservation", "Static lease" or "DHCP binding".

## Step 3: Create the secrets file (`.env`)

This generates every password and secret and asks you for the admin password. **Nothing secret is shown on screen.**

```powershell
docker run --rm -it -v "${PWD}:/work" -w /work python:3.12-slim sh -c "pip install -q argon2-cffi==25.1.0 && python scripts/init_env.py"
```

**Expected:** three prompts: the admin password (hidden), the same password again, then the PC's IP from step 2. The last line is `.env created. Secrets were not displayed.`
**If you see** `.env already exists: nothing changed`: it was already created, so go to step 4. Don't delete `.env` once the stack has run (see "Resetting" at the end).
**If you see** `Too short` or `Passwords differ`: nothing was written. Run the command again.
**If pip shows an SSL or network error:** this PC can't reach pypi.org. Check your internet connection or VPN and run it again.

## Step 4: Check the secrets file (prints SET / MISSING, never values)

```powershell
docker run --rm -v "${PWD}:/work" -w /work python:3.12-slim python scripts/check_env.py
```

**Expected:** seven lines starting `SET`, then `OK       ADMIN_PASSWORD_HASH format` and `OK       NATS password format`.
**If any line says** `MISSING` or `INVALID`: delete `.env` (`Remove-Item .env`) and repeat step 3. This is safe only before step 5 has ever run.
**Only `NATS password format` is INVALID** (an `.env` made by an older version of `init_env.py`): open `.env` and put the letter `n` in front of `NATS_SERVICES_PASSWORD` and `NATS_AGENTS_PASSWORD`. Unlike the Postgres password, this is safe at any time.

## Step 5: Build and start

```powershell
docker compose --profile sim up -d --build
```

`--profile sim` adds simulated printers, so you can try the whole system before any real printer is connected. Once your printers are set up (step 10), start without it: `docker compose up -d --build`.

**Expected:** the first run downloads images and builds the gateway, finance and agent images and the dashboard (5–10 minutes; the agent image includes OpenSCAD, and the dashboard is built with Node.js inside Docker, so nothing extra is installed on this PC). It ends with lines such as `Container printfarm-caddy-1  Started`.
**If you see** `required variable ... is missing a value`: `.env` is missing or incomplete. Go back to step 4.
**If you see** `port is already allocated` for 80 or 443: another program (IIS, Skype or another web server) is using that port. Stop it, or tell me and I'll move Caddy to 8443.

## Step 6: Check every container

```powershell
docker compose ps --format "table {{.Service}}`t{{.Status}}"
```

**Expected:** `gateway`, `go2rtc`, `nats` and `postgres` show `Up ... (healthy)`. `caddy`, the agents (`coordinator`, `finance`, `fleet`, `inventory`, `personalization`, `telemetry`) and, with `--profile sim`, `mosquitto`, `simulator`, `bambu-cam-sim` and `x1-camera-sim` show `Up`. (`migrate` runs once at start and exits, so it isn't listed.)
**If one says** `unhealthy` or `Restarting`: show its log, for example `docker compose logs gateway --tail 50`, and send me the last 20 lines. The log contains no secrets.

Health of the core path:

```powershell
docker compose exec gateway python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
```

**Expected:** `{"ok":true,"nats":true,"postgres":true,"go2rtc":true}`

## Step 7: Trust the HTTPS certificate (once per device)

Caddy creates its own local certificate authority. Until a device trusts it, the browser warns, and login cookies won't work from other devices.

**On this PC:**

```powershell
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt .\caddy-root.crt
Import-Certificate -FilePath .\caddy-root.crt -CertStoreLocation Cert:\CurrentUser\Root
```

**Expected:** a Windows "Security Warning" dialog asks whether to install a certificate from "Caddy Local Authority". Click **Yes**. PowerShell then prints a `Thumbprint` and `Subject` line.
**If `docker compose cp` says** `no such file`: Caddy hasn't created the CA yet. Open `https://localhost` once in a browser, wait 5 seconds and repeat.
Chrome and Edge use the Windows store. **Firefox** has its own: go to Settings → Privacy & Security → Certificates → View Certificates → Authorities → Import → `caddy-root.crt`, and tick "Trust this CA to identify websites".

**On a phone:** copy `caddy-root.crt` to the phone.
- Android: Settings → Security → More → Install certificate → **CA certificate**.
- iPhone: open the file → install the profile → Settings → General → About → Certificate Trust Settings → turn it on.

Open `https://localhost` (or `https://<PC-IP>` on another device).
**Expected:** the PrintFarm Ops **Sign in** page, with no certificate warning. Sign in as `admin` with the password you chose in step 3. You land on the **Live floor** (see "The dashboard" below).

On its first start the gateway makes this `.env` admin the first **owner** account. From then on accounts live in the database: add the people who work with you on the **Users** screen (each with their own role and password), and change your own password from your name in the top bar. Changing `ADMIN_PASSWORD_HASH` in `.env` later has no effect on existing accounts. If no owner can sign in any more (a forgotten password, every owner disabled), run on this PC: `docker compose exec gateway python -m app.recover admin` (any username). It makes that account an enabled owner with a one-time password printed once, signs out its sessions, and records the recovery in the audit log.
**If it says** "Wrong username or password. 4 attempts left": after 5 wrong tries from the same device the login locks for 15 minutes.

## Step 8: See the camera path working (no printer needed)

Open `http://127.0.0.1:1984` on this PC (the go2rtc page, reachable only from this PC). A stream called **demo** is listed. Click **stream** to see a moving test pattern.
That proves go2rtc works. The dashboard reaches the same stream at `/api/v1/cameras/demo/mjpeg` once you're logged in. Delete the `demo:` line in `infra/go2rtc/go2rtc.yaml` once real cameras work.

## Step 9: Let phones and other PCs connect (Windows Firewall)

On another device on the same Wi-Fi, open `https://<PC-IP>`.
**If it times out:** your Windows network is probably set to Public, or port 443 is blocked. In an **administrator** PowerShell:

```powershell
Get-NetConnectionProfile | Select-Object Name, NetworkCategory
New-NetFirewallRule -DisplayName "PrintFarm HTTPS" -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow -Profile Private
```

**Expected:** your home network shows `Private` (if it says `Public`, change it in Settings → Network → your network → Private), and the rule command prints `Status: The rule was parsed successfully`.

## Step 10: Add printers (static IP + pre-flight port check)

1. Give each printer a DHCP reservation on the router (step 2). Restart the printer so it picks up the reserved address.
2. Create `printers.txt` in the stack folder, one line per printer (`id,type,ip`, with type `x1`, `p1`, `a1` or `klipper`):

```text
x1c-02,x1,192.168.50.21
p1s-01,p1,192.168.50.23
voron-01,klipper,192.168.50.31
```

3. Run the check:

```powershell
docker run --rm -v "${PWD}:/work" -w /work python:3.12-slim python scripts/preflight.py
```

**Expected:** every line `PASS`, and the last line `ALL CHECKS PASSED`.
**If you see** `FAIL ... timed out`: wrong IP, printer off, or the PC and printer are on different networks (guest Wi-Fi blocks this). **If you see** `FAIL ... Connection refused` on Bambu ports: enable **LAN Only Mode** (and LAN live view on X1) on the printer. A printer stays "Not ready" until all its checks pass.

## Step 11: Printer access codes and cameras

Create the printer access-code file (a separate file, so go2rtc never sees your other secrets). The telemetry agent reads the same codes to control printers and upload plates:

```powershell
Copy-Item cameras.env.example cameras.env
notepad cameras.env
```

- **Every Bambu printer:** set `BAMBU_CODE_<ID>=` (e.g. `BAMBU_CODE_X1C_02=`) to the code shown on the printer screen, and point that printer's `access_code_env` in `config/fleet.toml` at it.
- **X1 camera:** in `infra/go2rtc/go2rtc.yaml`, remove the `#` in front of the `x1c-02:` line and fix its IP. Keep `rtspx://`: the printer's certificate is self-signed, and `rtsps://` would reject it.
- **Klipper/Crowsnest:** in `go2rtc.yaml`, remove the `#` in front of the `voron-01:` line and fix its IP. No code needed.
- Delete the `SIMULATED printers` block in `go2rtc.yaml` once you use real printers.

Then:

```powershell
docker compose restart go2rtc
```

**Expected:** at `http://127.0.0.1:1984` the new stream shows video when you click **stream**.
**If an X1 stream shows** `401` or `connection refused`: wrong access code, or LAN live view is off on the printer.

## Step 12: Bambu P1 / A1 cameras (custom frame-reader)

P1 and A1 printers have no RTSP. `bambu-cam` reads their port-6000 camera protocol and serves MJPEG to go2rtc.
**Status: tested against the printer simulator (including refused logins, dropped and stalled connections), not yet on a real P1/A1.** Try it on one printer first.

1. In `cameras.env`: `BAMBU_CAMS=p1s-01=192.168.50.23` (`host` or `host:port`) and `BAMBU_CODE_P1S_01=<access code>`.
2. In `go2rtc.yaml`: remove the `#` in front of `p1s-01: http://bambu-cam:8090/p1s-01.mjpeg`.
3. Start it, with the extra profile:

```powershell
docker compose --profile p1a1 up -d --build
docker compose restart go2rtc
docker compose logs bambu-cam --tail 20
```

**Expected** after opening the stream in go2rtc's page: `p1s-01: connected to 192.168.50.23:6000`.
**If you see** `payload is not a JPEG`: the access code is wrong, or this firmware speaks a different protocol. Send me the log lines.
Per-camera state (connected, frames, last error): `docker compose exec bambu-cam python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8090/status').read().decode())"`.
From now on, always start with `docker compose --profile p1a1 up -d`.

### Simulated printers and cameras (`--profile sim`)

Without hardware, `--profile sim` runs three simulated printers that speak the real protocols: `bambu-01` (P1: MQTT, FTPS uploads, port-6000 camera through `bambu-cam-sim`), `bambu-02` (X1: MQTT, FTPS, native RTSPS camera from `x1-camera-sim`) and `voron-01` (Klipper: Moonraker HTTP + WebSocket, Crowsnest webcam). Their access code is `12345678` (not a secret). Plates are uploaded to them and started, and every pause is verified, exactly as with real printers.

To rehearse failures, restart the simulator with faults (combine them per printer with `+`):

```powershell
$env:SIM_FAULTS = "bambu-01=ignore_pause,voron-01=thermal_runaway"; docker compose --profile sim up -d simulator; Remove-Item Env:SIM_FAULTS
```

| Fault | What the simulated printer does |
| --- | --- |
| `thermal_runaway`, `stall`, `fail` | the nozzle overheats / progress freezes / the print fails mid-way |
| `ignore_pause`, `ignore_commands` | drops pause (or every command), like firmware that refuses third-party control: you get a **pause unconfirmed** alert |
| `upload_fail`, `upload_flaky` | refuses uploads (no space) / fails the first upload of each file (the agent retries) |
| `camera_off`, `camera_auth_fail`, `camera_drop`, `camera_stall` | camera port closed / login refused / connection drops / frames stop |

## The dashboard

After signing in (step 7), the sidebar has the screens of the design, and all of them work. The Trend Analyst panel on Social & Trends arrives in phase 10b.

- **Live floor**: one tile per printer with its camera (live MJPEG; the tile shows the frame age, and STALLED / RECONNECTING / OFFLINE when the camera has a problem), state, temperatures and progress, and Pause / Resume. **History** on a tile replays that printer's last 15–60 minutes of telemetry (temperatures, progress, failure and pause markers; scrub or play it back). The **Live events** list below shows every event as it happens; **Replay** loads stored events (up to 7 days back). Safety alerts (a pause the printer did not confirm, a critical failure) appear as a red banner and stay until you acknowledge them.
- **Instant Catalog**: ready-made products (SKUs) printed from a 3MF template, ranked by units sold in the last 7 days (with the change against the week before), filtered by category. **Import 3MF**: best a plate **sliced** in Bambu Studio or OrcaSlicer (File › Export › Export plate sliced file, one plate per file): it goes to the printer as is, one piece per plate, and its print time and filament come from the slicer. A model-only 3MF also works (type its grams and minutes); simulated printers print it, a real printer refuses it until it is sliced. **Slice now** slices on the server when a slicer is installed next to the personalization agent; the standard image has none, so it tells you to export a sliced plate instead. **Add to batch** (it suggests this week's sales minus what is already being printed for stock) builds a batch: the bar shows its plates, print time and filament as the scheduler would pack them, and which filament would run short (spools minus what scheduled plates and queued jobs already need). **Queue batch** creates one stock order (channel "stock", no price) that goes straight to the printers' queue; it appears on the Order Pipeline. Catalog SKUs can also be sold in **New manual order** (Add catalog item): the price and filament come from the catalog.
- **Order Pipeline**: every order on a board, Lead → Order received → Personalization → Slicing / scheduling → Printing → Quality control → Shipped. **New manual order** takes the customer, channel, due date and several items (each with its own text, color, quantity and price; plus discount and shipping); the total is shown before you save. **Drag a card** to move it: into Personalization starts the nameplate models (one per item), into Slicing / scheduling sends the units to the printers; moves that are not possible (e.g. into Printing, which printers drive) are refused with the reason. Open an order for its items and prices, each unit's progress, payments (record one; the balance updates), QC (**Pass**, or **Fail · reprint** chosen units) and **Mark shipped**, history and Cancel. Orders that fail (a nameplate, a unit failing twice, three QC failures) turn red and wait in their column until you retry. Switches in System → Config (off by default) let new orders start personalization, ready models go to the printers and printed orders pass QC on their own; shipping is always yours. Above the board: open orders, due today, in print, QC failures (7 days), lead-to-ship time; below it: the nameplates being generated and the build plates the Fleet Manager assigned.
- **Filament**: every spool (material, color, brand, grams left, where it is: printer and AMS slot / tool, or shelf; days left at the current use). Active spools count down **live** while their printer prints (the plate's estimate × the printer's progress); the grams are booked once the plate finishes. **Scan or type a spool label** to find it, or register it under that label; open a spool to load it on a printer, put it back on a shelf, weigh it or mark it empty. **Alerts** (also a badge in the sidebar and a toast): a material below its minimum stock, a loaded spool too short for the plate just scheduled on its printer, a spool run empty; they close on their own when the condition clears. **Reorder rules** per material and color: minimum stock, supplier, lead time, Auto (orders under the approval limit go out on their own) or Manual (every order waits for you); the rule is "reorder when grams − daily use × (lead time + buffer) ≤ minimum", and the list shows when each material triggers. **Order now** places a purchase order; purchase orders are approved (or their quantity changed, or dismissed), confirmed as ordered with the supplier's real total (Finance books it), and received onto a shelf (new spools). A 14-day chart shows daily use. The rules in `config/fleet*.toml` only seed new materials: after that the dashboard owns them.
- **Finance**: a month, quarter or year against the one before: net revenue, expenses, net profit, margin and receivables; a 12-month profit chart; the expense mix (electricity is **estimated** from print hours × watts × tariff until you enter the bill for that period); margin per product type (filament and electricity per job from its grams and print hours, priced from your latest purchase order of that filament); receivables (shipped, unpaid: not income until a payment is recorded); the income and expense ledgers; VAT per two-month return (output on income, input on expenses with a tax invoice; for an osek patur, turnover against the ceiling). **Record payment** (an order's balance, or income without an order such as a market stall, with its receipt number) and **Add expense** (with or without a tax invoice, or as an estimate). **Export CSV** gives the accountant the ledger of the period. **Settings** (business type, VAT, electricity tariff, printer watts, filament price per gram) start as placeholders, marked on the screen until you confirm them with your accountant. A management view: legal receipts and tax invoices come from your invoicing system.
- **Printer Fleet**: every printer with its controls (Pause, Resume, Cancel, **Bed cleared** after a finished plate), filters by state, and **batch actions**: tick several printers, and Pause / Resume / Cancel / Bed cleared / Home apply to the ones in a fitting state (the dialog lists the ones skipped and why); each printer still gets its own audited, verified command. **Details** opens a printer's camera and telemetry, its **hardware** (fans, chamber, speed mode, nozzle, Wi-Fi, firmware, AMS trays, health codes; Klipper: homed axes, bed mesh, host load), **calibration** and history (prints, verified pauses, failures). Below the tiles, the **failure & event log** filters by printer, severity and period (up to 7 days). Cancelling a print queues its parts again automatically (up to 2 attempts per part).
- **Calibration** (Printer Fleet → Details → Calibration): Bambu: home, bed leveling, vibration compensation, motor-noise cancellation; Klipper: home, bed mesh, input shaper, nozzle and bed PID. A routine starts only on an online, idle printer after you tick "the bed is empty"; the scheduler sends that printer no plate meanwhile; the result (done, failed, or not started and why) appears in the log. A critical finding during a calibration stops it. Bambu's calibration commands come from community documentation: **try them on one printer first** (Phase 9).
- **System & Agents**: agent health (heartbeat, version, messages waiting), the servers (gateway, NATS, Postgres, go2rtc), printer connections, NATS clients and streams, the **dead letter queue** (inspect a message that failed 5 times, then Replay or Discard it; both are recorded in the audit log), and the **automation toggles** (failure auto-pause, auto-reorder; each change is recorded with who made it, and the agents apply it within about 5 seconds). Toggles for later phases are shown but disabled.

- **Users** (owners only): everyone signs in with their own account and role. **Owner**: everything, including users, finance settings, system toggles, refunds and dead letters. **Operator**: the daily work (floor, orders, fleet, filament, catalog, social review, payments and expenses). **Viewer**: sees the operational screens and changes nothing (Finance, System and Users are hidden). **Add user** and **Reset password** give a one-time temporary password, shown once; the person chooses their own at the first sign-in. A role change, reset or **Disable** signs the person out on every device at once. You cannot demote or disable yourself, and the last owner stays an owner. **System & Agents ▸ Audit log** (owners) lists who did what, filtered by person, action, result and dates; passwords are never logged.

The top bar shows whether the live feed is connected ("Live · 0.4 s ago"), the approvals inbox, **Pause all printers**, and your name (role, **Change password**, **Log out**). If the connection drops (Wi-Fi, a PC restart), the dashboard reconnects by itself and catches up on every event it missed. Camera video is not recorded: History replays the printer's telemetry and events, not video.

## Step 13: The agents and the message bus

The agents run as containers in this compose file and connect to NATS as the `agents` user (its password is in `.env`; it is passed to them through their environment, never in code). To run an agent on this PC outside Docker while developing, use `nats://agents:<NATS_AGENTS_PASSWORD>@127.0.0.1:4222` and see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

**Who may send what.** NATS has two users (`infra/nats/nats.conf`):

- `services` (the gateway and the operator CLI) has full access.
- `agents` may publish events, telemetry, dead letters and query replies, and only the five commands one agent sends another: model requests, job queueing and cancellation, and plate start.
- Every operator decision (approve, pay, refund, cancel, pause or resume printers, settings, purchase orders, expenses, catalog changes) is refused for `agents`, both by NATS and by the agents' own code. So is deleting or purging a stream.

An agent bug, or someone who gets hold of the agents' password, cannot approve, pay or cancel anything. The list lives in `src/printfarm/contracts/permissions.py`; a test keeps `nats.conf` equal to it.

Every message is a CloudEvents envelope with a typed payload. All subject names live in one place, `src/printfarm/contracts/messages.py`, used by the agents, the gateway and the ledger alike:

| Subject root | Meaning | Kept in |
| --- | --- | --- |
| `evt.*` | something happened (`evt.fleet.plate.finished`) | stream `EVENTS`, 30 days |
| `cmd.*` | a command for one agent (`cmd.telemetry.pause_print`) | stream `COMMANDS` (work queue) |
| `tlm.*` | live printer state (`tlm.<printer-id>.state`) | stream `TELEMETRY`, 1 hour, in memory |
| `qry.*` | a read-only question (request/reply) | never stored |
| `dlq.*` | a message that failed 5 times | stream `DLQ`, 90 days |

The dashboard sends commands with `POST /api/v1/commands/<name>` (the list: `GET /api/v1/commands`). The gateway checks the body against the command's contract, writes it to the audit log and publishes it; the answer is `202 Accepted` with a `command_id`, and the outcome arrives as an event on the live feed. With an `Idempotency-Key` header a retried request never runs twice. A command waits in its stream if its agent is down and runs when the agent is back; printer commands expire after 30 seconds instead. The browser's live feed (`/ws`) accepts only `evt.`, `tlm.` and `dlq.` topics, so commands are never forwarded to it.

### What the ledger books

| Message | Booked as | Comes from |
| --- | --- | --- |
| `evt.order.payment.recorded` / `.refunded` | income, VAT split (negative for a refund) | dashboard command `payments.record` / `payments.refund` |
| `evt.inventory.po.ordered` | expense, **cash** (filament purchase) | dashboard command `po.confirm_ordered`, with the real total from the supplier's invoice |
| `evt.fleet.plate.finished` | filament + electricity per job, **allocated** (product margins only, never in profit) | the Fleet agent, after every plate (failed plates too, as scrap) |
| `cmd.finance.create_expense` | expense, **cash** or **estimated** | dashboard command `expenses.create` |

Amounts are integers in agorot (`35400` = ₪354.00) and gross (VAT included). Every ledger row is keyed by the id of the message that caused it, so a redelivered message is never booked twice. A message that keeps failing goes to the DLQ (`dlq.message.dead_lettered`) after 5 attempts.

## Social Listening (optional)

The **Social & Trends** screen finds people in Facebook groups who want something you print, and drafts a Hebrew reply. **You** read it, edit it, copy it, paste it in the group and post it yourself. The system never posts, never sends messages and never reads Facebook by itself (ADR-0007: scraping groups breaks Meta's terms and gets accounts banned).

How posts get in:
- **The capture extension.** Install the `extension/` folder in Chrome (Extensions › Developer mode › Load unpacked). On Social & Trends › Capture extension, make a **New token** (it is shown once). In the extension's options, enter this dashboard's address and the token. In a group, select a post's text, right-click, and choose **Capture post to PrintFarm**. The extension sends only what you selected, the page title as the group's name, and the post's link. The device must trust the dashboard's certificate (step 7).
- **Add post** on the screen: paste a post by hand.

What happens to a post:
1. The same post captured twice is kept once.
2. A small model on this PC (no internet, nothing sent anywhere) compares it with posts known to be from buyers. Posts that look nothing like a buyer's are set aside as **ignored**, at no cost.
3. **Claude Haiku 5.5** scores the buying intent (0–1), picks the closest catalog SKU, and writes a one-line summary.
4. From the **lead threshold** (0.7) upward the post becomes a **lead**, and **Claude Opus 5.5** drafts the reply, using only the facts in `config/social.toml`. Put your real business name, area, contact line and facts there; the shipped values are placeholders.
5. The review queue shows the post, the matched SKU and the draft. **Approve & Copy** copies your (edited) text; paste it in the group, post it, then **Mark as posted**. Keys: J / K move, A approve & copy, P mark posted, R reject.

To switch it on, put a Claude API key in `.env` (`ANTHROPIC_API_KEY=sk-ant-...`, from console.anthropic.com), then `docker compose up -d social`. Only the social and trend containers get the key. Without a key, posts are kept and **wait**; press Rescore on each after adding it.

**Cost.** Claude is paid per use. At 50–300 captured posts a day, expect a few dollars a month: Haiku triage costs well under a tenth of a cent a post, and an Opus draft one to two cents, only for leads. The screen shows today's and this month's spend. **Daily model budget** (default $2) stops all model calls for the rest of the day once reached; waiting posts can be rescored the next day.

**Accuracy.** Mark captures **Buyer** or **Not buyer**. Each label also teaches the local prefilter, and the screen shows how many leads were real buyers and how many buyers became leads. Label a few dozen real posts before you trust or move the threshold.

## Integrations (Wave 2)

The `integrations` agent runs every connection to an outside service (stores, invoicing, couriers, alert channels) and keeps the notifications outbox (`notify.outbox`).

- **Off and in test mode by default.** Each connector starts switched off and in **fake** mode (deterministic test data, no outside call). An owner switches it on, and chooses the live service, on the **Integrations** screen.
- **Outbound only.** The agent polls each service on a schedule (backoff after failures, doubling up to 6 h; an owner is told after 3 failures in a row). Nothing on the internet calls this stack, so no port forwarding is needed.
- **Mapped once.** Every outside record is mapped to ours (`integrations.mappings`), so a repeated sync updates and never duplicates.
- **Secrets only in `integrations.env`.** Copy `integrations.env.example` to `integrations.env` (gitignored) and fill in what you use. Check it with `python scripts/check_integrations_env.py` (prints SET / MISSING, never values), then `docker compose up -d integrations`. The dashboard shows only set or missing; live mode refuses to run while a required key is missing.

## Production scheduling, maintenance and camera checks (Wave 2)

- **Schedule** (Make ▸ Schedule) forecasts the next 24 h per printer with the fleet agent's own policy (`src/printfarm/agents/fleet/policy.py`): your manual order first, then late or nearly-due jobs, priority, the overnight window (long jobs first from `fleet.overnight_from`), due date, first come. A printer only takes jobs of the filament loaded on it; jobs nobody can take show as a spool-change suggestion (also on Today). Operators hold, pin and reorder jobs there (`jobs.control`).
- **Quiet hours** (`fleet.quiet_hours`, with `fleet.quiet_from` / `fleet.quiet_until`): no new print starts; running prints finish. Off by default.
- **Maintenance** (Printer Fleet ▸ printer ▸ Maintenance): counters (print hours, plates, grams) and interval tasks per printer, seeded with three defaults. Due and overdue tasks show on Today; a task marked "keep the printer idle while overdue" keeps it out of the schedule until it is done.
- **Auto plate-swap** (same tab): for a printer with a plate changer, a good print clears the bed by itself while plates are left in its stack; a failed print always waits for a person; an empty stack is a Today item.
- **Camera failure detection** is off by default. Pick a model with `VISION_MODEL` for the telemetry service: `off`, `fake` (tests and demos, scores from `VISION_FAKE_SCORES="bambu-01=0.9"`), or `http` (a model on your LAN at `VISION_MODEL_URL` that takes a JPEG and answers `{"score": 0..1}`). Then switch on `vision.enabled` in System ▸ Automation. A suspicion needs `vision.frames` frames in a row above `vision.threshold`; by default it only asks a person (Today: Pause / It's fine). `vision.auto_pause` also pauses the printer (verified).

## Store orders: Shopify (Wave 2)

- **Connect.** In Shopify's Dev Dashboard, make an app with the scopes `read_orders`, `read_customers`, `read_merchant_managed_fulfillment_orders` and `write_merchant_managed_fulfillment_orders`, and install it on your shop. Put `SHOPIFY_CLIENT_ID` and `SHOPIFY_CLIENT_SECRET` in `integrations.env` (an older custom app's Admin token goes in `SHOPIFY_ACCESS_TOKEN` instead). Then, on **Integrations ▸ Shopify**, set the shop address (`your-shop.myshopify.com`) and optionally **Import orders placed from**. Switch the connector on and choose **Live service**. Until then it runs on test orders (**New test orders** makes a fresh set).
- **Orders in.** Every sync reads the orders changed since the last one (GraphQL Admin API, polling). A paid order whose products are all known becomes an order on the `shopify` channel (id `shp-<Shopify id>`, never twice), with its payment recorded once. Unpaid orders wait unless you switch off **Import only paid orders**.
- **Map this.** **Sell ▸ Store orders** lists each Shopify product that is not one of ours yet. Say once what it is: a catalog SKU, a nameplate (filament, plus the line property that holds the text, e.g. `Name`), or not printed (gift wrap). Every waiting order then comes in. A Shopify SKU equal to an active catalog SKU needs no mapping. Waiting orders show on Today and in the notifications.
- **Tracking back.** When an imported order ships, Shopify marks it fulfilled with the carrier and tracking number, and e-mails the customer if **Shopify e-mails the customer the tracking** is on. Failures retry with backoff; after 8 an owner is told to do it by hand.
- **Other stores** (WooCommerce, Etsy) plug into the same store framework (`src/printfarm/agents/integrations/stores.py`): an adapter that reads orders and sends tracking.

## Invoicing and shipping: Morning and Israel Post (Wave 2)

- **Documents (Morning / Green Invoice).** Put `MORNING_API_KEY_ID` and `MORNING_API_KEY_SECRET` in `integrations.env`, switch **Morning** on (Integrations) and choose **Live service**. It starts in Morning's **sandbox**; set **Morning environment** to `production` once a sandbox document looks right. Every payment then gets a document, and every refund a credit invoice linked to it, issued once (one per payment) with retries. An osek murshe gets tax invoice-receipts (320); an osek patur gets receipts (400), and refunds them by hand in Morning. Morning numbers the documents, keeps the PDFs, and requests the Israel Invoices allocation number itself (connect the Tax Authority once in Morning). Numbers and PDF links show on the order's payments and in **Finance ▸ Documents**, together with failed documents (**Issue again**) and payments without one. Turn off **Issue a document … automatically** to issue them by hand.
- **Parcels (Israel Post, or any carrier).** In the order's QC step, **Pass & ship** passes QC and ships it; **Ship** does the same after QC has passed. The address is prefilled from the store order, else the customer. With **Israel Post** switched on, test mode makes a test tracking number and moves the parcel on by itself. Live, Israel Post has no public label API: make the label at Israel Post (business site or counter) and type its tracking number; it is checked and linked to Israel Post's tracking page. **Another carrier** takes any name, tracking number and https link. Each parcel has a printable address label (sender from the Israel Post settings), its status (Label made → In transit → Delivered, or Returned; a label that has not left can be cancelled and made again), and the tracking goes back to Shopify for store orders. Today flags failed documents, refunds to document by hand, and labels made over 2 days ago that were not sent.

## Hebrew, alerts and the phone app (Wave 2)

- **Hebrew (right to left).** Each person picks the language in the user menu (**Language · שפה**); the sign-in page has a switch too, and a Hebrew browser starts in Hebrew. The choice follows the person to every device. Screens since Wave 2 use the vue-i18n catalogues (`web/src/i18n/en.ts`, `he.ts`); the older screens and the text the server sends (Today, schedule reasons, notifications) are translated by a dictionary (`web/src/i18n/he/*.json`, applied by `web/src/i18n/dom.ts`). `web/tests/hebrew.spec.ts` fails when any UI text has no Hebrew: after changing server text run `python scripts/server_text.py`, then add the Hebrew it asks for.
- **Alerts: Telegram and e-mail.** Notifications (safety, failed prints, maintenance, store orders, documents, connectors) go to the switched-on channels. Telegram: make a bot with @BotFather, put `TELEGRAM_BOT_TOKEN` in `integrations.env`, send the bot a message from each phone, and find each chat id with `https://api.telegram.org/bot<token>/getUpdates` (the `chat.id` numbers); put them in the Telegram card's **Chat ids**. E-mail: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, plus the sender and recipients in the card. Per channel: minimum severity (default warning), **quiet hours** (default 22:00–07:00 Israel time: only critical safety alerts go out; the rest wait for the morning), message language (Hebrew by default), and the dashboard address for links. Each channel sends each notification once; failures are retried, then shown as failed. **Send a test** checks a channel. Both start in test mode (nothing is sent; the outbox shows "telegram (test)").
- **Phone app (PWA), LAN only.** System ▸ **Phone app**: download the farm's certificate (`https://<farm>/ca.crt`, Caddy's local CA, public part only), trust it on the phone, open the dashboard and add it to the home screen. A service worker keeps the app's shell (never the API) so it opens offline with a "no connection" page. Away from the farm, use Tailscale: `tailscale serve --bg https+insecure://localhost:443` on the farm PC; every link in the app is relative, so nothing else changes. Push notifications are Telegram's.

## Trend Analyst and analytics

The `trend` agent counts every business day once an hour into `reporting.daily_sku` and `reporting.daily_niche` (sales, net revenue, job costs, print hours, QC, social activity), then marks the niches and products that are rising or cooling (`trend.signals`). The **Niche scorecard** (Business) and the Social page's Trends panel read them. On its first start it backfills the last 120 days; **Rebuild** on the scorecard (owner) recounts them on demand.

Optional insight: with **Trend Analyst insights** switched on (System › Config) and `ANTHROPIC_API_KEY` set, Claude Haiku writes one short Hebrew insight per niche once a day from aggregated numbers only (no customer data), capped at $0.25 a day. `TREND_LLM=fake` in `.env` writes them from the numbers without Claude (demos, tests). `TREND_INTERVAL_S` changes how often it counts (default 3600).

Every report and scorecard downloads as **CSV** (UTF-8 with a BOM, so Excel shows Hebrew) or **Excel** (`?format=csv|xlsx` on the API).

`RATE_API_PER_MIN` (default 600) is the gateway's read limit per computer; raise it only for automated browser tests.

## Daily operations

| Task | Command |
| --- | --- |
| Stop (keeps all data) | `docker compose down` |
| Start again | `docker compose up -d` (add `--profile sim` while you use simulated printers, `--profile p1a1` for P1/A1 cameras) |
| Logs of one service | `docker compose logs finance --tail 50 -f` (Ctrl+C to stop) |
| Apply code changes | `docker compose up -d --build` |
| Is everything up? | `docker compose ps` (every service `healthy`), or open `https://<this PC>/api/health` |
| Database migrations | `docker compose run --rm migrate migrate --status` (what is applied and pending; `--check` exits 1 if anything is wrong) |
| Back up now | `docker compose run --rm backup now` |
| List backups | `docker compose run --rm backup list` |
| Restore a backup | `docker compose stop gateway coordinator personalization fleet inventory finance telemetry social`, then `docker compose run --rm backup restore <file from list>`, then `docker compose up -d` |
| Practise a restore (safe) | `docker compose run --rm backup drill` (restores the newest backup into a scratch database, counts its tables, deletes it) |
| Off-site copies | `docker compose run --rm backup offsite-list` · practise: `docker compose run --rm -v D:\printfarm-key.txt:/key.txt:ro backup offsite-drill` |

**Backups.** The `backup` service saves the database to the `backups` folder next to this README every night and keeps the newest 14. Change this with `BACKUP_INTERVAL_S` and `BACKUP_KEEP` in `.env`. The service shows `unhealthy` when the last good backup is older than two intervals, and **System & Agents ▸ Backups** shows the last local and off-site copy and the last restore drill. The database holds everything that matters (orders, ledger, inventory, settings and their history, unsent messages); NATS only holds messages in flight.

### Off-site backups (encrypted, recommended)

A backup on the same disk does not survive the disk, a fire or a theft. With these steps every nightly backup is also **encrypted on this PC** and copied to cloud storage (Google Drive, OneDrive, Dropbox, Backblaze B2, S3 and others, through [rclone](https://rclone.org)). The cloud provider only ever stores ciphertext.

1. **Make the encryption key pair** (once). The public key encrypts; only the private key can decrypt.

   ```powershell
   docker compose build backup
   docker compose run --rm --entrypoint age-keygen backup
   ```

   It prints a `# public key: age1...` line and an `AGE-SECRET-KEY-1...` line. **Keep the secret key off this PC**: save it to a USB stick and/or print it, and store it somewhere safe. Without it the off-site copies cannot be restored; with it, anyone can read them.
2. **Connect the cloud storage** (once). This opens rclone's setup questions; pick your provider and name the remote, for example `gdrive`:

   ```powershell
   docker compose run --rm --entrypoint rclone backup config
   ```

   It saves the login to `backup-offsite/rclone.conf` (ignored by git; never share it).
3. **Turn it on** in `.env`, then restart the service:

   ```text
   BACKUP_REMOTE=gdrive:printfarm-backups
   BACKUP_AGE_RECIPIENT=age1...        (the public key from step 1)
   BACKUP_OFFSITE_KEEP_DAYS=90
   ```

   ```powershell
   python scripts/check_env.py
   docker compose up -d backup
   docker compose run --rm backup now
   ```

   **Expected:** `off-site copy ok: gdrive:printfarm-backups/printfarm-....dump.age`, and **Backups: Protected** on the System screen.
4. **Practise a restore from the cloud** (now, then every few months), with the secret key mounted for that one run:

   ```powershell
   docker compose run --rm -v D:\printfarm-key.txt:/key.txt:ro backup offsite-drill
   ```

   **Expected:** `DRILL OK: ... restores cleanly (N tables)`. The live database is not touched. To really restore from the cloud (after a lost disk), stop the services as for a local restore, then `docker compose run --rm -v D:\printfarm-key.txt:/key.txt:ro backup offsite-restore <file from offsite-list>`.

If `BACKUP_REMOTE` is set without `BACKUP_AGE_RECIPIENT`, nothing is uploaded and the System screen says why: an unencrypted backup never leaves this PC.

**Migrations.** The `migrate` service applies new database migrations before anything else starts. It refuses to run, and changes nothing, if an applied migration file was edited, or if the database comes from a newer version than the code (an older image running against it). The gateway's health check reports either case, and the dashboard shows a banner.

**Prints that ended unseen.** If a printer is restarted, a print is cancelled at the printer's screen, or a print ends while this PC or the network is down, the telemetry agent notices within about 20 seconds of the printer reporting again. It closes that plate as failed: its items are reprinted, and the bed is not assumed empty, so an operator clears it first.

**Limits.** The capture extension's endpoint counts as commands. The gateway answers each device with 429 (and `Retry-After`) beyond 600 requests a minute, 60 commands a minute or 20 uploads a minute. Bodies are limited to 1 MB, 50 MB for a 3MF upload. Sign-ins lock for 15 minutes after 5 wrong passwords. Sign-ins, failed sign-ins, lockouts, sign-outs and reused refresh tokens go to the same audit log as commands. When the database or the message bus is down, requests answer 503 ("try again shortly"), not a crash, and the dashboard retries a read once. Every response carries an `X-Request-ID`; a server error shows it as "ref …", so you can find it in `docker compose logs gateway`.

**Never run `docker compose down -v`** unless you mean to erase everything. `-v` deletes the database, the NATS history and Caddy's certificate authority, so every device would then have to trust a new certificate.

**Resetting secrets:** Postgres keeps the password it was first started with. To start over completely: `docker compose down -v`, `Remove-Item .env`, then steps 3–7 again.

## What's built, and what's still a skeleton

| Works now (tested end to end) | Still to build |
| --- | --- |
| HTTPS via Caddy; admin login with HttpOnly cookies, refresh rotation, logout, 5-try lockout | Trend Analyst (phase 10b): rising and cooling demand, catalog suggestions |
| Dashboard (Vue 3, built into the Caddy image): sign-in, app shell, Live floor (cameras, telemetry replay, event replay, safety alerts), Order Pipeline (multi-item priced orders, drag-to-dispatch board, QC, shipping, payments), Instant Catalog (3MF import, demand ranking, batches with plates/time/filament and shortages), Filament (spools, live grams, alerts, reorder rules and list, purchase orders), Finance (P&L by period, trend, product margins, receivables, VAT, ledgers, CSV, settings), Printer Fleet (controls, batch actions, hardware readings, calibration, failure & event log), System & Agents (agent health, NATS, servers, dead-letter replay/discard, audited automation toggles); works on phones | Calibration on real Bambu printers (commands are community-documented; verify in Phase 9) |
| Commands: CSRF check, validated against typed contracts, audit log, published durably to JetStream (202 + `command_id`, idempotent retries) | Building multi-part plate files automatically (plate compositor); until then a printer only gets an already-sliced plate file. Server-side slicing (Slice now) needs a slicer CLI in the personalization container; none ships today |
| Agents: order sagas, human approvals, payments, audited settings; plate packing and scheduling; filament ledger, reorders, PO ordered/received, spools | Real-printer tests (Phase 9), including `bambu-cam` on a real P1/A1 and whether your Bambu firmware needs Developer Mode for control |
| Printer control: plate upload (Bambu implicit FTPS with TLS session reuse, Moonraker HTTP), start confirmed from the printer's state, resume/cancel/pause-all; **every pause verified** against the printer's own state, with a critical alert when it does not stop. Tested against simulators only | |
| Simulated printers for P1 (port-6000 camera), X1 (RTSPS camera) and Klipper (Crowsnest MJPEG), with injectable faults | |
| Camera proxy: go2rtc → gateway → browser MJPEG and snapshots, login required; X1 (H.264) cameras transcoded to MJPEG on demand | Recording camera video |
| Live feed (`/ws`): events, telemetry and dead letters only, never commands; resumes after a reconnect from the last event seen, replays stored history; refuses other sites (4403) and visitors without a session (4401) | |
| Ledger: income on payment (VAT split), refunds, PO = cash expense, per-job filament/kWh = allocated (excluded from profit, filament priced from the latest PO), duplicate-safe; settings from the dashboard (audited, confirmed) | Metrics dashboards (Prometheus/Grafana); per-agent Postgres roles |
| Hardening: NATS permissions per user (agents cannot send operator commands or delete streams); rate limits, body limits, request ids, 503 on outages; audited sign-ins; checksummed migrations with `--status`/`--check`; nightly backups with restore, encrypted off-site copies (age + rclone) and restore drills; health checks for every service and a dashboard banner; CSP and security headers; CI (lint, unit, e2e under the real NATS permissions, web, image builds) | |
| One JetStream topology and one migration path (`migrate` service) shared by every component | Social Listening with a real API key (built and tested against a faked Claude client; set `ANTHROPIC_API_KEY` to run it for real) |
| Social Listening: posts captured by people (browser extension or Add post), local embeddings + pgvector prefilter, Claude Haiku triage, Claude Opus Hebrew drafts, review queue (Approve & Copy, Mark as posted), labels and accuracy, daily budget | |

The default finance settings (VAT 18%, 64 agorot/kWh, 150 W, ₪85/kg) are **placeholders**. Set real values with your accountant and from your electricity bill.
