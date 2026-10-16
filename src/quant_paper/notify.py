"""Bark delivery is best effort and never owns a trading/database operation."""
from dataclasses import dataclass, field
from decimal import Decimal
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import requests


@dataclass(frozen=True)
class BarkConfig:
    server: str = field(repr=False)
    device_key: str = field(repr=False)


def load_config(path):
    # Environment wins per field. Do not read an unnecessary private file.
    server = os.environ.get("BARK_SERVER")
    key = os.environ.get("BARK_DEVICE_KEY")
    if server is None or key is None:
        file = Path(path)
        data = json.loads(file.read_text(encoding="utf-8-sig")) if file.exists() else {}
        server = data.get("server", "") if server is None else server
        key = data.get("device_key", "") if key is None else key
    if not server and not key:
        return None
    if not isinstance(server, str) or not isinstance(key, str):
        raise ValueError("Bark 配置无效")
    parts = urlsplit(server)
    # Server base only; never accept a copied URL containing the device key.
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.path not in ("", "/") or parts.query or parts.fragment
            or not re.fullmatch(r"[A-Za-z0-9_-]+", key)):
        raise ValueError("Bark 配置无效")
    _ = parts.port
    return BarkConfig(server.rstrip("/"), key)


def deliver(config_path, title, body):
    """Return only fixed status text; never expose request URLs, payloads or errors."""
    try:
        cfg = load_config(config_path)
        if cfg is None:
            return "未配置"
        # POST /push keeps the key out of URLs. No retries or redirects.
        with requests.post(cfg.server + "/push", json={
            "device_key": cfg.device_key, "title": title, "body": body,
            "group": "模拟交易", "isArchive": 0,
        }, timeout=(3, 5), allow_redirects=False) as response:
            if response.status_code != 200 or response.json().get("code") != 200:
                return "失败"
        return "成功"
    except Exception:
        # Requests exceptions and server responses may contain the secret.
        return "失败"


def money(value):
    return f"{Decimal(value) / 100:.2f}元"


REASONS = {
    "DUPLICATE": "今日已有委托，本次未重复下单",
    "INSUFFICIENT_CASH": "资金不足", "POSITION_LIMIT": "超过仓位限制",
    "NO_POSITION": "无可卖持仓", "T_PLUS_ONE": "T+1限制",
    "PROCEEDS_BELOW_FEE": "卖出金额不足以支付费用",
    "SKIP_NON_TRADING_DAY": "非交易日，已跳过",
    "SKIP_OUTSIDE_SESSION": "非交易时段，已跳过",
}


def report(now, result, snapshot, error=False):
    lines = [f"日期：{now:%Y-%m-%d %H:%M:%S} 北京时间",
             "运行状态：异常，已停止后续下单" if error else "运行状态：正常"]
    if error:
        # No raw exception messages: these can contain credentials or URLs.
        lines.append("行情、配置或程序异常；请查看本机日志。已提交成交仍然有效，请勿重复下单。")
    if isinstance(result, str):
        lines.append(REASONS.get(result, "本次无交易"))
    elif not result and not error:
        lines.append("策略观望，本次无交易。")
    elif result:
        for symbol, status in result.items():
            order = next((o for o in (snapshot or {}).get("orders", [])
                          if o["day"] == now.date().isoformat() and o["symbol"] == symbol), None)
            if status == "FILLED" and order and order["status"] == "FILLED":
                direction = "模拟买入" if order["side"] == "BUY" else "模拟卖出"
                lines.append(f"{direction} {symbol}，数量 {order['qty']}，成交价 {money(order['price'])}，手续费 {money(order['fee'])}")
            else:
                lines.append(f"{symbol}：{REASONS.get(status, '本次无成交')}；成交数量0，成交价/手续费不适用")
    lines.append("账户现金：" + (money(snapshot["cash_fen"]) if snapshot else "无法读取"))
    return "\n".join(lines)


def send_report(config_path, logger, now, result, store, policy="auto", error=False):
    """The whole notification path is isolated, including report formatting/read errors."""
    if policy == "never" or (not error and policy == "auto" and isinstance(result, str)
                             and result.startswith("SKIP_")):
        return
    try:
        try:
            snapshot = store.snapshot() if store else None
        except Exception:
            snapshot = None
        body = report(now, result, snapshot, error)
        status = deliver(config_path, "模拟交易运行异常" if error else "模拟交易运行报告", body)
    except Exception:
        status = "失败"
    try:
        logger.info("Bark 通知：%s", status)
    except Exception:
        pass
