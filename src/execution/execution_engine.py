import MetaTrader5 as mt5
import logging

logger = logging.getLogger("PropBot.Execution")

class ExecutionEngine:
    def __init__(self, magic_number: int, notifier=None):
        self.magic_number = magic_number
        self.notifier = notifier

    def place_market_order(self, symbol: str, volume: float, order_type: str, stop_loss: float = 0.0, take_profit: float = 0.0, deviation: int = 20) -> bool:
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
            "comment": "PropBot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        # Check filling mode support (Simplified)
        # Some brokers require FOK or RETURN. IOC is common for ECN.
        
        logger.info(f"Sending Order: {request}")
        result = mt5.order_send(request)
        
        if result is None:
            logger.error("Order send failed (Unknown error)")
            return False
            
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Order failed: {result.retcode} - {result.comment}")
            return False
            
        logger.info(f"Order Executed: {result.order}")
        
        if self.notifier:
            side = "BUY" if order_type == mt5.ORDER_TYPE_BUY else "SELL"
            msg = f"🚀 **Trade Executed**\nSymbol: {symbol}\nSide: {side}\nVolume: {volume}\nPrice: {result.price}\nSL: {stop_loss}\nTP: {take_profit}"
            self.notifier.send_message(msg)
            
        return True

    def close_position(self, ticket: int, symbol: str) -> bool:
        """
        Closes an existing position by ticket.
        """
        # Need to find the position first
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
            msg = f"🔒 **Position Closed**\nTicket: {ticket}\nprofit: {result.profit}"
            self.notifier.send_message(msg)

        return True

    def place_limit_order(self, symbol: str, volume: float, order_type: int, price: float, stop_loss: float = 0.0, take_profit: float = 0.0) -> bool:
        """
        Places a pending LIMIT order.
        """
        if not mt5.symbol_select(symbol, True):
            return False

        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": stop_loss,
            "tp": take_profit,
            "magic": self.magic_number,
            "comment": "PropBot Grid",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN, # Limit orders often need RETURN
        }

        logger.info(f"Sending Limit Order: {request}")
        result = mt5.order_send(request)
        
        if result is None:
            logger.error("Limit Order send failed (Unknown)")
            return False
            
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Limit Order failed: {result.retcode} - {result.comment}")
            return False
            
        logger.info(f"Limit Order Placed: {result.order}")
        
        if self.notifier:
             side = "BUY LIMIT" if order_type == mt5.ORDER_TYPE_BUY_LIMIT else "SELL LIMIT"
             msg = f"⏳ **Limit Order Placed**\nSymbol: {symbol}\nSide: {side}\nVolume: {volume}\nPrice: {price}\nSL: {stop_loss}"
             self.notifier.send_message(msg)

        return True

    def modify_order(self, ticket: int, sl: float, tp: float) -> bool:
        """
        Modifies an existing order/position SL/TP
        """
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
            logger.info(f"Panic/Friday Close: Closed {count} positions.")
            if self.notifier:
                self.notifier.send_message(f"🚨 **Friday Exit**\nClosed {count} positions.")
        
        return count
