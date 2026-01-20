# ngrok Setup Guide for University Networks

This guide sets up ngrok to tunnel through university firewalls.

---

## Step 1: Create ngrok Account (Free)

1. Go to **https://ngrok.com/signup**
2. Sign up with email or GitHub
3. After signup, go to **https://dashboard.ngrok.com/get-started/your-authtoken**
4. Copy your authtoken (looks like: `2abc123xyz...`)

---

## Step 2: Download ngrok on Windows

### Option A: Direct Download
1. Go to **https://ngrok.com/download**
2. Download **Windows (64-bit)**
3. Extract `ngrok.exe` to a folder (e.g., `C:\tools\ngrok\`)

### Option B: Using PowerShell
```powershell
# Create directory
mkdir C:\tools\ngrok
cd C:\tools\ngrok

# Download ngrok (Windows 64-bit)
Invoke-WebRequest -Uri "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip" -OutFile "ngrok.zip"

# Extract
Expand-Archive -Path "ngrok.zip" -DestinationPath "."
```

---

## Step 3: Configure ngrok Authtoken

```powershell
cd C:\tools\ngrok
.\ngrok config add-authtoken YOUR_AUTH_TOKEN_HERE
```

---

## Step 4: Start the Bloomberg Server

**Terminal 1 (PowerShell):**
```powershell
cd C:\Users\YourUsername\bloom-connect
.\venv\Scripts\Activate.ps1

cd packages\blpremote_server
python -m uvicorn blpremote_server.app:app --host 127.0.0.1 --port 8000
```

Wait until you see:
```
INFO:     Uvicorn running on http://127.0.0.1:8000
```

---

## Step 5: Start ngrok Tunnel

**Terminal 2 (New PowerShell window):**
```powershell
cd C:\tools\ngrok
.\ngrok http 8000
```

You'll see something like:
```
Session Status                online
Account                       krishna@email.com (Plan: Free)
Forwarding                    https://a1b2c3d4.ngrok-free.app -> http://localhost:8000

Connections                   ttl     opn     rt1     rt5     p50     p90
                              0       0       0.00    0.00    0.00    0.00
```

**Copy the `https://....ngrok-free.app` URL!**

---

## Step 6: Test from macOS

Replace the ngrok URL below with your actual URL:

```bash
# Quick test with curl
curl https://a1b2c3d4.ngrok-free.app/health
```

Expected: `{"status":"healthy","bloomberg_connected":true}`

---

## Step 7: Use in Python (macOS)

```python
import sys
sys.path.insert(0, '/Users/krishna/workspace/UniveristyFinalYearCode/bloomCon/packages/blpremote_client/src')

from blpremote_client import RemoteHost, px_last, ref_data

# Use your ngrok URL
NGROK_URL = "https://a1b2c3d4.ngrok-free.app"  # <-- Replace with yours!

host = RemoteHost(NGROK_URL, username="krishna", password="your-password")

# Test 1: Health check
print("Health:", host.health())

# Test 2: Get IBM price
price = px_last(host, "IBM US Equity")
print(f"IBM Last Price: {price}")

# Test 3: Multiple securities
data = ref_data(
    host, 
    ["AAPL US Equity", "MSFT US Equity"], 
    ["PX_LAST", "NAME"]
)
print("Data:", data)
```

---

## Important Notes

### ngrok Free Tier Limitations
- URL changes every time you restart ngrok
- Rate limited (but fine for testing)
- Single tunnel at a time

### Keep Both Windows Open
You need **two PowerShell windows** running:
1. **Server**: `uvicorn blpremote_server.app:app ...`
2. **ngrok**: `ngrok http 8000`

### Bloomberg Must Be Logged In
Make sure Bloomberg Terminal is running and logged in before testing.

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| ngrok says "connection refused" | Server not running on port 8000 |
| "ERR_NGROK_108" | Invalid authtoken, re-run config step |
| Slow responses | Normal - ngrok adds latency |
| URL changed | Restart ngrok = new URL, update your code |

---

## Quick Reference

```
# Windows Terminal 1 (Server)
cd bloom-connect\packages\blpremote_server
python -m uvicorn blpremote_server.app:app --host 127.0.0.1 --port 8000

# Windows Terminal 2 (ngrok)
cd C:\tools\ngrok
.\ngrok http 8000

# macOS (test)
curl https://YOUR-URL.ngrok-free.app/health
```
