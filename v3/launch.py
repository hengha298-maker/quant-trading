"""Separate V3 launcher. Serves application files, never phone financial data."""

from __future__ import annotations

import json
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from .serve import HOST, PORT, make_server
except ImportError:
    from serve import HOST, PORT, make_server

TAILSCALE = Path(r"C:\Program Files\Tailscale\tailscale.exe")
HTTPS_PORT = 10000
LOCAL_URL = f"http://{HOST}:{PORT}/"
PROXY_TARGET = f"http://{HOST}:{PORT}"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class LauncherError(RuntimeError):
    """A fixed diagnostic, safe to display without sensitive command output."""


def tailscale_json(*arguments: str) -> dict:
    if not TAILSCALE.is_file():
        raise LauncherError("V3_TS_MISSING：未找到 Tailscale，请先打开已安装的客户端。")
    try:
        result = subprocess.run(
            [str(TAILSCALE), *arguments], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=12, creationflags=NO_WINDOW, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise LauncherError("V3_TS_UNAVAILABLE：无法读取 Tailscale 状态，请在客户端检查连接和权限。") from None
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise LauncherError("V3_TS_STATUS：Tailscale 状态不可用，请在客户端完成连接或系统确认。")
    try:
        value = json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise LauncherError("V3_TS_JSON：Tailscale 状态格式异常，未启用手机安装服务。") from None
    if value is None and arguments == ('serve', 'status', '--json'):
        return {}
    if not isinstance(value, dict):
        raise LauncherError("V3_TS_JSON：Tailscale 状态格式异常，未启用手机安装服务。")
    return value


def other_routes(configuration: dict) -> dict:
    """Canonical snapshot excluding only this launcher's 10000 route and ETag."""
    result = {}
    for key, value in configuration.items():
        if key == 'ETag':
            continue
        if key in {'TCP', 'Web', 'AllowFunnel'} and isinstance(value, dict):
            value = {k: v for k, v in value.items() if str(k).rsplit(':', 1)[-1] != str(HTTPS_PORT)}
        elif isinstance(value, dict):
            value = other_routes(value)
        if value not in ({}, None):
            result[key] = value
    return result


def port_in_use(configuration: dict, port: int = HTTPS_PORT) -> bool:
    """Handle both background and nested foreground Serve configurations."""
    if not isinstance(configuration, dict):
        return False
    wanted = str(port)
    for key, value in configuration.items():
        if key in {"TCP", "Web", "AllowFunnel"} and isinstance(value, dict):
            if any(str(endpoint).rsplit(":", 1)[-1] == wanted for endpoint in value):
                return True
        if isinstance(value, dict) and port_in_use(value, port):
            return True
        if isinstance(value, list) and any(port_in_use(item, port) for item in value if isinstance(item, dict)):
            return True
    return False


def port_has_funnel(configuration: dict) -> bool:
    if not isinstance(configuration, dict):
        return False
    funnel = configuration.get("AllowFunnel", {})
    if isinstance(funnel, dict) and any(
        str(endpoint).rsplit(":", 1)[-1] == str(HTTPS_PORT) and allowed
        for endpoint, allowed in funnel.items()
    ):
        return True
    return any(port_has_funnel(value) for value in configuration.values() if isinstance(value, dict))


def route_is_ours(configuration: dict, dns_name: str) -> bool:
    if not isinstance(configuration, dict):
        return False
    if port_has_funnel(configuration):
        return False
    tcp_map = configuration.get("TCP", {})
    web_map = configuration.get("Web", {})
    tcp = tcp_map.get(str(HTTPS_PORT), {}) if isinstance(tcp_map, dict) else {}
    web = web_map.get(f"{dns_name}:{HTTPS_PORT}", {}) if isinstance(web_map, dict) else {}
    proxy = web.get("Handlers", {}).get("/", {}).get("Proxy") if isinstance(web, dict) else None
    if isinstance(tcp, dict) and tcp.get("HTTPS") is True and proxy in {PROXY_TARGET, PROXY_TARGET + "/"}:
        return True
    for value in configuration.values():
        if isinstance(value, dict) and route_is_ours(value, dns_name):
            return True
    return False


def own_dns_name(status: dict) -> str:
    if status.get("BackendState") != "Running":
        raise LauncherError("V3_TS_OFFLINE：请先在 Windows 与 iPhone 上连接同一 Tailscale 私网。")
    own = status.get("Self")
    name = own.get("DNSName", "").rstrip(".") if isinstance(own, dict) else ""
    if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?", name) or not name.endswith(".ts.net"):
        raise LauncherError("V3_TS_DNS：未找到本机私有 HTTPS 名称，请在 Tailscale 中检查 MagicDNS。")
    return name


class Launcher:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.server = None
        self.server_thread = None
        self.share_process: subprocess.Popen | None = None
        self.serve_before: dict | None = None
        self.stopping = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.phone_url = tk.StringVar(value="尚未开启手机安装服务")
        self.status = tk.StringVar(value="正在启动仅本机可访问的 V3 页面……")
        root.title("养鹅计划 V3 · 独立开发版")
        root.geometry("670x520")
        root.minsize(600, 480)
        frame = ttk.Frame(root, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="养鹅计划 V3", font=("Microsoft YaHei UI", 21, "bold")).pack(anchor="w")
        ttk.Label(frame, text="手机独立记账 · 数据只保存在当前设备", font=("Microsoft YaHei UI", 11)).pack(anchor="w", pady=(3, 16))
        ttk.Button(frame, text="打开电脑页面", command=self.open_computer).pack(fill="x", ipady=7)
        self.share_button = ttk.Button(frame, text="开启 iPhone 首次安装服务", command=self.start_share)
        self.share_button.pack(fill="x", pady=8, ipady=7)
        ttk.Entry(frame, textvariable=self.phone_url, state="readonly").pack(fill="x", pady=(4, 6))
        ttk.Button(frame, text="复制 iPhone 安装地址", command=self.copy_url).pack(anchor="w")
        self.stop_button = ttk.Button(frame, text="停止本次手机安装服务", command=self.stop_share, state="disabled")
        self.stop_button.pack(fill="x", pady=10, ipady=5)
        ttk.Label(frame, textvariable=self.status, wraplength=615, justify="left").pack(anchor="w", pady=6)
        notes = (
            "安装：Safari 打开上方 HTTPS 地址 → 分享／添加到主屏幕 → 从新图标在线打开，等待“离线就绪”。\n\n"
            "随后可断开 Windows：飞行模式下关闭并重开 App，记录一笔虚构收支，再重开检查。\n\n"
            "Safari 标签与主屏幕 App 的存储独立。手机财富数据不会发送到 Windows；本阶段没有双向同步。"
            "浏览器数据可能被系统清理，请在设置中导出加密备份并保存到文件。\n\n"
            "使用冻结 V2 分享前，请先停止这里的 V3 安装服务，避免旧版所有权检查拒绝并发分享。"
        )
        ttk.Label(frame, text=notes, wraplength=615, justify="left").pack(anchor="w", pady=(6, 0))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)
        root.after(150, self.start_local)

    def start_local(self) -> None:
        try:
            self.server = make_server()
        except (OSError, ValueError):
            self.status.set("V3_LOCAL_START：本机端口 9393 已占用或应用资源不存在，未连接其他程序。")
            self.share_button.configure(state="disabled")
            return
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.status.set("电脑页面已就绪。手机首次安装需临时开启下方私有 HTTPS 服务。")
        self.open_computer()

    def open_computer(self) -> None:
        if self.server is not None:
            webbrowser.open(LOCAL_URL)

    def copy_url(self) -> None:
        if self.phone_url.get().startswith("https://"):
            self.root.clipboard_clear()
            self.root.clipboard_append(self.phone_url.get())

    def start_share(self) -> None:
        if self.busy or self.share_process is not None or self.server is None:
            return
        self.busy = True
        self.share_button.configure(state="disabled")
        self.status.set("正在检查独立 HTTPS 端口；不会更改 V2 端口，也不会开启公网分享……")
        threading.Thread(target=self._start_share_worker, daemon=True).start()

    def _start_share_worker(self) -> None:
        child = None
        ready = False
        try:
            before = tailscale_json("serve", "status", "--json")
            if port_in_use(before):
                raise LauncherError("V3_TS_PORT_BUSY：HTTPS 端口 10000 已有服务，未覆盖任何配置。")
            dns = own_dns_name(tailscale_json("status", "--json"))
            self.serve_before = other_routes(before)
            if self.stopping.is_set():
                return
            child = subprocess.Popen(
                [str(TAILSCALE), "serve", f"--https={HTTPS_PORT}", PROXY_TARGET],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=NO_WINDOW,
            )
            self.share_process = child
            for _ in range(24):
                if self.stopping.wait(0.4):
                    return
                if child.poll() is not None:
                    raise LauncherError("V3_TS_APPROVAL：手机安装服务未开启。请在 Tailscale 客户端确认连接、HTTPS 和系统权限后重试；程序不会代替你确认。")
                current = tailscale_json("serve", "status", "--json")
                if other_routes(current) != self.serve_before:
                    raise LauncherError("V3_TS_OTHER_CHANGED：其他服务配置出现变化，已停止本次安装服务，未尝试覆盖或重置任何配置。")
                if route_is_ours(current, dns):
                    ready = True
                    self.events.put(("ready", f"https://{dns}:{HTTPS_PORT}/"))
                    return
            raise LauncherError("V3_TS_CONFIRM：未确认本次 HTTPS 服务已就绪，已停止本次子进程。请检查 Tailscale 的权限或首次 HTTPS 确认。")
        except LauncherError as error:
            self.events.put(("error", str(error)))
        except OSError:
            self.events.put(("error", "V3_TS_START：无法启动私有安装服务，请检查 Tailscale 权限。"))
        finally:
            if child is not None and (self.stopping.is_set() or child.poll() is not None or not ready):
                self._terminate_child(child)
                if self.share_process is child:
                    self.share_process = None
            self.events.put(("idle", ""))

    @staticmethod
    def _terminate_child(child: subprocess.Popen) -> None:
        if child.poll() is None:
            try:
                child.terminate()
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            except OSError:
                pass

    def stop_share(self) -> None:
        child = self.share_process
        self.share_process = None
        if child is not None:
            self._terminate_child(child)
        self.phone_url.set("手机安装服务已停止；已安装 App 可继续离线使用")
        self.status.set("本次安装服务已停止。未执行 reset、off 或更改旧 V2 的 Tailscale 配置。")
        if self.serve_before is not None:
            try:
                current = tailscale_json('serve', 'status', '--json')
                if port_in_use(current) or other_routes(current) != self.serve_before:
                    self.status.set('V3_TS_STOP_UNCONFIRMED：本次进程已停止，但服务状态未能完整核对；未重置任何配置，请在 Tailscale 检查。')
            except LauncherError:
                self.status.set('V3_TS_STOP_UNCONFIRMED：本次进程已停止，状态查询失败；未更改其他配置。')
        self.stop_button.configure(state="disabled")
        if not self.busy:
            self.share_button.configure(state="normal")

    def poll(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if self.stopping.is_set():
                    break
                if kind == "ready" and self.share_process is not None:
                    self.phone_url.set(value)
                    self.status.set("手机安装服务已就绪，仅同一 Tailscale 私网可访问。请从主屏幕图标再打开一次后测试离线。")
                    self.stop_button.configure(state="normal")
                elif kind == "error":
                    self.status.set(value)
                    self.phone_url.set("尚未开启手机安装服务")
                elif kind == "idle":
                    self.busy = False
                    self.share_button.configure(state="disabled" if self.share_process is not None else "normal")
        except queue.Empty:
            pass
        if self.share_process is not None and self.share_process.poll() is not None:
            self.share_process = None
            self.stop_button.configure(state="disabled")
            self.share_button.configure(state="normal")
            self.phone_url.set("手机安装服务已退出")
            self.status.set("本次 Tailscale 子进程已退出。已安装 App 可离线使用；重新安装需再次开启服务。")
        if not self.stopping.is_set():
            self.root.after(150, self.poll)

    def close(self) -> None:
        self.stopping.set()
        child = self.share_process
        self.share_process = None
        if child is not None:
            self._terminate_child(child)
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        self.root.destroy()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description='Goose V3 local-only launcher')
    parser.add_argument('--phone', action='store_true', help='Start the separate foreground installation service after local startup')
    options = parser.parse_args()
    root = tk.Tk()
    launcher = Launcher(root)
    if options.phone:
        root.after(400, launcher.start_share)
    root.mainloop()


if __name__ == "__main__":
    main()
