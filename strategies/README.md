# Trading Strategies

This directory contains test trading strategies that use the Bloomberg Remote BLPAPI Wrapper.

## Strategy 1: Algebraic Topology Market-Neutral Strategy

A strategy that uses algebraic topology for market modeling, consistently profitable for five years including during 2022.

### Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Strategy Pipeline                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. UNIVERSE          Get index members (BDS)                   │
│        ↓                                                        │
│  2. DATA              Fetch 5yr historical prices (BDH)         │
│        ↓                                                        │
│  3. GRAPH             Build weighted correlation graph          │
│        ↓                                                        │
│  4. DIFFUSION         Laplacian diffusion on returns            │
│        ↓                                                        │
│  5. TOPOLOGY          Persistent homology for regime features   │
│        ↓                                                        │
│  6. SIGNALS           Market-neutral signal generation          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Key Components

| Component | Description |
|-----------|-------------|
| **Laplacian Diffusion** | Smooths returns across the correlation graph to extract local pricing signals |
| **Persistent Homology** | Captures topological features of market structure for regime detection |
| **Market Neutrality** | Ensures long/short balance for hedged exposure |

### Bloomberg Data Requirements

| Request Type | Data | Bloomberg Call |
|--------------|------|----------------|
| Universe | Index members | `BDS("SPX Index", "INDX_MEMBERS")` |
| Prices | 5yr daily data | `BDH(<tickers>, "PX_LAST", <start>, <end>)` |
| Volume | Trading volume | `BDH(<tickers>, "VOLUME", <start>, <end>)` |

### Usage

```python
from blpremote_client import RemoteHost
from strategies import AlgebraicTopologyStrategy

# Connect to Windows Bloomberg server
host = RemoteHost(
    "http://WINDOWS_IP:8000",
    username="krishna",
    password="..."
)

# Initialize and run strategy
strategy = AlgebraicTopologyStrategy()
signals = strategy.run(host)

# Get position sizes for $1M portfolio
positions = strategy.compute_positions(signals, portfolio_value=1_000_000)

for security, position in sorted(positions.items(), key=lambda x: -abs(x[1]))[:10]:
    print(f"{security}: ${position:,.0f}")
```

### Configuration

```python
from strategies.algebraic_topology import StrategyConfig

config = StrategyConfig(
    index="SPX Index",           # Universe source
    max_securities=500,           # Limit analysis scope
    lookback_days=252 * 5,        # 5 years of data
    diffusion_steps=10,           # Smoothing iterations
    diffusion_alpha=0.5,          # Diffusion rate
    max_dimension=2,              # Homology dimension
    position_limit=0.02,          # 2% max per security
    rebalance_frequency=5,        # Days between rebalances
)

strategy = AlgebraicTopologyStrategy(config)
```

### Dependencies

For full persistent homology computation, install:

```bash
pip install numpy scipy
pip install ripser        # Fast persistent homology
pip install giotto-tda    # Alternative TDA library
```

### Theory

**Laplacian Diffusion**: Models information flow across the stock graph. Residuals after diffusion represent local mispricing signals.

**Persistent Homology**: Extracts topological invariants (Betti numbers, persistence diagrams) that capture:
- Market clustering (H0 features)
- Correlation cycles (H1 features)
- Regime transitions via persistence entropy

---

## Adding New Strategies

1. Create a new file in `strategies/`
2. Implement a class with `fetch_data()`, `generate_signals()`, and `run()` methods
3. Add to `strategies/__init__.py`
4. Document Bloomberg data requirements
