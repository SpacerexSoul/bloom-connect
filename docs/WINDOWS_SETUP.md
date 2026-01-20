# Windows Bloomberg Server Setup Guide

## Prerequisites

1. **Bloomberg Terminal** - Must be installed and logged in
2. **Python 3.10+** - Download from [python.org](https://www.python.org/downloads/)
3. **Bloomberg BLPAPI** - Python SDK for Bloomberg

---

## Step 1: Install Python

1. Download Python 3.10+ from https://www.python.org/downloads/
2. **IMPORTANT**: Check "Add Python to PATH" during installation
3. Verify installation:
   ```powershell
   python --version
   ```

---

## Step 2: Clone the Repository

Open PowerShell and run:

```powershell
cd C:\Users\YourUsername
git clone https://github.com/SpacerexSoul/bloom-connect.git
cd bloom-connect
```

---

## Step 3: Create Virtual Environment

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

> **Note**: If you get an execution policy error, run:
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

---

## Step 4: Install Server Dependencies

```powershell
cd packages\blpremote_server
pip install -e .
```

---

## Step 5: Install Bloomberg BLPAPI (IMPORTANT)

Bloomberg's Python API is not on PyPI. You need to:

1. Go to Bloomberg Terminal
2. Type `WAPI <GO>` to open the API page
3. Download the Python BLPAPI SDK
4. Install it:
   ```powershell
   pip install blpapi-<version>.whl
   ```

Or if you have the Bloomberg installer:
```powershell
pip install --index-url=https://bcms.bloomberg.com/pip/simple/ blpapi
```

---

## Step 6: Configure Firewall

Allow incoming connections on port 8000:

**PowerShell (Run as Administrator):**
```powershell
New-NetFirewallRule -DisplayName "Bloomberg Remote Server" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

---

## Step 7: Create First User

Before starting the server, create a user for authentication:

```powershell
cd C:\Users\YourUsername\bloom-connect
.\venv\Scripts\Activate.ps1

python -c "
from blpremote_server.auth import UserStore
store = UserStore('users.json')
store.create_user('krishna', 'your-secure-password')
print('User created successfully!')
"
```

---

## Step 8: Start the Server

**Make sure Bloomberg Terminal is logged in first!**

```powershell
cd C:\Users\YourUsername\bloom-connect\packages\blpremote_server
.\scripts\run_server.ps1
```

Or manually:
```powershell
python -m uvicorn blpremote_server.app:app --host 0.0.0.0 --port 8000
```

You should see:
```
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

---

## Step 9: Find Your Windows IP Address

```powershell
ipconfig
```

Look for `IPv4 Address` under your network adapter (e.g., `192.168.1.100`).

---

## Step 10: Test from macOS

On your Mac, run:

```bash
# Test health endpoint
curl http://WINDOWS_IP:8000/health

# Test version endpoint
curl http://WINDOWS_IP:8000/version
```

Expected responses:
```json
{"status":"healthy","bloomberg_connected":true}
{"version":"0.1.0","protocol_version":"1.0"}
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: blpapi` | Install Bloomberg BLPAPI SDK from WAPI page |
| Connection refused | Check firewall, ensure server is running |
| Bloomberg not connected | Make sure Bloomberg Terminal is logged in |
| Authentication failed | Run the "Create First User" step |

---

## Quick Test Script (on Windows)

After server starts, test locally:

```powershell
python -c "
import requests
r = requests.get('http://localhost:8000/health')
print(r.json())
"
```

---

## Environment Variables (Optional)

Create a `.env` file in the server directory:

```
BLPREMOTE_SECRET_KEY=your-very-secure-secret-key-change-this
BLPREMOTE_TOKEN_EXPIRE_MINUTES=60
BLPREMOTE_PORT=8000
```
