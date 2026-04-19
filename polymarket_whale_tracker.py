#!/usr/bin/env python3
"""
Polymarket Whale Tracker Bot
Monitors whale wallets and tracks their bets in real-time.
Uses working Data API endpoints from docs.polymarket.com
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
DATA_API_URL = "https://data-api.polymarket.com"
GAMMA_API_URL = "https://gamma-api.polymarket.com"
CHECK_INTERVAL = 300  # 5 minutes in seconds
DATA_FILE = "whale_positions.json"

# Top whale wallets to track (manually curated list of known large traders)
# You can modify this list with wallets you want to track
DEFAULT_WHALE_WALLETS = [
    # Example whales - replace with actual addresses you want to track
    # These are placeholder examples - you should find real whale wallets
    # from polymarketanalytics.com or other sources
    "0x56687bf447db6ffa42ffe2204a05edaa20f55839",
    "0x6af75d4e4aaf700450efbac3708cce1665810ff1",
]

@dataclass
class Position:
    """Represents a single market position"""
    market_slug: str
    market_question: str
    outcome: str
    size: float
    avg_price: float
    current_value: float
    pnl: float
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "Position":
        return cls(**data)

@dataclass
class Whale:
    """Represents a tracked whale wallet"""
    address: str
    positions: Dict[str, Position]
    total_value: float = 0.0
    
    def to_dict(self) -> Dict:
        return {
            "address": self.address,
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "total_value": self.total_value
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "Whale":
        positions = {k: Position.from_dict(v) for k, v in data.get("positions", {}).items()}
        return cls(
            address=data["address"],
            positions=positions,
            total_value=data.get("total_value", 0.0)
        )

class PolymarketAPI:
    """Handles all API interactions with Polymarket"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "PolymarketWhaleTracker/1.0",
            "Accept": "application/json"
        })
    
    def get_positions(self, wallet_address: str) -> List[Dict]:
        """Fetch current open positions for a user - WORKING ENDPOINT"""
        url = f"{DATA_API_URL}/positions"
        params = {
            "user": wallet_address,
            "sizeThreshold": 1,  # Minimum $1 positions
            "limit": 100
        }
        
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Failed to fetch positions for {wallet_address}: {e}")
            return []
    
    def get_user_value(self, wallet_address: str) -> float:
        """Get total value of user's positions"""
        url = f"{DATA_API_URL}/value"
        params = {"user": wallet_address}
        
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            return float(data.get("value", 0))
        except requests.exceptions.RequestException:
            return 0.0
    
    def get_recent_trades(self, wallet_address: str, limit: int = 10) -> List[Dict]:
        """Fetch recent trades for a user"""
        url = f"{DATA_API_URL}/trades"
        params = {
            "user": wallet_address,
            "limit": limit
        }
        
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Failed to fetch trades for {wallet_address}: {e}")
            return []
    
    def get_market_info(self, condition_id: str) -> Dict:
        """Get market details from Gamma API"""
        url = f"{GAMMA_API_URL}/markets"
        params = {"conditionId": condition_id}
        
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            markets = response.json()
            if markets and len(markets) > 0:
                return markets[0]
            return {}
        except requests.exceptions.RequestException:
            return {}

class WhaleTracker:
    """Main tracking logic"""
    
    def __init__(self, whale_addresses: List[str] = None):
        self.api = PolymarketAPI()
        self.whales: Dict[str, Whale] = {}
        self.running = True
        
        # Use provided addresses or defaults
        self.whale_addresses = whale_addresses or DEFAULT_WHALE_WALLETS
        
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
    
    def initialize_whales(self):
        """Initialize whale objects from address list"""
        print(f"[{self._timestamp()}] Initializing {len(self.whale_addresses)} whale wallets...")
        
        for addr in self.whale_addresses:
            if addr not in self.whales:
                self.whales[addr] = Whale(
                    address=addr,
                    positions={},
                    total_value=0.0
                )
        
        print(f"[{self._timestamp()}] Tracking {len(self.whale_addresses)} whales")
    
    def scan_positions(self, wallet_address: str) -> Dict[str, Position]:
        """Scan current positions for a wallet"""
        positions_data = self.api.get_positions(wallet_address)
        positions = {}
        
        for pos in positions_data:
            try:
                # Extract market info from position data
                condition_id = pos.get("conditionId", "unknown")
                title = pos.get("title", "Unknown Market")
                outcome = pos.get("outcome", "Unknown")
                
                # Get position values
                size = float(pos.get("size", 0))
                avg_price = float(pos.get("avgPrice", 0))
                current_value = float(pos.get("currentValue", 0))
                cash_pnl = float(pos.get("cashPnl", 0))
                
                # Skip zero-size positions
                if size <= 0:
                    continue
                
                positions[condition_id] = Position(
                    market_slug=condition_id[:20],  # Use shortened condition ID as slug
                    market_question=title,
                    outcome=outcome,
                    size=size,
                    avg_price=avg_price,
                    current_value=current_value,
                    pnl=cash_pnl
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
        
        # Check for new positions
        for condition_id, pos in current_positions.items():
            if condition_id not in previous_positions:
                # Completely new position
                self._print_alert(wallet_address, pos, "NEW BET")
            else:
                old_pos = previous_positions[condition_id]
                size_diff = pos.size - old_pos.size
                value_diff = pos.current_value - old_pos.current_value
                
                # Significant increase (>10% or >$500)
                if size_diff > 500 or (old_pos.size > 0 and size_diff / old_pos.size > 0.1):
                    self._print_alert(wallet_address, pos, "INCREASED", size_diff, value_diff)
        
        # Update stored positions
        whale.positions = current_positions
        
        # Update total value
        whale.total_value = sum(p.current_value for p in current_positions.values())
    
    def _print_alert(self, wallet: str, pos: Position, alert_type: str, size_diff: float = 0, value_diff: float = 0):
        """Print formatted alert to console"""
        short_addr = self._short_addr(wallet)
        timestamp = self._timestamp()
        
        if alert_type == "NEW BET":
            print(f"\n[{timestamp}] 🐋 Whale {short_addr} NEW BET: ${pos.current_value:,.0f} on \"{pos.market_question}\" ({pos.outcome}) at ${pos.avg_price:.2f}")
        elif alert_type == "INCREASED":
            print(f"[{timestamp}] 🐋 Whale {short_addr} ADDED ${value_diff:,.0f} to \"{pos.market_question}\" ({pos.outcome}) - Total: ${pos.current_value:,.0f}")
    
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
        self.initialize_whales()
        
        print(f"[{self._timestamp()}] Scanning positions for {len(self.whale_addresses)} whales...")
        
        for i, addr in enumerate(self.whale_addresses, 1):
            positions = self.scan_positions(addr)
            self.whales[addr].positions = positions
            total_value = sum(p.current_value for p in positions.values())
            print(f"  [{i}/{len(self.whale_addresses)}] {self._short_addr(addr)}: {len(positions)} positions, ${total_value:,.0f} total")
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
            
            new_bets_found = 0
            
            for addr in self.whale_addresses:
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
    print("Tracking whale wallets via Data API")
    print(f"Checking every {CHECK_INTERVAL // 60} minutes")
    print(f"Data stored in: {DATA_FILE}")
    print("=" * 60)
    print()
    
    # You can customize which wallets to track here
    # Or use environment variable: WHALE_WALLETS=addr1,addr2,addr3
    whale_env = os.getenv("WHALE_WALLETS", "")
    if whale_env:
        whale_list = [w.strip() for w in whale_env.split(",") if w.strip()]
        tracker = WhaleTracker(whale_list)
    else:
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
