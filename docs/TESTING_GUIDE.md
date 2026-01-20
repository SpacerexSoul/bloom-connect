# Testing Guide: Connector Verification

This guide walks through testing the Bloomberg connector step-by-step.

---

## Phase 1: Server Health Check

### From macOS Terminal:

```bash
# Replace WINDOWS_IP with your Windows machine's IP address
export WINDOWS_IP="192.168.1.100"

# 1. Test health endpoint
curl http://$WINDOWS_IP:8000/health

# Expected: {"status":"healthy","bloomberg_connected":true}

# 2. Test version endpoint  
curl http://$WINDOWS_IP:8000/version

# Expected: {"version":"0.1.0","protocol_version":"1.0"}
```

---

## Phase 2: Authentication Test

```bash
# Test login
curl -X POST http://$WINDOWS_IP:8000/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"krishna","password":"your-password"}'

# Expected: {"token":"eyJ...","expires_in":3600,"token_type":"Bearer"}
```

---

## Phase 3: Python Client Test

On your Mac, in the bloom-connect directory:

```bash
cd /Users/krishna/workspace/UniveristyFinalYearCode/bloomCon
```

### Test 1: Basic Connection

```python
import sys
sys.path.insert(0, 'packages/blpremote_client/src')

from blpremote_client import RemoteHost

# Replace with your Windows IP and credentials
host = RemoteHost(
    "http://192.168.1.100:8000", 
    username="krishna", 
    password="your-password"
)

# Check connection
print("Health:", host.health())
print("Version:", host.version())
```

### Test 2: Get Single Price (px_last)

```python
from blpremote_client import RemoteHost, px_last

host = RemoteHost("http://192.168.1.100:8000", username="krishna", password="...")

price = px_last(host, "IBM US Equity")
print(f"IBM Last Price: {price}")
```

### Test 3: Get Multiple Fields (ref_data)

```python
from blpremote_client import RemoteHost, ref_data

host = RemoteHost("http://192.168.1.100:8000", username="krishna", password="...")

data = ref_data(
    host,
    securities=["IBM US Equity", "AAPL US Equity", "MSFT US Equity"],
    fields=["PX_LAST", "NAME", "VOLUME"]
)

for security, fields in data.items():
    print(f"\n{security}:")
    for field, value in fields.items():
        print(f"  {field}: {value}")
```

### Test 4: Historical Data (bdh)

```python
from blpremote_client import RemoteHost, bdh

host = RemoteHost("http://192.168.1.100:8000", username="krishna", password="...")

# Get last 30 days of prices
data = bdh(
    host,
    securities="AAPL US Equity",
    fields=["PX_LAST", "VOLUME"],
    start_date="20231201",
    end_date="20231231"
)

print(data)
```

### Test 5: Index Members (bds)

```python
from blpremote_client import RemoteHost, get_index_members

host = RemoteHost("http://192.168.1.100:8000", username="krishna", password="...")

# Get S&P 500 members
members = get_index_members(host, "SPX Index")
print(f"S&P 500 has {len(members)} members")
print(f"First 10: {members[:10]}")
```

---

## Phase 4: Full Smoke Test

Run the included smoke test:

```bash
cd /Users/krishna/workspace/UniveristyFinalYearCode/bloomCon

python tools/manual_smoke_test.py \
  --host http://192.168.1.100:8000 \
  --username krishna \
  --password your-password
```

---

## Expected Results

| Test | Expected Output |
|------|-----------------|
| Health | `{"status":"healthy","bloomberg_connected":true}` |
| px_last | A floating point number (e.g., `182.45`) |
| ref_data | Dict with security data |
| bdh | Historical price arrays |
| get_index_members | List of ~500 tickers |

---

## If Tests Fail

1. **Connection refused**: Check Windows firewall, server running
2. **Authentication error**: Verify user was created on Windows
3. **Bloomberg not connected**: Log into Bloomberg Terminal
4. **Timeout**: Bloomberg may be slow, increase timeout_ms

---

## Next: Test Trading Strategy

Once all Phase 1-4 tests pass, proceed to test the strategy:

```python
from blpremote_client import RemoteHost
from strategies import AlgebraicTopologyStrategy

host = RemoteHost("http://192.168.1.100:8000", username="krishna", password="...")

strategy = AlgebraicTopologyStrategy()
signals = strategy.run(host)
```
