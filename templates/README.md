# SSH Manager

Lightweight web app that lets you manage files and SLURM jobs on a remote cluster
via SSH, straight from your local browser. No VPN, no VS Code, no admin rights needed.

```
PC (browser)  ←→  localhost:5000  ←→  SSH  ←→  CLUSTER
```

## Requirements

- Python 3.8+
- pip (user install, no sudo needed)

## Install

```bash
git clone <this-repo>
cd ssh-manager
pip install --user -r requirements.txt
```

## Run

```bash
python app.py
```

Opens automatically at http://localhost:5000

## Features

| Page | What it does |
|------|-------------|
| **Files** | Browse dirs, open & edit files with syntax highlighting (CodeMirror), create/rename/delete, Ctrl+S to save |
| **SLURM** | Live job queue (auto-refresh 15s), submit `.slurm` scripts, cancel jobs, run arbitrary commands |
| **Terminal** | Full interactive SSH terminal via xterm.js + WebSocket PTY |

## Auth

Supports password auth and SSH key auth (`~/.ssh/id_rsa` or custom path).
SSH key agent (already loaded keys) also works — leave both fields blank.

## Notes

- Single-user, local-only by design. Do not expose port 5000 to a network.
- The SSH connection is kept alive for the whole session.
- Right-click files in the browser for rename/delete options.
