# Agent Guide

This repository is a Packet Tracer Builder fork with an agent-friendly CLI
automation layer. Treat the Packet Tracer application as the source of truth
when a command talks to a live topology.

## Working Rules

- Run shell commands through `rtk` in this workspace.
- Prefer compact CLI output with `--output summary`; switch to `--output json`
  only when debugging or when the full payload is needed.
- Keep generated logs, caches, screenshots, and local handoff notes out of
  commits unless they are intentionally added as examples.
- Do not assume a Packet Tracer PDU submission means connectivity succeeded.
  Use `ping --wait-result` or `test-matrix --mode pdu-result` when the actual
  Packet Tracer PDU result matters.

## Useful Commands

```powershell
rtk python ptbuilder.py status
rtk python ptbuilder.py wait-connected
rtk python ptbuilder.py doctor
rtk python ptbuilder.py export-current --file examples\current_snapshot.json --output summary
rtk python ptbuilder.py audit --output summary
rtk python ptbuilder.py relayout-current --audit --timeout 30 --output summary
rtk python ptbuilder.py pdu-list --open-list --output summary
rtk python ptbuilder.py ping PC1 PC2 --wait-result --timeout 20 --output summary
```

## Project Map

- `ptbuilder.py` is the main CLI, bridge server, topology planner, audit tool,
  and Packet Tracer automation entry point.
- `source/` contains the Packet Tracer Builder JavaScript module and WebView UI.
- `tools/` contains PowerShell and JavaScript helpers for UI automation,
  introspection, hot patches, and PDU result reads.
- `examples/` contains topology plans, snapshots, capability manifests, and
  operation examples.
- `Builder.pts` and `ptbuilder-bridge.pts` are Packet Tracer script module
  packages.

## Validation Checklist

Before opening or updating a PR, run the checks that match the changed files:

```powershell
rtk python -m py_compile ptbuilder.py
rtk node --check source\userfunctions.js
rtk powershell -NoProfile -Command "[scriptblock]::Create((Get-Content -Raw -LiteralPath 'tools\ptbuilder_ui_pdu_list.ps1')) | Out-Null; Write-Output 'parse-ok'"
```

