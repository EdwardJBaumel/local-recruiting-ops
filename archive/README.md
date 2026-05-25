# Archive — Sentinel (v1) reference

This folder is the **frozen v1** of the project that became Lantern.

Nothing here is on the active build path. Files live here for two reasons:

1. **Reference.** When something in Lantern behaves unexpectedly, the
   v1 implementation is one folder away — easy to grep for how the
   problem was solved before, or to remind yourself what got
   deliberately cut in the rebuild.
2. **Story.** The README at the repo root says "I built a prototype
   called Sentinel to validate the approach. Lantern is the
   productized version, rebuilt around what I learned." Keeping this
   folder makes that story credible — recruiters who poke around can
   see the v1 and the v2 side by side.

## What's in here

| Path | What it is |
|---|---|
| `sentinel/` | The original Python backend (HTTPServer-based, single-file 1700-line server.py). The agents/, core/, and orchestrator pieces were copied to `lantern/api/` as the starting point for v2; this is the unmodified v1. |
| `sentinel-ui/` | The original 7,000-line `App.jsx` React app. Worked, but every fix risked regressing something else. The v1 → v2 lesson was "split the file, split the state." |
| `start-sentinel.ps1` | The original PowerShell launcher (renamed to disambiguate from Lantern's). |
| `start-sentinel.sh` | Mac/Linux equivalent. |
| `Start SENTINEL.cmd` | Double-click launcher for non-PowerShell users. |
| `OVERNIGHT_NOTES.md` | Working notes from the v1 development sprint. |
| `sentinel.spec`, `build-sentinel.{ps1,sh}` | PyInstaller spec + build scripts that produced a single-binary distribution. Lantern is dev-server-only by design — packaging is a v3 problem. |
| `dist/`, `build/` | PyInstaller output from the last v1 build. |
| `data/`, `logs/` | v1's runtime state, frozen in time. The active app reads/writes `lantern/api/data/`. |
| `tests/`, `pytest.ini`, `requirements-dev.txt` | v1's pytest suite. Tests cover modules that were copied into `lantern/api/`, so most still apply if you ever run them — just point pytest at `lantern/api/` instead of `sentinel/`. |
| `agent.py`, `provisioner.py`, `scripts/` | Admin tooling from v1. |

## Will this ever come back?

No. Lantern is the project going forward. If a feature here turns out
to be missed, port it forward into Lantern; don't revive Sentinel.

The folder size is ~50 MB; trivial to keep around. If disk pressure
ever becomes a real issue, deleting this whole folder leaves Lantern
fully functional.
