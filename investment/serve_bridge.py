"""Verify/reuse, or temporarily own, the existing private HTTPS app route.

No background Serve, Funnel, reset/off, registry, service or firewall change.
Only the exact foreground child created here may ever be stopped here.
"""
from __future__ import annotations

from copy import deepcopy
import re
import subprocess
import time

from v3.launch import (TAILSCALE, NO_WINDOW, tailscale_json, own_dns_name,
    other_routes, port_in_use, port_has_funnel, route_is_ours)


def _result(code, *, ready=False, status="unavailable", child=None, before=None):
    return {"ready": ready, "status": status, "code": code,
        "ownedProcess": child, "beforeOtherRoutes": before}


def _host(value):
    if not isinstance(value, str) or len(value) > 259:
        raise ValueError("PHONE_HOST_INVALID")
    if value.endswith(":10001"):
        value = value[:-6]
    labels = value.split(".")
    if not value.endswith(".ts.net") or not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", p) for p in labels):
        raise ValueError("PHONE_HOST_INVALID")
    return value.lower()


def _strict_route(configuration, dns):
    """The old helper finds a matching route; also reject conflicting siblings."""
    if not route_is_ours(configuration, dns) or port_has_funnel(configuration):
        return False
    def check(value):
        if isinstance(value, dict):
            for kind, items in value.items():
                if kind in {"TCP", "Web", "AllowFunnel"} and isinstance(items, dict):
                    for endpoint, settings in items.items():
                        if str(endpoint).rsplit(":", 1)[-1] != "10001":
                            continue
                        if kind == "TCP" and (not isinstance(settings, dict) or set(settings) != {"HTTPS"} or settings["HTTPS"] is not True):
                            return False
                        if kind == "AllowFunnel" and settings is not False:
                            return False
                        if kind == "Web":
                            if str(endpoint).lower() != dns + ":10001" or not isinstance(settings, dict) or set(settings) != {"Handlers"}:
                                return False
                            handlers = settings["Handlers"]
                            if not isinstance(handlers, dict) or set(handlers) != {"/"}:
                                return False
                            handler = handlers["/"]
                            if not isinstance(handler, dict) or set(handler) != {"Proxy"} or handler["Proxy"] not in {"http://127.0.0.1:9394", "http://127.0.0.1:9394/"}:
                                return False
                if isinstance(items, (dict, list)) and not check(items):
                    return False
        elif isinstance(value, list):
            return all(check(item) for item in value)
        return True
    return check(configuration)


def _stop_owned(child):
    if child is None:
        return True
    try:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
        return child.poll() is not None
    except (OSError, subprocess.TimeoutExpired):
        return False


def check_private_route(phone_host, port=9394):
    """Read-only revalidation for a borrowed route; never starts a child."""
    if type(port) is not int or port != 9394:
        return _result("PRIVATE_ROUTE_UNREVIEWED_LOCAL_PORT")
    try:
        wanted = _host(phone_host)
        dns = own_dns_name(tailscale_json("status", "--json"))
        if dns.lower() != wanted:
            return _result("PRIVATE_ROUTE_HOST_MISMATCH")
        current = tailscale_json("serve", "status", "--json")
        if _strict_route(current, dns):
            return _result("PRIVATE_ROUTE_REVALIDATED", ready=True, status="verified")
        return _result("PRIVATE_ROUTE_NOT_CURRENTLY_VERIFIED")
    except Exception:
        return _result("PRIVATE_ROUTE_STATUS_UNAVAILABLE")


def ensure_private_route(phone_host, port=9394):
    """Return an opaque route handle/status; never claim an unverified URL.

    Callers may display ready/status/code, not serialize the opaque process or
    configuration snapshot. An existing safe route is read and reused only.
    """
    if type(port) is not int or port != 9394:
        return _result("PRIVATE_ROUTE_UNREVIEWED_LOCAL_PORT")
    try:
        wanted = _host(phone_host)
        dns = own_dns_name(tailscale_json("status", "--json"))
        if dns.lower() != wanted:
            return _result("PRIVATE_ROUTE_HOST_MISMATCH")
        before = tailscale_json("serve", "status", "--json")
        if port_has_funnel(before):
            return _result("PRIVATE_ROUTE_FUNNEL_REJECTED")
        if port_in_use(before):
            if _strict_route(before, dns):
                return _result("PRIVATE_ROUTE_REUSED", ready=True, status="reused")
            return _result("PRIVATE_ROUTE_CONFLICT_NO_OVERRIDE")
        snapshot = deepcopy(other_routes(before))
    except Exception:
        return _result("PRIVATE_ROUTE_STATUS_UNAVAILABLE")
    child = None
    accepted = False
    try:
        child = subprocess.Popen([str(TAILSCALE), "serve", "--https=10001", "http://127.0.0.1:9394"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW)
        limit = time.monotonic()+12
        while time.monotonic() < limit:
            if child.poll() is not None:
                return _result("PRIVATE_ROUTE_FOREGROUND_EXITED")
            time.sleep(.2)
            current = tailscale_json("serve", "status", "--json")
            if other_routes(current) != snapshot:
                return _result("PRIVATE_ROUTE_OTHER_CONFIGURATION_CHANGED")
            if port_has_funnel(current):
                return _result("PRIVATE_ROUTE_FUNNEL_REJECTED")
            if _strict_route(current, dns):
                accepted = True
                return _result("PRIVATE_ROUTE_STARTED", ready=True, status="started", child=child, before=snapshot)
            if port_in_use(current):
                return _result("PRIVATE_ROUTE_CONFLICT_NO_OVERRIDE")
        return _result("PRIVATE_ROUTE_START_NOT_VERIFIED")
    except Exception:
        return _result("PRIVATE_ROUTE_START_FAILED")
    finally:
        if child is not None and not accepted:
            if not _stop_owned(child):
                # Preserve the exact ownership handle so the control window
                # can attempt its own child's cleanup on close as well.
                return _result("PRIVATE_ROUTE_OWNED_CLEANUP_UNVERIFIED", child=child, before=snapshot)


def close_owned_route(handle):
    """An existing shared route is never stopped. No global Serve reset/off."""
    if not isinstance(handle, dict) or handle.get("ownedProcess") is None:
        return {"status": "no-owned-process", "code": "EXISTING_PRIVATE_ROUTE_UNCHANGED"}
    child = handle["ownedProcess"]
    handle["ready"] = False
    if not _stop_owned(child):
        return {"status": "unverified", "code": "OWNED_PRIVATE_ROUTE_STOP_UNVERIFIED"}
    handle["ownedProcess"] = None
    try:
        current = tailscale_json("serve", "status", "--json")
        if other_routes(current) != handle.get("beforeOtherRoutes") or port_in_use(current):
            return {"status": "unverified", "code": "PRIVATE_ROUTE_STATUS_CHANGED_NO_RESET"}
    except Exception:
        return {"status": "unverified", "code": "PRIVATE_ROUTE_STOP_STATUS_UNAVAILABLE"}
    return {"status": "stopped", "code": "OWNED_PRIVATE_ROUTE_STOPPED"}
