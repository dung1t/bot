"""
SSI Trading Bot
- Support Futures
- Multiple strategies
- Risk management
"""

import time
import threading
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from collections import deque

# Import SSI Libraries
try:
    from ssi_fctrading import FCTradingClient
    from ssi_fc_data import FCDataClient, model
except ImportError:
    # Mocks for IntelliSense/No-Error if libs missing
    class FCTradingClient: def __init__(self, *args, **kwargs): pass
    class FCDataClient: def __init__(self, *args, **kwargs): pass
    class model: pass
    print("⚠️  Libraries 'ssi-fctrading' or 'ssi-fc-data' not found.")

class SSITradingBot:
    def __init__(self, data_consumer_id, data_consumer_secret, trading_consumer_id, trading_consumer_secret, config):
        """
        Trading Bot với nhiều tính năng (Phiên bản SSI)
        
        :param config: Dictionary chứa cấu hình:
        {
            'symbol': 'VN30F2309',
            'url': 'https://fc-trade.ssi.com.vn/',
            'stream_url': 'wss://fc-data.ssi.com.vn/',
            'private_key_path': 'path/to/private.pem',
            'mode': 'futures',  # 'spot' hoặc 'futures'
            'strategy': 'ma_cross',  # 'ma_cross', 'rsi'
            'timeframe': '1m',
            'trade_amount_qty': 1, # Số lượng hợp đồng/cổ phiếu
            'risk_per_trade': 0.02,
        }
        """
        self.data_consumer_id = data_consumer_id
        self.data_consumer_secret = data_consumer_secret
        self.trading_consumer_id = trading_consumer_id
        self.trading_consumer_secret = trading_consumer_secret
        self.config = config
        
        # Extract config
        self.symbol = config.get('symbol', 'VN30F1M')
        self.url = config.get('url', 'https://fc-trade.ssi.com.vn/')
        self.private_key_path = config.get('private_key_path', '')
        self.mode = config.get('mode', 'futures')  # futures mainly
        self.strategy_name = config.get('strategy', 'ma_cross')
        self.trade_amount_qty = config.get('trade_amount_qty', 1)
        self.risk_per_trade = config.get('risk_per_trade', 0.02)
        
        # Initialize clients
        try:
            self.trading_client = FCTradingClient(
                self.url,
                self.trading_consumer_id,
                self.trading_consumer_secret,
                self.private_key_path
            )
            
            self.data_client = FCDataClient(
                self.data_consumer_id,
                self.data_consumer_secret
            )
        except Exception as e:
            print(f"❌ Error initializing SSI clients: {e}")

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
        
        # WebSocket / Sync
        self.running = False
        
        # Statistics
        self.total_trades = 0
        self.winning_trades = 0
        self.total_pnl = 0
        
        # SSI specific info (defaults)
        self.tick_size = 0.1 # Futures usually 0.1
        self.step_size = 1
        
    def get_symbol_info(self):
        """Lấy thông tin chi tiết về symbol (Mock for SSI)"""
        try:
            # SSI API logic to get instrument details would go here
            # For now we hardcode common values for VN30 Futures
            if self.mode == 'futures':
                self.tick_size = 0.1
                self.step_size = 1
            else:
                self.tick_size = 100 # Equity
                self.step_size = 100
            
            print(f"📊 Symbol Info:")
            print(f"   Step Size: {self.step_size}")
            print(f"   Tick Size: {self.tick_size}")
            
        except Exception as e:
            print(f"❌ Error getting symbol info: {e}")
    
    def round_quantity(self, quantity):
        """Làm tròn số lượng"""
        return int(quantity) # SSI usually requires integer lots
    
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
        """Tính số lượng coin cần mua (Override: SSI use fixed quantity from config for simpler logic)"""
        # In binance bot this calculates based on USDT amount.
        # Here we just return the configured quantity for simplicity in Futures.
        return self.trade_amount_qty
    
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
    
    def place_order(self, side, quantity):
        """Đặt lệnh (SSI Implementation)"""
        try:
            print(f"\n💰 Đặt lệnh SSI {side} {quantity} {self.symbol}")
            
            ssi_side = 'B' if side == 'BUY' else 'S'
            
            # Market order type depends on instrument (VN30F uses MK, Equity uses MP)
            order_type = 'MK' if self.mode == 'futures' else 'MP'
            
            # Example payload
            req = {
                "instrumentID": self.symbol,
                "market": "VNFE" if self.mode == 'futures' else "VN",
                "buySell": ssi_side,
                "quantity": quantity,
                "price": 0, # Market order
                "channelID": "TA_API",
                # "orderType": order_type 
            }
            
            # Call API
            # order = self.trading_client.new_order(req)
            
            # MOCK RESPONSE
            order = {
                'orderId': f"mock_{int(time.time())}",
                'status': 'FILLED',
                'avgPrice': self.current_price if self.current_price else 1000,
                'executedQty': quantity
            }
            
            filled_qty = float(order.get('executedQty', 0))
            avg_price = float(order.get('avgPrice', 0))
            
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
        order = self.place_order(signal, quantity)
        
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
        order = self.place_order(side, self.position_size)
        
        if order:
            exit_price = order['price']
            
            # Tính P&L
            if self.position == 'LONG':
                pnl = (exit_price - self.entry_price) * self.position_size * 100000 # Contract multiplier for futures
            else:
                pnl = (self.entry_price - exit_price) * self.position_size * 100000
            
            if self.mode != 'futures':
                # Basic PnL for spot (no multiplier)
                 if self.position == 'LONG':
                    pnl = (exit_price - self.entry_price) * self.position_size
                 else:
                    pnl = (self.entry_price - exit_price) * self.position_size

            pnl_pct = (pnl / (self.entry_price * self.position_size)) * 100 if self.mode != 'futures' else 0 # Approx
            
            self.total_pnl += pnl
            
            if pnl > 0:
                self.winning_trades += 1
                emoji = "💚"
            else:
                emoji = "❤️"
            
            print(f"\n{emoji} Position closed ({reason}):")
            print(f"   Entry: {self.entry_price:.2f}")
            print(f"   Exit: {exit_price:.2f}")
            print(f"   P&L: {pnl:,.0f} VND")
            print(f"   Total P&L: {self.total_pnl:,.0f} VND")
            
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
        if self.current_price:
            print(f"💰 Price: {self.current_price:,.2f}")
        
        if self.position:
            print(f"📊 Position: {self.position}")
            print(f"   Entry: {self.entry_price:,.2f}")
            print(f"   Size: {self.position_size}")
            print(f"   Unrealized P&L: {self.unrealized_pnl:+.2f}%")
        else:
            print(f"📊 Position: None")
        
        if self.total_trades > 0:
            win_rate = self.winning_trades / self.total_trades * 100
            print(f"📈 Stats:")
            print(f"   Total Trades: {self.total_trades}")
            print(f"   Win Rate: {win_rate:.1f}%")
            print(f"   Total P&L: {self.total_pnl:,.0f} VND")
        
        print(f"{'='*60}\n")
    
    def on_message(self, message):
        """Process data (Unified for SSI Stream)"""
        try:
            # SSI sends various messages. We filter for Trade/Quote
            # Mock structure parsing
            # data = json.loads(message)
            # if 'LastPrice' in data: ...
            
            # Mock update because I cannot run real stream
            pass
            
        except Exception as e:
            print(f"❌ Error processing message: {e}")

    def on_market_update(self, price, volume, time_str):
        """Called when new price data arrives (Simulating Stream)"""
        self.current_price = float(price)
        
        # NOTE: SSI Data Stream handling is complex to map 1:1 to Binance 'trade' vs 'kline' channels cleanly without real data structure.
        # We assume we construct candles manually from ticks or receive minute bars if supported.
        # For this refactor, I will simulate 'on_kline_message' logic here if we were building candles.
        
        # ... logic to build candles ...
        # If candle closed:
        #   self.klines.append(new_candle)
        #   signal = self.check_strategy()
        #   ... (same as binance)
        
        self.check_position_management()
        self.print_status()

    def start(self):
        """Khởi động bot"""
        print("\n" + "="*60)
        print("🤖 SSI TRADING BOT")
        print("="*60)
        print(f"Symbol: {self.symbol}")
        print(f"Mode: {self.mode.upper()}")
        print(f"Strategy: {self.strategy_name.upper()}")
        print(f"Quantity: {self.trade_amount_qty}")
        print("="*60 + "\n")
        
        # Get symbol info
        self.get_symbol_info()
        
        # Get initial kline data (Mock)
        print("📊 Loading historical data...")
        # klines = self.data_client.get_history(...)
        # For now, fake headers to match binance logic
        for _ in range(50):
            self.klines.append({
                'time': 0, 'open': 1000, 'high': 1005, 'low': 995, 'close': 1000, 'volume': 100
            })
        
        print(f"✅ Loaded {len(self.klines)} candles")
        
        # Start WebSocket streams
        self.running = True
        
        # Mocking the thread run like Binance
        # In reality, SSI Data Client has its own stream method which might block or be async.
        # We will wrap it in a thread.
        
        def run_stream():
            print("✅ Data Stream connected")
            # self.data_client.stream(self.symbol, self.on_message)
            while self.running:
                time.sleep(1)
                # Simulate price update for demo
                # self.on_market_update(1200, 10, "now")
        
        thread = threading.Thread(target=run_stream)
        thread.daemon = True
        thread.start()
        
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
        
        print("✅ Bot stopped successfully!")
        print(f"\n📊 Final Statistics:")
        print(f"   Total Trades: {self.total_trades}")
        if self.total_trades > 0:
            print(f"   Winning Trades: {self.winning_trades}")
            print(f"   Win Rate: {self.winning_trades/self.total_trades*100:.1f}%")
        print(f"   Total P&L: {self.total_pnl:,.0f} VND")


def main():
    """Main function"""
    
    # Configuration
    DATA_CONSUMER_ID = "YOUR_ID"
    DATA_CONSUMER_SECRET = "YOUR_SECRET"
    TRADING_CONSUMER_ID = "YOUR_ID"
    TRADING_CONSUMER_SECRET = "YOUR_SECRET"
    
    if TRADING_CONSUMER_ID == "YOUR_ID" or  DATA_CONSUMER_ID == "YOUR_ID":
        print("❌ Vui lòng thay TRADING_CONSUMER_ID và DATA_CONSUMER_ID")
        return
    
    config = {
        'symbol': 'VN30F2M',
        'url': 'https://fc-trade.ssi.com.vn/',
        'private_key_path': 'key.pem',
        'mode': 'futures',
        'strategy': 'ma_cross',
        'trade_amount_qty': 1,
        'risk_per_trade': 0.02,
    }
    
    # Create and start bot
    bot = SSITradingBot(DATA_CONSUMER_ID, DATA_CONSUMER_SECRET, TRADING_CONSUMER_ID, TRADING_CONSUMER_SECRET, config)
    bot.start()


if __name__ == "__main__":
    main()
