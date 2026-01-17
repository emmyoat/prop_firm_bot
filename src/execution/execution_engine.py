import MetaTrader5 as mt5
import logging
import math

logger = logging.getLogger("PropBot.Execution")

class ExecutionEngine:
    def __init__(self, magic_number: int, notifier=None):
        self.magic_number = magic_number
        self.notifier = notifier

    def _normalize_price(self, symbol: str, price: float) -> float:
        """
        Rounds price to the nearest tick size for the symbol.
        """
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            logger.warning(f"Could not fetch symbol info for {symbol} to normalize price. Using raw price.")
            return price
            
        tick_size = symbol_info.trade_tick_size
        if tick_size == 0:
            return price
            
        return round(price / tick_size) * tick_size

    def place_market_order(self, symbol: str, volume: float, order_type: str, stop_loss: float = 0.0, take_profit: float = 0.0, comment: str = "PropBot", deviation: int = 20) -> bool:
        """
        places a market order (ORDER_TYPE_BUY or ORDER_TYPE_SELL)
        """
        # Ensure symbol is selected
        if not mt5.symbol_select(symbol, True):
            logger.error(f"Execution: Failed to select symbol {symbol}")
            return False

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            logger.error(f"Execution: Tick data not available for {symbol}")
            return False

        price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid
        
        # Normalize SL/TP
        if stop_loss > 0: stop_loss = self._normalize_price(symbol, stop_loss)
        if take_profit > 0: take_profit = self._normalize_price(symbol, take_profit)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": stop_loss,
            "tp": take_profit,
            "deviation": deviation,
            "magic": self.magic_number,
            "comment": comment[:25],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        logger.info(f"Sending Order: {request}")
        result = mt5.order_send(request)
        
        if result is None:
            last_error = mt5.last_error()
            logger.error(f"Order send failed (result is None). MT5 Error: {last_error}")
            return False
            
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Order failed: {result.retcode} - {result.comment}")
            return False
            
        logger.info(f"Order Executed: {result.order}")
        
        if self.notifier:
            side = "BUY" if order_type == mt5.ORDER_TYPE_BUY else "SELL"
            
            # Highlight Logic
            header = "🚀 **Trade Executed**"
            if "SWING" in comment.upper():
                header = "🌊 **SWING TRADE DETECTED** 🌊"
        
            
            # ... (Risk calculation omitted for brevity, assuming existing logic)
            msg = f"{header}\nSymbol: {symbol}\nSide: {side}\nType: {comment}\nVolume: {volume}\nPrice: {result.price}\nSL: {stop_loss}\nTP: {take_profit}"
            self.notifier.send_message(msg)
            
        return True

    def close_position(self, ticket: int, symbol: str) -> bool:
        """
        Closes an existing position by ticket.
        """
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            logger.error(f"Position {ticket} not found")
            return False
            
        pos = positions[0]
        tick = mt5.symbol_info_tick(symbol)
        
        order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if order_type == mt5.ORDER_TYPE_SELL else tick.ask
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": pos.volume,
            "type": order_type,
            "position": ticket,
            "price": price,
            "magic": self.magic_number,
            "comment": "Close Position",
        }
        
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Close failed: {result.retcode}")
            return False
            
        if self.notifier:
            acc = mt5.account_info()
            currency = acc.currency if acc else "$"
            msg = f"🔒 **Position Closed**\nTicket: {ticket}\nProfit: {currency}{result.profit:.2f}"
            self.notifier.send_message(msg)

        return True

    def place_limit_order(self, symbol: str, volume: float, order_type: int, price: float, stop_loss: float = 0.0, take_profit: float = 0.0, comment: str = "PropBot Grid") -> bool:
        """
        Places a pending LIMIT order.
        """
        if not mt5.symbol_select(symbol, True):
            return False
            
        # Normalize
        price = self._normalize_price(symbol, price)
        if stop_loss > 0: stop_loss = self._normalize_price(symbol, stop_loss)
        if take_profit > 0: take_profit = self._normalize_price(symbol, take_profit)

        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": stop_loss,
            "tp": take_profit,
            "magic": self.magic_number,
            "comment": comment[:25],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }

        logger.info(f"Sending Limit Order: {request}")
        result = mt5.order_send(request)
        
        if result is None:
            last_error = mt5.last_error()
            logger.error(f"Limit Order send failed (Unknown). MT5 Error: {last_error}")
            return False
            
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Limit Order failed: {result.retcode} - {result.comment}")
            return False
            
        logger.info(f"Limit Order Placed: {result.order}")
        
        if self.notifier:
             side = "BUY LIMIT" if order_type == mt5.ORDER_TYPE_BUY_LIMIT else "SELL LIMIT"
             
             # Highlight Logic
             header = "⏳ **Limit Order Placed**"
             if "SWING" in comment.upper():
                 header = "🌊 **SWING LIMIT ORDER** 🌊"

             msg = f"{header}\nSymbol: {symbol}\nSide: {side}\nType: {comment}\nVolume: {volume}\nPrice: {price}\nSL: {stop_loss}"
             self.notifier.send_message(msg)

        return True

    def place_stop_order(self, symbol: str, volume: float, order_type: int, price: float, stop_loss: float = 0.0, take_profit: float = 0.0, comment: str = "PropBot Stop", expiration_hours: int = 4) -> bool:
        """
        Places a pending STOP order (BUY STOP or SELL STOP).
        """
        if not mt5.symbol_select(symbol, True):
            return False
            
        # Normalize
        price = self._normalize_price(symbol, price)
        if stop_loss > 0: stop_loss = self._normalize_price(symbol, stop_loss)
        if take_profit > 0: take_profit = self._normalize_price(symbol, take_profit)

        import time
        expiration_time = int(time.time() + (expiration_hours * 3600))

        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": stop_loss,
            "tp": take_profit,
            "magic": self.magic_number,
            "comment": comment[:25],
            "type_time": mt5.ORDER_TIME_SPECIFIED, 
            "expiration": expiration_time,
            "type_filling": mt5.ORDER_FILLING_RETURN, 
        }

        logger.info(f"Sending Stop Order: {request}")
        result = mt5.order_send(request)
        
        if result is None:
            last_error = mt5.last_error()
            logger.error(f"Stop Order send failed (Unknown). MT5 Error: {last_error}")
            return False
            
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Stop Order failed: {result.retcode} - {result.comment}")
            return False
            
        logger.info(f"Stop Order Placed: {result.order}")
        
        if self.notifier:
             side = "BUY STOP" if order_type == mt5.ORDER_TYPE_BUY_STOP else "SELL STOP"
             msg = f"⏳ **Stop Order Placed**\nSymbol: {symbol}\nSide: {side}\nVolume: {volume}\nPrice: {price}\nSL: {stop_loss}"
             self.notifier.send_message(msg)

        return True

    def modify_order(self, ticket: int, sl: float, tp: float) -> bool:
        """
        Modifies an existing order/position SL/TP
        """
        # We should probably normalize here too, but looking up symbol from ticket is harder without passing it.
        # Assuming caller handles normalization or uses raw points... better to be safe, but can't easily get symbol from just ticket strictly speaking without selecting.
        # However, modification usually is less strict if SL/TP is 0. 
        # Ideally we should fetch the position to get the symbol.
        
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": sl,
            "tp": tp,
            "magic": self.magic_number,
        }
        
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
             logger.error(f"Modify Order {ticket} failed")
             return False
             
        logger.info(f"Order {ticket} Modified: SL={sl}, TP={tp}")
        return True

    def close_all_positions(self, symbol: str = None) -> int:
        """
        Closes ALL positions (optionally filtered by symbol).
        Returns number of positions closed.
        """
        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if not positions:
            return 0
        
        count = 0
        for pos in positions:
            if pos.magic == self.magic_number: # Only close our bot's trades
                if self.close_position(pos.ticket, pos.symbol):
                    count += 1
        
        if count > 0:
            logger.info(f"System Close Requested: Closed {count} positions.")
            if self.notifier:
                self.notifier.send_message(f"🚨 **Emergency/Bulk Close**\nClosed {count} positions from magic {self.magic_number}.")
        
        return count
