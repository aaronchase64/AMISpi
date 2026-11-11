# SPDX-License-Identifier: GPL-2.0-or-later
"""Small web UI (stdlib only): live values, settings, log, restart."""

from __future__ import annotations

import base64
import collections
import hmac
import json
import logging
import threading
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from . import config

log = logging.getLogger("amis.web")


class LogBuffer(logging.Handler):
    def __init__(self, size: int = 300) -> None:
        super().__init__()
        self.lines: collections.deque[str] = collections.deque(maxlen=size)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:  # pragma: no cover
            pass

    def snapshot(self) -> list[str]:
        with self.lock:
            return list(self.lines)


def masked(cfg: dict) -> dict:
    out = {s: dict(v) for s, v in cfg.items()}
    for sec, key in config.SECRETS:
        if out[sec][key]:
            out[sec][key] = config.MASK
    return out


def merge_posted(current: dict, posted: dict) -> dict:
    """Apply posted values onto a copy of the current config (type-checked, masked secrets kept)."""
    new = {s: dict(v) for s, v in current.items()}
    if not isinstance(posted, dict):
        raise ValueError("Configuration must be a JSON object")
    for sec, values in posted.items():
        if sec not in new or not isinstance(values, dict):
            raise ValueError("unknown section %r" % sec)
        for key, value in values.items():
            if key not in new[sec]:
                raise ValueError("unknown option %s.%s" % (sec, key))
            old = new[sec][key]
            like = config.DEFAULTS[sec][key]
            if (sec, key) in config.SECRETS and value == config.MASK:
                continue
            if isinstance(like, bool):
                if type(value) is bool:
                    pass
                elif isinstance(value, str):
                    value = config._cast(value, like)
                else:
                    raise ValueError("%s.%s must be true or false" % (sec, key))
            elif isinstance(like, int):
                if isinstance(value, bool) or not isinstance(value, (str, int)):
                    raise ValueError("%s.%s must be an integer" % (sec, key))
                value = int(value)
            else:
                if not isinstance(value, str):
                    raise ValueError("%s.%s must be text" % (sec, key))
                value = str(value).strip() if key != "password" else str(value)
            new[sec][key] = value
    return new


class WebUI:
    def __init__(self, get_cfg: Callable[[], dict], get_status: Callable[[], dict],
                 apply_cfg: Callable[[dict], None], logbuf: LogBuffer, restart: Callable[[], None]) -> None:
        self.get_cfg, self.get_status, self.apply_cfg = get_cfg, get_status, apply_cfg
        self.logbuf, self.restart = logbuf, restart
        self.httpd: ThreadingHTTPServer | None = None
        self._changes = threading.Lock()

    def start(self, host: str, port: int) -> None:
        ui = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "amis-pi"

            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, fmt, *args):  # quiet
                log.debug(fmt, *args)

            def _auth(self) -> bool:
                web = ui.get_cfg()["web"]
                if not web["password"]:
                    return True
                hdr = self.headers.get("Authorization", "")
                if hdr.startswith("Basic "):
                    try:
                        user, _, pw = base64.b64decode(hdr[6:]).decode("utf-8").partition(":")
                    except Exception:
                        user, pw = "", ""
                    ok_u = hmac.compare_digest(user.encode(), web["username"].encode())
                    ok_p = hmac.compare_digest(pw.encode(), web["password"].encode())
                    if ok_u and ok_p:
                        return True
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="amis-pi"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, obj: Any, code: int = 200) -> None:
                self._send(code, json.dumps(obj).encode(), "application/json")

            def do_GET(self) -> None:
                if self.path == "/livez":
                    self._json({"alive": True})
                    return
                if self.path == "/healthz":
                    status = ui.get_status()
                    self._json({"ready": bool(status["online"])}, 200 if status["online"] else 503)
                    return
                if not self._auth():
                    return
                path = self.path.split("?")[0]
                if path == "/":
                    self._send(200, PAGE.encode(), "text/html; charset=utf-8")
                elif path == "/api/status":
                    self._json(ui.get_status())
                elif path == "/api/config":
                    self._json(masked(ui.get_cfg()))
                elif path == "/api/log":
                    self._json(ui.logbuf.snapshot())
                else:
                    self._send(404, b"not found", "text/plain")

            def do_POST(self) -> None:
                if not self._auth():
                    return
                path = self.path.split("?")[0]
                try:
                    origin = self.headers.get("Origin")
                    if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                        self._json({"ok": False, "error": "Cross-origin changes are not allowed"}, 403)
                        return
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 <= length <= 65536:
                        raise ValueError("body too large")
                    body = self.rfile.read(length)
                    if path == "/api/config":
                        with ui._changes:
                            new = merge_posted(ui.get_cfg(), json.loads(body or b"{}"))
                            config.validate(new)
                            ui.apply_cfg(new)
                        self._json({"ok": True})
                    elif path == "/api/restart":
                        threading.Thread(target=ui.restart, daemon=True).start()
                        self._json({"ok": True})
                    else:
                        self._send(404, b"not found", "text/plain")
                except (ValueError, KeyError, TypeError) as exc:
                    self._json({"ok": False, "error": str(exc)}, 400)
                except OSError:
                    log.exception("Could not save configuration")
                    self._json({"ok": False, "error": "Speichern fehlgeschlagen; Dateirechte/Datentraeger pruefen"}, 500)

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, name="web", daemon=True).start()
        log.info("Web UI listening on %s:%d", host, port)

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


FIELDS = [
    ("meter", "Zähler / Lesekopf", [
        ("key", "AMIS-Schlüssel (32 Hex)", "password"), ("port", "Serielle Schnittstelle", "text"),
        ("stale_after", "Daten ungültig nach (s)", "number"), ("serialnumber", "Seriennummer (nur MQTT)", "text"),
        ("simulate", "Simulation (Demo-Daten)", "bool")]),
    ("mqtt", "MQTT", [
        ("enabled", "MQTT aktiv", "bool"), ("host", "Broker", "text"), ("port", "Port", "number"),
        ("username", "Benutzer", "text"), ("password", "Passwort", "password"), ("tls", "TLS", "bool"),
        ("tls_insecure", "TLS-Zertifikat nicht prüfen", "bool"), ("client_id", "Client-ID", "text"),
        ("topic", "Topic (JSON)", "text"), ("status_topic", "Status-Topic", "text"),
        ("interval", "Intervall (s)", "number"), ("qos", "QoS (0/1)", "number"), ("retain", "Retain", "bool"),
        ("key_style", "Schlüsselstil (dots/underscore)", "text"), ("saldo_offset", "Saldo-Offset (W)", "number"),
        ("saldo_invert", "Saldo invertieren", "bool")]),
    ("modbus", "Fronius Modbus/TCP", [
        ("enabled", "Emulation aktiv", "bool"), ("host", "Bind-Adresse", "text"), ("port", "Port", "number")]),
    ("web", "Web-UI", [
        ("enabled", "Web-UI aktiv (Änderung wirkt nach Neustart des Containers)", "bool"),
        ("host", "Bind-Adresse", "text"), ("port", "Port", "number"),
        ("username", "Benutzer", "text"), ("password", "Passwort (leer = kein Login)", "password")]),
    ("log", "Log", [("level", "Level (DEBUG/INFO/WARNING/ERROR)", "text")]),
]

PAGE = """<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>AMIS Pi</title>
<style>
:root{--bg:#f6f7f9;--fg:#1c2330;--card:#fff;--line:#d9dde3;--acc:#0b6bcb;--ok:#1a7f37;--bad:#c62828}
@media(prefers-color-scheme:dark){:root{--bg:#14171c;--fg:#e6e9ee;--card:#1d222a;--line:#323943;--acc:#5aa2ff;--ok:#4cc26b;--bad:#ff6b6b}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:16px}
h1{font-size:20px}h2{font-size:16px;margin:0 0 10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px;margin-bottom:14px}
table{border-collapse:collapse;width:100%}td{padding:4px 6px;border-bottom:1px solid var(--line)}td:last-child{text-align:right;font-variant-numeric:tabular-nums}
label{display:grid;grid-template-columns:minmax(120px,1fr) 2fr;gap:8px;align-items:center;margin:6px 0}
input[type=text],input[type=password],input[type=number]{width:100%;box-sizing:border-box;padding:6px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}
button{padding:8px 14px;border:0;border-radius:6px;background:var(--acc);color:#fff;font-size:15px;cursor:pointer;margin-right:8px}
button.sec{background:transparent;color:var(--fg);border:1px solid var(--line)}
.ok{color:var(--ok)}.bad{color:var(--bad)}pre{max-height:260px;overflow:auto;font-size:12px;white-space:pre-wrap}
#msg{margin-left:8px}
</style><main>
<h1>AMIS Smartmeter Reader</h1>
<div class="card"><h2>Status</h2><div id="st">…</div><table id="vals"></table></div>
<form class="card" id="f"><h2>Einstellungen</h2><div id="fields"></div>
<button type="submit">Speichern &amp; anwenden</button><button type="button" class="sec" id="rs">Neu starten</button><span id="msg"></span></form>
<div class="card"><h2>Log</h2><pre id="log"></pre></div></main>
<script>
const FIELDS=__FIELDS__;
const $=id=>document.getElementById(id);
const READINGS=[
 ['1.8.0','1.8.0 (Energie A+)','kWh',true],
 ['2.8.0','2.8.0 (Energie A-)','kWh',true],
 ['3.8.1','3.8.1 (Energie R+)','kVarh',false],
 ['4.8.1','4.8.1 (Energie R-)','kVarh',false],
 ['1.7.0','1.7.0 (Wirk. P+)','kW',true],
 ['2.7.0','2.7.0 (Wirk. P-)','kW',true],
 ['saldo','Saldo 1.7.0-2.7.0','kW',true],
 ['3.7.0','3.7.0 (Blind. Q+)','kVar',false],
 ['4.7.0','4.7.0 (Blind. Q-)','kVar',false],
 ['inkasso','1.128.0 (Inkasso)','',false]];
function meterTime(ts){
 const parts=new Intl.DateTimeFormat('de-DE',{timeZone:'Europe/Vienna',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date(ts*1000));
 const p=Object.fromEntries(parts.map(v=>[v.type,v.value]));
 return `${p.day}.${p.month}.${p.year} ${p.hour}:${p.minute}:${p.second}`}
function readingRows(values){
 let rows=`<tr><td>Uhrzeit (Zähler)</td><td>${meterTime(values.time)}</td></tr>`;
 for(const [key,label,unit,bold] of READINGS){
  const value=key==='saldo'?values.saldo:key==='inkasso'?values.inkasso:values.obis[key];
  const text=value==null?'–':unit?(Number(value)/1000).toFixed(3).replace('.',',')+' '+unit:String(value);
  const style=key==='saldo'?` style="color:${value>0?'#FF0000':'var(--ok)'}"`:'';
  rows+=`<tr><td>${label}</td><td${style}>${bold?'<b>':''}${esc(text)}${bold?'</b>':''}</td></tr>`}
 return rows}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
async function build(){
 const cfg=await (await fetch('/api/config')).json();let h='';
 for(const [sec,title,fs] of FIELDS){h+=`<h3>${title}</h3>`;
  for(const [k,l,t] of fs){const v=cfg[sec][k],n=`${sec}.${k}`;
   h+= t==='bool'?`<label>${l}<input type="checkbox" name="${n}" ${v?'checked':''}></label>`
    :`<label>${l}<input type="${t==='password'?'password':t==='number'?'number':'text'}" name="${n}" value="${esc(v)}" autocomplete="off"></label>`}}
 $('fields').innerHTML=h}
$('f').onsubmit=async e=>{e.preventDefault();const o={};
 for(const el of $('f').elements){if(!el.name)continue;const [s,k]=el.name.split('.');o[s]=o[s]||{};
  o[s][k]=el.type==='checkbox'?el.checked:el.value}
 const r=await fetch('/api/config',{method:'POST',body:JSON.stringify(o)});const j=await r.json();
 $('msg').className=j.ok?'ok':'bad';$('msg').textContent=j.ok?'Gespeichert und angewendet':j.error;
 if(j.ok){setTimeout(()=>{$('msg').textContent=''},4000);refresh()}};
$('rs').onclick=async()=>{await fetch('/api/restart',{method:'POST'});$('msg').className='ok';$('msg').textContent='Neustart …'};
async function refresh(){
 try{const s=await (await fetch('/api/status')).json();
  let h=`Zähler: <b class="${s.online?'ok':'bad'}">${s.online?'Daten OK':'keine Daten'}</b>${s.simulate?' (Simulation)':''}`;
  h=`<div>${h}</div>`;
  h+=`<div>Fronius-Modbus: ${s.modbus?`<b class="${s.modbus.valid?'ok':'bad'}">${s.modbus.valid?'liefert Werte':'ungültig'}</b>`:'deaktiviert'}</div>`;
  if(s.mqtt)h+=`<div>MQTT: <b class="${s.mqtt.connected?'ok':'bad'}">${s.mqtt.connected?'verbunden':'getrennt'}</b></div>`;
  $('st').innerHTML=h;let t='';
  if(s.error){const msg=document.createElement('p');msg.className='bad';msg.textContent=s.error;$('st').appendChild(msg)}
  if(s.values)t=readingRows(s.values);
  $('vals').innerHTML=t;
  const l=await (await fetch('/api/log')).json();const p=$('log');p.textContent=l.join('\\n');}catch(e){$('st').textContent='keine Verbindung'}}
build();refresh();setInterval(refresh,3000);
</script></html>""".replace("__FIELDS__", json.dumps(FIELDS))
