# #
from __future__ import annotations
import argparse, atexit, base64, concurrent.futures, dataclasses, gzip, ipaddress, json, os, shutil, signal, socket, ssl, struct, platform, subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path
STATE_DIR = Path.home() / ".mintray"; CONFIG_PATH = STATE_DIR / "xray-config.json"; SERVERS_CACHE = STATE_DIR / "servers.json"; LOG_PATH = STATE_DIR / "mintray.log"; XRAY_PROC_LOG = STATE_DIR / "xray-proc.log"; TUN2SOCKS_PROC_LOG = STATE_DIR / "tun2socks-proc.log"; PLATFORM = platform.system(); TUN_DEVICE_DEFAULT = "utun123" if PLATFORM == "Darwin" else "tun123"; TUN_ADDR = "198.18.0.1"; TUN_GW = "198.18.0.1"; SOCKS_PORT_DEFAULT = 10808; PING_HOST = "time.grapheneos.org"; PING_PATH = "/generate_204"; PING_URL_DEFAULT = f"https://{PING_HOST}{PING_PATH}"; PING_BASE_PORT = 19000; STATS_PORT = 10085; SUBS_PATH = STATE_DIR / "subscriptions.json"; SUB_META_PATH = STATE_DIR / "subscription_meta.json"; SETTINGS_PATH = STATE_DIR / "settings.json"; SETTINGS_DEFAULTS = {"xray_bin": "xray", "tun2socks_bin": "tun2socks", "ping_url": PING_URL_DEFAULT}
class Main:
    # Variables
    # Classes
    class Version: # Not used anywhere, only for code viewing ig
        ManageVersion = 10
        Version = 1.0
        SubVersion = 5
        SubComment = 'CORE'
        BuildType = 'Stable'  # Could be: Unstable (a default release, but may contain major/small bugs), Stable, Alpha (early versions, mostly very unstable or contains unfinished parts)
        __build_type_show__ = {'Alpha': 'ALPH', 'Stable': 'STBL', 'Unstable': 'BETA'}[BuildType]
        BuildShow = f'{ManageVersion}{__build_type_show__}-{SubVersion}{SubComment}'
    class GlobalCache:
        SettingsFields = ['xray_bin', 'tun2socks_bin', 'ping_url']
    class Activities:
        @classmethod
        def Log(cls, msg: str) -> None: STATE_DIR.mkdir(parents=True, exist_ok=True); open(LOG_PATH, "a").write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        @classmethod
        def _FmtLogLine(cls, l: str) -> str:
            try: e = json.loads(l)
            except json.JSONDecodeError: return l
            return (f"[{e.get('level', '')}] {e['msg']}" if e.get("level") else str(e["msg"])) if isinstance(e, dict) and "msg" in e else l
        @classmethod
        def ReadLogTail(cls, path: Path = LOG_PATH, n: int = 8, max_len: int = 1200) -> str:
            if not path.exists(): return "(no log file yet)"
            raw = [l for l in path.read_text(errors="replace").splitlines() if l.strip()]
            if not raw: return "(process produced zero output before dying - check for a stale process already holding the device, e.g. ps aux | grep tun2socks)"
            return " | ".join(cls._FmtLogLine(l) for l in raw[-n:])[:max_len]
        @classmethod
        def BuildXrayConfig(cls, outbound: dict, socks_port: int, stats_port: int | None = None) -> dict:
            outbound = dict(outbound); outbound["tag"] = "proxy"
            config = {"log": {"loglevel": "warning"}, "inbounds": [{"tag": "socks-in", "listen": "127.0.0.1", "port": socks_port, "protocol": "socks", "settings": {"auth": "noauth", "udp": True}, "sniffing": {"enabled": True, "destOverride": ["http", "tls"]}}], "outbounds": [outbound, {"protocol": "freedom", "tag": "direct"}, {"protocol": "blackhole", "tag": "block"}]}
            if stats_port is not None: config["stats"] = {}; config["api"] = {"tag": "api", "listen": f"127.0.0.1:{stats_port}", "services": ["StatsService"]}; config["policy"] = {"system": {"statsOutboundUplink": True, "statsOutboundDownlink": True}}
            return config
        @classmethod
        def WriteXrayConfig(cls, config: dict) -> Path: STATE_DIR.mkdir(parents=True, exist_ok=True); CONFIG_PATH.write_text(json.dumps(config, indent=2)); return CONFIG_PATH
        @classmethod
        def QueryStats(cls, xray_bin: str, stats_port: int) -> dict[str, int]:
            result = subprocess.run([xray_bin, "api", "statsquery", f"--server=127.0.0.1:{stats_port}"], capture_output=True, text=True, timeout=3)
            if result.returncode != 0: raise RuntimeError(result.stderr.strip() or "statsquery failed")
            return {entry["name"]: entry.get("value", 0) for entry in json.loads(result.stdout).get("stat", [])}
        @classmethod
        def FormatSpeed(cls, bits_per_sec: float) -> str:
            mbps = bits_per_sec / 1_000_000
            return f"{mbps:.1f} Mbps" if mbps >= 0.1 else f"{bits_per_sec / 1_000:.0f} Kbps"
        @classmethod
        def FormatDuration(cls, seconds: float) -> str:
            s = int(seconds); days, s = divmod(s, 86400); hours, s = divmod(s, 3600); minutes, s = divmod(s, 60)
            return f"{days}d {hours}h" if days > 0 else f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m {s}s" if minutes > 0 else f"{s}s"
        @classmethod
        def FetchSubscription(cls, url: str) -> list[dict]:
            req = urllib.request.Request(url, headers={"User-Agent": "mintray/1", "Accept-Encoding": "gzip"})
            try:
                with urllib.request.urlopen(req, timeout=15) as resp: raw = resp.read(); headers = resp.headers
            except urllib.error.URLError as e:
                if isinstance(e.reason, ssl.SSLCertVerificationError): raise RuntimeError("TLS cert verification failed fetching the subscription - run 'Install Certificates.command' (python.org builds) or 'sudo python3 -m pip install certifi --break-system-packages' then re-run with SSL_CERT_FILE=$(python3 -c \"import certifi;print(certifi.where())\")") from e
                raise
            support_url, web_page_url, profile_title = headers.get("support-url"), headers.get("profile-web-page-url"), headers.get("profile-title")
            if support_url or web_page_url or profile_title: meta = cls.LoadSubMeta(); meta[url] = {"support_url": support_url, "web_page_url": web_page_url, "profile_title": profile_title}; cls.SaveSubMeta(meta)
            try: return cls.DecodeSubscriptionBody(raw)
            except Exception: (STATE_DIR / "servers.raw").write_bytes(raw); raise
        @classmethod
        def FetchAllSubscriptions(cls, urls: list[str]) -> list[dict]:
            all_servers: list[dict] = []; errors: list[str] = []
            for url in urls:
                try: all_servers.extend(cls.FetchSubscription(url))
                except Exception as e: errors.append(f"{url}: {e}"); cls.Log(f"subscription fetch failed for {url}: {e}")
            if errors and not all_servers: raise RuntimeError("all subscriptions failed: " + " | ".join(errors))
            if errors: cls.Log(f"continuing with {len(all_servers)} servers from the subscriptions that did work; failures: {errors}")
            return all_servers
        @classmethod
        def LoadSubMeta(cls) -> dict:
            try: return json.loads(SUB_META_PATH.read_text()) if SUB_META_PATH.exists() else {}
            except json.JSONDecodeError: return {}
        @classmethod
        def SaveSubMeta(cls, meta: dict) -> None: STATE_DIR.mkdir(parents=True, exist_ok=True); SUB_META_PATH.write_text(json.dumps(meta, indent=2))
        @classmethod
        def GetAnySupportUrl(cls) -> str | None:
            meta = cls.LoadSubMeta()
            return next((meta[url]["support_url"] for url in cls.LoadSavedSubs() if meta.get(url, {}).get("support_url")), None)
        @classmethod
        def LoadSettings(cls) -> dict:
            try: return json.loads(SETTINGS_PATH.read_text()) if SETTINGS_PATH.exists() else {}
            except json.JSONDecodeError: return {}
        @classmethod
        def SaveSettings(cls, values: dict) -> None: STATE_DIR.mkdir(parents=True, exist_ok=True); current = cls.LoadSettings(); current.update(values); SETTINGS_PATH.write_text(json.dumps(current, indent=2))
        @classmethod
        def ApplySettings(cls, args: argparse.Namespace) -> None:
            saved = cls.LoadSettings()
            for field in gc.SettingsFields:
                if field in saved and str(getattr(args, field, SETTINGS_DEFAULTS[field])) == str(SETTINGS_DEFAULTS[field]): setattr(args, field, saved[field])
        @classmethod
        def LoadSavedSubs(cls) -> list[str]:
            if not SUBS_PATH.exists(): return []
            try: data = json.loads(SUBS_PATH.read_text())
            except json.JSONDecodeError: return []
            return [str(u) for u in data] if isinstance(data, list) else []
        @classmethod
        def SaveSubs(cls, urls: list[str]) -> None: STATE_DIR.mkdir(parents=True, exist_ok=True); SUBS_PATH.write_text(json.dumps(urls, indent=2))
        @classmethod
        def AddSub(cls, url: str) -> list[str]:
            subs = cls.LoadSavedSubs()
            if url not in subs: subs.append(url); cls.SaveSubs(subs)
            return subs
        @classmethod
        def RemoveSub(cls, identifier: str) -> tuple[list[str], str | None]:
            subs = cls.LoadSavedSubs(); removed = None
            if identifier.isdigit(): removed = subs.pop(idx) if 0 <= (idx := int(identifier) - 1) < len(subs) else None
            elif identifier in subs: subs.remove(identifier); removed = identifier
            if removed is not None: cls.SaveSubs(subs)
            return subs, removed
        @classmethod
        def DecodeSubscriptionBody(cls, raw: bytes) -> list[dict]:
            try: raw = gzip.decompress(raw)
            except OSError: pass
            text = raw.decode("utf-8", errors="replace").strip()
            try: return cls.ParseJsonPayload(json.loads(text))
            except json.JSONDecodeError: pass
            links = cls.TryDecodeBase64Links(text)
            if links: return [cls.ParseShareLink(link) for link in links]
            plain_links = [line.strip() for line in text.splitlines() if cls.IsShareLink(line.strip())]
            if plain_links: return [cls.ParseShareLink(link) for link in plain_links]
            raise RuntimeError("unrecognized subscription format (not JSON, not base64 share-links, not plaintext share-links)")
        @classmethod
        def ParseJsonPayload(cls, payload) -> list[dict]:
            if isinstance(payload, list): return payload
            found = next((payload[k] for k in ("servers", "outbounds", "nodes") if isinstance(payload, dict) and k in payload and isinstance(payload[k], list)), None)
            if found is not None: return found
            raise RuntimeError(f"unrecognized JSON subscription shape: {payload if isinstance(payload, list) else list(payload)}")
        @classmethod
        def IsShareLink(cls, s: str) -> bool: return s.startswith(("vless://", "vmess://", "trojan://", "ss://"))
        @classmethod
        def TryDecodeBase64Links(cls, text: str) -> list[str]:
            padded = text + "=" * (-len(text) % 4)
            try: decoded = base64.b64decode(padded, validate=True).decode("utf-8")
            except Exception: return []
            lines = [line.strip() for line in decoded.splitlines() if line.strip()]
            return lines if lines and all(cls.IsShareLink(line) for line in lines) else []
        @classmethod
        def ParseShareLink(cls, uri: str) -> dict:
            scheme = uri.split("://", 1)[0]
            if scheme == "vless": return cls.ParseVlessUri(uri)
            raise ValueError(f"{scheme}:// share links aren't implemented yet (only vless:// is)")
        @classmethod
        def ParseVlessUri(cls, uri: str) -> dict:
            u = urllib.parse.urlparse(uri); q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            name = urllib.parse.unquote(u.fragment) or f"{u.hostname}:{u.port}"
            user = {"id": urllib.parse.unquote(u.username or ""), "encryption": q.get("encryption", "none"), **({"flow": q["flow"]} if q.get("flow") else {})}
            network, security = q.get("type", "tcp"), q.get("security", "none")
            reality = {"serverName": q.get("sni", ""), "publicKey": q.get("pbk", ""), "shortId": q.get("sid", ""), **({"fingerprint": q["fp"]} if "fp" in q else {}), **({"spiderX": q["spx"]} if "spx" in q else {})}
            tls = {**({"serverName": q["sni"]} if "sni" in q else {}), **({"fingerprint": q["fp"]} if "fp" in q else {})}
            xhttp = {k: q[k] for k in ("path", "mode", "host") if k in q}
            ws = {**({"path": q["path"]} if "path" in q else {}), **({"headers": {"Host": q["host"]}} if "host" in q else {})}
            grpc = {"serviceName": q["serviceName"]} if "serviceName" in q else {}
            stream = {"network": network, "security": security, **({"realitySettings": reality} if security == "reality" else {"tlsSettings": tls} if security == "tls" else {}), **({"xhttpSettings": xhttp} if network == "xhttp" else {"wsSettings": ws} if network == "ws" else {"grpcSettings": grpc} if network == "grpc" else {})}
            return {"tag": name, "protocol": "vless", "settings": {"vnext": [{"address": u.hostname, "port": u.port, "users": [user]}]}, "streamSettings": stream}
        @classmethod
        def LoadCachedServers(cls) -> list[dict]: return json.loads(SERVERS_CACHE.read_text()) if SERVERS_CACHE.exists() else []
        @classmethod
        def LoadLocalOutbound(cls, path: str) -> dict: return json.loads(Path(path).read_text())
        @classmethod
        def RecvExact(cls, sock: socket.socket, n: int) -> bytes:
            buf = b""
            while len(buf) < n:
                if not (chunk := sock.recv(n - len(buf))): raise RuntimeError("socket closed mid-handshake")
                buf += chunk
            return buf
        @classmethod
        def IsIpAddress(cls, s: str) -> bool:
            try: ipaddress.ip_address(s); return True
            except ValueError: return False
        @classmethod
        def Socks5Connect(cls, sock: socket.socket, dest_host: str, dest_port: int) -> None:
            sock.sendall(b"\x05\x01\x00")
            greeting = cls.RecvExact(sock, 2)
            if greeting[0] != 0x05 or greeting[1] != 0x00: raise RuntimeError(f"SOCKS5 greeting rejected: {greeting!r}")
            if cls.IsIpAddress(dest_host): req = b"\x05\x01\x00\x01" + socket.inet_aton(dest_host) + struct.pack(">H", dest_port)
            else: host_bytes = dest_host.encode("ascii"); req = b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + struct.pack(">H", dest_port)
            sock.sendall(req)
            header = cls.RecvExact(sock, 4)
            if header[1] != 0x00: raise RuntimeError(f"SOCKS5 CONNECT failed, REP={header[1]}")
            atyp = header[3]
            if atyp == 0x01: cls.RecvExact(sock, 4 + 2)
            elif atyp == 0x03: cls.RecvExact(sock, cls.RecvExact(sock, 1)[0] + 2)
            elif atyp == 0x04: cls.RecvExact(sock, 16 + 2)
            else: raise RuntimeError(f"unknown ATYP in SOCKS5 reply: {atyp}")
        @classmethod
        def ParsePingUrl(cls, url: str) -> tuple[str, str]:
            url = url if "://" in url else "https://" + url
            parsed = urllib.parse.urlparse(url)
            return parsed.hostname or PING_HOST, parsed.path or "/"
        @classmethod
        def PingServer(cls, local_socks_port: int, timeout: float = 5.0, ping_host: str = PING_HOST, ping_path: str = PING_PATH) -> float | None:
            sock = None; start = time.monotonic()
            try:
                sock = socket.create_connection(("127.0.0.1", local_socks_port), timeout=timeout); sock.settimeout(timeout)
                cls.Socks5Connect(sock, ping_host, 443)
                tls = ssl.create_default_context().wrap_socket(sock, server_hostname=ping_host)
                tls.sendall(f"GET {ping_path} HTTP/1.1\r\nHost: {ping_host}\r\nConnection: close\r\nUser-Agent: mintray/1\r\n\r\n".encode())
                status_line = b""
                while b"\r\n" not in status_line and len(status_line) < 512 and (chunk := tls.recv(256)): status_line += chunk
                elapsed_ms = (time.monotonic() - start) * 1000
                first_line = status_line.split(b"\r\n", 1)[0].decode(errors="replace")
                if " 204 " in first_line or first_line.rstrip().endswith(" 204"): return elapsed_ms
                cls.Log(f"ping({local_socks_port}) unexpected response: {first_line!r}"); return None
            except Exception as e:
                cls.Log(f"ping({local_socks_port}) failed: {e}"); return None
            finally:
                if sock is not None:
                    try: sock.close()
                    except OSError: pass
        @classmethod
        def BuildPingTestConfig(cls, servers: list[dict], base_port: int) -> dict:
            outbounds, inbounds, rules = [], [], []
            for i, srv in enumerate(servers): ob = dict(srv); out_tag = f"ping-out-{i}"; ob["tag"] = out_tag; outbounds.append(ob); in_tag = f"ping-in-{i}"; inbounds.append({"tag": in_tag, "listen": "127.0.0.1", "port": base_port + i, "protocol": "socks", "settings": {"auth": "noauth", "udp": False}}); rules.append({"type": "field", "inboundTag": [in_tag], "outboundTag": out_tag})
            outbounds += [{"protocol": "freedom", "tag": "direct"}, {"protocol": "blackhole", "tag": "block"}]
            return {"log": {"loglevel": "warning"}, "inbounds": inbounds, "outbounds": outbounds, "routing": {"rules": rules}}
        @classmethod
        def PingAllServers(cls, servers: list[dict], xray_bin: str, ping_host: str = PING_HOST, ping_path: str = PING_PATH, on_result=None) -> dict[int, float | None]:
            if not servers: return {}
            config = cls.BuildPingTestConfig(servers, PING_BASE_PORT)
            config_path = STATE_DIR / "ping-config.json"
            STATE_DIR.mkdir(parents=True, exist_ok=True); config_path.write_text(json.dumps(config, indent=2))
            log_f = open(LOG_PATH, "a")
            proc = subprocess.Popen([xray_bin, "run", "-c", str(config_path)], stdout=log_f, stderr=log_f)
            time.sleep(1.0)
            results: dict[int, float | None] = {}
            try:
                if proc.poll() is not None: raise RuntimeError(f"ping-test xray instance exited immediately, check {LOG_PATH}")
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(servers))) as pool:
                    futures = {pool.submit(cls.PingServer, PING_BASE_PORT + i, 5.0, ping_host, ping_path): i for i in range(len(servers))}
                    for fut in concurrent.futures.as_completed(futures):
                        i = futures[fut]; val = fut.result(); results[i] = val
                        if on_result is not None: on_result(i, val)
            finally:
                if proc.poll() is None:
                    try: proc.terminate(); proc.wait(timeout=5)
                    except subprocess.TimeoutExpired: proc.kill()
            return results
        @classmethod
        def RunCmd(cls, *args: str, check: bool = True) -> subprocess.CompletedProcess:
            cls.Log("$ " + " ".join(args)); result = subprocess.run(args, capture_output=True, text=True)
            if check and result.returncode != 0: raise RuntimeError(f"command failed: {' '.join(args)}\n{result.stderr}")
            return result
        @classmethod
        def DetectDefaultRoute(cls) -> tuple[str, str]:
            if PLATFORM == "Linux":
                parts = cls.RunCmd("ip", "route", "show", "default").stdout.split()
                iface = next((parts[i + 1] for i, t in enumerate(parts) if t == "dev" and i + 1 < len(parts)), "")
                gw = next((parts[i + 1] for i, t in enumerate(parts) if t == "via" and i + 1 < len(parts)), "")
                if not iface or not gw: raise RuntimeError("couldn't detect default route - are you online?")
                return iface, gw
            out = cls.RunCmd("route", "-n", "get", "default").stdout
            iface = next((l.split(":", 1)[1].strip() for l in out.splitlines() if l.strip().startswith("interface:")), "")
            gw = next((l.split(":", 1)[1].strip() for l in out.splitlines() if l.strip().startswith("gateway:")), "")
            if not iface or not gw: raise RuntimeError("couldn't detect default route - are you online?")
            return iface, gw
        @classmethod
        def DetectServiceForInterface(cls, interface: str) -> str:
            if PLATFORM == "Linux": return interface
            blocks = cls.RunCmd("networksetup", "-listallhardwareports").stdout.split("Hardware Port: ")[1:]
            for block in blocks:
                lines = block.splitlines(); name = lines[0].strip(); dev_line = next((l for l in lines if l.startswith("Device:")), "")
                if dev_line.split(":", 1)[1].strip() == interface: return name
            raise RuntimeError(f"couldn't map interface {interface} to a networksetup service")
        @classmethod
        def GetCurrentDns(cls, service: str) -> list[str]:
            if PLATFORM == "Linux":
                if shutil.which("resolvectl") is None: cls.Log("resolvectl not found - can't read current DNS, will skip DNS management on this connection"); return []
                result = subprocess.run(["resolvectl", "dns", service], capture_output=True, text=True)
                if result.returncode != 0: return []
                line = result.stdout.strip()
                if ":" not in line: return []
                servers_part = line.split(":", 1)[1].strip()
                return servers_part.split() if servers_part else []
            out = cls.RunCmd("networksetup", "-getdnsservers", service).stdout.strip()
            if out.startswith("There aren't any DNS Servers"): return []
            return [line.strip() for line in out.splitlines() if line.strip()]
        @classmethod
        def SetDns(cls, service: str, servers: list[str]) -> None:
            if PLATFORM == "Linux":
                if shutil.which("resolvectl") is None: cls.Log("resolvectl not found - skipping DNS override (traffic is still tunneled, just using whatever DNS was already configured)"); return
                cls.RunCmd("resolvectl", "dns", service, *servers) if servers else cls.RunCmd("resolvectl", "dns", service, "")
                return
            cls.RunCmd("networksetup", "-setdnsservers", service, *(servers or ["empty"]))
        @classmethod
        def RestoreDns(cls, state: "NetState") -> None:
            if not state.dns_changed: return
            try: cls.SetDns(state.service, state.original_dns)
            except Exception as e:
                fallback = " ".join(state.original_dns) if state.original_dns else "empty"
                cls.Log(f"failed to restore DNS on {state.service!r} to {state.original_dns!r}: {e}. If it looks wrong now, fix manually: " + (f"resolvectl dns '{state.service}' {fallback}" if PLATFORM == "Linux" else f"networksetup -setdnsservers '{state.service}' {fallback}"))
            state.dns_changed = False
        @classmethod
        def WaitForInterface(cls, device: str, timeout: float = 5.0) -> bool:
            deadline = time.monotonic() + timeout
            check_cmd = ["ip", "link", "show", device] if PLATFORM == "Linux" else ["ifconfig", device]
            while time.monotonic() < deadline:
                if subprocess.run(check_cmd, capture_output=True, text=True).returncode == 0: return True
                time.sleep(0.2)
            return False
        @classmethod
        def BringUpTunInterface(cls, device: str) -> None:
            if PLATFORM == "Linux": cls.RunCmd("ip", "addr", "add", f"{TUN_ADDR}/32", "dev", device); cls.RunCmd("ip", "link", "set", device, "up"); return
            cls.RunCmd("ifconfig", device, TUN_ADDR, TUN_GW, "up")
        @classmethod
        def AddHostRoute(cls, host: str, gateway: str) -> None:
            if PLATFORM == "Linux": cls.RunCmd("ip", "route", "add", f"{host}/32", "via", gateway); return
            cls.RunCmd("route", "add", "-host", host, gateway)
        @classmethod
        def DeleteHostRoute(cls, host: str, gateway: str, check: bool = False) -> None:
            if PLATFORM == "Linux": cls.RunCmd("ip", "route", "del", f"{host}/32", "via", gateway, check=check); return
            cls.RunCmd("route", "delete", "-host", host, gateway, check=check)
        @classmethod
        def AddFullTunnelRoutes(cls, tun_gw: str) -> None:
            if PLATFORM == "Linux": cls.RunCmd("ip", "route", "add", "0.0.0.0/1", "via", tun_gw); cls.RunCmd("ip", "route", "add", "128.0.0.0/1", "via", tun_gw); return
            cls.RunCmd("route", "add", "-net", "0.0.0.0/1", tun_gw); cls.RunCmd("route", "add", "-net", "128.0.0.0/1", tun_gw)
        @classmethod
        def DeleteFullTunnelRoutes(cls, tun_gw: str, check: bool = False) -> None:
            if PLATFORM == "Linux": cls.RunCmd("ip", "route", "del", "0.0.0.0/1", "via", tun_gw, check=check); cls.RunCmd("ip", "route", "del", "128.0.0.0/1", "via", tun_gw, check=check); return
            cls.RunCmd("route", "delete", "-net", "0.0.0.0/1", tun_gw, check=check); cls.RunCmd("route", "delete", "-net", "128.0.0.0/1", tun_gw, check=check)
        @classmethod
        def Teardown(cls, state: "NetState") -> None:
            if state.routes_added: cls.DeleteFullTunnelRoutes(TUN_GW, check=False); state.routes_added = False
            if state.host_route_added and state.proxy_host and state.gateway: cls.DeleteHostRoute(state.proxy_host, state.gateway, check=False); state.host_route_added = False
            cls.RestoreDns(state)
        @classmethod
        def ExtractProxyHost(cls, outbound: dict) -> str:
            try:
                settings = outbound.get("settings", {}); vnext = settings.get("vnext") or settings.get("servers")
                return vnext[0].get("address", "") if vnext else ""
            except (AttributeError, IndexError, KeyError): return ""
        @classmethod
        def ResolveBundledBinary(cls, name: str) -> str | None:
            if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
                candidate = Path(sys._MEIPASS) / "bin" / name
                if candidate.exists(): return str(candidate)
            return None
        @classmethod
        def ResolveBinaryDefaults(cls, args: argparse.Namespace) -> None:
            if args.xray_bin == "xray": args.xray_bin = cls.ResolveBundledBinary("xray") or args.xray_bin
            if args.tun2socks_bin == "tun2socks": args.tun2socks_bin = cls.ResolveBundledBinary("tun2socks") or args.tun2socks_bin
        @classmethod
        def ParseArgs(cls) -> argparse.Namespace:
            p = argparse.ArgumentParser(description=__doc__)
            p.add_argument("--subscription-url", help="one-off subscription URL, doesn't get saved. Omit this and use --add-sub instead to avoid retyping it")
            p.add_argument("--config", help="path to a raw Xray outbound JSON (bypasses subscription)")
            p.add_argument("--socks-port", type=int, default=SOCKS_PORT_DEFAULT)
            p.add_argument("--tun-device", default=TUN_DEVICE_DEFAULT)
            p.add_argument("--xray-bin", default="xray")
            p.add_argument("--tun2socks-bin", default="tun2socks")
            p.add_argument("--dns", default="1.1.1.1,1.0.0.1")
            p.add_argument("--ping-url", default=PING_URL_DEFAULT, help="URL used for the connectivity-check ping (default: https://time.grapheneos.org/generate_204)")
            p.add_argument("--cleanup", action="store_true", help="tear down routes/DNS from a previous crashed run and exit")
            p.add_argument("--add-sub", metavar="URL", help="save a subscription URL for every future run (supports more than one - servers get merged)")
            p.add_argument("--remove-sub", metavar="URL_OR_N", help="remove a saved subscription, by URL or by its number from --list-subs")
            p.add_argument("--list-subs", action="store_true", help="list saved subscription URLs and exit")
            p.add_argument("--print-binary-paths", action="store_true", help="print which xray/tun2socks binaries would be used and exit (useful to confirm a PyInstaller bundle worked)")
            return p.parse_args()
        @classmethod
        def PrintSubs(cls, subs: list[str]) -> None: print("(none)") if not subs else [print(f"{i}. {url}") for i, url in enumerate(subs, 1)]
        @classmethod
        def HandleCliCommands(cls, args: argparse.Namespace) -> bool:
            if args.print_binary_paths:
                for label, path in (("xray", args.xray_bin), ("tun2socks", args.tun2socks_bin)): resolved = shutil.which(path) or path; print(f"{label}: {path}  (executable: {os.path.isfile(resolved) and os.access(resolved, os.X_OK)})"); return True
            if args.list_subs: cls.PrintSubs(cls.LoadSavedSubs()); return True
            if args.add_sub:
                subs = cls.AddSub(args.add_sub); print(f"saved: {args.add_sub}")
                try: print(f"validated - found {len(cls.FetchSubscription(args.add_sub))} server(s)")
                except Exception as e: print(f"warning: couldn't validate it right now ({e}) - saved anyway, will retry next run")
                print(f"\n{len(subs)} subscription(s) saved:"); cls.PrintSubs(subs); return True
            if args.remove_sub: subs, removed = cls.RemoveSub(args.remove_sub); print(f"removed: {removed}" if removed else f"no match for {args.remove_sub!r}"); print(f"\n{len(subs)} subscription(s) remaining:"); cls.PrintSubs(subs); return True
            if args.cleanup:
                if os.geteuid() != 0: print("MintRay needs root for --cleanup (it touches routes/DNS). Run with sudo."); sys.exit(1)
                iface, gw = cls.DetectDefaultRoute(); service = cls.DetectServiceForInterface(iface)
                cls.DeleteFullTunnelRoutes(TUN_GW, check=False)
                print("cleared full-tunnel routes. If DNS looks wrong, reset it manually:"); print(f"  resolvectl dns '{service}' \"\"" if PLATFORM == "Linux" else f"  networksetup -setdnsservers '{service}' empty"); return True
            return False
        @classmethod
        def StartApp(cls, args: argparse.Namespace) -> "App":
            if os.geteuid() != 0: print("MintRay needs root (TUN device, routes, DNS). Run with sudo -E."); sys.exit(1)
            app = App(args)
            try: app.CheckBinaries(); app.LoadServers()
            except Exception as e: print(f"startup failed: {e}"); sys.exit(1)
            return app
@dataclasses.dataclass
class NetState: interface: str = ""; gateway: str = ""; service: str = ""; original_dns: list[str] = dataclasses.field(default_factory=list); proxy_host: str = ""; tun_device: str = TUN_DEVICE_DEFAULT; routes_added: bool = False; dns_changed: bool = False; host_route_added: bool = False
class ProcMgr:
    def __init__(self, xray_bin: str, tun2socks_bin: str):
        self.xray_bin = xray_bin; self.tun2socks_bin = tun2socks_bin
        self.xray_proc: subprocess.Popen | None = None; self.tun2socks_proc: subprocess.Popen | None = None; self.started_at: float = 0.0
    def StartXray(self, config_path: Path) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.xray_proc = subprocess.Popen([self.xray_bin, "run", "-c", str(config_path)], stdout=(log_f := open(XRAY_PROC_LOG, "w")), stderr=log_f)
    def StartTun2socks(self, device: str, socks_port: int, interface: str) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.tun2socks_proc = subprocess.Popen([self.tun2socks_bin, "-device", device, "-proxy", f"socks5://127.0.0.1:{socks_port}", "-interface", interface, "-loglevel", "warn"], stdout=(log_f := open(TUN2SOCKS_PROC_LOG, "w")), stderr=log_f)
        self.started_at = time.time()
    def Alive(self) -> tuple[bool, bool]: return self.xray_proc is not None and self.xray_proc.poll() is None, self.tun2socks_proc is not None and self.tun2socks_proc.poll() is None
    def Uptime(self) -> float: return time.time() - self.started_at if self.started_at else 0.0
    def StopAll(self) -> None:
        for proc in (self.tun2socks_proc, self.xray_proc):
            if proc and proc.poll() is None:
                try: proc.terminate(); proc.wait(timeout=5)
                except subprocess.TimeoutExpired: proc.kill()
        self.xray_proc = None; self.tun2socks_proc = None; self.started_at = 0.0
class App:
    def __init__(self, args: argparse.Namespace):
        self.args = args; self.net = NetState(tun_device=args.tun_device); self.proc = ProcMgr(args.xray_bin, args.tun2socks_bin)
        self.servers: list[dict] = []; self.current_server: dict | None = None; self.pings: dict[int, float | None] = {}
        self.connected = False; self.status_msg = "idle"; self.speed_up_bps = 0.0; self.speed_down_bps = 0.0
        self._stats_stop = threading.Event(); self._ping_busy = False; self._stats_thread: threading.Thread | None = None
        atexit.register(self.Disconnect); signal.signal(signal.SIGINT, lambda *_: self._SignalExit()); signal.signal(signal.SIGTERM, lambda *_: self._SignalExit())
    def _SignalExit(self) -> None: self.Disconnect(); sys.exit(0)
    def CheckBinaries(self) -> None:
        missing = [b for b in (self.args.xray_bin, self.args.tun2socks_bin) if not shutil.which(b)]
        if missing:
            hint = "install xray from your distro's package (e.g. apt install xray, or github.com/XTLS/Xray-core/releases); download tun2socks-linux-amd64 (or arm64) from github.com/xjasonlyu/tun2socks/releases" if PLATFORM == "Linux" else "brew install xray; download tun2socks from github.com/xjasonlyu/tun2socks/releases"
            raise RuntimeError(f"missing binaries on PATH: {missing}. {hint}")
    def LoadServers(self) -> None:
        if self.args.config: self.servers = [Main.Activities.LoadLocalOutbound(self.args.config)]
        else:
            urls = [self.args.subscription_url] if self.args.subscription_url else Main.Activities.LoadSavedSubs()
            if urls:
                try:
                    self.servers = Main.Activities.FetchAllSubscriptions(urls); STATE_DIR.mkdir(parents=True, exist_ok=True); SERVERS_CACHE.write_text(json.dumps(self.servers, indent=2))
                except Exception as e:
                    Main.Activities.Log(f"subscription fetch failed: {e}"); self.servers = Main.Activities.LoadCachedServers()
                    if not self.servers: raise
            else: self.servers = Main.Activities.LoadCachedServers()
        if not self.servers: raise RuntimeError("no servers available - use --subscription-url, save one with --add-sub, use --config, or there's no cache from a previous run either")
        self.current_server = self.servers[0]; self.pings = {}
    def RefreshSubscription(self) -> None:
        urls = [self.args.subscription_url] if self.args.subscription_url else Main.Activities.LoadSavedSubs()
        if not urls: self.status_msg = "no subscription URL(s) configured - see --add-sub"; return
        try:
            self.servers = Main.Activities.FetchAllSubscriptions(urls); STATE_DIR.mkdir(parents=True, exist_ok=True); SERVERS_CACHE.write_text(json.dumps(self.servers, indent=2))
            self.pings = {}; self.status_msg = f"refreshed: {len(self.servers)} servers from {len(urls)} subscription(s)"
        except Exception as e: self.status_msg = f"refresh failed: {e}"
    def PingAll(self) -> None:
        if not self.servers: self.status_msg = "no servers to ping"; return
        if self._ping_busy: return
        self._ping_busy = True
        ping_url = getattr(self.args, "ping_url", PING_URL_DEFAULT); ping_host, ping_path = Main.Activities.ParsePingUrl(ping_url)
        self.status_msg = f"pinging {len(self.servers)} servers via {ping_host}..."; self.pings = {}
        def on_result(idx, val): self.pings[idx] = val
        def work():
            try:
                self.pings = Main.Activities.PingAllServers(self.servers, self.args.xray_bin, ping_host, ping_path, on_result=on_result)
                ok = sum(1 for v in self.pings.values() if v is not None); self.status_msg = f"ping done: {ok}/{len(self.servers)} reachable"
            except Exception as e: self.status_msg = f"ping failed: {e}"; Main.Activities.Log(self.status_msg)
            finally: self._ping_busy = False
        threading.Thread(target=work, daemon=True).start()
    def SortByPing(self) -> None:
        if len(self.pings) != len(self.servers): self.status_msg = "ping all servers first (p)"; return
        order = sorted(range(len(self.servers)), key=lambda i: (self.pings.get(i) is None, self.pings.get(i) or 0.0))
        self.servers = [self.servers[i] for i in order]; self.pings = {new_i: self.pings[old_i] for new_i, old_i in enumerate(order)}; self.status_msg = "sorted by ping"
    def Connect(self) -> None:
        if self.connected or self.current_server is None: return
        try: self._DoConnect()
        except Exception: self.proc.StopAll(); Main.Activities.Teardown(self.net); raise
    def _DoConnect(self) -> None:
        outbound = self.current_server; proxy_host = Main.Activities.ExtractProxyHost(outbound); proxy_ip = proxy_host
        if proxy_host and not Main.Activities.IsIpAddress(proxy_host):
            try: proxy_ip = socket.gethostbyname(proxy_host); Main.Activities.Log(f"resolved proxy host {proxy_host!r} -> {proxy_ip} for routing")
            except socket.gaierror as e: raise RuntimeError(f"couldn't resolve proxy host {proxy_host!r} to add its bypass route: {e}")
        config_path = Main.Activities.WriteXrayConfig(Main.Activities.BuildXrayConfig(outbound, self.args.socks_port, stats_port=STATS_PORT))
        iface, gw = Main.Activities.DetectDefaultRoute(); service = Main.Activities.DetectServiceForInterface(iface)
        self.net.interface, self.net.gateway, self.net.service, self.net.proxy_host = iface, gw, service, proxy_ip
        self.net.original_dns = Main.Activities.GetCurrentDns(service)
        non_ip = [d for d in self.net.original_dns if not Main.Activities.IsIpAddress(d)]
        if non_ip: Main.Activities.Log(f"heads up: current DNS entries on {service!r} aren't plain IPs: {non_ip} - " + ("likely DNS-over-TLS/HTTPS configured in systemd-resolved. resolvectl can't restore that on disconnect - you may need to re-set it manually afterward." if PLATFORM == "Linux" else f"likely an Encrypted DNS (DoH/DoT) provider set in System Settings > Network > {service} > DNS, not classic DNS servers. networksetup can't restore that on disconnect - you may need to re-select it there manually afterward."))
        self.proc.StartXray(config_path); time.sleep(0.5)
        xray_ok, _ = self.proc.Alive()
        if not xray_ok: raise RuntimeError(f"xray died on startup: {Main.Activities.ReadLogTail(XRAY_PROC_LOG)}")
        if proxy_ip: Main.Activities.AddHostRoute(proxy_ip, gw); self.net.host_route_added = True
        self.proc.StartTun2socks(self.net.tun_device, self.args.socks_port, iface); time.sleep(0.5)
        _, t2s_ok = self.proc.Alive()
        if not t2s_ok: raise RuntimeError(f"tun2socks died on startup: {Main.Activities.ReadLogTail(TUN2SOCKS_PROC_LOG)}")
        if not Main.Activities.WaitForInterface(self.net.tun_device, timeout=5.0):
            _, still_alive = self.proc.Alive()
            raise RuntimeError(f"tun2socks alive={still_alive} but {self.net.tun_device} never appeared within 5s: {Main.Activities.ReadLogTail(TUN2SOCKS_PROC_LOG)}")
        Main.Activities.BringUpTunInterface(self.net.tun_device)
        Main.Activities.AddFullTunnelRoutes(TUN_GW); self.net.routes_added = True
        Main.Activities.SetDns(service, self.args.dns.split(",")); self.net.dns_changed = True
        self.connected = True; self.status_msg = "connected"
        self._stats_stop.clear(); self._stats_thread = threading.Thread(target=self._StatsLoop, daemon=True); self._stats_thread.start()
    def _StatsLoop(self) -> None:
        prev: tuple[int, int, float] | None = None
        while not self._stats_stop.is_set() and self.connected:
            try:
                stats = Main.Activities.QueryStats(self.args.xray_bin, STATS_PORT)
                up, down, now = stats.get("outbound>>>proxy>>>traffic>>>uplink", 0), stats.get("outbound>>>proxy>>>traffic>>>downlink", 0), time.monotonic()
                if prev is not None:
                    dt = now - prev[2]
                    if dt > 0: self.speed_up_bps, self.speed_down_bps = max((up - prev[0]) / dt, 0.0) * 8, max((down - prev[1]) / dt, 0.0) * 8
                prev = (up, down, now)
            except Exception as e: Main.Activities.Log(f"stats poll failed (speed display will stay blank): {e}")
            self._stats_stop.wait(1.5)
    def Disconnect(self) -> None:
        if not self.connected and self.proc.xray_proc is None and self.proc.tun2socks_proc is None: return
        self.status_msg = "disconnecting"; self._stats_stop.set(); self.speed_up_bps = 0.0; self.speed_down_bps = 0.0
        try: self.proc.StopAll(); Main.Activities.Teardown(self.net)
        except Exception as e: Main.Activities.Log(f"Disconnect: unexpected error during cleanup, continuing: {e}")
        self.connected = False; self.status_msg = "disconnected"
    def SwitchServer(self, index: int) -> None:
        if index < 0 or index >= len(self.servers): return
        was_connected = self.connected
        if was_connected: self.Disconnect()
        self.current_server = self.servers[index]
        if was_connected: self.Connect()
gc = Main.GlobalCache