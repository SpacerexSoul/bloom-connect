"""
Algebraic Topology Market-Neutral Strategy

This proprietary trading strategy uses algebraic topology for market modeling
and has been consistently profitable for the last five years, including during
2022 when the market was losing money.

Strategy Overview:
1. Market Modeling: Model the market as a weighted graph of stocks
2. Laplacian Diffusion: Run diffusion on recent stock returns
3. Residual Extraction: Take residuals to estimate local pricings
4. Topological Compression: Apply persistent homology to correlation structure
5. Feature Extraction: Compress global market shape and regime features
6. Model Input: Feed compressed features into market-neutral model
"""

import sys
from pathlib import Path
from typing import Any, Optional
from datetime import date, datetime, timedelta
from dataclasses import dataclass, field

import numpy as np

# Add parent path for imports when running standalone
sys.path.insert(0, str(Path(__file__).parent.parent / "packages/blpremote_client/src"))


@dataclass
class StrategyConfig:
    """Configuration for the Algebraic Topology Strategy."""

    # Universe definition
    index: str = "SPX Index"  # Index to get members from
    max_securities: int = 500  # Maximum number of securities to analyze

    # Historical data parameters
    lookback_days: int = 252 * 5  # 5 years of daily data
    min_data_points: int = 252  # Minimum required data points

    # Laplacian diffusion parameters
    diffusion_steps: int = 10
    diffusion_alpha: float = 0.5

    # Persistent homology parameters
    max_dimension: int = 2  # Maximum homology dimension
    filtration_steps: int = 100

    # Trading parameters
    position_limit: float = 0.02  # Max position per security (2%)
    rebalance_frequency: int = 5  # Rebalance every N days


@dataclass
class MarketData:
    """Container for market data."""

    securities: list[str] = field(default_factory=list)
    prices: dict[str, np.ndarray] = field(default_factory=dict)
    returns: dict[str, np.ndarray] = field(default_factory=dict)
    dates: list[date] = field(default_factory=list)
    correlation_matrix: Optional[np.ndarray] = None


@dataclass
class TopologicalFeatures:
    """Container for extracted topological features."""

    persistence_diagrams: dict[int, np.ndarray] = field(default_factory=dict)
    betti_numbers: list[int] = field(default_factory=list)
    persistence_entropy: float = 0.0
    regime_features: np.ndarray = field(default_factory=lambda: np.array([]))


class AlgebraicTopologyStrategy:
    """
    Algebraic Topology Market-Neutral Trading Strategy.

    This strategy uses:
    - Weighted graph representation of the stock market
    - Laplacian diffusion for price residual estimation
    - Persistent homology for topological feature extraction
    - Market-neutral portfolio construction
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        self.config = config or StrategyConfig()
        self.market_data: Optional[MarketData] = None
        self.topo_features: Optional[TopologicalFeatures] = None
        self._graph_laplacian: Optional[np.ndarray] = None

    def fetch_data(self, host: "RemoteHost") -> MarketData:
        """
        Fetch required data from Bloomberg via the remote connector.

        This method:
        1. Gets index members (BDS request)
        2. Fetches historical prices (BDH request)
        3. Computes returns and correlation matrix

        Args:
            host: Connected RemoteHost instance

        Returns:
            MarketData object with prices, returns, and correlations
        """
        from blpremote_client.data import bdh, get_index_members

        print(f"[1/4] Fetching index members for {self.config.index}...")
        securities = get_index_members(host, self.config.index)

        # Limit to max_securities
        if len(securities) > self.config.max_securities:
            securities = securities[: self.config.max_securities]

        print(f"      Found {len(securities)} securities")

        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=self.config.lookback_days)

        print(f"[2/4] Fetching historical data ({start_date.date()} to {end_date.date()})...")
        print(f"      This may take several minutes for {len(securities)} securities...")

        # Fetch in batches to avoid timeouts
        batch_size = 50
        all_data = {}

        for i in range(0, len(securities), batch_size):
            batch = securities[i:i + batch_size]
            print(f"      Batch {i // batch_size + 1}/{(len(securities) + batch_size - 1) // batch_size}")

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

        print(f"[3/4] Processing price data...")
        market_data = self._process_price_data(all_data, securities)

        print(f"[4/4] Computing returns and correlation matrix...")
        market_data = self._compute_returns_and_correlation(market_data)

        self.market_data = market_data
        return market_data

    def _process_price_data(
        self, raw_data: dict[str, Any], securities: list[str]
    ) -> MarketData:
        """Process raw Bloomberg data into MarketData structure."""
        market_data = MarketData()

        for security in securities:
            if security not in raw_data:
                continue

            sec_data = raw_data[security]
            if "PX_LAST" not in sec_data:
                continue

            prices = sec_data["PX_LAST"]
            if len(prices) >= self.config.min_data_points:
                market_data.securities.append(security)
                market_data.prices[security] = np.array(prices, dtype=float)

        print(f"      {len(market_data.securities)} securities with sufficient data")
        return market_data

    def _compute_returns_and_correlation(self, market_data: MarketData) -> MarketData:
        """Compute log returns and correlation matrix."""
        # Compute log returns
        for security, prices in market_data.prices.items():
            returns = np.diff(np.log(prices))
            market_data.returns[security] = returns

        # Build returns matrix (securities x time)
        n_securities = len(market_data.securities)
        if n_securities == 0:
            return market_data

        min_length = min(len(r) for r in market_data.returns.values())
        returns_matrix = np.zeros((n_securities, min_length))

        for i, security in enumerate(market_data.securities):
            returns_matrix[i, :] = market_data.returns[security][:min_length]

        # Compute correlation matrix
        market_data.correlation_matrix = np.corrcoef(returns_matrix)

        return market_data

    def build_graph_laplacian(self) -> np.ndarray:
        """
        Build the graph Laplacian from the correlation structure.

        The market is modeled as a weighted graph where:
        - Nodes are stocks
        - Edge weights are derived from correlations
        """
        if self.market_data is None or self.market_data.correlation_matrix is None:
            raise ValueError("Market data must be fetched first")

        corr = self.market_data.correlation_matrix
        n = corr.shape[0]

        # Convert correlation to distance/adjacency
        # Using: weight = max(0, correlation) to get positive semi-definite
        adjacency = np.maximum(corr, 0)
        np.fill_diagonal(adjacency, 0)

        # Degree matrix
        degree = np.diag(adjacency.sum(axis=1))

        # Normalized Laplacian: L = I - D^(-1/2) A D^(-1/2)
        d_inv_sqrt = np.diag(1.0 / np.sqrt(np.diag(degree) + 1e-10))
        self._graph_laplacian = np.eye(n) - d_inv_sqrt @ adjacency @ d_inv_sqrt

        return self._graph_laplacian

    def run_laplacian_diffusion(self, returns: np.ndarray) -> np.ndarray:
        """
        Run Laplacian diffusion on returns to estimate local pricings.

        This smooths the returns across the graph structure, and the
        residuals represent local deviations from the diffused signal.

        Args:
            returns: Returns vector for current time step

        Returns:
            Residuals after diffusion (local pricing signals)
        """
        if self._graph_laplacian is None:
            self.build_graph_laplacian()

        L = self._graph_laplacian
        alpha = self.config.diffusion_alpha
        signal = returns.copy()

        # Run diffusion steps: x_{t+1} = (1 - alpha) * x_t + alpha * L @ x_t
        for _ in range(self.config.diffusion_steps):
            signal = (1 - alpha) * signal + alpha * (L @ signal)

        # Residuals = original - diffused
        residuals = returns - signal
        return residuals

    def compute_persistent_homology(self) -> TopologicalFeatures:
        """
        Compute persistent homology of the correlation structure.

        This extracts topological features that capture:
        - Global market shape
        - Regime features
        - Clusters and holes in correlation structure
        """
        if self.market_data is None or self.market_data.correlation_matrix is None:
            raise ValueError("Market data must be fetched first")

        corr = self.market_data.correlation_matrix
        n = corr.shape[0]

        # Convert correlation to distance for filtration
        # distance = sqrt(2 * (1 - correlation))
        distance = np.sqrt(2 * (1 - np.clip(corr, -1, 1)))
        np.fill_diagonal(distance, 0)

        features = TopologicalFeatures()

        # Simplified persistent homology computation
        # In production, use libraries like ripser, gudhi, or dionysus
        features = self._simplified_persistence(distance, features)

        self.topo_features = features
        return features

    def _simplified_persistence(
        self, distance_matrix: np.ndarray, features: TopologicalFeatures
    ) -> TopologicalFeatures:
        """
        Simplified persistence computation.

        Note: For production, use proper TDA libraries like:
        - ripser: pip install ripser
        - gudhi: pip install gudhi
        - giotto-tda: pip install giotto-tda
        """
        n = distance_matrix.shape[0]
        max_dist = distance_matrix.max()

        # H0: Connected components (simplified)
        # Track when each point connects to the growing complex
        thresholds = np.linspace(0, max_dist, self.config.filtration_steps)

        # Approximate Betti numbers at each threshold
        betti_0 = []
        for thresh in thresholds:
            # Count connected components via adjacency
            adj = (distance_matrix <= thresh).astype(int)
            np.fill_diagonal(adj, 1)

            # Simple connected component count
            visited = np.zeros(n, dtype=bool)
            components = 0
            for i in range(n):
                if not visited[i]:
                    # BFS from node i
                    stack = [i]
                    while stack:
                        node = stack.pop()
                        if not visited[node]:
                            visited[node] = True
                            neighbors = np.where(adj[node] > 0)[0]
                            stack.extend(neighbors)
                    components += 1
            betti_0.append(components)

        features.betti_numbers = [betti_0[-1]]  # Final Betti-0

        # Persistence entropy (simplified)
        betti_0_array = np.array(betti_0)
        changes = np.abs(np.diff(betti_0_array))
        if changes.sum() > 0:
            probs = changes / changes.sum()
            probs = probs[probs > 0]
            features.persistence_entropy = -np.sum(probs * np.log(probs))

        # Regime features from persistence landscape
        features.regime_features = np.array([
            np.mean(betti_0),
            np.std(betti_0),
            features.persistence_entropy,
            betti_0[0] - betti_0[-1],  # Total merges
        ])

        return features

    def generate_signals(self) -> dict[str, float]:
        """
        Generate trading signals based on topological analysis.

        Returns:
            Dictionary mapping security -> signal strength (-1 to 1)
        """
        if self.market_data is None:
            raise ValueError("Must fetch data first")

        if self.topo_features is None:
            self.compute_persistent_homology()

        signals = {}
        securities = self.market_data.securities

        # Get most recent returns
        latest_returns = np.array([
            self.market_data.returns[sec][-1]
            for sec in securities
        ])

        # Run Laplacian diffusion to get residuals
        residuals = self.run_laplacian_diffusion(latest_returns)

        # Compute z-scores of residuals
        residual_mean = residuals.mean()
        residual_std = residuals.std()

        if residual_std > 0:
            z_scores = (residuals - residual_mean) / residual_std
        else:
            z_scores = np.zeros_like(residuals)

        # Apply regime adjustment from topological features
        regime_adjustment = 1.0
        if self.topo_features is not None:
            # High persistence entropy indicates regime uncertainty
            if self.topo_features.persistence_entropy > 2.0:
                regime_adjustment = 0.5  # Reduce signal strength

        # Generate market-neutral signals
        for i, security in enumerate(securities):
            # Signal is negative of z-score (mean reversion)
            # with regime adjustment
            raw_signal = -z_scores[i] * regime_adjustment

            # Clip to [-1, 1]
            signals[security] = float(np.clip(raw_signal, -1, 1))

        # Ensure market neutrality (signals sum to ~0)
        signal_mean = np.mean(list(signals.values()))
        for sec in signals:
            signals[sec] -= signal_mean

        return signals

    def compute_positions(
        self, signals: dict[str, float], portfolio_value: float
    ) -> dict[str, float]:
        """
        Convert signals to position sizes.

        Args:
            signals: Signal strengths per security
            portfolio_value: Total portfolio value

        Returns:
            Dictionary mapping security -> position size (in dollars)
        """
        positions = {}
        max_position = portfolio_value * self.config.position_limit

        for security, signal in signals.items():
            # Position size proportional to signal strength
            position = signal * max_position
            positions[security] = position

        return positions

    def run(self, host: "RemoteHost") -> dict[str, float]:
        """
        Run the complete strategy pipeline.

        Args:
            host: Connected RemoteHost instance

        Returns:
            Dictionary of signals per security
        """
        print("=" * 60)
        print("Algebraic Topology Market-Neutral Strategy")
        print("=" * 60)
        print()

        # Fetch data
        self.fetch_data(host)

        if len(self.market_data.securities) < 10:
            print("Error: Insufficient securities with data")
            return {}

        print()
        print("Building topological model...")
        print("-" * 40)

        # Build graph structure
        print("[1/3] Building graph Laplacian...")
        self.build_graph_laplacian()

        # Compute persistent homology
        print("[2/3] Computing persistent homology...")
        self.compute_persistent_homology()

        # Generate signals
        print("[3/3] Generating trading signals...")
        signals = self.generate_signals()

        print()
        print("Strategy execution complete!")
        print(f"Generated signals for {len(signals)} securities")

        # Summary statistics
        signal_values = list(signals.values())
        print(f"Signal stats: mean={np.mean(signal_values):.4f}, "
              f"std={np.std(signal_values):.4f}")

        long_count = sum(1 for s in signal_values if s > 0.1)
        short_count = sum(1 for s in signal_values if s < -0.1)
        print(f"Positions: {long_count} long, {short_count} short")

        return signals


def main():
    """Example usage of the strategy."""
    # This would require a running Bloomberg server
    print("Algebraic Topology Market-Neutral Strategy")
    print("-" * 40)
    print()
    print("To run this strategy, you need:")
    print("1. A Windows machine running the blpremote_server")
    print("2. Bloomberg Terminal logged in on the Windows machine")
    print("3. Network connectivity from macOS to Windows")
    print()
    print("Example usage:")
    print()
    print("  from blpremote_client import RemoteHost")
    print("  from strategies import AlgebraicTopologyStrategy")
    print()
    print("  host = RemoteHost('http://192.168.1.100:8000',")
    print("                    username='krishna', password='...')")
    print()
    print("  strategy = AlgebraicTopologyStrategy()")
    print("  signals = strategy.run(host)")
    print()
    print("  print(signals)")


if __name__ == "__main__":
    main()
