# PrintFarm Ops: local Docker stack (Windows + Docker Desktop)

One PC runs everything in Docker: **NATS JetStream**, **PostgreSQL**, the **FastAPI gateway**, the **Finance (Ledger) service**, the **go2rtc** camera relay, **Caddy** (HTTPS) and the **agents** (coordinator, personalization, fleet, inventory, telemetry). Optional extras: **bambu-cam** for Bambu P1/A1 cameras (`--profile p1a1`) and **simulated printers** for trying everything without hardware (`--profile sim`).

Only Caddy is reachable from your LAN (ports 80/443). NATS, Postgres and go2rtc are bound to `127.0.0.1` (this PC only). The gateway, the Finance service and the agents have no published ports at all.

```
browser ──HTTPS──> caddy ──> gateway ──> postgres <──── agents ──> printers (LAN: MQTT / Moonraker)
                               │  └────> go2rtc ──> printer cameras (LAN)
                               └─────> nats <──── finance (ledger)
                                         ^──────── agents
```

Every command below is **PowerShell**, run from the repository folder. Each step lists the output you should see and what to do if you see something else.
Working on the code? See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

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

**Expected:** seven lines starting `SET`, then `OK       ADMIN_PASSWORD_HASH format`.
**If any line says** `MISSING` or `INVALID`: delete `.env` (`Remove-Item .env`) and repeat step 3. This is safe only before step 5 has ever run.

## Step 5: Build and start

```powershell
docker compose --profile sim up -d --build
```

`--profile sim` adds simulated printers, so you can try the whole system before any real printer is connected. Once your printers are set up (step 10), start without it: `docker compose up -d --build`.

**Expected:** the first run downloads images and builds the gateway, finance and agent images (5–10 minutes; the agent image includes OpenSCAD). It ends with lines such as `Container printfarm-caddy-1  Started`.
**If you see** `required variable ... is missing a value`: `.env` is missing or incomplete. Go back to step 4.
**If you see** `port is already allocated` for 80 or 443: another program (IIS, Skype or another web server) is using that port. Stop it, or tell me and I'll move Caddy to 8443.

## Step 6: Check every container

```powershell
docker compose ps --format "table {{.Service}}`t{{.Status}}"
```

**Expected:** `finance`, `gateway`, `go2rtc`, `nats` and `postgres` show `Up ... (healthy)`. `caddy`, the agents (`coordinator`, `fleet`, `inventory`, `personalization`, `telemetry`) and, with `--profile sim`, `mosquitto` and `simulator` show `Up`. (`migrate` runs once at start and exits, so it isn't listed.)
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
**Expected:** a dark page titled "PrintFarm Ops: stack is running", with no certificate warning. It's a placeholder until the React app is built into `web/dist`.

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

## Step 11: Connect cameras

Create the camera secrets file (a separate file, so go2rtc never sees your other secrets):

```powershell
Copy-Item cameras.env.example cameras.env
notepad cameras.env
```

- **X1:** in `cameras.env`, set `X1C_02_ACCESS_CODE=` to the code shown on the printer screen. In `infra/go2rtc/go2rtc.yaml`, remove the `#` in front of the `x1c-02:` line and fix its IP.
- **Klipper/Crowsnest:** in `go2rtc.yaml`, remove the `#` in front of the `voron-01:` line and fix its IP. No code needed.

Then:

```powershell
docker compose restart go2rtc
```

**Expected:** at `http://127.0.0.1:1984` the new stream shows video when you click **stream**.
**If an X1 stream shows** `401` or `connection refused`: wrong access code, or LAN live view is off on the printer.

## Step 12: Bambu P1 / A1 cameras (custom frame-reader)

P1 and A1 printers have no RTSP. `bambu-cam` reads their port-6000 camera protocol and serves MJPEG to go2rtc.
**Status: tested here against a simulated printer, not yet on a real P1/A1.** Try it on one printer first.

1. In `cameras.env`: `BAMBU_CAMS=p1s-01=192.168.50.23` and `BAMBU_CODE_P1S_01=<access code>`.
2. In `go2rtc.yaml`: remove the `#` in front of `p1s-01: http://bambu-cam:8090/p1s-01.mjpeg`.
3. Start it, with the extra profile:

```powershell
docker compose --profile p1a1 up -d --build
docker compose restart go2rtc
docker compose logs bambu-cam --tail 20
```

**Expected** after opening the stream in go2rtc's page: `p1s-01: connected to 192.168.50.23:6000`.
**If you see** `payload is not a JPEG`: the access code is wrong, or this firmware speaks a different protocol. Send me the log lines.
From now on, always start with `docker compose --profile p1a1 up -d`.

## Step 13: Connect your agents

| Agent runs | NATS URL |
| --- | --- |
| On this PC (outside Docker) | `nats://agents:<NATS_AGENTS_PASSWORD>@127.0.0.1:4222` |
| As a container added to this compose file | `nats://agents:<NATS_AGENTS_PASSWORD>@nats:4222` |

The agents' password is in `.env`. Pass it to agents through their own environment, never in code. Every subject string lives in `common/pf_common/subjects.py`. Replace the *unconfirmed* names there with your backend's real ones, then run `docker compose up -d --build`.

**Rule that must hold:** commands (`print.job.create`) are request/reply and must never be captured by a JetStream stream. That's why the streams list event subjects explicitly instead of using `print.>`.

### Event payloads the Finance service expects (align with the backend)

| Subject | Required fields | Optional |
| --- | --- | --- |
| `order.payment.recorded` / `.refunded` | `event_id`, `order_id`, `amount_agorot` (gross) | `method`, `paid_at` (ISO), `receipt_no`, `product_type` |
| `inventory.po.ordered` | `event_id`, `total_agorot` (gross) | `po_id`, `supplier`, `ordered_at`, `vat_invoice` (default true) |
| `print.job.completed` | `event_id`, `grams_used`, `print_seconds` | `job_id`, `printer_avg_watts`, `spool_price_per_gram_agorot`, `product_type`, `completed_at` |

`event_id` must be unique per event: redelivered or duplicated events are then ignored, so nothing is booked twice. A message that fails 5 times goes to `dlq.finance.<subject>` (visible in System → DLQ).

## Daily operations

| Task | Command |
| --- | --- |
| Stop (keeps all data) | `docker compose down` |
| Start again | `docker compose up -d` (add `--profile p1a1` if used) |
| Logs of one service | `docker compose logs finance --tail 50 -f` (Ctrl+C to stop) |
| Apply code changes | `docker compose up -d --build` |
| Back up the database | `docker compose exec -T postgres pg_dump -U printfarm -d printfarm -f /tmp/backup.sql` then `docker compose cp postgres:/tmp/backup.sql ".\backup-$(Get-Date -Format yyyyMMdd).sql"` |

Don't back up with `pg_dump ... > file.sql` in Windows PowerShell 5: it saves the file as UTF-16, and the restore then fails. The two-step command above avoids this.

**Never run `docker compose down -v`** unless you mean to erase everything. `-v` deletes the database, the NATS history and Caddy's certificate authority, so every device would then have to trust a new certificate.

**Resetting secrets:** Postgres keeps the password it was first started with. To start over completely: `docker compose down -v`, `Remove-Item .env`, then steps 3–7 again.

## What's built, and what's still a skeleton

| Works now (tested end to end) | Still to build |
| --- | --- |
| HTTPS via Caddy; admin login with HttpOnly cookies, refresh rotation, logout, 5-try lockout | React frontend (`web/dist` is a placeholder) |
| CSRF check on every command; commands → NATS request/reply with audit log; clear errors when no agent answers | Agents themselves (gateway returns 503 "no service is handling …" until they run) |
| Camera proxy: go2rtc → gateway → browser MJPEG and snapshots, login required | Real P1/A1 test of `bambu-cam` |
| WebSocket bridge with subject allow-list (command subjects are never forwarded); 4401 close when not logged in | WebSocket replay from JetStream after a reconnect (today: resync through REST) |
| Ledger: income on payment (VAT split), refunds, PO = cash expense, per-job grams/kWh = allocated (excluded from profit), duplicate-safe, DLQ after 5 failures | Finance settings UI and more finance read endpoints (only `/api/v1/finance/summary` exists); edit settings in Postgres for now |
| JetStream streams EVENTS / TELEMETRY / DLQ created automatically | Separate NATS permissions per user (both users have full access today); database migrations tool (Alembic) |

The default finance settings (VAT 18%, 64 agorot/kWh, 150 W, ₪85/kg) are **placeholders**. Set real values with your accountant and from your electricity bill.
