"""Mail relay for example.com: the Kaggle-hosted site POSTs password-reset mail here and it goes out
through the local Proton bridge (hydroxide) as no-reply@example.com. Only runs while the laptop has more than
4 GB of RAM free (gate.sh starts and stops it); it also refuses to send below that.

    POST /send  Authorization: Bearer <RELAY_TOKEN>   {"to", "subject", "text", "html"}
"""
import hmac, json, os, smtplib
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOKEN = os.environ["RELAY_TOKEN"]
ACCOUNTS = Path.home() / ".config/proton-mail-mcp/accounts.json"
FROM = "no-reply@example.com"
MIN_FREE_KB = 4 * 1024 * 1024


def free_kb() -> int:
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable:"):
            return int(line.split()[1])
    return 0


def send(to: str, subject: str, text: str, html: str = "") -> None:
    accts = json.loads(ACCOUNTS.read_text())
    accts = accts if isinstance(accts, list) else accts.get("accounts", [])
    acct = next(a for a in accts if a.get("email") == FROM)
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = f"Arety <{FROM}>", to, subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    with smtplib.SMTP("127.0.0.1", 1026, timeout=60) as s:
        s.login(acct.get("login") or acct["email"], acct["password"])
        s.send_message(msg)


class H(BaseHTTPRequestHandler):
    def _reply(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/send" or not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + TOKEN):
            return self._reply(403, {"error": "forbidden"})
        if free_kb() < MIN_FREE_KB:
            return self._reply(503, {"error": "mail is paused (laptop low on memory)"})
        try:
            b = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 200_000)))
            send(str(b["to"])[:200], str(b["subject"])[:200], str(b["text"])[:20000], str(b.get("html", ""))[:50000])
        except Exception as e:  # noqa: BLE001
            return self._reply(500, {"error": f"{type(e).__name__}: {e}"[:300]})
        self._reply(200, {"sent": True})

    def log_message(self, *a):
        pass


ThreadingHTTPServer(("127.0.0.1", 8990), H).serve_forever()
