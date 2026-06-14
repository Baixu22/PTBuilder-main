#!/usr/bin/env python3
import argparse
import http.client
import ipaddress
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 54321
TASKS = queue.Queue()
RESULTS = {}
STATE = {
    "last_poll": None,
    "last_result": None,
    "poll_count": 0,
}
RESULTS_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()


def json_response(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.end_headers()
    handler.wfile.write(body)


def empty_response(handler, status):
    handler.send_response(status)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.end_headers()


def read_json(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    if length == 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "PTBuilderBridge/0.1"

    def log_message(self, fmt, *args):
        if self.path in ("/next", "/health"):
            return
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def do_OPTIONS(self):
        empty_response(self, 204)

    def do_GET(self):
        if self.path == "/health":
            with STATE_LOCK:
                state = dict(STATE)
            state["ok"] = True
            state["connected"] = is_pt_bridge_connected(state)
            json_response(self, 200, state)
            return

        if self.path == "/next":
            with STATE_LOCK:
                STATE["last_poll"] = time.time()
                STATE["poll_count"] += 1
            try:
                task = TASKS.get_nowait()
            except queue.Empty:
                empty_response(self, 204)
                return
            json_response(self, 200, task)
            return

        empty_response(self, 404)

    def do_POST(self):
        if self.path == "/run":
            payload = read_json(self)
            code = payload.get("code")
            if not code:
                json_response(self, 400, {"ok": False, "error": "Missing code"})
                return

            task_id = payload.get("id") or str(uuid.uuid4())
            TASKS.put({"id": task_id, "code": code})
            json_response(self, 202, {"ok": True, "id": task_id})
            return

        if self.path == "/result":
            payload = read_json(self)
            task_id = payload.get("id")
            if not task_id:
                json_response(self, 400, {"ok": False, "error": "Missing id"})
                return

            with RESULTS_LOCK:
                RESULTS[task_id] = payload.get("result", payload)
            with STATE_LOCK:
                STATE["last_result"] = time.time()
            json_response(self, 200, {"ok": True})
            return

        if self.path == "/wait":
            payload = read_json(self)
            task_id = payload.get("id")
            timeout = float(payload.get("timeout", 30))
            deadline = time.time() + timeout

            while time.time() < deadline:
                with RESULTS_LOCK:
                    if task_id in RESULTS:
                        json_response(self, 200, RESULTS.pop(task_id))
                        return
                time.sleep(0.1)

            json_response(self, 408, {"ok": False, "error": "Timed out waiting for Packet Tracer"})
            return

        empty_response(self, 404)


def start_server(args):
    server = ThreadingHTTPServer((args.host, args.port), BridgeHandler)
    print(f"PTBuilder bridge listening on http://{args.host}:{args.port}")
    print("Open Packet Tracer, open Extensions > Builder Code Editor, and keep CLI Bridge checked.")
    server.serve_forever()


def is_pt_bridge_connected(state):
    last_poll = state.get("last_poll")
    return bool(last_poll and time.time() - last_poll < 2)


def candidate_packet_tracer_paths():
    names = ["PacketTracer.exe", "Cisco Packet Tracer.exe"]
    roots = [
        os.environ.get("PACKET_TRACER_PATH"),
        r"C:\Program Files\Cisco Packet Tracer 8.2.2\bin\PacketTracer.exe",
        r"C:\Program Files\Cisco Packet Tracer 8.2.1\bin\PacketTracer.exe",
        r"C:\Program Files\Cisco Packet Tracer 8.2.0\bin\PacketTracer.exe",
        r"C:\Program Files\Cisco Packet Tracer 8.2\bin\PacketTracer.exe",
        r"C:\Program Files\Cisco Packet Tracer\bin\PacketTracer.exe",
        r"C:\Program Files (x86)\Cisco Packet Tracer 8.2.2\bin\PacketTracer.exe",
        r"C:\Program Files (x86)\Cisco Packet Tracer 8.2.1\bin\PacketTracer.exe",
        r"C:\Program Files (x86)\Cisco Packet Tracer 8.2.0\bin\PacketTracer.exe",
        r"C:\Program Files (x86)\Cisco Packet Tracer 8.2\bin\PacketTracer.exe",
        r"C:\Program Files (x86)\Cisco Packet Tracer\bin\PacketTracer.exe",
    ]
    for root in roots:
        if root:
            yield root
    for name in names:
        found = shutil.which(name)
        if found:
            yield found


def find_packet_tracer_path(explicit_path=None):
    if explicit_path:
        return explicit_path if os.path.exists(explicit_path) else None
    seen = set()
    for path in candidate_packet_tracer_paths():
        if path in seen:
            continue
        seen.add(path)
        if os.path.exists(path):
            return path
    return None


def is_packet_tracer_running():
    if os.name != "nt":
        return None
    try:
        output = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        lowered = output.lower()
        return "packettracer.exe" in lowered or "cisco packet tracer" in lowered
    except Exception:
        return None


def is_builder_window_open():
    if os.name != "nt":
        return None
    try:
        import ctypes

        return bool(ctypes.windll.user32.FindWindowW(None, "Builder Code Editor"))
    except Exception:
        return None


def launch_packet_tracer(args):
    path = find_packet_tracer_path(args.pt_path)
    if not path:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "Could not find PacketTracer.exe. Pass --pt-path or set PACKET_TRACER_PATH.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    cmd = [path]
    if args.file:
        cmd.append(args.file)
    process = subprocess.Popen(
        cmd,
        cwd=os.path.dirname(path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(
        json.dumps(
            {"ok": True, "path": path, "pid": process.pid, "started": True},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def bridge_status(args):
    status, payload = request_json("GET", "/health", host=args.host, port=args.port)
    if status >= 400:
        raise RuntimeError(payload)
    payload["packetTracerRunning"] = is_packet_tracer_running()
    payload["builderWindowOpen"] = is_builder_window_open()
    payload["availableTransport"] = (
        "bridge" if payload.get("connected") else "ui" if payload["builderWindowOpen"] else None
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("availableTransport") else 1


def wait_connected(args):
    result = wait_connected_result(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("connected") else 1


def wait_connected_result(args):
    deadline = time.time() + args.timeout
    last_payload = None
    while time.time() < deadline:
        try:
            status, payload = request_json("GET", "/health", host=args.host, port=args.port, timeout=2)
            if status < 400:
                last_payload = payload
                if payload.get("connected"):
                    return payload
        except Exception:
            pass
        time.sleep(0.5)

    return {
        "ok": False,
        "connected": False,
        "error": "Timed out waiting for Packet Tracer WebView bridge polling",
        "lastHealth": last_payload,
    }


def doctor(args):
    report = {
        "packetTracerPath": find_packet_tracer_path(args.pt_path),
        "packetTracerRunning": is_packet_tracer_running(),
        "builderWindowOpen": is_builder_window_open(),
        "bridgeServer": None,
        "webViewConnected": False,
        "builderPtsExists": os.path.exists(os.path.join(os.getcwd(), "Builder.pts")),
        "sourceExists": os.path.exists(os.path.join(os.getcwd(), "source", "main.js")),
    }
    try:
        status, payload = request_json("GET", "/health", host=args.host, port=args.port, timeout=2)
        report["bridgeServer"] = payload if status < 400 else {"ok": False, "status": status}
        report["webViewConnected"] = bool(payload and payload.get("connected"))
    except Exception as error:
        report["bridgeServer"] = {"ok": False, "error": str(error)}

    report["ok"] = bool(report["bridgeServer"] and report["bridgeServer"].get("ok"))
    report["availableTransport"] = (
        "bridge" if report["webViewConnected"] else "ui" if report["builderWindowOpen"] else None
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def request_json(method, path, payload=None, host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=5):
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    data = response.read()
    connection.close()

    if not data:
        return response.status, None
    return response.status, json.loads(data.decode("utf-8"))


def parse_jsonish(value):
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "{[":
        return value
    try:
        return json.loads(text)
    except Exception:
        return value


def normalize_execution_result(result):
    result = parse_jsonish(result)
    if isinstance(result, dict) and "value" in result:
        result = dict(result)
        result["value"] = parse_jsonish(result.get("value"))
    return result


def submit_code(code, args):
    transport = getattr(args, "transport", "auto")
    if transport == "auto":
        try:
            status, health = request_json("GET", "/health", host=args.host, port=args.port, timeout=1)
            transport = "bridge" if status < 400 and health.get("connected") else "ui"
        except Exception:
            transport = "ui"

    if transport == "ui":
        return submit_code_via_ui(code, args)

    if getattr(args, "require_connected", False):
        wait_args = argparse.Namespace(host=args.host, port=args.port, timeout=min(args.timeout, 10))
        if not wait_connected_result(wait_args).get("connected"):
            raise RuntimeError("Packet Tracer WebView bridge is not connected")

    task_id = str(uuid.uuid4())
    code = wrap_code_for_bridge(code)
    status, payload = request_json(
        "POST",
        "/run",
        {"id": task_id, "code": code},
        host=args.host,
        port=args.port,
    )
    if status >= 400:
        raise RuntimeError(payload)

    status, result = request_json(
        "POST",
        "/wait",
        {"id": task_id, "timeout": args.timeout},
        host=args.host,
        port=args.port,
        timeout=args.timeout + 5,
    )
    if status >= 400:
        raise RuntimeError(result)
    return normalize_execution_result(result)


def wrap_code_for_bridge(code):
    encoded = urllib.parse.quote(code, safe="")
    return (
        "return (new Function(decodeURIComponent("
        + json.dumps(encoded)
        + ")))();"
    )


def submit_code_via_ui(code, args):
    fd, path = tempfile.mkstemp(prefix="ptbuilder-", suffix=".js", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(code)
        script = os.path.join(os.getcwd(), "tools", "ptbuilder_ui_runner.ps1")
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                script,
                "-CodePath",
                path,
                "-TimeoutSeconds",
                str(int(getattr(args, "timeout", 20))),
            ],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            return {
                "ok": False,
                "error": completed.stderr.strip() or completed.stdout.strip(),
                "transport": "ui",
            }
        try:
            value = json.loads(completed.stdout.strip())
        except Exception:
            value = completed.stdout.strip()
        return normalize_execution_result({"ok": True, "value": value, "transport": "ui"})
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def js_string(value):
    return json.dumps(value, ensure_ascii=False)


def wrap_expression(expression):
    return f"return ({expression});"


def print_result(result):
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def print_output(args, result):
    if getattr(args, "output", "summary") == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(summarize_result(result), ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def summarize_device(value):
    if not isinstance(value, dict):
        return value
    device = value.get("device") if "device" in value else value
    if not isinstance(device, dict):
        return value
    summary = {
        "name": device.get("name"),
        "model": device.get("model"),
        "type": device.get("type"),
        "power": device.get("power"),
    }
    ports = []
    for port in device.get("ports") or []:
        if port.get("name") in ("FastEthernet0", "GigabitEthernet0/0", "GigabitEthernet0/1"):
            ports.append(
                {
                    "name": port.get("name"),
                    "ip": port.get("ip"),
                    "mask": port.get("subnetMask"),
                    "up": port.get("isPortUp"),
                    "protocol": port.get("isProtocolUp"),
                }
            )
    if ports:
        summary["ports"] = ports
    if "created" in value:
        summary["created"] = value.get("created")
    return summary


def summarize_step(step):
    result = step.get("result") or {}
    value = result.get("value")
    item = {
        "kind": step.get("kind"),
        "name": step.get("name"),
        "ok": bool(result.get("ok")),
    }
    if step.get("kind") == "ensure-device":
        item["device"] = summarize_device(value)
    elif step.get("kind") == "move-device" and isinstance(value, dict):
        item["moved"] = value.get("moved")
        item["before"] = value.get("before", {}).get("logical")
        item["after"] = value.get("device", {}).get("logical")
        item["device"] = summarize_device(value.get("device"))
    elif step.get("kind") == "configure-pc":
        item["device"] = summarize_device(value)
    elif step.get("kind") == "ensure-link" and isinstance(value, dict):
        item["created"] = value.get("created", False)
        item["existing"] = value.get("existing", False)
        item["left"] = value.get("left")
        item["right"] = value.get("right")
    elif step.get("kind") == "remove-link" and isinstance(value, dict):
        item["deleted"] = value.get("deleted", False)
        item["before"] = value.get("before")
        item["after"] = value.get("after")
    elif step.get("kind") == "remove-device" and isinstance(value, dict):
        item["deleted"] = value.get("deleted", False)
        item["reason"] = value.get("reason")
    elif value is not None:
        item["value"] = value
    if not result.get("ok"):
        item["error"] = result.get("error")
    return item


def summarize_steps(steps):
    failures = [summarize_step(step) for step in steps if not (step.get("result") or {}).get("ok")]
    return {
        "total": len(steps),
        "ok": len(steps) - len(failures),
        "failed": failures,
    }


def summarize_test(test):
    result = test.get("result") or {}
    value = result.get("value")
    inner = value if isinstance(value, dict) else {}
    item = {
        "type": test.get("type"),
        "source": test.get("source"),
        "destination": test.get("destination"),
        "ok": bool(test.get("ok")),
    }
    if inner.get("method"):
        item["method"] = inner.get("method")
    if "expected" in test:
        item["expected"] = test.get("expected")
    if "actual" in test:
        item["actual"] = test.get("actual")
    if test.get("skipped"):
        item["skipped"] = True
        item["reason"] = test.get("reason")
    if not item["ok"]:
        item["error"] = result.get("error") or inner.get("error") or test.get("error")
    return item


SENSITIVE_SUMMARY_KEYS = ("password", "secret", "token", "key", "credential")


def redact_for_summary(value):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            lower = str(key).lower()
            if any(token in lower for token in SENSITIVE_SUMMARY_KEYS):
                redacted[key] = "***"
            else:
                redacted[key] = redact_for_summary(item)
        return redacted
    if isinstance(value, list):
        items = [redact_for_summary(item) for item in value[:5]]
        if len(value) > 5:
            items.append({"truncated": len(value) - 5})
        return items
    return value


def brief_request_summary(request):
    if not isinstance(request, dict):
        return request
    keys = (
        "device",
        "name",
        "service",
        "type",
        "enabled",
        "ssid",
        "security",
        "ip",
        "mask",
        "subnetMask",
        "gateway",
        "dns",
        "source",
        "destination",
        "mode",
        "expect",
    )
    brief = {key: redact_for_summary(request.get(key)) for key in keys if key in request}
    if isinstance(request.get("users"), list):
        brief["users"] = len(request.get("users") or [])
    commands = request.get("commands") or request.get("config")
    if commands:
        brief["commandLines"] = len([line for line in command_text(commands).splitlines() if line.strip()])
    return brief


def compact_operation_summary(item):
    compact = {
        "id": item.get("id"),
        "kind": item.get("kind"),
        "device": item.get("device"),
        "status": item.get("status"),
        "adapter": item.get("adapter"),
        "capability": item.get("capability"),
    }
    if item.get("reason"):
        compact["reason"] = item.get("reason")
    if item.get("confidence"):
        compact["confidence"] = item.get("confidence")
    request = item.get("request")
    if isinstance(request, dict):
        compact["request"] = brief_request_summary(request)
    commands = item.get("commands")
    if commands:
        compact["commandLines"] = len([line for line in str(commands).splitlines() if line.strip()])
    return compact


def compact_ui_device_result(result):
    if not isinstance(result, dict):
        return result
    compact = {
        "ok": result.get("ok"),
        "schema": result.get("schema"),
        "action": result.get("action"),
        "device": result.get("device"),
        "service": result.get("service"),
        "enabled": result.get("enabled"),
        "username": result.get("username"),
        "permission": result.get("permission"),
        "recipe": result.get("recipe"),
        "verified": result.get("verified"),
        "coordinateSource": result.get("coordinateSource"),
        "openMethod": result.get("openMethod"),
        "summary": result.get("summary"),
        "error": result.get("error"),
    }
    return {key: value for key, value in compact.items() if value is not None}


def compact_apply_step(item):
    if not isinstance(item, dict):
        return item
    compact = {
        "id": item.get("id"),
        "kind": item.get("kind"),
    }
    result = item.get("result")
    if isinstance(result, dict) and result.get("schema") == "ptbuilder.ui.device.v1":
        compact["result"] = compact_ui_device_result(result)
    else:
        compact["result"] = redact_for_summary(result)
    return compact


def compact_capability_operation(item):
    if not isinstance(item, dict):
        return item
    return {
        "supported": item.get("supported"),
        "adapter": item.get("adapter"),
        "confidence": item.get("confidence"),
    }


def compact_capability_device(item):
    if not isinstance(item, dict):
        return item
    return {
        "name": item.get("name"),
        "model": item.get("model"),
        "type": item.get("type"),
        "ports": item.get("ports"),
        "candidates": sorted((item.get("candidates") or {}).keys()),
    }


def summarize_result(result):
    if not isinstance(result, dict):
        return result
    if result.get("schema") == "ptbuilder.layout.plan.v1":
        plan = result.get("plan") or {}
        return {
            "ok": result.get("ok"),
            "summary": result.get("summary") or plan.get("summary"),
            "groups": (plan.get("groupSummaries") or [])[:20],
            "warnings": (plan.get("warnings") or [])[:20],
            "planFile": result.get("planFile"),
        }
    if result.get("schema") == "ptbuilder.layout.apply.v1":
        apply_result = result.get("apply") or {}
        audit = result.get("audit")
        apply_summary = {
            "ok": apply_result.get("ok"),
            "summary": apply_result.get("summary"),
        }
        if apply_result.get("dryRun"):
            apply_summary["dryRun"] = True
            apply_summary["operations"] = (apply_result.get("operations") or [])[:50]
        else:
            apply_summary["steps"] = summarize_steps(apply_result.get("steps", []))
        return {
            "ok": result.get("ok"),
            "summary": result.get("summary"),
            "planFile": result.get("planFile"),
            "apply": apply_summary,
            "audit": {
                "ok": audit.get("ok"),
                "summary": audit.get("summary"),
                "layoutWarnings": count_layout_warnings(audit),
                "issues": (audit.get("issues") or [])[:20],
            } if audit else None,
        }
    if result.get("schema") == "ptbuilder.company.v1":
        return {
            "ok": result.get("ok"),
            "summary": result.get("summary"),
            "apply": {
                "ok": (result.get("apply") or {}).get("ok"),
                "dryRun": (result.get("apply") or {}).get("dryRun"),
                "summary": (result.get("apply") or {}).get("summary"),
            } if result.get("apply") else None,
            "departments": result.get("departments"),
            "policies": result.get("policies"),
            "files": result.get("files"),
            "notes": result.get("notes"),
        }
    if result.get("schema") == "ptbuilder.company.audit.v1":
        return {
            "ok": result.get("ok"),
            "summary": result.get("summary"),
            "departments": result.get("departments"),
            "issues": (result.get("issues") or [])[:50],
            "unverified": (result.get("unverified") or [])[:50],
        }
    if result.get("schema") == "ptbuilder.capabilities.v1":
        return {
            "ok": result.get("ok"),
            "summary": result.get("summary"),
            "operations": {key: compact_capability_operation(value) for key, value in (result.get("operations") or {}).items()},
            "devices": [compact_capability_device(item) for item in (result.get("devices") or [])[:30]],
            "files": result.get("files"),
            "warnings": result.get("warnings"),
        }
    if result.get("schema") == "ptbuilder.pdu.list.v1":
        return {
            "ok": result.get("ok"),
            "summary": result.get("summary"),
            "error": result.get("error"),
            "rows": [compact_pdu_row(row) for row in (result.get("rows") or [])[:50]],
        }
    if result.get("schema") == "ptbuilder.pdu.ping.v1":
        return {
            "ok": result.get("ok"),
            "summary": result.get("summary") or pdu_status_summary(result),
            "row": result.get("row"),
            "submitted": result.get("submitted"),
            "submitError": (result.get("submit") or {}).get("error") if isinstance(result.get("submit"), dict) else None,
            "pduListBefore": result.get("pduListBefore"),
            "pduListAfter": result.get("pduListAfter"),
        }
    if result.get("schema") == "ptbuilder.connectivity.matrix.v1":
        def compact_matrix_item(item):
            compact = {
                "source": item.get("source"),
                "destination": item.get("destination"),
                "ok": item.get("ok"),
                "status": item.get("status"),
                "row": item.get("row"),
            }
            nested = item.get("result")
            if isinstance(nested, dict):
                compact["error"] = nested.get("error")
                if not compact.get("status"):
                    compact["status"] = nested.get("status")
                if not compact.get("row"):
                    compact["row"] = nested.get("row")
            return compact

        return {
            "ok": result.get("ok"),
            "summary": result.get("summary"),
            "failures": [compact_matrix_item(item) for item in (result.get("failures") or [])[:50]],
            "tests": [compact_matrix_item(item) for item in (result.get("tests") or [])[:50]],
        }
    if result.get("schema") == "ptbuilder.ui.inspect.v1":
        windows = []
        for window in result.get("windows") or []:
            windows.append(
                {
                    "name": window.get("name"),
                    "className": window.get("className"),
                    "controlType": window.get("controlType"),
                    "processId": window.get("processId"),
                    "children": len(window.get("children") or []),
                }
            )
        return {
            "ok": result.get("ok"),
            "summary": result.get("summary"),
            "windows": windows,
            "files": result.get("files"),
        }
    if result.get("schema") == "ptbuilder.ui.device.v1":
        return compact_ui_device_result(result)
    if result.get("schema") == "ptbuilder.operation.plan.v1":
        return {
            "ok": result.get("ok"),
            "summary": result.get("summary"),
            "blocked": [compact_operation_summary(item) for item in (result.get("blocked") or [])[:20]],
            "operations": [compact_operation_summary(item) for item in (result.get("operations") or [])[:50]],
            "files": result.get("files"),
        }
    if result.get("schema") == "ptbuilder.operation.apply.v1":
        return {
            "ok": result.get("ok"),
            "dryRun": result.get("dryRun"),
            "summary": result.get("summary"),
            "blocked": [compact_operation_summary(item) for item in (result.get("blocked") or [])[:20]],
            "steps": [compact_apply_step(item) for item in (result.get("steps") or [])[:50]],
        }
    if "snapshot" in result and "audit" in result:
        snapshot = result.get("snapshot") or {}
        audit = result.get("audit") or {}
        return {
            "ok": result.get("ok"),
            "summary": audit.get("summary"),
            "deviceCount": snapshot.get("deviceCount"),
            "links": len(snapshot.get("links", [])),
            "subnets": [
                {"network": subnet.get("network"), "devices": len(subnet.get("devices", []))}
                for subnet in audit.get("subnets", [])
            ],
            "issues": (audit.get("issues") or [])[:20],
        }
    if "plan" in result and "audit" in result:
        plan = result.get("plan") or {}
        return {
            "ok": result.get("ok"),
            "summary": {
                "pcConfigs": len(plan.get("pcConfigs") or {}),
                "moveDevices": len(plan.get("moveDevices") or []),
                "links": len(plan.get("links") or []),
                "removeLinks": len(plan.get("removeLinks") or []),
                "removeDevices": len(plan.get("removeDevices") or []),
                "manualActions": len(plan.get("manualActions") or []),
            },
            "audit": (result.get("audit") or {}).get("summary"),
            "groups": plan.get("groupSummaries"),
            "manualActions": (plan.get("manualActions") or [])[:20],
            "planFile": result.get("planFile"),
        }
    if "summary" in result and "issues" in result and "subnets" in result:
        return {
            "ok": result.get("ok"),
            "summary": result.get("summary"),
            "subnets": [
                {"network": subnet.get("network"), "devices": len(subnet.get("devices", []))}
                for subnet in result.get("subnets", [])
            ],
            "issues": (result.get("issues") or [])[:50],
        }
    if "devices" in result and "summary" in result and "matches" in (result.get("summary") or {}):
        return {
            "ok": result.get("ok"),
            "summary": result.get("summary"),
            "devices": result.get("devices", []),
        }
    if "tests" in result and "failures" in result and "summary" in result:
        return {
            "ok": result.get("ok"),
            "summary": result.get("summary"),
            "failures": [
                {
                    "source": item.get("source"),
                    "destination": item.get("destination"),
                    "ok": item.get("ok"),
                    "error": (item.get("result") or {}).get("error") if isinstance(item.get("result"), dict) else None,
                }
                for item in result.get("failures", [])
            ][:50],
            "tests": [
                {"source": item.get("source"), "destination": item.get("destination"), "ok": item.get("ok")}
                for item in result.get("tests", [])
            ][:50],
        }
    if "unverified" in result and "groups" in result and "issues" in result:
        return {
            "ok": result.get("ok"),
            "summary": result.get("summary"),
            "issues": (result.get("issues") or [])[:50],
            "unverified": (result.get("unverified") or [])[:50],
        }
    if "rootCauses" in result and "audit" in result:
        return {
            "ok": result.get("ok"),
            "summary": result.get("summary"),
            "rootCauses": [
                {
                    "type": item.get("type"),
                    "severity": item.get("severity"),
                    "count": item.get("count"),
                    "source": item.get("source"),
                    "fix": item.get("fix"),
                    "examples": item.get("examples", [])[:2],
                }
                for item in result.get("rootCauses", [])
            ][:20],
        }
    if isinstance(result.get("value"), dict) and ("services" in result.get("value") or "iosCli" in result.get("value")):
        value = result.get("value") or {}
        return {
            "ok": result.get("ok"),
            "device": value.get("device"),
            "services": value.get("services"),
            "iosCli": value.get("iosCli"),
            "workspace": value.get("workspace"),
            "rawCounts": value.get("rawCounts"),
        }
    if "apply" in result and ("intentAudit" in result or "tests" in result):
        return {
            "ok": result.get("ok"),
            "summary": (result.get("apply") or {}).get("summary"),
            "steps": summarize_steps((result.get("apply") or {}).get("steps", [])),
            "audit": {
                "ok": (result.get("audit") or {}).get("ok"),
                "summary": (result.get("audit") or {}).get("summary"),
                "issues": ((result.get("audit") or {}).get("issues") or [])[:20],
            } if result.get("audit") else None,
            "intentAudit": {
                "ok": (result.get("intentAudit") or {}).get("ok"),
                "summary": (result.get("intentAudit") or {}).get("summary"),
                "issues": ((result.get("intentAudit") or {}).get("issues") or [])[:20],
                "unverified": ((result.get("intentAudit") or {}).get("unverified") or [])[:20],
            } if result.get("intentAudit") else None,
            "tests": {
                "ok": (result.get("tests") or {}).get("ok"),
                "summary": {
                    "tests": len((result.get("tests") or {}).get("tests") or []),
                    "failed": len((result.get("tests") or {}).get("failures") or []),
                },
                "failures": ((result.get("tests") or {}).get("failures") or [])[:20],
            } if result.get("tests") else None,
        }
    if "apply" in result and "audit" in result:
        apply_result = result.get("apply") or {}
        if not apply_result.get("ok") and apply_result.get("error"):
            return {
                "ok": result.get("ok"),
                "error": apply_result.get("error"),
                "hint": apply_result.get("hint"),
                "details": {
                    key: value
                    for key, value in apply_result.items()
                    if key not in ("ok", "error", "hint")
                },
                "audit": {
                    "ok": (result.get("audit") or {}).get("ok"),
                    "summary": (result.get("audit") or {}).get("summary"),
                    "issues": ((result.get("audit") or {}).get("issues") or [])[:20],
                },
            }
        return {
            "ok": result.get("ok"),
            "summary": apply_result.get("summary"),
            "steps": summarize_steps(apply_result.get("steps", [])),
            "audit": {
                "ok": (result.get("audit") or {}).get("ok"),
                "summary": (result.get("audit") or {}).get("summary"),
                "issues": ((result.get("audit") or {}).get("issues") or [])[:20],
            },
        }
    if "apply" in result:
        apply_result = result.get("apply") or {}
        if apply_result.get("dryRun"):
            return {
                "ok": result.get("ok"),
                "dryRun": True,
                "summary": apply_result.get("summary"),
                "operations": (apply_result.get("operations") or [])[:50],
            }
        return {
            "ok": result.get("ok"),
            "summary": apply_result.get("summary"),
            "steps": summarize_steps(apply_result.get("steps", [])),
        }
    if "apply" in result and "diagnose" in result:
        summary = {
            "ok": result.get("ok"),
            "summary": (result.get("apply") or {}).get("summary"),
            "steps": summarize_steps((result.get("apply") or {}).get("steps", [])),
            "issues": (result.get("diagnose") or {}).get("issues", []),
        }
        if "tests" in result:
            summary["tests"] = [summarize_test(test) for test in result.get("tests", [])]
        return summary
    if "steps" in result:
        return {
            "ok": result.get("ok"),
            "summary": result.get("summary"),
            "steps": summarize_steps(result.get("steps", [])),
        }
    if result.get("dryRun"):
        return {
            "ok": result.get("ok"),
            "dryRun": True,
            "summary": result.get("summary"),
            "operations": (result.get("operations") or [])[:50],
        }
    value = result.get("value")
    if isinstance(value, dict) and "moved" in value and "device" in value:
        return {
            "ok": result.get("ok"),
            "moved": value.get("moved"),
            "before": (value.get("before") or {}).get("logical"),
            "after": (value.get("device") or {}).get("logical"),
            "device": summarize_device(value.get("device")),
        }
    if isinstance(value, dict) and ("device" in value or "ports" in value):
        compact = dict(result)
        compact["value"] = summarize_device(value)
        return compact
    return result


def rpc_code(op, args_payload=None):
    return wrap_expression(
        "ptbDispatch(%s)"
        % js_string({"op": op, "args": args_payload or {}})
    )


def rpc_call(args, op, args_payload=None):
    result = submit_code(rpc_code(op, args_payload), args)
    if not result.get("ok"):
        return result
    value = result.get("value")
    if isinstance(value, dict) and "ok" in value:
        return value
    return {"ok": True, "value": value}


def run_file(args):
    with open(args.file, "r", encoding="utf-8") as handle:
        return print_output(args, submit_code(handle.read(), args))


def run_inline(args):
    return print_output(args, submit_code(args.code, args))


def add_device(args):
    return print_output(
        args,
        rpc_call(
            args,
            "ensureDevice",
            {"name": args.name, "model": args.model, "x": args.x, "y": args.y},
        ),
    )


def move_device_result(args, name, x, y, centered=True):
    code = wrap_expression(
        """(function(payload) {
    var device = ipc.network().getDevice(payload.name);
    function safe(target, methodName) {
        try {
            if (target && typeof target[methodName] == "function") {
                var value = target[methodName]();
                return value === undefined ? null : value;
            }
        } catch (error) {
            return null;
        }
        return null;
    }
    function info(current) {
        if (!current) {
            return null;
        }
        return {
            name: safe(current, "getName") || payload.name,
            model: safe(current, "getModel"),
            type: safe(current, "getType"),
            power: safe(current, "getPower"),
            logical: {
                x: safe(current, "getXCoordinate"),
                y: safe(current, "getYCoordinate"),
                centerX: safe(current, "getCenterXCoordinate"),
                centerY: safe(current, "getCenterYCoordinate")
            }
        };
    }
    if (!device) {
        return { ok: false, reason: "missing-device", name: payload.name };
    }
    var before = info(device);
    try {
        if (payload.centered && typeof device.moveToLocationCentered == "function") {
            device.moveToLocationCentered(Number(payload.x), Number(payload.y));
        } else if (typeof device.moveToLocation == "function") {
            device.moveToLocation(Number(payload.x), Number(payload.y));
        } else {
            return { ok: false, reason: "move-unavailable", name: payload.name, value: { before: before } };
        }
    } catch (error) {
        return {
            ok: false,
            reason: "move-failed",
            name: payload.name,
            error: String(error && error.message ? error.message : error),
            value: { before: before }
        };
    }
    var after = info(device);
    var beforeLogical = before && before.logical ? before.logical : {};
    var afterLogical = after && after.logical ? after.logical : {};
    var moved = beforeLogical.centerX !== afterLogical.centerX || beforeLogical.centerY !== afterLogical.centerY || beforeLogical.x !== afterLogical.x || beforeLogical.y !== afterLogical.y;
    return {
        ok: true,
        value: {
            name: payload.name,
            moved: moved,
            before: before,
            device: after
        }
    };
})(%s)"""
        % js_string({"name": name, "x": x, "y": y, "centered": centered})
    )
    result = submit_code(code, args)
    if not result.get("ok"):
        return result
    value = result.get("value")
    if isinstance(value, dict) and "ok" in value:
        return value
    return {"ok": True, "value": value}


def move_device(args):
    return print_output(args, move_device_result(args, args.name, args.x, args.y, not args.top_left))


def add_link(args):
    return print_output(
        args,
        rpc_call(
            args,
            "ensureLink",
            {
                "device1": args.device1,
                "interface1": args.interface1,
                "device2": args.device2,
                "interface2": args.interface2,
                "linkType": args.link_type,
            },
        ),
    )


def delete_link_result(args, device, port):
    code = wrap_expression(
        """(function(payload) {
    var device = ipc.network().getDevice(payload.device);
    if (!device) {
        return { ok: false, reason: "missing-device", device: payload.device };
    }
    var port = device.getPort(payload.port);
    if (!port) {
        return { ok: false, reason: "missing-port", device: payload.device, port: payload.port };
    }
    function summary() {
        var link = null;
        try { link = port.getLink(); } catch (error) { link = null; }
        return {
            device: payload.device,
            port: payload.port,
            hasLink: !!link,
            isPortUp: typeof port.isPortUp == "function" ? port.isPortUp() : null,
            isProtocolUp: typeof port.isProtocolUp == "function" ? port.isProtocolUp() : null
        };
    }
    var before = summary();
    if (!before.hasLink) {
        return { ok: true, deleted: false, before: before, after: before };
    }
    if (typeof port.deleteLink != "function") {
        return { ok: false, reason: "deleteLink-unavailable", before: before };
    }
    try {
        port.deleteLink();
    } catch (error) {
        return {
            ok: false,
            reason: "deleteLink-failed",
            error: String(error && error.message ? error.message : error),
            before: before
        };
    }
    return { ok: true, deleted: true, before: before, after: summary() };
})(%s)"""
        % js_string({"device": device, "port": port})
    )
    result = submit_code(code, args)
    if not result.get("ok"):
        return result
    value = result.get("value")
    if isinstance(value, dict) and "ok" in value:
        return value
    return {"ok": True, "value": value}


def remove_link(args):
    return print_output(args, delete_link_result(args, args.device, args.interface))


def delete_device_result(args, name):
    code = wrap_expression(
        """(function(payload) {
    var device = ipc.network().getDevice(payload.name);
    if (!device) {
        return { ok: true, deleted: false, reason: "missing-device", name: payload.name };
    }
    var workspace = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace();
    if (!workspace || typeof workspace.removeDevice != "function") {
        return { ok: false, deleted: false, reason: "removeDevice-unavailable", name: payload.name };
    }
    try {
        workspace.removeDevice(payload.name);
    } catch (error) {
        return {
            ok: false,
            deleted: false,
            reason: "removeDevice-failed",
            name: payload.name,
            error: String(error && error.message ? error.message : error)
        };
    }
    return {
        ok: true,
        deleted: !ipc.network().getDevice(payload.name),
        name: payload.name
    };
})(%s)"""
        % js_string({"name": name})
    )
    result = submit_code(code, args)
    if not result.get("ok"):
        return result
    value = result.get("value")
    if isinstance(value, dict) and "ok" in value:
        return value
    return {"ok": True, "value": value}


def remove_device(args):
    return print_output(args, delete_device_result(args, args.name))


def configure_pc(args):
    return print_output(
        args,
        rpc_call(
            args,
            "configurePc",
            {
                "name": args.name,
                "dhcp": args.dhcp,
                "ip": args.ip,
                "mask": args.mask,
                "gateway": args.gateway,
                "dns": args.dns,
            },
        ),
    )


def configure_ios(args):
    with open(args.file, "r", encoding="utf-8") as handle:
        commands = handle.read()
    return print_output(args, rpc_call(args, "configureIos", {"name": args.name, "commands": commands}))


def get_network(args):
    return print_output(args, rpc_call(args, "getNetwork"))


def get_device(args):
    return print_output(args, rpc_call(args, "inspectDevice", {"name": args.name}))


def pdu_list_script_path():
    return os.path.join(os.getcwd(), "tools", "ptbuilder_ui_pdu_list.ps1")


def read_pdu_list_result(timeout=20, max_rows=200, open_list=False):
    script = pdu_list_script_path()
    if not os.path.exists(script):
        return {"ok": False, "schema": "ptbuilder.pdu.list.v1", "error": "missing-pdu-list-script", "script": script}
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        script,
        "-TimeoutSeconds",
        str(max(1, int(timeout))),
        "-MaxRows",
        str(max(1, int(max_rows))),
    ]
    if open_list:
        command.append("-OpenList")
    try:
        completed = subprocess.run(
            command,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(5, int(timeout) + 10),
        )
    except subprocess.TimeoutExpired as error:
        return {
            "ok": False,
            "schema": "ptbuilder.pdu.list.v1",
            "error": "pdu-list-read-timeout",
            "script": script,
            "timeout": timeout,
            "stdout": error.stdout,
            "stderr": error.stderr,
        }

    try:
        payload = parse_jsonish(completed.stdout)
        if not isinstance(payload, dict):
            raise ValueError("PDU list did not return a JSON object")
    except Exception:
        payload = {
            "ok": False,
            "schema": "ptbuilder.pdu.list.v1",
            "error": "invalid-json",
            "stdout": completed.stdout,
        }
    payload.setdefault("schema", "ptbuilder.pdu.list.v1")
    payload["returnCode"] = completed.returncode
    payload["script"] = script
    if completed.stderr.strip():
        payload["stderr"] = completed.stderr.strip()
    if completed.returncode != 0 and payload.get("ok") is not False:
        payload["ok"] = False
    return payload


def pdu_list(args):
    return print_output(
        args,
        read_pdu_list_result(
            timeout=getattr(args, "timeout", 20),
            max_rows=getattr(args, "max_rows", 200),
            open_list=getattr(args, "open_list", False),
        ),
    )


def pdu_submit_succeeded(result):
    if not isinstance(result, dict) or not result.get("ok"):
        return False
    value = result.get("value")
    if isinstance(value, dict) and "ok" in value:
        return bool(value.get("ok"))
    return True


def pdu_submit_row_id(result):
    if not isinstance(result, dict):
        return None
    for key in ("id", "pduId", "rowId", "index"):
        if result.get(key) not in (None, ""):
            return str(result.get(key))
    value = result.get("value")
    if isinstance(value, dict):
        nested = pdu_submit_row_id(value)
        if nested is not None:
            return nested
    elif value not in (None, ""):
        return str(value)
    return None


def compact_pdu_row(row):
    if not isinstance(row, dict):
        return None
    keys = ("id", "status", "lastStatus", "source", "destination", "type", "timeSeconds", "periodic", "y")
    return {key: row.get(key) for key in keys if key in row}


def pdu_row_identity(row):
    if not isinstance(row, dict):
        return ""
    parts = [
        row.get("id"),
        row.get("source"),
        row.get("destination"),
        row.get("type"),
        row.get("lastStatus"),
        row.get("timeSeconds"),
        row.get("y"),
    ]
    return "\x1f".join("" if part is None else str(part) for part in parts)


def pdu_row_number(row):
    if not isinstance(row, dict):
        return None
    text = str(row.get("id") or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def pdu_row_y(row):
    if not isinstance(row, dict):
        return -1
    try:
        return int(float(row.get("y")))
    except Exception:
        return -1


def latest_pdu_row(rows):
    rows = [row for row in rows if isinstance(row, dict)]
    if not rows:
        return None
    return sorted(rows, key=lambda row: (pdu_row_number(row) if pdu_row_number(row) is not None else -1, pdu_row_y(row)))[-1]


def pdu_row_matches(row, source, destination, pdu_type="ICMP"):
    if not isinstance(row, dict):
        return False
    row_source = str(row.get("source") or "").strip().lower()
    row_destination = str(row.get("destination") or "").strip().lower()
    row_type = str(row.get("type") or "").strip().upper()
    return (
        row_source == str(source or "").strip().lower()
        and row_destination == str(destination or "").strip().lower()
        and (not pdu_type or row_type == str(pdu_type).strip().upper())
    )


def select_new_pdu_row(rows, before_rows, source, destination, pdu_type="ICMP"):
    rows = [row for row in (rows or []) if isinstance(row, dict)]
    before_rows = [row for row in (before_rows or []) if isinstance(row, dict)]
    matches = [row for row in rows if pdu_row_matches(row, source, destination, pdu_type)]
    if not matches:
        return None, []

    before_keys = {pdu_row_identity(row) for row in before_rows}
    before_numbers = [pdu_row_number(row) for row in before_rows]
    before_numbers = [number for number in before_numbers if number is not None]
    before_max_number = max(before_numbers) if before_numbers else None
    new_matches = []
    for row in matches:
        row_number = pdu_row_number(row)
        if row_number is not None and before_max_number is not None and row_number > before_max_number:
            new_matches.append(row)
        elif pdu_row_identity(row) not in before_keys:
            new_matches.append(row)

    if new_matches:
        return latest_pdu_row(new_matches), matches
    if not before_rows:
        return latest_pdu_row(matches), matches
    return None, matches


def select_pdu_row_by_id(rows, source, destination, row_id, pdu_type="ICMP"):
    if row_id in (None, ""):
        return None
    row_id = str(row_id).strip()
    matches = [
        row
        for row in (rows or [])
        if pdu_row_matches(row, source, destination, pdu_type) and str(row.get("id") or "").strip() == row_id
    ]
    return latest_pdu_row(matches)


def pdu_status_summary(result):
    row = result.get("row") if isinstance(result, dict) else None
    return {
        "source": result.get("source"),
        "destination": result.get("destination"),
        "submitted": result.get("submitted"),
        "submittedRowId": result.get("submittedRowId"),
        "matchedBy": result.get("matchedBy"),
        "status": result.get("status"),
        "id": row.get("id") if isinstance(row, dict) else None,
        "lastStatus": row.get("lastStatus") if isinstance(row, dict) else None,
        "polls": result.get("polls"),
        "error": result.get("error"),
    }


def send_pdu_and_wait_result(args, source, destination):
    result_timeout = getattr(args, "result_timeout", None)
    if result_timeout is None:
        result_timeout = getattr(args, "timeout", 20)
    result_timeout = max(0.1, float(result_timeout))
    poll_interval = max(0.1, float(getattr(args, "result_poll_interval", 0.5)))
    max_rows = max(1, int(getattr(args, "result_max_rows", 200)))

    before_probe = read_pdu_list_result(timeout=min(result_timeout, 8), max_rows=max_rows, open_list=True)
    before_rows = before_probe.get("rows") if before_probe.get("ok") else []
    before_rows = before_rows if isinstance(before_rows, list) else []

    submit_result = rpc_call(args, "sendPdu", {"source": source, "destination": destination})
    submitted = pdu_submit_succeeded(submit_result)
    submitted_row_id = pdu_submit_row_id(submit_result)
    result = {
        "ok": False,
        "schema": "ptbuilder.pdu.ping.v1",
        "source": source,
        "destination": destination,
        "submitted": submitted,
        "submittedRowId": submitted_row_id,
        "submit": submit_result,
        "status": "unknown",
        "row": None,
        "polls": 0,
        "pduListBefore": {"ok": before_probe.get("ok"), "summary": before_probe.get("summary"), "error": before_probe.get("error")},
    }
    if not submitted:
        result["error"] = submit_result.get("error") or "pdu-submit-failed"
        result["summary"] = pdu_status_summary(result)
        return result

    deadline = time.monotonic() + result_timeout
    last_probe = None
    best_row = None
    matches = []
    while True:
        remaining = max(0.1, deadline - time.monotonic())
        probe = read_pdu_list_result(timeout=min(remaining, 5), max_rows=max_rows, open_list=True)
        result["polls"] += 1
        last_probe = probe
        if probe.get("ok"):
            rows = probe.get("rows") if isinstance(probe.get("rows"), list) else []
            candidate = select_pdu_row_by_id(rows, source, destination, submitted_row_id, "ICMP")
            matched_by = "submitted-id" if candidate else None
            if not candidate:
                candidate, matches = select_new_pdu_row(rows, before_rows, source, destination, "ICMP")
                matched_by = "new-row" if candidate else None
            elif not matches:
                matches = [row for row in rows if pdu_row_matches(row, source, destination, "ICMP")]
            if candidate:
                best_row = candidate
                status = str(candidate.get("status") or "unknown").strip().lower() or "unknown"
                result["row"] = compact_pdu_row(candidate)
                result["status"] = status
                result["matchedBy"] = matched_by
                result["matches"] = [compact_pdu_row(row) for row in matches[-10:]]
                result["pduListAfter"] = {"ok": True, "summary": probe.get("summary")}
                if status in ("success", "failed"):
                    result["ok"] = status == "success"
                    result["summary"] = pdu_status_summary(result)
                    return result
        else:
            result["lastPduListError"] = probe.get("error")

        if time.monotonic() >= deadline:
            break
        time.sleep(min(poll_interval, max(0.1, deadline - time.monotonic())))

    if best_row:
        result["row"] = compact_pdu_row(best_row)
        result["status"] = str(best_row.get("status") or "unknown").strip().lower() or "unknown"
    elif matches:
        stale = latest_pdu_row(matches)
        result["staleMatch"] = compact_pdu_row(stale)
    result["pduListAfter"] = {
        "ok": last_probe.get("ok") if isinstance(last_probe, dict) else None,
        "summary": last_probe.get("summary") if isinstance(last_probe, dict) else None,
        "error": last_probe.get("error") if isinstance(last_probe, dict) else None,
    }
    result["error"] = "pdu-result-timeout"
    result["summary"] = pdu_status_summary(result)
    return result


def ping(args):
    if getattr(args, "wait_result", False):
        return print_output(args, send_pdu_and_wait_result(args, args.source, args.destination))
    return print_output(args, rpc_call(args, "sendPdu", {"source": args.source, "destination": args.destination}))


def test_matrix(args):
    if getattr(args, "file", None):
        snapshot_doc = load_json_file(args.file)
        snapshot = snapshot_doc.get("snapshot", snapshot_doc)
    else:
        snapshot = snapshot_value(args)
    devices = [device for device in snapshot.get("devices", []) if isinstance(device, dict)]
    source_names = []
    explicit = []
    if args.sources:
        explicit = [item.strip() for item in args.sources.split(",") if item.strip()]
    source_subnet = ipaddress.ip_network(args.source_subnet, strict=False) if args.source_subnet else None
    for device in devices:
        name = device.get("name")
        if not is_terminal_device(device):
            continue
        if explicit and name not in explicit:
            continue
        if args.source_prefix and not str(name).startswith(args.source_prefix):
            continue
        if source_subnet:
            port = first_usable_terminal_port(device)
            if not port or valid_ip_network(port.get("ip"), port.get("subnetMask")) != source_subnet:
                continue
        source_names.append(name)

    tests = []
    failures = []
    for source in sorted(source_names):
        item = {"source": source, "destination": args.destination, "ok": False}
        if args.mode == "same-subnet":
            access = subnet_access_result(device_by_name(snapshot), source, args.destination)
            item["ok"] = bool(access.get("ok") and access.get("allowed"))
            item["result"] = access
            item["_known_result"] = True
        elif args.mode == "pdu-result":
            pdu_result = send_pdu_and_wait_result(args, source, args.destination)
            item["ok"] = bool(pdu_result.get("ok"))
            item["status"] = pdu_result.get("status")
            item["row"] = pdu_result.get("row")
            item["result"] = pdu_result
            item["_known_result"] = pdu_result.get("status") in ("success", "failed")
        else:
            pdu_result = rpc_call(args, "sendPdu", {"source": source, "destination": args.destination})
            value = pdu_result.get("value")
            inner_ok = True
            if isinstance(value, dict) and "ok" in value:
                inner_ok = bool(value.get("ok"))
            item["ok"] = bool(pdu_result.get("ok") and inner_ok)
            item["result"] = pdu_result
            item["_known_result"] = True
        if args.expect == "deny":
            item["ok"] = (not item["ok"]) if item.get("_known_result", True) else False
        item.pop("_known_result", None)
        if item["ok"] or args.show_passed:
            tests.append(item)
        if not item["ok"]:
            failures.append(item)

    result = {
        "ok": len(failures) == 0,
        "schema": "ptbuilder.connectivity.matrix.v1",
        "summary": {
            "sources": len(source_names),
            "tested": len(source_names),
            "failed": len(failures),
            "shown": len(tests),
            "mode": args.mode,
            "expect": args.expect,
        },
        "tests": tests,
        "failures": failures,
    }
    return print_output(args, result)


def load_json_file(path):
    with open(path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def apply_lab(args):
    return print_output(args, apply_lab_result(args))


def plan_summary(spec):
    return {
        "devices": len(spec.get("devices") or []),
        "moveDevices": len(spec.get("moveDevices") or []),
        "removeDevices": len(spec.get("removeDevices") or []),
        "pcConfigs": len(spec.get("pcConfigs") or {}),
        "iosConfigs": len(spec.get("iosConfigs") or {}),
        "removeLinks": len(spec.get("removeLinks") or []),
        "links": len(spec.get("links") or []),
    }


def dry_run_plan(args, spec):
    operations = []
    for device in spec.get("devices", []):
        operations.append({"kind": "ensure-device", "name": device.get("name"), "model": device.get("model")})
        if getattr(args, "relayout", False):
            operations.append({"kind": "move-device", "name": device.get("name"), "x": device.get("x"), "y": device.get("y"), "source": "devices"})
    for item in spec.get("moveDevices", []):
        operations.append({"kind": "move-device", "name": item.get("name"), "x": item.get("x"), "y": item.get("y"), "source": "moveDevices"})
    for device in spec.get("removeDevices", []):
        operations.append({"kind": "remove-device", "name": device.get("name") if isinstance(device, dict) else device})
    for name, config in (spec.get("pcConfigs") or {}).items():
        operations.append({"kind": "configure-pc", "name": name, "ip": config.get("ip"), "mask": config.get("mask") or config.get("subnetMask"), "gateway": config.get("gateway")})
    for name in (spec.get("iosConfigs") or {}).keys():
        operations.append({"kind": "configure-ios", "name": name})
    for link in spec.get("removeLinks", []):
        if isinstance(link, dict):
            operations.append({"kind": "remove-link", "device": link.get("device") or link.get("device1"), "port": link.get("interface") or link.get("port") or link.get("interface1")})
        else:
            operations.append({"kind": "remove-link", "device": link[0] if len(link) > 0 else None, "port": link[1] if len(link) > 1 else None})
    for link in spec.get("links", []):
        operations.append({"kind": "ensure-link", "link": link})
    summary = plan_summary(spec)
    if getattr(args, "relayout", False):
        summary["moveDevices"] += len(spec.get("devices") or [])
    return {"ok": True, "dryRun": True, "summary": summary, "operations": operations}


def validate_plan_safety(args, spec):
    if getattr(args, "force", False):
        return None
    remove_devices = spec.get("removeDevices") or []
    if len(remove_devices) > getattr(args, "max_delete", 3):
        return {
            "ok": False,
            "error": "refusing-large-device-delete",
            "count": len(remove_devices),
            "maxDelete": getattr(args, "max_delete", 3),
            "hint": "Re-run with --force if this deletion set is intentional.",
        }
    if remove_devices:
        try:
            snapshot = snapshot_value(args)
            devices = device_by_name(snapshot)
            risky = []
            for item in remove_devices:
                name = item.get("name") if isinstance(item, dict) else item
                device = devices.get(name)
                if device and not is_terminal_device(device):
                    risky.append({"name": name, "model": device.get("model"), "type": device.get("type")})
            if risky:
                return {
                    "ok": False,
                    "error": "refusing-network-device-delete",
                    "devices": risky,
                    "hint": "Deleting switches/routers/servers can disconnect many nodes. Re-run with --force if intentional.",
                }
        except Exception:
            return None
    return None


def apply_lab_result(args):
    spec = load_json_file(args.file)
    if getattr(args, "dry_run", False):
        return dry_run_plan(args, spec)
    safety_error = validate_plan_safety(args, spec)
    if safety_error:
        return safety_error
    result = {
        "ok": True,
        "steps": [],
        "summary": {
            "devices": 0,
            "moveDevices": 0,
            "removeDevices": 0,
            "pcConfigs": 0,
            "iosConfigs": 0,
            "removeLinks": 0,
            "links": 0,
        },
    }

    def add_step(kind, name, step_result):
        step = {"kind": kind, "name": name, "result": step_result}
        result["steps"].append(step)
        if not step_result.get("ok"):
            result["ok"] = False

    for device in spec.get("devices", []):
        name = device.get("name")
        step_result = rpc_call(
            args,
            "ensureDevice",
            {
                "name": name,
                "model": device.get("model"),
                "x": device.get("x", 100),
                "y": device.get("y", 100),
            },
        )
        add_step("ensure-device", name, step_result)
        result["summary"]["devices"] += 1
        if not step_result.get("ok"):
            return result
        if getattr(args, "relayout", False):
            step_result = move_device_result(args, name, device.get("x", 100), device.get("y", 100), True)
            add_step("move-device", name, step_result)
            result["summary"]["moveDevices"] += 1
            if not step_result.get("ok"):
                return result

    for item in spec.get("moveDevices", []):
        name = item.get("name")
        step_result = move_device_result(args, name, item.get("x", 100), item.get("y", 100), not bool(item.get("topLeft")))
        add_step("move-device", name, step_result)
        result["summary"]["moveDevices"] += 1
        if not step_result.get("ok"):
            return result

    for name, config in (spec.get("pcConfigs") or {}).items():
        payload = dict(config)
        payload["name"] = name
        step_result = rpc_call(args, "configurePc", payload)
        add_step("configure-pc", name, step_result)
        result["summary"]["pcConfigs"] += 1
        if not step_result.get("ok"):
            return result

    for name, commands in (spec.get("iosConfigs") or {}).items():
        step_result = rpc_call(args, "configureIos", {"name": name, "commands": commands})
        add_step("configure-ios", name, step_result)
        result["summary"]["iosConfigs"] += 1
        if not step_result.get("ok"):
            return result

    for index, device in enumerate(spec.get("removeDevices", [])):
        device_name = device.get("name") if isinstance(device, dict) else device
        step_result = delete_device_result(args, device_name)
        add_step("remove-device", str(index), step_result)
        result["summary"]["removeDevices"] += 1
        if not step_result.get("ok"):
            return result

    for index, link in enumerate(spec.get("removeLinks", [])):
        if isinstance(link, dict):
            device_name = link.get("device") or link.get("device1")
            interface_name = link.get("interface") or link.get("port") or link.get("interface1")
        else:
            device_name = link[0] if len(link) > 0 else None
            interface_name = link[1] if len(link) > 1 else None
        step_result = delete_link_result(args, device_name, interface_name)
        add_step("remove-link", str(index), step_result)
        result["summary"]["removeLinks"] += 1
        if not step_result.get("ok"):
            return result

    for index, link in enumerate(spec.get("links", [])):
        step_result = rpc_call(
            args,
            "ensureLink",
            {
                "device1": link[0],
                "interface1": link[1],
                "device2": link[2],
                "interface2": link[3],
                "linkType": link[4] if len(link) > 4 else "straight",
            },
        )
        add_step("ensure-link", str(index), step_result)
        result["summary"]["links"] += 1
        if not step_result.get("ok"):
            return result

    return result


def network_value(args):
    result = rpc_call(args, "getNetwork")
    if not result.get("ok"):
        raise RuntimeError(result)
    return result.get("value") or {}


SNAPSHOT_CODE = r"""
function ptbSafe(target, methodName) {
    try {
        if (target && typeof target[methodName] == "function") {
            var value = target[methodName]();
            return value === undefined ? null : value;
        }
    } catch (error) {
        return null;
    }
    return null;
}

function ptbPortInfo(port) {
    if (!port) {
        return null;
    }
    return {
        name: ptbSafe(port, "getName"),
        uuid: ptbSafe(port, "getObjectUuid"),
        ip: ptbSafe(port, "getIpAddress"),
        subnetMask: ptbSafe(port, "getSubnetMask"),
        mac: ptbSafe(port, "getMacAddress"),
        description: ptbSafe(port, "getDescription"),
        type: ptbSafe(port, "getType"),
        isEthernet: ptbSafe(port, "isEthernetPort"),
        isWireless: ptbSafe(port, "isWirelessPort"),
        isPortUp: ptbSafe(port, "isPortUp"),
        isProtocolUp: ptbSafe(port, "isProtocolUp"),
        isPowerOn: ptbSafe(port, "isPowerOn"),
        remotePortName: ptbSafe(port, "getRemotePortName")
    };
}

function ptbEndpoint(port) {
    if (!port) {
        return null;
    }
    var owner = null;
    try {
        owner = port.getOwnerDevice();
    } catch (error) {
        owner = null;
    }
    var info = ptbPortInfo(port);
    return {
        device: owner && typeof owner.getName == "function" ? owner.getName() : null,
        port: info ? info.name : null,
        portUuid: info ? info.uuid : null,
        ip: info ? info.ip : null,
        subnetMask: info ? info.subnetMask : null,
        isPortUp: info ? info.isPortUp : null,
        isProtocolUp: info ? info.isProtocolUp : null
    };
}

function ptbDeviceInfo(device) {
    if (!device) {
        return null;
    }
    var ports = [];
    var portCount = ptbSafe(device, "getPortCount");
    if (typeof portCount == "number") {
        for (var i = 0; i < portCount; i++) {
            try {
                ports.push(ptbPortInfo(device.getPortAt(i)));
            } catch (error) {
                ports.push({ index: i, error: String(error && error.message ? error.message : error) });
            }
        }
    }
    return {
        name: ptbSafe(device, "getName"),
        uuid: ptbSafe(device, "getObjectUuid"),
        model: ptbSafe(device, "getModel"),
        type: ptbSafe(device, "getType"),
        power: ptbSafe(device, "getPower"),
        dhcp: ptbSafe(device, "getDhcpFlag"),
        gateway: ptbSafe(device, "getDefaultGateway"),
        logical: {
            x: ptbSafe(device, "getXCoordinate"),
            y: ptbSafe(device, "getYCoordinate"),
            centerX: ptbSafe(device, "getCenterXCoordinate"),
            centerY: ptbSafe(device, "getCenterYCoordinate")
        },
        ports: ports
    };
}

function ptbLinksInfo() {
    var links = [];
    var count = ptbSafe(ipc.network(), "getLinkCount");
    if (typeof count != "number") {
        return links;
    }
    for (var i = 0; i < count; i++) {
        var link = null;
        try {
            link = ipc.network().getLinkAt(i);
        } catch (error) {
            links.push({ index: i, error: String(error && error.message ? error.message : error) });
            continue;
        }
        var endpoints = [];
        try {
            if (link && typeof link.getPort1 == "function") {
                endpoints.push(ptbEndpoint(link.getPort1()));
            }
            if (link && typeof link.getPort2 == "function") {
                endpoints.push(ptbEndpoint(link.getPort2()));
            }
        } catch (error) {
            endpoints.push({ error: String(error && error.message ? error.message : error) });
        }
        links.push({
            index: i,
            uuid: ptbSafe(link, "getObjectUuid"),
            className: ptbSafe(link, "getClassName"),
            connectionType: ptbSafe(link, "getConnectionType"),
            endpoints: endpoints
        });
    }
    return links;
}

var devices = [];
var deviceCount = ptbSafe(ipc.network(), "getDeviceCount");
if (typeof deviceCount == "number") {
    for (var d = 0; d < deviceCount; d++) {
        try {
            devices.push(ptbDeviceInfo(ipc.network().getDeviceAt(d)));
        } catch (error) {
            devices.push({ index: d, error: String(error && error.message ? error.message : error) });
        }
    }
}
return {
    schema: "ptbuilder.snapshot.v1",
    deviceCount: typeof deviceCount == "number" ? deviceCount : devices.length,
    devices: devices,
    links: ptbLinksInfo()
};
"""


def snapshot_value(args):
    result = submit_code(SNAPSHOT_CODE, args)
    if not result.get("ok"):
        raise RuntimeError(result)
    return result.get("value") or {}


def iter_ports(device):
    ports = device.get("ports") or []
    return ports if isinstance(ports, list) else []


def port_key(device_name, port_name):
    return f"{device_name}::{port_name}"


def parse_port_name(port_name):
    text = str(port_name or "")
    prefix = text.rstrip("0123456789")
    suffix = text[len(prefix):]
    if suffix == "":
        return text, None
    try:
        return prefix, int(suffix)
    except Exception:
        return text, None


def expand_port_range(value):
    if not value:
        return []
    text = str(value)
    if "-" not in text:
        return [text]
    start, end = text.split("-", 1)
    start_prefix, start_number = parse_port_name(start)
    end_prefix, end_number = parse_port_name(end)
    if end_prefix == "":
        end_prefix = start_prefix
    if start_number is None or end_number is None or start_prefix != end_prefix or end_number < start_number:
        return [text]
    return [f"{start_prefix}{number}" for number in range(start_number, end_number + 1)]


def is_terminal_device(device):
    model = str(device.get("model") or "").lower()
    return any(token in model for token in ("pc-pt", "laptop", "server-pt", "printer"))


def valid_ip_network(ip_value, mask_value):
    if not ip_value or not mask_value or ip_value in ("0.0.0.0", "255.255.255.255"):
        return None
    try:
        return ipaddress.ip_network(f"{ip_value}/{mask_value}", strict=False)
    except Exception:
        return None


def is_empty_value(value):
    return value in (None, "", "0.0.0.0", "null", "None")


def number_or_none(value):
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def device_logical_point(device):
    logical = device.get("logical") if isinstance(device.get("logical"), dict) else {}
    x = number_or_none(logical.get("centerX"))
    y = number_or_none(logical.get("centerY"))
    if x is None:
        x = number_or_none(logical.get("x"))
    if y is None:
        y = number_or_none(logical.get("y"))
    if x is None:
        x = number_or_none(device.get("centerX") or device.get("x"))
    if y is None:
        y = number_or_none(device.get("centerY") or device.get("y"))
    if x is None or y is None:
        return None
    return {"x": x, "y": y}


def layout_proximity_issues(devices, min_distance=60, limit=50):
    points = []
    for device in devices:
        point = device_logical_point(device)
        if point:
            points.append({"name": device.get("name"), "model": device.get("model"), "x": point["x"], "y": point["y"]})

    issues = []
    total = 0
    for left_index, left in enumerate(points):
        for right in points[left_index + 1:]:
            dx = left["x"] - right["x"]
            dy = left["y"] - right["y"]
            distance = (dx * dx + dy * dy) ** 0.5
            if distance >= min_distance:
                continue
            total += 1
            if len(issues) < limit:
                issues.append(
                    {
                        "severity": "warn",
                        "type": "layout-devices-too-close",
                        "devices": [left["name"], right["name"]],
                        "distance": round(distance, 1),
                        "fix": "run-apply-or-company-deploy-with-relayout-or-use-move-device",
                    }
                )
    if total > len(issues):
        issues.append(
            {
                "severity": "warn",
                "type": "layout-devices-too-close-summary",
                "count": total,
                "shown": len(issues),
                "fix": "run-apply-or-company-deploy-with-relayout-or-use-move-device",
            }
        )
    return issues


def audit_snapshot(snapshot, subnet_filter=None):
    devices = [device for device in snapshot.get("devices", []) if isinstance(device, dict)]
    links = [link for link in snapshot.get("links", []) if isinstance(link, dict)]
    issues = []
    connected_ports = set()
    issues.extend(layout_proximity_issues(devices))

    for link in links:
        if link.get("className") in ("Antenna",):
            continue
        endpoints = [endpoint for endpoint in link.get("endpoints", []) if isinstance(endpoint, dict)]
        if len(endpoints) != 2 or any(not endpoint.get("device") or not endpoint.get("port") for endpoint in endpoints):
            issues.append(
                {
                    "severity": "warn",
                    "type": "link-endpoint-unresolved",
                    "link": link.get("index"),
                    "uuid": link.get("uuid"),
                    "fix": "inspect-cable-or-redraw-link",
                }
            )
        for endpoint in endpoints:
            if endpoint.get("device") and endpoint.get("port"):
                connected_ports.add(port_key(endpoint.get("device"), endpoint.get("port")))
            if endpoint.get("isPortUp") is False or endpoint.get("isProtocolUp") is False:
                issues.append(
                    {
                        "severity": "error",
                        "type": "linked-port-down",
                        "device": endpoint.get("device"),
                        "port": endpoint.get("port"),
                        "portUp": endpoint.get("isPortUp"),
                        "protocolUp": endpoint.get("isProtocolUp"),
                        "fix": "check-cable-port-power-or-interface-config",
                    }
                )

    ip_index = {}
    subnets = {}
    for device in devices:
        device_name = device.get("name")
        if device.get("power") is False:
            issues.append({"severity": "error", "type": "device-power-off", "device": device_name, "fix": "power-on-device"})
        terminal = is_terminal_device(device)
        for port in iter_ports(device):
            port_name = port.get("name")
            ip_value = port.get("ip")
            mask_value = port.get("subnetMask")
            network = valid_ip_network(ip_value, mask_value)
            if network:
                key = str(network)
                subnets.setdefault(key, {"network": key, "devices": []})
                subnets[key]["devices"].append(
                    {
                        "device": device_name,
                        "port": port_name,
                        "ip": ip_value,
                        "mask": mask_value,
                        "up": port.get("isPortUp"),
                        "protocol": port.get("isProtocolUp"),
                    }
                )
                ip_index.setdefault(ip_value, []).append({"device": device_name, "port": port_name})
            elif terminal and port_name == "FastEthernet0":
                issues.append(
                    {
                        "severity": "warn",
                        "type": "terminal-ip-missing",
                        "device": device_name,
                        "port": port_name,
                        "ip": ip_value,
                        "mask": mask_value,
                        "fix": "configure-pc",
                    }
                )

            if terminal and port_name == "FastEthernet0" and port_key(device_name, port_name) not in connected_ports:
                issues.append(
                    {
                        "severity": "error",
                        "type": "terminal-link-missing",
                        "device": device_name,
                        "port": port_name,
                        "ip": ip_value,
                        "mask": mask_value,
                        "fix": "connect-terminal-port",
                    }
                )

            if port_key(device_name, port_name) in connected_ports:
                if port.get("isPortUp") is False or port.get("isProtocolUp") is False:
                    issues.append(
                        {
                            "severity": "error",
                            "type": "connected-port-down",
                            "device": device_name,
                            "port": port_name,
                            "portUp": port.get("isPortUp"),
                            "protocolUp": port.get("isProtocolUp"),
                            "fix": "check-cable-port-power-or-interface-config",
                        }
                    )

    for ip_value, owners in ip_index.items():
        if len(owners) > 1:
            issues.append({"severity": "error", "type": "duplicate-ip", "ip": ip_value, "owners": owners, "fix": "assign-unique-ip"})

    if subnet_filter:
        wanted = ipaddress.ip_network(subnet_filter, strict=False)
        filtered = {}
        for key, subnet in subnets.items():
            current = ipaddress.ip_network(key, strict=False)
            if current == wanted:
                filtered[key] = subnet
        subnets = filtered

    return {
        "ok": not any(issue.get("severity") == "error" for issue in issues),
        "summary": {
            "devices": len(devices),
            "links": len(links),
            "subnets": len(subnets),
            "issues": len(issues),
            "errors": len([issue for issue in issues if issue.get("severity") == "error"]),
            "warnings": len([issue for issue in issues if issue.get("severity") == "warn"]),
        },
        "subnets": list(subnets.values()),
        "issues": issues,
    }


def export_current(args):
    snapshot = snapshot_value(args)
    audit = audit_snapshot(snapshot, getattr(args, "subnet", None))
    payload = {"ok": True, "snapshot": snapshot, "audit": audit}
    if args.file:
        with open(args.file, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    return print_output(args, payload)


def audit_current(args):
    snapshot = load_json_file(args.file).get("snapshot") if args.file else snapshot_value(args)
    if not snapshot:
        raise RuntimeError("Missing snapshot data")
    return print_output(args, audit_snapshot(snapshot, args.subnet))


def snapshot_from_document(document):
    if not isinstance(document, dict):
        return None
    snapshot = document.get("snapshot")
    if isinstance(snapshot, dict):
        return snapshot
    if isinstance(document.get("devices"), list):
        return document
    return None


def snapshot_from_args(args, file_attr="file"):
    path = getattr(args, file_attr, None)
    snapshot = snapshot_from_document(load_json_file(path)) if path else snapshot_value(args)
    if not snapshot:
        raise RuntimeError("Missing snapshot data")
    return snapshot


def layout_int_arg(args, name, default, minimum=None):
    try:
        value = int(getattr(args, name, default))
    except Exception:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def device_ip_networks(device):
    networks = []
    seen = set()
    for port in iter_ports(device):
        network = valid_ip_network(port.get("ip"), port.get("subnetMask"))
        if network and str(network) not in seen:
            networks.append(network)
            seen.add(str(network))
    return networks


def device_has_ip_in_subnet(device, subnet):
    if subnet is None:
        return True
    for port in iter_ports(device):
        ip_value = port.get("ip")
        if is_empty_value(ip_value):
            continue
        try:
            if ipaddress.ip_address(ip_value) in subnet:
                return True
        except Exception:
            continue
    return False


def primary_device_network(device):
    if is_terminal_device(device):
        primary = first_usable_terminal_port(device)
        if primary:
            network = valid_ip_network(primary.get("ip"), primary.get("subnetMask"))
            if network:
                return network
    networks = device_ip_networks(device)
    return networks[0] if networks else None


def inferred_name_group(name):
    text = str(name or "").strip()
    if not text:
        return "ungrouped"
    parts = text.split("_")
    if len(parts) > 1:
        tail = parts[-1].lower()
        if any(tail.startswith(token) for token in ("pc", "laptop", "server", "srv", "printer", "host", "client")):
            return "_".join(parts[:-1]) or text
    stripped = text.rstrip("0123456789").rstrip("_-")
    return stripped or text


def is_layout_network_device(device):
    if not isinstance(device, dict) or is_terminal_device(device):
        return False
    model = str(device.get("model") or "").lower()
    if "power distribution" in model:
        return False
    if iter_ports(device):
        return True
    return any(token in model for token in ("switch", "router", "ap", "wireless", "2960", "1941", "2811"))


def layout_filter_matches(device, args, target_subnet=None):
    name = str(device.get("name") or "")
    name_prefix = getattr(args, "name_prefix", None)
    if name_prefix and not name.startswith(str(name_prefix)):
        return False
    if target_subnet and not device_has_ip_in_subnet(device, target_subnet):
        return False
    return True


def first_network_neighbor_name(device_name, neighbors, devices_by_name):
    for neighbor in neighbors.get(device_name, []):
        neighbor_name = neighbor.get("device")
        neighbor_device = devices_by_name.get(neighbor_name)
        if is_layout_network_device(neighbor_device):
            return neighbor_name
    return None


def layout_group_for_device(device, neighbors, devices_by_name):
    network = primary_device_network(device)
    if network:
        return f"subnet:{network}", str(network), (0, int(network.network_address), network.prefixlen)
    neighbor_name = first_network_neighbor_name(device.get("name"), neighbors, devices_by_name)
    if neighbor_name:
        return f"neighbor:{neighbor_name}", f"attached-to-{neighbor_name}", (1, str(neighbor_name))
    group = inferred_name_group(device.get("name"))
    return f"name:{group}", group, (2, str(group))


def ensure_layout_group(groups, key, label, sort_key, network=None):
    group = groups.get(key)
    if not group:
        group = {
            "key": key,
            "label": label,
            "sortKey": sort_key,
            "network": str(network) if network else None,
            "terminals": [],
            "networkDevices": [],
        }
        groups[key] = group
    return group


def planned_min_distance(points):
    if len(points) < 2:
        return None, None
    best_distance = None
    best_pair = None
    for left_index, left in enumerate(points):
        for right in points[left_index + 1:]:
            dx = left["x"] - right["x"]
            dy = left["y"] - right["y"]
            distance = (dx * dx + dy * dy) ** 0.5
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_pair = [left["name"], right["name"]]
    return best_distance, best_pair


def build_layout_plan(snapshot, args, generated_by="ptbuilder layout-plan"):
    origin_x = layout_int_arg(args, "origin_x", 80)
    origin_y = layout_int_arg(args, "origin_y", 120)
    group_width = layout_int_arg(args, "group_width", 620, 1)
    group_height = layout_int_arg(args, "group_height", 520, 1)
    columns = layout_int_arg(args, "columns", 3, 1)
    device_columns = layout_int_arg(args, "device_columns", 5, 1)
    min_distance = layout_int_arg(args, "min_distance", 70, 1)
    x_spacing = max(layout_int_arg(args, "x_spacing", 95, 1), min_distance)
    y_spacing = max(layout_int_arg(args, "y_spacing", 85, 1), min_distance)
    include_network_devices = bool(getattr(args, "include_network_devices", False))

    subnet_text = getattr(args, "subnet", None)
    target_subnet = ipaddress.ip_network(subnet_text, strict=False) if subnet_text else None
    devices = [device for device in snapshot.get("devices", []) if isinstance(device, dict) and device.get("name")]
    devices_by_name = device_by_name(snapshot)
    neighbors = link_neighbors(snapshot)
    groups = {}
    terminal_group_keys = {}

    for device in sorted(devices, key=lambda item: str(item.get("name") or "")):
        if not is_terminal_device(device):
            continue
        if not layout_filter_matches(device, args, target_subnet):
            continue
        key, label, sort_key = layout_group_for_device(device, neighbors, devices_by_name)
        group = ensure_layout_group(groups, key, label, sort_key, primary_device_network(device))
        group["terminals"].append(device)
        terminal_group_keys[device.get("name")] = key

    assigned_network_devices = set()
    if include_network_devices:
        for terminal_name, group_key in terminal_group_keys.items():
            group = groups.get(group_key)
            if not group:
                continue
            for neighbor in neighbors.get(terminal_name, []):
                neighbor_name = neighbor.get("device")
                neighbor_device = devices_by_name.get(neighbor_name)
                if neighbor_name in assigned_network_devices or not is_layout_network_device(neighbor_device):
                    continue
                name_prefix = getattr(args, "name_prefix", None)
                if name_prefix and not str(neighbor_name or "").startswith(str(name_prefix)):
                    continue
                group["networkDevices"].append(neighbor_device)
                assigned_network_devices.add(neighbor_name)

        if getattr(args, "name_prefix", None) or target_subnet:
            for device in sorted(devices, key=lambda item: str(item.get("name") or "")):
                name = device.get("name")
                if name in assigned_network_devices or not is_layout_network_device(device):
                    continue
                if not layout_filter_matches(device, args, target_subnet):
                    continue
                key, label, sort_key = layout_group_for_device(device, neighbors, devices_by_name)
                group = ensure_layout_group(groups, key, label, sort_key, primary_device_network(device))
                group["networkDevices"].append(device)
                assigned_network_devices.add(name)

    move_devices = []
    warnings = []
    group_summaries = []
    planned_points = []
    moved_names = set()
    ordered_groups = sorted(groups.values(), key=lambda item: item.get("sortKey"))

    def add_move(device, x, y, group, role):
        name = device.get("name")
        if not name or name in moved_names:
            return
        x_value = int(round(x))
        y_value = int(round(y))
        moved_names.add(name)
        move_devices.append({"name": name, "x": x_value, "y": y_value, "group": group.get("label"), "role": role})
        planned_points.append({"name": name, "x": x_value, "y": y_value})

    for group_index, group in enumerate(ordered_groups):
        group_x = origin_x + (group_index % columns) * group_width
        group_y = origin_y + (group_index // columns) * group_height
        network_devices = sorted(group.get("networkDevices") or [], key=lambda item: str(item.get("name") or ""))
        terminals = sorted(group.get("terminals") or [], key=lambda item: str(item.get("name") or ""))

        for index, device in enumerate(network_devices):
            column = index % device_columns
            row = index // device_columns
            add_move(device, group_x + column * x_spacing, group_y + row * y_spacing, group, "network")

        network_rows = (len(network_devices) + device_columns - 1) // device_columns if network_devices else 0
        terminal_base_y = group_y + 140 + max(0, network_rows - 1) * y_spacing
        for index, device in enumerate(terminals):
            column = index % device_columns
            row = index // device_columns
            add_move(device, group_x + column * x_spacing, terminal_base_y + row * y_spacing, group, "terminal")

        terminal_rows = (len(terminals) + device_columns - 1) // device_columns if terminals else 0
        needed_height = (terminal_base_y - group_y) + max(0, terminal_rows - 1) * y_spacing
        if needed_height > group_height:
            warnings.append(
                {
                    "type": "layout-group-overflow",
                    "group": group.get("label"),
                    "neededHeight": needed_height,
                    "groupHeight": group_height,
                    "hint": "Increase --group-height, --device-columns, or --columns for this topology.",
                }
            )
        group_summaries.append(
            {
                "group": group.get("label"),
                "network": group.get("network"),
                "terminals": len(terminals),
                "networkDevices": len(network_devices),
                "moves": len(terminals) + len(network_devices),
                "origin": {"x": group_x, "y": group_y},
            }
        )

    minimum_distance, closest_pair = planned_min_distance(planned_points)
    if minimum_distance is not None and minimum_distance < min_distance:
        warnings.append(
            {
                "type": "layout-targets-too-close",
                "distance": round(minimum_distance, 1),
                "devices": closest_pair,
                "minDistance": min_distance,
            }
        )
    if not move_devices:
        warnings.append({"type": "layout-no-matching-devices", "hint": "No terminal devices matched the selected prefix/subnet filters."})

    summary = {
        "groups": len(group_summaries),
        "moveDevices": len(move_devices),
        "terminals": sum(item.get("terminals", 0) for item in group_summaries),
        "networkDevices": sum(item.get("networkDevices", 0) for item in group_summaries),
        "warnings": len(warnings),
        "plannedMinDistance": round(minimum_distance, 1) if minimum_distance is not None else None,
        "closestPair": closest_pair,
    }
    return {
        "moveDevices": move_devices,
        "manualActions": [],
        "generatedBy": generated_by,
        "summary": summary,
        "groupSummaries": group_summaries,
        "warnings": warnings,
        "strategy": {
            "origin": {"x": origin_x, "y": origin_y},
            "groupWidth": group_width,
            "groupHeight": group_height,
            "columns": columns,
            "deviceColumns": device_columns,
            "xSpacing": x_spacing,
            "ySpacing": y_spacing,
            "minDistance": min_distance,
            "namePrefix": getattr(args, "name_prefix", None),
            "subnet": str(target_subnet) if target_subnet else None,
            "includeNetworkDevices": include_network_devices,
        },
    }


def count_layout_warnings(audit):
    if not isinstance(audit, dict):
        return 0
    return len([issue for issue in audit.get("issues", []) if str(issue.get("type") or "").startswith("layout-")])


def audit_has_layout_issues(audit):
    return count_layout_warnings(audit) > 0


def merge_layout_repair_plan(plan, snapshot, audit, args, generated_by):
    if not audit_has_layout_issues(audit):
        plan.setdefault("moveDevices", [])
        return None
    layout_plan = build_layout_plan(snapshot, args, generated_by=generated_by)
    existing = {item.get("name") for item in plan.get("moveDevices", []) if isinstance(item, dict)}
    plan.setdefault("moveDevices", [])
    for item in layout_plan.get("moveDevices", []):
        if item.get("name") in existing:
            continue
        plan["moveDevices"].append(item)
        existing.add(item.get("name"))
    plan["layoutSummary"] = layout_plan.get("summary")
    plan["layoutGroups"] = layout_plan.get("groupSummaries")
    plan["layoutWarnings"] = layout_plan.get("warnings")
    if not plan.get("groupSummaries"):
        plan["groupSummaries"] = layout_plan.get("groupSummaries")
    plan["manualActions"] = [
        action
        for action in plan.get("manualActions", [])
        if not (
            action.get("type") == "inspect-issue"
            and str(((action.get("issue") or {}).get("type")) or "").startswith("layout-")
        )
    ]
    return layout_plan


def layout_plan(args):
    snapshot = snapshot_from_args(args)
    plan = build_layout_plan(snapshot, args)
    if args.plan_file:
        with open(args.plan_file, "w", encoding="utf-8") as handle:
            json.dump(plan, handle, ensure_ascii=False, indent=2)
    result = {"ok": True, "schema": "ptbuilder.layout.plan.v1", "summary": plan.get("summary"), "plan": plan, "planFile": args.plan_file}
    return print_output(args, result)


def apply_move_devices_result(args, plan):
    result = {
        "ok": True,
        "steps": [],
        "summary": {"moveDevices": 0, "changed": 0, "failed": 0},
    }

    for item in plan.get("moveDevices", []):
        name = item.get("name")
        step_result = move_device_result(args, name, item.get("x", 100), item.get("y", 100), not bool(item.get("topLeft")))
        result["steps"].append({"kind": "move-device", "name": name, "result": step_result})
        result["summary"]["moveDevices"] += 1
        if not step_result.get("ok"):
            result["ok"] = False
            result["summary"]["failed"] += 1
            continue
        value = step_result.get("value")
        if isinstance(value, dict) and value.get("moved"):
            result["summary"]["changed"] += 1
    return result


def relayout_current(args):
    snapshot = snapshot_from_args(args)
    plan = build_layout_plan(snapshot, args, generated_by="ptbuilder relayout-current")
    if args.plan_file:
        with open(args.plan_file, "w", encoding="utf-8") as handle:
            json.dump(plan, handle, ensure_ascii=False, indent=2)

    apply_result = dry_run_plan(args, plan) if getattr(args, "dry_run", False) else apply_move_devices_result(args, plan)
    audit_result = None
    if args.audit:
        audit_snapshot_value = snapshot if getattr(args, "dry_run", False) else snapshot_value(args)
        audit_result = audit_snapshot(audit_snapshot_value, getattr(args, "subnet", None))

    summary = {
        "planned": len(plan.get("moveDevices") or []),
        "groups": (plan.get("summary") or {}).get("groups", 0),
        "failed": (apply_result.get("summary") or {}).get("failed", 0),
        "layoutWarnings": count_layout_warnings(audit_result) if audit_result else None,
    }
    result = {
        "ok": bool(apply_result.get("ok") and (audit_result is None or audit_result.get("ok"))),
        "schema": "ptbuilder.layout.apply.v1",
        "summary": summary,
        "plan": plan,
        "planFile": args.plan_file,
        "apply": apply_result,
    }
    if audit_result is not None:
        result["audit"] = audit_result
    return print_output(args, result)


def find_devices(args):
    snapshot = load_json_file(args.file).get("snapshot") if args.file else snapshot_value(args)
    devices = [device for device in snapshot.get("devices", []) if isinstance(device, dict)]
    connected = connected_port_keys(snapshot)
    subnet = ipaddress.ip_network(args.subnet, strict=False) if args.subnet else None
    matches = []

    for device in devices:
        name = str(device.get("name") or "")
        model = str(device.get("model") or "")
        if args.name and name != args.name:
            continue
        if args.name_prefix and not name.startswith(args.name_prefix):
            continue
        if args.name_contains and args.name_contains not in name:
            continue
        if args.model and model != args.model:
            continue
        if args.model_contains and args.model_contains.lower() not in model.lower():
            continue
        if args.terminal and not is_terminal_device(device):
            continue

        ports = iter_ports(device)
        primary = first_usable_terminal_port(device) if is_terminal_device(device) else None
        ips = [port.get("ip") for port in ports if port.get("ip") and port.get("ip") not in ("0.0.0.0", "255.255.255.255")]
        if args.ip and args.ip not in ips:
            continue
        if subnet:
            found_in_subnet = False
            for port in ports:
                network = valid_ip_network(port.get("ip"), port.get("subnetMask"))
                if network == subnet:
                    found_in_subnet = True
                    break
            if not found_in_subnet:
                continue
        if args.disconnected:
            if not primary or port_key(name, primary.get("name")) in connected:
                continue
        matches.append(
            {
                "name": name,
                "model": model,
                "type": device.get("type"),
                "power": device.get("power"),
                "gateway": device.get("gateway"),
                "primaryPort": primary.get("name") if primary else None,
                "primaryIp": primary.get("ip") if primary else None,
                "primaryMask": primary.get("subnetMask") if primary else None,
                "connected": bool(primary and port_key(name, primary.get("name")) in connected),
            }
        )

    result = {"ok": True, "summary": {"matches": len(matches)}, "devices": matches[: args.limit]}
    return print_output(args, result)


def summarize_root_causes(audit, intent_result=None):
    buckets = {}

    def add(issue, source):
        issue_type = issue.get("type", "unknown")
        bucket = buckets.setdefault(
            issue_type,
            {
                "type": issue_type,
                "severity": issue.get("severity", "warn"),
                "count": 0,
                "source": source,
                "examples": [],
                "fix": issue.get("fix"),
            },
        )
        bucket["count"] += 1
        if issue.get("severity") == "error":
            bucket["severity"] = "error"
        if len(bucket["examples"]) < 5:
            bucket["examples"].append(issue)

    for issue in audit.get("issues", []):
        add(issue, "topology")
    if intent_result:
        for issue in intent_result.get("issues", []):
            add(issue, "intent")
        for issue in intent_result.get("unverified", []):
            issue = dict(issue)
            issue.setdefault("severity", "info")
            add(issue, "intent-unverified")

    order = {"error": 0, "warn": 1, "info": 2}
    return sorted(buckets.values(), key=lambda item: (order.get(item.get("severity"), 9), -item.get("count", 0), item.get("type", "")))


def audit_intent_result(intent, snapshot):
    class IntentArgs:
        pass

    # Keep this helper small: it mirrors audit_intent without printing.
    devices = device_by_name(snapshot)
    neighbors = link_neighbors(snapshot)
    issues = []
    unverified = []
    groups = {}
    for group_name, group_config in (intent.get("groups") or {}).items():
        matched = group_devices(snapshot, group_config)
        groups[group_name] = [device.get("name") for device in matched]
        subnet_text = group_config.get("subnet")
        expected_network = ipaddress.ip_network(subnet_text, strict=False) if subnet_text else None
        expected_gateway = group_config.get("gateway")
        expected_switch = (group_config.get("linkPolicy") or {}).get("switch") or group_config.get("switch")
        expected_vlan = group_config.get("vlan") or (group_config.get("linkPolicy") or {}).get("vlan")
        if expected_gateway and expected_network:
            try:
                if ipaddress.ip_address(expected_gateway) not in expected_network:
                    issues.append({"severity": "error", "type": "gateway-outside-subnet", "group": group_name, "gateway": expected_gateway, "subnet": str(expected_network)})
            except Exception:
                issues.append({"severity": "error", "type": "invalid-gateway", "group": group_name, "gateway": expected_gateway})
        for device in matched:
            name = device.get("name")
            port = first_usable_terminal_port(device, group_config.get("terminalPort", "FastEthernet0"))
            actual_network = valid_ip_network(port.get("ip"), port.get("subnetMask")) if port else None
            if expected_network and actual_network != expected_network:
                issues.append({"severity": "error", "type": "intent-subnet-mismatch", "group": group_name, "device": name, "expectedSubnet": str(expected_network)})
            actual_gateway = device.get("gateway")
            if expected_gateway and not is_empty_value(actual_gateway) and actual_gateway != expected_gateway:
                issues.append({"severity": "error", "type": "intent-gateway-mismatch", "group": group_name, "device": name, "actual": actual_gateway, "expected": expected_gateway})
            elif expected_gateway and is_empty_value(actual_gateway):
                unverified.append({"type": "gateway-unavailable", "group": group_name, "device": name, "expected": expected_gateway})
            if expected_switch:
                attached = [neighbor.get("device") for neighbor in neighbors.get(name, [])]
                if expected_switch not in attached:
                    issues.append({"severity": "error", "type": "intent-switch-mismatch", "group": group_name, "device": name, "expectedSwitch": expected_switch, "actualNeighbors": attached})
            if expected_vlan is not None:
                unverified.append({"type": "vlan-state-unavailable", "group": group_name, "device": name, "expectedVlan": expected_vlan})
    for policy in intent.get("policies", []):
        policy_type = policy.get("type")
        if policy_type not in ("allow-subnet", "deny-subnet"):
            unverified.append({"type": "unsupported-policy", "policy": policy})
            continue
        sources = groups.get(policy.get("sourceGroup"), [])
        destination = policy.get("destination")
        for source in sources:
            access = subnet_access_result(devices, source, destination)
            actual_allow = bool(access.get("ok") and access.get("allowed"))
            expected_allow = policy_type == "allow-subnet"
            if actual_allow != expected_allow:
                issues.append({"severity": "error", "type": "intent-policy-mismatch", "source": source, "destination": destination, "expected": "allow" if expected_allow else "deny", "actual": "allow" if actual_allow else "deny"})
    return {
        "ok": not any(issue.get("severity") == "error" for issue in issues),
        "summary": {"groups": len(groups), "matchedDevices": sum(len(names) for names in groups.values()), "issues": len(issues), "unverified": len(unverified)},
        "groups": groups,
        "issues": issues,
        "unverified": unverified,
    }


def deep_diagnose(args):
    snapshot = snapshot_value(args)
    audit = audit_snapshot(snapshot, args.subnet)
    intent_result = None
    if args.intent:
        intent_result = audit_intent_result(load_json_file(args.intent), snapshot)
    root_causes = summarize_root_causes(audit, intent_result)
    result = {
        "ok": audit.get("ok") and (intent_result.get("ok") if intent_result else True),
        "summary": {
            "devices": audit.get("summary", {}).get("devices"),
            "links": audit.get("summary", {}).get("links"),
            "topologyIssues": audit.get("summary", {}).get("issues"),
            "intentIssues": (intent_result or {}).get("summary", {}).get("issues", 0),
            "rootCauses": len(root_causes),
        },
        "rootCauses": root_causes[:20],
        "audit": audit,
        "intentAudit": intent_result,
    }
    return print_output(args, result)


def verify_rpc(args):
    code = r"""
return (function () {
  var network = null;
  var deleteResult = null;
  var getNetworkError = null;
  try {
    network = typeof getNetworkInfo == 'function' ? getNetworkInfo() : null;
  } catch (error) {
    getNetworkError = String(error && error.message ? error.message : error);
  }
  try {
    if (typeof ptbDispatch == 'function') {
      deleteResult = ptbDispatch({op:'deleteLink', args:{device:'__PTB_VERIFY_NO_SUCH_DEVICE__', port:'FastEthernet0'}});
    }
  } catch (error) {
    deleteResult = {ok:false, error:String(error && error.message ? error.message : error)};
  }
  var deleteLinkAvailable = false;
  if (deleteResult && deleteResult.ok === false && deleteResult.error && deleteResult.error.message) {
    deleteLinkAvailable = deleteResult.error.message.indexOf('Unknown operation') < 0;
  } else if (deleteResult && deleteResult.ok === false && deleteResult.reason) {
    deleteLinkAvailable = true;
  } else if (deleteResult && deleteResult.ok === true) {
    deleteLinkAvailable = true;
  }
  return {
    hasDispatch: typeof ptbDispatch == 'function',
    hasGetLinksInfo: typeof getLinksInfo == 'function',
    networkHasLinks: !!(network && network.links),
    networkLinkCount: network && network.links ? network.links.length : null,
    getNetworkError: getNetworkError,
    deleteLinkAvailable: deleteLinkAvailable,
    deleteProbe: deleteResult
  };
})();
"""
    result = submit_code(code, args)
    if result.get("ok") and isinstance(result.get("value"), dict):
        value = result["value"]
        result["ok"] = bool(value.get("hasDispatch") and value.get("hasGetLinksInfo") and value.get("networkHasLinks") and value.get("deleteLinkAvailable"))
        result["recommendation"] = None if result["ok"] else "Repack/reload the Builder module, or keep using CLI fallback commands."
    return print_output(args, result)


def probe_capabilities(args):
    code = r"""
return (function () {
  function names(obj) {
    var out = [];
    if (!obj) return out;
    for (var k in obj) out.push(k);
    return out.sort();
  }
  function pick(list, patterns) {
    var out = [];
    for (var i = 0; i < list.length; i++) {
      for (var p = 0; p < patterns.length; p++) {
        if (String(list[i]).toLowerCase().indexOf(patterns[p]) >= 0) {
          out.push(list[i]);
          break;
        }
      }
    }
    return out;
  }
  var device = null;
  if (%s) {
    try { device = ipc.network().getDevice(%s); } catch (error) { device = null; }
  }
  if (!device) {
    var count = ipc.network().getDeviceCount();
    for (var i = 0; i < count; i++) {
      var d = ipc.network().getDeviceAt(i);
      var model = d && d.getModel ? String(d.getModel()) : "";
      if (model.indexOf("Server") >= 0 || model.indexOf("PC") >= 0 || model.indexOf("2960") >= 0 || model.indexOf("3560") >= 0) {
        device = d;
        break;
      }
    }
  }
  var deviceMethods = names(device);
  var cli = null;
  var cliMethods = [];
  try { cli = device && device.getCommandLine ? device.getCommandLine() : null; } catch (error) { cli = null; }
  cliMethods = names(cli);
  var processMethods = [];
  try {
    var process = device && device.getProcess ? device.getProcess("Command Line Interface") : null;
    processMethods = names(process);
  } catch (error) {}
  return {
    device: device ? { name: device.getName ? device.getName() : null, model: device.getModel ? device.getModel() : null, type: device.getType ? device.getType() : null } : null,
    services: pick(deviceMethods, ["ftp", "http", "dns", "dhcp", "service", "server"]),
    iosCli: {
      commandLineType: cli ? typeof cli : null,
      commandLineMethods: pick(cliMethods, ["command", "text", "write", "read", "show", "exec", "line"]),
      processMethods: pick(processMethods, ["command", "text", "write", "read", "show", "exec", "line"])
    },
    workspace: {
      canRemoveDevice: typeof ipc.appWindow().getActiveWorkspace().getLogicalWorkspace().removeDevice == "function",
      canCreateLink: typeof ipc.appWindow().getActiveWorkspace().getLogicalWorkspace().createLink == "function"
    },
    rawCounts: { deviceMethods: deviceMethods.length, cliMethods: cliMethods.length, processMethods: processMethods.length }
  };
})();
""" % ("true" if args.device else "false", js_string(args.device) if args.device else "null")
    result = submit_code(code, args)
    return print_output(args, result)


CAPABILITY_PATTERNS = {
    "ftp": ["ftp"],
    "http": ["http", "web"],
    "dns": ["dns"],
    "dhcp": ["dhcp"],
    "wireless": ["wireless", "ssid", "wpa", "wep", "radio"],
    "vpn": ["vpn", "ipsec", "isakmp", "crypto"],
    "acl": ["acl", "accesslist", "access-list"],
    "service": ["service", "server", "daemon"],
    "user": ["user", "account", "password", "credential"],
}


def method_candidates(methods, patterns):
    values = []
    lowered_patterns = [str(pattern).lower() for pattern in patterns]
    for method in methods or []:
        text = str(method).lower()
        if any(pattern in text for pattern in lowered_patterns):
            values.append(method)
    return sorted(set(values))


def first_items(value, limit):
    if isinstance(value, list):
        return value[:limit]
    if isinstance(value, dict):
        return dict(list(value.items())[:limit])
    if value:
        return value
    return []


def collect_candidate_methods(subject, categories=None):
    if not isinstance(subject, dict):
        return {}
    methods = subject.get("methods") or []
    categories = categories or CAPABILITY_PATTERNS
    return {
        name: method_candidates(methods, patterns)
        for name, patterns in categories.items()
        if method_candidates(methods, patterns)
    }


def any_device_method(scan, model_tokens=None, type_values=None, patterns=None):
    model_tokens = [token.lower() for token in (model_tokens or [])]
    type_values = set(type_values or [])
    patterns = patterns or []
    matches = []
    for device in scan.get("rawDevices", []):
        if not isinstance(device, dict):
            continue
        model = str(device.get("model") or "").lower()
        device_type = device.get("type")
        if model_tokens and not any(token in model for token in model_tokens):
            continue
        if type_values and device_type not in type_values:
            continue
        candidates = method_candidates(device.get("methods") or [], patterns)
        if candidates:
            matches.append({"device": device.get("name"), "model": device.get("model"), "methods": candidates[:20]})
    return matches


def first_root_methods(scan, root_name):
    for root in scan.get("roots", []):
        if not isinstance(root, dict):
            continue
        if root.get("name") == root_name:
            return root.get("methods") or []
    return []


def build_operation_manifest(scan, ui_available):
    workspace_methods = first_root_methods(scan, "logicalWorkspace")
    network_methods = first_root_methods(scan, "network")
    port_methods = []
    ios_cli_devices = []
    for device in scan.get("rawDevices", []):
        if not isinstance(device, dict):
            continue
        for port in device.get("ports") or []:
            if not isinstance(port, dict):
                continue
            port_methods.extend(port.get("methods") or [])
        if "enterCommand" in (device.get("methods") or []):
            ios_cli_devices.append({"device": device.get("name"), "model": device.get("model")})

    server_service = {
        service: any_device_method(scan, model_tokens=["server"], patterns=patterns)
        for service, patterns in {
            "ftp": CAPABILITY_PATTERNS["ftp"],
            "http": CAPABILITY_PATTERNS["http"],
            "dns": CAPABILITY_PATTERNS["dns"],
            "dhcp": CAPABILITY_PATTERNS["dhcp"],
        }.items()
    }
    wireless_methods = any_device_method(scan, model_tokens=["accesspoint", "wireless", "laptop"], patterns=CAPABILITY_PATTERNS["wireless"])

    def operation(name, supported, adapter, evidence=None, confidence="high", note=None):
        item = {
            "supported": bool(supported),
            "adapter": adapter if supported else None,
            "confidence": confidence if supported else "none",
        }
        if evidence:
            item["evidence"] = first_items(evidence, 10)
        if note:
            item["note"] = note
        return name, item

    operations = dict(
        [
            operation("topology.device.create", "addDevice" in workspace_methods, "builder-js", ["logicalWorkspace.addDevice"]),
            operation("topology.link.create", "createLink" in workspace_methods, "builder-js", ["logicalWorkspace.createLink"]),
            operation("topology.device.remove", "removeDevice" in workspace_methods, "builder-js", ["logicalWorkspace.removeDevice"]),
            operation("topology.snapshot", "getDeviceCount" in network_methods, "builder-js", ["network.getDeviceCount"]),
            operation("pc.ip.configure", "setIpSubnetMask" in port_methods, "builder-js", ["port.setIpSubnetMask"]),
            operation("pc.gateway.configure", "setDefaultGateway" in port_methods, "builder-js", ["port.setDefaultGateway"]),
            operation("pc.dns.configure", "setDnsServerIp" in port_methods, "builder-js", ["port.setDnsServerIp"]),
            operation("ios.cli.configure", bool(ios_cli_devices), "builder-js", ios_cli_devices, note="Uses device.enterCommand; best for routers/switches/firewalls."),
        ]
    )

    for service, evidence in server_service.items():
        if evidence:
            operations[f"server.{service}.configure"] = {
                "supported": True,
                "adapter": "builder-js-candidate",
                "confidence": "low",
                "evidence": evidence,
                "note": "Candidate methods exist. Use an allowlisted adapter before invoking unknown method names.",
            }
        elif ui_available:
            operations[f"server.{service}.configure"] = {
                "supported": True,
                "adapter": "ui-automation-required",
                "confidence": "medium",
                "evidence": ["Packet Tracer window is visible; no stable Builder API method was discovered."],
                "note": "Requires a model-specific UI automation recipe and post-action verification.",
            }
        else:
            operations[f"server.{service}.configure"] = {
                "supported": False,
                "adapter": None,
                "confidence": "none",
                "note": "No Builder API method discovered and Packet Tracer UI is not available.",
            }

    operations["wireless.ssid.configure"] = {
        "supported": bool(wireless_methods or ui_available),
        "adapter": "builder-js-candidate" if wireless_methods else "ui-automation-required" if ui_available else None,
        "confidence": "low" if wireless_methods else "medium" if ui_available else "none",
        "evidence": first_items(wireless_methods, 10) if wireless_methods else (["Packet Tracer window is visible; no stable Builder API method was discovered."] if ui_available else []),
        "note": "Wireless SSID support depends on device model. Use capability-scan plus UI inspection before applying.",
    }
    operations["vpn.configure"] = {
        "supported": bool(ios_cli_devices or ui_available),
        "adapter": "ios-cli-or-ui",
        "confidence": "medium" if ios_cli_devices else "low",
        "evidence": first_items(ios_cli_devices, 10) if ios_cli_devices else (["Packet Tracer window is visible."] if ui_available else []),
        "note": "Prefer IOS/ASA CLI when the target supports it; otherwise use a UI automation recipe.",
    }
    return operations


def summarize_capability_devices(scan):
    devices = []
    for device in scan.get("rawDevices", []):
        if not isinstance(device, dict):
            continue
        candidates = collect_candidate_methods(device)
        port_count = len(device.get("ports") or [])
        devices.append(
            {
                "name": device.get("name"),
                "model": device.get("model"),
                "type": device.get("type"),
                "methodCount": len(device.get("methods") or []),
                "ports": port_count,
                "candidates": {key: first_items(value, 10) for key, value in candidates.items()},
            }
        )
    return devices


def capability_scan_result(args):
    code = r"""
return (function () {
  function safeCall(fn, fallback) {
    try { return fn(); } catch (error) { return fallback; }
  }
  function unique(values) {
    var seen = {};
    var out = [];
    for (var i = 0; i < values.length; i++) {
      var value = String(values[i]);
      if (!seen[value]) {
        seen[value] = true;
        out.push(value);
      }
    }
    return out.sort();
  }
  function names(obj, deep) {
    var out = [];
    var maxNames = 800;
    if (!obj) return out;
    for (var k in obj) {
      out.push(k);
      if (out.length >= maxNames) break;
    }
    if (!deep) return unique(out);
    var current = obj;
    var depth = 0;
    while (current && depth < 4 && out.length < maxNames) {
      try {
        var own = Object.getOwnPropertyNames(current);
        for (var i = 0; i < own.length && out.length < maxNames; i++) out.push(own[i]);
      } catch (error) {}
      try { current = Object.getPrototypeOf(current); } catch (error) { current = null; }
      depth += 1;
    }
    return unique(out);
  }
  function methods(obj, deep) {
    var raw = names(obj, deep);
    var out = [];
    for (var i = 0; i < raw.length; i++) {
      var name = raw[i];
      try {
        if (typeof obj[name] == "function") out.push(name);
      } catch (error) {}
    }
    return unique(out);
  }
  function valuesMatching(methodList, patterns) {
    var out = [];
    for (var i = 0; i < methodList.length; i++) {
      var lower = String(methodList[i]).toLowerCase();
      for (var p = 0; p < patterns.length; p++) {
        if (lower.indexOf(patterns[p]) >= 0) {
          out.push(methodList[i]);
          break;
        }
      }
    }
    return unique(out);
  }
  function subject(name, obj, deep) {
    var methodList = methods(obj, deep);
    return {
      name: name,
      exists: !!obj,
      methods: methodList,
      candidates: {
        service: valuesMatching(methodList, ["service", "server", "daemon"]),
        ftp: valuesMatching(methodList, ["ftp"]),
        http: valuesMatching(methodList, ["http", "web"]),
        dns: valuesMatching(methodList, ["dns"]),
        dhcp: valuesMatching(methodList, ["dhcp"]),
        wireless: valuesMatching(methodList, ["wireless", "ssid", "wpa", "wep", "radio"]),
        vpn: valuesMatching(methodList, ["vpn", "ipsec", "isakmp", "crypto"]),
        acl: valuesMatching(methodList, ["acl", "accesslist", "access-list"]),
        user: valuesMatching(methodList, ["user", "account", "password", "credential"])
      }
    };
  }
  function deviceInfo(device, deep) {
    var name = safeCall(function () { return device.getName(); }, null);
    var model = safeCall(function () { return device.getModel(); }, null);
    var type = safeCall(function () { return device.getType(); }, null);
    var info = subject(name, device, deep);
    info.name = name;
    info.model = model;
    info.type = type;
    info.ports = [];
    var portCount = safeCall(function () { return device.getPortCount(); }, 0);
    for (var i = 0; i < portCount; i++) {
      var port = safeCall(function () { return device.getPortAt(i); }, null);
      if (port) {
        var portInfo = subject(safeCall(function () { return port.getName(); }, "port-" + i), port, deep);
        portInfo.ip = safeCall(function () { return port.getIpAddress(); }, null);
        portInfo.mask = safeCall(function () { return port.getSubnetMask(); }, null);
        info.ports.push(portInfo);
      }
    }
    var cli = safeCall(function () { return device.getCommandLine ? device.getCommandLine() : null; }, null);
    info.commandLine = subject("commandLine", cli, deep);
    var process = safeCall(function () { return device.getProcess ? device.getProcess("Command Line Interface") : null; }, null);
    info.cliProcess = subject("cliProcess", process, deep);
    return info;
  }
  var deep = %s;
  var limit = %d;
  var wanted = %s;
  var network = safeCall(function () { return ipc.network(); }, null);
  var appWindow = safeCall(function () { return ipc.appWindow(); }, null);
  var activeWorkspace = safeCall(function () { return appWindow.getActiveWorkspace(); }, null);
  var logicalWorkspace = safeCall(function () { return activeWorkspace.getLogicalWorkspace(); }, null);
  var pdu = safeCall(function () { return appWindow.getUserCreatedPDU(); }, null);
  var roots = [
    subject("ipc", ipc, deep),
    subject("network", network, deep),
    subject("appWindow", appWindow, deep),
    subject("activeWorkspace", activeWorkspace, deep),
    subject("logicalWorkspace", logicalWorkspace, deep),
    subject("userCreatedPDU", pdu, deep)
  ];
  var devices = [];
  if (wanted) {
    var selected = safeCall(function () { return network.getDevice(wanted); }, null);
    if (selected) devices.push(deviceInfo(selected, deep));
  } else {
    var count = safeCall(function () { return network.getDeviceCount(); }, 0);
    var seenModels = {};
    for (var i = 0; i < count; i++) {
      var device = safeCall(function () { return network.getDeviceAt(i); }, null);
      if (!device) continue;
      var model = safeCall(function () { return String(device.getModel()); }, "");
      var name = safeCall(function () { return String(device.getName()); }, "");
      var key = model || name;
      if (limit > 0 && devices.length >= limit && seenModels[key]) continue;
      devices.push(deviceInfo(device, deep));
      seenModels[key] = true;
      if (limit > 0 && devices.length >= limit && Object.keys(seenModels).length >= limit) break;
    }
  }
  return {
    packetTracer: {
      deviceCount: safeCall(function () { return network.getDeviceCount(); }, null),
      linkCount: safeCall(function () { return network.getLinkCount(); }, null)
    },
    roots: roots,
    rawDevices: devices
  };
})();
""" % ("true" if args.deep else "false", int(args.limit), js_string(args.device) if args.device else "null")
    submitted = submit_code(code, args)
    if not submitted.get("ok"):
        return submitted
    scan = submitted.get("value") or {}
    scan = parse_jsonish(scan)
    if not isinstance(scan, dict):
        return {
            "ok": False,
            "schema": "ptbuilder.capabilities.v1",
            "error": "invalid-capability-scan-result",
            "resultType": type(scan).__name__,
            "raw": scan,
        }
    ui_available = bool(is_packet_tracer_running() and is_builder_window_open())
    operations = build_operation_manifest(scan, ui_available)
    manifest = {
        "ok": True,
        "schema": "ptbuilder.capabilities.v1",
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "transport": submitted.get("transport", getattr(args, "transport", None)),
        "packetTracer": scan.get("packetTracer"),
        "summary": {
            "devicesScanned": len(scan.get("rawDevices") or []),
            "rootsScanned": len(scan.get("roots") or []),
            "operations": len(operations),
            "uiAutomationAvailable": ui_available,
        },
        "operations": operations,
        "devices": summarize_capability_devices(scan),
        "roots": [
            {
                "name": root.get("name"),
                "exists": root.get("exists"),
                "methodCount": len(root.get("methods") or []),
                "candidates": {key: first_items(value, 20) for key, value in (root.get("candidates") or {}).items() if value},
            }
            for root in scan.get("roots", [])
            if isinstance(root, dict)
        ],
        "raw": scan if args.include_raw else None,
        "warnings": [],
        "files": {},
    }
    if not any(item.get("adapter") == "builder-js" and item.get("supported") for item in operations.values()):
        manifest["warnings"].append("No stable Builder JS operations were detected. Repack/reload the Builder extension or check the bridge context.")
    if args.file:
        directory = os.path.dirname(os.path.abspath(args.file))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(args.file, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
        manifest["files"]["manifest"] = args.file
    return manifest


def capability_scan(args):
    return print_output(args, capability_scan_result(args))


def ui_inspect(args):
    script = os.path.join(os.getcwd(), "tools", "ptbuilder_ui_inspect.ps1")
    if not os.path.exists(script):
        return print_output(args, {"ok": False, "error": "missing-ui-inspect-script", "script": script})
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        script,
        "-TimeoutSeconds",
        str(int(args.timeout)),
        "-MaxDepth",
        str(int(args.max_depth)),
        "-MaxChildren",
        str(int(args.max_children)),
    ]
    if args.window:
        command.extend(["-WindowName", args.window])
    completed = subprocess.run(
        command,
        cwd=os.getcwd(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        return print_output(args, {"ok": False, "schema": "ptbuilder.ui.inspect.v1", "error": completed.stderr.strip() or completed.stdout.strip()})
    try:
        payload = parse_jsonish(completed.stdout)
        if not isinstance(payload, dict):
            raise ValueError("UI inspect did not return a JSON object")
    except Exception:
        payload = {"ok": False, "schema": "ptbuilder.ui.inspect.v1", "error": "invalid-json", "stdout": completed.stdout}
    files = {}
    if args.file and payload.get("ok"):
        directory = os.path.dirname(os.path.abspath(args.file))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(args.file, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        files["uiTree"] = args.file
    payload.setdefault("files", {}).update(files)
    return print_output(args, payload)


def default_capability_file():
    return os.path.join(os.getcwd(), "examples", "capabilities_pt822.json")


def capability_for_operations(args):
    path = getattr(args, "capabilities", None)
    if path:
        return load_json_file(path)
    default_path = default_capability_file()
    if os.path.exists(default_path) and not getattr(args, "refresh_capabilities", False):
        return load_json_file(default_path)

    scan_args = argparse.Namespace(
        host=getattr(args, "host", DEFAULT_HOST),
        port=getattr(args, "port", DEFAULT_PORT),
        timeout=getattr(args, "timeout", 30),
        transport=getattr(args, "transport", "auto"),
        require_connected=getattr(args, "require_connected", False),
        device=None,
        deep=False,
        limit=getattr(args, "capability_limit", 12),
        include_raw=False,
        file=default_path,
        output="json",
    )
    return capability_scan_result(scan_args)


def capability_operation(capabilities, name):
    operations = (capabilities or {}).get("operations") or {}
    return operations.get(name) or {"supported": False, "adapter": None, "confidence": "none"}


def command_text(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(item) for item in value if str(item).strip())
    return str(value)


def parse_optional_float(value):
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def truthy_config(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("false", "0", "no", "off", "deny", "disabled"):
        return False
    if text in ("true", "1", "yes", "on", "allow", "enabled"):
        return True
    return default


def bridge_health_for_lookup(args):
    transport = getattr(args, "transport", "auto")
    if transport == "ui":
        return False, {"transport": "ui", "reason": "ui-transport-selected"}
    try:
        status, health = request_json("GET", "/health", host=args.host, port=args.port, timeout=1)
    except Exception as error:
        return False, {"transport": transport, "reason": "bridge-health-unavailable", "error": str(error)}
    if isinstance(health, dict) and status < 400 and health.get("connected"):
        return True, {"transport": "bridge", "connected": True}
    return False, {"transport": transport, "reason": "bridge-not-connected", "health": health if isinstance(health, dict) else None}


def logical_pair_from_mapping(value):
    if not isinstance(value, dict):
        return None
    x = None
    y = None
    logical = value.get("logical") if isinstance(value.get("logical"), dict) else {}
    for x_key in ("logicalX", "centerX", "x"):
        x = parse_optional_float(value.get(x_key))
        if x is not None:
            break
    if x is None:
        for x_key in ("centerX", "x"):
            x = parse_optional_float(logical.get(x_key))
            if x is not None:
                break
    for y_key in ("logicalY", "centerY", "y"):
        y = parse_optional_float(value.get(y_key))
        if y is not None:
            break
    if y is None:
        for y_key in ("centerY", "y"):
            y = parse_optional_float(logical.get(y_key))
            if y is not None:
                break
    if x is None or y is None:
        return None
    return {"x": x, "y": y}


def fetch_device_logical_position(args, device_name):
    code = wrap_expression(
        """(function (name) {
    var device = ipc.network().getDevice(name);
    function safe(methodName) {
        try {
            if (device && typeof device[methodName] == "function") {
                var value = device[methodName]();
                return value === undefined ? null : value;
            }
        } catch (error) {
            return null;
        }
        return null;
    }
    if (!device) {
        return { ok: false, device: name, error: "device-not-found" };
    }
    return {
        ok: true,
        device: name,
        logical: {
            x: safe("getXCoordinate"),
            y: safe("getYCoordinate"),
            centerX: safe("getCenterXCoordinate"),
            centerY: safe("getCenterYCoordinate")
        }
    };
})(%s)"""
        % js_string(device_name)
    )
    submitted = submit_code(code, args)
    if not submitted.get("ok"):
        return None, submitted
    value = parse_jsonish(submitted.get("value"))
    if isinstance(value, dict) and value.get("ok"):
        pair = logical_pair_from_mapping(value)
        if pair:
            return pair, value
    return None, value if isinstance(value, dict) else {"ok": False, "error": "invalid-position-result", "value": value}


def resolve_device_logical_position(args, device_name, request=None):
    pair = logical_pair_from_mapping(request or {})
    if pair:
        return pair, {"source": "request"}

    bridge_ready, bridge_detail = bridge_health_for_lookup(args)
    if not bridge_ready:
        return None, {"source": "ui-fallback", "device": device_name, "bridge": bridge_detail}

    try:
        snapshot = snapshot_value(args)
        for device in snapshot.get("devices", []):
            if isinstance(device, dict) and device.get("name") == device_name:
                pair = logical_pair_from_mapping(device)
                if pair:
                    return pair, {"source": "snapshot", "device": device_name}
                break
    except Exception as error:
        snapshot_error = str(error)
    else:
        snapshot_error = None

    pair, detail = fetch_device_logical_position(args, device_name)
    if pair:
        return pair, {"source": "direct-js", "device": device_name}
    return None, {"source": "ui-fallback", "device": device_name, "snapshotError": snapshot_error, "detail": detail}


def ui_device_script_path():
    return os.path.join(os.getcwd(), "tools", "ptbuilder_ui_device.ps1")


def run_ui_device_action(args, action, params):
    script = ui_device_script_path()
    if os.name != "nt":
        return {"ok": False, "schema": "ptbuilder.ui.device.v1", "action": action, "error": "ui-automation-requires-windows"}
    if not os.path.exists(script):
        return {"ok": False, "schema": "ptbuilder.ui.device.v1", "action": action, "error": "missing-ui-device-script", "script": script}

    timeout_seconds = int(getattr(args, "timeout", 30) or 30)
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        script,
        "-Action",
        action,
        "-TimeoutSeconds",
        str(timeout_seconds),
    ]
    for key, value in params.items():
        if value is None or value == "":
            continue
        command.extend([f"-{key}", str(value)])

    try:
        completed = subprocess.run(
            command,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds + 10,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "schema": "ptbuilder.ui.device.v1",
            "action": action,
            "error": "ui-device-action-timeout",
            "timeoutSeconds": timeout_seconds,
        }
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if completed.returncode != 0:
        return {
            "ok": False,
            "schema": "ptbuilder.ui.device.v1",
            "action": action,
            "error": stderr or stdout or "ui-device-action-failed",
            "returncode": completed.returncode,
        }
    try:
        payload = parse_jsonish(stdout)
        if not isinstance(payload, dict):
            raise ValueError("UI action did not return a JSON object")
        return payload
    except Exception:
        return {"ok": False, "schema": "ptbuilder.ui.device.v1", "action": action, "error": "invalid-json", "stdout": stdout}


def ftp_users_from_request(request):
    users = request.get("users") or request.get("accounts") or []
    if isinstance(users, dict):
        users = [users]
    if not isinstance(users, list):
        users = []
    if not users and (request.get("username") or request.get("user")):
        users = [request]
    normalized = []
    for user in users:
        if not isinstance(user, dict):
            continue
        username = user.get("username") or user.get("user") or user.get("name")
        password = user.get("password") or user.get("pass")
        permission = user.get("permission") or user.get("permissions") or request.get("permission") or request.get("permissions") or "RWDNL"
        if username and password:
            normalized.append({"username": username, "password": password, "permission": permission})
    return normalized


def configure_server_ftp_ui(args, item):
    request = item.get("request") or {}
    device_name = item.get("device") or request.get("device") or request.get("name")
    if not device_name:
        return {"ok": False, "schema": "ptbuilder.ui.device.v1", "action": "ConfigureServerFtp", "error": "missing-device"}

    users = ftp_users_from_request(request)
    if not users:
        return {
            "ok": False,
            "schema": "ptbuilder.ui.device.v1",
            "action": "ConfigureServerFtp",
            "device": device_name,
            "service": "ftp",
            "error": "missing-ftp-users",
        }

    logical, detail = resolve_device_logical_position(args, device_name, request)
    coordinate_source = detail.get("source") if isinstance(detail, dict) else None

    results = []
    enabled = "true" if truthy_config(request.get("enabled"), True) else "false"
    for user in users:
        params = {
            "DeviceName": device_name,
            "Enabled": enabled,
            "Username": user["username"],
            "Password": user["password"],
            "Permission": user.get("permission") or "RWDNL",
        }
        if logical:
            params["LogicalX"] = logical["x"]
            params["LogicalY"] = logical["y"]
        result = run_ui_device_action(
            args,
            "ConfigureServerFtp",
            params,
        )
        results.append(result)
        if not result.get("ok"):
            break

    ok = all(result.get("ok") for result in results)
    verified = all(result.get("verified") for result in results)
    if len(results) == 1:
        single = dict(results[0])
        single.setdefault("coordinateSource", coordinate_source)
        return single
    return {
        "ok": ok and verified,
        "schema": "ptbuilder.ui.device.v1",
        "action": "ConfigureServerFtp",
        "device": device_name,
        "service": "ftp",
        "enabled": enabled == "true",
        "recipe": "server-pt-ftp-ui-v1",
        "verified": verified,
        "users": [{"username": result.get("username"), "ok": result.get("ok"), "verified": result.get("verified")} for result in results],
        "summary": {"users": len(results), "failed": sum(1 for result in results if not result.get("ok")), "verified": verified},
        "coordinateSource": coordinate_source,
        "error": None if ok and verified else "one-or-more-ftp-users-failed",
    }


def ui_server_ftp(args):
    request = {
        "device": args.device,
        "service": "ftp",
        "enabled": args.enabled,
        "users": [
            {
                "username": args.username,
                "password": args.password,
                "permission": args.permission,
            }
        ],
    }
    if args.logical_x is not None:
        request["logicalX"] = args.logical_x
    if args.logical_y is not None:
        request["logicalY"] = args.logical_y
    return print_output(args, configure_server_ftp_ui(args, {"kind": "server-service", "device": args.device, "request": request}))


def ui_recipe_adapter(kind, capability_name, request):
    service = str((request or {}).get("service") or (request or {}).get("type") or "").lower()
    if kind == "server-service" and capability_name == "server.ftp.configure" and service == "ftp":
        if os.name == "nt" and os.path.exists(ui_device_script_path()):
            return "ui-server-ftp"
    return None


def stable_cli_supported(capabilities):
    op = capability_operation(capabilities, "ios.cli.configure")
    return bool(op.get("supported") and op.get("adapter") == "builder-js")


def operation_item(index, kind, request, capability=None, adapter=None, status=None, reason=None, commands=None):
    capability = capability or {}
    chosen_adapter = adapter or capability.get("adapter")
    if status is None:
        if capability.get("supported") and chosen_adapter == "builder-js":
            status = "applyable"
        elif capability.get("supported"):
            status = "blocked"
        else:
            status = "unsupported"
    if not reason:
        if status == "blocked":
            reason = "No stable apply adapter is available for this operation yet."
        elif status == "unsupported":
            reason = "Capability scan did not find a supported Packet Tracer API path."
    item = {
        "id": f"op{index:03d}",
        "kind": kind,
        "device": request.get("device") or request.get("name"),
        "status": status,
        "adapter": chosen_adapter if status in ("applyable", "verify-only", "blocked") else None,
        "capability": request.get("capability"),
        "request": request,
    }
    if capability:
        item["confidence"] = capability.get("confidence")
        if capability.get("evidence"):
            item["evidence"] = capability.get("evidence")
    if commands:
        item["commands"] = commands
    if reason:
        item["reason"] = reason
    return item


def add_operation(collection, kind, request, capabilities, capability_name=None, commands=None, force_adapter=None):
    request = dict(request)
    capability_name = capability_name or request.get("capability")
    if capability_name:
        request["capability"] = capability_name
    capability = capability_operation(capabilities, capability_name) if capability_name else {}
    status = None
    reason = None
    adapter = force_adapter

    if force_adapter == "ios-cli":
        if stable_cli_supported(capabilities):
            status = "applyable"
            adapter = "ios-cli"
        else:
            status = "unsupported"
            reason = "No stable IOS CLI adapter was detected in the capability manifest."
    elif ui_recipe_adapter(kind, capability_name, request):
        status = "applyable"
        adapter = ui_recipe_adapter(kind, capability_name, request)
    elif capability_name and capability.get("adapter") == "builder-js-candidate":
        status = "blocked"
        reason = "Candidate Builder JS method names were discovered, but no allowlisted implementation exists yet."
    elif capability_name and capability.get("adapter") == "ui-automation-required":
        status = "blocked"
        reason = "Packet Tracer UI is visible, but this operation needs a model-specific UI automation recipe."
    elif capability_name and capability.get("adapter") in ("ios-cli-or-ui", "ios-cli"):
        if commands and stable_cli_supported(capabilities):
            status = "applyable"
            adapter = "ios-cli"
        else:
            status = "blocked"
            reason = "Provide IOS/ASA CLI commands or add a UI automation recipe for this target."

    collection.append(
        operation_item(
            len(collection) + 1,
            kind,
            request,
            capability=capability,
            adapter=adapter,
            status=status,
            reason=reason,
            commands=commands,
        )
    )


def add_pc_config_operation(collection, request, capabilities):
    required = ["pc.ip.configure"]
    if request.get("gateway"):
        required.append("pc.gateway.configure")
    if request.get("dns"):
        required.append("pc.dns.configure")

    missing = []
    for name in required:
        op = capability_operation(capabilities, name)
        if not (op.get("supported") and op.get("adapter") == "builder-js"):
            missing.append(name)

    status = "applyable" if not missing else "unsupported"
    reason = None
    if missing:
        reason = "Missing stable PC configuration capabilities: " + ", ".join(missing)
    request = dict(request)
    request["capability"] = ",".join(required)
    collection.append(
        operation_item(
            len(collection) + 1,
            "pc-config",
            request,
            capability=capability_operation(capabilities, "pc.ip.configure"),
            adapter="builder-js" if not missing else None,
            status=status,
            reason=reason,
        )
    )


def build_operation_plan(spec, capabilities):
    operations = []
    for item in spec.get("services") or spec.get("serverServices") or []:
        service = str(item.get("service") or item.get("type") or "").lower()
        capability_name = f"server.{service}.configure" if service else "server.service.configure"
        add_operation(operations, "server-service", dict(item, service=service), capabilities, capability_name)

    for item in spec.get("wireless") or spec.get("wirelessNetworks") or []:
        add_operation(operations, "wireless-ssid", item, capabilities, "wireless.ssid.configure")

    for item in spec.get("vpn") or spec.get("vpns") or []:
        commands = command_text(item.get("commands") or item.get("config"))
        add_operation(operations, "vpn", item, capabilities, "vpn.configure", commands=commands or None)

    for item in spec.get("acl") or spec.get("acls") or []:
        commands = command_text(item.get("commands") or item.get("config"))
        add_operation(operations, "acl", item, capabilities, "ios.cli.configure", commands=commands or None, force_adapter="ios-cli")

    for name, value in (spec.get("iosConfigs") or {}).items():
        commands = command_text(value.get("commands") if isinstance(value, dict) else value)
        add_operation(operations, "ios-cli", {"device": name}, capabilities, "ios.cli.configure", commands=commands, force_adapter="ios-cli")

    for name, config in (spec.get("pcConfigs") or {}).items():
        request = dict(config or {})
        request["device"] = name
        add_pc_config_operation(operations, request, capabilities)

    for item in spec.get("tests") or []:
        request = dict(item)
        request.setdefault("mode", "pdu")
        operations.append(
            operation_item(
                len(operations) + 1,
                "connectivity-test",
                request,
                adapter="pdu" if request.get("mode") == "pdu" else "offline-same-subnet",
                status="verify-only",
            )
        )

    summary = {
        "requested": len(operations),
        "applyable": sum(1 for item in operations if item.get("status") == "applyable"),
        "blocked": sum(1 for item in operations if item.get("status") == "blocked"),
        "unsupported": sum(1 for item in operations if item.get("status") == "unsupported"),
        "verifyOnly": sum(1 for item in operations if item.get("status") == "verify-only"),
    }
    return {
        "ok": summary["unsupported"] == 0,
        "schema": "ptbuilder.operation.plan.v1",
        "summary": summary,
        "capabilities": {
            "source": "manifest",
            "ok": bool(capabilities.get("ok")),
            "summary": capabilities.get("summary"),
        },
        "operations": operations,
        "blocked": [item for item in operations if item.get("status") in ("blocked", "unsupported")],
        "files": {},
    }


def operation_plan(args):
    spec = load_json_file(args.file)
    capabilities = capability_for_operations(args)
    if not capabilities.get("ok"):
        return print_output(
            args,
            {
                "ok": False,
                "schema": "ptbuilder.operation.plan.v1",
                "error": "capability-manifest-unavailable",
                "capabilities": capabilities,
            },
        )
    plan = build_operation_plan(spec, capabilities)
    if args.plan_file:
        directory = os.path.dirname(os.path.abspath(args.plan_file))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(args.plan_file, "w", encoding="utf-8") as handle:
            json.dump(plan, handle, ensure_ascii=False, indent=2)
        plan["files"]["plan"] = args.plan_file
    return print_output(args, plan)


def apply_operation_plan_result(args, plan):
    if getattr(args, "dry_run", False):
        operations = plan.get("operations") or []
        blocked = [item for item in operations if item.get("status") in ("blocked", "unsupported")]
        return {
            "ok": not blocked or getattr(args, "allow_partial", False),
            "schema": "ptbuilder.operation.apply.v1",
            "dryRun": True,
            "summary": {
                "seen": len(operations),
                "wouldApply": sum(1 for item in operations if item.get("status") == "applyable"),
                "wouldVerify": sum(1 for item in operations if item.get("status") == "verify-only"),
                "blocked": len(blocked),
            },
            "steps": [
                {"id": item.get("id"), "kind": item.get("kind"), "status": item.get("status"), "adapter": item.get("adapter")}
                for item in operations
                if item.get("status") in ("applyable", "verify-only")
            ],
            "blocked": blocked,
        }

    result = {
        "ok": True,
        "schema": "ptbuilder.operation.apply.v1",
        "summary": {"seen": 0, "applied": 0, "verified": 0, "blocked": 0, "failed": 0},
        "steps": [],
        "blocked": [],
    }
    for item in plan.get("operations") or []:
        status = item.get("status")
        kind = item.get("kind")
        result["summary"]["seen"] += 1
        if status in ("blocked", "unsupported"):
            result["summary"]["blocked"] += 1
            result["blocked"].append(item)
            if not getattr(args, "allow_partial", False):
                result["ok"] = False
            continue
        if status == "verify-only":
            request = item.get("request") or {}
            if request.get("mode", "pdu") != "pdu":
                step_result = {"ok": False, "error": "only-pdu-tests-are-live-applyable"}
            else:
                step_result = rpc_call(args, "sendPdu", {"source": request.get("source"), "destination": request.get("destination")})
            if not step_result.get("ok"):
                result["summary"]["failed"] += 1
                result["ok"] = False
            else:
                result["summary"]["verified"] += 1
            result["steps"].append({"id": item.get("id"), "kind": kind, "result": step_result})
            continue
        if status != "applyable":
            continue

        request = item.get("request") or {}
        if kind == "pc-config":
            step_result = rpc_call(
                args,
                "configurePc",
                {
                    "name": item.get("device"),
                    "dhcp": request.get("dhcp", False),
                    "ip": request.get("ip"),
                    "mask": request.get("mask") or request.get("subnetMask"),
                    "gateway": request.get("gateway"),
                    "dns": request.get("dns"),
                },
            )
        elif item.get("adapter") == "ios-cli":
            step_result = rpc_call(args, "configureIos", {"name": item.get("device"), "commands": item.get("commands") or ""})
        elif kind == "server-service" and item.get("adapter") == "ui-server-ftp":
            step_result = configure_server_ftp_ui(args, item)
        else:
            step_result = {"ok": False, "error": "adapter-not-implemented", "adapter": item.get("adapter")}

        if step_result.get("ok"):
            result["summary"]["applied"] += 1
        else:
            result["summary"]["failed"] += 1
            result["ok"] = False
        result["steps"].append({"id": item.get("id"), "kind": kind, "result": step_result})
    return result


def operation_apply(args):
    document = load_json_file(args.file)
    plan = document if document.get("schema") == "ptbuilder.operation.plan.v1" else None
    if not plan:
        capabilities = capability_for_operations(args)
        if not capabilities.get("ok"):
            return print_output(args, {"ok": False, "schema": "ptbuilder.operation.apply.v1", "error": "capability-manifest-unavailable", "capabilities": capabilities})
        plan = build_operation_plan(document, capabilities)
    return print_output(args, apply_operation_plan_result(args, plan))


def device_by_name(snapshot):
    return {
        device.get("name"): device
        for device in snapshot.get("devices", [])
        if isinstance(device, dict) and device.get("name")
    }


def find_port(snapshot, device_name, port_name):
    device = device_by_name(snapshot).get(device_name)
    if not device:
        return None
    for port in iter_ports(device):
        if port.get("name") == port_name:
            return port
    return None


def connected_port_keys(snapshot):
    keys = set()
    for link in snapshot.get("links", []):
        if not isinstance(link, dict):
            continue
        for endpoint in link.get("endpoints", []):
            if isinstance(endpoint, dict) and endpoint.get("device") and endpoint.get("port"):
                keys.add(port_key(endpoint.get("device"), endpoint.get("port")))
    return keys


def match_device(device, matcher):
    matcher = matcher or {}
    name = str(device.get("name") or "")
    model = str(device.get("model") or "")
    if matcher.get("namePrefix") and not name.startswith(str(matcher.get("namePrefix"))):
        return False
    if matcher.get("nameContains") and str(matcher.get("nameContains")) not in name:
        return False
    if matcher.get("model") and model != str(matcher.get("model")):
        return False
    if matcher.get("modelContains") and str(matcher.get("modelContains")).lower() not in model.lower():
        return False
    if matcher.get("names") and name not in matcher.get("names"):
        return False
    if matcher.get("terminal") is True and not is_terminal_device(device):
        return False
    return True


def group_devices(snapshot, group_config):
    devices = [device for device in snapshot.get("devices", []) if isinstance(device, dict)]
    matched = [device for device in devices if match_device(device, group_config.get("match") or {})]
    if group_config.get("terminalOnly", True):
        matched = [device for device in matched if is_terminal_device(device)]
    return sorted(matched, key=lambda item: str(item.get("name") or ""))


def first_usable_terminal_port(device, preferred="FastEthernet0"):
    preferred_port = None
    for port in iter_ports(device):
        if port.get("name") == preferred:
            preferred_port = port
            break
    if preferred_port:
        return preferred_port
    for port in iter_ports(device):
        if port.get("isEthernet") is True or str(port.get("name") or "").startswith(("FastEthernet", "GigabitEthernet", "Ethernet")):
            return port
    return None


def find_free_switch_port(snapshot, switch_name, port_range=None, used_for_plan=None):
    used_for_plan = used_for_plan or set()
    connected = connected_port_keys(snapshot) | used_for_plan
    switch = device_by_name(snapshot).get(switch_name)
    if not switch:
        return None
    candidate_names = expand_port_range(port_range) if port_range else []
    if not candidate_names:
        candidate_names = [
            port.get("name")
            for port in iter_ports(switch)
            if str(port.get("name") or "").startswith(("FastEthernet", "GigabitEthernet", "Ethernet"))
        ]
    for name in candidate_names:
        if not name:
            continue
        key = port_key(switch_name, name)
        if key in connected:
            continue
        if find_port(snapshot, switch_name, name):
            used_for_plan.add(key)
            return name
    return None


def used_ips(snapshot):
    values = set()
    for device in snapshot.get("devices", []):
        if not isinstance(device, dict):
            continue
        for port in iter_ports(device):
            ip_value = port.get("ip")
            if ip_value and ip_value not in ("0.0.0.0", "255.255.255.255"):
                values.add(ip_value)
    return values


def next_available_ip(network, used, start_host=10):
    hosts = list(network.hosts())
    for host in hosts:
        last_octet = int(str(host).split(".")[-1])
        if last_octet < start_host:
            continue
        value = str(host)
        if value not in used:
            used.add(value)
            return value
    return None


def suggest_repair_plan(snapshot, audit, args):
    plan = generate_repair_plan(audit)
    plan["generatedBy"] = "ptbuilder suggest-plan"
    plan["strategy"] = {
        "startHost": args.start_host,
        "gateway": args.gateway,
        "subnet": args.subnet,
    }
    merge_layout_repair_plan(plan, snapshot, audit, args, "ptbuilder suggest-plan layout")
    used = used_ips(snapshot)
    target_network = ipaddress.ip_network(args.subnet, strict=False) if args.subnet else None
    target_mask = str(target_network.netmask) if target_network else None

    for issue in audit.get("issues", []):
        issue_type = issue.get("type")
        if issue_type == "duplicate-ip":
            owners = issue.get("owners") or []
            for owner in owners[1:]:
                port = find_port(snapshot, owner.get("device"), owner.get("port"))
                network = target_network
                if not network and port:
                    network = valid_ip_network(port.get("ip"), port.get("subnetMask"))
                if not network:
                    continue
                new_ip = next_available_ip(network, used, args.start_host)
                if not new_ip:
                    continue
                plan["pcConfigs"][owner.get("device")] = {
                    "ip": new_ip,
                    "mask": str(network.netmask),
                }
                if args.gateway:
                    plan["pcConfigs"][owner.get("device")]["gateway"] = args.gateway
        elif issue_type == "terminal-ip-missing" and target_network:
            device_name = issue.get("device")
            new_ip = next_available_ip(target_network, used, args.start_host)
            if new_ip:
                plan["pcConfigs"][device_name] = {
                    "ip": new_ip,
                    "mask": target_mask,
                }
                if args.gateway:
                    plan["pcConfigs"][device_name]["gateway"] = args.gateway

    configured_devices = set(plan["pcConfigs"].keys())
    plan["manualActions"] = [
        action
        for action in plan.get("manualActions", [])
        if not (action.get("type") == "assign-terminal-ip" and action.get("device") in configured_devices)
    ]
    return plan


def generate_repair_plan(audit):
    plan = {
        "pcConfigs": {},
        "moveDevices": [],
        "removeDevices": [],
        "removeLinks": [],
        "links": [],
        "manualActions": [],
        "sourceIssues": audit.get("issues", []),
    }
    for issue in audit.get("issues", []):
        issue_type = issue.get("type")
        if issue_type == "duplicate-ip":
            plan["manualActions"].append(
                {
                    "type": "choose-unique-ip",
                    "ip": issue.get("ip"),
                    "owners": issue.get("owners", []),
                    "hint": "Pick one owner to keep this IP, then add the others to pcConfigs with unique addresses.",
                }
            )
        elif issue_type == "terminal-ip-missing":
            plan["manualActions"].append(
                {
                    "type": "assign-terminal-ip",
                    "device": issue.get("device"),
                    "port": issue.get("port"),
                    "hint": "Add this device to pcConfigs with ip/mask/gateway.",
                }
            )
        elif issue_type in ("linked-port-down", "connected-port-down"):
            plan["manualActions"].append(
                {
                    "type": "inspect-or-reconnect-port",
                    "device": issue.get("device"),
                    "port": issue.get("port"),
                    "portUp": issue.get("portUp"),
                    "protocolUp": issue.get("protocolUp"),
                    "hint": "If the cable is wrong, add this port to removeLinks, then add the desired new link to links.",
                }
            )
        elif issue_type == "terminal-link-missing":
            plan["manualActions"].append(
                {
                    "type": "connect-terminal-port",
                    "device": issue.get("device"),
                    "port": issue.get("port"),
                    "ip": issue.get("ip"),
                    "mask": issue.get("mask"),
                    "hint": "Add the expected switch/router endpoint to links, for example [device, port, switch, switchPort, 'straight'].",
                }
            )
        elif issue_type == "link-endpoint-unresolved":
            plan["manualActions"].append(
                {
                    "type": "inspect-link",
                    "link": issue.get("link"),
                    "uuid": issue.get("uuid"),
                    "hint": "Packet Tracer did not expose both endpoints. Inspect the cable visually or redraw it.",
                }
            )
        else:
            plan["manualActions"].append({"type": "inspect-issue", "issue": issue})
    return plan


def plan_from_audit(args):
    snapshot = snapshot_from_args(args)
    audit = audit_snapshot(snapshot, args.subnet)
    plan = generate_repair_plan(audit)
    plan["generatedBy"] = "ptbuilder plan-from-audit"
    merge_layout_repair_plan(plan, snapshot, audit, args, "ptbuilder plan-from-audit layout")
    result = {"ok": True, "audit": audit, "plan": plan, "planFile": args.plan_file}
    if args.plan_file:
        with open(args.plan_file, "w", encoding="utf-8") as handle:
            json.dump(plan, handle, ensure_ascii=False, indent=2)
    return print_output(args, result)


def suggest_plan(args):
    snapshot = snapshot_from_args(args)
    audit = audit_snapshot(snapshot, args.subnet)
    plan = suggest_repair_plan(snapshot, audit, args)
    result = {"ok": True, "audit": audit, "plan": plan, "planFile": args.plan_file}
    if args.plan_file:
        with open(args.plan_file, "w", encoding="utf-8") as handle:
            json.dump(plan, handle, ensure_ascii=False, indent=2)
    return print_output(args, result)


def suggest_from_intent(args):
    intent = load_json_file(args.intent)
    if args.snapshot:
        snapshot_doc = load_json_file(args.snapshot)
        snapshot = snapshot_doc.get("snapshot", snapshot_doc)
    else:
        snapshot = snapshot_value(args)
    if not snapshot:
        raise RuntimeError("Missing snapshot data")

    audit = audit_snapshot(snapshot, None)
    plan = {
        "pcConfigs": {},
        "removeDevices": [],
        "removeLinks": [],
        "links": [],
        "manualActions": [],
        "sourceIssues": audit.get("issues", []),
        "generatedBy": "ptbuilder suggest-from-intent",
        "intent": {
            "name": intent.get("name"),
            "groups": list((intent.get("groups") or {}).keys()),
        },
    }

    used = used_ips(snapshot)
    planned_ports = set()
    connected = connected_port_keys(snapshot)
    group_summaries = []

    for group_name, group_config in (intent.get("groups") or {}).items():
        devices = group_devices(snapshot, group_config)
        subnet = group_config.get("subnet")
        network = ipaddress.ip_network(subnet, strict=False) if subnet else None
        gateway = group_config.get("gateway")
        dns = group_config.get("dns")
        start_host = int(group_config.get("startHost", 10))
        terminal_port_name = group_config.get("terminalPort", "FastEthernet0")
        link_policy = group_config.get("linkPolicy") or {}
        switch_name = link_policy.get("switch") or group_config.get("switch")
        port_range = link_policy.get("portRange") or group_config.get("portRange")
        link_type = link_policy.get("linkType", "straight")
        assigned = 0
        linked = 0

        for device in devices:
            device_name = device.get("name")
            port = first_usable_terminal_port(device, terminal_port_name)
            if not port:
                plan["manualActions"].append(
                    {
                        "type": "missing-terminal-port",
                        "group": group_name,
                        "device": device_name,
                        "hint": "No usable terminal Ethernet port was found.",
                    }
                )
                continue

            current_network = valid_ip_network(port.get("ip"), port.get("subnetMask"))
            needs_ip = bool(network and current_network != network)
            if needs_ip:
                new_ip = next_available_ip(network, used, start_host)
                if new_ip:
                    config = {"ip": new_ip, "mask": str(network.netmask)}
                    if gateway:
                        config["gateway"] = gateway
                    if dns:
                        config["dns"] = dns
                    plan["pcConfigs"][device_name] = config
                    assigned += 1
                else:
                    plan["manualActions"].append(
                        {
                            "type": "subnet-full",
                            "group": group_name,
                            "device": device_name,
                            "subnet": str(network),
                            "hint": "No free IP address was available in the group subnet.",
                        }
                    )

            terminal_key = port_key(device_name, port.get("name"))
            if switch_name and terminal_key not in connected:
                switch_port = find_free_switch_port(snapshot, switch_name, port_range, planned_ports)
                if switch_port:
                    plan["links"].append([device_name, port.get("name"), switch_name, switch_port, link_type])
                    planned_ports.add(terminal_key)
                    linked += 1
                else:
                    plan["manualActions"].append(
                        {
                            "type": "no-free-switch-port",
                            "group": group_name,
                            "device": device_name,
                            "switch": switch_name,
                            "portRange": port_range,
                            "hint": "No free switch port was found for this disconnected terminal.",
                        }
                    )

        group_summaries.append(
            {
                "group": group_name,
                "matched": len(devices),
                "pcConfigs": assigned,
                "links": linked,
                "subnet": str(network) if network else None,
                "switch": switch_name,
            }
        )

    plan["groupSummaries"] = group_summaries
    result = {"ok": True, "audit": audit, "plan": plan, "planFile": args.plan_file}
    if args.plan_file:
        with open(args.plan_file, "w", encoding="utf-8") as handle:
            json.dump(plan, handle, ensure_ascii=False, indent=2)
    return print_output(args, result)


def build_switch_vlan_configs(intent, snapshot):
    neighbors = link_neighbors(snapshot)
    configs = {}
    for group_name, group_config in (intent.get("groups") or {}).items():
        vlan = group_config.get("vlan") or (group_config.get("linkPolicy") or {}).get("vlan")
        if vlan is None:
            continue
        switch_name = (group_config.get("linkPolicy") or {}).get("switch") or group_config.get("switch")
        if not switch_name:
            continue
        vlan_name = group_config.get("vlanName") or group_name.upper().replace("-", "_")
        commands = configs.setdefault(switch_name, ["enable", "configure terminal"])
        commands.extend([f"vlan {vlan}", f"name {vlan_name}", "exit"])
        for device in group_devices(snapshot, group_config):
            device_name = device.get("name")
            for neighbor in neighbors.get(device_name, []):
                if neighbor.get("device") == switch_name and neighbor.get("remotePort"):
                    commands.extend(
                        [
                            f"interface {neighbor.get('remotePort')}",
                            "switchport mode access",
                            f"switchport access vlan {vlan}",
                            "exit",
                        ]
                    )
    return {name: "\n".join(commands + ["end"]) for name, commands in configs.items()}


def deploy_intent(args):
    intent = load_json_file(args.intent)
    snapshot = snapshot_value(args)

    class IntentArgs:
        pass

    suggest_args = IntentArgs()
    suggest_args.intent = args.intent
    suggest_args.snapshot = None
    suggest_args.plan_file = None
    # Reuse the plan builder without printing by inlining the important part.
    audit = audit_snapshot(snapshot, None)
    plan = {
        "pcConfigs": {},
        "removeDevices": [],
        "removeLinks": [],
        "links": [],
        "manualActions": [],
        "sourceIssues": audit.get("issues", []),
        "generatedBy": "ptbuilder deploy-intent",
        "intent": {"name": intent.get("name"), "groups": list((intent.get("groups") or {}).keys())},
    }
    used = used_ips(snapshot)
    planned_ports = set()
    connected = connected_port_keys(snapshot)
    for group_name, group_config in (intent.get("groups") or {}).items():
        network = ipaddress.ip_network(group_config.get("subnet"), strict=False) if group_config.get("subnet") else None
        gateway = group_config.get("gateway")
        dns = group_config.get("dns")
        start_host = int(group_config.get("startHost", 10))
        link_policy = group_config.get("linkPolicy") or {}
        switch_name = link_policy.get("switch") or group_config.get("switch")
        port_range = link_policy.get("portRange") or group_config.get("portRange")
        link_type = link_policy.get("linkType", "straight")
        for device in group_devices(snapshot, group_config):
            name = device.get("name")
            port = first_usable_terminal_port(device, group_config.get("terminalPort", "FastEthernet0"))
            if port and network and valid_ip_network(port.get("ip"), port.get("subnetMask")) != network:
                new_ip = next_available_ip(network, used, start_host)
                if new_ip:
                    config = {"ip": new_ip, "mask": str(network.netmask)}
                    if gateway:
                        config["gateway"] = gateway
                    if dns:
                        config["dns"] = dns
                    plan["pcConfigs"][name] = config
            if port and switch_name and port_key(name, port.get("name")) not in connected:
                switch_port = find_free_switch_port(snapshot, switch_name, port_range, planned_ports)
                if switch_port:
                    plan["links"].append([name, port.get("name"), switch_name, switch_port, link_type])

    if args.configure_ios:
        for switch_name, commands in build_switch_vlan_configs(intent, snapshot).items():
            plan.setdefault("iosConfigs", {})[switch_name] = commands

    if args.plan_file:
        with open(args.plan_file, "w", encoding="utf-8") as handle:
            json.dump(plan, handle, ensure_ascii=False, indent=2)

    if args.dry_run:
        return print_output(args, {"ok": True, "apply": dry_run_plan(args, plan)})

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
            json.dump(plan, handle, ensure_ascii=False, indent=2)
            temp_path = handle.name
        old_file = getattr(args, "file", None)
        args.file = temp_path
        apply_result = apply_lab_result(args)
        args.file = old_file
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass

    audit_result = audit_snapshot(snapshot_value(args), None) if args.audit else None
    intent_result = audit_intent_result(intent, snapshot_value(args)) if args.audit else None
    tests_result = None
    if args.test:
        tests = []
        failures = []
        current = snapshot_value(args)
        for policy in intent.get("policies", []):
            if policy.get("type") not in ("allow-subnet", "deny-subnet"):
                continue
            groups = audit_intent_result(intent, current).get("groups", {})
            expected_allow = policy.get("type") == "allow-subnet"
            for source in groups.get(policy.get("sourceGroup"), []):
                access = subnet_access_result(device_by_name(current), source, policy.get("destination"))
                ok = bool(access.get("ok") and access.get("allowed")) == expected_allow
                item = {"source": source, "destination": policy.get("destination"), "expected": "allow" if expected_allow else "deny", "ok": ok}
                tests.append(item)
                if not ok:
                    failures.append(item)
        tests_result = {"ok": len(failures) == 0, "tests": tests, "failures": failures}

    result = {"ok": bool(apply_result.get("ok") and (not audit_result or audit_result.get("ok")) and (not intent_result or intent_result.get("ok")) and (not tests_result or tests_result.get("ok"))), "apply": apply_result}
    if audit_result:
        result["audit"] = audit_result
    if intent_result:
        result["intentAudit"] = intent_result
    if tests_result:
        result["tests"] = tests_result
    return print_output(args, result)


def cidr_mask(subnet):
    return str(ipaddress.ip_network(subnet, strict=False).netmask)


def subnet_host(subnet, host_number):
    network = ipaddress.ip_network(subnet, strict=False)
    return str(network.network_address + int(host_number))


def slug_name(value):
    text = "".join(ch if ch.isalnum() else "_" for ch in str(value or "").upper())
    return "_".join(part for part in text.split("_") if part)


def int_config(value, default):
    try:
        return int(value)
    except Exception:
        return int(default)


def default_company_spec():
    return {
        "name": "ACME",
        "prefix": "ACME",
        "layout": {
            "departmentColumns": 3,
            "departmentOriginX": 80,
            "departmentOriginY": 620,
            "departmentWidth": 620,
            "departmentHeight": 560,
            "pcColumns": 5,
            "pcXSpacing": 95,
            "pcYSpacing": 85,
        },
        "coreSwitch": {"name": "ACME_CORE_SW1", "model": "2960-24TT", "x": 900, "y": 340},
        "departments": [
            {"name": "Marketing", "code": "MKT", "pcCount": 20, "subnet": "10.10.10.0/24", "gateway": "10.10.10.1", "vlan": 10, "x": 80, "y": 620},
            {"name": "HR", "code": "HR", "pcCount": 12, "subnet": "10.10.20.0/24", "gateway": "10.10.20.1", "vlan": 20, "x": 700, "y": 620},
            {"name": "Business", "code": "BIZ", "pcCount": 20, "subnet": "10.10.30.0/24", "gateway": "10.10.30.1", "vlan": 30, "x": 1320, "y": 620, "intranetAccess": True},
            {"name": "Finance", "code": "FIN", "pcCount": 10, "subnet": "10.10.40.0/24", "gateway": "10.10.40.1", "vlan": 40, "x": 80, "y": 1180},
            {"name": "Engineering", "code": "ENG", "pcCount": 20, "subnet": "10.10.50.0/24", "gateway": "10.10.50.1", "vlan": 50, "x": 700, "y": 1180},
        ],
        "servers": [
            {"name": "INTRANET", "model": "Server-PT", "ip": "10.10.30.100", "subnet": "10.10.30.0/24", "gateway": "10.10.30.1", "x": 700, "y": 160},
            {"name": "FTP", "model": "Server-PT", "ip": "10.10.30.110", "subnet": "10.10.30.0/24", "gateway": "10.10.30.1", "x": 900, "y": 160},
        ],
        "wireless": {"name": "GUEST_AP1", "apModel": "AccessPoint-PT-AC", "clientModel": "Laptop-PT", "clientCount": 4, "subnet": "10.10.60.0/24", "gateway": "10.10.60.1", "vlan": 60, "x": 1320, "y": 1180},
        "vpn": {"name": "VPN_REMOTE", "clientModel": "Laptop-PT", "clientCount": 2, "subnet": "10.10.70.0/24", "gateway": "10.10.70.1", "x": 1320, "y": 210},
    }


def normalize_company_spec(spec):
    base = default_company_spec()
    merged = dict(base)
    merged.update(spec or {})
    if "coreSwitch" in (spec or {}):
        core = dict(base.get("coreSwitch") or {})
        core.update(spec.get("coreSwitch") or {})
        merged["coreSwitch"] = core
    return merged


def build_company_artifacts(raw_spec):
    spec = normalize_company_spec(raw_spec)
    prefix = slug_name(spec.get("prefix") or spec.get("name") or "COMPANY")
    layout = spec.get("layout") or {}
    department_columns = max(1, int_config(layout.get("departmentColumns"), 3))
    department_origin_x = int_config(layout.get("departmentOriginX"), 80)
    department_origin_y = int_config(layout.get("departmentOriginY"), 620)
    department_width = int_config(layout.get("departmentWidth"), 620)
    department_height = int_config(layout.get("departmentHeight"), 430)
    default_pc_columns = max(1, int_config(layout.get("pcColumns"), 5))
    default_pc_x_spacing = max(70, int_config(layout.get("pcXSpacing"), 95))
    default_pc_y_spacing = max(65, int_config(layout.get("pcYSpacing"), 85))
    core = dict(spec.get("coreSwitch") or {})
    core_name = core.get("name") or f"{prefix}_CORE_SW1"
    core["name"] = core_name
    core.setdefault("model", "2960-24TT")
    core.setdefault("x", 760)
    core.setdefault("y", 260)

    plan = {
        "devices": [core],
        "removeDevices": [],
        "pcConfigs": {},
        "iosConfigs": {},
        "removeLinks": [],
        "links": [],
        "annotations": [],
        "metadata": {"company": spec.get("name"), "prefix": prefix, "schema": "ptbuilder.company-plan.v1"},
    }
    intent = {"name": f"{prefix.lower()}-company-intent", "groups": {}, "policies": [], "metadata": {"company": spec.get("name"), "prefix": prefix}}
    departments = []
    core_port = 1

    def add_core_link(device_name, device_port, link_type="straight"):
        nonlocal core_port
        if core_port > 24:
            return None
        switch_port = f"FastEthernet0/{core_port}"
        plan["links"].append([device_name, device_port, core_name, switch_port, link_type])
        core_port += 1
        return switch_port

    for index, dept in enumerate(spec.get("departments") or []):
        code = slug_name(dept.get("code") or dept.get("name") or f"DEPT{index + 1}")
        name = dept.get("name") or code
        subnet = dept.get("subnet")
        if not subnet:
            subnet = f"10.10.{10 + index * 10}.0/24"
        network = ipaddress.ip_network(subnet, strict=False)
        mask = str(network.netmask)
        gateway = dept.get("gateway") or subnet_host(subnet, 1)
        pc_count = int(dept.get("pcCount", 5))
        start_host = int(dept.get("startHost", 11))
        base_x = int_config(dept.get("x"), department_origin_x + (index % department_columns) * department_width)
        base_y = int_config(dept.get("y"), department_origin_y + (index // department_columns) * department_height)
        pc_columns = max(1, int_config(dept.get("pcColumns", dept.get("columns", layout.get("pcColumns"))), default_pc_columns))
        pc_x_spacing = max(70, int_config(dept.get("pcXSpacing", layout.get("pcXSpacing")), default_pc_x_spacing))
        pc_y_spacing = max(65, int_config(dept.get("pcYSpacing", layout.get("pcYSpacing")), default_pc_y_spacing))
        grid_width = max(0, min(pc_count, pc_columns) - 1) * pc_x_spacing
        switch_name = dept.get("switch") or f"{prefix}_{code}_SW1"
        switch_x = int_config(dept.get("switchX"), base_x + max(110, grid_width // 2))
        switch_y = int_config(dept.get("switchY"), base_y - 150)
        plan["devices"].append({"name": switch_name, "model": dept.get("switchModel", "2960-24TT"), "x": switch_x, "y": switch_y})
        uplink_port = dept.get("switchUplinkPort", "FastEthernet0/24")
        core_uplink = add_core_link(switch_name, uplink_port)
        devices = []
        ports = []
        for pc_index in range(pc_count):
            pc_name = f"{prefix}_{code}_PC{pc_index + 1:02d}"
            x = base_x + (pc_index % pc_columns) * pc_x_spacing
            y = base_y + (pc_index // pc_columns) * pc_y_spacing
            plan["devices"].append({"name": pc_name, "model": dept.get("model", "PC-PT"), "x": x, "y": y})
            host = start_host + pc_index
            if network.network_address + host not in network:
                plan["pcConfigs"][pc_name] = {"dhcp": True}
            else:
                plan["pcConfigs"][pc_name] = {"ip": subnet_host(subnet, host), "mask": mask, "gateway": gateway}
            switch_port = f"FastEthernet0/{pc_index + 1}"
            if pc_index + 1 <= 23:
                plan["links"].append([pc_name, dept.get("terminalPort", "FastEthernet0"), switch_name, switch_port, "straight"])
            else:
                switch_port = None
            if switch_port:
                ports.append(switch_port)
            devices.append(pc_name)
        group_name = code.lower()
        intent["groups"][group_name] = {
            "match": {"namePrefix": f"{prefix}_{code}_PC"},
            "terminalOnly": True,
            "terminalPort": dept.get("terminalPort", "FastEthernet0"),
            "subnet": str(network),
            "gateway": gateway,
            "startHost": start_host,
            "vlan": dept.get("vlan"),
            "linkPolicy": {"switch": switch_name, "portRange": f"FastEthernet0/1-23", "linkType": "straight"},
        }
        access_commands = ["enable", "configure terminal", f"hostname {switch_name}"]
        if dept.get("vlan") is not None:
            access_commands.extend([f"vlan {dept['vlan']}", f"name {code}", "exit"])
            for port_name in ports:
                access_commands.extend([f"interface {port_name}", "switchport mode access", f"switchport access vlan {dept['vlan']}", "exit"])
            access_commands.extend([f"interface {uplink_port}", "switchport mode access", f"switchport access vlan {dept['vlan']}", "exit"])
        plan["iosConfigs"][switch_name] = "\n".join(access_commands + ["end"])
        annotation = {
            "name": f"{prefix}_{code}_NOTE",
            "department": name,
            "text": f"{name}\nSubnet: {network}\nMask: {mask}\nGateway: {gateway}\nVLAN: {dept.get('vlan', 'n/a')}\nSwitch: {switch_name}",
            "x": base_x,
            "y": max(20, switch_y - 80),
        }
        plan["annotations"].append(annotation)
        departments.append({"name": name, "code": code, "group": group_name, "devices": len(devices), "subnet": str(network), "gateway": gateway, "vlan": dept.get("vlan"), "switch": switch_name, "corePort": core_uplink, "ports": ports[:3] + (["..."] if len(ports) > 3 else [])})

    server_names = []
    for server_index, server in enumerate(spec.get("servers") or []):
        code = slug_name(server.get("name") or f"SERVER{server_index + 1}")
        name = f"{prefix}_{code}" if not str(server.get("name", "")).upper().startswith(prefix + "_") else server.get("name")
        subnet = server.get("subnet", "10.10.30.0/24")
        mask = cidr_mask(subnet)
        plan["devices"].append({"name": name, "model": server.get("model", "Server-PT"), "x": server.get("x", 760 + server_index * 160), "y": server.get("y", 120)})
        plan["pcConfigs"][name] = {"ip": server.get("ip") or subnet_host(subnet, 100 + server_index), "mask": mask, "gateway": server.get("gateway") or subnet_host(subnet, 1)}
        add_core_link(name, server.get("terminalPort", "FastEthernet0"))
        server_names.append(name)

    wireless = spec.get("wireless") or {}
    if wireless:
        ap_name = f"{prefix}_{slug_name(wireless.get('name') or 'GUEST_AP1')}"
        subnet = wireless.get("subnet", "10.10.60.0/24")
        gateway = wireless.get("gateway") or subnet_host(subnet, 1)
        mask = cidr_mask(subnet)
        x = int(wireless.get("x", 1180))
        y = int(wireless.get("y", 170))
        plan["devices"].append({"name": ap_name, "model": wireless.get("apModel", "AccessPoint-PT-AC"), "x": x, "y": y})
        add_core_link(ap_name, wireless.get("apPort", "Ethernet0"))
        client_count = int(wireless.get("clientCount", 2))
        client_columns = max(1, int_config(wireless.get("clientColumns", layout.get("wirelessClientColumns")), 3))
        client_x_spacing = max(80, int_config(wireless.get("clientXSpacing", layout.get("wirelessClientXSpacing")), 105))
        client_y_spacing = max(75, int_config(wireless.get("clientYSpacing", layout.get("wirelessClientYSpacing")), 90))
        for client_index in range(client_count):
            client_name = f"{prefix}_WIFI_CLIENT{client_index + 1:02d}"
            plan["devices"].append(
                {
                    "name": client_name,
                    "model": wireless.get("clientModel", "Laptop-PT"),
                    "x": x + 130 + (client_index % client_columns) * client_x_spacing,
                    "y": y + 100 + (client_index // client_columns) * client_y_spacing,
                }
            )
            plan["pcConfigs"][client_name] = {"ip": subnet_host(subnet, 20 + client_index), "mask": mask, "gateway": gateway}
            add_core_link(client_name, "FastEthernet0")
        intent["groups"]["wireless"] = {"match": {"namePrefix": f"{prefix}_WIFI_CLIENT"}, "terminalOnly": True, "subnet": subnet, "gateway": gateway, "terminalPort": "FastEthernet0"}
        plan["annotations"].append({"name": f"{prefix}_WIRELESS_NOTE", "department": "Wireless", "text": f"Wireless\nSubnet: {subnet}\nMask: {mask}\nGateway: {gateway}\nSSID: {wireless.get('ssid', 'unverified')}", "x": x, "y": max(20, y - 70)})

    vpn = spec.get("vpn") or {}
    if vpn:
        subnet = vpn.get("subnet", "10.10.70.0/24")
        gateway = vpn.get("gateway") or subnet_host(subnet, 1)
        mask = cidr_mask(subnet)
        x = int(vpn.get("x", 1500))
        y = int(vpn.get("y", 130))
        count = int(vpn.get("clientCount", 1))
        vpn_columns = max(1, int_config(vpn.get("clientColumns", layout.get("vpnClientColumns")), 2))
        vpn_x_spacing = max(85, int_config(vpn.get("clientXSpacing", layout.get("vpnClientXSpacing")), 110))
        vpn_y_spacing = max(75, int_config(vpn.get("clientYSpacing", layout.get("vpnClientYSpacing")), 90))
        for vpn_index in range(count):
            name = f"{prefix}_VPN_CLIENT{vpn_index + 1:02d}"
            plan["devices"].append(
                {
                    "name": name,
                    "model": vpn.get("clientModel", "Laptop-PT"),
                    "x": x + (vpn_index % vpn_columns) * vpn_x_spacing,
                    "y": y + (vpn_index // vpn_columns) * vpn_y_spacing,
                }
            )
            plan["pcConfigs"][name] = {"ip": subnet_host(subnet, 20 + vpn_index), "mask": mask, "gateway": gateway}
            add_core_link(name, "FastEthernet0")
        intent["groups"]["vpn"] = {"match": {"namePrefix": f"{prefix}_VPN_CLIENT"}, "terminalOnly": True, "subnet": subnet, "gateway": gateway, "terminalPort": "FastEthernet0"}
        plan["annotations"].append({"name": f"{prefix}_VPN_NOTE", "department": "VPN", "text": f"External VPN\nSubnet: {subnet}\nMask: {mask}\nGateway: {gateway}\nAccess: intranet only (policy)", "x": x, "y": max(20, y - 70)})

    business_group = None
    for dept in departments:
        if dept["code"] in ("BIZ", "BUSINESS") or "business" in dept["name"].lower() or "业务" in dept["name"]:
            business_group = dept["group"]
            break
    intranet = next((name for name in server_names if "INTRANET" in name.upper()), server_names[0] if server_names else None)
    if intranet:
        for dept in departments:
            policy_type = "allow-subnet" if dept["group"] == business_group else "deny-subnet"
            intent["policies"].append({"type": policy_type, "sourceGroup": dept["group"], "destination": intranet})
        if "wireless" in intent["groups"]:
            intent["policies"].append({"type": "deny-subnet", "sourceGroup": "wireless", "destination": intranet})
        if "vpn" in intent["groups"]:
            intent["policies"].append({"type": "allow-vpn", "sourceGroup": "vpn", "destination": intranet, "verify": "unverified"})

    vlan_commands = ["enable", "configure terminal", f"hostname {core_name}"]
    for dept in departments:
        if dept.get("vlan") is not None:
            vlan_commands.extend([f"vlan {dept['vlan']}", f"name {dept['code']}", "exit"])
    for link in plan["links"]:
        if link[2] == core_name and str(link[3]).startswith("FastEthernet"):
            vlan = None
            for dept in departments:
                if link[0] == dept.get("switch"):
                    vlan = dept.get("vlan")
                    break
            if vlan is not None:
                vlan_commands.extend([f"interface {link[3]}", "switchport mode access", f"switchport access vlan {vlan}", "exit"])
    plan["iosConfigs"][core_name] = "\n".join(vlan_commands + ["end"])

    return {
        "ok": True,
        "schema": "ptbuilder.company.v1",
        "plan": plan,
        "intent": intent,
        "departments": departments,
        "policies": intent["policies"],
        "notes": {
            "serviceConfiguration": "FTP/HTTP/DNS/VPN service toggles are generated as topology intent and marked unverified until Packet Tracer exposes a stable WebView service API.",
            "capacity": f"Core switch {core_name} connects one access switch per department; each generated access switch uses ports FastEthernet0/1-23 for endpoints and FastEthernet0/24 as uplink.",
        },
        "summary": {"devices": len(plan["devices"]), "links": len(plan["links"]), "pcConfigs": len(plan["pcConfigs"]), "departments": len(departments), "annotations": len(plan["annotations"]), "policies": len(intent["policies"])},
    }


def company_plan(args):
    spec = load_json_file(args.file) if args.file else {}
    artifacts = build_company_artifacts(spec)
    files = {}
    if args.plan_file:
        with open(args.plan_file, "w", encoding="utf-8") as handle:
            json.dump(artifacts["plan"], handle, ensure_ascii=False, indent=2)
        files["plan"] = args.plan_file
    if args.intent_file:
        with open(args.intent_file, "w", encoding="utf-8") as handle:
            json.dump(artifacts["intent"], handle, ensure_ascii=False, indent=2)
        files["intent"] = args.intent_file
    artifacts["files"] = files
    return print_output(args, artifacts)


def company_deploy(args):
    spec = load_json_file(args.file) if args.file else {}
    artifacts = build_company_artifacts(spec)
    files = {}
    if args.plan_file:
        with open(args.plan_file, "w", encoding="utf-8") as handle:
            json.dump(artifacts["plan"], handle, ensure_ascii=False, indent=2)
        files["plan"] = args.plan_file
    if args.intent_file:
        with open(args.intent_file, "w", encoding="utf-8") as handle:
            json.dump(artifacts["intent"], handle, ensure_ascii=False, indent=2)
        files["intent"] = args.intent_file
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
            json.dump(artifacts["plan"], handle, ensure_ascii=False, indent=2)
            temp_path = handle.name
        old_file = getattr(args, "file", None)
        args.file = temp_path
        apply_result = dry_run_plan(args, artifacts["plan"]) if args.dry_run else apply_lab_result(args)
        args.file = old_file
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
    result = dict(artifacts)
    result["files"] = files
    result["apply"] = apply_result
    result["ok"] = bool(apply_result.get("ok"))
    if args.audit and not args.dry_run:
        snapshot = snapshot_value(args)
        result["audit"] = audit_snapshot(snapshot, None)
        result["companyAudit"] = company_audit_result(artifacts["intent"], snapshot)
        result["ok"] = bool(result["ok"] and result["audit"].get("ok") and result["companyAudit"].get("ok"))
    return print_output(args, result)


def company_audit_result(intent, snapshot):
    intent_audit = audit_intent_result(intent, snapshot)
    topology_audit = audit_snapshot(snapshot, None)
    departments = []
    for group_name, group_config in (intent.get("groups") or {}).items():
        devices = group_devices(snapshot, group_config)
        departments.append({"group": group_name, "expectedSubnet": group_config.get("subnet"), "gateway": group_config.get("gateway"), "devices": len(devices), "sample": [device.get("name") for device in devices[:5]]})
        if len(devices) == 0:
            expected = (group_config.get("match") or {}).get("namePrefix") or group_name
            intent_audit.setdefault("issues", []).append({"severity": "error", "type": "company-group-empty", "group": group_name, "expected": expected, "fix": "deploy-topology-or-refresh-bridge-snapshot"})
    unverified = compact_company_unverified(intent_audit.get("unverified") or [])
    if intent.get("policies"):
        unverified.append({"type": "acl-runtime-enforcement", "reason": "Packet Tracer WebView API does not expose reliable ACL/routing state readback yet; subnet policy is verified by IP placement and same-subnet reachability."})
    return {
        "ok": bool(topology_audit.get("ok") and not any(issue.get("severity") == "error" for issue in intent_audit.get("issues") or [])),
        "schema": "ptbuilder.company.audit.v1",
        "summary": {
            "devices": topology_audit.get("summary", {}).get("devices"),
            "links": topology_audit.get("summary", {}).get("links"),
            "departments": len(departments),
            "issues": len(topology_audit.get("issues") or []) + len(intent_audit.get("issues") or []),
            "unverified": len(unverified),
        },
        "departments": departments,
        "issues": (topology_audit.get("issues") or []) + (intent_audit.get("issues") or []),
        "unverified": unverified,
    }


def compact_company_unverified(items):
    grouped = {}
    passthrough = []
    for item in items:
        item_type = item.get("type") if isinstance(item, dict) else None
        if item_type in ("gateway-unavailable", "vlan-state-unavailable"):
            key = (item_type, item.get("group"), item.get("expected") or item.get("expectedVlan"))
            entry = grouped.setdefault(
                key,
                {
                    "type": item_type,
                    "group": item.get("group"),
                    "expected": item.get("expected") or item.get("expectedVlan"),
                    "count": 0,
                    "sample": [],
                },
            )
            entry["count"] += 1
            if item.get("device") and len(entry["sample"]) < 5:
                entry["sample"].append(item.get("device"))
        else:
            passthrough.append(item)
    return list(grouped.values()) + passthrough


def company_audit(args):
    intent = load_json_file(args.intent)
    if args.snapshot:
        snapshot_doc = load_json_file(args.snapshot)
        snapshot = snapshot_doc.get("snapshot", snapshot_doc)
    else:
        snapshot = snapshot_value(args)
    return print_output(args, company_audit_result(intent, snapshot))


def link_neighbors(snapshot):
    neighbors = {}
    for link in snapshot.get("links", []):
        if not isinstance(link, dict) or link.get("className") == "Antenna":
            continue
        endpoints = [endpoint for endpoint in link.get("endpoints", []) if isinstance(endpoint, dict)]
        if len(endpoints) != 2:
            continue
        left, right = endpoints
        neighbors.setdefault(left.get("device"), []).append({"device": right.get("device"), "localPort": left.get("port"), "remotePort": right.get("port")})
        neighbors.setdefault(right.get("device"), []).append({"device": left.get("device"), "localPort": right.get("port"), "remotePort": left.get("port")})
    return neighbors


def audit_intent(args):
    intent = load_json_file(args.intent)
    if args.snapshot:
        snapshot_doc = load_json_file(args.snapshot)
        snapshot = snapshot_doc.get("snapshot", snapshot_doc)
    else:
        snapshot = snapshot_value(args)
    return print_output(args, audit_intent_result(intent, snapshot))


def patch_from_plan(args):
    apply_result = apply_lab_result(args)
    audit_result = None
    if args.audit:
        audit_result = audit_snapshot(snapshot_value(args), None)
    result = {"ok": bool(apply_result.get("ok") and (audit_result is None or audit_result.get("ok"))), "apply": apply_result}
    if audit_result is not None:
        result["audit"] = audit_result
    return print_output(args, result)


def first_port(device, port_name):
    for port in device.get("ports") or []:
        if port.get("name") == port_name:
            return port
    return None


def ipv4_to_int(value):
    parts = [int(part) for part in value.split(".")]
    if len(parts) != 4:
        raise ValueError(value)
    result = 0
    for part in parts:
        if part < 0 or part > 255:
            raise ValueError(value)
        result = (result << 8) | part
    return result


def same_subnet(ip_a, mask_a, ip_b, mask_b):
    if not ip_a or not mask_a or not ip_b or not mask_b:
        return False
    try:
        mask = ipv4_to_int(mask_a)
        return mask_a == mask_b and (ipv4_to_int(ip_a) & mask) == (ipv4_to_int(ip_b) & mask)
    except Exception:
        return False


def get_pc_port(devices_by_name, name):
    device = devices_by_name.get(name)
    if not device:
        return None
    return first_port(device, "FastEthernet0")


def expected_access_allowed(test):
    if "allowed" in test:
        return bool(test.get("allowed"))
    expect = str(test.get("expect", "allow")).lower()
    return expect not in ("deny", "blocked", "false", "0", "no")


def subnet_access_result(devices_by_name, source, destination):
    src_port = get_pc_port(devices_by_name, source)
    dst_port = get_pc_port(devices_by_name, destination)
    if not src_port:
        return {"ok": False, "error": "source-missing"}
    if not dst_port:
        return {"ok": False, "error": "destination-missing"}
    allowed = same_subnet(
        src_port.get("ip"),
        src_port.get("subnetMask"),
        dst_port.get("ip"),
        dst_port.get("subnetMask"),
    )
    return {
        "ok": True,
        "allowed": allowed,
        "sourceIp": src_port.get("ip"),
        "sourceMask": src_port.get("subnetMask"),
        "destinationIp": dst_port.get("ip"),
        "destinationMask": dst_port.get("subnetMask"),
    }


def diagnose_lab(args):
    result = diagnose_lab_result(args)
    return print_output(args, result)


def diagnose_lab_result(args):
    spec = load_json_file(args.file)
    network = network_value(args)
    devices_by_name = {device.get("name"): device for device in network.get("devices", [])}
    issues = []

    for device in spec.get("devices", []):
        if device.get("name") not in devices_by_name:
            issues.append({"type": "missing-device", "device": device.get("name"), "fix": "apply"})

    for name, config in (spec.get("pcConfigs") or {}).items():
        device = devices_by_name.get(name)
        if not device:
            continue
        port = first_port(device, "FastEthernet0")
        if not port:
            issues.append({"type": "missing-port", "device": name, "port": "FastEthernet0", "fix": "check-device-model"})
            continue
        expected_ip = config.get("ip")
        expected_mask = config.get("mask") or config.get("subnetMask")
        if expected_ip and port.get("ip") != expected_ip:
            issues.append(
                {
                    "type": "pc-ip-mismatch",
                    "device": name,
                    "actual": port.get("ip"),
                    "expected": expected_ip,
                    "fix": "configure-pc",
                }
            )
        if expected_mask and port.get("subnetMask") != expected_mask:
            issues.append(
                {
                    "type": "pc-mask-mismatch",
                    "device": name,
                    "actual": port.get("subnetMask"),
                    "expected": expected_mask,
                    "fix": "configure-pc",
                }
            )
        if port.get("isPortUp") is False:
            issues.append({"type": "pc-port-down", "device": name, "port": "FastEthernet0", "fix": "check-link-or-power"})
        if port.get("isProtocolUp") is False:
            issues.append({"type": "pc-protocol-down", "device": name, "port": "FastEthernet0", "fix": "check-link-or-switch-port"})

    for test in spec.get("tests", []):
        test_type = test.get("type", "same-subnet-ping")
        if test_type not in ("same-subnet-ping", "subnet-access"):
            continue
        source = test.get("source")
        destination = test.get("destination")
        access = subnet_access_result(devices_by_name, source, destination)
        if access.get("error") == "source-missing":
            issues.append({"type": "test-source-missing", "source": source, "fix": "apply"})
            continue
        if access.get("error") == "destination-missing":
            issues.append({"type": "test-destination-missing", "destination": destination, "fix": "apply"})
            continue
        expected_allowed = expected_access_allowed(test)
        if access.get("allowed") != expected_allowed:
            issues.append(
                {
                    "type": "access-policy-mismatch" if test_type == "subnet-access" else "same-subnet-test-mismatch",
                    "source": source,
                    "destination": destination,
                    "actual": "allow" if access.get("allowed") else "deny",
                    "expected": "allow" if expected_allowed else "deny",
                    "sourceIp": access.get("sourceIp"),
                    "sourceMask": access.get("sourceMask"),
                    "destinationIp": access.get("destinationIp"),
                    "destinationMask": access.get("destinationMask"),
                    "fix": "align-ip-or-isolate-subnet",
                }
            )

    return {"ok": len(issues) == 0, "issues": issues}


def repair_lab(args):
    apply_result = apply_lab_result(args)
    diagnose_result = diagnose_lab_result(args)
    result = {
        "ok": bool(apply_result.get("ok") and diagnose_result.get("ok")),
        "apply": apply_result,
        "diagnose": diagnose_result,
    }
    return print_output(args, result)


def run_lab_tests_result(args, spec):
    result = {"ok": True, "tests": []}
    devices_by_name = None
    for index, test in enumerate(spec.get("tests", [])):
        test_type = test.get("type", "same-subnet-ping")
        source = test.get("source")
        destination = test.get("destination")
        item = {
            "index": index,
            "type": test_type,
            "source": source,
            "destination": destination,
            "ok": False,
        }
        if not source or not destination:
            item["error"] = "test requires source and destination"
            result["ok"] = False
            result["tests"].append(item)
            continue

        if test_type == "subnet-access":
            if devices_by_name is None:
                network = network_value(args)
                devices_by_name = {device.get("name"): device for device in network.get("devices", [])}
            access = subnet_access_result(devices_by_name, source, destination)
            expected_allowed = expected_access_allowed(test)
            item["expected"] = "allow" if expected_allowed else "deny"
            item["actual"] = "allow" if access.get("allowed") else "deny"
            item["ok"] = bool(access.get("ok") and access.get("allowed") == expected_allowed)
            item["result"] = {"ok": access.get("ok"), "value": access}
            if not item["ok"]:
                item["error"] = access.get("error") or "access-policy-mismatch"
                result["ok"] = False
            result["tests"].append(item)
            continue

        if test_type != "same-subnet-ping":
            item.update({"skipped": True, "reason": "unsupported-test-type", "ok": True})
            result["tests"].append(item)
            continue

        pdu_result = rpc_call(args, "sendPdu", {"source": source, "destination": destination})
        value = pdu_result.get("value")
        inner_ok = True
        if isinstance(value, dict) and "ok" in value:
            inner_ok = bool(value.get("ok"))
        item["result"] = pdu_result
        item["ok"] = bool(pdu_result.get("ok") and inner_ok)
        if not item["ok"]:
            result["ok"] = False
        result["tests"].append(item)
    return result


def validate_lab(args):
    spec = load_json_file(args.file)
    apply_result = apply_lab_result(args)
    diagnose_result = diagnose_lab_result(args) if apply_result.get("ok") else {"ok": False, "issues": []}
    tests_result = {"ok": True, "tests": []}
    if apply_result.get("ok") and diagnose_result.get("ok"):
        tests_result = run_lab_tests_result(args, spec)
    elif spec.get("tests"):
        tests_result = {
            "ok": False,
            "tests": [
                {
                    "index": index,
                    "type": test.get("type", "same-subnet-ping"),
                    "source": test.get("source"),
                    "destination": test.get("destination"),
                    "ok": False,
                    "skipped": True,
                    "reason": "diagnose-failed",
                }
                for index, test in enumerate(spec.get("tests", []))
            ],
        }
    result = {
        "ok": bool(apply_result.get("ok") and diagnose_result.get("ok") and tests_result.get("ok")),
        "apply": apply_result,
        "diagnose": diagnose_result,
        "tests": tests_result.get("tests", []),
    }
    return print_output(args, result)


def pc_link_test(args):
    code = """
var names = getDevices();

if (names.indexOf("PTB_TEST_PC1") < 0) {
    addDevice("PTB_TEST_PC1", "PC-PT", 100, 260);
}
if (names.indexOf("PTB_TEST_SW1") < 0) {
    addDevice("PTB_TEST_SW1", "2960-24TT", 280, 180);
}
if (names.indexOf("PTB_TEST_PC2") < 0) {
    addDevice("PTB_TEST_PC2", "PC-PT", 460, 260);
}

configurePcIp("PTB_TEST_PC1", false, "192.168.10.11", "255.255.255.0");
configurePcIp("PTB_TEST_PC2", false, "192.168.10.12", "255.255.255.0");

var linkResults = [];
linkResults.push(addLink("PTB_TEST_PC1", "FastEthernet0", "PTB_TEST_SW1", "FastEthernet0/1", "straight"));
linkResults.push(addLink("PTB_TEST_PC2", "FastEthernet0", "PTB_TEST_SW1", "FastEthernet0/2", "straight"));

var pduResult = { ok: false, skipped: true, reason: "PDU result serialization is not enabled yet." };
if (typeof sendSimplePdu == "function") {
    try {
        var pdu = sendSimplePdu("PTB_TEST_PC1", "PTB_TEST_PC2");
        pduResult = {
            ok: !!(pdu && pdu.ok),
            method: pdu && pdu.method ? String(pdu.method) : null,
            error: pdu && pdu.error ? String(pdu.error) : null
        };
    } catch (error) {
        pduResult = { ok: false, error: String(error && error.message ? error.message : error) };
    }
}

return {
    devices: [
        getDeviceInfo("PTB_TEST_PC1"),
        getDeviceInfo("PTB_TEST_SW1"),
        getDeviceInfo("PTB_TEST_PC2")
    ],
    linkResults: linkResults,
    pdu: pduResult
};
"""
    return print_result(submit_code(code, args))


def add_common_client_args(parser):
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    parser.add_argument("--timeout", default=30, type=float)
    parser.add_argument(
        "--require-connected",
        action="store_true",
        help="Fail before submitting if the Packet Tracer WebView is not polling the bridge",
    )
    parser.add_argument(
        "--transport",
        choices=["auto", "bridge", "ui"],
        default="auto",
        help="Automatically choose the bridge or visible Builder Code Editor automation",
    )
    parser.add_argument(
        "--output",
        choices=["summary", "json"],
        default="summary",
        help="Use compact agent-friendly output or full JSON",
    )


def add_pdu_result_args(parser):
    parser.add_argument(
        "--result-timeout",
        type=float,
        default=None,
        help="Seconds to wait for the Packet Tracer PDU list to show a success/failure result",
    )
    parser.add_argument(
        "--result-poll-interval",
        type=float,
        default=0.5,
        help="Seconds between PDU list polls while waiting for a result",
    )
    parser.add_argument(
        "--result-max-rows",
        type=int,
        default=200,
        help="Maximum PDU list rows to read while matching a result",
    )


def add_layout_args(parser, include_file=True, include_plan_file=True):
    if include_file:
        parser.add_argument("--file", help="Read a snapshot produced by export-current")
    if include_plan_file:
        parser.add_argument("--plan-file", help="Write the generated moveDevices repair plan")
    parser.add_argument("--origin-x", type=int, default=80)
    parser.add_argument("--origin-y", type=int, default=120)
    parser.add_argument("--group-width", type=int, default=620)
    parser.add_argument("--group-height", type=int, default=520)
    parser.add_argument("--columns", type=int, default=3)
    parser.add_argument("--device-columns", type=int, default=5)
    parser.add_argument("--x-spacing", type=int, default=95)
    parser.add_argument("--y-spacing", type=int, default=85)
    parser.add_argument("--min-distance", type=int, default=70)
    parser.add_argument("--name-prefix", help="Only relayout devices with this name prefix")
    parser.add_argument("--subnet", help="Only relayout devices with an IP in this subnet")
    parser.add_argument("--include-network-devices", action="store_true", help="Also move adjacent switches, routers, and APs")


def build_parser():
    parser = argparse.ArgumentParser(description="Packet Tracer Builder bridge CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Start the local bridge server")
    serve.add_argument("--host", default=DEFAULT_HOST)
    serve.add_argument("--port", default=DEFAULT_PORT, type=int)
    serve.set_defaults(func=start_server)

    launch = subparsers.add_parser("launch", help="Launch Cisco Packet Tracer")
    launch.add_argument("--pt-path")
    launch.add_argument("file", nargs="?")
    launch.set_defaults(func=launch_packet_tracer)

    status = subparsers.add_parser("status", help="Show bridge and Packet Tracer connection status")
    status.add_argument("--host", default=DEFAULT_HOST)
    status.add_argument("--port", default=DEFAULT_PORT, type=int)
    status.set_defaults(func=bridge_status)

    wait = subparsers.add_parser("wait-connected", help="Wait until the Packet Tracer WebView is polling")
    wait.add_argument("--host", default=DEFAULT_HOST)
    wait.add_argument("--port", default=DEFAULT_PORT, type=int)
    wait.add_argument("--timeout", default=30, type=float)
    wait.set_defaults(func=wait_connected)

    diag = subparsers.add_parser("doctor", help="Diagnose Packet Tracer and bridge setup")
    diag.add_argument("--host", default=DEFAULT_HOST)
    diag.add_argument("--port", default=DEFAULT_PORT, type=int)
    diag.add_argument("--pt-path")
    diag.set_defaults(func=doctor)

    verify_parser = subparsers.add_parser("verify-rpc", help="Verify the Packet Tracer Builder RPC functions loaded from the package")
    add_common_client_args(verify_parser)
    verify_parser.set_defaults(func=verify_rpc)

    probe_parser = subparsers.add_parser("probe-capabilities", help="Probe Packet Tracer API capabilities for services and IOS CLI")
    add_common_client_args(probe_parser)
    probe_parser.add_argument("--device", help="Probe a specific device")
    probe_parser.set_defaults(func=probe_capabilities)

    capability_parser = subparsers.add_parser("capability-scan", help="Build an agent-ready Packet Tracer capability manifest")
    add_common_client_args(capability_parser)
    capability_parser.add_argument("--device", help="Scan a specific device instead of representative devices")
    capability_parser.add_argument("--deep", action="store_true", help="Scan prototype chains, not just enumerable methods")
    capability_parser.add_argument("--limit", type=int, default=20, help="Maximum representative devices to scan")
    capability_parser.add_argument("--include-raw", action="store_true", help="Include full raw method lists in JSON output/file")
    capability_parser.add_argument("--file", help="Write the capability manifest to a JSON file")
    capability_parser.set_defaults(func=capability_scan)

    ui_inspect_parser = subparsers.add_parser("ui-inspect", help="Inspect Packet Tracer/Builder UI Automation tree")
    ui_inspect_parser.add_argument("--timeout", type=float, default=20)
    ui_inspect_parser.add_argument("--window", help="Window title substring to inspect")
    ui_inspect_parser.add_argument("--max-depth", type=int, default=4)
    ui_inspect_parser.add_argument("--max-children", type=int, default=250)
    ui_inspect_parser.add_argument("--file", help="Write the full UI tree JSON to a file")
    ui_inspect_parser.add_argument(
        "--output",
        choices=["summary", "json"],
        default="summary",
        help="Use compact agent-friendly output or full JSON",
    )
    ui_inspect_parser.set_defaults(func=ui_inspect)

    pdu_list_parser = subparsers.add_parser("pdu-list", help="Read Packet Tracer's user-created PDU result list through UI Automation")
    pdu_list_parser.add_argument("--timeout", type=float, default=20)
    pdu_list_parser.add_argument("--max-rows", type=int, default=200)
    pdu_list_parser.add_argument("--open-list", action="store_true", help="Click the Packet Tracer PDU list toggle if the list is hidden")
    pdu_list_parser.add_argument(
        "--output",
        choices=["summary", "json"],
        default="summary",
        help="Use compact agent-friendly output or full JSON",
    )
    pdu_list_parser.set_defaults(func=pdu_list)

    ui_ftp_parser = subparsers.add_parser("ui-server-ftp", help="Configure Server-PT FTP through the verified UI automation recipe")
    add_common_client_args(ui_ftp_parser)
    ui_ftp_parser.add_argument("--device", required=True)
    ui_ftp_parser.add_argument("--username", required=True)
    ui_ftp_parser.add_argument("--password", required=True)
    ui_ftp_parser.add_argument("--permission", default="RWDNL")
    ui_ftp_parser.add_argument("--enabled", choices=["true", "false", "1", "0", "yes", "no", "on", "off"], default="true")
    ui_ftp_parser.add_argument("--logical-x", type=float)
    ui_ftp_parser.add_argument("--logical-y", type=float)
    ui_ftp_parser.set_defaults(func=ui_server_ftp)

    operation_plan_parser = subparsers.add_parser("operation-plan", help="Plan service, wireless, VPN, ACL, CLI, PC config, and test operations")
    add_common_client_args(operation_plan_parser)
    operation_plan_parser.add_argument("file", help="Operation request JSON")
    operation_plan_parser.add_argument("--capabilities", help="Capability manifest JSON from capability-scan")
    operation_plan_parser.add_argument("--refresh-capabilities", action="store_true", help="Refresh examples/capabilities_pt822.json instead of reusing it")
    operation_plan_parser.add_argument("--capability-limit", type=int, default=12, help="Representative devices to scan when refreshing capabilities")
    operation_plan_parser.add_argument("--plan-file", help="Write the adapter operation plan JSON")
    operation_plan_parser.set_defaults(func=operation_plan)

    operation_apply_parser = subparsers.add_parser("operation-apply", help="Apply only stable adapter operations from an operation request or plan")
    add_common_client_args(operation_apply_parser)
    operation_apply_parser.add_argument("file", help="Operation request JSON or operation-plan JSON")
    operation_apply_parser.add_argument("--capabilities", help="Capability manifest JSON from capability-scan")
    operation_apply_parser.add_argument("--refresh-capabilities", action="store_true", help="Refresh examples/capabilities_pt822.json instead of reusing it")
    operation_apply_parser.add_argument("--capability-limit", type=int, default=12, help="Representative devices to scan when refreshing capabilities")
    operation_apply_parser.add_argument("--allow-partial", action="store_true", help="Return success when blocked operations are skipped but stable operations succeed")
    operation_apply_parser.add_argument("--dry-run", action="store_true", help="Show what would be applied or verified without changing Packet Tracer")
    operation_apply_parser.set_defaults(func=operation_apply)

    run = subparsers.add_parser("run", help="Run a JavaScript file in Packet Tracer")
    add_common_client_args(run)
    run.add_argument("file")
    run.set_defaults(func=run_file)

    inline = subparsers.add_parser("eval", help="Run inline JavaScript in Packet Tracer")
    add_common_client_args(inline)
    inline.add_argument("code")
    inline.set_defaults(func=run_inline)

    add = subparsers.add_parser("add-device")
    add_common_client_args(add)
    add.add_argument("name")
    add.add_argument("model")
    add.add_argument("x", type=int)
    add.add_argument("y", type=int)
    add.set_defaults(func=add_device)

    ensure_device_parser = subparsers.add_parser("ensure-device")
    add_common_client_args(ensure_device_parser)
    ensure_device_parser.add_argument("name")
    ensure_device_parser.add_argument("model")
    ensure_device_parser.add_argument("x", type=int)
    ensure_device_parser.add_argument("y", type=int)
    ensure_device_parser.set_defaults(func=add_device)

    move_device_parser = subparsers.add_parser("move-device", help="Move an existing device to a logical workspace coordinate")
    add_common_client_args(move_device_parser)
    move_device_parser.add_argument("name")
    move_device_parser.add_argument("x", type=int)
    move_device_parser.add_argument("y", type=int)
    move_device_parser.add_argument("--top-left", action="store_true", help="Use moveToLocation instead of centered coordinates")
    move_device_parser.set_defaults(func=move_device)

    link = subparsers.add_parser("add-link")
    add_common_client_args(link)
    link.add_argument("device1")
    link.add_argument("interface1")
    link.add_argument("device2")
    link.add_argument("interface2")
    link.add_argument("link_type")
    link.set_defaults(func=add_link)

    ensure_link_parser = subparsers.add_parser("ensure-link")
    add_common_client_args(ensure_link_parser)
    ensure_link_parser.add_argument("device1")
    ensure_link_parser.add_argument("interface1")
    ensure_link_parser.add_argument("device2")
    ensure_link_parser.add_argument("interface2")
    ensure_link_parser.add_argument("link_type")
    ensure_link_parser.set_defaults(func=add_link)

    remove_link_parser = subparsers.add_parser("remove-link")
    add_common_client_args(remove_link_parser)
    remove_link_parser.add_argument("device")
    remove_link_parser.add_argument("interface")
    remove_link_parser.set_defaults(func=remove_link)

    remove_device_parser = subparsers.add_parser("remove-device")
    add_common_client_args(remove_device_parser)
    remove_device_parser.add_argument("name")
    remove_device_parser.set_defaults(func=remove_device)

    pc = subparsers.add_parser("configure-pc")
    add_common_client_args(pc)
    pc.add_argument("name")
    pc.add_argument("--dhcp", action="store_true")
    pc.add_argument("--ip")
    pc.add_argument("--mask")
    pc.add_argument("--gateway")
    pc.add_argument("--dns")
    pc.set_defaults(func=configure_pc)

    ios = subparsers.add_parser("configure-ios")
    add_common_client_args(ios)
    ios.add_argument("name")
    ios.add_argument("file")
    ios.set_defaults(func=configure_ios)

    network = subparsers.add_parser("get-network")
    add_common_client_args(network)
    network.set_defaults(func=get_network)

    export_parser = subparsers.add_parser("export-current", help="Export the current Packet Tracer topology snapshot")
    add_common_client_args(export_parser)
    export_parser.add_argument("--file", help="Write the full snapshot JSON to a file")
    export_parser.add_argument("--subnet", help="Limit the audit subnet summary, for example 192.168.30.0/24")
    export_parser.set_defaults(func=export_current)

    audit_parser = subparsers.add_parser("audit", help="Audit the current topology or an exported snapshot")
    add_common_client_args(audit_parser)
    audit_parser.add_argument("--file", help="Read a snapshot produced by export-current")
    audit_parser.add_argument("--subnet", help="Only include this subnet in the subnet summary")
    audit_parser.set_defaults(func=audit_current)

    layout_parser = subparsers.add_parser("layout-plan", help="Generate a moveDevices-only plan to spread out the current topology")
    add_common_client_args(layout_parser)
    add_layout_args(layout_parser)
    layout_parser.set_defaults(func=layout_plan)

    relayout_parser = subparsers.add_parser("relayout-current", help="Generate and apply a layout repair plan for the current topology")
    add_common_client_args(relayout_parser)
    add_layout_args(relayout_parser)
    relayout_parser.add_argument("--dry-run", action="store_true", help="Show planned move operations without changing Packet Tracer")
    relayout_parser.add_argument("--audit", action="store_true", help="Audit the topology after moving devices")
    relayout_parser.set_defaults(func=relayout_current)

    find_parser = subparsers.add_parser("find-device", help="Find devices by name, model, IP, subnet, or link state")
    add_common_client_args(find_parser)
    find_parser.add_argument("--file", help="Read a snapshot produced by export-current")
    find_parser.add_argument("--name")
    find_parser.add_argument("--name-prefix")
    find_parser.add_argument("--name-contains")
    find_parser.add_argument("--model")
    find_parser.add_argument("--model-contains")
    find_parser.add_argument("--ip")
    find_parser.add_argument("--subnet")
    find_parser.add_argument("--terminal", action="store_true")
    find_parser.add_argument("--disconnected", action="store_true")
    find_parser.add_argument("--limit", type=int, default=100)
    find_parser.set_defaults(func=find_devices)

    plan_parser = subparsers.add_parser("plan-from-audit", help="Generate a repair plan skeleton from an audit")
    add_common_client_args(plan_parser)
    plan_parser.add_argument("--file", help="Read a snapshot produced by export-current")
    plan_parser.add_argument("--subnet", help="Only plan around this subnet")
    plan_parser.add_argument("--plan-file", help="Write the repair plan skeleton to a file")
    plan_parser.set_defaults(func=plan_from_audit)

    suggest_parser = subparsers.add_parser("suggest-plan", help="Generate an executable repair plan where safe")
    add_common_client_args(suggest_parser)
    suggest_parser.add_argument("--file", help="Read a snapshot produced by export-current")
    suggest_parser.add_argument("--subnet", help="Constrain automatic IP suggestions to this subnet")
    suggest_parser.add_argument("--gateway", help="Gateway to include in suggested pcConfigs")
    suggest_parser.add_argument("--start-host", type=int, default=10, help="First host number to try when assigning IPs")
    suggest_parser.add_argument("--plan-file", help="Write the suggested repair plan to a file")
    suggest_parser.set_defaults(func=suggest_plan)

    intent_parser = subparsers.add_parser("suggest-from-intent", help="Generate a repair plan from a desired topology intent")
    add_common_client_args(intent_parser)
    intent_parser.add_argument("intent")
    intent_parser.add_argument("--snapshot", help="Read a snapshot produced by export-current")
    intent_parser.add_argument("--plan-file", help="Write the suggested repair plan to a file")
    intent_parser.set_defaults(func=suggest_from_intent)

    audit_intent_parser = subparsers.add_parser("audit-intent", help="Audit gateway, subnet, link, and policy intent")
    add_common_client_args(audit_intent_parser)
    audit_intent_parser.add_argument("intent")
    audit_intent_parser.add_argument("--snapshot", help="Read a snapshot produced by export-current")
    audit_intent_parser.set_defaults(func=audit_intent)

    deep_parser = subparsers.add_parser("deep-diagnose", help="Run topology and intent diagnostics with grouped root causes")
    add_common_client_args(deep_parser)
    deep_parser.add_argument("--intent")
    deep_parser.add_argument("--subnet")
    deep_parser.set_defaults(func=deep_diagnose)

    deploy_parser = subparsers.add_parser("deploy-intent", help="Generate and apply a plan from intent, optionally with IOS VLAN config")
    add_common_client_args(deploy_parser)
    deploy_parser.add_argument("intent")
    deploy_parser.add_argument("--configure-ios", action="store_true", help="Generate switch VLAN/access-port IOS config from intent")
    deploy_parser.add_argument("--dry-run", action="store_true")
    deploy_parser.add_argument("--audit", action="store_true")
    deploy_parser.add_argument("--test", action="store_true")
    deploy_parser.add_argument("--plan-file")
    deploy_parser.add_argument("--force", action="store_true")
    deploy_parser.add_argument("--max-delete", type=int, default=3)
    deploy_parser.set_defaults(func=deploy_intent)

    company_plan_parser = subparsers.add_parser("company-plan", help="Generate a company topology plan and intent from a high-level spec")
    add_common_client_args(company_plan_parser)
    company_plan_parser.add_argument("file", nargs="?", help="Company spec JSON; uses a five-department default when omitted")
    company_plan_parser.add_argument("--plan-file", help="Write generated topology/config plan JSON")
    company_plan_parser.add_argument("--intent-file", help="Write generated audit intent JSON")
    company_plan_parser.set_defaults(func=company_plan)

    company_deploy_parser = subparsers.add_parser("company-deploy", help="Deploy a company topology plan and optionally audit it")
    add_common_client_args(company_deploy_parser)
    company_deploy_parser.add_argument("file", nargs="?", help="Company spec JSON; uses a five-department default when omitted")
    company_deploy_parser.add_argument("--plan-file", help="Write generated topology/config plan JSON")
    company_deploy_parser.add_argument("--intent-file", help="Write generated audit intent JSON")
    company_deploy_parser.add_argument("--dry-run", action="store_true")
    company_deploy_parser.add_argument("--relayout", action="store_true", help="Move existing generated devices to the planned coordinates")
    company_deploy_parser.add_argument("--audit", action="store_true")
    company_deploy_parser.add_argument("--force", action="store_true")
    company_deploy_parser.add_argument("--max-delete", type=int, default=3)
    company_deploy_parser.set_defaults(func=company_deploy)

    company_audit_parser = subparsers.add_parser("company-audit", help="Audit a deployed company topology against a generated intent")
    add_common_client_args(company_audit_parser)
    company_audit_parser.add_argument("intent")
    company_audit_parser.add_argument("--snapshot", help="Read a snapshot produced by export-current")
    company_audit_parser.set_defaults(func=company_audit)

    patch_parser = subparsers.add_parser("patch-from-plan", help="Apply a repair plan JSON and optionally audit afterwards")
    add_common_client_args(patch_parser)
    patch_parser.add_argument("file")
    patch_parser.add_argument("--audit", action="store_true", help="Audit the topology after applying the plan")
    patch_parser.add_argument("--dry-run", action="store_true", help="Show planned operations without changing Packet Tracer")
    patch_parser.add_argument("--relayout", action="store_true", help="Move existing devices listed in the plan to the plan coordinates")
    patch_parser.add_argument("--force", action="store_true", help="Allow risky deletes")
    patch_parser.add_argument("--max-delete", type=int, default=3, help="Maximum device deletes allowed without --force")
    patch_parser.set_defaults(func=patch_from_plan)

    device = subparsers.add_parser("get-device")
    add_common_client_args(device)
    device.add_argument("name")
    device.set_defaults(func=get_device)

    inspect_device_parser = subparsers.add_parser("inspect-device")
    add_common_client_args(inspect_device_parser)
    inspect_device_parser.add_argument("name")
    inspect_device_parser.set_defaults(func=get_device)

    pdu = subparsers.add_parser("ping")
    add_common_client_args(pdu)
    pdu.add_argument("source")
    pdu.add_argument("destination")
    pdu.add_argument("--wait-result", action="store_true", help="Wait for Packet Tracer's PDU list to report success/failure")
    add_pdu_result_args(pdu)
    pdu.set_defaults(func=ping)

    matrix_parser = subparsers.add_parser("test-matrix", help="Run compact batch connectivity tests")
    add_common_client_args(matrix_parser)
    matrix_parser.add_argument("--file", help="Read a snapshot produced by export-current")
    matrix_parser.add_argument("--sources", help="Comma separated source device names")
    matrix_parser.add_argument("--source-prefix")
    matrix_parser.add_argument("--source-subnet")
    matrix_parser.add_argument("--destination", required=True)
    matrix_parser.add_argument("--mode", choices=["pdu", "pdu-result", "same-subnet"], default="same-subnet")
    matrix_parser.add_argument("--expect", choices=["allow", "deny"], default="allow")
    matrix_parser.add_argument("--show-passed", action="store_true")
    add_pdu_result_args(matrix_parser)
    matrix_parser.set_defaults(func=test_matrix)

    apply_parser = subparsers.add_parser("apply", help="Apply a topology/config JSON spec")
    add_common_client_args(apply_parser)
    apply_parser.add_argument("file")
    apply_parser.add_argument("--dry-run", action="store_true", help="Show planned operations without changing Packet Tracer")
    apply_parser.add_argument("--relayout", action="store_true", help="Move existing devices listed in the spec to the spec coordinates")
    apply_parser.add_argument("--force", action="store_true", help="Allow risky deletes")
    apply_parser.add_argument("--max-delete", type=int, default=3, help="Maximum device deletes allowed without --force")
    apply_parser.set_defaults(func=apply_lab)

    diagnose_parser = subparsers.add_parser("diagnose", help="Compare current Packet Tracer state to a JSON spec")
    add_common_client_args(diagnose_parser)
    diagnose_parser.add_argument("file")
    diagnose_parser.set_defaults(func=diagnose_lab)

    repair_parser = subparsers.add_parser("repair", help="Apply a JSON spec and verify configuration-level issues")
    add_common_client_args(repair_parser)
    repair_parser.add_argument("file")
    repair_parser.set_defaults(func=repair_lab)

    validate_parser = subparsers.add_parser("validate", help="Apply, diagnose, and run declared tests")
    add_common_client_args(validate_parser)
    validate_parser.add_argument("file")
    validate_parser.set_defaults(func=validate_lab)

    lab = subparsers.add_parser(
        "pc-link-test",
        help="Create or reuse a minimal PC1-S1-PC2 topology and test PC1 to PC2",
    )
    add_common_client_args(lab)
    lab.set_defaults(func=pc_link_test)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args) or 0
    except ConnectionRefusedError:
        print("Bridge server is not running. Start it with: python ptbuilder.py serve", file=sys.stderr)
        return 2
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
