#!/usr/bin/env python3
"""
Polymarket Whale Tracker Bot - FIXED VERSION
Monitors whale wallets and tracks their bets in real-time.
"""

import requests
import json
import time
import os
from datetime import datetime
from typing import Dict, List
from dataclasses import dataclass, asdict
import signal
import sys

# Configuration
DATA_API_URL = "https://data-api.polymarket.com"
CHECK_INTERVAL = 300  # 5 minutes
DATA_FILE = "whale_positions.json"

# REAL whale wallets - these are actual known Polymarket traders
# Add more addresses you want to track
DEFAULT_WHALE_WALLETS = [
    "0x56687bf447db6ffa42ffe2204a05edaa20f55839",
    "0x6af75d4e4aaf700450efbac3708cce1665810ff1",
]

@dataclass
class Position:
    market_slug: str
    market_question: str
    outcome: str
    size: float
    avg_price: float
    current_value: float
    pnl: float
    condition_id: str
    
    def to_dict(self):
        return asdict(self)

@dataclass
class Whale:
    address: str
    positions: Dict[str, Position]
    total_value: float = 0.0
    
    def to_dict(self):
        return {
            "address": self.address,
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "total_value": self.total_value
        }

class PolymarketAPI:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "PolymarketWhaleTracker/1.0",
            "Accept": "application/json"
        })
    
    def get_positions(self, wallet_address: str) -> List[Dict]:
        """Fetch current open positions for a user"""
        url = f"{DATA_API_URL}/positions"
        params = {
            "user": wallet_address,
            "limit": 100,
            "offset": 0,
            "sizeThreshold": 0,  # Include ALL positions
            "sortBy": "TOKENS",
            "sortDirection": "DESC"
        }
        
        try:
            print(f"[DEBUG] Calling API: {url}?user={wallet_address}")
            response = self.session.get(url, params=params, timeout=30)
            
            print(f"[DEBUG] Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"[DEBUG] Got {len(data)} positions")
                return data
            else:
                print(f"[ERROR] API returned {response.status_code}: {response.text[:200]}")
                return []
                
        except Exception as e:
            print(f"[ERROR] Failed: {e}")
            return []

class WhaleTracker:
    def __init__(self, whale_addresses=None):
        self.api = PolymarketAPI()
        self.whales: Dict[str, Whale] = {}
        self.running = True
        self.whale_addresses = whale_addresses or DEFAULT_WHALE_WALLETS
        
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        print("\n[SYSTEM] Saving state...")
        self.running = False
        self.save_state()
        sys.exit(0)
    
    def load_state(self):
        if not os.path.exists(DATA_FILE):
            return False
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
            self.whales = {addr: Whale(**whale_data) for addr, whale_data in data.items()}
            print(f"[INFO] Loaded {len(self.whales)} whales from file")
            return True
        except Exception as e:
            print(f"[WARNING] Failed to load: {e}")
            return False
    
    def save_state(self):
        try:
            data = {addr: whale.to_dict() for addr, whale in self.whales.items()}
            with open(DATA_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"[INFO] State saved")
        except Exception as e:
            print(f"[ERROR] Save failed: {e}")
    
    def scan_positions(self, wallet_address: str):
        positions_data = self.api.get_positions(wallet_address)
        positions = {}
        
        if not positions_data:
            print(f"[DEBUG] No data for {wallet_address[:10]}...")
            return positions
        
        for pos in positions_data:
            try:
                condition_id = pos.get("conditionId", "unknown")
                title = pos.get("title", pos.get("question", "Unknown"))
                outcome = pos.get("outcome", "Unknown")
                
                size = float(pos.get("size", 0) or 0)
                avg_price = float(pos.get("avgPrice", 0) or 0)
                current_value = float(pos.get("currentValue", 0) or 0)
                cash_pnl = float(pos.get("cashPnl", 0) or 0)
                
                if size <= 0:
                    continue
                
                positions[condition_id] = Position(
                    market_slug=condition_id[:20],
                    market_question=title,
                    outcome=outcome,
                    size=size,
                    avg_price=avg_price,
                    current_value=current_value,
                    pnl=cash_pnl,
                    condition_id=condition_id
                )
            except Exception as e:
                continue
        
        return positions
    
    def check_new_bets(self, wallet_address, current_positions):
        whale = self.whales.get(wallet_address)
        if not whale:
            return
        
        previous = whale.positions
        
        for cid, pos in current_positions.items():
            if cid not in previous:
                print(f"\n[{self._timestamp()}] 🐋 NEW BET: ${pos.current_value:,.0f} on \"{pos.market_question}\" ({pos.outcome})")
            else:
                old = previous[cid]
                if pos.size != old.size:
                    diff = pos.current_value - old.current_value
                    print(f"[{self._timestamp()}] 🐋 UPDATED: {pos.market_question} (${diff:+,.0f})")
        
        whale.positions = current_positions
        whale.total_value = sum(p.current_value for p in current_positions.values())
    
    def _timestamp(self):
        return datetime.now().strftime("%H:%M")
    
    def run_initial_scan(self):
        print(f"[{self._timestamp()}] Starting scan of {len(self.whale_addresses)} whales...")
        
        for addr in self.whale_addresses:
            if addr not in self.whales:
                self.whales[addr] = Whale(address=addr, positions={})
            
            print(f"\n[{self._timestamp()}] Scanning: {addr[:10]}...")
            positions = self.scan_positions(addr)
            self.whales[addr].positions = positions
            total = sum(p.current_value for p in positions.values())
            print(f"  -> {len(positions)} positions, ${total:,.0f} total")
            time.sleep(1)
        
        self.save_state()
        print(f"\n[{self._timestamp()}] Scan complete. Monitoring...\n")
    
    def run_monitor_loop(self):
        self.run_initial_scan()
        
        cycle = 0
        while self.running:
            cycle += 1
            print(f"\n[{self._timestamp()}] Check #{cycle}")
            
            found_new = 0
            
            for addr in self.whale_addresses:
                if not self.running:
                    break
                
                print(f"[{self._timestamp()}] Checking {addr[:10]}...")
                current = self.scan_positions(addr)
                
                new_count = len(set(current.keys()) - set(self.whales[addr].positions.keys()))
                found_new += new_count
                
                self.check_new_bets(addr, current)
                time.sleep(1)
            
            if found_new == 0:
                print(f"[{self._timestamp()}] No new bets")
            
            self.save_state()
            
            if self.running:
                print(f"[{self._timestamp()}] Next check in 5 min...")
                for _ in range(CHECK_INTERVAL // 10):
                    if not self.running:
                        break
                    time.sleep(10)

def main():
    print("=" * 60)
    print("🐋 POLYMARKET WHALE TRACKER 🐋")
    print("=" * 60)
    
    whale_env = os.getenv("WHALE_WALLETS", "")
    if whale_env:
        whale_list = [w.strip() for w in whale_env.split(",") if w.strip()]
        tracker = WhaleTracker(whale_list)
    else:
        tracker = WhaleTracker()
    
    tracker.load_state()
    
    try:
        tracker.run_monitor_loop()
    except KeyboardInterrupt:
        print("\nShutting down...")
        tracker.save_state()
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        tracker.save_state()

if __name__ == "__main__":
    main()
