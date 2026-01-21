"""
Multi-Factor Momentum Trading Strategy

A momentum + quality + technical + sentiment multi-factor strategy that:
1. Filters S&P 500 using liquidity and quality screens
2. Scores stocks using weighted factors (momentum, earnings, revisions, technical, sentiment)
3. Generates buy/sell signals for top-ranked stocks
4. Applies VIX-based position sizing and risk controls

Based on specification from TRADING_SYSTEM_SPECIFICATION.md
"""

import sys
from pathlib import Path
from typing import Any, Optional
from datetime import date, datetime, timedelta
from dataclasses import dataclass, field

import numpy as np

# Add parent path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "packages/blpremote_client/src"))


@dataclass
class MultiFactorConfig:
    """Configuration for Multi-Factor Momentum Strategy."""
    
    # Universe
    index: str = "SPX Index"
    max_universe_size: int = 100
    
    # Universe Filters
    min_market_cap: float = 10_000_000_000  # $10B
    min_avg_volume: int = 5_000_000  # shares
    min_roe: float = 0.10  # 10%
    max_debt_equity: float = 2.0
    require_positive_net_income: bool = True
    excluded_sectors: list = field(default_factory=lambda: ["Utilities"])
    
    # Momentum Parameters
    momentum_lookback_days: int = 252  # 12 months
    momentum_skip_days: int = 21  # Skip most recent month
    
    # Technical Parameters
    ema_fast_period: int = 10
    ema_slow_period: int = 50
    ema_trend_period: int = 200
    rsi_period: int = 14
    rsi_min: int = 50
    rsi_max: int = 75
    atr_period: int = 14
    
    # Volatility Filter
    vol_lookback_days: int = 20
    vol_baseline_days: int = 60
    vol_threshold_multiplier: float = 1.5
    vix_reduce_25_threshold: int = 25
    vix_reduce_50_threshold: int = 30
    vix_stop_trading_threshold: int = 35
    
    # Signal Weights (must sum to 1.0)
    weight_momentum: float = 0.40
    weight_earnings_surprise: float = 0.15
    weight_estimate_revisions: float = 0.15
    weight_technical: float = 0.15
    weight_sentiment: float = 0.15
    
    # Portfolio Parameters
    max_positions: int = 10
    min_position_pct: float = 0.03  # 3%
    max_position_pct: float = 0.15  # 15%
    max_sector_pct: float = 0.30  # 30%
    
    # Risk Parameters
    risk_per_trade_pct: float = 0.01  # 1%
    stop_loss_atr_multiplier: float = 1.5


@dataclass
class StockData:
    """Container for a single stock's data."""
    ticker: str
    bloomberg_ticker: str
    sector: str = ""
    market_cap: float = 0.0
    avg_volume: float = 0.0
    roe: float = 0.0
    debt_to_equity: float = 0.0
    net_income: float = 0.0
    earnings_surprise: float = 0.0
    estimate_revision: float = 0.0
    prices: list = field(default_factory=list)
    volumes: list = field(default_factory=list)
    dates: list = field(default_factory=list)


@dataclass
class FactorScores:
    """Factor scores for a stock."""
    ticker: str
    momentum_raw: float = 0.0
    momentum_zscore: float = 0.0
    earnings_zscore: float = 0.0
    revision_zscore: float = 0.0
    technical_pass: bool = False
    technical_score: float = 0.0  # 1.0 if pass, 0.0 if fail
    sentiment_score: float = 0.0  # -1 to 1
    composite_score: float = 0.0
    rank: int = 0
    passes_filters: bool = True
    filter_failures: list = field(default_factory=list)


class MultiFactorMomentumStrategy:
    """
    Multi-Factor Momentum Trading Strategy.
    
    Factors (with weights):
    - Momentum (40%): 12-1 month volatility-adjusted return
    - Earnings Surprise (15%): Latest quarter surprise vs expectations
    - Estimate Revisions (15%): 30-day analyst estimate changes
    - Technical (15%): EMA alignment + RSI confirmation
    - Sentiment (15%): News sentiment score (simplified)
    """
    
    def __init__(self, config: Optional[MultiFactorConfig] = None):
        self.config = config or MultiFactorConfig()
        self.universe: list[StockData] = []
        self.factor_scores: list[FactorScores] = []
        self.vix: float = 0.0
    
    def fetch_universe(self, host: "RemoteHost") -> list[str]:
        """Fetch and filter tradeable universe from index."""
        from blpremote_client.data import get_index_members, bdh
        from blpremote_client import ref_data
        
        print(f"[1/6] Getting index members for {self.config.index}...")
        members = get_index_members(host, self.config.index, timeout_ms=30000)
        print(f"      Found {len(members)} members")
        
        # Format tickers properly
        tickers = [f"{m} Equity" for m in members[:self.config.max_universe_size * 2]]
        
        print(f"[2/6] Fetching reference data for filtering...")
        # Fetch fundamental data for filtering
        try:
            fund_data = ref_data(
                host,
                securities=tickers[:50],  # Start with subset
                fields=[
                    "CUR_MKT_CAP",
                    "VOLUME_AVG_20D", 
                    "RETURN_ON_EQUITY",
                    "TOT_DEBT_TO_TOT_EQY",
                    "GICS_SECTOR_NAME",
                ],
                timeout_ms=30000
            )
        except Exception as e:
            print(f"      Warning: Could not fetch reference data: {e}")
            fund_data = {}
        
        # Apply filters
        print(f"[3/6] Applying universe filters...")
        filtered_tickers = []
        
        for ticker in tickers:
            data = fund_data.get(ticker, {})
            
            # Extract values with defaults
            market_cap = data.get("CUR_MKT_CAP", 0) or 0
            avg_volume = data.get("VOLUME_AVG_20D", 0) or 0
            roe = data.get("RETURN_ON_EQUITY", 0) or 0
            debt_eq = data.get("TOT_DEBT_TO_TOT_EQY", 0) or 0
            sector = data.get("GICS_SECTOR_NAME", "") or ""
            
            # Apply filters
            passes = True
            if market_cap < self.config.min_market_cap / 1_000_000:  # Bloomberg returns in millions
                passes = False
            if avg_volume < self.config.min_avg_volume:
                passes = False
            if roe < self.config.min_roe:
                passes = False
            if debt_eq > self.config.max_debt_equity:
                passes = False
            if sector in self.config.excluded_sectors:
                passes = False
            
            if passes:
                stock = StockData(
                    ticker=ticker.replace(" Equity", ""),
                    bloomberg_ticker=ticker,
                    sector=sector,
                    market_cap=market_cap,
                    avg_volume=avg_volume,
                    roe=roe,
                    debt_to_equity=debt_eq,
                )
                self.universe.append(stock)
                filtered_tickers.append(ticker)
            
            if len(filtered_tickers) >= self.config.max_universe_size:
                break
        
        print(f"      {len(filtered_tickers)} stocks passed filters")
        return filtered_tickers
    
    def fetch_price_data(self, host: "RemoteHost", tickers: list[str]) -> None:
        """Fetch historical price data for momentum calculation."""
        from blpremote_client.data import bdh
        
        print(f"[4/6] Fetching historical prices ({self.config.momentum_lookback_days + 30} days)...")
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=self.config.momentum_lookback_days + 30)
        
        # Fetch in batches
        batch_size = 20
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i:i + batch_size]
            print(f"      Batch {i // batch_size + 1}/{(len(tickers) + batch_size - 1) // batch_size}")
            
            try:
                data = bdh(
                    host,
                    batch,
                    ["PX_LAST", "VOLUME"],
                    start_date.strftime("%Y%m%d"),
                    end_date.strftime("%Y%m%d"),
                    timeout_ms=60000,
                )
                
                # Store price data
                for ticker in batch:
                    if ticker in data:
                        for stock in self.universe:
                            if stock.bloomberg_ticker == ticker:
                                stock.prices = data[ticker].get("PX_LAST", [])
                                stock.volumes = data[ticker].get("VOLUME", [])
                                stock.dates = data[ticker].get("dates", [])
                                break
                
            except Exception as e:
                print(f"      Warning: Batch failed - {e}")
    
    def fetch_vix(self, host: "RemoteHost") -> float:
        """Fetch current VIX level for risk adjustment."""
        from blpremote_client import px_last
        
        try:
            self.vix = px_last(host, "VIX Index")
            print(f"      VIX: {self.vix:.2f}")
        except Exception as e:
            print(f"      Warning: Could not fetch VIX: {e}")
            self.vix = 20.0  # Default assumption
        
        return self.vix
    
    def calculate_momentum(self, stock: StockData) -> float:
        """
        Calculate volatility-adjusted momentum score.
        
        Formula: (12-1 month return) / volatility
        """
        prices = np.array(stock.prices, dtype=float)
        
        if len(prices) < self.config.momentum_lookback_days:
            return np.nan
        
        # 12-1 month return (skip most recent month)
        skip = self.config.momentum_skip_days
        lookback = self.config.momentum_lookback_days
        
        if len(prices) < lookback:
            return np.nan
        
        price_old = prices[-lookback]
        price_recent = prices[-(skip + 1)]  # Skip recent month
        
        if price_old <= 0:
            return np.nan
        
        raw_return = (price_recent - price_old) / price_old
        
        # Calculate volatility over the period
        returns = np.diff(np.log(prices[-lookback:-skip]))
        if len(returns) == 0:
            return np.nan
        
        volatility = np.std(returns) * np.sqrt(252)
        
        if volatility <= 0:
            return np.nan
        
        return raw_return / volatility
    
    def calculate_technical_score(self, stock: StockData) -> tuple[bool, float, list[str]]:
        """
        Check technical conditions.
        
        Conditions:
        1. Price > EMA50 (above medium-term trend)
        2. EMA10 > EMA50 (short-term momentum positive)
        3. EMA50 > EMA200 (long-term uptrend)
        4. RSI in range [50, 75]
        5. Volatility not spiking
        
        Returns: (passes, score, failures)
        """
        prices = np.array(stock.prices, dtype=float)
        failures = []
        
        if len(prices) < self.config.ema_trend_period:
            return False, 0.0, ["Insufficient data"]
        
        current_price = prices[-1]
        
        # Calculate EMAs
        def ema(data, period):
            alpha = 2 / (period + 1)
            result = np.zeros_like(data)
            result[0] = data[0]
            for i in range(1, len(data)):
                result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
            return result[-1]
        
        ema_10 = ema(prices, self.config.ema_fast_period)
        ema_50 = ema(prices, self.config.ema_slow_period)
        ema_200 = ema(prices, self.config.ema_trend_period)
        
        # Calculate RSI
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[-self.config.rsi_period:])
        avg_loss = np.mean(losses[-self.config.rsi_period:])
        
        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        
        # Calculate volatility
        returns = np.diff(np.log(prices))
        vol_20 = np.std(returns[-20:]) * np.sqrt(252) if len(returns) >= 20 else 0
        vol_60 = np.std(returns[-60:]) * np.sqrt(252) if len(returns) >= 60 else vol_20
        
        # Check conditions
        passes = True
        
        if current_price <= ema_50:
            passes = False
            failures.append("Price <= EMA50")
        
        if ema_10 <= ema_50:
            passes = False
            failures.append("EMA10 <= EMA50")
        
        if ema_50 <= ema_200:
            passes = False
            failures.append("EMA50 <= EMA200")
        
        if rsi < self.config.rsi_min:
            passes = False
            failures.append(f"RSI {rsi:.1f} < {self.config.rsi_min}")
        
        if rsi > self.config.rsi_max:
            passes = False
            failures.append(f"RSI {rsi:.1f} > {self.config.rsi_max}")
        
        if vol_60 > 0 and vol_20 > vol_60 * self.config.vol_threshold_multiplier:
            passes = False
            failures.append("Volatility spike")
        
        return passes, 1.0 if passes else 0.0, failures
    
    def calculate_factor_scores(self) -> list[FactorScores]:
        """Calculate all factor scores and composite ranking."""
        print(f"[5/6] Calculating factor scores...")
        
        scores = []
        momentum_raw_values = []
        earnings_values = []
        revision_values = []
        
        # First pass: calculate raw scores
        for stock in self.universe:
            score = FactorScores(ticker=stock.bloomberg_ticker)
            
            # Momentum
            mom = self.calculate_momentum(stock)
            score.momentum_raw = mom if not np.isnan(mom) else 0.0
            momentum_raw_values.append(score.momentum_raw)
            
            # Earnings surprise (would come from ref_data, using placeholder)
            score.earnings_zscore = stock.earnings_surprise
            earnings_values.append(score.earnings_zscore)
            
            # Estimate revisions (would come from ref_data, using placeholder)
            score.revision_zscore = stock.estimate_revision
            revision_values.append(score.revision_zscore)
            
            # Technical
            tech_pass, tech_score, failures = self.calculate_technical_score(stock)
            score.technical_pass = tech_pass
            score.technical_score = tech_score
            score.filter_failures = failures
            
            # Sentiment (placeholder - would come from news API)
            score.sentiment_score = 0.0
            
            scores.append(score)
        
        # Z-score normalize momentum
        mom_arr = np.array(momentum_raw_values)
        mom_mean = np.nanmean(mom_arr)
        mom_std = np.nanstd(mom_arr)
        
        for score in scores:
            if mom_std > 0:
                score.momentum_zscore = (score.momentum_raw - mom_mean) / mom_std
            else:
                score.momentum_zscore = 0.0
        
        # Calculate composite scores
        for score in scores:
            score.composite_score = (
                self.config.weight_momentum * score.momentum_zscore +
                self.config.weight_earnings_surprise * score.earnings_zscore +
                self.config.weight_estimate_revisions * score.revision_zscore +
                self.config.weight_technical * score.technical_score +
                self.config.weight_sentiment * score.sentiment_score
            )
            
            # Check if passes all filters
            score.passes_filters = score.technical_pass
        
        # Rank by composite score
        scores.sort(key=lambda x: x.composite_score, reverse=True)
        for i, score in enumerate(scores):
            score.rank = i + 1
        
        self.factor_scores = scores
        return scores
    
    def generate_signals(self) -> dict[str, float]:
        """
        Generate trading signals based on factor scores.
        
        Returns dict of {ticker: signal} where signal is:
        - Positive: BUY strength
        - Negative: SELL/Avoid
        - Zero: HOLD
        """
        print(f"[6/6] Generating trading signals...")
        
        signals = {}
        
        # Apply VIX adjustment
        if self.vix >= self.config.vix_stop_trading_threshold:
            print(f"      ⚠️ VIX ({self.vix:.1f}) above stop threshold ({self.config.vix_stop_trading_threshold})")
            print(f"      All signals set to HOLD")
            for score in self.factor_scores:
                signals[score.ticker] = 0.0
            return signals
        
        # VIX scaling factor
        vix_scale = 1.0
        if self.vix >= self.config.vix_reduce_50_threshold:
            vix_scale = 0.5
            print(f"      VIX ({self.vix:.1f}): Reducing position sizes by 50%")
        elif self.vix >= self.config.vix_reduce_25_threshold:
            vix_scale = 0.75
            print(f"      VIX ({self.vix:.1f}): Reducing position sizes by 25%")
        
        # Top N stocks that pass filters get BUY signals
        buy_count = 0
        for score in self.factor_scores:
            if buy_count < self.config.max_positions and score.passes_filters:
                # Signal strength = composite score * VIX scaling
                signals[score.ticker] = score.composite_score * vix_scale
                buy_count += 1
            else:
                # Not in top N or fails filters
                signals[score.ticker] = -score.composite_score if score.composite_score > 0 else score.composite_score
        
        return signals
    
    def compute_positions(
        self,
        signals: dict[str, float],
        portfolio_value: float
    ) -> dict[str, float]:
        """
        Convert signals to dollar position sizes using volatility targeting.
        
        Position size = (risk_per_trade * portfolio) / volatility
        """
        positions = {}
        
        # Get current prices and volatility for position sizing
        available_capital = portfolio_value * (1 - 0.05)  # 5% cash buffer
        
        for ticker, signal in signals.items():
            if signal <= 0:
                continue  # Only size BUY signals
            
            # Find the stock's volatility
            volatility = 0.20  # Default 20% annual volatility assumption
            for stock in self.universe:
                if stock.bloomberg_ticker == ticker and len(stock.prices) >= 20:
                    returns = np.diff(np.log(np.array(stock.prices, dtype=float)))
                    volatility = np.std(returns[-20:]) * np.sqrt(252)
                    break
            
            if volatility <= 0:
                volatility = 0.20
            
            # Calculate position size
            target_risk = self.config.risk_per_trade_pct * portfolio_value
            position_value = target_risk / volatility
            
            # Apply constraints
            max_pos = portfolio_value * self.config.max_position_pct
            min_pos = portfolio_value * self.config.min_position_pct
            position_value = np.clip(position_value, min_pos, max_pos)
            
            # Scale by signal strength (normalized)
            position_value *= min(abs(signal), 1.0)
            
            positions[ticker] = position_value
        
        # Ensure we don't exceed available capital
        total = sum(positions.values())
        if total > available_capital:
            scale = available_capital / total
            positions = {k: v * scale for k, v in positions.items()}
        
        return positions
    
    def run(self, host: "RemoteHost") -> dict[str, float]:
        """
        Run the complete strategy pipeline.
        
        Returns dict of {ticker: signal}
        """
        print("=" * 60)
        print("  Multi-Factor Momentum Strategy")
        print("=" * 60)
        print()
        
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
        print("=" * 60)
        print("  Strategy Results")
        print("=" * 60)
        print(f"Universe size: {len(self.universe)}")
        print(f"VIX: {self.vix:.2f}")
        print(f"Buy signals: {len(buy_signals)}")
        print()
        
        print("Top Buy Signals:")
        for ticker, signal in buy_signals[:10]:
            # Find rank and score details
            for score in self.factor_scores:
                if score.ticker == ticker:
                    print(f"  {ticker:25s} signal: {signal:+.3f} (mom: {score.momentum_zscore:+.2f}, rank: {score.rank})")
                    break
        
        return signals


def main():
    """Example usage."""
    print("Multi-Factor Momentum Strategy")
    print("-" * 40)
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
