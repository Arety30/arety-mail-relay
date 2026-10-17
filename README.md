# arety-mail-relay

arety-mail-relay is a very small, RAM-gated outbound mail relay written by Syed Abrar Ali. A web application hosted elsewhere (one that has no mail server of its own) POSTs a password-reset message to this relay over HTTPS, and the relay hands it to a local Proton Mail bridge (hydroxide) over SMTP so it leaves from a real sender address. The twist is that the relay, the bridge and the public tunnel are only allowed to run while the laptop hosting them has more than 4 GB of memory available; a one-minute timer starts and stops the whole chain to protect the machine's other workloads. It solves two problems at once: giving a cloud-hosted site a trustworthy sender without paying for a mail service, and making sure that convenience never pushes the laptop into swap.

## Where it is used

In Abrar's setup a chat site runs on free hosted notebook kernels that cannot send e-mail. When a user requests a password reset, the site calls this relay through a tunnel that terminates on his laptop. The relay sends the mail through the hydroxide bridge for a `no-reply@` address on his domain. Because the laptop also runs local language models and other services, `gate.sh` runs every minute from a systemd user timer and keeps mail switched off whenever memory is tight.

## How it works

### Request flow

```
site ──POST /send──▶ tunnel ──▶ relay.py (127.0.0.1:8990) ──SMTP 127.0.0.1:1026──▶ hydroxide ──▶ Proton Mail
```

`relay.py` is a `ThreadingHTTPServer` bound to `127.0.0.1:8990` with a single endpoint:

- `POST /send` with header `Authorization: Bearer <RELAY_TOKEN>` and a JSON body `{"to", "subject", "text", "html"}` (`html` optional).

Decision rules, in order:

1. Any path other than `/send`, or a token that does not match (compared with `hmac.compare_digest`), returns `403 {"error": "forbidden"}`.
2. If `MemAvailable` in `/proc/meminfo` is below 4 GiB, the relay refuses with `503 {"error": "mail is paused (laptop low on memory)"}` even if it happens to be running.
3. The body is read with a 200 kB cap; fields are truncated (`to` and `subject` 200 chars, `text` 20,000, `html` 50,000).
4. The sender account is looked up in `~/.config/proton-mail-mcp/accounts.json` (the account whose `email` equals the configured `FROM` address); an `EmailMessage` is built with a plain-text body and an optional HTML alternative, then sent with `smtplib.SMTP` to the local bridge using that account's bridge login and password.
5. Success returns `200 {"sent": true}`; any exception returns `500` with the exception type and message (truncated).

Request logging is disabled (`log_message` is a no-op), so recipients are not written to the journal.

### Memory gate

`gate.sh` is a oneshot script run every minute by a systemd user timer. It reads `MemAvailable` and manages three user units together: the hydroxide bridge, the relay, and the tunnel that exposes it.

| Available RAM | Action |
|---|---|
| below 4096 MB | stop all three units (logs a line only if they were running) |
| 4400 MB or more | start all three if any is inactive |
| between | leave things as they are |

The 300 MB hysteresis band prevents the act of starting the services from immediately tipping memory back under the threshold and causing flapping.

### Components

| File | Role |
|---|---|
| `relay.py` | HTTP server, bearer-token check, memory check, SMTP send via the bridge |
| `gate.sh` | Minute-by-minute RAM gate that starts/stops hydroxide, relay and tunnel |
| `~/.config/arety-mail-relay/env` (not in repo) | `RELAY_TOKEN` and tunnel settings, loaded by the systemd units |
| `~/.config/proton-mail-mcp/accounts.json` (not in repo) | Bridge accounts; the relay reads the sender's login and bridge password from here |

## Setup

Requirements: Linux with systemd user services, Python 3 (standard library only), a running hydroxide bridge listening for SMTP on `127.0.0.1:1026` with the sender account logged in, and some way to expose `127.0.0.1:8990` to the calling site (a tunnel service is what the gate manages).

1. Clone to `~/arety-mail-relay`.
2. Edit the `FROM` constant in `relay.py` to the sender address that exists in your bridge `accounts.json` (for example `no-reply@example.com`).
3. Create `~/.config/arety-mail-relay/env`:

   ```
   RELAY_TOKEN=<long random string>
   # plus whatever your tunnel binary needs
   ```

4. Create the user units (names are what `gate.sh` expects):

   ```ini
   # ~/.config/systemd/user/arety-mail-relay.service
   [Unit]
   Description=Mail relay for password-reset mail; started only by arety-mail-gate
   [Service]
   EnvironmentFile=%h/.config/arety-mail-relay/env
   ExecStart=/usr/bin/python3 %h/arety-mail-relay/relay.py
   Restart=on-failure
   ```

   ```ini
   # ~/.config/systemd/user/arety-mail-gate.service      (Type=oneshot, ExecStart=%h/arety-mail-relay/gate.sh)
   # ~/.config/systemd/user/arety-mail-gate.timer        (OnBootSec=2min, OnUnitActiveSec=1min)
   # ~/.config/systemd/user/arety-mail-tunnel.service    (your tunnel, EnvironmentFile as above)
   # hydroxide.service                                   (the Proton bridge)
   ```

5. `systemctl --user daemon-reload && systemctl --user enable --now arety-mail-gate.timer`

Do not enable `arety-mail-relay.service` or the tunnel directly; the gate owns their lifecycle.

## Usage

The relay has no CLI. From the calling application:

```bash
curl -s -X POST https://HOST/send \
  -H "Authorization: Bearer $RELAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"to":"user@example.com","subject":"Reset your password",
       "text":"Open this link to reset: https://example.com/reset/abc",
       "html":"<p>Open <a href=\"https://example.com/reset/abc\">this link</a> to reset.</p>"}'
# -> {"sent": true}
```

Operational checks:

```bash
systemctl --user list-timers arety-mail-gate.timer
systemctl --user status arety-mail-relay.service hydroxide.service arety-mail-tunnel.service
journalctl --user -u arety-mail-gate.service --since today   # "N MB free: starting/stopping mail"
curl -s -X POST http://127.0.0.1:8990/send -H 'Authorization: Bearer wrong' -d '{}'   # {"error": "forbidden"}
```

## Project structure

```
arety-mail-relay/
├── relay.py   HTTP relay (about 70 lines, standard library only)
└── gate.sh    RAM gate run by a systemd user timer
```

## Limitations and notes

- Single purpose and single sender: the `FROM` address and the 4 GiB threshold are constants in `relay.py`; the thresholds in `gate.sh` are hard-coded too. Change them in the source.
- There is no rate limiting or recipient allow-list; the bearer token is the only access control, so the relay must stay bound to localhost and be reached only through an authenticated or obscure tunnel URL.
- Mail is deliberately unavailable whenever the laptop is low on memory. Callers must handle `503` (and connection failures while the tunnel is down) by telling the user to retry later.
- The relay depends on the bridge's `accounts.json` layout (a list, or an object with an `accounts` list, with `email`, optional `login`, and `password` fields) and on hydroxide listening on SMTP port 1026.
- TLS is provided by the tunnel, not by the relay itself; traffic between the tunnel client and the relay stays on the loopback interface.
