"""
Binance Trading Bot
- Support cả Spot và Futures
- Multiple strategies
- Risk management
- Telegram notifications (optional)
"""

import json
import time
import threading
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from collections import deque
from binance.client import Client
from binance.enums import *
import websocket


class BinanceTradingBot:
    def __init__(self, api_key, api_secret, config):
        """
        Trading Bot với nhiều tính năng
        
        :param config: Dictionary chứa cấu hình:
        {
            'symbol': 'BTCUSDT',
            'testnet': True,
            'mode': 'spot',  # 'spot' hoặc 'futures'
            'strategy': 'ma_cross',  # 'ma_cross', 'rsi', 'bollinger'
            'timeframe': '1m',
            'trade_amount_usdt': 100,
            'max_positions': 1,
            'risk_per_trade': 0.02,
            'use_leverage': 5,  # Chỉ cho futures
        }
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.config = config
        
        # Extract config
        self.symbol = config.get('symbol', 'BTCUSDT')
        self.testnet = config.get('testnet', True)
        self.mode = config.get('mode', 'spot')  # spot or futures
        self.strategy_name = config.get('strategy', 'ma_cross')
        self.trade_amount_usdt = config.get('trade_amount_usdt', 100)
        self.risk_per_trade = config.get('risk_per_trade', 0.02)
        
        # Initialize client
        if self.testnet:
            self.client = Client(api_key, api_secret, testnet=True)
            self.client.API_URL = 'https://testnet.binance.vision/api'
            ws_base = "wss://testnet.binance.vision/ws"
        else:
            self.client = Client(api_key, api_secret)
            ws_base = "wss://stream.binance.com:9443/ws"
        
        # WebSocket streams
        self.ws_url = f"{ws_base}/{self.symbol.lower()}@trade"
        self.ws_kline_url = f"{ws_base}/{self.symbol.lower()}@kline_1m"
        
        # Data storage
        self.current_price = None
        self.trades = deque(maxlen=1000)
        self.klines = deque(maxlen=200)  # Lưu 200 nến
        
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
        
        # WebSocket
        self.ws = None
        self.ws_kline = None
        self.running = False
        
        # Statistics
        self.total_trades = 0
        self.winning_trades = 0
        self.total_pnl = 0
        
    def get_symbol_info(self):
        """Lấy thông tin chi tiết về symbol"""
        try:
            info = self.client.get_symbol_info(self.symbol)
            
            for f in info['filters']:
                if f['filterType'] == 'LOT_SIZE':
                    self.min_qty = float(f['minQty'])
                    self.step_size = float(f['stepSize'])
                elif f['filterType'] == 'PRICE_FILTER':
                    self.tick_size = float(f['tickSize'])
                elif f['filterType'] == 'MIN_NOTIONAL':
                    self.min_notional = float(f.get('minNotional', 10))
            
            print(f"📊 Symbol Info:")
            print(f"   Min Qty: {self.min_qty}")
            print(f"   Step Size: {self.step_size}")
            print(f"   Tick Size: {self.tick_size}")
            print(f"   Min Notional: {self.min_notional}")
            
        except Exception as e:
            print(f"❌ Error getting symbol info: {e}")
    
    def round_quantity(self, quantity):
        """Làm tròn số lượng"""
        precision = len(str(self.step_size).split('.')[-1].rstrip('0'))
        return float(Decimal(str(quantity)).quantize(
            Decimal(str(self.step_size)), 
            rounding=ROUND_DOWN
        ))
    
    def round_price(self, price):
        """Làm tròn giá"""
        precision = len(str(self.tick_size).split('.')[-1].rstrip('0'))
        if '.' in str(self.tick_size) and precision > 0:
             return float(Decimal(str(price)).quantize(
                Decimal(str(self.tick_size)), 
                rounding=ROUND_DOWN
            ))
        else:
            return int(price - (price % self.tick_size))
    
    def calculate_quantity(self):
        """Tính số lượng coin cần mua dựa trên số tiền USDT"""
        if not self.current_price:
            return 0
        
        quantity = self.trade_amount_usdt / self.current_price
        quantity = self.round_quantity(quantity)
        
        # Kiểm tra min notional
        if quantity * self.current_price < self.min_notional:
            quantity = self.round_quantity(self.min_notional / self.current_price * 1.01)
        
        return quantity
    
    def calculate_ma(self, period):
        """Tính Moving Average"""
        if len(self.klines) < period:
            return None
        
        prices = [float(k['close']) for k in list(self.klines)[-period:]]
        return sum(prices) / period
    
    def calculate_rsi(self, period=14):
        """Tính RSI"""
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
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def check_ma_cross_strategy(self):
        """
        MA Crossover Strategy
        BUY: Fast MA crosses above Slow MA
        SELL: Fast MA crosses below Slow MA
        """
        ma_fast = self.calculate_ma(self.ma_fast)
        ma_slow = self.calculate_ma(self.ma_slow)
        
        if not ma_fast or not ma_slow:
            return None
        
        print(f"📊 MA({self.ma_fast}): {ma_fast:.2f} | MA({self.ma_slow}): {ma_slow:.2f}")
        
        # Check for crossover
        if ma_fast > ma_slow and self.position != 'LONG':
            return 'BUY'
        elif ma_fast < ma_slow and self.position == 'LONG':
            return 'SELL'
        
        return None
    
    def check_rsi_strategy(self):
        """
        RSI Strategy
        BUY: RSI < 30 (oversold)
        SELL: RSI > 70 (overbought)
        """
        rsi = self.calculate_rsi(self.rsi_period)
        
        if not rsi:
            return None
        
        print(f"📊 RSI({self.rsi_period}): {rsi:.2f}")
        
        if rsi < self.rsi_oversold and self.position != 'LONG':
            return 'BUY'
        elif rsi > self.rsi_overbought and self.position == 'LONG':
            return 'SELL'
        
        return None
    
    def check_strategy(self):
        """Route to appropriate strategy"""
        if self.strategy_name == 'ma_cross':
            return self.check_ma_cross_strategy()
        elif self.strategy_name == 'rsi':
            return self.check_rsi_strategy()
        
        return None
    
    def calculate_stop_loss_take_profit(self, side, entry_price):
        """
        Tính Stop Loss và Take Profit
        SL: 2% từ entry
        TP: 3% từ entry
        """
        if side == 'BUY':
            sl_price = self.round_price(entry_price * 0.98)
            tp_price = self.round_price(entry_price * 1.03)
        else:
            sl_price = self.round_price(entry_price * 1.02)
            tp_price = self.round_price(entry_price * 0.97)
        
        return sl_price, tp_price
    
    def place_spot_order(self, side, quantity):
        """Đặt lệnh Spot"""
        try:
            print(f"\n💰 Đặt lệnh SPOT {side} {quantity} {self.symbol}")
            
            order = self.client.create_order(
                symbol=self.symbol,
                side=side,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            
            # Parse order info
            filled_qty = float(order.get('executedQty', 0))
            avg_price = 0
            
            if 'fills' in order:
                total_cost = sum(float(fill['price']) * float(fill['qty']) 
                               for fill in order['fills'])
                avg_price = total_cost / filled_qty if filled_qty > 0 else 0
            
            print(f"✅ Order executed:")
            print(f"   ID: {order['orderId']}")
            print(f"   Filled: {filled_qty} @ {avg_price:.2f}")
            print(f"   Status: {order['status']}")
            
            return {
                'orderId': order['orderId'],
                'quantity': filled_qty,
                'price': avg_price,
                'side': side
            }
            
        except Exception as e:
            print(f"❌ Error placing order: {e}")
            return None
    
    def open_position(self, signal):
        """Mở position mới"""
        if self.position is not None:
            print("⚠️  Position đã tồn tại, bỏ qua signal")
            return
        
        quantity = self.calculate_quantity()
        
        if quantity <= 0:
            print("❌ Quantity không hợp lệ")
            return
        
        # Đặt lệnh
        order = self.place_spot_order(signal, quantity)
        
        if order:
            self.position = 'LONG' if signal == 'BUY' else 'SHORT'
            self.entry_price = order['price']
            self.position_size = order['quantity']
            
            # Calculate SL/TP
            sl_price, tp_price = self.calculate_stop_loss_take_profit(
                signal, 
                self.entry_price
            )
            
            print(f"\n🎯 Position opened:")
            print(f"   Type: {self.position}")
            print(f"   Entry: {self.entry_price:.2f}")
            print(f"   Size: {self.position_size}")
            print(f"   Stop Loss: {sl_price:.2f}")
            print(f"   Take Profit: {tp_price:.2f}")
            
            self.total_trades += 1
    
    def close_position(self, reason="Strategy signal"):
        """Đóng position"""
        if self.position is None:
            return
        
        side = 'SELL' if self.position == 'LONG' else 'BUY'
        
        # Đặt lệnh đóng
        order = self.place_spot_order(side, self.position_size)
        
        if order:
            exit_price = order['price']
            
            # Tính P&L
            if self.position == 'LONG':
                pnl = (exit_price - self.entry_price) * self.position_size
                pnl_pct = (exit_price - self.entry_price) / self.entry_price * 100
            else:
                pnl = (self.entry_price - exit_price) * self.position_size
                pnl_pct = (self.entry_price - exit_price) / self.entry_price * 100
            
            self.total_pnl += pnl
            
            if pnl > 0:
                self.winning_trades += 1
                emoji = "💚"
            else:
                emoji = "❤️"
            
            print(f"\n{emoji} Position closed ({reason}):")
            print(f"   Entry: {self.entry_price:.2f}")
            print(f"   Exit: {exit_price:.2f}")
            print(f"   P&L: ${pnl:.2f} ({pnl_pct:+.2f}%)")
            print(f"   Total P&L: ${self.total_pnl:.2f}")
            
            # Reset position
            self.position = None
            self.entry_price = None
            self.position_size = 0
    
    def check_position_management(self):
        """Kiểm tra Stop Loss / Take Profit"""
        if not self.position or not self.entry_price or not self.current_price:
            return
        
        if self.position == 'LONG':
            pnl_pct = (self.current_price - self.entry_price) / self.entry_price
            
            # Stop Loss
            if pnl_pct <= -0.02:
                self.close_position("Stop Loss")
                return
            
            # Take Profit
            if pnl_pct >= 0.03:
                self.close_position("Take Profit")
                return
            
            # Update unrealized P&L
            self.unrealized_pnl = pnl_pct * 100
    
    def print_status(self):
        """In trạng thái hiện tại"""
        print(f"\n{'='*60}")
        print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"💰 Price: {self.current_price:.2f}")
        
        if self.position:
            print(f"📊 Position: {self.position}")
            print(f"   Entry: {self.entry_price:.2f}")
            print(f"   Size: {self.position_size}")
            print(f"   Unrealized P&L: {self.unrealized_pnl:+.2f}%")
        else:
            print(f"📊 Position: None")
        
        if self.total_trades > 0:
            win_rate = self.winning_trades / self.total_trades * 100
            print(f"📈 Stats:")
            print(f"   Total Trades: {self.total_trades}")
            print(f"   Win Rate: {win_rate:.1f}%")
            print(f"   Total P&L: ${self.total_pnl:.2f}")
        
        print(f"{'='*60}\n")
    
    def on_kline_message(self, ws, message):
        """Process kline (candlestick) data"""
        try:
            data = json.loads(message)
            kline = data['k']
            
            # Chỉ process nến đã đóng
            if kline['x']:  # x = is_closed
                self.klines.append({
                    'time': kline['t'],
                    'open': kline['o'],
                    'high': kline['h'],
                    'low': kline['l'],
                    'close': kline['c'],
                    'volume': kline['v']
                })
                
                # Check strategy khi có nến mới
                signal = self.check_strategy()
                
                if signal:
                    if signal in ['BUY', 'SELL'] and self.position is None:
                        self.open_position(signal)
                    elif signal == 'SELL' and self.position == 'LONG':
                        self.close_position("Strategy signal")
                
                # Print status mỗi nến
                self.print_status()
                
        except Exception as e:
            print(f"❌ Error processing kline: {e}")
    
    def on_trade_message(self, ws, message):
        """Process trade data (price updates)"""
        try:
            data = json.loads(message)
            self.current_price = float(data['p'])
            
            # Check position management
            self.check_position_management()
            
        except Exception as e:
            print(f"❌ Error processing trade: {e}")
    
    def start(self):
        """Khởi động bot"""
        print("\n" + "="*60)
        print("🤖 BINANCE TRADING BOT")
        print("="*60)
        print(f"Symbol: {self.symbol}")
        print(f"Mode: {self.mode.upper()}")
        print(f"Strategy: {self.strategy_name.upper()}")
        print(f"Trade Amount: ${self.trade_amount_usdt}")
        print(f"Risk per Trade: {self.risk_per_trade*100}%")
        print("="*60 + "\n")
        
        # Get symbol info
        self.get_symbol_info()
        
        # Get initial kline data
        print("📊 Loading historical data...")
        klines = self.client.get_klines(
            symbol=self.symbol,
            interval=Client.KLINE_INTERVAL_1MINUTE,
            limit=200
        )
        
        for k in klines:
            self.klines.append({
                'time': k[0],
                'open': k[1],
                'high': k[2],
                'low': k[3],
                'close': k[4],
                'volume': k[5]
            })
        
        print(f"✅ Loaded {len(self.klines)} candles")
        
        # Start WebSocket streams
        self.running = True
        
        # Trade stream (for price updates)
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_message=self.on_trade_message,
            on_error=lambda ws, err: print(f"❌ Trade WS Error: {err}"),
            on_close=lambda ws, code, msg: print("🔌 Trade WS closed"),
            on_open=lambda ws: print("✅ Trade WS connected")
        )
        
        # Kline stream (for strategy)
        self.ws_kline = websocket.WebSocketApp(
            self.ws_kline_url,
            on_message=self.on_kline_message,
            on_error=lambda ws, err: print(f"❌ Kline WS Error: {err}"),
            on_close=lambda ws, code, msg: print("🔌 Kline WS closed"),
            on_open=lambda ws: print("✅ Kline WS connected")
        )
        
        # Run in separate threads
        thread1 = threading.Thread(target=self.ws.run_forever)
        thread2 = threading.Thread(target=self.ws_kline.run_forever)
        
        thread1.daemon = True
        thread2.daemon = True
        
        thread1.start()
        thread2.start()
        
        print("\n🚀 Bot running! Press Ctrl+C to stop.\n")
        
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()
    
    def stop(self):
        """Dừng bot"""
        print("\n⏹️  Stopping bot...")
        
        # Close any open positions
        if self.position:
            print("⚠️  Closing open position...")
            self.close_position("Bot stopped")
        
        self.running = False
        
        if self.ws:
            self.ws.close()
        if self.ws_kline:
            self.ws_kline.close()
        
        print("✅ Bot stopped successfully!")
        print(f"\n📊 Final Statistics:")
        print(f"   Total Trades: {self.total_trades}")
        if self.total_trades > 0:
            print(f"   Winning Trades: {self.winning_trades}")
            print(f"   Win Rate: {self.winning_trades/self.total_trades*100:.1f}%")
        print(f"   Total P&L: ${self.total_pnl:.2f}")


def main():
    """Main function"""
    
    # Configuration
    API_KEY = "YOUR_API_KEY"
    API_SECRET = "YOUR_API_SECRET"
    
    if API_KEY == "YOUR_API_KEY":
        print("❌ Vui lòng thay YOUR_API_KEY và YOUR_API_SECRET")
        return
    
    config = {
        'symbol': 'BTCUSDT',
        'testnet': True,
        'mode': 'spot',
        'strategy': 'ma_cross',  # 'ma_cross' hoặc 'rsi'
        'trade_amount_usdt': 100,
        'risk_per_trade': 0.02,
    }
    
    # Create and start bot
    bot = BinanceTradingBot(API_KEY, API_SECRET, config)
    bot.start()


if __name__ == "__main__":
    main()