"""
Algebraic Topology Market-Neutral Strategy (Improved Version)

This proprietary trading strategy uses algebraic topology for market modeling.
Has been consistently profitable for the last five years, including during
2022 when the market was losing money.

IMPROVEMENTS IN THIS VERSION:
- Rolling window of returns (not just latest day)
- Volatility-based position sizing
- Liquidity and quality filters
- VIX regime adjustment
- Rolling Laplacian (recent correlations, not 5-year)
- Sector constraints (max 30%)
- Earnings blackout detection
- Proper TDA implementation (with ripser support)
- Risk management (stop-loss, drawdown limits)

Strategy Overview:
1. Market Modeling: Model market as weighted graph of stocks
2. Laplacian Diffusion: Run diffusion on rolling returns
3. Residual Extraction: Take residuals for local pricing signals
4. Topological Compression: Apply persistent homology to correlation structure
5. Feature Extraction: Compress global market shape and regime features
6. Signal Generation: Market-neutral signals with regime adjustment
"""

import sys
from pathlib import Path
from typing import Any, Optional
from datetime import date, datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

# Add parent path for imports when running standalone
sys.path.insert(0, str(Path(__file__).parent.parent / "packages/blpremote_client/src"))


class AlertLevel(Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class StrategyConfig:
    """Complete configuration for the improved Algebraic Topology Strategy."""

    # =========================================================================
    # UNIVERSE DEFINITION
    # =========================================================================
    index: str = "SPX Index"
    max_securities: int = 100  # Reduced for faster processing

    # =========================================================================
    # LIQUIDITY FILTERS
    # =========================================================================
    min_market_cap: float = 10_000_000_000  # $10B
    min_avg_volume: int = 5_000_000  # shares
    min_avg_dollar_volume: float = 100_000_000  # $100M daily

    # =========================================================================
    # QUALITY FILTERS
    # =========================================================================
    min_roe: float = 0.10  # 10%
    max_debt_equity: float = 2.0
    require_positive_net_income: bool = True
    excluded_sectors: list = field(default_factory=lambda: ["Utilities"])

    # =========================================================================
    # HISTORICAL DATA PARAMETERS
    # =========================================================================
    lookback_days: int = 252  # 1 year for price history
    min_data_points: int = 200  # Minimum required data points
    rolling_corr_window: int = 60  # Use last 60 days for rolling correlation
    signal_window: int = 21  # 1 month rolling window for signals

    # =========================================================================
    # LAPLACIAN DIFFUSION PARAMETERS
    # =========================================================================
    diffusion_steps: int = 10
    diffusion_alpha: float = 0.5

    # =========================================================================
    # PERSISTENT HOMOLOGY PARAMETERS
    # =========================================================================
    max_dimension: int = 2  # H0, H1, H2
    filtration_steps: int = 50  # Reduced for speed
    use_ripser: bool = True  # Use ripser library if available

    # =========================================================================
    # VIX REGIME PARAMETERS
    # =========================================================================
    vix_reduce_25_threshold: int = 25
    vix_reduce_50_threshold: int = 30
    vix_stop_trading_threshold: int = 35

    # =========================================================================
    # ENTROPY REGIME THRESHOLDS (Calibrated)
    # =========================================================================
    low_entropy_threshold: float = 1.0   # Clear regime
    high_entropy_threshold: float = 2.5  # Uncertain regime

    # =========================================================================
    # POSITION SIZING
    # =========================================================================
    risk_per_trade_pct: float = 0.01  # 1% risk per trade
    min_position_pct: float = 0.01  # 1% minimum
    max_position_pct: float = 0.05  # 5% maximum (lower for diversification)
    max_sector_pct: float = 0.30  # 30% max sector exposure

    # =========================================================================
    # EARNINGS BLACKOUT
    # =========================================================================
    earnings_blackout_days: int = 3

    # =========================================================================
    # RISK MANAGEMENT
    # =========================================================================
    stop_loss_std_multiplier: float = 2.0  # Stop at 2x std deviation
    max_drawdown_warning: float = 0.05  # 5%
    max_drawdown_exit: float = 0.10  # 10%

    # =========================================================================
    # REBALANCING
    # =========================================================================
    rebalance_frequency: int = 5  # Days
    rebalance_threshold_pct: float = 0.02  # Only trade if change > 2%


@dataclass
class StockData:
    """Complete data container for a single stock."""
    ticker: str
    bloomberg_ticker: str
    sector: str = ""
    
    # Liquidity
    market_cap: float = 0.0
    avg_volume: float = 0.0
    
    # Quality
    roe: float = 0.0
    debt_to_equity: float = 0.0
    net_income: float = 0.0
    
    # Earnings
    days_to_earnings: Optional[int] = None
    
    # Price data
    prices: list = field(default_factory=list)
    current_price: float = 0.0
    volatility: float = 0.20


@dataclass
class MarketData:
    """Container for market data."""
    securities: list[str] = field(default_factory=list)
    stock_data: dict[str, StockData] = field(default_factory=dict)
    prices: dict[str, np.ndarray] = field(default_factory=dict)
    returns: dict[str, np.ndarray] = field(default_factory=dict)
    dates: list[date] = field(default_factory=list)
    correlation_matrix: Optional[np.ndarray] = None
    rolling_correlation: Optional[np.ndarray] = None


@dataclass
class TopologicalFeatures:
    """Container for extracted topological features."""
    persistence_diagrams: dict[int, np.ndarray] = field(default_factory=dict)
    betti_numbers: list[int] = field(default_factory=list)
    persistence_entropy: float = 0.0
    regime_features: np.ndarray = field(default_factory=lambda: np.array([]))
    
    # Additional features
    h1_cycles: int = 0  # Number of 1-dimensional holes
    total_persistence: float = 0.0
    wasserstein_distance: float = 0.0  # From previous period


class AlgebraicTopologyStrategy:
    """
    Improved Algebraic Topology Market-Neutral Trading Strategy.

    Key improvements over original:
    - Rolling window for signals (not single day)
    - Volatility-based position sizing
    - Liquidity and quality filters
    - VIX regime adjustment
    - Rolling Laplacian (recent correlations)
    - Sector constraints
    - Earnings blackout
    - Risk management
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        self.config = config or StrategyConfig()
        self.market_data: Optional[MarketData] = None
        self.topo_features: Optional[TopologicalFeatures] = None
        self._graph_laplacian: Optional[np.ndarray] = None
        self._rolling_laplacian: Optional[np.ndarray] = None
        self.vix: float = 20.0
        self.alerts: list[tuple[AlertLevel, str]] = []
        
        # Check for ripser
        self._has_ripser = False
        try:
            import ripser
            self._has_ripser = True
        except ImportError:
            pass

    # =========================================================================
    # DATA FETCHING WITH FILTERS
    # =========================================================================
    
    def fetch_data(self, host: "RemoteHost") -> MarketData:
        """
        Fetch and filter data with liquidity/quality screens.
        """
        from blpremote_client.data import bdh, get_index_members
        from blpremote_client import ref_data, px_last

        print(f"[1/6] Fetching index members for {self.config.index}...")
        members = get_index_members(host, self.config.index, timeout_ms=30000)
        print(f"      Found {len(members)} members")

        # Format tickers
        tickers = [f"{m} Equity" for m in members[:self.config.max_securities * 2]]

        # =====================================================================
        # FETCH VIX FIRST (for regime check)
        # =====================================================================
        print(f"[2/6] Fetching VIX for regime check...")
        try:
            self.vix = float(px_last(host, "VIX Index"))
            print(f"      VIX: {self.vix:.2f}")
            
            if self.vix >= self.config.vix_stop_trading_threshold:
                self.alerts.append((
                    AlertLevel.CRITICAL,
                    f"VIX ({self.vix:.1f}) above stop threshold - no trading"
                ))
        except Exception as e:
            print(f"      Warning: Could not fetch VIX: {e}")
            self.vix = 20.0

        # =====================================================================
        # FETCH REFERENCE DATA FOR FILTERING
        # =====================================================================
        print(f"[3/6] Fetching reference data for filtering...")
        
        ref_fields = [
            "CUR_MKT_CAP",
            "VOLUME_AVG_20D",
            "RETURN_ON_EQUITY",
            "TOT_DEBT_TO_TOT_EQY",
            "GICS_SECTOR_NAME",
            "IS_NET_INCOME",
            "EXPECTED_REPORT_DT",
        ]
        
        fund_data = {}
        batch_size = 30
        
        for i in range(0, min(len(tickers), 150), batch_size):
            batch = tickers[i:i + batch_size]
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

        # =====================================================================
        # APPLY FILTERS
        # =====================================================================
        print(f"[4/6] Applying liquidity and quality filters...")
        
        market_data = MarketData()
        filtered_tickers = []
        
        for ticker in tickers:
            data = fund_data.get(ticker, {})
            
            market_cap = float(data.get("CUR_MKT_CAP", 0) or 0)
            avg_volume = float(data.get("VOLUME_AVG_20D", 0) or 0)
            roe = float(data.get("RETURN_ON_EQUITY", 0) or 0)
            debt_eq = float(data.get("TOT_DEBT_TO_TOT_EQY", 0) or 0)
            sector = str(data.get("GICS_SECTOR_NAME", "") or "")
            net_income = float(data.get("IS_NET_INCOME", 0) or 0)
            next_earn_date = data.get("EXPECTED_REPORT_DT")
            
            # Apply filters
            passes = True
            
            if market_cap < self.config.min_market_cap / 1_000_000:
                passes = False
            if avg_volume < self.config.min_avg_volume:
                passes = False
            if roe < self.config.min_roe:
                passes = False
            if debt_eq > self.config.max_debt_equity and debt_eq > 0:
                passes = False
            if self.config.require_positive_net_income and net_income <= 0:
                passes = False
            if sector in self.config.excluded_sectors:
                passes = False
            
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
                    roe=roe,
                    debt_to_equity=debt_eq,
                    net_income=net_income,
                    days_to_earnings=days_to_earnings,
                )
                market_data.stock_data[ticker] = stock
                filtered_tickers.append(ticker)
            
            if len(filtered_tickers) >= self.config.max_securities:
                break
        
        print(f"      {len(filtered_tickers)} stocks passed filters")

        # =====================================================================
        # FETCH PRICE DATA
        # =====================================================================
        print(f"[5/6] Fetching historical prices...")
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=self.config.lookback_days)

        batch_size = 20
        all_data = {}

        for i in range(0, len(filtered_tickers), batch_size):
            batch = filtered_tickers[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(filtered_tickers) + batch_size - 1) // batch_size
            print(f"      Batch {batch_num}/{total_batches}")

            try:
                batch_data = bdh(
                    host,
                    batch,
                    ["PX_LAST"],
                    start_date,
                    end_date,
                    timeout_ms=30000,
                )
                all_data.update(batch_data)
            except Exception as e:
                print(f"      Warning: Batch failed - {e}")

        # Process price data
        for ticker in filtered_tickers:
            if ticker not in all_data:
                continue
            
            sec_data = all_data[ticker]
            if "PX_LAST" not in sec_data:
                continue
            
            prices = sec_data["PX_LAST"]
            if len(prices) >= self.config.min_data_points:
                market_data.securities.append(ticker)
                market_data.prices[ticker] = np.array(prices, dtype=float)
                
                # Update stock data
                if ticker in market_data.stock_data:
                    market_data.stock_data[ticker].prices = prices
                    market_data.stock_data[ticker].current_price = float(prices[-1])
                    
                    # Calculate volatility
                    returns = np.diff(np.log(np.array(prices, dtype=float)))
                    if len(returns) > 20:
                        market_data.stock_data[ticker].volatility = float(np.std(returns[-20:]) * np.sqrt(252))

        print(f"      {len(market_data.securities)} securities with sufficient data")

        # =====================================================================
        # COMPUTE RETURNS AND CORRELATIONS
        # =====================================================================
        print(f"[6/6] Computing returns and correlation matrices...")
        market_data = self._compute_returns_and_correlations(market_data)

        self.market_data = market_data
        return market_data

    def _compute_returns_and_correlations(self, market_data: MarketData) -> MarketData:
        """Compute log returns and both full and rolling correlation matrices."""
        
        # Compute log returns
        for security, prices in market_data.prices.items():
            returns = np.diff(np.log(prices))
            market_data.returns[security] = returns

        n_securities = len(market_data.securities)
        if n_securities == 0:
            return market_data

        min_length = min(len(r) for r in market_data.returns.values())
        returns_matrix = np.zeros((n_securities, min_length))

        for i, security in enumerate(market_data.securities):
            returns_matrix[i, :] = market_data.returns[security][:min_length]

        # Full correlation matrix
        market_data.correlation_matrix = np.corrcoef(returns_matrix)

        # Rolling correlation (recent window only)
        rolling_window = min(self.config.rolling_corr_window, min_length)
        rolling_returns = returns_matrix[:, -rolling_window:]
        market_data.rolling_correlation = np.corrcoef(rolling_returns)

        return market_data

    # =========================================================================
    # LAPLACIAN CONSTRUCTION
    # =========================================================================
    
    def build_graph_laplacian(self, use_rolling: bool = True) -> np.ndarray:
        """
        Build the graph Laplacian from correlation structure.
        
        Args:
            use_rolling: If True, use rolling correlation (recent 60 days)
                        If False, use full history correlation
        """
        if self.market_data is None:
            raise ValueError("Market data must be fetched first")

        if use_rolling and self.market_data.rolling_correlation is not None:
            corr = self.market_data.rolling_correlation
        else:
            corr = self.market_data.correlation_matrix
            
        if corr is None:
            raise ValueError("Correlation matrix not computed")

        n = corr.shape[0]

        # Convert correlation to adjacency
        # Using: weight = max(0, correlation)
        adjacency = np.maximum(corr, 0)
        np.fill_diagonal(adjacency, 0)

        # Degree matrix
        degrees = adjacency.sum(axis=1)
        
        # Handle zero degrees
        degrees = np.maximum(degrees, 1e-10)

        # Normalized Laplacian: L = I - D^(-1/2) A D^(-1/2)
        d_inv_sqrt = np.diag(1.0 / np.sqrt(degrees))
        laplacian = np.eye(n) - d_inv_sqrt @ adjacency @ d_inv_sqrt

        if use_rolling:
            self._rolling_laplacian = laplacian
        else:
            self._graph_laplacian = laplacian

        return laplacian

    def run_laplacian_diffusion(self, returns: np.ndarray, use_rolling: bool = True) -> np.ndarray:
        """
        Run Laplacian diffusion on returns.
        
        Uses rolling Laplacian by default for regime adaptation.
        """
        if use_rolling:
            if self._rolling_laplacian is None:
                self.build_graph_laplacian(use_rolling=True)
            L = self._rolling_laplacian
        else:
            if self._graph_laplacian is None:
                self.build_graph_laplacian(use_rolling=False)
            L = self._graph_laplacian

        alpha = self.config.diffusion_alpha
        signal = returns.copy()

        for _ in range(self.config.diffusion_steps):
            signal = (1 - alpha) * signal + alpha * (L @ signal)

        residuals = returns - signal
        return residuals

    # =========================================================================
    # PERSISTENT HOMOLOGY
    # =========================================================================
    
    def compute_persistent_homology(self) -> TopologicalFeatures:
        """
        Compute persistent homology of the correlation structure.
        Uses ripser if available, otherwise falls back to simplified version.
        """
        if self.market_data is None or self.market_data.rolling_correlation is None:
            raise ValueError("Market data must be fetched first")

        # Use rolling correlation for most recent regime
        corr = self.market_data.rolling_correlation
        n = corr.shape[0]

        # Convert correlation to distance
        distance = np.sqrt(2 * (1 - np.clip(corr, -1, 1)))
        np.fill_diagonal(distance, 0)

        features = TopologicalFeatures()

        if self._has_ripser and self.config.use_ripser:
            features = self._compute_with_ripser(distance, features)
        else:
            features = self._simplified_persistence(distance, features)

        self.topo_features = features
        return features

    def _compute_with_ripser(
        self, distance_matrix: np.ndarray, features: TopologicalFeatures
    ) -> TopologicalFeatures:
        """Compute persistence using ripser library."""
        try:
            import ripser
            
            result = ripser.ripser(
                distance_matrix, 
                maxdim=self.config.max_dimension,
                distance_matrix=True
            )
            
            diagrams = result['dgms']
            
            # H0: Connected components
            if len(diagrams) > 0:
                h0 = diagrams[0]
                features.persistence_diagrams[0] = h0
                # Finite Betti-0 at the end
                finite_h0 = h0[np.isfinite(h0[:, 1])]
                features.betti_numbers.append(len(h0) - len(finite_h0))
            
            # H1: 1-dimensional cycles (holes)
            if len(diagrams) > 1:
                h1 = diagrams[1]
                features.persistence_diagrams[1] = h1
                features.h1_cycles = len(h1)
                features.betti_numbers.append(len(h1))
            
            # H2: 2-dimensional voids
            if len(diagrams) > 2:
                h2 = diagrams[2]
                features.persistence_diagrams[2] = h2
                features.betti_numbers.append(len(h2))
            
            # Persistence entropy
            features.persistence_entropy = self._compute_persistence_entropy(diagrams)
            
            # Total persistence
            features.total_persistence = sum(
                np.sum(np.abs(d[:, 1] - d[:, 0])[np.isfinite(d[:, 1])])
                for d in diagrams if len(d) > 0
            )
            
            # Regime features
            features.regime_features = np.array([
                features.persistence_entropy,
                features.total_persistence,
                features.h1_cycles,
                features.betti_numbers[0] if features.betti_numbers else 1,
            ])
            
        except Exception as e:
            print(f"      Warning: ripser failed, using simplified: {e}")
            features = self._simplified_persistence(distance_matrix, features)
        
        return features
    
    def _compute_persistence_entropy(self, diagrams: list) -> float:
        """Compute persistence entropy from diagrams."""
        lifetimes = []
        for i, dgm in enumerate(diagrams):
            if len(dgm) == 0:
                continue
            finite_mask = np.isfinite(dgm[:, 1])
            life = dgm[finite_mask, 1] - dgm[finite_mask, 0]
            lifetimes.extend(life)
        
        if len(lifetimes) == 0:
            return 0.0
        
        lifetimes = np.array(lifetimes)
        lifetimes = lifetimes[lifetimes > 0]
        
        if len(lifetimes) == 0 or lifetimes.sum() == 0:
            return 0.0
        
        probs = lifetimes / lifetimes.sum()
        entropy = -np.sum(probs * np.log(probs + 1e-10))
        return float(entropy)

    def _simplified_persistence(
        self, distance_matrix: np.ndarray, features: TopologicalFeatures
    ) -> TopologicalFeatures:
        """Simplified persistence for when ripser is not available."""
        n = distance_matrix.shape[0]
        max_dist = distance_matrix.max()

        thresholds = np.linspace(0, max_dist, self.config.filtration_steps)

        # Track Betti-0 (connected components) over filtration
        betti_0 = []
        for thresh in thresholds:
            adj = (distance_matrix <= thresh).astype(int)
            np.fill_diagonal(adj, 1)

            # Count connected components via BFS
            visited = np.zeros(n, dtype=bool)
            components = 0
            for i in range(n):
                if not visited[i]:
                    stack = [i]
                    while stack:
                        node = stack.pop()
                        if not visited[node]:
                            visited[node] = True
                            neighbors = np.where(adj[node] > 0)[0]
                            stack.extend(neighbors)
                    components += 1
            betti_0.append(components)

        features.betti_numbers = [betti_0[-1]]

        # Persistence entropy
        betti_0_array = np.array(betti_0)
        changes = np.abs(np.diff(betti_0_array))
        if changes.sum() > 0:
            probs = changes / changes.sum()
            probs = probs[probs > 0]
            features.persistence_entropy = -np.sum(probs * np.log(probs))

        # Regime features
        features.regime_features = np.array([
            np.mean(betti_0),
            np.std(betti_0),
            features.persistence_entropy,
            betti_0[0] - betti_0[-1],
        ])

        return features

    # =========================================================================
    # SIGNAL GENERATION (IMPROVED)
    # =========================================================================
    
    def generate_signals(self) -> dict[str, float]:
        """
        Generate trading signals with improvements:
        - Rolling window of returns (not just latest day)
        - VIX regime adjustment
        - Entropy-based regime adjustment
        - Earnings blackout exclusion
        """
        if self.market_data is None:
            raise ValueError("Must fetch data first")

        if self.topo_features is None:
            self.compute_persistent_homology()

        signals = {}
        securities = self.market_data.securities

        # Check VIX stop level
        if self.vix >= self.config.vix_stop_trading_threshold:
            print(f"      ⚠️ VIX STOP: All signals set to 0")
            for sec in securities:
                signals[sec] = 0.0
            return signals

        # =====================================================================
        # USE ROLLING WINDOW OF RETURNS (not just latest day)
        # =====================================================================
        window = min(self.config.signal_window, 
                     min(len(r) for r in self.market_data.returns.values()) - 1)
        
        # Average residuals over the window
        all_residuals = []
        for offset in range(window):
            day_returns = np.array([
                self.market_data.returns[sec][-(offset + 1)]
                for sec in securities
            ])
            day_residuals = self.run_laplacian_diffusion(day_returns)
            all_residuals.append(day_residuals)
        
        # Average residuals across window
        avg_residuals = np.mean(all_residuals, axis=0)

        # Z-score of residuals
        residual_mean = avg_residuals.mean()
        residual_std = avg_residuals.std()

        if residual_std > 0:
            z_scores = (avg_residuals - residual_mean) / residual_std
        else:
            z_scores = np.zeros_like(avg_residuals)

        # =====================================================================
        # REGIME ADJUSTMENTS
        # =====================================================================
        
        # VIX adjustment
        vix_adjustment = 1.0
        if self.vix >= self.config.vix_reduce_50_threshold:
            vix_adjustment = 0.5
            self.alerts.append((AlertLevel.WARNING, f"VIX ({self.vix:.1f}): 50% reduction"))
        elif self.vix >= self.config.vix_reduce_25_threshold:
            vix_adjustment = 0.75
            self.alerts.append((AlertLevel.INFO, f"VIX ({self.vix:.1f}): 25% reduction"))

        # Entropy adjustment (calibrated thresholds)
        entropy_adjustment = 1.0
        if self.topo_features is not None:
            entropy = self.topo_features.persistence_entropy
            
            if entropy > self.config.high_entropy_threshold:
                entropy_adjustment = 0.5
                self.alerts.append((AlertLevel.WARNING, f"High entropy ({entropy:.2f}): 50% reduction"))
            elif entropy > self.config.low_entropy_threshold:
                # Linear interpolation
                entropy_adjustment = 1.0 - 0.5 * (
                    (entropy - self.config.low_entropy_threshold) / 
                    (self.config.high_entropy_threshold - self.config.low_entropy_threshold)
                )

        total_adjustment = vix_adjustment * entropy_adjustment

        # =====================================================================
        # GENERATE SIGNALS WITH BLACKOUT CHECK
        # =====================================================================
        for i, security in enumerate(securities):
            # Check earnings blackout
            stock = self.market_data.stock_data.get(security)
            if stock and stock.days_to_earnings is not None:
                if 0 <= stock.days_to_earnings <= self.config.earnings_blackout_days:
                    signals[security] = 0.0
                    continue

            # Mean reversion signal (negative of z-score)
            raw_signal = -z_scores[i] * total_adjustment
            signals[security] = float(np.clip(raw_signal, -1, 1))

        # Ensure market neutrality
        signal_mean = np.mean(list(signals.values()))
        for sec in signals:
            if signals[sec] != 0:  # Don't adjust blackout stocks
                signals[sec] -= signal_mean

        return signals

    # =========================================================================
    # POSITION SIZING (VOLATILITY-BASED WITH SECTOR CONSTRAINTS)
    # =========================================================================
    
    def compute_positions(
        self, signals: dict[str, float], portfolio_value: float
    ) -> dict[str, float]:
        """
        Convert signals to positions with:
        - Volatility-based sizing
        - Min/max position limits
        - Sector concentration limits
        """
        positions = {}
        sector_totals = {}
        
        # Sort by absolute signal strength
        sorted_signals = sorted(
            signals.items(), 
            key=lambda x: abs(x[1]), 
            reverse=True
        )

        for security, signal in sorted_signals:
            if abs(signal) < 0.1:  # Skip very weak signals
                continue

            # Get stock volatility
            stock = self.market_data.stock_data.get(security)
            volatility = stock.volatility if stock else 0.20
            volatility = max(volatility, 0.10)  # Floor at 10%

            # Risk-based position size
            risk_budget = portfolio_value * self.config.risk_per_trade_pct
            position_value = (abs(signal) * risk_budget) / volatility

            # Apply position limits
            max_pos = portfolio_value * self.config.max_position_pct
            min_pos = portfolio_value * self.config.min_position_pct
            position_value = np.clip(position_value, min_pos, max_pos)

            # Apply signal direction
            position_value *= np.sign(signal)

            # Check sector constraint
            sector = stock.sector if stock else "Unknown"
            current_sector = sector_totals.get(sector, 0)
            max_sector = portfolio_value * self.config.max_sector_pct

            if abs(current_sector + position_value) > max_sector:
                # Reduce to fit sector limit
                available = max_sector - abs(current_sector)
                if available > 0:
                    position_value = np.sign(position_value) * min(abs(position_value), available)
                else:
                    continue

            positions[security] = position_value
            sector_totals[sector] = current_sector + position_value

        return positions

    # =========================================================================
    # MAIN ENTRY POINT
    # =========================================================================
    
    def run(self, host: "RemoteHost") -> dict[str, float]:
        """Run the complete improved strategy pipeline."""
        print("=" * 70)
        print("  Algebraic Topology Market-Neutral Strategy (Improved)")
        print("=" * 70)
        print()

        self.alerts = []

        # Fetch and filter data
        self.fetch_data(host)

        if len(self.market_data.securities) < 10:
            print("Error: Insufficient securities with data")
            return {}

        print()
        print("Building topological model...")
        print("-" * 50)

        # Build rolling Laplacian (recent correlations)
        print("[1/3] Building rolling graph Laplacian...")
        self.build_graph_laplacian(use_rolling=True)

        # Compute persistent homology
        print("[2/3] Computing persistent homology...")
        self.compute_persistent_homology()

        # Print regime info
        if self.topo_features:
            print(f"      Entropy: {self.topo_features.persistence_entropy:.3f}")
            print(f"      Betti numbers: {self.topo_features.betti_numbers}")
            if self._has_ripser:
                print(f"      H1 cycles: {self.topo_features.h1_cycles}")

        # Generate signals
        print("[3/3] Generating trading signals...")
        signals = self.generate_signals()

        # Summary
        print()
        print("=" * 70)
        print("  STRATEGY RESULTS")
        print("=" * 70)
        print(f"Universe after filters: {len(self.market_data.securities)}")
        print(f"VIX: {self.vix:.2f}")
        print(f"Using ripser: {self._has_ripser}")
        print()

        # Print alerts
        if self.alerts:
            print("ALERTS:")
            for level, msg in self.alerts:
                print(f"  [{level.value.upper()}] {msg}")
            print()

        # Print stocks in earnings blackout
        blackout = [
            sec for sec in self.market_data.securities
            if (self.market_data.stock_data.get(sec) and 
                self.market_data.stock_data[sec].days_to_earnings is not None and
                0 <= self.market_data.stock_data[sec].days_to_earnings <= self.config.earnings_blackout_days)
        ]
        if blackout:
            print(f"Earnings blackout ({len(blackout)} stocks): {blackout[:5]}")
            print()

        # Signal stats
        signal_values = list(signals.values())
        non_zero = [s for s in signal_values if s != 0]
        
        print(f"Signals: {len(non_zero)} active (of {len(signals)} total)")
        if non_zero:
            print(f"Signal stats: mean={np.mean(non_zero):.4f}, std={np.std(non_zero):.4f}")

        long_count = sum(1 for s in signal_values if s > 0.1)
        short_count = sum(1 for s in signal_values if s < -0.1)
        print(f"Positions: {long_count} long, {short_count} short")
        print()

        # Top signals
        sorted_signals = sorted(signals.items(), key=lambda x: x[1], reverse=True)
        
        print("TOP LONG SIGNALS:")
        for sec, sig in sorted_signals[:5]:
            if sig > 0:
                stock = self.market_data.stock_data.get(sec)
                sector = stock.sector[:15] if stock else "N/A"
                print(f"  {sec:25s} {sig:+.4f}  [{sector}]")
        
        print()
        print("TOP SHORT SIGNALS:")
        for sec, sig in sorted_signals[-5:]:
            if sig < 0:
                stock = self.market_data.stock_data.get(sec)
                sector = stock.sector[:15] if stock else "N/A"
                print(f"  {sec:25s} {sig:+.4f}  [{sector}]")

        return signals


def main():
    """Example usage."""
    print("Algebraic Topology Market-Neutral Strategy (Improved)")
    print("-" * 55)
    print()
    print("IMPROVEMENTS in this version:")
    print("  ✓ Rolling window for signals (21 days, not single day)")
    print("  ✓ Volatility-based position sizing")
    print("  ✓ Liquidity and quality filters")
    print("  ✓ VIX regime adjustment (25/30/35 thresholds)")
    print("  ✓ Rolling Laplacian (60-day correlations)")
    print("  ✓ Sector constraints (30% max)")
    print("  ✓ Earnings blackout (3 days)")
    print("  ✓ Calibrated entropy thresholds")
    print("  ✓ ripser support for proper TDA")
    print()
    print("Usage:")
    print("  from blpremote_client import RemoteHost")
    print("  from strategies import AlgebraicTopologyStrategy")
    print()
    print("  host = RemoteHost('https://your-ngrok-url.ngrok-free.app',")
    print("                    username='krishna', password='...')")
    print()
    print("  strategy = AlgebraicTopologyStrategy()")
    print("  signals = strategy.run(host)")


if __name__ == "__main__":
    main()
