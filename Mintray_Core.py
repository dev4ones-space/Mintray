#!/usr/bin/env python3
"""
Mintray_Core.py - shared backend for MintRay: Xray config generation,
subscription fetching/parsing, SOCKS5 ping testing, macOS route/DNS
management, and the App/ProcMgr/NetState state machine.

Zero UI code lives here. Mintray_TUI.py (curses) and Mintray_GUI.py
(pygame) both import this unchanged and only add a rendering layer on
top - fix a bug here once, both renderers get the fix.

Requires:
    - xray binary on PATH (brew install xray)
    - tun2socks binary on PATH (github.com/xjasonlyu/tun2socks releases)
    - sudo (creates TUN device, modifies routes, changes DNS)

Stdlib only. No third-party Python packages.
"""

from __future__ import annotations

import argparse
import atexit
import base64
import concurrent.futures
import dataclasses
import gzip
import ipaddress
import json
import os
import shutil
import signal
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Bump for every real change: X.Y -> X.(Y+1) normally, X.Y -> (X+1).0 for a
# major change. This is the single source of truth - TUI reads it from here.
MINTRAY_VERSION = "1.0"



# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

STATE_DIR = Path.home() / ".mintray"
CONFIG_PATH = STATE_DIR / "xray-config.json"
SERVERS_CACHE = STATE_DIR / "servers.json"
LOG_PATH = STATE_DIR / "mintray.log"                 # our own diagnostic trail (RunCmd echoes etc.)
XRAY_PROC_LOG = STATE_DIR / "xray-proc.log"        # xray's own stdout/stderr - truncated fresh each connect
TUN2SOCKS_PROC_LOG = STATE_DIR / "tun2socks-proc.log"  # tun2socks's own stdout/stderr - same deal

TUN_DEVICE_DEFAULT = "utun123"
TUN_ADDR = "198.18.0.1"     # point-to-point addr on the TUN side
TUN_GW = "198.18.0.1"       # gateway tun2socks listens behind
SOCKS_PORT_DEFAULT = 10808

# generate_204 is GrapheneOS's connectivity-check endpoint - plain HTTPS,
# returns a bare 204 with no body, cheap and doesn't leak much about us.
PING_HOST = "time.grapheneos.org"
PING_PATH = "/generate_204"
PING_BASE_PORT = 19000
STATS_PORT = 10085  # Xray's own stats API - loopback only, separate from tunnel traffic


def Log(msg: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")


def ReadLogTail(path: Path = LOG_PATH, n: int = 8, max_len: int = 1200) -> str:
    """Last few non-empty lines from a specific log file - meant to be
    embeddable directly in a curses status line/exception message. Point
    this at a process's OWN log (not the shared mintray.log), or you'll get
    our own RunCmd diagnostic echoes instead of the actual crash reason.

    xray and tun2socks both emit structured zap-style JSON logs
    ({"level":"fatal","msg":"...long useful text..."}) - naively slicing
    raw text cuts the useful part off mid-string behind ts/caller noise,
    so JSON lines get their level+msg pulled out instead."""
    if not path.exists():
        return "(no log file yet)"
    raw_lines = [l for l in path.read_text(errors="replace").splitlines() if l.strip()]
    if not raw_lines:
        return "(process produced zero output before dying - check for a stale process already holding the device, e.g. ps aux | grep tun2socks)"

    formatted = []
    for line in raw_lines[-n:]:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            formatted.append(line)
            continue
        if isinstance(entry, dict) and "msg" in entry:
            level = entry.get("level", "")
            formatted.append(f"[{level}] {entry['msg']}" if level else str(entry["msg"]))
        else:
            formatted.append(line)

    return " | ".join(formatted)[:max_len]


# --------------------------------------------------------------------------
# Xray config generation
# --------------------------------------------------------------------------

def BuildXrayConfig(outbound: dict, socks_port: int, stats_port: int | None = None) -> dict:
    """Wrap a single Xray outbound (from subscription / local file) into a
    full client config with a local SOCKS5 inbound for tun2socks to hit.
    If stats_port is given, also enables Xray's stats API on that local
    port so speed can be read from real traffic counters."""
    outbound = dict(outbound)
    outbound["tag"] = "proxy"  # forced, not just default - keeps stats queries
    # deterministic regardless of what the server's display name contains
    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "port": socks_port,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": True},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
            }
        ],
        "outbounds": [
            outbound,
            {"protocol": "freedom", "tag": "direct"},
            {"protocol": "blackhole", "tag": "block"},
        ],
    }
    if stats_port is not None:
        config["stats"] = {}
        config["api"] = {"tag": "api", "listen": f"127.0.0.1:{stats_port}", "services": ["StatsService"]}
        # counters only, no per-user tracking - this is a single-outbound
        # personal client, not multi-tenant, and the increment is a documented
        # low-overhead operation, not something that touches actual throughput
        config["policy"] = {"system": {"statsOutboundUplink": True, "statsOutboundDownlink": True}}
    return config


def WriteXrayConfig(config: dict) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))
    return CONFIG_PATH


def QueryStats(xray_bin: str, stats_port: int) -> dict[str, int]:
    """Queries xray's own stats API (a tiny local gRPC call via the xray
    CLI) for the outbound>>>proxy>>>traffic>>>{uplink,downlink} byte
    counters. Runs from a background thread - never called from the
    render loop, so it can never add latency to the TUI."""
    result = subprocess.run(
        [xray_bin, "api", "statsquery", f"--server=127.0.0.1:{stats_port}"],
        capture_output=True, text=True, timeout=3,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "statsquery failed")
    payload = json.loads(result.stdout)
    return {entry["name"]: entry.get("value", 0) for entry in payload.get("stat", [])}


def FormatSpeed(bits_per_sec: float) -> str:
    mbps = bits_per_sec / 1_000_000
    if mbps >= 0.1:
        return f"{mbps:.1f} Mbps"
    return f"{bits_per_sec / 1_000:.0f} Kbps"


# --------------------------------------------------------------------------
# Subscription / server list loading
#
# NOTE: this is the one part I can't nail exactly without seeing your
# xray-sub.py output schema. FetchSubscription tries a few common shapes
# (raw list of outbounds, {"servers": [...]}, {"outbounds": [...]}) and
# falls back to dumping the raw response to STATE_DIR/servers.raw so you
# can see what actually came back and adjust ParseSubscriptionPayload.
# --------------------------------------------------------------------------

def FetchSubscription(url: str) -> list[dict]:
    """Fetch and parse ONE subscription. Caller is responsible for caching -
    that lets FetchAllSubscriptions merge several without each call
    clobbering the cache with just its own results."""
    req = urllib.request.Request(url, headers={"User-Agent": "mintray/1", "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            headers = resp.headers
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLCertVerificationError):
            raise RuntimeError(
                "TLS cert verification failed fetching the subscription - this is "
                "your Python install's CA bundle, not the subscription server. Fix: "
                "run 'Install Certificates.command' (python.org builds) or "
                "'sudo python3 -m pip install certifi --break-system-packages' then "
                "re-run with SSL_CERT_FILE=$(python3 -c \"import certifi;print(certifi.where())\")"
            ) from e
        raise

    # v2rayTun/Hiddify/v2rayNG convention: subscription servers can return
    # these response headers alongside the actual payload. HTTP headers are
    # case-insensitive, resp.headers.get() handles that correctly.
    support_url = headers.get("support-url")
    web_page_url = headers.get("profile-web-page-url")
    profile_title = headers.get("profile-title")
    if support_url or web_page_url or profile_title:
        meta = LoadSubMeta()
        meta[url] = {"support_url": support_url, "web_page_url": web_page_url, "profile_title": profile_title}
        SaveSubMeta(meta)

    try:
        return DecodeSubscriptionBody(raw)
    except Exception:
        (STATE_DIR / "servers.raw").write_bytes(raw)
        raise


def FetchAllSubscriptions(urls: list[str]) -> list[dict]:
    """Fetch every saved subscription and merge. One bad/unreachable
    subscription shouldn't take down the others - only raises if ALL of
    them fail."""
    all_servers: list[dict] = []
    errors: list[str] = []
    for url in urls:
        try:
            all_servers.extend(FetchSubscription(url))
        except Exception as e:
            errors.append(f"{url}: {e}")
            Log(f"subscription fetch failed for {url}: {e}")
    if errors and not all_servers:
        raise RuntimeError("all subscriptions failed: " + " | ".join(errors))
    if errors:
        Log(f"continuing with {len(all_servers)} servers from the subscriptions that did work; failures: {errors}")
    return all_servers


# --------------------------------------------------------------------------
# Saved subscriptions (~/.mintray/subscriptions.json) - so --subscription-url
# doesn't have to be retyped on every run, and so more than one can be used
# at once (servers from all of them get merged into one list).
# --------------------------------------------------------------------------

SUBS_PATH = STATE_DIR / "subscriptions.json"
SUB_META_PATH = STATE_DIR / "subscription_meta.json"


def LoadSubMeta() -> dict:
    if SUB_META_PATH.exists():
        try:
            return json.loads(SUB_META_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def SaveSubMeta(meta: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SUB_META_PATH.write_text(json.dumps(meta, indent=2))


def GetAnySupportUrl() -> str | None:
    """First support-url found across saved subscriptions that have one -
    subscriptions are per-provider, providers set this header themselves,
    so there's no single canonical 'the' support URL when several are
    saved. First-found is a reasonable, simple default."""
    meta = LoadSubMeta()
    for url in LoadSavedSubs():
        info = meta.get(url, {})
        if info.get("support_url"):
            return info["support_url"]
    return None


# --------------------------------------------------------------------------
# Settings persistence - editable via the TUI's settings view (or any other
# renderer). Only overrides an arg if it's still at its hardcoded default,
# so an explicit CLI flag always wins over a saved setting.
# --------------------------------------------------------------------------

SETTINGS_PATH = STATE_DIR / "settings.json"
SETTINGS_FIELDS = ["xray_bin", "tun2socks_bin", "ping_host", "ping_path"]
SETTINGS_DEFAULTS = {
    "xray_bin": "xray",
    "tun2socks_bin": "tun2socks",
    "ping_host": PING_HOST,
    "ping_path": PING_PATH,
}
SETTINGS_LABELS = {
    "xray_bin": "xray binary path",
    "tun2socks_bin": "tun2socks binary path",
    "ping_host": "ping host",
    "ping_path": "ping path",
}


def LoadSettings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def SaveSettings(values: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    current = LoadSettings()
    current.update(values)
    SETTINGS_PATH.write_text(json.dumps(current, indent=2))


def ApplySettings(args: argparse.Namespace) -> None:
    saved = LoadSettings()
    for field in SETTINGS_FIELDS:
        if field in saved and str(getattr(args, field, SETTINGS_DEFAULTS[field])) == str(SETTINGS_DEFAULTS[field]):
            setattr(args, field, saved[field])


def LoadSavedSubs() -> list[str]:
    if not SUBS_PATH.exists():
        return []
    try:
        data = json.loads(SUBS_PATH.read_text())
    except json.JSONDecodeError:
        return []
    return [str(u) for u in data] if isinstance(data, list) else []


def SaveSubs(urls: list[str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SUBS_PATH.write_text(json.dumps(urls, indent=2))


def AddSub(url: str) -> list[str]:
    subs = LoadSavedSubs()
    if url not in subs:
        subs.append(url)
        SaveSubs(subs)
    return subs


def RemoveSub(identifier: str) -> tuple[list[str], str | None]:
    """identifier is either the exact URL or a 1-based index (as shown by --list-subs)."""
    subs = LoadSavedSubs()
    removed = None
    if identifier.isdigit():
        idx = int(identifier) - 1
        if 0 <= idx < len(subs):
            removed = subs.pop(idx)
    elif identifier in subs:
        subs.remove(identifier)
        removed = identifier
    if removed is not None:
        SaveSubs(subs)
    return subs, removed


def DecodeSubscriptionBody(raw: bytes) -> list[dict]:
    """Handles the three shapes a subscription response might actually be:
    JSON, a base64 blob of newline-separated share links (the de facto
    v2ray/xray subscription standard - what xray-sub.py actually returns),
    or plaintext share links, one per line, no base64 wrapper."""
    try:
        raw = gzip.decompress(raw)  # your infra gzips payloads for DPI evasion
    except OSError:
        pass  # wasn't gzipped, that's fine

    text = raw.decode("utf-8", errors="replace").strip()

    try:
        return ParseJsonPayload(json.loads(text))
    except json.JSONDecodeError:
        pass

    links = TryDecodeBase64Links(text)
    if links:
        return [ParseShareLink(link) for link in links]

    plain_links = [line.strip() for line in text.splitlines() if IsShareLink(line.strip())]
    if plain_links:
        return [ParseShareLink(link) for link in plain_links]

    raise RuntimeError(
        "unrecognized subscription format (not JSON, not base64 share-links, "
        "not plaintext share-links)"
    )


def ParseJsonPayload(payload) -> list[dict]:
    """Fallback shape - some subscription servers emit ready-to-use Xray
    outbound objects directly as JSON instead of share links."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("servers", "outbounds", "nodes"):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
    raise RuntimeError(f"unrecognized JSON subscription shape: {payload if isinstance(payload, list) else list(payload)}")


SHARE_LINK_SCHEMES = ("vless://", "vmess://", "trojan://", "ss://")


def IsShareLink(s: str) -> bool:
    return s.startswith(SHARE_LINK_SCHEMES)


def TryDecodeBase64Links(text: str) -> list[str]:
    padded = text + "=" * (-len(text) % 4)
    try:
        decoded = base64.b64decode(padded, validate=True).decode("utf-8")
    except Exception:
        return []
    lines = [line.strip() for line in decoded.splitlines() if line.strip()]
    if lines and all(IsShareLink(line) for line in lines):
        return lines
    return []


def ParseShareLink(uri: str) -> dict:
    scheme = uri.split("://", 1)[0]
    if scheme == "vless":
        return ParseVlessUri(uri)
    raise ValueError(
        f"{scheme}:// share links aren't implemented yet (only vless:// is) - "
        "say the word if you need vmess/trojan/ss too"
    )


def ParseVlessUri(uri: str) -> dict:
    """vless://uuid@host:port?encryption=..&security=..&sni=..&fp=..&pbk=..
    &sid=..&type=..&path=..&mode=..#display-name  ->  Xray outbound object"""
    u = urllib.parse.urlparse(uri)
    q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
    name = urllib.parse.unquote(u.fragment) or f"{u.hostname}:{u.port}"
    uuid = urllib.parse.unquote(u.username or "")

    user = {"id": uuid, "encryption": q.get("encryption", "none")}
    if q.get("flow"):
        user["flow"] = q["flow"]

    network = q.get("type", "tcp")
    security = q.get("security", "none")

    stream: dict = {"network": network, "security": security}

    if security == "reality":
        reality = {
            "serverName": q.get("sni", ""),
            "publicKey": q.get("pbk", ""),
            "shortId": q.get("sid", ""),
        }
        if "fp" in q:
            reality["fingerprint"] = q["fp"]
        if "spx" in q:
            reality["spiderX"] = q["spx"]
        stream["realitySettings"] = reality
    elif security == "tls":
        tls = {}
        if "sni" in q:
            tls["serverName"] = q["sni"]
        if "fp" in q:
            tls["fingerprint"] = q["fp"]
        stream["tlsSettings"] = tls

    if network == "xhttp":
        xhttp = {}
        if "path" in q:
            xhttp["path"] = q["path"]
        if "mode" in q:
            xhttp["mode"] = q["mode"]
        if "host" in q:
            xhttp["host"] = q["host"]
        stream["xhttpSettings"] = xhttp
    elif network == "ws":
        ws = {}
        if "path" in q:
            ws["path"] = q["path"]
        if "host" in q:
            ws["headers"] = {"Host": q["host"]}
        stream["wsSettings"] = ws
    elif network == "grpc":
        grpc = {}
        if "serviceName" in q:
            grpc["serviceName"] = q["serviceName"]
        stream["grpcSettings"] = grpc
    # tcp/raw: no network-specific settings needed

    return {
        "tag": name,
        "protocol": "vless",
        "settings": {
            "vnext": [{
                "address": u.hostname,
                "port": u.port,
                "users": [user],
            }]
        },
        "streamSettings": stream,
    }


def LoadCachedServers() -> list[dict]:
    if SERVERS_CACHE.exists():
        return json.loads(SERVERS_CACHE.read_text())
    return []


def LoadLocalOutbound(path: str) -> dict:
    """Fallback: point --config at a raw Xray outbound JSON block you've
    already got working (e.g. exported from your subscription server)."""
    return json.loads(Path(path).read_text())


# --------------------------------------------------------------------------
# Latency testing
#
# Spins up a *separate* throwaway xray-core instance (not the main tunnel)
# with one SOCKS inbound + routing rule per candidate server, then does a
# minimal stdlib SOCKS5 handshake + raw HTTPS GET to time.grapheneos.org's
# generate_204 through each one, concurrently, timing the round trip.
# --------------------------------------------------------------------------

def RecvExact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise RuntimeError("socket closed mid-handshake")
        buf += chunk
    return buf


def IsIpAddress(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def Socks5Connect(sock: socket.socket, dest_host: str, dest_port: int) -> None:
    """No-auth SOCKS5 handshake + CONNECT, per RFC 1928."""
    sock.sendall(b"\x05\x01\x00")  # ver=5, 1 method offered, no-auth
    greeting = RecvExact(sock, 2)
    if greeting[0] != 0x05 or greeting[1] != 0x00:
        raise RuntimeError(f"SOCKS5 greeting rejected: {greeting!r}")

    if IsIpAddress(dest_host):
        req = b"\x05\x01\x00\x01" + socket.inet_aton(dest_host) + struct.pack(">H", dest_port)
    else:
        host_bytes = dest_host.encode("ascii")
        req = b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + struct.pack(">H", dest_port)
    sock.sendall(req)

    header = RecvExact(sock, 4)  # VER REP RSV ATYP
    if header[1] != 0x00:
        raise RuntimeError(f"SOCKS5 CONNECT failed, REP={header[1]}")
    atyp = header[3]
    if atyp == 0x01:
        RecvExact(sock, 4 + 2)
    elif atyp == 0x03:
        ln = RecvExact(sock, 1)[0]
        RecvExact(sock, ln + 2)
    elif atyp == 0x04:
        RecvExact(sock, 16 + 2)
    else:
        raise RuntimeError(f"unknown ATYP in SOCKS5 reply: {atyp}")


def PingServer(local_socks_port: int, timeout: float = 5.0, ping_host: str = PING_HOST, ping_path: str = PING_PATH) -> float | None:
    """Round-trip time in ms to ping_host through a local SOCKS5 proxy, or
    None on failure/timeout/non-204 response."""
    sock: socket.socket | None = None
    start = time.monotonic()
    try:
        sock = socket.create_connection(("127.0.0.1", local_socks_port), timeout=timeout)
        sock.settimeout(timeout)
        Socks5Connect(sock, ping_host, 443)

        ctx = ssl.create_default_context()
        tls = ctx.wrap_socket(sock, server_hostname=ping_host)
        request = (
            f"GET {ping_path} HTTP/1.1\r\n"
            f"Host: {ping_host}\r\n"
            "Connection: close\r\n"
            "User-Agent: mintray/1\r\n\r\n"
        ).encode()
        tls.sendall(request)

        status_line = b""
        while b"\r\n" not in status_line and len(status_line) < 512:
            chunk = tls.recv(256)
            if not chunk:
                break
            status_line += chunk
        elapsed_ms = (time.monotonic() - start) * 1000

        first_line = status_line.split(b"\r\n", 1)[0].decode(errors="replace")
        if " 204 " in first_line or first_line.rstrip().endswith(" 204"):
            return elapsed_ms
        Log(f"ping({local_socks_port}) unexpected response: {first_line!r}")
        return None
    except Exception as e:
        Log(f"ping({local_socks_port}) failed: {e}")
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def BuildPingTestConfig(servers: list[dict], base_port: int) -> dict:
    """One SOCKS inbound + routing rule per server, all in a single
    throwaway xray-core instance. Internal tags are regenerated so this
    works regardless of what (if anything) the subscription set as tags."""
    outbounds: list[dict] = []
    inbounds: list[dict] = []
    rules: list[dict] = []
    for i, srv in enumerate(servers):
        ob = dict(srv)
        out_tag = f"ping-out-{i}"
        ob["tag"] = out_tag
        outbounds.append(ob)

        in_tag = f"ping-in-{i}"
        inbounds.append({
            "tag": in_tag,
            "listen": "127.0.0.1",
            "port": base_port + i,
            "protocol": "socks",
            "settings": {"auth": "noauth", "udp": False},
        })
        rules.append({"type": "field", "inboundTag": [in_tag], "outboundTag": out_tag})

    outbounds.append({"protocol": "freedom", "tag": "direct"})
    outbounds.append({"protocol": "blackhole", "tag": "block"})
    return {
        "log": {"loglevel": "warning"},
        "inbounds": inbounds,
        "outbounds": outbounds,
        "routing": {"rules": rules},
    }


def PingAllServers(servers: list[dict], xray_bin: str, ping_host: str = PING_HOST, ping_path: str = PING_PATH, on_result=None) -> dict[int, float | None]:
    """Returns {server_index: latency_ms_or_None}. If on_result is given,
    it's called as on_result(index, latency) the moment each individual
    ping finishes - lets a caller show results live as they arrive instead
    of waiting for the whole batch to complete."""
    if not servers:
        return {}

    config = BuildPingTestConfig(servers, PING_BASE_PORT)
    config_path = STATE_DIR / "ping-config.json"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2))

    log_f = open(LOG_PATH, "a")
    proc = subprocess.Popen([xray_bin, "run", "-c", str(config_path)], stdout=log_f, stderr=log_f)
    time.sleep(1.0)  # let xray bind every inbound before we start hammering them

    results: dict[int, float | None] = {}
    try:
        if proc.poll() is not None:
            raise RuntimeError(f"ping-test xray instance exited immediately, check {LOG_PATH}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(servers))) as pool:
            futures = {
                pool.submit(PingServer, PING_BASE_PORT + i, 5.0, ping_host, ping_path): i
                for i in range(len(servers))
            }
            for fut in concurrent.futures.as_completed(futures):
                i = futures[fut]
                val = fut.result()
                results[i] = val
                if on_result is not None:
                    on_result(i, val)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    return results


# --------------------------------------------------------------------------
# macOS network control
# --------------------------------------------------------------------------

@dataclasses.dataclass
class NetState:
    interface: str = ""
    gateway: str = ""
    service: str = ""
    original_dns: list[str] = dataclasses.field(default_factory=list)
    proxy_host: str = ""
    tun_device: str = TUN_DEVICE_DEFAULT
    routes_added: bool = False
    dns_changed: bool = False
    host_route_added: bool = False


def RunCmd(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    Log("$ " + " ".join(args))
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(args)}\n{result.stderr}")
    return result


def DetectDefaultRoute() -> tuple[str, str]:
    """Returns (interface, gateway) for the current default IPv4 route."""
    out = RunCmd("route", "-n", "get", "default").stdout
    iface = gw = ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("interface:"):
            iface = line.split(":", 1)[1].strip()
        elif line.startswith("gateway:"):
            gw = line.split(":", 1)[1].strip()
    if not iface or not gw:
        raise RuntimeError("couldn't detect default route - are you online?")
    return iface, gw


def DetectServiceForInterface(interface: str) -> str:
    """Map a BSD interface name (en0) to its networksetup service name (Wi-Fi)."""
    out = RunCmd("networksetup", "-listallhardwareports").stdout
    blocks = out.split("Hardware Port: ")[1:]
    for block in blocks:
        lines = block.splitlines()
        name = lines[0].strip()
        dev_line = next((l for l in lines if l.startswith("Device:")), "")
        if dev_line.split(":", 1)[1].strip() == interface:
            return name
    raise RuntimeError(f"couldn't map interface {interface} to a networksetup service")


def GetCurrentDns(service: str) -> list[str]:
    out = RunCmd("networksetup", "-getdnsservers", service).stdout.strip()
    if out.startswith("There aren't any DNS Servers"):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def SetDns(service: str, servers: list[str]) -> None:
    RunCmd("networksetup", "-setdnsservers", service, *(servers or ["empty"]))


def RestoreDns(state: NetState) -> None:
    if not state.dns_changed:
        return
    try:
        SetDns(state.service, state.original_dns)
    except Exception as e:
        fallback = " ".join(state.original_dns) if state.original_dns else "empty"
        Log(
            f"failed to restore DNS on {state.service!r} to {state.original_dns!r}: {e}. "
            f"If it looks wrong now, fix manually: networksetup -setdnsservers '{state.service}' {fallback}"
        )
    state.dns_changed = False


def WaitForInterface(device: str, timeout: float = 5.0) -> bool:
    """Poll for the TUN device actually existing rather than guessing a fixed
    sleep is long enough - a freshly downloaded binary's first Gatekeeper
    scan alone can eat more than a second before tun2socks finishes setup."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(["ifconfig", device], capture_output=True, text=True)
        if result.returncode == 0:
            return True
        time.sleep(0.2)
    return False


def BringUpTunInterface(device: str) -> None:
    RunCmd("ifconfig", device, TUN_ADDR, TUN_GW, "up")


def AddHostRoute(host: str, gateway: str) -> None:
    RunCmd("route", "add", "-host", host, gateway)


def DeleteHostRoute(host: str, gateway: str, check: bool = False) -> None:
    RunCmd("route", "delete", "-host", host, gateway, check=check)


def AddFullTunnelRoutes(tun_gw: str) -> None:
    # split-default trick: two /1 routes are more specific than the existing
    # default route, so they win, without us having to touch/replace it
    RunCmd("route", "add", "-net", "0.0.0.0/1", tun_gw)
    RunCmd("route", "add", "-net", "128.0.0.0/1", tun_gw)


def DeleteFullTunnelRoutes(tun_gw: str, check: bool = False) -> None:
    RunCmd("route", "delete", "-net", "0.0.0.0/1", tun_gw, check=check)
    RunCmd("route", "delete", "-net", "128.0.0.0/1", tun_gw, check=check)


def Teardown(state: NetState) -> None:
    """Best-effort cleanup - safe to call even on a half-connected state."""
    if state.routes_added:
        DeleteFullTunnelRoutes(TUN_GW, check=False)
        state.routes_added = False
    if state.host_route_added and state.proxy_host and state.gateway:
        DeleteHostRoute(state.proxy_host, state.gateway, check=False)
        state.host_route_added = False
    RestoreDns(state)


# --------------------------------------------------------------------------
# Process management (xray + tun2socks)
# --------------------------------------------------------------------------

class ProcMgr:
    def __init__(self, xray_bin: str, tun2socks_bin: str):
        self.xray_bin = xray_bin
        self.tun2socks_bin = tun2socks_bin
        self.xray_proc: subprocess.Popen | None = None
        self.tun2socks_proc: subprocess.Popen | None = None
        self.started_at: float = 0.0

    def StartXray(self, config_path: Path) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        log_f = open(XRAY_PROC_LOG, "w")  # truncate - always reflects only the current attempt
        self.xray_proc = subprocess.Popen(
            [self.xray_bin, "run", "-c", str(config_path)],
            stdout=log_f, stderr=log_f,
        )

    def StartTun2socks(self, device: str, socks_port: int, interface: str) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        log_f = open(TUN2SOCKS_PROC_LOG, "w")  # truncate - always reflects only the current attempt
        self.tun2socks_proc = subprocess.Popen(
            [
                self.tun2socks_bin,
                "-device", device,
                "-proxy", f"socks5://127.0.0.1:{socks_port}",
                "-interface", interface,
                "-loglevel", "warn",
            ],
            stdout=log_f, stderr=log_f,
        )
        self.started_at = time.time()

    def Alive(self) -> tuple[bool, bool]:
        xray_ok = self.xray_proc is not None and self.xray_proc.poll() is None
        t2s_ok = self.tun2socks_proc is not None and self.tun2socks_proc.poll() is None
        return xray_ok, t2s_ok

    def Uptime(self) -> float:
        return time.time() - self.started_at if self.started_at else 0.0

    def StopAll(self) -> None:
        for proc in (self.tun2socks_proc, self.xray_proc):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self.xray_proc = None
        self.tun2socks_proc = None
        self.started_at = 0.0


# --------------------------------------------------------------------------
# App: ties config + net + process together, owns cleanup
# --------------------------------------------------------------------------

class App:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.net = NetState(tun_device=args.tun_device)
        self.proc = ProcMgr(args.xray_bin, args.tun2socks_bin)
        self.servers: list[dict] = []
        self.current_server: dict | None = None
        self.pings: dict[int, float | None] = {}
        self.connected = False
        self.status_msg = "idle"
        self.speed_up_bps = 0.0
        self.speed_down_bps = 0.0
        self._stats_stop = threading.Event()
        self._ping_busy = False
        self._stats_thread: threading.Thread | None = None
        atexit.register(self.Disconnect)
        signal.signal(signal.SIGINT, lambda *_: self._SignalExit())
        signal.signal(signal.SIGTERM, lambda *_: self._SignalExit())

    def _SignalExit(self) -> None:
        self.Disconnect()
        sys.exit(0)

    def CheckBinaries(self) -> None:
        missing = [b for b in (self.args.xray_bin, self.args.tun2socks_bin) if not shutil.which(b)]
        if missing:
            raise RuntimeError(
                f"missing binaries on PATH: {missing}. "
                "brew install xray; download tun2socks from github.com/xjasonlyu/tun2socks/releases"
            )

    def LoadServers(self) -> None:
        if self.args.config:
            self.servers = [LoadLocalOutbound(self.args.config)]
        else:
            urls = [self.args.subscription_url] if self.args.subscription_url else LoadSavedSubs()
            if urls:
                try:
                    self.servers = FetchAllSubscriptions(urls)
                    STATE_DIR.mkdir(parents=True, exist_ok=True)
                    SERVERS_CACHE.write_text(json.dumps(self.servers, indent=2))
                except Exception as e:
                    Log(f"subscription fetch failed: {e}")
                    self.servers = LoadCachedServers()
                    if not self.servers:
                        raise
            else:
                self.servers = LoadCachedServers()
        if not self.servers:
            raise RuntimeError(
                "no servers available - use --subscription-url, save one with --add-sub, "
                "use --config, or there's no cache from a previous run either"
            )
        self.current_server = self.servers[0]
        self.pings = {}

    def RefreshSubscription(self) -> None:
        urls = [self.args.subscription_url] if self.args.subscription_url else LoadSavedSubs()
        if not urls:
            self.status_msg = "no subscription URL(s) configured - see --add-sub"
            return
        try:
            self.servers = FetchAllSubscriptions(urls)
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            SERVERS_CACHE.write_text(json.dumps(self.servers, indent=2))
            self.pings = {}
            self.status_msg = f"refreshed: {len(self.servers)} servers from {len(urls)} subscription(s)"
        except Exception as e:
            self.status_msg = f"refresh failed: {e}"

    def PingAll(self) -> None:
        if not self.servers:
            self.status_msg = "no servers to ping"
            return
        if self._ping_busy:
            return
        self._ping_busy = True
        ping_host = getattr(self.args, "ping_host", PING_HOST)
        ping_path = getattr(self.args, "ping_path", PING_PATH)
        self.status_msg = f"pinging {len(self.servers)} servers via {ping_host}..."
        self.pings = {}

        def on_result(idx, val):
            self.pings[idx] = val  # written live - the render loop picks this up on its next frame

        def work():
            try:
                self.pings = PingAllServers(self.servers, self.args.xray_bin, ping_host, ping_path, on_result=on_result)
                ok = sum(1 for v in self.pings.values() if v is not None)
                self.status_msg = f"ping done: {ok}/{len(self.servers)} reachable"
            except Exception as e:
                self.status_msg = f"ping failed: {e}"
                Log(self.status_msg)
            finally:
                self._ping_busy = False

        threading.Thread(target=work, daemon=True).start()

    def SortByPing(self) -> None:
        if len(self.pings) != len(self.servers):
            self.status_msg = "ping all servers first (p)"
            return
        order = sorted(
            range(len(self.servers)),
            key=lambda i: (self.pings.get(i) is None, self.pings.get(i) or 0.0),
        )
        self.servers = [self.servers[i] for i in order]
        self.pings = {new_i: self.pings[old_i] for new_i, old_i in enumerate(order)}
        self.status_msg = "sorted by ping"

    def Connect(self) -> None:
        if self.connected or self.current_server is None:
            return
        try:
            self._DoConnect()
        except Exception:
            # partial failure - don't leave xray/tun2socks running or a
            # host route dangling for a connection the app thinks is down
            self.proc.StopAll()
            Teardown(self.net)
            raise

    def _DoConnect(self) -> None:
        outbound = self.current_server
        proxy_host = ExtractProxyHost(outbound)

        config = BuildXrayConfig(outbound, self.args.socks_port, stats_port=STATS_PORT)
        config_path = WriteXrayConfig(config)

        iface, gw = DetectDefaultRoute()
        service = DetectServiceForInterface(iface)
        self.net.interface = iface
        self.net.gateway = gw
        self.net.service = service
        self.net.proxy_host = proxy_host
        self.net.original_dns = GetCurrentDns(service)
        non_ip = [d for d in self.net.original_dns if not IsIpAddress(d)]
        if non_ip:
            Log(
                f"heads up: current DNS entries on {service!r} aren't plain IPs: {non_ip} - "
                "likely an Encrypted DNS (DoH/DoT) provider set in System Settings > Network > "
                f"{service} > DNS, not classic DNS servers. networksetup can't restore that on "
                "disconnect - you may need to re-select it there manually afterward."
            )

        self.proc.StartXray(config_path)
        time.sleep(0.5)  # give xray a moment to bind the socks port
        xray_ok, _ = self.proc.Alive()
        if not xray_ok:
            raise RuntimeError(f"xray died on startup: {ReadLogTail(XRAY_PROC_LOG)}")

        if proxy_host:
            AddHostRoute(proxy_host, gw)
            self.net.host_route_added = True

        self.proc.StartTun2socks(self.net.tun_device, self.args.socks_port, iface)
        time.sleep(0.5)
        _, t2s_ok = self.proc.Alive()
        if not t2s_ok:
            raise RuntimeError(f"tun2socks died on startup: {ReadLogTail(TUN2SOCKS_PROC_LOG)}")

        if not WaitForInterface(self.net.tun_device, timeout=5.0):
            _, still_alive = self.proc.Alive()
            raise RuntimeError(
                f"tun2socks alive={still_alive} but {self.net.tun_device} never "
                f"appeared within 5s: {ReadLogTail(TUN2SOCKS_PROC_LOG)}"
            )

        BringUpTunInterface(self.net.tun_device)

        AddFullTunnelRoutes(TUN_GW)
        self.net.routes_added = True

        SetDns(service, self.args.dns.split(","))
        self.net.dns_changed = True

        self.connected = True
        self.status_msg = "connected"

        self._stats_stop.clear()
        self._stats_thread = threading.Thread(target=self._StatsLoop, daemon=True)
        self._stats_thread.start()

    def _StatsLoop(self) -> None:
        """Runs on its own thread - polls xray's stats API every 1.5s and
        computes uplink/downlink rate from the byte-counter delta. Never
        touches the render loop, so it can't add latency to the TUI no
        matter how slow a single statsquery call happens to be."""
        prev: tuple[int, int, float] | None = None
        while not self._stats_stop.is_set() and self.connected:
            try:
                stats = QueryStats(self.args.xray_bin, STATS_PORT)
                up = stats.get("outbound>>>proxy>>>traffic>>>uplink", 0)
                down = stats.get("outbound>>>proxy>>>traffic>>>downlink", 0)
                now = time.monotonic()
                if prev is not None:
                    dt = now - prev[2]
                    if dt > 0:
                        self.speed_up_bps = max((up - prev[0]) / dt, 0.0) * 8
                        self.speed_down_bps = max((down - prev[1]) / dt, 0.0) * 8
                prev = (up, down, now)
            except Exception as e:
                Log(f"stats poll failed (speed display will stay blank): {e}")
            self._stats_stop.wait(1.5)

    def Disconnect(self) -> None:
        if not self.connected and self.proc.xray_proc is None and self.proc.tun2socks_proc is None:
            return
        self.status_msg = "disconnecting"
        self._stats_stop.set()
        self.speed_up_bps = 0.0
        self.speed_down_bps = 0.0
        try:
            self.proc.StopAll()
            Teardown(self.net)
        except Exception as e:
            # Disconnect runs from atexit/signal handlers too - an uncaught
            # exception here is strictly worse than a logged, swallowed one
            Log(f"Disconnect: unexpected error during cleanup, continuing: {e}")
        self.connected = False
        self.status_msg = "disconnected"

    def SwitchServer(self, index: int) -> None:
        if index < 0 or index >= len(self.servers):
            return
        was_connected = self.connected
        if was_connected:
            self.Disconnect()
        self.current_server = self.servers[index]
        if was_connected:
            self.Connect()


def ExtractProxyHost(outbound: dict) -> str:
    """Pull the proxy server IP/host out of a vless/vmess/trojan outbound so
    we can route it around the tunnel (avoids a routing loop)."""
    try:
        settings = outbound.get("settings", {})
        vnext = settings.get("vnext") or settings.get("servers")
        if vnext:
            return vnext[0].get("address", "")
    except (AttributeError, IndexError, KeyError):
        pass
    return ""

def ResolveBundledBinary(name: str) -> str | None:
    """If this is a PyInstaller-frozen build with xray/tun2socks embedded
    via --add-binary into a bin/ subfolder, return that path. Returns None
    for a normal `python3 mintray.py` run - PATH lookup handles that case."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidate = Path(sys._MEIPASS) / "bin" / name
        if candidate.exists():
            return str(candidate)
    return None


def ResolveBinaryDefaults(args: argparse.Namespace) -> None:
    """Prefer bundled binaries when frozen, but never override an explicit
    --xray-bin/--tun2socks-bin the user actually passed."""
    if args.xray_bin == "xray":
        bundled = ResolveBundledBinary("xray")
        if bundled:
            args.xray_bin = bundled
    if args.tun2socks_bin == "tun2socks":
        bundled = ResolveBundledBinary("tun2socks")
        if bundled:
            args.tun2socks_bin = bundled



def ParseArgs() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--subscription-url", help="one-off subscription URL, doesn't get saved. Omit this and use --add-sub instead to avoid retyping it")
    p.add_argument("--config", help="path to a raw Xray outbound JSON (bypasses subscription)")
    p.add_argument("--socks-port", type=int, default=SOCKS_PORT_DEFAULT)
    p.add_argument("--tun-device", default=TUN_DEVICE_DEFAULT)
    p.add_argument("--xray-bin", default="xray")
    p.add_argument("--tun2socks-bin", default="tun2socks")
    p.add_argument("--dns", default="1.1.1.1,1.0.0.1")
    p.add_argument("--ping-host", default=PING_HOST, help="host used for the connectivity-check ping (default: time.grapheneos.org)")
    p.add_argument("--ping-path", default=PING_PATH, help="path used for the connectivity-check ping (default: /generate_204)")
    p.add_argument("--cleanup", action="store_true", help="tear down routes/DNS from a previous crashed run and exit")
    p.add_argument("--add-sub", metavar="URL", help="save a subscription URL for every future run (supports more than one - servers get merged)")
    p.add_argument("--remove-sub", metavar="URL_OR_N", help="remove a saved subscription, by URL or by its number from --list-subs")
    p.add_argument("--list-subs", action="store_true", help="list saved subscription URLs and exit")
    p.add_argument("--print-binary-paths", action="store_true", help="print which xray/tun2socks binaries would be used and exit (useful to confirm a PyInstaller bundle worked)")
    return p.parse_args()


def PrintSubs(subs: list[str]) -> None:
    if not subs:
        print("(none)")
        return
    for i, url in enumerate(subs, 1):
        print(f"{i}. {url}")




# --------------------------------------------------------------------------
# Shared entrypoint helpers - both Mintray_TUI.py and Mintray_GUI.py call
# these so neither renderer duplicates CLI/startup logic.
# --------------------------------------------------------------------------

def HandleCliCommands(args: argparse.Namespace) -> bool:
    """Non-interactive CLI subcommands (--list-subs, --add-sub, --cleanup,
    etc). Returns True if one was handled (caller should exit immediately),
    False if the interactive UI should launch instead."""
    if args.print_binary_paths:
        for label, path in (("xray", args.xray_bin), ("tun2socks", args.tun2socks_bin)):
            resolved = shutil.which(path) or path
            runnable = os.path.isfile(resolved) and os.access(resolved, os.X_OK)
            print(f"{label}: {path}  (executable: {runnable})")
        return True

    if args.list_subs:
        PrintSubs(LoadSavedSubs())
        return True

    if args.add_sub:
        subs = AddSub(args.add_sub)
        print(f"saved: {args.add_sub}")
        try:
            found = FetchSubscription(args.add_sub)
            print(f"validated - found {len(found)} server(s)")
        except Exception as e:
            print(f"warning: couldn't validate it right now ({e}) - saved anyway, will retry next run")
        print(f"\n{len(subs)} subscription(s) saved:")
        PrintSubs(subs)
        return True

    if args.remove_sub:
        subs, removed = RemoveSub(args.remove_sub)
        print(f"removed: {removed}" if removed else f"no match for {args.remove_sub!r}")
        print(f"\n{len(subs)} subscription(s) remaining:")
        PrintSubs(subs)
        return True

    if args.cleanup:
        if os.geteuid() != 0:
            print("MintRay needs root for --cleanup (it touches routes/DNS). Run with sudo.")
            sys.exit(1)
        iface, gw = DetectDefaultRoute()
        service = DetectServiceForInterface(iface)
        DeleteFullTunnelRoutes(TUN_GW, check=False)
        print("cleared full-tunnel routes. If DNS looks wrong, reset it manually:")
        print(f"  networksetup -setdnsservers '{service}' empty")
        return True

    return False


def StartApp(args: argparse.Namespace) -> "App":
    """Common startup for any renderer: root check, binary check, load
    servers. Exits the process on failure - both renderers want identical
    failure behavior here, so it lives once in Core rather than twice."""
    if os.geteuid() != 0:
        print("MintRay needs root (TUN device, routes, DNS). Run with sudo -E.")
        sys.exit(1)
    app = App(args)
    try:
        app.CheckBinaries()
        app.LoadServers()
    except Exception as e:
        print(f"startup failed: {e}")
        sys.exit(1)
    return app

