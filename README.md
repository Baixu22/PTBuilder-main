# PTBuilder Main

PTBuilder Main is a Cisco Packet Tracer 8.2.x automation workspace built on
top of Packet Tracer Builder. It keeps the original JavaScript-based Builder
extension, then adds a local Python CLI, bridge server, UI automation helpers,
topology planning, auditing, layout repair, and result-aware connectivity
checks.

The goal is to let an agent or engineer move from a high-level network intent
to a real Packet Tracer topology without repeatedly pasting JavaScript into the
Packet Tracer UI by hand.

## What It Can Do

- Create devices, links, PC IP settings, IOS CLI snippets, and company-style
  topology plans.
- Export the current Packet Tracer topology into JSON.
- Audit existing topologies for missing IPs, duplicate IPs, broken links,
  invalid gateways, close device placement, and related issues.
- Generate and apply repair plans.
- Relayout crowded existing topologies so UI automation can target devices
  more reliably.
- Drive Packet Tracer through a WebView bridge or a visible UI automation
  fallback.
- Read Packet Tracer's user-created PDU list and report actual ICMP
  success/failure status.

## Repository Layout

```text
ptbuilder.py              Main CLI, bridge server, audit, planning, and tests
source/                   Packet Tracer Builder JavaScript module and UI
tools/                    UI automation, diagnostics, and helper scripts
examples/                 Plans, snapshots, capability manifests, and requests
Builder.pts               Original Builder script module package
ptbuilder-bridge.pts      Builder package with bridge support
AGENT.md                  Notes for automation agents working in this repo
```

## Requirements

- Windows with Cisco Packet Tracer 8.2.x installed.
- Python 3.10+.
- PowerShell for the UI automation helpers.
- Optional: Node.js for checking JavaScript files.
- Optional: GitHub CLI (`gh`) for repository and PR workflows.

The default Packet Tracer path used by the CLI is:

```text
D:\software\Cisco Packet Tracer 8.2.2\bin\PacketTracer.exe
```

Use `--pt-path` when Packet Tracer is installed somewhere else.

## Packet Tracer Setup

1. Open Cisco Packet Tracer.
2. Go to **Extensions** > **Scripting** > **Configure PT Script Modules**.
3. Add [ptbuilder-bridge.pts](ptbuilder-bridge.pts).
4. Open **Extensions** > **Builder Code Editor**.
5. Keep the CLI bridge enabled when using bridge mode.

## Quick Start

Start the local bridge server:

```powershell
python ptbuilder.py serve
```

Launch Packet Tracer if needed:

```powershell
python ptbuilder.py launch
```

Check connection health:

```powershell
python ptbuilder.py wait-connected
python ptbuilder.py status
python ptbuilder.py doctor
```

Create a small topology:

```powershell
python ptbuilder.py add-device R1 2911 100 100
python ptbuilder.py add-device PC1 PC-PT 300 100
python ptbuilder.py add-link R1 GigabitEthernet0/1 PC1 FastEthernet0 straight
```

Export and audit the current topology:

```powershell
python ptbuilder.py export-current --file examples\current_snapshot.json --output summary
python ptbuilder.py audit --output summary
```

## Layout Repair

For existing topologies where devices are stacked too tightly, generate and
apply a layout repair:

```powershell
python ptbuilder.py layout-plan --plan-file examples\layout_repair_plan.json --output summary
python ptbuilder.py patch-from-plan examples\layout_repair_plan.json --dry-run --output summary
python ptbuilder.py relayout-current --audit --timeout 30 --output summary
```

Add `--include-network-devices` when switches, routers, and APs should move
with endpoint groups.

## Connectivity Testing

Fast PDU submission check:

```powershell
python ptbuilder.py ping PC1 PC2 --output summary
```

Actual Packet Tracer PDU result check:

```powershell
python ptbuilder.py ping PC1 PC2 --wait-result --timeout 20 --output summary
```

Batch tests:

```powershell
python ptbuilder.py test-matrix --sources PC1,PC2 --destination SERVER1 --mode pdu-result --show-passed --timeout 30 --output summary
```

Read the current Packet Tracer PDU list:

```powershell
python ptbuilder.py pdu-list --open-list --output summary
```

## Development Checks

Run the relevant checks before committing changes:

```powershell
python -m py_compile ptbuilder.py
node --check source\userfunctions.js
powershell -NoProfile -Command "[scriptblock]::Create((Get-Content -Raw -LiteralPath 'tools\ptbuilder_ui_pdu_list.ps1')) | Out-Null; Write-Output 'parse-ok'"
```

## Upstream

This project is based on the original Packet Tracer Builder idea: a JavaScript
extension that creates and configures Packet Tracer networks from code. This
fork focuses on making that workflow scriptable, inspectable, and friendly to
agent-driven automation.
