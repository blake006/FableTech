"""本地中转：Hysteria2 / AnyTLS → 本地 SOCKS5（mihomo 内核）。

背景：浏览器引擎（Firefox/Chromium）只能使用 HTTP/SOCKS5 代理，
而 Hysteria2（hy2://，QUIC）与 AnyTLS（anytls://，TLS 混淆）是传输层
协议，浏览器无法直连。标准解法是运行一个本地客户端把上游协议转换
成本地 SOCKS5/混合端口。

本模块集成 mihomo（Clash.Meta，GPL-3.0）：
  - 内核二进制**运行时按需下载**（~21MB，存 data/bin/，不预装、不再分发）
  - 每个 hy2/anytls 代理启动一个本地 mixed 端口（127.0.0.1:随机端口）
  - 按 proxy_id 引用计数：档案关闭/应用退出时释放进程

红线：日志/UI 中上游代理 URL 必须掩码（复用 proxy.masked_url）。
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ..config import paths
from .logger import log_event
from .proxy import ProxyInfo

MIHOMO_VERSION = "v1.19.32"
MIHOMO_ASSET = f"mihomo-windows-amd64-{MIHOMO_VERSION}.zip"
MIHOMO_URL = (
    f"https://github.com/MetaCubeX/mihomo/releases/download/"
    f"{MIHOMO_VERSION}/{MIHOMO_ASSET}"
)

READY_TIMEOUT = 20.0       # 等待本地端口就绪的秒数
DOWNLOAD_TIMEOUT = 300     # 内核下载秒数


class RelayError(RuntimeError):
    """中转不可用（内核缺失/下载失败/启动失败/上游不通）。"""


# ---------- 内核管理 ----------

def mihomo_path() -> Path:
    return paths().bin / "mihomo.exe"


def ensure_binary(progress: bool = True) -> Path:
    """确保 mihomo.exe 存在（不存在则下载解压）。返回可执行路径。"""
    exe = mihomo_path()
    if exe.exists():
        return exe
    paths().bin.mkdir(parents=True, exist_ok=True)
    log_event("INFO", "relay", f"正在下载 mihomo 中转内核（{MIHOMO_VERSION}，约 21MB）…")
    with tempfile.TemporaryDirectory() as td:
        zp = Path(td) / MIHOMO_ASSET
        _download(MIHOMO_URL, zp, progress=progress)
        with zipfile.ZipFile(zp) as zf:
            # 包内为 mihomo.exe（部分版本带版本目录）
            names = [n for n in zf.namelist() if n.endswith("mihomo.exe")]
            if not names:
                raise RelayError("mihomo 压缩包内未找到 mihomo.exe")
            with zf.open(names[0]) as src, open(exe, "wb") as dst:
                shutil.copyfileobj(src, dst)
    log_event("INFO", "relay", "mihomo 中转内核下载完成")
    return exe


def _download(url: str, dest: Path, progress: bool = True) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "FableTech"})
    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp, \
                open(dest, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            last_log = 0.0
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    now = time.monotonic()
                    if now - last_log > 5:
                        last_log = now
                        log_event("INFO", "relay",
                                  f"内核下载进度 {done / 1024 / 1024:.0f}/"
                                  f"{total / 1024 / 1024:.0f} MB")
    except Exception as e:
        raise RelayError(f"mihomo 内核下载失败：{e}") from e


# ---------- 配置生成 ----------

def _y(s: str) -> str:
    """YAML 安全字符串（JSON 字符串是合法 YAML）。"""
    return json.dumps(s, ensure_ascii=False)


def build_config(proxy: ProxyInfo, port: int,
                 dialer: ProxyInfo | None = None) -> str:
    """生成 mihomo 配置：单个上游 + mixed 监听 + 全局模式。

    支持 hysteria2 / anytls / vmess / vless / trojan / ss。
    dialer：预代理（proxy chain，本地 → 预代理 → 上游 → 目标）。
    """
    out = _upstream_outbound(proxy)
    if not out:
        raise RelayError(f"不支持的协议类型: {proxy.type}")

    lines = [
        f"mixed-port: {port}",
        "allow-lan: false",
        "bind-address: 127.0.0.1",
        "mode: global",
        "log-level: error",
        "ipv6: false",
        "geodata-mode: false",
        "geo-auto-update: false",
        "external-controller: ",
        "proxies:",
    ]
    if dialer is not None:
        dialer_out = _upstream_outbound(dialer, name="dialer")
        if not dialer_out:
            raise RelayError(f"预代理协议不支持: {dialer.type}")
        lines.append(_flow_entry(dialer_out))
        out["dialer-proxy"] = "dialer"
    lines.append(_flow_entry(out))
    lines += [
        "proxy-groups: []",
        "rules:",
        "  - MATCH,upstream",
    ]
    return "\n".join(lines) + "\n"


def _flow_entry(out: dict) -> str:
    """outbound dict → YAML flow 映射条目。"""
    fields = ",\n    ".join(
        f"{k}: {_y(v) if isinstance(v, str) else v}" for k, v in out.items())
    return f"  - {{\n    {fields}\n  }}"


def _upstream_outbound(proxy: ProxyInfo, name: str = "upstream") -> dict | None:
    """按协议生成 mihomo 出站配置；不支持的类型返回 None。"""
    ex = proxy.extra or {}
    if proxy.type == "hysteria2":
        out = {
            "name": name,
            "type": "hysteria2",
            "server": proxy.host,
            "port": proxy.port,
            "password": proxy.password or proxy.username or "",
        }
        if proxy.sni:
            out["sni"] = proxy.sni
        if proxy.insecure:
            out["skip-cert-verify"] = True
        if proxy.obfs:
            out["obfs"] = proxy.obfs
            if proxy.obfs_password:
                out["obfs-password"] = proxy.obfs_password
        alpn = ex.get("alpn")
        out["alpn"] = [str(x) for x in alpn] if isinstance(alpn, list) and alpn else ["h3"]
        return out
    if proxy.type == "anytls":
        out = {
            "name": name,
            "type": "anytls",
            "server": proxy.host,
            "port": proxy.port,
            "password": proxy.password or proxy.username or "",
        }
        if proxy.sni:
            out["sni"] = proxy.sni
        if proxy.insecure:
            out["skip-cert-verify"] = True
        return out
    if proxy.type == "vmess":
        network = ex.get("network") or "tcp"
        out = {
            "name": name,
            "type": "vmess",
            "server": proxy.host,
            "port": proxy.port,
            "uuid": ex.get("uuid") or proxy.password or "",
            "alterId": int(ex.get("alterId") or 0),
            "cipher": ex.get("cipher") or "auto",
        }
        if network in ("ws", "http", "h2", "grpc"):
            out["network"] = network
            ws_opts: dict = {}
            if ex.get("path"):
                ws_opts["path"] = ex["path"]
            if ex.get("ws_host"):
                ws_opts["headers"] = {"Host": ex["ws_host"]}
            if ws_opts:
                out["ws-opts"] = ws_opts
            if network == "grpc" and ex.get("path"):
                out["grpc-opts"] = {"grpc-service-name": ex["path"]}
        if ex.get("tls"):
            out["tls"] = True
            if proxy.sni or ex.get("ws_host"):
                out["servername"] = proxy.sni or ex.get("ws_host")
            if proxy.insecure:
                out["skip-cert-verify"] = True
        return out
    if proxy.type == "vless":
        security = (ex.get("security") or "").lower()
        out = {
            "name": name,
            "type": "vless",
            "server": proxy.host,
            "port": proxy.port,
            "uuid": proxy.password or proxy.username or "",
        }
        network = ex.get("type") or "tcp"
        if network != "tcp":
            out["network"] = network
            ws_opts = {}
            if ex.get("path"):
                ws_opts["path"] = ex["path"]
            if ex.get("host"):
                ws_opts["headers"] = {"Host": ex["host"]}
            if ws_opts:
                out["ws-opts"] = ws_opts
        if security in ("tls", "reality", "xtls"):
            out["tls"] = True
            if proxy.sni or ex.get("sni"):
                out["servername"] = proxy.sni or ex.get("sni")
            if proxy.insecure:
                out["skip-cert-verify"] = True
            if security == "reality":
                reality: dict = {}
                if ex.get("pbk"):
                    reality["public-key"] = ex["pbk"]
                if ex.get("sid"):
                    reality["short-id"] = ex["sid"]
                if ex.get("fp"):
                    out["client-fingerprint"] = ex["fp"]
                if reality:
                    out["reality-opts"] = reality
        flow = ex.get("flow")
        if flow:
            out["flow"] = flow
        return out
    if proxy.type == "trojan":
        network = ex.get("type") or "tcp"
        out = {
            "name": name,
            "type": "trojan",
            "server": proxy.host,
            "port": proxy.port,
            "password": proxy.password or proxy.username or "",
        }
        if proxy.sni or ex.get("sni"):
            out["sni"] = proxy.sni or ex.get("sni")
        if proxy.insecure:
            out["skip-cert-verify"] = True
        if network == "ws":
            out["network"] = "ws"
            ws_opts = {}
            if ex.get("path"):
                ws_opts["path"] = ex["path"]
            if ex.get("host"):
                ws_opts["headers"] = {"Host": ex["host"]}
            if ws_opts:
                out["ws-opts"] = ws_opts
        return out
    if proxy.type == "ss":
        return {
            "name": name,
            "type": "ss",
            "server": proxy.host,
            "port": proxy.port,
            "cipher": ex.get("cipher") or "aes-256-gcm",
            "password": proxy.password or "",
        }
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_ready(port: int, timeout: float = READY_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


# ---------- 中转管理器（引用计数） ----------

@dataclass
class _Relay:
    proxy_id: str
    proc: subprocess.Popen
    port: int
    config_path: Path
    log_path: Path
    refcount: int


class RelayManager:
    """按 proxy_id 管理本地中转进程；应用退出时统一回收。"""

    def __init__(self) -> None:
        self._relays: dict[str, _Relay] = {}
        self._lock = threading.Lock()

    def endpoint_for(self, proxy_id: str, proxy: ProxyInfo,
                     dialer: ProxyInfo | None = None) -> tuple[str, int]:
        """确保该代理的本地中转已启动，返回 (127.0.0.1, port)。

        同一 proxy_id 重复调用复用同一中转（引用计数 +1）。
        dialer：预代理（proxy chain，本地 → 预代理 → 上游）。
        """
        with self._lock:
            r = self._relays.get(proxy_id)
            if r is not None and r.proc.poll() is None:
                r.refcount += 1
                return "127.0.0.1", r.port
            # 进程已退出：清理后重建
            if r is not None:
                self._kill(r)

        exe = ensure_binary()
        port = _free_port()
        relay_dir = paths().relays
        relay_dir.mkdir(parents=True, exist_ok=True)
        config_path = relay_dir / f"{proxy_id}.yaml"
        log_path = relay_dir / f"{proxy_id}.log"
        config_path.write_text(build_config(proxy, port, dialer=dialer),
                               encoding="utf-8")
        log_file = open(log_path, "ab")
        try:
            proc = subprocess.Popen(
                [str(exe), "-d", str(relay_dir), "-f", str(config_path)],
                stdout=log_file, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
        except Exception as e:
            log_file.close()
            raise RelayError(f"mihomo 启动失败：{e}") from e

        if not _wait_ready(port):
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            log_file.close()
            tail = ""
            try:
                tail = log_path.read_text(encoding="utf-8",
                                          errors="replace")[-300:]
            except Exception:
                pass
            raise RelayError(
                f"本地中转端口未就绪（上游 {proxy.masked_url()} 可能不通）：{tail}")

        with self._lock:
            self._relays[proxy_id] = _Relay(
                proxy_id=proxy_id, proc=proc, port=port,
                config_path=config_path, log_path=log_path, refcount=1)
        log_event("INFO", "relay",
                  f"本地中转已启动：{proxy.masked_url()} → "
                  f"socks5://127.0.0.1:{port}")
        return "127.0.0.1", port

    def release(self, proxy_id: str) -> None:
        """释放一个引用；归零时停止中转进程。"""
        with self._lock:
            r = self._relays.get(proxy_id)
            if r is None:
                return
            r.refcount -= 1
            if r.refcount > 0:
                return
            del self._relays[proxy_id]
            self._kill(r)

    @staticmethod
    def _kill(r: _Relay) -> None:
        try:
            r.proc.terminate()
            r.proc.wait(timeout=5)
        except Exception:
            try:
                r.proc.kill()
            except Exception:
                pass
        log_event("INFO", "relay", f"本地中转已停止（档案代理 {r.proxy_id}）")

    def stop_all(self) -> None:
        with self._lock:
            relays = list(self._relays.values())
            self._relays.clear()
        for r in relays:
            self._kill(r)

    def status(self) -> list[dict]:
        with self._lock:
            return [
                {"proxy_id": r.proxy_id, "port": r.port,
                 "refcount": r.refcount,
                 "alive": r.proc.poll() is None}
                for r in self._relays.values()
            ]


# 全局单例（GUI/CLI 共用）
_manager: RelayManager | None = None
_manager_lock = threading.Lock()


def relay_manager() -> RelayManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = RelayManager()
        return _manager
