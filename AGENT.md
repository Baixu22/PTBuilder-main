# Agent Quickstart

This repo turns the Packet Tracer Builder `.pts` module into a CLI-driven
automation tool. The main entry point is `ptbuilder.py`; the Packet Tracer app
is the source of truth for live topology state.

## Non-Negotiables

- Run every shell command through `rtk` in this workspace.
- Prefer `--output summary` for normal work. Use `--output json` only when the
  full payload is needed.
- Keep Packet Tracer open when using live commands.
- Keep **Extensions > Builder Code Editor** open when using the bridge or UI
  fallback.
- Do not treat `sendPdu` submission as proof of connectivity. Use
  `--wait-result` or `--mode pdu-result` when the real Packet Tracer result is
  required.

## First 5 Minutes

1. Check whether Packet Tracer and the bridge are reachable:

   ```powershell
   rtk python ptbuilder.py status
   rtk python ptbuilder.py doctor
   ```

2. If Packet Tracer is not running, launch it:

   ```powershell
   rtk python ptbuilder.py launch
   ```

   If the install path is custom:

   ```powershell
   rtk python ptbuilder.py launch --pt-path "D:\software\Cisco Packet Tracer 8.2.2\bin\PacketTracer.exe"
   ```

3. If the local bridge server is not running, start it in a dedicated terminal:

   ```powershell
   rtk python ptbuilder.py serve
   ```

4. In Packet Tracer, install or load [ptbuilder-bridge.pts](ptbuilder-bridge.pts),
   then open **Extensions > Builder Code Editor** and keep the CLI bridge
   enabled.

5. Wait for the WebView bridge:

   ```powershell
   rtk python ptbuilder.py wait-connected
   rtk python ptbuilder.py status
   ```

If bridge mode is unavailable, the CLI can still use visible UI automation.
Keep the Builder Code Editor window visible and let `--transport auto` choose.

## Mental Model

- `ptbuilder.py serve` runs the local bridge server.
- Packet Tracer's Builder WebView polls the bridge and executes JavaScript.
- CLI commands call Packet Tracer through the bridge when available.
- UI fallback uses PowerShell UI Automation to drive visible Packet Tracer
  windows.
- `examples/` stores reusable topology plans, snapshots, repair plans, and
  capability manifests.

## Most Useful Commands

Inspect live state:

```powershell
rtk python ptbuilder.py status
rtk python ptbuilder.py get-network --output summary
rtk python ptbuilder.py export-current --file examples\current_snapshot.json --output summary
rtk python ptbuilder.py audit --output summary
```

Create simple topology objects:

```powershell
rtk python ptbuilder.py add-device R1 2911 100 100 --output summary
rtk python ptbuilder.py add-device PC1 PC-PT 300 100 --output summary
rtk python ptbuilder.py add-link R1 GigabitEthernet0/1 PC1 FastEthernet0 straight --output summary
rtk python ptbuilder.py configure-pc PC1 --ip 192.168.1.10 --mask 255.255.255.0 --gateway 192.168.1.1 --output summary
```

Apply or dry-run a plan:

```powershell
rtk python ptbuilder.py apply examples\small_company_lab.json --dry-run --output summary
rtk python ptbuilder.py apply examples\small_company_lab.json --output summary
```

Find and inspect devices:

```powershell
rtk python ptbuilder.py find-device --name PC --output summary
rtk python ptbuilder.py inspect-device PC1 --output summary
```

## Layout Repair Workflow

Use this when devices are stacked together and UI automation cannot click the
right device reliably.

```powershell
rtk python ptbuilder.py layout-plan --plan-file examples\layout_repair_plan.json --output summary
rtk python ptbuilder.py patch-from-plan examples\layout_repair_plan.json --dry-run --output summary
rtk python ptbuilder.py relayout-current --audit --timeout 30 --output summary
```

Add `--include-network-devices` when switches, routers, or APs should move with
the endpoint groups.

## Connectivity Testing

Fast submission-only PDU check:

```powershell
rtk python ptbuilder.py ping PC1 PC2 --output summary
```

Real Packet Tracer PDU result check:

```powershell
rtk python ptbuilder.py ping PC1 PC2 --wait-result --timeout 20 --output summary
```

Read the current user-created PDU list:

```powershell
rtk python ptbuilder.py pdu-list --open-list --output summary
```

Batch connectivity check with real Packet Tracer PDU status:

```powershell
rtk python ptbuilder.py test-matrix --sources PC1,PC2 --destination SERVER1 --mode pdu-result --show-passed --timeout 30 --output summary
```

Offline same-subnet policy check from a snapshot:

```powershell
rtk python ptbuilder.py test-matrix --file examples\current_snapshot.json --destination SERVER1 --mode same-subnet --output summary
```

## Repair Loop

Use this loop for most existing Packet Tracer files:

```powershell
rtk python ptbuilder.py export-current --file examples\current_snapshot.json --output summary
rtk python ptbuilder.py audit --output summary
rtk python ptbuilder.py suggest-plan --plan-file examples\suggested_repair_plan.json --output summary
rtk python ptbuilder.py patch-from-plan examples\suggested_repair_plan.json --dry-run --output summary
rtk python ptbuilder.py patch-from-plan examples\suggested_repair_plan.json --audit --output summary
```

## Troubleshooting

- `status` says Packet Tracer is not running: launch Packet Tracer with
  `ptbuilder.py launch` or open it manually.
- `status` says bridge is not connected: open **Builder Code Editor**, enable
  the CLI bridge, then run `wait-connected`.
- UI fallback fails to click devices: run `relayout-current --audit` first.
- PDU submission is OK but connectivity is uncertain: rerun with
  `ping --wait-result`.
- `pdu-list` cannot find the list: use `--open-list` and keep Packet Tracer
  visible.
- Output is too large: use `--output summary`.
- You need exact fields for debugging: rerun the same command with
  `--output json`.

## Project Map

- `ptbuilder.py`: main CLI, bridge server, topology planner, audit tool, and
  live Packet Tracer automation entry point.
- `ptbuilder-bridge.pts`: recommended Packet Tracer script module package for
  bridge-driven automation.
- `Builder.pts`: original Builder package kept for compatibility.
- `source/`: Packet Tracer Builder JavaScript module and WebView UI.
- `tools/`: PowerShell and JavaScript helpers for UI automation, diagnostics,
  hot patches, and PDU result reads.
- `examples/`: topology plans, snapshots, capability manifests, and operation
  requests.

## Validation Checklist

Run checks that match the files you changed:

```powershell
rtk python -m py_compile ptbuilder.py
rtk node --check source\userfunctions.js
rtk powershell -NoProfile -Command "[scriptblock]::Create((Get-Content -Raw -LiteralPath 'tools\ptbuilder_ui_pdu_list.ps1')) | Out-Null; Write-Output 'parse-ok'"
```

