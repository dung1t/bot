"""
Base Trading Bot Class
Contains common logic for strategy, position management, and statistics.
"""

import threading
import time
from datetime import datetime
from collections import deque
from decimal import Decimal, ROUND_DOWN
from abc import ABC, abstractmethod

class BaseTradingBot(ABC):
    def __init__(self, config):
        """
        Base Trading Bot
        :param config: Dictionary containing common configuration
        """
        self.config = config
        
        # Extract config
        self.symbol = config.get('symbol', '')
        self.mode = config.get('mode', 'spot')
        self.strategy_name = config.get('strategy', 'ma_cross')
        self.risk_per_trade = config.get('risk_per_trade', 0.02)
        
        # Data storage
        self.current_price = None
        self.trades = deque(maxlen=1000)
        self.klines = deque(maxlen=200)
        
        # Position management
        self.position = None
        self.entry_price = None
        self.position_size = 0
        self.unrealized_pnl = 0
        
        # Strategy parameters
        self.ma_fast = 10
        self.ma_slow = 30
        self.rsi_period = 14
        self.rsi_overbought = 70
        self.rsi_oversold = 30
        
        # Statistics
        self.total_trades = 0
        self.winning_trades = 0
        self.total_pnl = 0
        
        # Runtime
        self.running = False
        
        # Default info (override in subclasses)
        self.tick_size = 0.01
        self.step_size = 0.0001
        
    @abstractmethod
    def get_symbol_info(self):
        """Get symbol specifics (tick_size, step_size, etc.)"""
        pass
        
    @abstractmethod
    def place_order_api(self, side, quantity, price=None, order_type=None):
        """Actual API call to place order. Returns order dict or None."""
        pass
        
    @abstractmethod
    def start_stream(self):
        """Start data stream (WebSocket or Polling)"""
        pass

    @abstractmethod
    def calculate_quantity(self):
        """Calculate trade quantity based on money management"""
        pass

    def round_quantity(self, quantity):
        """Round quantity to step size"""
        precision = len(str(self.step_size).split('.')[-1].rstrip('0'))
        if '.' in str(self.step_size) and precision > 0:
             return float(Decimal(str(quantity)).quantize(
                Decimal(str(self.step_size)), 
                rounding=ROUND_DOWN
            ))
        else:
            return int(quantity)

    def round_price(self, price):
        """Round price to tick size"""
        precision = len(str(self.tick_size).split('.')[-1].rstrip('0'))
        if '.' in str(self.tick_size) and precision > 0:
             return float(Decimal(str(price)).quantize(
                Decimal(str(self.tick_size)), 
                rounding=ROUND_DOWN
            ))
        else:
            return 
            
            int(price - (price % self.tick_size))
            
    def calculate_ma(self, period):
        """Calculate Moving Average"""
        if len(self.klines) < period:
            return None
        prices = [float(k['close']) for k in list(self.klines)[-period:]]
        return sum(prices) / period

    def calculate_rsi(self, period=14):
        """Calculate RSI"""
        if len(self.klines) < period + 1:
            return None
        closes = [float(k['close']) for k in list(self.klines)[-(period+1):]]
        gains = []
        losses = []
        for i in range(1, len(closes)):
            change = closes[i] - closes[i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def check_strategy(self):
        """Route to appropriate strategy"""
        if self.strategy_name == 'ma_cross':
            ma_fast = self.calculate_ma(self.ma_fast)
            ma_slow = self.calculate_ma(self.ma_slow)
            if not ma_fast or not ma_slow:
                return None
                
            print(f"📊 MA({self.ma_fast}): {ma_fast:.2f} | MA({self.ma_slow}): {ma_slow:.2f}")
            if ma_fast > ma_slow and self.position != 'LONG':
                return 'BUY'
            elif ma_fast < ma_slow and self.position == 'LONG':
                return 'SELL'
                
        elif self.strategy_name == 'rsi':
            rsi = self.calculate_rsi(self.rsi_period)
            if not rsi:
                return None
                
            print(f"📊 RSI({self.rsi_period}): {rsi:.2f}")
            if rsi < self.rsi_oversold and self.position != 'LONG':
                return 'BUY'
            elif rsi > self.rsi_overbought and self.position == 'LONG':
                return 'SELL'
                
        return None

    def calculate_stop_loss_take_profit(self, side, entry_price):
        """Calculate Stop Loss and Take Profit"""
        if side == 'BUY':
            sl_price = self.round_price(entry_price * 0.98)
            tp_price = self.round_price(entry_price * 1.03)
        else:
            sl_price = self.round_price(entry_price * 1.02)
            tp_price = self.round_price(entry_price * 0.97)
        return sl_price, tp_price

    def place_order(self, side, quantity):
        """Handle order placement wrapper"""
        try:
            print(f"\n💰 Placing Order: {side} {quantity} {self.symbol}")
            order = self.place_order_api(side, quantity)
            
            if order:
                print(f"✅ Order exectued: {order['orderId']}")
                return order
            return None
        except Exception as e:
            print(f"❌ Error placing order: {e}")
            return None

    def open_position(self, signal):
        """Open new position"""
        if self.position is not None:
            print("⚠️  Position exists, skipping signal")
            return
        
        quantity = self.calculate_quantity()
        if quantity <= 0:
            print("❌ Invalid Quantity")
            return
            
        order = self.place_order(signal, quantity)
        if order:
            self.position = 'LONG' if signal == 'BUY' else 'SHORT'
            self.entry_price = float(order['price'])
            self.position_size = float(order['quantity'])
            
            sl, tp = self.calculate_stop_loss_take_profit(signal, self.entry_price)
            
            print(f"\n🎯 Position Opened: {self.position} @ {self.entry_price}")
            print(f"   SL: {sl} | TP: {tp}")
            self.total_trades += 1

    def close_position(self, reason="Signal"):
        """Close existing position"""
        if self.position is None:
            return
            
        side = 'SELL' if self.position == 'LONG' else 'BUY'
        order = self.place_order(side, self.position_size)
        
        if order:
            exit_price = float(order['price'])
            
            # Simple PnL (subclasses can override for contract multipliers)
            multiplier = 100000 if self.mode == 'futures' and 'VN30' in self.symbol else 1
            
            if self.position == 'LONG':
                pnl = (exit_price - self.entry_price) * self.position_size * multiplier
            else:
                pnl = (self.entry_price - exit_price) * self.position_size * multiplier
                
            self.total_pnl += pnl
            if pnl > 0: self.winning_trades += 1
            
            emoji = "💚" if pnl > 0 else "❤️"
            print(f"\n{emoji} Position Closed ({reason})")
            print(f"   Entry: {self.entry_price} -> Exit: {exit_price}")
            print(f"   PnL: {pnl:,.2f}")
            
            self.position = None
            self.entry_price = None
            self.position_size = 0

    def check_position_management(self):
        """Check Stop Loss / Take Profit"""
        if not self.position or not self.entry_price or not self.current_price:
            return
            
        if self.position == 'LONG':
            pnl_pct = (self.current_price - self.entry_price) / self.entry_price
            if pnl_pct <= -0.02: self.close_position("Stop Loss")
            elif pnl_pct >= 0.03: self.close_position("Take Profit")
            self.unrealized_pnl = pnl_pct * 100
        # Add Short logic if needed
            
    def print_status(self):
        """Print status"""
        print(f"\n{'='*60}")
        print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        if self.current_price:
            print(f"💰 Price: {self.current_price:,.2f}")
        
        if self.position:
            print(f"📊 Position: {self.position} @ {self.entry_price} (Unrealized: {self.unrealized_pnl:+.2f}%)")
        else:
            print(f"📊 Position: None")
            
        if self.total_trades > 0:
            win_rate = self.winning_trades / self.total_trades * 100
            print(f"📈 Stats: Trades: {self.total_trades} | Win Rate: {win_rate:.1f}% | Total PnL: {self.total_pnl:,.2f}")
        print(f"{'='*60}\n")

    def run_loop(self):
        """Main loop"""
        print("\n🚀 Bot running! Press Ctrl+C to stop.\n")
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()
            
    def stop(self):
        """Stop bot"""
        print("\n⏹️  Stopping bot...")
        if self.position:
            self.close_position("Bot Stopped")
        self.running = False
        print("✅ Bot stopped.")
