#!/usr/bin/env python3
"""
Trend Trading Bot - Modular role-based trading system
Usage: python trend_trading.py <SYMBOL>
Example: python trend_trading.py BTC/USD
         python trend_trading.py AAPL
"""

import sys
import os
import time
from datetime import datetime
from alpaca.trading.requests import LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from roles.trend import TrendRole
from roles.plot import PlotRole


class TrendTradingBot:
    """Main trading bot orchestrator"""
    
    CHECK_INTERVAL = 10  # Check market every 60 seconds (1 minute)
    
    def __init__(self, symbol):
        self.symbol = symbol
        self.trend_role = TrendRole(symbol)
        
        # Create timestamped log file
        self.log_filename = self._create_log_file()
        
        # State tracking
        self.position = None
        self.entry_price = None
        self.stop_loss = None
        self.first_target = None
        self.first_target_hit = False
        self.breakeven_stop_set = False
        self.qty_per_order = 1  # Two separate orders
        self.total_qty = 2
        
        print(f"\n╔{'='*58}╗")
        print(f"║ Trend Trading Bot for {symbol:<32} ║")
        print(f"╠{'='*58}╣")
        print(f"║ Market: {'Crypto' if '/' in symbol else 'Stock':<45} ║")
        print(f"║ Volume per Candle: 1000                           ║")
        print(f"║ Trend Lookback: 10 candles                       ║")
        print(f"║ Position Size: {self.total_qty} shares                              ║")
        print(f"║ Check Interval: Every {self.CHECK_INTERVAL}s (1 minute)               ║")
        print(f"║ Log File: {os.path.basename(self.log_filename):<39} ║")
        print(f"╚{'='*58}╝\n")
    
    def _create_log_file(self):
        """Create timestamped log file"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"trading_log_{self.symbol.replace('/', '_')}_{timestamp}.txt"
        filepath = os.path.join(os.path.dirname(__file__), filename)
        
        # Create file with header
        with open(filepath, 'w') as f:
            f.write(f"Trading Log - {self.symbol}\n")
            f.write(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("="*60 + "\n\n")
        
        return filepath
    
    def _write_log(self, msg):
        """Write message to log file"""
        try:
            with open(self.log_filename, 'a') as f:
                f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        except:
            pass
    
    def check_existing_position(self):
        """Check if position already exists"""
        try:
            positions = self.trend_role.trading_client.get_all_positions()
            for pos in positions:
                if pos.symbol == self.symbol:
                    self.position = pos
                    return True
        except:
            pass
        return False
    
    def log(self, msg, level="INFO"):
        """Print formatted log message and write to file"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        prefix = {
            'INFO': '  ℹ',
            'TRADE': '  🔄',
            'BUY': '  ✓ BUY',
            'SELL': '  ✓ SELL',
            'STOP': '  ✗ STOP',
            'ERROR': '  ✗',
            'SUCCESS': '  ✓'
        }.get(level, '  •')
        
        output = f"[{timestamp}] {prefix} {msg}"
        print(output)
        self._write_log(f"[{level}] {msg}")
    
    def run_analysis(self):
        """Run trend analysis"""
        self.log("Analyzing trend...", "INFO")
        return self.trend_role.analyze()
    
    def place_entry_order(self, mid_price):
        """Place limit buy order at mid price"""
        self.log(f"Placing entry orders at ${mid_price:.2f} ({self.total_qty} shares)...", "BUY")
        
        try:
            for i in range(self.total_qty):
                order = LimitOrderRequest(
                    symbol=self.symbol,
                    qty=self.qty_per_order,
                    side=OrderSide.BUY,
                    limit_price=mid_price,
                    time_in_force=TimeInForce.GTC
                )
                response = self.trend_role.trading_client.submit_order(order)
                self.log(f"Order {i+1} placed: {response.id}", "BUY")
            
            self.entry_price = mid_price
            time.sleep(2)
            return True
        except Exception as e:
            self.log(f"Error placing order: {e}", "ERROR")
            return False
    
    def place_sell_order(self, qty, target_price, order_type="Target"):
        """Place sell order at target price"""
        self.log(f"Placing {order_type} sell for {qty} at ${target_price:.2f}...", "SELL")
        
        try:
            order = LimitOrderRequest(
                symbol=self.symbol,
                qty=qty,
                side=OrderSide.SELL,
                limit_price=target_price,
                time_in_force=TimeInForce.GTC
            )
            response = self.trend_role.trading_client.submit_order(order)
            self.log(f"{order_type} order placed: {response.id}", "SELL")
            return True
        except Exception as e:
            self.log(f"Error placing {order_type}: {e}", "ERROR")
            return False
    
    def close_position_at_market(self, qty, reason="Manual"):
        """Close position at market price"""
        self.log(f"Closing {qty} shares ({reason})...", "STOP")
        
        try:
            from alpaca.trading.requests import MarketOrderRequest
            order = MarketOrderRequest(
                symbol=self.symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC
            )
            response = self.trend_role.trading_client.submit_order(order)
            self.log(f"Market close order placed: {response.id}", "STOP")
            return True
        except Exception as e:
            self.log(f"Error closing position: {e}", "ERROR")
            return False
    
    def run(self):
        """Main trading loop"""
        # Check for existing position
        if self.check_existing_position():
            self.log(f"Found existing position: {self.position.qty} {self.symbol}", "INFO")
        else:
            self.log("No existing position found", "INFO")
        
        try:
            while True:
                analysis = self.run_analysis()
                
                candles = analysis['candles']
                breakout = analysis['breakout']
                stop_loss_level = analysis['stop_loss']
                
                # Skip if not enough data
                if analysis['trend_line'] is None:
                    self.log(f"Waiting for more data... ({len(candles)}/20 candles)", "INFO")
                    time.sleep(5)
                    continue
                
                current_price = breakout['current_price']
                
                # Display current status
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Price: ${current_price:.2f} | " +
                      f"Trend: ${breakout['trend_value']:.2f} | " +
                      f"Distance: ${breakout['distance']:.2f}")
                
                # Get current position
                try:
                    positions = self.trend_role.trading_client.get_all_positions()
                    self.position = None
                    for pos in positions:
                        if pos.symbol == self.symbol:
                            self.position = pos
                            break
                except:
                    pass
                
                # No position - check for breakout
                if self.position is None:
                    if analysis['trend_line'] is None:
                        self.log(f"Not enough data yet. Have {len(analysis['candles'])} candles, need 20", "INFO")
                    elif breakout['breakout']:
                        self.log("✓ BREAKOUT DETECTED!", "SUCCESS")
                        
                        # Calculate entry: mid price of latest candle
                        last_candle = breakout['last_candle']
                        mid_price = (last_candle['open'] + last_candle['close']) / 2
                        
                        self.stop_loss = stop_loss_level
                        self.first_target = mid_price + (mid_price - stop_loss_level)
                        
                        self.log(f"Entry (mid): ${mid_price:.2f}", "INFO")
                        self.log(f"Stop Loss: ${self.stop_loss:.2f}", "INFO")
                        self.log(f"Target 1: ${self.first_target:.2f}", "INFO")
                        
                        self.place_entry_order(mid_price)
                
                # Has position - monitor profit targets
                else:
                    pos_qty = float(self.position.qty)
                    pnl = float(self.position.unrealized_pl)
                    
                    self.log(f"Position: {pos_qty} | P&L: ${pnl:.2f}", "TRADE")
                    
                    # Check stop loss
                    if current_price <= self.stop_loss:
                        self.log(f"STOP LOSS HIT at ${current_price:.2f}!", "STOP")
                        self.close_position_at_market(pos_qty, "Stop Loss")
                        self.position = None
                        self.entry_price = None
                        self.stop_loss = None
                        self.first_target_hit = False
                        self.breakeven_stop_set = False
                    
                    # Check first target
                    elif not self.first_target_hit and current_price >= self.first_target:
                        self.log(f"FIRST TARGET HIT at ${current_price:.2f}!", "SUCCESS")
                        self.place_sell_order(1, self.first_target, "First Target")
                        self.first_target_hit = True
                        self.stop_loss = self.entry_price
                        self.breakeven_stop_set = True
                        self.log(f"Stop loss moved to breakeven: ${self.stop_loss:.2f}", "INFO")
                    
                    # Check second target (2x first target profit)
                    elif self.first_target_hit and pos_qty > 0:
                        second_target = self.entry_price + (self.first_target - self.entry_price) * 2
                        if current_price >= second_target:
                            self.log(f"SECOND TARGET HIT at ${current_price:.2f}!", "SUCCESS")
                            self.close_position_at_market(pos_qty, "Second Target")
                            self.position = None
                            self.entry_price = None
                            self.stop_loss = None
                            self.first_target_hit = False
                            self.breakeven_stop_set = False
                
                time.sleep(self.CHECK_INTERVAL)  # Check every 1 minute
        
        except KeyboardInterrupt:
            self.log("Bot stopped by user", "INFO")


def main():
    if len(sys.argv) < 2:
        print("Usage: python trend_trading.py <SYMBOL>")
        print("Example: python trend_trading.py BTC/USD")
        print("         python trend_trading.py AAPL")
        sys.exit(1)
    
    symbol = sys.argv[1].upper()
    
    # Show plot first
    print("\n" + "="*60)
    print("DISPLAYING TREND CHART")
    print("="*60 + "\n")
    
    plot_bot = PlotRole(symbol)
    plot_bot.print_analysis()
    plot_bot.plot_chart()
    
    # Then run the trading bot
    print("\n" + "="*60)
    print("STARTING TRADING BOT")
    print("="*60 + "\n")
    
    bot = TrendTradingBot(symbol)
    bot.run()


if __name__ == '__main__':
    main()
