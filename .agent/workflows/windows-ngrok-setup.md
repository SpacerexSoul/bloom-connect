---
description: Setup Bloomberg Remote Server with ngrok tunnel on Windows
---

# Bloomberg Remote Server Setup (Windows)

This workflow sets up the Bloomberg remote server with ngrok tunneling on a Windows PC with Bloomberg Terminal.

## Prerequisites

- Bloomberg Terminal installed and **logged in**
- Python 3.10+ installed (with "Add to PATH" checked during install)
- ngrok.exe in the `tools/` folder of this repository
- ngrok account created at https://ngrok.com (free tier)
- ngrok authtoken from https://dashboard.ngrok.com/get-started/your-authtoken

---

## Step 1: Navigate to Repository

```powershell
cd C:\path\to\bloom-connect
```

// turbo

---

## Step 2: Configure ngrok Authtoken (First Time Only)

```powershell
.\tools\ngrok config add-authtoken YOUR_AUTHTOKEN_HERE
```

Replace `YOUR_AUTHTOKEN_HERE` with the actual token from ngrok dashboard.

---

## Step 3: Create Virtual Environment

```powershell
python -m venv venv
```

// turbo

---

## Step 4: Activate Virtual Environment

```powershell
.\venv\Scripts\Activate.ps1
```

If you get an execution policy error, run this first:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

// turbo

---

## Step 5: Install Server Dependencies

```powershell
pip install fastapi uvicorn pydantic pydantic-settings python-jose passlib bcrypt
```

// turbo

---

## Step 6: Install Bloomberg BLPAPI

Check if blpapi is available:
```powershell
pip install blpapi
```

If that fails, get the installer from Bloomberg Terminal:
1. In Bloomberg Terminal, type `WAPI <GO>`
2. Download Python BLPAPI SDK
3. Install the downloaded wheel file: `pip install blpapi-*.whl`

---

## Step 7: Create User Account

```powershell
$env:PYTHONPATH = "packages\blpremote_server\src"
python -c "from blpremote_server.auth import UserStore; UserStore('users.json').create_user('krishna','CHOOSE_A_PASSWORD')"
```

Replace `CHOOSE_A_PASSWORD` with a secure password.

// turbo

---

## Step 8: Start the Bloomberg Server

**Keep this terminal running!**

```powershell
$env:PYTHONPATH = "packages\blpremote_server\src"
python -m uvicorn blpremote_server.app:app --host 127.0.0.1 --port 8000
```

Wait for output:
```
INFO:     Started server process
INFO:     Uvicorn running on http://127.0.0.1:8000
```

---

## Step 9: Start ngrok Tunnel (New Terminal)

Open a **new PowerShell window** and run:

```powershell
cd C:\path\to\bloom-connect\tools
.\ngrok http 8000
```

// turbo

---

## Step 10: Get the Public URL

ngrok will display:
```
Forwarding    https://xxxx-xx-xx-xxx-xxx.ngrok-free.app -> http://localhost:8000
```

**Copy this URL** - this is what macOS will use to connect.

---

## Step 11: Verify Server is Working

Test locally in a third terminal:

```powershell
curl http://localhost:8000/health
```

Expected output:
```json
{"status":"healthy","bloomberg_connected":true}
```

If `bloomberg_connected` is `false`, make sure Bloomberg Terminal is logged in.

---

## Summary: Running Commands

Terminal 1 (Server):
```powershell
cd C:\path\to\bloom-connect
.\venv\Scripts\Activate.ps1
$env:PYTHONPATH = "packages\blpremote_server\src"
python -m uvicorn blpremote_server.app:app --host 127.0.0.1 --port 8000
```

Terminal 2 (ngrok):
```powershell
cd C:\path\to\bloom-connect\tools
.\ngrok http 8000
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: blpremote_server` | Set PYTHONPATH: `$env:PYTHONPATH = "packages\blpremote_server\src"` |
| `ModuleNotFoundError: blpapi` | Install from Bloomberg WAPI page |
| ngrok "ERR_NGROK_108" | Invalid authtoken, reconfigure |
| `bloomberg_connected: false` | Log into Bloomberg Terminal |
| Port 8000 in use | Change port: `--port 8001` and `.\ngrok http 8001` |

---

## Output Required

After completing setup, provide:
1. The ngrok URL (e.g., `https://xxxx.ngrok-free.app`)
2. The username created (e.g., `krishna`)
3. Confirmation that health check returns `bloomberg_connected: true`
