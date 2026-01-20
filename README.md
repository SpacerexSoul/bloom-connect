# Bloomberg Remote BLPAPI Wrapper

Remote Bloomberg API execution from macOS via a Windows host running Bloomberg Terminal.

```
┌─────────────────┐         ┌──────────────────────┐
│  macOS Client   │  HTTP   │   Windows Server     │
│  (Python code)  │ ──────► │  (Bloomberg + API)   │
│                 │         │                      │
│  Builds IR plan │         │  Executes via blpapi │
│  No blpapi dep  │ ◄────── │  Returns results     │
└─────────────────┘  JSON   └──────────────────────┘
```

## Features

- **No Bloomberg dependency on macOS** - real `blpapi` only needed on Windows
- **Two development APIs**: Convenience functions (`px_last()`) and Bloomberg-like proxy (`Session`/`Service`/`Request`)
- **Secure**: Token-based auth, password hashing, validated execution plans
- **Validated execution**: No arbitrary code execution - only allowlisted operations

---

## macOS Setup (Client)

### Installation

```bash
cd packages/blpremote_client
pip install -e .

# Optional: pandas support for DataFrame output
pip install -e ".[pandas]"
```

### Pair with Windows Host

Before first use, pair with your Windows Bloomberg server:

```python
from blpremote_client import RemoteHost

host = RemoteHost("http://WINDOWS_IP:8000", username="krishna", password="your-password")
if host.pair("krishna", "your-password"):
    print("Pairing successful!")
```

### Quick Start: Convenience API

```python
from blpremote_client import RemoteHost, px_last, ref_data

host = RemoteHost("http://WINDOWS_IP:8000", username="krishna", password="...")

# Get last price
price = px_last(host, "IBM US Equity")
print(f"IBM: {price}")

# Get multiple fields
data = ref_data(host, ["IBM US Equity", "AAPL US Equity"], ["PX_LAST", "NAME"])
print(data)
```

### Bloomberg-like Proxy API

For code that mirrors Bloomberg's actual API:

```python
from blpremote_client.proxy import Session, SessionOptions

opts = SessionOptions()
session = Session(opts, remote_host="http://WINDOWS_IP:8000", username="krishna", password="...")

session.start()
session.openService("//blp/refdata")
svc = session.getService("//blp/refdata")

req = svc.createRequest("ReferenceDataRequest")
req.getElement("securities").appendValue("IBM US Equity")
req.getElement("fields").appendValue("PX_LAST")

cid = session.sendRequest(req)
result = session.collectResponse(cid)
print(result.to_dict())
```

### Troubleshooting (macOS)

| Issue | Solution |
|-------|----------|
| `ConnectionError` | Check Windows IP, ensure firewall allows port 8000 |
| `AuthenticationError` | Verify username/password, re-pair if needed |
| `TimeoutError` | Increase timeout or check Windows server status |
| Token expired | Client auto-refreshes; if issues, delete `~/.blpremote/tokens.json` |

---

## Windows Setup (Server)

### Prerequisites

1. **Python 3.10+** installed
2. **Bloomberg Terminal** installed and logged in
3. **Bloomberg BLPAPI Python SDK** (`pip install blpapi`)

### Installation

```powershell
cd packages\blpremote_server
pip install -e .

# Or with Bloomberg support
pip install -e ".[bloomberg]"
```

### Start the Server

**PowerShell:**
```powershell
.\scripts\run_server.ps1 -Host 0.0.0.0 -Port 8000
```

**Command Prompt:**
```cmd
scripts\run_server.cmd --host 0.0.0.0 --port 8000
```

**Or directly with Python:**
```bash
python -m uvicorn blpremote_server.app:app --host 0.0.0.0 --port 8000
```

### Configure Firewall

Allow inbound connections on port 8000:

```powershell
New-NetFirewallRule -DisplayName "Bloomberg Remote Server" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BLPREMOTE_PORT` | 8000 | Server port |
| `BLPREMOTE_SECRET_KEY` | (dev key) | JWT secret - **change in production** |
| `BLPREMOTE_TOKEN_EXPIRE_MINUTES` | 60 | Token lifetime |
| `BLPREMOTE_IP_ALLOWLIST` | (empty) | Comma-separated allowed IPs |

### Recommended Network Setup

- Use **static IP** or **DHCP reservation** for the Windows machine
- Consider using a **reverse proxy** (nginx/Caddy) for TLS termination

> ⚠️ **Security Warning**: Do not expose this server to the public internet without TLS and proper authentication. Use a reverse proxy with HTTPS for production deployments.

---

## API Reference

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Server health check |
| `/version` | GET | Server version |
| `/v1/auth/login` | POST | Authenticate and get token |
| `/v1/execute` | POST | Execute a Bloomberg operation plan |

### Execution Plan Format

```json
{
  "protocol_version": "1.0",
  "request_id": "uuid",
  "auth": { "token": "..." },
  "ops": [
    { "op": "start_session" },
    { "op": "open_service", "service": "//blp/refdata" },
    { "op": "create_request", "service": "//blp/refdata", "request": "ReferenceDataRequest", "id": "req1" },
    { "op": "append", "id": "req1", "path": "securities", "value": "IBM US Equity" },
    { "op": "append", "id": "req1", "path": "fields", "value": "PX_LAST" },
    { "op": "send_request", "id": "req1", "correlation_id": "cid1" },
    { "op": "collect_refdata_response", "correlation_id": "cid1", "timeout_ms": 10000 }
  ]
}
```

### Error Codes

| Code | Description |
|------|-------------|
| `AUTH_FAILED` | Invalid credentials or expired token |
| `PLAN_INVALID` | Execution plan validation failed |
| `BLP_SESSION_FAIL` | Bloomberg session failed to start |
| `BLP_TIMEOUT` | Request timed out |
| `BLP_SECURITY_ERROR` | Invalid security identifier |
| `BLP_FIELD_ERROR` | Invalid field |

---

## Development

### Run Tests

```bash
# All tests
pytest

# Client only
pytest packages/blpremote_client/tests -v

# Server only
pytest packages/blpremote_server/tests -v
```

### Lint & Format

```bash
ruff check .
black .
```

### Manual Smoke Test (requires Bloomberg)

On macOS, with server running on Windows:

```bash
python tools/manual_smoke_test.py --host http://WINDOWS_IP:8000 --username krishna --password ...
```

---

## Trading Strategies

A dedicated `strategies/` directory contains proprietary trading strategies using the Bloomberg remote wrapper.

### Strategy 1: Algebraic Topology Market-Neutral

Uses algebraic topology for market modeling - consistently profitable for 5 years including 2022.

```python
from blpremote_client import RemoteHost
from strategies import AlgebraicTopologyStrategy

host = RemoteHost("http://WINDOWS_IP:8000", username="krishna", password="...")

strategy = AlgebraicTopologyStrategy()
signals = strategy.run(host)

# Position sizing for $1M portfolio
positions = strategy.compute_positions(signals, portfolio_value=1_000_000)
```

**Pipeline:**
1. **Universe**: Get index members via BDS
2. **Data**: Fetch 5yr historical prices via BDH
3. **Graph**: Build weighted correlation graph
4. **Diffusion**: Laplacian diffusion on returns
5. **Topology**: Persistent homology for regime features
6. **Signals**: Market-neutral signal generation

See [strategies/README.md](strategies/README.md) for full documentation.

---

## License

MIT License - see [LICENSE](LICENSE)
