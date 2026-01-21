# Bloomberg Algorithmic Trading System — AI Agent Implementation Specification

## Document Purpose

This document serves as the **master specification** for a team of AI agents (Claude Opus 4.5) to implement a complete algorithmic trading system. It is written to be directly parseable and actionable by LLM-based coding agents.

**For AI Agents Reading This Document:**
- This is your complete specification. Follow it precisely.
- Each module section contains exact interfaces, data structures, and logic.
- When ambiguity exists, choose the simpler implementation.
- All code should be Python 3.11+ unless otherwise specified.
- Prioritize correctness over optimization in initial implementation.

---

## System Overview

### What We Are Building

A **multi-factor momentum trading system** that:
1. Pulls data from Bloomberg Terminal (via remote connector) for research and signals
2. Generates daily buy/sell signals based on momentum + quality + sentiment factors
3. Executes trades through a retail broker API (Alpaca or Interactive Brokers)
4. Manages risk through position sizing, stop-losses, and portfolio constraints
5. Logs everything and provides monitoring/alerting

### Architecture Diagram (ASCII)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           SYSTEM ARCHITECTURE                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐     │
│  │  DATA LAYER     │      │  STRATEGY LAYER │      │  EXECUTION LAYER│     │
│  │                 │      │                 │      │                 │     │
│  │ - Bloomberg     │─────▶│ - Universe      │─────▶│ - Order Manager │     │
│  │   Connector     │      │   Selection     │      │ - Broker API    │     │
│  │ - Data Store    │      │ - Signal Gen    │      │ - Fill Tracker  │     │
│  │ - Cache         │      │ - Ranking       │      │                 │     │
│  └─────────────────┘      └─────────────────┘      └─────────────────┘     │
│           │                       │                        │               │
│           └───────────────────────┼────────────────────────┘               │
│                                   ▼                                        │
│                    ┌─────────────────────────────┐                         │
│                    │      RISK LAYER             │                         │
│                    │                             │                         │
│                    │ - Position Sizer            │                         │
│                    │ - Portfolio Constraints     │                         │
│                    │ - Stop Loss Manager         │                         │
│                    │ - Drawdown Monitor          │                         │
│                    └─────────────────────────────┘                         │
│                                   │                                        │
│                                   ▼                                        │
│                    ┌─────────────────────────────┐                         │
│                    │      MONITORING LAYER       │                         │
│                    │                             │                         │
│                    │ - Logger                    │                         │
│                    │ - Alerting                  │                         │
│                    │ - Performance Tracker       │                         │
│                    └─────────────────────────────┘                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### File Structure

```
trading_system/
├── config/
│   ├── settings.yaml           # All configurable parameters
│   ├── credentials.yaml        # API keys (gitignored)
│   └── symbols.yaml            # Universe definitions
├── src/
│   ├── __init__.py
│   ├── data/
│   │   ├── __init__.py
│   │   ├── bloomberg_client.py # Bloomberg API wrapper
│   │   ├── broker_client.py    # Alpaca/IB wrapper
│   │   ├── data_store.py       # SQLite/cache management
│   │   └── models.py           # Data classes/schemas
│   ├── strategy/
│   │   ├── __init__.py
│   │   ├── universe.py         # Universe selection logic
│   │   ├── signals.py          # Signal generation
│   │   ├── ranking.py          # Composite scoring
│   │   └── factors.py          # Individual factor calculations
│   ├── risk/
│   │   ├── __init__.py
│   │   ├── position_sizer.py   # Position sizing logic
│   │   ├── constraints.py      # Portfolio constraints
│   │   ├── stop_loss.py        # Stop loss management
│   │   └── drawdown.py         # Drawdown monitoring
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── order_manager.py    # Order creation and tracking
│   │   ├── executor.py         # Order execution logic
│   │   └── reconciliation.py   # Position reconciliation
│   ├── monitoring/
│   │   ├── __init__.py
│   │   ├── logger.py           # Logging configuration
│   │   ├── alerts.py           # Alert system
│   │   └── performance.py      # Performance tracking
│   └── orchestrator.py         # Main entry point / scheduler
├── tests/
│   └── [mirror of src structure]
├── scripts/
│   ├── backtest.py             # Backtesting entry point
│   ├── paper_trade.py          # Paper trading mode
│   └── live_trade.py           # Live trading mode
├── data/
│   ├── cache/                  # Cached Bloomberg data
│   └── logs/                   # Log files
└── requirements.txt
```

---

## Configuration Schema

### settings.yaml

```yaml
# TRADING SYSTEM CONFIGURATION
# All parameters that control system behavior

system:
  mode: "paper"  # "paper" | "live" | "backtest"
  timezone: "America/New_York"
  log_level: "INFO"

universe:
  base_index: "SPX Index"
  filters:
    min_market_cap: 10_000_000_000  # $10B
    min_avg_volume: 5_000_000       # shares
    min_avg_dollar_volume: 100_000_000  # $100M
    max_spread_pct: 0.0005          # 0.05%
    min_roe: 0.10                   # 10%
    max_debt_equity: 2.0
    require_positive_net_income: true
  excluded_sectors:
    - "Utilities"
  max_universe_size: 100

momentum:
  lookback_days: 252              # ~12 months
  skip_recent_days: 21            # ~1 month
  
technical:
  ema_fast_period: 10
  ema_slow_period: 50
  ema_trend_period: 200
  rsi_period: 14
  rsi_min: 50
  rsi_max: 75
  atr_period: 14

sentiment:
  news_lookback_days: 7
  min_sentiment_score: -0.2
  earnings_blackout_days: 3

volatility_filter:
  vol_lookback_days: 20
  vol_baseline_days: 60
  vol_threshold_multiplier: 1.5
  vix_reduce_25_threshold: 25
  vix_reduce_50_threshold: 30
  vix_stop_trading_threshold: 35

signal_weights:
  momentum: 0.40
  earnings_surprise: 0.15
  estimate_revisions: 0.15
  technical: 0.15
  sentiment: 0.15

portfolio:
  max_positions: 10
  min_position_pct: 0.03           # 3%
  max_position_pct: 0.15           # 15%
  max_sector_pct: 0.30             # 30%
  max_correlation: 0.70
  cash_buffer_pct: 0.05            # 5%
  rebalance_threshold_pct: 0.02    # Only trade if change > 2%

risk:
  risk_per_trade_pct: 0.01         # 1% of portfolio
  stop_loss_atr_multiplier: 1.5
  trailing_stop_atr_multiplier: 2.0
  profit_target_1_atr: 3.0         # First profit target
  profit_target_1_sell_pct: 0.33   # Sell 1/3 at first target
  profit_target_2_atr: 5.0
  profit_target_2_sell_pct: 0.33
  daily_loss_warning_pct: 0.02     # 2%
  daily_loss_reduce_pct: 0.03      # 3%
  daily_loss_exit_pct: 0.05        # 5%
  drawdown_reduce_25_pct: 0.05     # 5% DD -> reduce 25%
  drawdown_reduce_50_pct: 0.10     # 10% DD -> reduce 50%
  drawdown_exit_pct: 0.15          # 15% DD -> exit all
  max_overnight_gap_assumption: 0.10  # 10%

execution:
  broker: "alpaca"                 # "alpaca" | "interactive_brokers"
  order_type: "limit"
  limit_buffer_pct: 0.0002         # 0.02% above ask for buys
  max_order_pct_of_adv: 0.02       # 2% of avg daily volume
  split_order_threshold: 50_000    # Split orders > $50k
  execution_timeout_seconds: 60
  retry_attempts: 3

schedule:
  data_update_time: "08:30"
  signal_generation_time: "08:45"
  order_review_time: "09:00"
  execution_start_time: "09:35"
  execution_end_time: "09:55"
  intraday_check_interval_minutes: 15
  end_of_day_reconciliation_time: "16:15"

alerts:
  email_enabled: true
  sms_enabled: false
  channels:
    info: ["email"]
    warning: ["email"]
    critical: ["email", "sms"]
    emergency: ["email", "sms"]
```

---

## Data Models

### Core Data Classes

```python
"""
models.py - Core data structures used throughout the system

AI AGENT INSTRUCTIONS:
- Implement these exactly as specified using dataclasses or Pydantic
- All timestamps should be timezone-aware (use pytz or zoneinfo)
- Use Decimal for prices and monetary values where precision matters
- Use float for ratios, percentages, and indicators
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from typing import Optional, List, Dict


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class SignalType(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class AlertLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


@dataclass
class PriceBar:
    """Single OHLCV bar"""
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    adjusted_close: Optional[Decimal] = None  # Split/dividend adjusted


@dataclass
class SecurityInfo:
    """Static security information"""
    symbol: str
    bloomberg_ticker: str  # e.g., "AAPL US Equity"
    name: str
    sector: str
    industry: str
    market_cap: float
    shares_outstanding: int
    
    
@dataclass
class FundamentalData:
    """Point-in-time fundamental snapshot"""
    symbol: str
    as_of_date: date
    roe: float                    # Return on Equity
    debt_to_equity: float
    net_income_ttm: float         # Trailing twelve months
    operating_cash_flow_ttm: float
    earnings_surprise_pct: Optional[float]  # Most recent quarter
    estimate_revision_pct: Optional[float]  # 30-day
    next_earnings_date: Optional[date]


@dataclass 
class TechnicalIndicators:
    """Calculated technical indicators for a symbol"""
    symbol: str
    as_of_date: date
    price: Decimal
    ema_10: float
    ema_50: float
    ema_200: float
    rsi_14: float
    atr_14: float
    volume_20d_avg: float
    volatility_20d: float         # Annualized
    volatility_60d_avg: float     # Baseline for comparison


@dataclass
class MomentumScore:
    """Momentum calculation result"""
    symbol: str
    as_of_date: date
    raw_return: float             # 12-1 month return
    volatility: float             # Std dev over period
    momentum_score: float         # Normalized score
    rank: int                     # Rank in universe


@dataclass
class SentimentData:
    """News sentiment aggregation"""
    symbol: str
    as_of_date: date
    story_count: int
    positive_count: int
    negative_count: int
    neutral_count: int
    sentiment_score: float        # (pos - neg) / total, range [-1, 1]
    news_volume_ratio: float      # vs 20-day average


@dataclass
class CompositeSignal:
    """Final ranked signal for a symbol"""
    symbol: str
    as_of_date: date
    momentum_score: float         # Z-score
    earnings_score: float         # Z-score
    revision_score: float         # Z-score
    technical_score: float        # Binary: 1.0 or 0.0
    sentiment_score: float        # Raw: -1 to 1
    composite_score: float        # Weighted sum
    rank: int                     # Final rank in universe
    signal: SignalType
    passes_all_filters: bool
    filter_failures: List[str]    # Which filters failed, if any


@dataclass
class Position:
    """Current position in a security"""
    symbol: str
    quantity: int
    avg_entry_price: Decimal
    current_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    unrealized_pnl_pct: float
    entry_date: date
    days_held: int
    stop_loss_price: Optional[Decimal]
    trailing_stop_price: Optional[Decimal]
    sector: str


@dataclass
class PortfolioState:
    """Current portfolio snapshot"""
    timestamp: datetime
    cash: Decimal
    total_equity: Decimal
    positions: List[Position]
    position_count: int
    total_exposure: Decimal       # Sum of absolute position values
    net_exposure: Decimal         # Long - Short
    sector_exposures: Dict[str, float]  # Sector -> pct of portfolio
    current_drawdown_pct: float
    high_water_mark: Decimal
    daily_pnl: Decimal
    daily_pnl_pct: float


@dataclass
class Order:
    """Order to be executed"""
    order_id: str                 # Internal ID
    symbol: str
    side: OrderSide
    quantity: int
    order_type: OrderType
    limit_price: Optional[Decimal]
    stop_price: Optional[Decimal]
    status: OrderStatus
    created_at: datetime
    submitted_at: Optional[datetime]
    filled_at: Optional[datetime]
    filled_quantity: int
    filled_avg_price: Optional[Decimal]
    broker_order_id: Optional[str]
    reason: str                   # Why this order was generated


@dataclass
class TradeLog:
    """Completed trade record"""
    trade_id: str
    symbol: str
    side: OrderSide
    quantity: int
    price: Decimal
    commission: Decimal
    timestamp: datetime
    order_id: str
    signal_score: float           # Composite score that triggered
    portfolio_value_before: Decimal
    portfolio_value_after: Decimal


@dataclass
class DailyPerformance:
    """Daily performance record"""
    date: date
    starting_equity: Decimal
    ending_equity: Decimal
    daily_return_pct: float
    cumulative_return_pct: float
    drawdown_pct: float
    sharpe_30d: Optional[float]
    positions_held: int
    trades_executed: int
    vix_close: float
    spy_return_pct: float         # Benchmark
```

---

## Module Specifications

### Module 1: Bloomberg Data Client

```python
"""
bloomberg_client.py

AI AGENT INSTRUCTIONS:
- This module wraps the remote Bloomberg connector
- Assume the connector is passed in during initialization
- Handle all Bloomberg-specific field names and data transformations
- Implement caching to avoid redundant API calls
- All dates should be in YYYYMMDD format for Bloomberg API
"""

class BloombergClient:
    """
    Wrapper for Bloomberg API calls via remote connector.
    
    REQUIRED METHODS:
    """
    
    def __init__(self, remote_connector, cache_dir: str):
        """
        Args:
            remote_connector: The Bloomberg remote connector instance
            cache_dir: Directory for caching responses
        """
        pass
    
    def get_index_members(
        self, 
        index: str = "SPX Index",
        as_of_date: Optional[date] = None
    ) -> List[str]:
        """
        Get index constituents as of a specific date.
        
        Bloomberg Function: BDS
        Field: INDX_MEMBERS
        Override: END_DATE_OVERRIDE for historical membership
        
        Returns:
            List of Bloomberg tickers (e.g., ["AAPL US Equity", "MSFT US Equity"])
            
        CRITICAL: Use point-in-time data to avoid survivorship bias.
        If as_of_date is None, use current date.
        """
        pass
    
    def get_historical_prices(
        self,
        symbols: List[str],
        start_date: date,
        end_date: date,
        fields: List[str] = None
    ) -> Dict[str, List[PriceBar]]:
        """
        Get adjusted historical OHLCV data.
        
        Bloomberg Function: BDH
        Default Fields: PX_OPEN, PX_HIGH, PX_LOW, PX_LAST, VOLUME
        Options: adjustmentNormal=True, adjustmentAbnormal=True, adjustmentSplit=True
        
        Returns:
            Dict mapping symbol to list of PriceBar objects, sorted by date ascending
        """
        pass
    
    def get_reference_data(
        self,
        symbols: List[str],
        fields: List[str]
    ) -> Dict[str, Dict[str, any]]:
        """
        Get current snapshot reference data.
        
        Bloomberg Function: BDP
        
        Common fields we need:
        - CUR_MKT_CAP: Market cap in millions
        - AVG_VOLUME_20D: 20-day average volume
        - RETURN_ON_EQUITY: ROE as decimal
        - TOT_DEBT_TO_TOT_EQY: Debt/Equity ratio
        - GICS_SECTOR_NAME: Sector name
        - BEST_EPS_SURPRISE: Latest earnings surprise %
        - BEST_EST_REVIS_PCT: Estimate revision %
        - EXPECTED_REPORT_DT: Next earnings date
        - IS_NET_INCOME: Net income TTM
        - CF_FROM_OPER: Operating cash flow TTM
        
        Returns:
            Dict mapping symbol to dict of field values
        """
        pass
    
    def get_news_sentiment(
        self,
        symbols: List[str],
        lookback_days: int = 7
    ) -> Dict[str, SentimentData]:
        """
        Get aggregated news sentiment for symbols.
        
        Bloomberg Function: NEWS
        
        Process:
        1. Retrieve all news stories for symbol in lookback period
        2. Filter by relevance score (> 50)
        3. Aggregate sentiment: positive, negative, neutral counts
        4. Calculate sentiment score: (positive - negative) / total
        5. Calculate news volume ratio vs 20-day average
        
        Returns:
            Dict mapping symbol to SentimentData
            
        NOTE: If Bloomberg news API is not available, return neutral sentiment
        (score=0, volume_ratio=1) and log a warning.
        """
        pass
    
    def get_vix(self, as_of_date: Optional[date] = None) -> float:
        """
        Get VIX close value.
        
        Symbol: VIX Index
        Field: PX_LAST
        
        Returns:
            VIX closing value as float
        """
        pass
```

### Module 2: Universe Selection

```python
"""
universe.py

AI AGENT INSTRUCTIONS:
- This module filters the base index to tradeable universe
- Apply filters in order: liquidity first, then fundamentals
- Log which symbols are filtered out and why
- Cache results for the day to avoid recomputation
"""

class UniverseSelector:
    """
    Selects tradeable universe from base index.
    
    FILTERING PIPELINE:
    1. Get index members (point-in-time)
    2. Apply liquidity filters
    3. Apply fundamental quality filters  
    4. Apply sector exclusions
    5. Sort by liquidity, take top N
    """
    
    def __init__(self, bloomberg_client: BloombergClient, config: dict):
        pass
    
    def select_universe(self, as_of_date: Optional[date] = None) -> List[str]:
        """
        Main method to select tradeable universe.
        
        ALGORITHM:
        
        1. members = bloomberg_client.get_index_members(config.base_index, as_of_date)
        
        2. ref_data = bloomberg_client.get_reference_data(members, [
               "CUR_MKT_CAP", "AVG_VOLUME_20D", "RETURN_ON_EQUITY",
               "TOT_DEBT_TO_TOT_EQY", "GICS_SECTOR_NAME", "IS_NET_INCOME",
               "CF_FROM_OPER"
           ])
        
        3. For each symbol, apply filters (track failures):
           
           LIQUIDITY FILTERS:
           - market_cap >= config.min_market_cap
           - avg_volume >= config.min_avg_volume
           
           FUNDAMENTAL FILTERS:
           - roe >= config.min_roe
           - debt_to_equity <= config.max_debt_equity
           - net_income > 0 (if require_positive_net_income)
           - operating_cash_flow > net_income (earnings quality)
           
           SECTOR FILTERS:
           - sector not in config.excluded_sectors
        
        4. Sort passing symbols by avg_volume descending
        
        5. Return top config.max_universe_size symbols
        
        Returns:
            List of symbols that pass all filters
        """
        pass
    
    def get_filter_report(self) -> Dict[str, List[str]]:
        """
        Returns dict mapping filter name to list of symbols it removed.
        Useful for debugging and transparency.
        """
        pass
```

### Module 3: Factor Calculations

```python
"""
factors.py

AI AGENT INSTRUCTIONS:
- Each factor calculation is a pure function
- Handle edge cases (insufficient data, division by zero)
- All calculations should be vectorized using pandas/numpy where possible
- Return NaN for symbols with insufficient data (will be filtered out later)
"""

import pandas as pd
import numpy as np
from typing import Dict, List

def calculate_momentum_scores(
    price_data: Dict[str, pd.DataFrame],
    lookback_days: int = 252,
    skip_days: int = 21
) -> Dict[str, MomentumScore]:
    """
    Calculate volatility-adjusted momentum scores.
    
    FORMULA:
    raw_return = (price[t - skip_days] - price[t - lookback_days]) / price[t - lookback_days]
    volatility = std(daily_returns[t - lookback_days : t - skip_days]) * sqrt(252)
    momentum_score = raw_return / volatility
    
    ALGORITHM:
    1. For each symbol:
       a. Extract close prices for the period
       b. Calculate raw return (skip most recent `skip_days`)
       c. Calculate daily returns over the period
       d. Calculate annualized volatility
       e. Compute momentum_score = raw_return / volatility
    
    2. Z-score normalize across all symbols:
       z_score = (score - mean(all_scores)) / std(all_scores)
    
    3. Rank symbols by z_score descending
    
    Args:
        price_data: Dict mapping symbol to DataFrame with 'close' column and date index
        
    Returns:
        Dict mapping symbol to MomentumScore
    """
    pass


def calculate_technical_indicators(
    price_data: Dict[str, pd.DataFrame],
    config: dict
) -> Dict[str, TechnicalIndicators]:
    """
    Calculate technical indicators for each symbol.
    
    INDICATORS TO CALCULATE:
    
    1. EMA (Exponential Moving Average):
       EMA_t = price_t * k + EMA_{t-1} * (1 - k)
       where k = 2 / (period + 1)
       
       Calculate: ema_10, ema_50, ema_200
    
    2. RSI (Relative Strength Index):
       For each day, calculate gain (if close > prev_close) or loss
       avg_gain = EMA of gains over period
       avg_loss = EMA of losses over period
       RS = avg_gain / avg_loss
       RSI = 100 - (100 / (1 + RS))
       
       Calculate: rsi_14
    
    3. ATR (Average True Range):
       TR = max(high - low, abs(high - prev_close), abs(low - prev_close))
       ATR = EMA of TR over period
       
       Calculate: atr_14
    
    4. Volatility:
       daily_returns = (close - prev_close) / prev_close
       volatility = std(daily_returns) * sqrt(252)
       
       Calculate: volatility_20d, volatility_60d_avg (rolling mean of volatility_20d)
    
    5. Volume:
       Calculate: volume_20d_avg
    
    Returns:
        Dict mapping symbol to TechnicalIndicators
    """
    pass


def check_technical_conditions(
    indicators: TechnicalIndicators,
    config: dict
) -> tuple[bool, List[str]]:
    """
    Check if symbol passes all technical conditions.
    
    CONDITIONS (ALL must be true):
    1. price > ema_50 (above medium-term trend)
    2. ema_10 > ema_50 (short-term momentum positive)
    3. ema_50 > ema_200 (long-term uptrend)
    4. rsi_14 > config.rsi_min (momentum confirmed)
    5. rsi_14 < config.rsi_max (not overbought)
    6. volatility_20d < volatility_60d_avg * config.vol_threshold_multiplier (not spiking)
    
    Returns:
        (passes: bool, failed_conditions: List[str])
    """
    pass


def calculate_earnings_score(
    fundamental_data: Dict[str, FundamentalData]
) -> Dict[str, float]:
    """
    Calculate earnings surprise z-scores.
    
    ALGORITHM:
    1. Extract earnings_surprise_pct for each symbol (skip None)
    2. Z-score normalize across all symbols
    
    Returns:
        Dict mapping symbol to z-score (or NaN if no data)
    """
    pass


def calculate_revision_score(
    fundamental_data: Dict[str, FundamentalData]
) -> Dict[str, float]:
    """
    Calculate estimate revision z-scores.
    
    ALGORITHM:
    1. Extract estimate_revision_pct for each symbol (skip None)
    2. Z-score normalize across all symbols
    
    Returns:
        Dict mapping symbol to z-score (or NaN if no data)
    """
    pass
```

### Module 4: Signal Generation

```python
"""
signals.py

AI AGENT INSTRUCTIONS:
- This is the core ranking engine
- Combine all factors into composite score
- Apply all filters before ranking
- Generate clear buy/sell/hold signals
"""

class SignalGenerator:
    """
    Generates trading signals from factor scores.
    """
    
    def __init__(
        self,
        bloomberg_client: BloombergClient,
        config: dict
    ):
        pass
    
    def generate_signals(
        self,
        universe: List[str],
        as_of_date: Optional[date] = None
    ) -> List[CompositeSignal]:
        """
        Generate ranked signals for the universe.
        
        ALGORITHM:
        
        1. GATHER DATA:
           - price_data = get 15 months of daily prices for universe
           - fundamental_data = get current fundamentals
           - sentiment_data = get news sentiment
           - vix = get current VIX
        
        2. CALCULATE FACTORS:
           - momentum_scores = calculate_momentum_scores(price_data)
           - technical_indicators = calculate_technical_indicators(price_data)
           - earnings_scores = calculate_earnings_score(fundamental_data)
           - revision_scores = calculate_revision_score(fundamental_data)
        
        3. FOR EACH SYMBOL, BUILD COMPOSITE:
           
           a. Check filters (record all failures):
              - Technical conditions pass
              - Sentiment score >= config.min_sentiment_score
              - Not within earnings_blackout_days of earnings
              - News volume ratio < 3.0 (no news spike)
           
           b. Calculate composite score:
              composite = (
                  config.weights.momentum * momentum_z_score +
                  config.weights.earnings_surprise * earnings_z_score +
                  config.weights.estimate_revisions * revision_z_score +
                  config.weights.technical * (1.0 if tech_pass else 0.0) +
                  config.weights.sentiment * sentiment_score
              )
              
              Handle NaN: If any component is NaN, use 0.0 for that component
        
        4. APPLY VIX ADJUSTMENT:
           - If vix > vix_stop_trading_threshold: all signals = HOLD
           - Otherwise, proceed normally
        
        5. RANK AND ASSIGN SIGNALS:
           - Sort by composite_score descending
           - Top config.max_positions with passes_all_filters=True: signal = BUY
           - All others: signal = HOLD
           - Existing positions not in top N: signal = SELL (handled elsewhere)
        
        Returns:
            List of CompositeSignal, sorted by rank ascending (1 = best)
        """
        pass
    
    def get_sell_signals(
        self,
        current_positions: List[Position],
        buy_signals: List[CompositeSignal]
    ) -> List[CompositeSignal]:
        """
        Determine which current positions should be sold.
        
        SELL CONDITIONS (any triggers sell):
        1. Symbol not in top 2*max_positions by composite score
        2. Symbol fails filters that it previously passed
        3. Symbol has earnings within blackout period
        
        Returns:
            List of CompositeSignal with signal=SELL
        """
        pass
```

### Module 5: Position Sizing

```python
"""
position_sizer.py

AI AGENT INSTRUCTIONS:
- Position sizes are based on volatility targeting
- Apply all constraints after initial sizing
- Return both target sizes and the orders needed to reach them
"""

class PositionSizer:
    """
    Calculates position sizes based on risk targeting.
    """
    
    def __init__(self, config: dict):
        pass
    
    def calculate_target_positions(
        self,
        signals: List[CompositeSignal],
        portfolio: PortfolioState,
        technical_indicators: Dict[str, TechnicalIndicators]
    ) -> Dict[str, int]:
        """
        Calculate target position sizes in shares.
        
        ALGORITHM:
        
        1. DETERMINE AVAILABLE CAPITAL:
           available = portfolio.total_equity * (1 - config.cash_buffer_pct)
        
        2. FOR EACH BUY SIGNAL:
           
           a. Calculate base position size (volatility targeting):
              target_risk = config.risk_per_trade_pct * portfolio.total_equity
              stock_volatility = technical_indicators[symbol].volatility_20d
              position_value = target_risk / stock_volatility
              
           b. Apply VIX adjustment:
              vix = get_current_vix()
              if vix > config.vix_reduce_50_threshold:
                  position_value *= 0.5
              elif vix > config.vix_reduce_25_threshold:
                  position_value *= 0.75
           
           c. Apply constraints:
              - max_position = portfolio.total_equity * config.max_position_pct
              - min_position = portfolio.total_equity * config.min_position_pct
              - position_value = clip(position_value, min_position, max_position)
           
           d. Convert to shares:
              shares = floor(position_value / current_price)
        
        3. CHECK PORTFOLIO CONSTRAINTS:
           
           a. Total positions <= config.max_positions
              If exceeded: remove lowest-ranked signals until compliant
           
           b. Sector exposure <= config.max_sector_pct
              For each sector, sum position values
              If exceeded: reduce largest position in that sector
           
           c. Total exposure <= available capital
              If exceeded: scale all positions proportionally
        
        4. APPLY REBALANCE THRESHOLD:
           For existing positions, only include in orders if:
           abs(target_shares - current_shares) / current_shares > config.rebalance_threshold_pct
        
        Returns:
            Dict mapping symbol to target share count
        """
        pass
    
    def generate_orders(
        self,
        target_positions: Dict[str, int],
        current_positions: List[Position],
        prices: Dict[str, Decimal]
    ) -> List[Order]:
        """
        Generate orders to move from current to target positions.
        
        ALGORITHM:
        
        1. For each symbol in target_positions:
           current = current_positions.get(symbol, 0)
           target = target_positions[symbol]
           diff = target - current
           
           if diff > 0: Create BUY order for diff shares
           if diff < 0: Create SELL order for abs(diff) shares
        
        2. For each symbol in current_positions not in target_positions:
           Create SELL order for all shares (full exit)
        
        3. Set order prices:
           - BUY: limit_price = ask * (1 + config.limit_buffer_pct)
           - SELL: limit_price = bid * (1 - config.limit_buffer_pct)
        
        4. Split large orders:
           If order_value > config.split_order_threshold:
              Split into multiple orders spaced over time
        
        Returns:
            List of Order objects ready for execution
        """
        pass
```

### Module 6: Risk Management

```python
"""
risk/stop_loss.py, drawdown.py, constraints.py

AI AGENT INSTRUCTIONS:
- Risk checks run continuously during market hours
- Stop losses are placed immediately after fills
- Drawdown monitor can halt trading system-wide
"""

class StopLossManager:
    """
    Manages stop-loss orders for all positions.
    """
    
    def calculate_stop_price(
        self,
        entry_price: Decimal,
        atr: float,
        side: OrderSide
    ) -> Decimal:
        """
        Calculate initial stop-loss price.
        
        FORMULA:
        For LONG positions:
            stop_price = entry_price - (atr * config.stop_loss_atr_multiplier)
        
        For SHORT positions:
            stop_price = entry_price + (atr * config.stop_loss_atr_multiplier)
        
        Round to 2 decimal places.
        """
        pass
    
    def calculate_trailing_stop(
        self,
        entry_price: Decimal,
        highest_price: Decimal,  # Highest since entry for longs
        atr: float
    ) -> Decimal:
        """
        Calculate trailing stop price.
        
        ACTIVATION CONDITION:
        Only activate when: highest_price >= entry_price + atr
        (Position is profitable by at least 1 ATR)
        
        FORMULA:
        trailing_stop = highest_price - (atr * config.trailing_stop_atr_multiplier)
        
        CONSTRAINT:
        trailing_stop must be >= initial stop (stops only move up, never down)
        """
        pass
    
    def check_profit_targets(
        self,
        position: Position,
        current_price: Decimal,
        atr: float
    ) -> Optional[Order]:
        """
        Check if profit targets are hit and generate partial sell orders.
        
        TARGET 1:
        If current_price >= entry_price + (atr * config.profit_target_1_atr):
            AND position hasn't taken first profit yet:
            Return SELL order for floor(quantity * config.profit_target_1_sell_pct) shares
        
        TARGET 2:
        If current_price >= entry_price + (atr * config.profit_target_2_atr):
            AND position hasn't taken second profit yet:
            Return SELL order for floor(remaining * config.profit_target_2_sell_pct) shares
        
        Returns:
            Order if target hit, None otherwise
        """
        pass


class DrawdownMonitor:
    """
    Monitors portfolio drawdown and triggers risk reduction.
    """
    
    def __init__(self, config: dict):
        self.high_water_mark: Decimal = Decimal("0")
        self.trading_halted: bool = False
        self.reduction_level: float = 1.0  # 1.0 = full size, 0.5 = half size
    
    def update(self, portfolio: PortfolioState) -> Optional[AlertLevel]:
        """
        Update drawdown tracking and check thresholds.
        
        ALGORITHM:
        
        1. Update high water mark:
           if portfolio.total_equity > self.high_water_mark:
               self.high_water_mark = portfolio.total_equity
        
        2. Calculate current drawdown:
           drawdown_pct = (self.high_water_mark - portfolio.total_equity) / self.high_water_mark
        
        3. Check thresholds and take action:
        
           if drawdown_pct >= config.drawdown_exit_pct:
               self.trading_halted = True
               self.reduction_level = 0.0
               return AlertLevel.EMERGENCY
               # Trigger: Exit all positions
           
           elif drawdown_pct >= config.drawdown_reduce_50_pct:
               self.reduction_level = 0.5
               return AlertLevel.CRITICAL
               # Trigger: Reduce all positions by 50%
           
           elif drawdown_pct >= config.drawdown_reduce_25_pct:
               self.reduction_level = 0.75
               return AlertLevel.WARNING
               # Trigger: Reduce all positions by 25%
           
           else:
               self.reduction_level = 1.0
               return None
        
        Returns:
            AlertLevel if threshold crossed, None otherwise
        """
        pass
    
    def check_daily_loss(
        self,
        portfolio: PortfolioState
    ) -> Optional[AlertLevel]:
        """
        Check daily loss limits.
        
        THRESHOLDS:
        - daily_loss_warning_pct: Log warning, continue trading
        - daily_loss_reduce_pct: Exit weakest 2 positions
        - daily_loss_exit_pct: Exit ALL positions, halt trading
        
        Returns:
            AlertLevel if threshold crossed, None otherwise
        """
        pass


class PortfolioConstraints:
    """
    Enforces portfolio-level constraints.
    """
    
    def check_constraints(
        self,
        proposed_orders: List[Order],
        current_portfolio: PortfolioState,
        config: dict
    ) -> tuple[List[Order], List[str]]:
        """
        Validate orders against portfolio constraints.
        
        CONSTRAINTS TO CHECK:
        
        1. MAX POSITIONS:
           Count of positions after orders <= config.max_positions
           If violated: Remove lowest-priority BUY orders
        
        2. SECTOR CONCENTRATION:
           For each sector, exposure <= config.max_sector_pct
           If violated: Reduce/remove BUY orders in that sector
        
        3. SINGLE POSITION SIZE:
           Each position <= config.max_position_pct of portfolio
           If violated: Reduce order quantity
        
        4. CORRELATION:
           Check pairwise correlations of holdings
           If avg_correlation > config.max_correlation: Flag warning
        
        5. SUFFICIENT CASH:
           Cash after BUY orders >= 0
           If violated: Scale down all BUY orders proportionally
        
        Returns:
            (approved_orders: List[Order], violations: List[str])
        """
        pass
```

### Module 7: Execution

```python
"""
execution/order_manager.py, executor.py

AI AGENT INSTRUCTIONS:
- Handle broker API errors gracefully with retries
- Log every order action (submit, fill, cancel, reject)
- Implement order timeout handling
"""

class OrderManager:
    """
    Manages order lifecycle.
    """
    
    def __init__(self, broker_client, config: dict):
        self.pending_orders: Dict[str, Order] = {}
        self.broker = broker_client
    
    def submit_order(self, order: Order) -> bool:
        """
        Submit order to broker.
        
        ALGORITHM:
        
        1. Validate order:
           - Symbol exists
           - Quantity > 0
           - Price is reasonable (within 5% of last trade)
        
        2. Check order size limits:
           - Order value <= portfolio_value * 0.05 (fat finger check)
           - Order quantity <= avg_daily_volume * config.max_order_pct_of_adv
        
        3. Submit to broker API:
           broker_order_id = self.broker.submit_order(
               symbol=order.symbol,
               side=order.side.value,
               qty=order.quantity,
               type=order.order_type.value,
               limit_price=order.limit_price,
               stop_price=order.stop_price
           )
        
        4. Update order status:
           order.broker_order_id = broker_order_id
           order.status = OrderStatus.SUBMITTED
           order.submitted_at = datetime.now()
        
        5. Add to pending_orders for tracking
        
        6. Log: "Order submitted: {order_id} {side} {quantity} {symbol} @ {price}"
        
        Returns:
            True if submitted successfully, False otherwise
        """
        pass
    
    def check_fills(self) -> List[TradeLog]:
        """
        Check status of all pending orders.
        
        ALGORITHM:
        
        For each order in pending_orders:
            status = self.broker.get_order_status(order.broker_order_id)
            
            if status.filled_quantity > order.filled_quantity:
                # New fill
                Create TradeLog entry
                Update order.filled_quantity, order.filled_avg_price
                
            if status.is_complete:
                order.status = OrderStatus.FILLED
                order.filled_at = datetime.now()
                Remove from pending_orders
                
            if status.is_rejected:
                order.status = OrderStatus.REJECTED
                Log error with rejection reason
                Remove from pending_orders
                
            if order timed out (submitted_at + timeout < now):
                Cancel order
                Log timeout
        
        Returns:
            List of new TradeLog entries
        """
        pass
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order."""
        pass
    
    def cancel_all(self) -> int:
        """Cancel all pending orders. Returns count cancelled."""
        pass


class Executor:
    """
    Orchestrates order execution sequence.
    """
    
    def execute_rebalance(
        self,
        orders: List[Order],
        portfolio: PortfolioState
    ) -> List[TradeLog]:
        """
        Execute a rebalancing set of orders.
        
        EXECUTION SEQUENCE:
        
        1. Separate orders into SELLS and BUYS
        
        2. Execute all SELLS first:
           - Submit all sell orders
           - Wait for fills (with timeout)
           - Log results
        
        3. Update available cash based on sell proceeds
        
        4. Execute BUYS:
           - Verify sufficient cash for all buys
           - If not: scale down buy quantities proportionally
           - Submit buy orders
           - Wait for fills
           - Log results
        
        5. Place stop-loss orders for all new positions
        
        6. Return complete trade log
        
        Returns:
            List of TradeLog for all executed trades
        """
        pass
```

### Module 8: Monitoring

```python
"""
monitoring/logger.py, alerts.py, performance.py

AI AGENT INSTRUCTIONS:
- Use structured logging (JSON format for machine parsing)
- Alerts should be non-blocking (async/queued)
- Performance metrics should be calculated efficiently
"""

class TradingLogger:
    """
    Centralized logging for the trading system.
    """
    
    def __init__(self, log_dir: str, config: dict):
        """
        Set up logging with:
        - Console output (INFO level)
        - File output (DEBUG level, daily rotation)
        - Structured JSON format
        """
        pass
    
    def log_trade(self, trade: TradeLog):
        """Log completed trade with all details."""
        pass
    
    def log_signal(self, signal: CompositeSignal):
        """Log generated signal."""
        pass
    
    def log_order(self, order: Order, action: str):
        """Log order action (submit, fill, cancel, reject)."""
        pass
    
    def log_risk_event(self, event_type: str, details: dict):
        """Log risk management events (stop hit, drawdown threshold, etc.)."""
        pass
    
    def log_system_health(self, component: str, status: str, details: dict):
        """Log system health checks."""
        pass


class AlertManager:
    """
    Manages alerts and notifications.
    """
    
    def __init__(self, config: dict):
        pass
    
    def send_alert(
        self,
        level: AlertLevel,
        title: str,
        message: str,
        data: Optional[dict] = None
    ):
        """
        Send alert through configured channels.
        
        CHANNELS BY LEVEL (from config):
        - INFO: email digest only
        - WARNING: email immediately
        - CRITICAL: email + SMS
        - EMERGENCY: email + SMS + phone call (if configured)
        
        IMPLEMENTATION:
        - Queue alerts for async sending
        - Rate limit to avoid spam (max 1 per minute per level)
        - Log all alerts locally regardless of send status
        """
        pass
    
    def send_daily_summary(self, performance: DailyPerformance):
        """Send end-of-day summary email."""
        pass


class PerformanceTracker:
    """
    Tracks and calculates performance metrics.
    """
    
    def __init__(self, data_store):
        pass
    
    def record_daily_performance(self, portfolio: PortfolioState):
        """
        Record end-of-day performance snapshot.
        
        CALCULATIONS:
        - daily_return = (ending_equity - starting_equity) / starting_equity
        - cumulative_return = (ending_equity - initial_equity) / initial_equity
        - drawdown = (high_water_mark - ending_equity) / high_water_mark
        """
        pass
    
    def calculate_sharpe(self, lookback_days: int = 30) -> Optional[float]:
        """
        Calculate rolling Sharpe ratio.
        
        FORMULA:
        daily_returns = array of daily return percentages
        excess_returns = daily_returns - risk_free_rate / 252
        sharpe = mean(excess_returns) / std(excess_returns) * sqrt(252)
        
        Returns None if insufficient data.
        """
        pass
    
    def calculate_sortino(self, lookback_days: int = 30) -> Optional[float]:
        """
        Calculate Sortino ratio (downside deviation only).
        
        FORMULA:
        downside_returns = [r for r in daily_returns if r < 0]
        downside_std = std(downside_returns)
        sortino = mean(excess_returns) / downside_std * sqrt(252)
        """
        pass
    
    def get_performance_report(self) -> dict:
        """
        Generate comprehensive performance report.
        
        INCLUDE:
        - Total return (inception to date)
        - Annualized return
        - Sharpe ratio
        - Sortino ratio
        - Max drawdown
        - Current drawdown
        - Win rate
        - Profit factor
        - Average trade return
        - Best/worst day
        - Best/worst trade
        """
        pass
```

### Module 9: Orchestrator

```python
"""
orchestrator.py

AI AGENT INSTRUCTIONS:
- This is the main entry point
- Manages the daily schedule
- Coordinates all modules
- Handles errors gracefully without crashing
"""

class TradingOrchestrator:
    """
    Main orchestrator that runs the trading system.
    """
    
    def __init__(self, config_path: str):
        """
        Initialize all components:
        1. Load configuration
        2. Initialize Bloomberg client
        3. Initialize broker client
        4. Initialize all modules (universe, signals, risk, execution, monitoring)
        5. Load any persisted state (positions, orders)
        """
        pass
    
    def run_daily_cycle(self):
        """
        Execute the complete daily trading cycle.
        
        SCHEDULE:
        
        08:30 - DATA UPDATE:
            bloomberg_client.update_daily_data()
            Log: "Data update complete"
        
        08:45 - SIGNAL GENERATION:
            universe = universe_selector.select_universe()
            signals = signal_generator.generate_signals(universe)
            Log: "Generated {n} signals, {m} buys"
        
        09:00 - ORDER PREPARATION:
            current_positions = broker_client.get_positions()
            target_positions = position_sizer.calculate_target_positions(signals)
            orders = position_sizer.generate_orders(target_positions, current_positions)
            orders = portfolio_constraints.check_constraints(orders)
            Log: "Prepared {n} orders"
            
            # OPTIONAL: Wait for human review
            if config.require_human_review:
                send_order_summary_for_review(orders)
                wait_for_approval()
        
        09:35 - EXECUTION:
            if is_market_open():
                trades = executor.execute_rebalance(orders)
                Log: "Executed {n} trades"
        
        09:35-15:45 - INTRADAY MONITORING:
            Every config.intraday_check_interval_minutes:
                portfolio = broker_client.get_portfolio()
                drawdown_monitor.update(portfolio)
                drawdown_monitor.check_daily_loss(portfolio)
                stop_loss_manager.check_all_stops(portfolio.positions)
                
                if any stops triggered:
                    execute stop orders
                    
                if drawdown threshold crossed:
                    execute risk reduction
        
        16:15 - END OF DAY:
            portfolio = broker_client.get_portfolio()
            performance_tracker.record_daily_performance(portfolio)
            reconciliation.verify_positions()
            alert_manager.send_daily_summary()
            Log: "Day complete. P&L: {pnl}"
        """
        pass
    
    def run_backtest(
        self,
        start_date: date,
        end_date: date,
        initial_capital: Decimal
    ) -> dict:
        """
        Run historical backtest.
        
        ALGORITHM:
        
        1. Initialize simulated portfolio with initial_capital
        
        2. For each trading day in range:
           a. Load historical data as of that date
           b. Run signal generation (using only data available at that time)
           c. Generate orders
           d. Simulate execution (next day's open + slippage)
           e. Update simulated positions
           f. Check stop losses against intraday prices
           g. Record daily performance
        
        3. Calculate final metrics
        
        4. Return comprehensive backtest report
        
        Returns:
            dict with equity_curve, trades, metrics, etc.
        """
        pass
    
    def handle_error(self, error: Exception, context: str):
        """
        Centralized error handling.
        
        SEVERITY CLASSIFICATION:
        
        RECOVERABLE (log and continue):
        - Single API call failure (will retry)
        - Data for one symbol missing
        - Non-critical calculation error
        
        DEGRADED (alert and continue with reduced functionality):
        - Bloomberg connection lost (switch to cached data)
        - Broker API rate limited (slow down)
        
        CRITICAL (alert and halt):
        - Broker connection lost
        - Position reconciliation mismatch
        - Unexpected exception in order execution
        
        Always:
        - Log full stack trace
        - Send appropriate alert
        - Update system health status
        """
        pass
```

---

## Testing Requirements

### Unit Tests Required

```python
"""
AI AGENT INSTRUCTIONS:
Implement tests for all critical calculations.
Use pytest. Mock external dependencies.
"""

# tests/test_factors.py
class TestMomentumCalculation:
    def test_basic_momentum_score(self):
        """Verify momentum score calculation with known values."""
        pass
    
    def test_momentum_with_missing_data(self):
        """Should return NaN for symbols with insufficient history."""
        pass
    
    def test_momentum_zscore_normalization(self):
        """Z-scores should have mean≈0, std≈1."""
        pass

class TestTechnicalIndicators:
    def test_ema_calculation(self):
        """Verify EMA matches expected values."""
        pass
    
    def test_rsi_bounds(self):
        """RSI should always be between 0 and 100."""
        pass
    
    def test_atr_positive(self):
        """ATR should always be positive."""
        pass

class TestPositionSizing:
    def test_volatility_targeting(self):
        """Higher vol stocks should get smaller positions."""
        pass
    
    def test_max_position_constraint(self):
        """No position should exceed max_position_pct."""
        pass
    
    def test_sector_constraint(self):
        """Sector exposure should not exceed limit."""
        pass

class TestRiskManagement:
    def test_stop_loss_calculation(self):
        """Stop should be entry - 1.5*ATR for longs."""
        pass
    
    def test_trailing_stop_activation(self):
        """Trailing stop activates after 1 ATR profit."""
        pass
    
    def test_drawdown_thresholds(self):
        """Correct actions at each drawdown level."""
        pass
```

### Integration Tests Required

```python
# tests/test_integration.py
class TestSignalPipeline:
    def test_end_to_end_signal_generation(self):
        """From raw data to ranked signals."""
        pass

class TestOrderExecution:
    def test_rebalance_execution_sequence(self):
        """Sells before buys, stops placed after fills."""
        pass

class TestBacktest:
    def test_no_lookahead_bias(self):
        """Signals only use data available at decision time."""
        pass
    
    def test_transaction_costs_applied(self):
        """P&L reflects realistic costs."""
        pass
```

---

## Deployment Checklist

### Pre-Launch Verification

```
□ All unit tests passing
□ All integration tests passing
□ Backtest results reviewed and reasonable
□ Paper trading for minimum 2 weeks
□ Paper results within 20% of backtest expectations
□ All API credentials configured and tested
□ Alerting system tested (send test alert)
□ Logging verified (check log rotation)
□ Risk limits confirmed in config
□ Emergency procedures documented
□ Manual override capability tested (can close all positions)
```

### Go-Live Sequence

```
1. Start with 25% of intended capital
2. Monitor every trade for first week
3. Verify fills match expectations
4. Check daily reconciliation
5. After 1 week: increase to 50% if no issues
6. After 2 weeks: increase to 100% if no issues
7. Transition to normal monitoring cadence
```

---

## Quick Reference: Key Formulas

### Momentum Score
```
raw_return = (price[t-21] - price[t-252]) / price[t-252]
volatility = std(daily_returns[t-252:t-21]) * sqrt(252)
momentum_score = raw_return / volatility
z_score = (momentum_score - mean) / std
```

### Position Size
```
target_risk = account_equity * 0.01  # 1% risk
position_value = target_risk / stock_volatility_annual
shares = floor(position_value / current_price)
```

### Stop Loss
```
stop_price = entry_price - (ATR_14 * 1.5)
```

### Trailing Stop
```
if highest_price >= entry_price + ATR_14:
    trailing_stop = highest_price - (ATR_14 * 2.0)
    stop_price = max(stop_price, trailing_stop)
```

### Composite Signal
```
composite = 0.40 * momentum_z 
          + 0.15 * earnings_z 
          + 0.15 * revision_z
          + 0.15 * (1.0 if tech_pass else 0.0)
          + 0.15 * sentiment_score
```

---

## Error Codes Reference

| Code | Meaning | Action |
|------|---------|--------|
| E001 | Bloomberg connection failed | Retry 3x, then use cache |
| E002 | Broker connection failed | HALT, alert emergency |
| E003 | Order rejected | Log reason, skip order |
| E004 | Position mismatch | HALT, manual reconciliation |
| E005 | Drawdown limit hit | Auto-exit, alert emergency |
| E006 | Daily loss limit hit | Auto-exit, alert critical |
| E007 | Data quality issue | Skip symbol, log warning |
| E008 | Timeout on order | Cancel and retry |

---

*End of Specification Document*

*For AI Agents: This document is your complete reference. Implement each module according to its specification. When in doubt, prioritize safety (risk management) over returns.*
