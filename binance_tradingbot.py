"""
Binance Trading Bot (Inherits from BaseBot)
"""

import threading
import json
from base_bot import BaseTradingBot
from binance.client import Client
from binance.enums import *
import websocket

class BinanceTradingBot(BaseTradingBot):
    def __init__(self, api_key, api_secret, config):
        super().__init__(config)
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = config.get('testnet', True)
        self.trade_amount_usdt = config.get('trade_amount_usdt', 100)
        
        # Initialize Client
        # CP1:
        # - Binance sử dụng 1package, 1 client cho cả Data/Trading
        # - Binance có hỗ trợ môi trường paper
        if self.testnet:
            self.client = Client(api_key, api_secret, testnet=True)
            self.client.API_URL = 'https://testnet.binance.vision/api'
            ws_base = "wss://testnet.binance.vision/ws"
        else:
            self.client = Client(api_key, api_secret)
            ws_base = "wss://stream.binance.com:9443/ws"
            
        self.ws_url = f"{ws_base}/{self.symbol.lower()}@trade"
        self.ws_kline_url = f"{ws_base}/{self.symbol.lower()}@kline_1m"
        
        self.ws = None
        self.ws_kline = None

    def get_symbol_info(self):
        """Get symbol specifics from Binance"""
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
            print(f"📊 Symbol Info: Tick {self.tick_size}, Step {self.step_size}")
        except Exception as e:
            print(f"❌ Error getting symbol info: {e}")

    def calculate_quantity(self):
        """Calculate quantity based on USDT amount"""
        if not self.current_price: return 0
        quantity = self.trade_amount_usdt / self.current_price
        quantity = self.round_quantity(quantity)
        if quantity * self.current_price < getattr(self, 'min_notional', 10):
            quantity = self.round_quantity(getattr(self, 'min_notional', 10) / self.current_price * 1.01)
        return quantity

    def place_order_api(self, side, quantity, price=None, order_type=ORDER_TYPE_MARKET):
        """Place Order via Binance API"""
        print(f"DEBUG: Placing {side} {quantity}")
        try:
            # CP4:
            # - SDK hỗ trợ nhiều method cho từng loại lệnh với từng argument truyền lên API -> người dùng ko bị confuse giữa các argument của API
            # - Khi đặt lệnh có định danh cho clientId -> 1 tài khoản chạy nhiều bot thì vẫn nắm được lệnh nào của bot nào
            # - Khi đặt lênh thành công thì server trả về response có kèm orderId để có thể trace luôn
            # - Hỗ trợ đặt và hủy lệnh theo batch
            order = self.client.create_order(
                symbol=self.symbol,
                side=side,
                type=order_type,
                quantity=quantity
            )
            
            filled_qty = float(order.get('executedQty', 0))
            avg_price = 0
            if 'fills' in order:
                total_cost = sum(float(f['price']) * float(f['qty']) for f in order['fills'])
                avg_price = total_cost / filled_qty if filled_qty > 0 else 0
                
            return {
                'orderId': order['orderId'],
                'quantity': filled_qty,
                'price': avg_price,
                'side': side
            }
        except Exception as e:
            print(f"Error: {e}")
            return None

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

    def on_trade_message(self, ws, message):
        """Process trade data"""
        try:
            data = json.loads(message)
            self.current_price = float(data['p'])
            self.check_position_management()
        except Exception:
            pass

    def start_stream(self):
        """Start WebSocket streams"""
        # CP3:
        # - Raw Websocket: dễ dàng kết nối và tùy chỉnh với đa dạng ngôn ngữ
        self.ws = websocket.WebSocketApp(
            self.ws_url, on_message=self.on_trade_message,
            on_open=lambda ws: print("✅ Trade WS connected")
        )
        self.ws_kline = websocket.WebSocketApp(
            self.ws_kline_url, on_message=self.on_kline_message,
            on_open=lambda ws: print("✅ Kline WS connected")
        )
        
        t1 = threading.Thread(target=self.ws.run_forever)
        t2 = threading.Thread(target=self.ws_kline.run_forever)
        t1.daemon = True
        t2.daemon = True
        t1.start()
        t2.start()

    def start(self):
        """Start Bot"""
        self.get_symbol_info()
        print("Loading historical data...")
        start_time="1 Jan, 2026"
        end_time="1 Feb, 2026"
        # CP2
        # - Hỗ trợ da dạng timeframe data (1s, 1m,3m,5m,... 1d,3d,1w,1M)
        klines = self.client.get_historical_klines(
            symbol=self.symbol, interval=Client.KLINE_INTERVAL_1MINUTE, start_time, end_time
        )
        for k in klines:
            self.klines.append({
                'time': k[0], 'open': k[1], 'high': k[2], 'low': k[3], 'close': k[4], 'volume': k[5]
            })
        print(f"Loaded {len(self.klines)} candles")
        
        self.running = True
        self.start_stream()
        self.run_loop()

    def stop(self):
        super().stop()
        if self.ws: self.ws.close()
        if self.ws_kline: self.ws_kline.close()

def main():
    API_KEY = "YOUR_API_KEY"
    API_SECRET = "YOUR_API_SECRET"
    
    if API_KEY == "YOUR_API_KEY":
        print("❌ Please update API_KEY")
        return
        
    config = {
        'symbol': 'BTCUSDT',
        'testnet': True,
        'mode': 'spot',
        'strategy': 'ma_cross',
        'trade_amount_usdt': 100,
        'risk_per_trade': 0.02,
    }
    
    bot = BinanceTradingBot(API_KEY, API_SECRET, config)
    bot.start()

if __name__ == "__main__":
    main()