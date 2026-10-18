"""LAN IPv4 discovery for generated links.

Candidates are private IPv4 addresses on interfaces that carry a default route;
virtual and VPN adapters are excluded. PUBLIC_HOST in config overrides the result.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import sys

EXCLUDE = re.compile(r"warp|cloudflare|tailscale|zerotier|wireguard|wg\d|tun|tap|utun|vethernet|hyper-v|"
                     r"virtualbox|vbox|vmware|vmnet|docker|br-|veth|wsl|loopback|npcap|hamachi|nordlynx|"
                     r"openvpn|proton|mullvad|bluetooth", re.I)


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5,
                              encoding="utf-8", errors="replace").stdout
    except Exception:
        return ""


def _private(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    # 100.64/10 is CGNAT (Tailscale); 172.16.0.2 style WARP addresses are caught by the name filter
    return a.version == 4 and a.is_private and not a.is_loopback and not a.is_link_local \
        and a not in ipaddress.ip_network("100.64.0.0/10")


def _windows() -> list[tuple[str, str, bool]]:
    """(name, ip, has_gateway) from PowerShell's Get-NetIPConfiguration."""
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-NetIPConfiguration | ForEach-Object { $_.InterfaceAlias + '|' + $_.InterfaceDescription + '|' + "
          "(($_.IPv4Address | ForEach-Object { $_.IPAddress }) -join ',') + '|' + "
          "[string]($_.IPv4DefaultGateway.NextHop) }")
    out = []
    for line in _run(["powershell", "-NoProfile", "-Command", ps]).splitlines():
        parts = line.strip().split("|")
        if len(parts) != 4:
            continue
        alias, desc, ips, gw = parts
        for ip in filter(None, ips.split(",")):
            out.append((f"{alias} {desc}", ip, bool(gw.strip())))
    return out


def _linux() -> list[tuple[str, str, bool]]:
    gw_devs = set(re.findall(r"\bdev (\S+)", _run(["ip", "-4", "route", "show", "default"])))
    out = []
    for line in _run(["ip", "-4", "-o", "addr", "show"]).splitlines():
        m = re.match(r"\d+:\s+(\S+)\s+inet\s+([\d.]+)/", line)
        if m:
            out.append((m.group(1), m.group(2), m.group(1) in gw_devs))
    return out


def _macos() -> list[tuple[str, str, bool]]:
    m = re.search(r"interface:\s*(\S+)", _run(["route", "-n", "get", "default"]))
    gw = m.group(1) if m else ""
    out, cur = [], ""
    for line in _run(["ifconfig"]).splitlines():
        if line and not line[0].isspace():
            cur = line.split(":")[0]
        m = re.search(r"inet ([\d.]+)", line)
        if m:
            out.append((cur, m.group(1), cur == gw))
    return out


def _fallback() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET, nothing is sent
            return s.getsockname()[0]
    except OSError:
        return None


def lan_ip() -> str:
    if sys.platform == "win32":
        ifaces = _windows()
    elif sys.platform == "darwin":
        ifaces = _macos()
    else:
        ifaces = _linux()
    cands = [(name, ip, gw) for name, ip, gw in ifaces if _private(ip) and not EXCLUDE.search(name)]
    cands.sort(key=lambda x: (not x[2], not x[1].startswith("192.168."), x[1]))
    if cands:
        return cands[0][1]
    ip = _fallback()
    return ip if ip and _private(ip) else "127.0.0.1"


def public_base(cfg) -> str:
    host = cfg.server.public_host or (
        "127.0.0.1" if cfg.server.host in ("127.0.0.1", "localhost") else lan_ip())
    return f"http://{host}:{cfg.server.port}"
