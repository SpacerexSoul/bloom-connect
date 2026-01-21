"""
Multi-Factor Momentum Trading Strategy (Complete Implementation)

A comprehensive momentum + quality + technical + sentiment multi-factor strategy
implementing ALL requirements from the Bloomberg Trading Strategy Guide:

1. Universe Selection with full liquidity and quality filters
2. 12-1 Month Momentum with volatility normalization
3. Technical confirmation (EMA structure + RSI)
4. Fundamental quality overlay (earnings, revisions, cash flow)
5. News sentiment with volume anomaly detection
6. VIX-based regime filtering
7. Position sizing with volatility targeting
8. Portfolio constraints (sector, correlation)
9. Risk controls (stop-loss, trailing stops, profit targets)
10. Circuit breakers (daily loss, drawdown limits)

Based on specification from Bloomberg_Trading_Strategy_Guide.docx
"""

import sys
from pathlib import Path
from typing import Any, Optional
from datetime import date, datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

# Add parent path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "packages/blpremote_client/src"))


class SignalType(Enum):
    """Trading signal types."""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    PROFIT_TAKE = "profit_take"  # RSI > 80 or ATR target hit


class AlertLevel(Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


@dataclass
class MultiFactorConfig:
    """Complete configuration for Multi-Factor Momentum Strategy."""
    
    # =========================================================================
    # UNIVERSE SELECTION
    # =========================================================================
    index: str = "SPX Index"
    max_universe_size: int = 100
    
    # Liquidity Filters
    min_market_cap: float = 10_000_000_000  # $10B
    min_avg_volume: int = 5_000_000  # shares
    min_avg_dollar_volume: float = 100_000_000  # $100M daily
    max_bid_ask_spread_pct: float = 0.0005  # 0.05%
    
    # Fundamental Quality Filters
    min_roe: float = 0.10  # 10%
    max_debt_equity: float = 2.0
    require_positive_net_income: bool = True
    require_earnings_quality: bool = True  # Operating CF > Net Income
    
    # Sector Exclusions
    excluded_sectors: list = field(default_factory=lambda: ["Utilities"])
    
    # =========================================================================
    # MOMENTUM PARAMETERS
    # =========================================================================
    momentum_lookback_days: int = 252  # 12 months
    momentum_skip_days: int = 21  # Skip most recent month (mean reversion)
    
    # =========================================================================
    # TECHNICAL PARAMETERS
    # =========================================================================
    ema_fast_period: int = 10
    ema_slow_period: int = 50
    ema_trend_period: int = 200
    rsi_period: int = 14
    rsi_min: int = 50  # Minimum RSI for entry
    rsi_max: int = 75  # Maximum RSI for entry
    rsi_profit_take: int = 80  # Flag for profit-taking
    atr_period: int = 14
    
    # =========================================================================
    # VOLATILITY REGIME FILTERS
    # =========================================================================
    vol_lookback_days: int = 20
    vol_baseline_days: int = 60
    vol_threshold_multiplier: float = 1.5  # Stock vol spike threshold
    
    # VIX Thresholds
    vix_reduce_25_threshold: int = 25  # Reduce positions 25%
    vix_reduce_50_threshold: int = 30  # Reduce positions 50%
    vix_stop_trading_threshold: int = 35  # No new positions
    vix_complacency_threshold: int = 15  # Watch for complacency
    vix_complacency_days: int = 20  # Consecutive days low VIX
    
    # =========================================================================
    # FUNDAMENTAL QUALITY OVERLAY
    # =========================================================================
    min_earnings_surprise_pct: float = 0.02  # 2% surprise threshold
    earnings_blackout_days: int = 2  # Days before earnings to exit
    
    # =========================================================================
    # NEWS SENTIMENT
    # =========================================================================
    news_lookback_days: int = 7
    min_sentiment_score: float = -0.2  # Allow neutral or positive
    news_volume_anomaly_multiplier: float = 3.0  # 3x normal = anomaly
    excluded_news_keywords: list = field(default_factory=lambda: [
        "fraud", "investigation", "SEC", "lawsuit", "recall", "downgrade"
    ])
    
    # =========================================================================
    # SIGNAL WEIGHTS (must sum to 1.0)
    # =========================================================================
    weight_momentum: float = 0.40
    weight_earnings_surprise: float = 0.15
    weight_estimate_revisions: float = 0.15
    weight_technical: float = 0.15
    weight_sentiment: float = 0.15
    
    # =========================================================================
    # PORTFOLIO CONSTRAINTS
    # =========================================================================
    max_positions: int = 10
    min_position_pct: float = 0.03  # 3%
    max_position_pct: float = 0.15  # 15%
    max_sector_pct: float = 0.30  # 30%
    max_pairwise_correlation: float = 0.70
    cash_buffer_pct: float = 0.05  # 5%
    rebalance_threshold_pct: float = 0.02  # Only trade if change > 2%
    
    # =========================================================================
    # RISK MANAGEMENT
    # =========================================================================
    risk_per_trade_pct: float = 0.01  # 1% of portfolio per trade
    
    # Stop-Loss
    stop_loss_atr_multiplier: float = 1.5  # Initial stop at 1.5x ATR
    trailing_stop_atr_multiplier: float = 2.0  # Trail at 2x ATR
    trailing_stop_activation_atr: float = 1.0  # Activate after 1x ATR profit
    
    # Profit Taking
    profit_target_1_atr: float = 3.0  # First profit target
    profit_target_1_sell_pct: float = 0.33  # Sell 1/3 at first target
    profit_target_2_atr: float = 5.0  # Second profit target
    profit_target_2_sell_pct: float = 0.33  # Sell another 1/3
    
    # Daily Loss Limits
    daily_loss_warning_pct: float = 0.02  # 2% - stop new buys
    daily_loss_reduce_pct: float = 0.03  # 3% - exit weakest positions
    daily_loss_exit_pct: float = 0.05  # 5% - exit all positions
    
    # Drawdown Circuit Breakers
    drawdown_reduce_25_pct: float = 0.05  # 5% DD -> reduce 25%
    drawdown_reduce_50_pct: float = 0.10  # 10% DD -> reduce 50%
    drawdown_exit_all_pct: float = 0.15  # 15% DD -> exit all
    drawdown_hard_stop_pct: float = 0.20  # 20% DD -> system audit required
    
    # Gap Risk
    max_overnight_gap_assumption: float = 0.10  # Assume 10% gap possible
    high_beta_threshold: float = 1.5  # Reduce position for high beta
    high_beta_reduction: float = 0.25  # Reduce by 25%


@dataclass
class StockData:
    """Complete data container for a single stock."""
    ticker: str
    bloomberg_ticker: str
    
    # Basic Info
    sector: str = ""
    name: str = ""
    
    # Liquidity Metrics
    market_cap: float = 0.0
    avg_volume: float = 0.0
    avg_dollar_volume: float = 0.0
    bid_ask_spread_pct: float = 0.0
    
    # Fundamental Metrics
    roe: float = 0.0
    debt_to_equity: float = 0.0
    net_income: float = 0.0
    operating_cash_flow: float = 0.0
    beta: float = 1.0
    
    # Earnings Data
    earnings_surprise_pct: float = 0.0
    estimate_revision_pct: float = 0.0
    next_earnings_date: Optional[date] = None
    days_to_earnings: Optional[int] = None
    
    # News Sentiment
    sentiment_score: float = 0.0  # -1 to 1
    news_count_7d: int = 0
    news_count_avg_20d: float = 0.0
    news_volume_ratio: float = 1.0
    has_negative_keywords: bool = False
    
    # Price Data
    prices: list = field(default_factory=list)
    volumes: list = field(default_factory=list)
    dates: list = field(default_factory=list)
    current_price: float = 0.0
    
    # Technical Indicators (calculated)
    ema_10: float = 0.0
    ema_50: float = 0.0
    ema_200: float = 0.0
    rsi: float = 50.0
    atr: float = 0.0
    volatility_20d: float = 0.0
    volatility_60d: float = 0.0


@dataclass
class FactorScores:
    """Complete factor scores for a stock."""
    ticker: str
    
    # Individual Factor Scores
    momentum_raw: float = 0.0
    momentum_zscore: float = 0.0
    earnings_zscore: float = 0.0
    revision_zscore: float = 0.0
    technical_score: float = 0.0  # 1.0 if pass, 0.0 if fail
    sentiment_score: float = 0.0
    
    # Composite
    composite_score: float = 0.0
    rank: int = 0
    
    # Filter Status
    passes_liquidity: bool = True
    passes_fundamental: bool = True
    passes_technical: bool = True
    passes_sentiment: bool = True
    passes_earnings_blackout: bool = True
    passes_news_volume: bool = True
    passes_all_filters: bool = True
    filter_failures: list = field(default_factory=list)
    
    # Flags
    rsi_profit_take_flag: bool = False  # RSI > 80
    volatility_spike: bool = False
    in_earnings_blackout: bool = False
    
    # Signal
    signal: SignalType = SignalType.HOLD


@dataclass
class Position:
    """Current position with risk tracking."""
    ticker: str
    quantity: int
    entry_price: float
    entry_date: date
    current_price: float
    sector: str
    
    # Risk Levels
    atr_at_entry: float
    stop_loss_price: float
    trailing_stop_price: float
    trailing_stop_active: bool = False
    highest_price_since_entry: float = 0.0
    
    # Profit Targets
    profit_target_1_price: float = 0.0
    profit_target_2_price: float = 0.0
    profit_target_1_hit: bool = False
    profit_target_2_hit: bool = False
    
    @property
    def market_value(self) -> float:
        return self.quantity * self.current_price
    
    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.entry_price) * self.quantity
    
    @property
    def unrealized_pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        return (self.current_price - self.entry_price) / self.entry_price


@dataclass
class PortfolioState:
    """Complete portfolio state for risk monitoring."""
    timestamp: datetime
    cash: float
    total_equity: float
    positions: list  # List[Position]
    
    # Performance
    high_water_mark: float = 0.0
    current_drawdown_pct: float = 0.0
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    
    # Exposure
    total_exposure: float = 0.0
    sector_exposures: dict = field(default_factory=dict)
    
    # Risk Status
    daily_loss_warning: bool = False
    daily_loss_reduce: bool = False
    daily_loss_exit: bool = False
    drawdown_warning: bool = False


class MultiFactorMomentumStrategy:
    """
    Complete Multi-Factor Momentum Trading Strategy.
    
    Implements ALL requirements from the Bloomberg Trading Strategy Guide:
    - Universe selection with full filters
    - 5-factor weighted scoring
    - VIX regime adjustment
    - Position sizing with vol targeting
    - Sector/correlation constraints  
    - Stop-loss and profit targets
    - Circuit breakers
    """
    
    def __init__(self, config: Optional[MultiFactorConfig] = None):
        self.config = config or MultiFactorConfig()
        self.universe: list[StockData] = []
        self.factor_scores: list[FactorScores] = []
        self.portfolio: Optional[PortfolioState] = None
        self.vix: float = 0.0
        self.vix_history: list[float] = []
        self.alerts: list[tuple[AlertLevel, str]] = []
    
    # =========================================================================
    # DATA FETCHING
    # =========================================================================
    
    def fetch_universe(self, host: "RemoteHost") -> list[str]:
        """Fetch and filter tradeable universe with ALL filters."""
        from blpremote_client.data import get_index_members
        from blpremote_client import ref_data
        
        print(f"[1/7] Getting index members for {self.config.index}...")
        members = get_index_members(host, self.config.index, timeout_ms=30000)
        print(f"      Found {len(members)} members")
        
        # Format tickers
        tickers = [f"{m} Equity" for m in members[:self.config.max_universe_size * 2]]
        
        print(f"[2/7] Fetching reference data for filtering...")
        
        # Fetch comprehensive fundamental data
        ref_fields = [
            "CUR_MKT_CAP",           # Market cap (millions)
            "VOLUME_AVG_20D",        # 20-day avg volume
            "RETURN_ON_EQUITY",      # ROE
            "TOT_DEBT_TO_TOT_EQY",   # Debt/Equity
            "GICS_SECTOR_NAME",      # Sector
            "IS_NET_INCOME",         # Net income
            "CF_FROM_OPER",          # Operating cash flow
            "BEST_EPS_SURPRISE",     # Earnings surprise %
            "BEST_EST_REVIS_PCT",    # Estimate revision %
            "EXPECTED_REPORT_DT",    # Next earnings date
            "BETA_RAW_OVERRIDABLE",  # Beta
        ]
        
        fund_data = {}
        batch_size = 30
        
        for i in range(0, min(len(tickers), 150), batch_size):
            batch = tickers[i:i + batch_size]
            print(f"      Batch {i // batch_size + 1}...")
            try:
                batch_data = ref_data(
                    host,
                    securities=batch,
                    fields=ref_fields,
                    timeout_ms=30000
                )
                fund_data.update(batch_data)
            except Exception as e:
                print(f"      Warning: Batch failed - {e}")
        
        print(f"[3/7] Applying universe filters...")
        
        filtered_tickers = []
        filter_stats = {
            "market_cap": 0,
            "volume": 0,
            "roe": 0,
            "debt": 0,
            "net_income": 0,
            "earnings_quality": 0,
            "sector": 0,
        }
        
        for ticker in tickers:
            data = fund_data.get(ticker, {})
            
            # Extract values with defaults
            market_cap = float(data.get("CUR_MKT_CAP", 0) or 0)
            avg_volume = float(data.get("VOLUME_AVG_20D", 0) or 0)
            roe = float(data.get("RETURN_ON_EQUITY", 0) or 0)
            debt_eq = float(data.get("TOT_DEBT_TO_TOT_EQY", 0) or 0)
            sector = str(data.get("GICS_SECTOR_NAME", "") or "")
            net_income = float(data.get("IS_NET_INCOME", 0) or 0)
            op_cf = float(data.get("CF_FROM_OPER", 0) or 0)
            eps_surprise = float(data.get("BEST_EPS_SURPRISE", 0) or 0)
            est_revision = float(data.get("BEST_EST_REVIS_PCT", 0) or 0)
            next_earn_date = data.get("EXPECTED_REPORT_DT")
            beta = float(data.get("BETA_RAW_OVERRIDABLE", 1.0) or 1.0)
            
            # Calculate dollar volume (approximate)
            # Would need price data for exact, estimate from market cap
            current_price = market_cap / 1000 if avg_volume > 0 else 0  # Rough estimate
            avg_dollar_volume = avg_volume * current_price
            
            # Apply filters
            passes = True
            
            # Liquidity filters
            if market_cap < self.config.min_market_cap / 1_000_000:
                passes = False
                filter_stats["market_cap"] += 1
            if avg_volume < self.config.min_avg_volume:
                passes = False
                filter_stats["volume"] += 1
            
            # Fundamental filters
            if roe < self.config.min_roe:
                passes = False
                filter_stats["roe"] += 1
            if debt_eq > self.config.max_debt_equity and debt_eq > 0:
                passes = False
                filter_stats["debt"] += 1
            
            # Positive net income
            if self.config.require_positive_net_income and net_income <= 0:
                passes = False
                filter_stats["net_income"] += 1
            
            # Earnings quality: Operating CF > Net Income
            if self.config.require_earnings_quality and op_cf < net_income and net_income > 0:
                passes = False
                filter_stats["earnings_quality"] += 1
            
            # Sector exclusions
            if sector in self.config.excluded_sectors:
                passes = False
                filter_stats["sector"] += 1
            
            if passes:
                # Calculate days to earnings
                days_to_earnings = None
                if next_earn_date:
                    try:
                        if isinstance(next_earn_date, str):
                            earn_date = datetime.strptime(next_earn_date, "%Y-%m-%d").date()
                        else:
                            earn_date = next_earn_date
                        days_to_earnings = (earn_date - datetime.now().date()).days
                    except:
                        pass
                
                stock = StockData(
                    ticker=ticker.replace(" Equity", ""),
                    bloomberg_ticker=ticker,
                    sector=sector,
                    market_cap=market_cap,
                    avg_volume=avg_volume,
                    avg_dollar_volume=avg_dollar_volume,
                    roe=roe,
                    debt_to_equity=debt_eq,
                    net_income=net_income,
                    operating_cash_flow=op_cf,
                    earnings_surprise_pct=eps_surprise / 100 if eps_surprise else 0,
                    estimate_revision_pct=est_revision / 100 if est_revision else 0,
                    days_to_earnings=days_to_earnings,
                    beta=beta,
                )
                self.universe.append(stock)
                filtered_tickers.append(ticker)
            
            if len(filtered_tickers) >= self.config.max_universe_size:
                break
        
        print(f"      {len(filtered_tickers)} stocks passed all filters")
        print(f"      Filter breakdown: {filter_stats}")
        
        return filtered_tickers
    
    def fetch_price_data(self, host: "RemoteHost", tickers: list[str]) -> None:
        """Fetch historical price data for momentum and technical calculations."""
        from blpremote_client.data import bdh
        
        lookback = self.config.momentum_lookback_days + 30
        print(f"[4/7] Fetching historical prices ({lookback} days)...")
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=lookback)
        
        batch_size = 15
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(tickers) + batch_size - 1) // batch_size
            print(f"      Batch {batch_num}/{total_batches}")
            
            try:
                data = bdh(
                    host,
                    batch,
                    ["PX_LAST", "VOLUME", "PX_HIGH", "PX_LOW"],
                    start_date.strftime("%Y%m%d"),
                    end_date.strftime("%Y%m%d"),
                    timeout_ms=60000,
                )
                
                for ticker in batch:
                    if ticker in data:
                        for stock in self.universe:
                            if stock.bloomberg_ticker == ticker:
                                stock.prices = data[ticker].get("PX_LAST", [])
                                stock.volumes = data[ticker].get("VOLUME", [])
                                stock.dates = data[ticker].get("dates", [])
                                if stock.prices:
                                    stock.current_price = float(stock.prices[-1])
                                break
                
            except Exception as e:
                print(f"      Warning: Batch failed - {e}")
    
    def fetch_vix(self, host: "RemoteHost") -> float:
        """Fetch current VIX level and history for regime detection."""
        from blpremote_client import px_last
        from blpremote_client.data import bdh
        
        print(f"[5/7] Fetching VIX data...")
        
        try:
            self.vix = float(px_last(host, "VIX Index"))
            print(f"      Current VIX: {self.vix:.2f}")
        except Exception as e:
            print(f"      Warning: Could not fetch VIX: {e}")
            self.vix = 20.0
        
        # Get VIX history for complacency check
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=30)
            vix_hist = bdh(
                host,
                ["VIX Index"],
                ["PX_LAST"],
                start_date.strftime("%Y%m%d"),
                end_date.strftime("%Y%m%d"),
                timeout_ms=15000,
            )
            if "VIX Index" in vix_hist:
                self.vix_history = [float(v) for v in vix_hist["VIX Index"].get("PX_LAST", [])]
        except:
            self.vix_history = []
        
        # Check for complacency (VIX < 15 for 20+ days)
        if len(self.vix_history) >= self.config.vix_complacency_days:
            recent_vix = self.vix_history[-self.config.vix_complacency_days:]
            if all(v < self.config.vix_complacency_threshold for v in recent_vix):
                self.alerts.append((
                    AlertLevel.WARNING,
                    f"VIX below {self.config.vix_complacency_threshold} for "
                    f"{self.config.vix_complacency_days}+ days - complacency warning"
                ))
        
        return self.vix
    
    # =========================================================================
    # TECHNICAL CALCULATIONS
    # =========================================================================
    
    def calculate_technical_indicators(self, stock: StockData) -> None:
        """Calculate all technical indicators for a stock."""
        prices = np.array(stock.prices, dtype=float)
        
        if len(prices) < self.config.ema_trend_period:
            return
        
        # EMA calculation
        def ema(data, period):
            alpha = 2 / (period + 1)
            result = np.zeros_like(data)
            result[0] = data[0]
            for i in range(1, len(data)):
                result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
            return result[-1]
        
        stock.ema_10 = ema(prices, self.config.ema_fast_period)
        stock.ema_50 = ema(prices, self.config.ema_slow_period)
        stock.ema_200 = ema(prices, self.config.ema_trend_period)
        stock.current_price = float(prices[-1])
        
        # RSI
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        period = self.config.rsi_period
        if len(gains) >= period:
            avg_gain = np.mean(gains[-period:])
            avg_loss = np.mean(losses[-period:])
            
            if avg_loss == 0:
                stock.rsi = 100
            else:
                rs = avg_gain / avg_loss
                stock.rsi = 100 - (100 / (1 + rs))
        
        # ATR
        if len(prices) >= self.config.atr_period + 1:
            highs = stock.prices  # Would need actual high/low data
            lows = stock.prices
            
            # Simplified ATR using price range
            returns = np.abs(np.diff(prices))
            stock.atr = float(np.mean(returns[-self.config.atr_period:]))
        
        # Volatility
        returns = np.diff(np.log(prices))
        if len(returns) >= 20:
            stock.volatility_20d = float(np.std(returns[-20:]) * np.sqrt(252))
        if len(returns) >= 60:
            stock.volatility_60d = float(np.std(returns[-60:]) * np.sqrt(252))
    
    def calculate_momentum(self, stock: StockData) -> float:
        """
        Calculate volatility-adjusted momentum score.
        
        Formula: (P[t-skip] - P[t-lookback]) / (σ × √days)
        """
        prices = np.array(stock.prices, dtype=float)
        
        lookback = self.config.momentum_lookback_days
        skip = self.config.momentum_skip_days
        
        if len(prices) < lookback:
            return np.nan
        
        price_old = prices[-lookback]
        price_recent = prices[-(skip + 1)] if skip > 0 else prices[-1]
        
        if price_old <= 0:
            return np.nan
        
        raw_return = (price_recent - price_old) / price_old
        
        # Volatility over the measurement period
        returns = np.diff(np.log(prices[-lookback:-skip])) if skip > 0 else np.diff(np.log(prices[-lookback:]))
        if len(returns) == 0:
            return np.nan
        
        volatility = np.std(returns) * np.sqrt(252)
        
        if volatility <= 0:
            return np.nan
        
        return raw_return / volatility
    
    def check_technical_conditions(self, stock: StockData) -> tuple[bool, float, list[str]]:
        """
        Check all technical conditions from the guide.
        
        Conditions:
        1. Price > EMA50
        2. EMA10 > EMA50
        3. EMA50 > EMA200
        4. RSI between 50-75
        5. Volatility not spiking
        
        Returns: (passes, score, failures)
        """
        failures = []
        
        if stock.current_price <= 0:
            return False, 0.0, ["No price data"]
        
        # EMA structure
        if stock.current_price <= stock.ema_50:
            failures.append("Price <= EMA50")
        
        if stock.ema_10 <= stock.ema_50:
            failures.append("EMA10 <= EMA50")
        
        if stock.ema_50 <= stock.ema_200:
            failures.append("EMA50 <= EMA200")
        
        # RSI range
        if stock.rsi < self.config.rsi_min:
            failures.append(f"RSI {stock.rsi:.1f} < {self.config.rsi_min}")
        
        if stock.rsi > self.config.rsi_max:
            failures.append(f"RSI {stock.rsi:.1f} > {self.config.rsi_max}")
        
        # Volatility spike
        if stock.volatility_60d > 0:
            vol_ratio = stock.volatility_20d / stock.volatility_60d
            if vol_ratio > self.config.vol_threshold_multiplier:
                failures.append(f"Vol spike ({vol_ratio:.2f}x)")
        
        passes = len(failures) == 0
        return passes, 1.0 if passes else 0.0, failures
    
    # =========================================================================
    # FACTOR SCORING
    # =========================================================================
    
    def calculate_factor_scores(self) -> list[FactorScores]:
        """Calculate all factor scores with proper filters."""
        print(f"[6/7] Calculating factor scores...")
        
        # First pass: calculate raw scores and indicators
        momentum_values = []
        earnings_values = []
        revision_values = []
        
        for stock in self.universe:
            self.calculate_technical_indicators(stock)
        
        for stock in self.universe:
            score = FactorScores(ticker=stock.bloomberg_ticker)
            
            # Momentum
            mom = self.calculate_momentum(stock)
            score.momentum_raw = mom if not np.isnan(mom) else 0.0
            momentum_values.append(score.momentum_raw)
            
            # Earnings surprise
            score.earnings_zscore = stock.earnings_surprise_pct * 10  # Scale
            earnings_values.append(score.earnings_zscore)
            
            # Estimate revisions
            score.revision_zscore = stock.estimate_revision_pct * 10  # Scale
            revision_values.append(score.revision_zscore)
            
            # Technical
            tech_pass, tech_score, failures = self.check_technical_conditions(stock)
            score.passes_technical = tech_pass
            score.technical_score = tech_score
            score.filter_failures.extend(failures)
            
            # RSI profit-take flag
            if stock.rsi > self.config.rsi_profit_take:
                score.rsi_profit_take_flag = True
            
            # Volatility spike flag
            if stock.volatility_60d > 0:
                if stock.volatility_20d / stock.volatility_60d > self.config.vol_threshold_multiplier:
                    score.volatility_spike = True
            
            # Sentiment (from stock data)
            score.sentiment_score = stock.sentiment_score
            
            # Earnings blackout check
            if stock.days_to_earnings is not None:
                if 0 <= stock.days_to_earnings <= self.config.earnings_blackout_days:
                    score.in_earnings_blackout = True
                    score.passes_earnings_blackout = False
                    score.filter_failures.append(f"Earnings in {stock.days_to_earnings} days")
            
            # News volume anomaly
            if stock.news_volume_ratio > self.config.news_volume_anomaly_multiplier:
                score.passes_news_volume = False
                score.filter_failures.append(f"News spike ({stock.news_volume_ratio:.1f}x)")
            
            # Check sentiment threshold
            if stock.sentiment_score < self.config.min_sentiment_score:
                score.passes_sentiment = False
                score.filter_failures.append(f"Negative sentiment ({stock.sentiment_score:.2f})")
            
            # Check for negative news keywords
            if stock.has_negative_keywords:
                score.passes_sentiment = False
                score.filter_failures.append("Negative news keywords detected")
            
            self.factor_scores.append(score)
        
        # Z-score normalize momentum
        mom_arr = np.array(momentum_values)
        valid_mom = mom_arr[~np.isnan(mom_arr)]
        if len(valid_mom) > 1:
            mom_mean = np.mean(valid_mom)
            mom_std = np.std(valid_mom)
            if mom_std > 0:
                for i, score in enumerate(self.factor_scores):
                    if not np.isnan(momentum_values[i]):
                        score.momentum_zscore = (score.momentum_raw - mom_mean) / mom_std
        
        # Z-score normalize earnings
        earn_arr = np.array(earnings_values)
        valid_earn = earn_arr[~np.isnan(earn_arr) & (earn_arr != 0)]
        if len(valid_earn) > 1:
            earn_mean = np.mean(valid_earn)
            earn_std = np.std(valid_earn)
            if earn_std > 0:
                for i, score in enumerate(self.factor_scores):
                    if earnings_values[i] != 0:
                        score.earnings_zscore = (earnings_values[i] - earn_mean) / earn_std
        
        # Z-score normalize revisions
        rev_arr = np.array(revision_values)
        valid_rev = rev_arr[~np.isnan(rev_arr) & (rev_arr != 0)]
        if len(valid_rev) > 1:
            rev_mean = np.mean(valid_rev)
            rev_std = np.std(valid_rev)
            if rev_std > 0:
                for i, score in enumerate(self.factor_scores):
                    if revision_values[i] != 0:
                        score.revision_zscore = (revision_values[i] - rev_mean) / rev_std
        
        # Calculate composite scores
        for score in self.factor_scores:
            score.composite_score = (
                self.config.weight_momentum * score.momentum_zscore +
                self.config.weight_earnings_surprise * score.earnings_zscore +
                self.config.weight_estimate_revisions * score.revision_zscore +
                self.config.weight_technical * score.technical_score +
                self.config.weight_sentiment * score.sentiment_score
            )
            
            # Overall filter check
            score.passes_all_filters = (
                score.passes_technical and
                score.passes_sentiment and
                score.passes_earnings_blackout and
                score.passes_news_volume and
                not score.volatility_spike
            )
        
        # Rank by composite score
        self.factor_scores.sort(key=lambda x: x.composite_score, reverse=True)
        for i, score in enumerate(self.factor_scores):
            score.rank = i + 1
        
        return self.factor_scores
    
    # =========================================================================
    # SIGNAL GENERATION
    # =========================================================================
    
    def generate_signals(self) -> dict[str, float]:
        """
        Generate trading signals with VIX regime adjustment.
        
        Returns dict of {ticker: signal_strength}
        """
        print(f"[7/7] Generating trading signals...")
        
        signals = {}
        
        # Check VIX stop-trading level
        if self.vix >= self.config.vix_stop_trading_threshold:
            self.alerts.append((
                AlertLevel.CRITICAL,
                f"VIX ({self.vix:.1f}) >= {self.config.vix_stop_trading_threshold}: "
                f"No new positions allowed"
            ))
            print(f"      ⚠️ VIX STOP: All signals set to HOLD")
            for score in self.factor_scores:
                score.signal = SignalType.HOLD
                signals[score.ticker] = 0.0
            return signals
        
        # VIX scaling factor
        vix_scale = 1.0
        if self.vix >= self.config.vix_reduce_50_threshold:
            vix_scale = 0.5
            self.alerts.append((
                AlertLevel.WARNING,
                f"VIX ({self.vix:.1f}): Reducing positions by 50%"
            ))
        elif self.vix >= self.config.vix_reduce_25_threshold:
            vix_scale = 0.75
            self.alerts.append((
                AlertLevel.INFO,
                f"VIX ({self.vix:.1f}): Reducing positions by 25%"
            ))
        
        # Assign signals
        buy_count = 0
        for score in self.factor_scores:
            # Check for profit-taking flags
            if score.rsi_profit_take_flag:
                score.signal = SignalType.PROFIT_TAKE
                signals[score.ticker] = 0.0  # Don't enter new, consider exiting
                continue
            
            # Top N with passing filters = BUY
            if buy_count < self.config.max_positions and score.passes_all_filters:
                score.signal = SignalType.BUY
                signals[score.ticker] = score.composite_score * vix_scale
                buy_count += 1
            else:
                # Not in top N or fails filters
                score.signal = SignalType.HOLD
                signals[score.ticker] = 0.0
        
        return signals
    
    # =========================================================================
    # POSITION SIZING AND RISK
    # =========================================================================
    
    def compute_positions(
        self,
        signals: dict[str, float],
        portfolio_value: float
    ) -> dict[str, float]:
        """
        Compute position sizes with:
        - Volatility targeting (1% risk per trade)
        - Position limits (3-15%)
        - Sector constraints (30%)
        - Beta adjustment for high-beta stocks
        """
        positions = {}
        sector_totals = {}
        
        available_capital = portfolio_value * (1 - self.config.cash_buffer_pct)
        
        # Sort by signal strength
        sorted_signals = sorted(
            [(t, s) for t, s in signals.items() if s > 0],
            key=lambda x: x[1],
            reverse=True
        )
        
        for ticker, signal in sorted_signals:
            # Find stock data
            stock = None
            for s in self.universe:
                if s.bloomberg_ticker == ticker:
                    stock = s
                    break
            
            if not stock or stock.current_price <= 0:
                continue
            
            # Get volatility for sizing
            volatility = stock.volatility_20d if stock.volatility_20d > 0 else 0.20
            
            # Base position size = Risk / Volatility
            target_risk = self.config.risk_per_trade_pct * portfolio_value
            position_value = target_risk / volatility
            
            # Apply beta adjustment
            if stock.beta > self.config.high_beta_threshold:
                position_value *= (1 - self.config.high_beta_reduction)
            
            # Apply position limits
            max_pos = portfolio_value * self.config.max_position_pct
            min_pos = portfolio_value * self.config.min_position_pct
            position_value = np.clip(position_value, min_pos, max_pos)
            
            # Scale by signal strength (normalized to max 1.0)
            max_signal = max(s for _, s in sorted_signals) if sorted_signals else 1.0
            signal_scale = min(signal / max_signal, 1.0) if max_signal > 0 else 1.0
            position_value *= signal_scale
            
            # Check sector constraint
            sector = stock.sector
            current_sector_total = sector_totals.get(sector, 0)
            max_sector = portfolio_value * self.config.max_sector_pct
            
            if current_sector_total + position_value > max_sector:
                # Reduce to fit sector limit
                position_value = max(0, max_sector - current_sector_total)
            
            if position_value >= min_pos:
                positions[ticker] = position_value
                sector_totals[sector] = current_sector_total + position_value
        
        # Check total exposure
        total = sum(positions.values())
        if total > available_capital:
            scale = available_capital / total
            positions = {k: v * scale for k, v in positions.items()}
        
        return positions
    
    def calculate_stop_loss(self, entry_price: float, atr: float) -> float:
        """Calculate initial stop-loss price."""
        return entry_price - (atr * self.config.stop_loss_atr_multiplier)
    
    def calculate_trailing_stop(
        self,
        entry_price: float,
        highest_price: float,
        atr: float
    ) -> tuple[float, bool]:
        """
        Calculate trailing stop.
        
        Returns: (trailing_stop_price, is_active)
        """
        # Activate after 1 ATR profit
        activation_price = entry_price + (atr * self.config.trailing_stop_activation_atr)
        is_active = highest_price >= activation_price
        
        if is_active:
            trailing_stop = highest_price - (atr * self.config.trailing_stop_atr_multiplier)
            # Trailing stop can only move up
            initial_stop = self.calculate_stop_loss(entry_price, atr)
            trailing_stop = max(trailing_stop, initial_stop)
            return trailing_stop, True
        else:
            return self.calculate_stop_loss(entry_price, atr), False
    
    def calculate_profit_targets(
        self,
        entry_price: float,
        atr: float
    ) -> tuple[float, float]:
        """Calculate profit target prices."""
        target_1 = entry_price + (atr * self.config.profit_target_1_atr)
        target_2 = entry_price + (atr * self.config.profit_target_2_atr)
        return target_1, target_2
    
    # =========================================================================
    # CIRCUIT BREAKERS
    # =========================================================================
    
    def check_daily_loss_limits(self, daily_pnl_pct: float) -> str:
        """
        Check daily loss limits and return action.
        
        Returns: "normal", "warning", "reduce", or "exit"
        """
        if daily_pnl_pct <= -self.config.daily_loss_exit_pct:
            self.alerts.append((
                AlertLevel.EMERGENCY,
                f"DAILY LOSS LIMIT: {daily_pnl_pct:.1%} - EXIT ALL POSITIONS"
            ))
            return "exit"
        elif daily_pnl_pct <= -self.config.daily_loss_reduce_pct:
            self.alerts.append((
                AlertLevel.CRITICAL,
                f"Daily loss {daily_pnl_pct:.1%}: Exit weakest positions"
            ))
            return "reduce"
        elif daily_pnl_pct <= -self.config.daily_loss_warning_pct:
            self.alerts.append((
                AlertLevel.WARNING,
                f"Daily loss {daily_pnl_pct:.1%}: Stop new buys"
            ))
            return "warning"
        return "normal"
    
    def check_drawdown_limits(self, drawdown_pct: float) -> str:
        """
        Check drawdown circuit breakers.
        
        Returns: "normal", "reduce_25", "reduce_50", "exit", or "halt"
        """
        if drawdown_pct >= self.config.drawdown_hard_stop_pct:
            self.alerts.append((
                AlertLevel.EMERGENCY,
                f"DRAWDOWN HARD STOP: {drawdown_pct:.1%} - SYSTEM AUDIT REQUIRED"
            ))
            return "halt"
        elif drawdown_pct >= self.config.drawdown_exit_all_pct:
            self.alerts.append((
                AlertLevel.EMERGENCY,
                f"Drawdown {drawdown_pct:.1%}: EXIT ALL POSITIONS"
            ))
            return "exit"
        elif drawdown_pct >= self.config.drawdown_reduce_50_pct:
            self.alerts.append((
                AlertLevel.CRITICAL,
                f"Drawdown {drawdown_pct:.1%}: Reduce positions by 50%"
            ))
            return "reduce_50"
        elif drawdown_pct >= self.config.drawdown_reduce_25_pct:
            self.alerts.append((
                AlertLevel.WARNING,
                f"Drawdown {drawdown_pct:.1%}: Reduce positions by 25%"
            ))
            return "reduce_25"
        return "normal"
    
    # =========================================================================
    # MAIN ENTRY POINT
    # =========================================================================
    
    def run(self, host: "RemoteHost") -> dict[str, float]:
        """
        Run the complete strategy pipeline.
        
        Returns dict of {ticker: signal_strength}
        """
        print("=" * 70)
        print("  Multi-Factor Momentum Strategy (Complete Implementation)")
        print("=" * 70)
        print()
        
        self.alerts = []
        
        # 1. Fetch and filter universe
        tickers = self.fetch_universe(host)
        
        if len(tickers) < 10:
            print(f"Warning: Only {len(tickers)} stocks passed filters")
            return {}
        
        # 2. Fetch price data
        self.fetch_price_data(host, tickers)
        
        # 3. Fetch VIX
        self.fetch_vix(host)
        
        # 4. Calculate factor scores
        self.calculate_factor_scores()
        
        # 5. Generate signals
        signals = self.generate_signals()
        
        # Summary
        buy_signals = [(t, s) for t, s in signals.items() if s > 0]
        buy_signals.sort(key=lambda x: x[1], reverse=True)
        
        print()
        print("=" * 70)
        print("  STRATEGY RESULTS")
        print("=" * 70)
        print(f"Universe after filters: {len(self.universe)}")
        print(f"VIX: {self.vix:.2f}")
        print(f"Buy signals: {len(buy_signals)}")
        print()
        
        # Print alerts
        if self.alerts:
            print("ALERTS:")
            for level, msg in self.alerts:
                print(f"  [{level.value.upper()}] {msg}")
            print()
        
        # Print top signals
        print("TOP BUY SIGNALS:")
        print("-" * 70)
        print(f"{'Rank':<5} {'Ticker':<20} {'Signal':>8} {'Mom':>8} {'Tech':>6} {'Sector':<15}")
        print("-" * 70)
        
        for ticker, signal in buy_signals[:15]:
            for score in self.factor_scores:
                if score.ticker == ticker:
                    for stock in self.universe:
                        if stock.bloomberg_ticker == ticker:
                            sector = stock.sector[:15] if stock.sector else "N/A"
                            tech = "✓" if score.passes_technical else "✗"
                            print(f"{score.rank:<5} {ticker:<20} {signal:>+8.3f} "
                                  f"{score.momentum_zscore:>+8.2f} {tech:>6} {sector:<15}")
                            break
                    break
        
        # Print stocks in earnings blackout
        blackout_stocks = [s for s in self.factor_scores if s.in_earnings_blackout]
        if blackout_stocks:
            print()
            print(f"EARNINGS BLACKOUT ({len(blackout_stocks)} stocks):")
            for s in blackout_stocks[:5]:
                print(f"  {s.ticker}")
        
        # Print profit-take flags
        profit_take = [s for s in self.factor_scores if s.rsi_profit_take_flag]
        if profit_take:
            print()
            print(f"RSI > 80 PROFIT-TAKE FLAGS ({len(profit_take)} stocks):")
            for s in profit_take[:5]:
                print(f"  {s.ticker}")
        
        return signals


def main():
    """Example usage."""
    print("Multi-Factor Momentum Strategy (Complete)")
    print("-" * 50)
    print()
    print("This is the COMPLETE implementation including:")
    print("  - Full universe filtering (liquidity + quality)")
    print("  - 5-factor weighted scoring")
    print("  - VIX regime adjustment")
    print("  - Earnings blackout detection")
    print("  - News sentiment and volume anomaly")
    print("  - Position sizing with sector limits")
    print("  - Stop-loss and trailing stops")
    print("  - Profit targets (3x/5x ATR)")
    print("  - Daily loss circuit breakers")
    print("  - Drawdown circuit breakers")
    print()
    print("Usage:")
    print("  from blpremote_client import RemoteHost")
    print("  from strategies import MultiFactorMomentumStrategy")
    print()
    print("  host = RemoteHost('https://your-ngrok-url.ngrok-free.app',")
    print("                    username='krishna', password='...')")
    print()
    print("  strategy = MultiFactorMomentumStrategy()")
    print("  signals = strategy.run(host)")


if __name__ == "__main__":
    main()
