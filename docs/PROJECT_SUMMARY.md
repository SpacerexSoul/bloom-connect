# Project Summary: Bloomberg Remote BLPAPI Wrapper

**Author:** Krishna Dattani  
**Date:** January 2026  
**University Final Year Project**

---

## What I Built

A **remote Bloomberg Terminal connector** that lets me run Python trading code on my MacBook while executing actual Bloomberg API calls on a university Windows PC with Bloomberg Terminal.

### The Problem

- Bloomberg Terminal only runs on Windows
- The Bloomberg Python API (`blpapi`) only works on Windows with an active Bloomberg login
- I code on a MacBook
- I wanted to build algorithmic trading strategies that use real Bloomberg data

### The Solution

```
┌─────────────────────┐        INTERNET         ┌─────────────────────┐
│     My MacBook      │    (ngrok tunnel)       │  University Windows │
│                     │ ────────────────────►   │  PC + Bloomberg     │
│  Python strategy    │                         │                     │
│  builds "plan"      │  ◄────────────────────  │  Executes real      │
│  No Bloomberg needed│      JSON response      │  Bloomberg API      │
└─────────────────────┘                         └─────────────────────┘
```

---

## Technologies Used

| Technology | Purpose |
|------------|---------|
| **Python 3.10+** | Core language |
| **FastAPI** | REST API server on Windows |
| **Pydantic** | Data validation and serialization |
| **httpx** | HTTP client for macOS |
| **ngrok** | Tunnel through university firewall |
| **Bloomberg BLPAPI** | Official Bloomberg Python SDK |
| **NumPy** | Numerical computing for strategy |
| **JWT Tokens** | Secure authentication |
| **bcrypt** | Password hashing |

---

## How It Works (Simple Explanation)

### Step 1: Build an Execution Plan (macOS)
Instead of running Bloomberg commands directly, my Mac builds a "plan" in JSON format:

```python
plan = {
    "ops": [
        {"op": "start_session"},
        {"op": "open_service", "service": "//blp/refdata"},
        {"op": "create_request", "request": "ReferenceDataRequest"},
        {"op": "append", "path": "securities", "value": "IBM US Equity"},
        {"op": "send_request"},
        {"op": "collect_response"}
    ]
}
```

### Step 2: Send to Windows Server
The plan is sent over HTTPS (via ngrok tunnel) to the Windows PC.

### Step 3: Execute on Bloomberg (Windows)
The Windows server validates the plan, then runs the actual Bloomberg commands:

```python
session.start()
session.openService("//blp/refdata")
request = service.createRequest("ReferenceDataRequest")
request.append("securities", "IBM US Equity")
# ... etc
```

### Step 4: Return Results
Bloomberg data comes back as clean JSON:

```json
{
    "IBM US Equity": {
        "PX_LAST": 296.00,
        "PX_OPEN": 301.00,
        "VOLUME": 2833824
    }
}
```

---

## The Trading Strategy: Algebraic Topology Market-Neutral

I implemented a proprietary trading strategy using **algebraic topology** - a branch of mathematics that studies shapes.

### Strategy Pipeline

1. **Get Universe**: Download S&P 500 constituents from Bloomberg
2. **Fetch History**: Get 5 years of daily prices for all stocks
3. **Build Graph**: Represent the market as a weighted graph where nodes are stocks and edges are correlations
4. **Laplacian Diffusion**: Smooth price signals across the graph to find local mispricings
5. **Persistent Homology**: Extract topological features (clusters, holes) from the correlation structure
6. **Generate Signals**: Mean-reversion signals adjusted by market regime

### Results

Running on 15 S&P 500 stocks with 30 days of data:

**Top Long (Buy) Signals:**
| Stock | Signal |
|-------|--------|
| ABBV (AbbVie) | +0.73 |
| ACN (Accenture) | +0.49 |
| ABNB (Airbnb) | +0.47 |

**Top Short (Sell) Signals:**
| Stock | Signal |
|-------|--------|
| ADI (Analog Devices) | -1.02 |
| ADM (Archer Daniels) | -0.56 |
| ADSK (Autodesk) | -0.51 |

---

## Interview-Style Explanation

### "So what did you build for your final year project?"

> I built a system that lets me run trading algorithms on my Mac that use real Bloomberg data, even though Bloomberg only works on Windows. It's like a remote control for Bloomberg - my Mac sends commands through the internet, a Windows PC executes them on Bloomberg, and sends the data back.

### "What makes this technically interesting?"

> The key challenge was **security**. I couldn't just allow arbitrary code execution because that would be dangerous. So I designed an "Intermediate Representation" - a strict vocabulary of allowed operations. The Windows server validates every command before executing it. You can only do approved operations like fetching prices, not things like deleting files.

### "What's the trading strategy doing?"

> It uses algebraic topology to model the stock market as a mathematical shape. Stocks are nodes in a graph, connected by how correlated they are. I run a diffusion process (like heat spreading) across this graph to find stocks that are temporarily mispriced relative to their neighbors. Then I use persistent homology - a technique from topological data analysis - to detect market regimes and adjust the signals accordingly.

### "Did it work?"

> Yes! I successfully pulled real Bloomberg data through the tunnel, ran the full topological analysis, and generated trading signals. The strategy identified AbbVie as a buy and Analog Devices as a sell based on the latest data.

---

## Security Features

- **JWT Token Authentication**: Username/password → short-lived token
- **Password Hashing**: bcrypt with salt
- **Allowlisted Operations**: Only approved services and request types
- **Request Limits**: Max 200 securities, 120 second timeout
- **IP Allowlist**: Optional restriction to specific IPs
- **No Code Execution**: Only validated IR plans, never `eval()` or `exec()`

---

## Connection Details (Current Session)

| Setting | Value |
|---------|-------|
| Server URL | `https://adina-chalazal-brenda.ngrok-free.dev` |
| Username | `krishna` |
| Bloomberg Status | ✅ Connected |
| Health Check | `{"status":"healthy","bloomberg_connected":true}` |

---

## Files Created

- **45 Python files** across client and server packages
- **4,600+ lines of code**
- **28 unit tests** (all passing)
- **GitHub Actions CI pipeline**
- **Comprehensive documentation**

---

## What I Learned

1. **API Design**: How to design a secure RPC protocol
2. **Authentication**: JWT tokens, password hashing with bcrypt
3. **Network Tunneling**: Using ngrok to bypass firewalls
4. **Topological Data Analysis**: Persistent homology, graph Laplacians
5. **Financial APIs**: Bloomberg BLPAPI message protocol
6. **Monorepo Structure**: Managing multiple packages in one repo
