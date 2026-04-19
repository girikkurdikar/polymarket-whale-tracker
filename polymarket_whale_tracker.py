#!/usr/bin/env python3
"""
Polymarket Whale Tracker Bot
Monitors top 20 wallets by 30-day profit and tracks their new bets in real-time.
"""

import requests
import json
import time
import os
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
import signal
import sys

# Configuration
BASE_URL = "https://data-api.polymarket.com"
LEADERBOARD_LIMIT = 20
TIMEFRAME = "30d"
CHECK_INTERVAL = 300  # 5 minutes in seconds
DATA_FILE = "whale_positions.json"

@dataclass
class Position:
    """Represents a single market position"""
    market_slug: str
    market_question: str
    outcome: str  # "Yes" or "No"
    size: float  # USD position size
    avg_price: float

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "Position":
        return cls(**data)

@dataclass
class Whale:
    """Represents a tracked whale wallet"""
    address: str
    profit_30d: float
    volume_30d: float
    positions: Dict[str, Position]  # market_slug -> Position

    def to_dict(self) -> Dict:
        return {
            "address": self.address,
            "profit_30d": self.profit_30d,
            "volume_30d": self.volume_30d,
            "positions": {k: v.to_dict() for k, v in self.positions.items()}
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Whale":
        positions = {k: Position.from_dict(v) for k, v in data.get("positions", {}).items()}
        return cls(
            address=data["address"],
            profit_30d=data["profit_30d"],
            volume_30d=data["volume_30d"],
            positions=positions
        )

class PolymarketAPI:
    """Handles all API interactions with Polymarket"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "PolymarketWhaleTracker/1.0",
            "Accept": "application/json"
        })

    def get_leaderboard(self, limit: int = 20, timeframe: str = "30d") -> List[Dict]:
        """Fetch top wallets by profit"""
        url = f"{BASE_URL}/leaderboard"
        params = {
            "limit": limit,
            "sort_by": "profit",
            "timeframe": timeframe
        }

        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Failed to fetch leaderboard: {e}")
            return []

    def get_positions(self, wallet_address: str) -> List[Dict]:
        """Fetch current open positions for a wallet"""
        url = f"{BASE_URL}/positions"
        params = {"user": wallet_address}

        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Failed to fetch positions for {wallet_address}: {e}")
            return []

    def get_recent_activity(self, wallet_address: str, limit: int = 5) -> List[Dict]:
        """Fetch recent trading activity for a wallet"""
        url = f"{BASE_URL}/activity"
        params = {
            "user": wallet_address,
            "limit": limit
        }

        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Failed to fetch activity for {wallet_address}: {e}")
            return []

class WhaleTracker:
    """Main tracking logic"""

    def __init__(self):
        self.api = PolymarketAPI()
        self.whales: Dict[str, Whale] = {}
        self.running = True

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        print("\n[SYSTEM] Shutdown signal received. Saving state...")
        self.running = False
        self.save_state()
        sys.exit(0)

    def load_state(self) -> bool:
        """Load previous state from JSON file"""
        if not os.path.exists(DATA_FILE):
            print(f"[INFO] No previous state found at {DATA_FILE}")
            return False

        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)

            self.whales = {
                addr: Whale.from_dict(whale_data) 
                for addr, whale_data in data.items()
            }
            print(f"[INFO] Loaded {len(self.whales)} whales from previous state")
            return True
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"[WARNING] Failed to load state: {e}")
            return False

    def save_state(self):
        """Save current state to JSON file"""
        try:
            data = {addr: whale.to_dict() for addr, whale in self.whales.items()}
            with open(DATA_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"[INFO] State saved to {DATA_FILE}")
        except IOError as e:
            print(f"[ERROR] Failed to save state: {e}")

    def fetch_leaderboard(self) -> List[str]:
        """Get top 20 wallet addresses from leaderboard"""
        print(f"[{self._timestamp()}] Fetching top {LEADERBOARD_LIMIT} whales...")

        leaderboard = self.api.get_leaderboard(LEADERBOARD_LIMIT, TIMEFRAME)
        addresses = []

        for entry in leaderboard:
            addr = entry.get("address") or entry.get("user") or entry.get("wallet")
            if addr:
                addresses.append(addr)
                # Initialize whale if not exists
                if addr not in self.whales:
                    self.whales[addr] = Whale(
                        address=addr,
                        profit_30d=entry.get("profit", 0),
                        volume_30d=entry.get("volume", 0),
                        positions={}
                    )
                else:
                    # Update stats
                    self.whales[addr].profit_30d = entry.get("profit", 0)
                    self.whales[addr].volume_30d = entry.get("volume", 0)

        print(f"[{self._timestamp()}] Found {len(addresses)} whales")
        return addresses

    def scan_positions(self, wallet_address: str) -> Dict[str, Position]:
        """Scan current positions for a wallet"""
        positions_data = self.api.get_positions(wallet_address)
        positions = {}

        for pos in positions_data:
            try:
                # Extract market info
                market = pos.get("market", {})
                slug = market.get("slug", "unknown")
                question = market.get("question", "Unknown Market")

                # Determine outcome and size
                outcome = pos.get("outcome", "Unknown")
                size = float(pos.get("size", 0))
                avg_price = float(pos.get("avgPrice", 0) or pos.get("avg_price", 0))

                # Skip zero-size positions
                if size <= 0:
                    continue

                positions[slug] = Position(
                    market_slug=slug,
                    market_question=question,
                    outcome=outcome,
                    size=size,
                    avg_price=avg_price
                )
            except (KeyError, TypeError, ValueError) as e:
                continue  # Skip malformed positions

        return positions

    def check_new_bets(self, wallet_address: str, current_positions: Dict[str, Position]):
        """Compare current positions with previous state to detect new bets"""
        whale = self.whales.get(wallet_address)
        if not whale:
            return

        previous_positions = whale.positions

        # Check for new positions or increased sizes
        for slug, pos in current_positions.items():
            if slug not in previous_positions:
                # Completely new position
                self._print_alert(wallet_address, pos, "NEW BET")
            else:
                old_pos = previous_positions[slug]
                size_diff = pos.size - old_pos.size

                # Significant increase (>10% or >$1000)
                if size_diff > 1000 or (old_pos.size > 0 and size_diff / old_pos.size > 0.1):
                    self._print_alert(wallet_address, pos, "INCREASED", size_diff)

        # Check for closed positions (optional - uncomment if needed)
        # for slug in previous_positions:
        #     if slug not in current_positions:
        #         print(f"[{self._timestamp()}] Whale {self._short_addr(wallet_address)} CLOSED: {previous_positions[slug].market_question}")

        # Update stored positions
        whale.positions = current_positions

    def _print_alert(self, wallet: str, pos: Position, alert_type: str, size_diff: float = 0):
        """Print formatted alert to console"""
        short_addr = self._short_addr(wallet)
        timestamp = self._timestamp()

        if alert_type == "NEW BET":
            print(f"\n[{timestamp}] 🐋 Whale {short_addr} NEW BET: ${pos.size:,.0f} on \"{pos.market_question}\" ({pos.outcome}) at ${pos.avg_price:.2f}")
        elif alert_type == "INCREASED":
            print(f"[{timestamp}] 🐋 Whale {short_addr} ADDED ${size_diff:,.0f} to \"{pos.market_question}\" ({pos.outcome}) - Total: ${pos.size:,.0f}")

    def _short_addr(self, addr: str) -> str:
        """Return shortened wallet address"""
        if len(addr) > 10:
            return f"{addr[:6]}...{addr[-4:]}"
        return addr

    def _timestamp(self) -> str:
        """Return current timestamp string"""
        return datetime.now().strftime("%H:%M")

    def run_initial_scan(self):
        """Perform initial scan of all whales"""
        addresses = self.fetch_leaderboard()

        print(f"[{self._timestamp()}] Scanning positions for {len(addresses)} whales...")

        for i, addr in enumerate(addresses, 1):
            positions = self.scan_positions(addr)
            self.whales[addr].positions = positions
            print(f"  [{i}/{len(addresses)}] {self._short_addr(addr)}: {len(positions)} positions")
            time.sleep(0.5)  # Rate limiting

        self.save_state()
        print(f"[{self._timestamp()}] Initial scan complete. Monitoring for new bets...\n")

    def run_monitor_loop(self):
        """Main monitoring loop"""
        self.run_initial_scan()

        cycle = 0
        while self.running:
            cycle += 1
            print(f"\n[{self._timestamp()}] Check #{cycle} - Scanning for new bets...")

            addresses = self.fetch_leaderboard()
            new_bets_found = 0

            for addr in addresses:
                if not self.running:
                    break

                current_positions = self.scan_positions(addr)

                # Count new bets for summary
                previous_slugs = set(self.whales[addr].positions.keys())
                current_slugs = set(current_positions.keys())
                new_bets_found += len(current_slugs - previous_slugs)

                self.check_new_bets(addr, current_positions)
                time.sleep(0.5)  # Rate limiting between wallets

            if new_bets_found == 0:
                print(f"[{self._timestamp()}] No new bets detected")

            self.save_state()

            # Countdown for next check
            if self.running:
                print(f"[{self._timestamp()}] Next check in 5 minutes...")
                for remaining in range(CHECK_INTERVAL, 0, -10):
                    if not self.running:
                        break
                    time.sleep(10)

def main():
    """Entry point"""
    print("=" * 60)
    print("🐋 POLYMARKET WHALE TRACKER 🐋")
    print("=" * 60)
    print(f"Tracking top {LEADERBOARD_LIMIT} wallets by 30-day profit")
    print(f"Checking every {CHECK_INTERVAL // 60} minutes")
    print(f"Data stored in: {DATA_FILE}")
    print("=" * 60)
    print()

    tracker = WhaleTracker()

    # Try to load previous state
    if tracker.load_state():
        print(f"[{tracker._timestamp()}] Resuming monitoring...\n")

    try:
        tracker.run_monitor_loop()
    except KeyboardInterrupt:
        print("\n\n[SYSTEM] Interrupted by user. Shutting down...")
        tracker.save_state()
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        tracker.save_state()
        raise

if __name__ == "__main__":
    main()
