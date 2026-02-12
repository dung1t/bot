"""
SSI Trading Bot (Inherits from BaseBot)
"""
import json
import time
import threading
from base_bot import BaseTradingBot
from ssi_fctrading import FCTradingClient, FCTradingStream
from ssi_fc_data import FCDataClient, model, fc_md_stream


class SSITradingBot(BaseTradingBot):
    def __init__(
        self, data_consumer_id, data_consumer_secret,
        trading_consumer_id, trading_consumer_secret, trading_private_key, otp,
        config
    ):
        super().__init__(config)
        self.data_consumer_id = data_consumer_id
        self.data_consumer_secret = data_consumer_secret
        self.trading_consumer_id = trading_consumer_id
        self.trading_consumer_secret = trading_consumer_secret
        self.trading_private_key = trading_private_key
        self.url = config.get('url', 'https://fc-trade.ssi.com.vn/')
        self.trade_amount_qty = config.get('trade_amount_qty', 1)
        self.otp = otp
        
        # Stream objects
        self.trading_stream = None
        self.md_stream = None
        
        # Initialize clients
        try:
            self.trading_client = FCTradingClient(
                self.url,
                self.trading_consumer_id,
                self.trading_consumer_secret,
                self.trading_private_key
            )
            self.trading_client.verifyCode(self.otp)

            self.data_client = FCDataClient(
                self.data_consumer_id,
                self.data_consumer_secret
            )
        except Exception as e:
            print(f"❌ Error initializing SSI clients: {e}")

    def get_symbol_info(self):
        """Get symbol specifics"""
        if self.mode == 'futures':
            self.tick_size = 0.1
            self.step_size = 1
        else:
            self.tick_size = 100
            self.step_size = 100
        print(f"📊 Symbol Info: Tick {self.tick_size}, Step {self.step_size}")

    def calculate_quantity(self):
        """Use configured quantity"""
        return self.trade_amount_qty

    def place_order_api(self, side, quantity, price=None, order_type=None):
        """Place Order via SSI API"""
        ssi_side = 'B' if side == 'BUY' else 'S'
        order_type_api = 'MTL' if self.mode == 'futures' else 'MP' # Default Market
        
        req = {
            "instrumentID": self.symbol,
            "market": "VNFE" if self.mode == 'futures' else "VN",
            "buySell": ssi_side,
            "quantity": quantity,
            "orderType": order_type_api,
            "price": 0,
            "channelID": "TA"
        }
        
        # Real API call would go here:
        order = self.trading_client.new_order(req)
        
        # Mock Response
        return {
            'orderId': order.orderId,
            'price': order.price,
            'quantity': order.quantity
        }

    def on_trade_message(self, ws, message):
        """Process trade data"""
        try:
            data = json.loads(message)
            self.current_price = float(data['p'])
            self.check_position_management()
        except Exception:
            pass

    def on_trading_open(self):
        """Callback for Trading Stream Open"""
        print("✅ Trading Stream Connected")

    def on_kline_message(self, ws, message):
        """Process kline data"""
        try:
            data = json.loads(message)
            kline = data['k']
            if kline['x']:
                self.klines.append({
                    'time': kline['t'], 'open': kline['o'], 'high': kline['h'], 
                    'low': kline['l'], 'close': kline['c'], 'volume': kline['v']
                })
                signal = self.check_strategy()
                if signal:
                    if (signal == 'BUY' and self.position != 'LONG') or \
                       (signal == 'SELL' and self.position == 'LONG'):
                        if self.position: self.close_position()
                        self.open_position(signal)
                self.print_status()
        except Exception as e:
            print(f"Kline Error: {e}")

    def on_data_error(self, error):
        """Callback for Data Stream Errors"""
        print(f"❌ Data Stream Error: {error}")

    def _run_trading_stream(self):
        """Thread target for Trading Stream"""
        try:
            self.trading_stream.start()
            while self.running:
                time.sleep(1)
        except Exception as e:
            print(f"❌ Trading Stream Thread Error: {e}")

    def _run_data_stream(self):
        """Thread target for Data Stream"""
        try:
            channel = f"B:{self.symbol}"
            self.md_stream.start(self.on_kline_message, self.on_data_error, channel)
            while self.running:
                time.sleep(1)
        except Exception as e:
            print(f"❌ Data Stream Thread Error: {e}")

    def start_stream(self):
        """Start WebSocket streams"""
        
        # 1. Setup Trading Stream
        self.trading_stream = FCTradingStream(
            self.trading_client, 
            self.url, 
            self.on_trade_message, 
            self.on_trade_message, 
            on_open=lambda ws: print("✅ Trade WS connected")
        )
        
        # 2. Setup Market Data Stream
        class Config:
            def __init__(self):
                self.stream_url = "wss://fc-data.ssi.com.vn/"
                self.auth_type = "Bearer"
        
        self.md_stream = fc_md_stream.MarketDataStream(
            Config(), 
            self.data_client, 
            on_open=lambda ws: print("✅ Kline WS connected")
        )
        
        # 3. Start Threads
        # Using separate thread methods to keep Main Thread clean just like Binance's run_forever logic
        t1 = threading.Thread(target=self._run_trading_stream)
        t2 = threading.Thread(target=self._run_data_stream)
        
        t1.daemon = True
        t2.daemon = True
        
        t1.start()
        t2.start()

    def start(self):
        """Start Bot"""
        self.get_symbol_info()
        
        # Load History
        print("Loading historical data...")
        klines = self.data_client.intraday_ohlc(
            symbol=self.symbol,
            fromDate="2026-02-01",
            toDate="2026-02-11",
            pageIndex=1,
            pageSize=200
        )
        for k in klines:
            self.klines.append({
                'time': k[0], 'open': k[1], 'high': k[2], 'low': k[3], 'close': k[4], 'volume': k[5]
            })
        print(f"Loaded {len(self.klines)} candles")
        
        self.running = True
        self.start_stream()
        self.run_loop()

def main():
    DATA_CONSUMER_ID = "YOUR_ID"
    DATA_CONSUMER_SECRET = "YOUR_SECRET"
    TRADING_CONSUMER_ID = "YOUR_ID"
    TRADING_CONSUMER_SECRET = "YOUR_SECRET"
    TRADING_PRIVATE_KEY = "YOUR_PRIVATE_KEY"
    OTP = "123456"
    
    if TRADING_CONSUMER_ID == "YOUR_ID":
        print("❌ Please update credentials")
        return
    
    config = {
        'symbol': 'VN30F2M',
        'url': 'https://fc-trade.ssi.com.vn/',
        'mode': 'futures',
        'strategy': 'ma_cross',
        'trade_amount_qty': 1,
        'risk_per_trade': 0.02,
    }
    
    bot = SSITradingBot(
        DATA_CONSUMER_ID, DATA_CONSUMER_SECRET,
        TRADING_CONSUMER_ID, TRADING_CONSUMER_SECRET, TRADING_PRIVATE_KEY, OTP,
        config
    )
    bot.start()

if __name__ == "__main__":
    main()
