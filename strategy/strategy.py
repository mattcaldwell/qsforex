import copy
from decimal import Decimal

from qsforex.event.event import SignalEvent


class TestStrategy(object):
    """
    A testing strategy that alternates between buying and selling
    a currency pair on every 5th tick. This has the effect of
    continuously "crossing the spread" and so will be loss-making
    strategy.

    It is used to test that the backtester/live trading system is
    behaving as expected.
    """
    def __init__(self, pairs, events):
        self.pairs = pairs
        self.events = events
        self.ticks = 0
        self.invested = False

    def calculate_signals(self, event):
        if event.type == 'TICK' and event.instrument == self.pairs[0]:
            if self.ticks % 5 == 0:
                if self.invested is False:
                    signal = SignalEvent(self.pairs[0], "market", "buy", event.time)
                    self.events.put(signal)
                    self.invested = True
                else:
                    signal = SignalEvent(self.pairs[0], "market", "sell", event.time)
                    self.events.put(signal)
                    self.invested = False
            self.ticks += 1


class PSARWithMACDStrategy(object):
    """
    Parabolic SAR with MACD.

    See: http://forex-strategies-revealed.com/trading-strategy-eurusd
    """
    def __init__(self, pairs, events, step=0.02, af=0.2, af_max=0.2):
        self.pairs = pairs
        self.events = events
        self.step = Decimal(step)
        self.af = Decimal(af)
        self.af_max = Decimal(af_max)
        self.pairs_dict = self.create_pairs_dict()

    def create_pairs_dict(self):
        attr_dict = {
            "af": self.af,
            "direction": 0,
            "ep": Decimal(0),
            "invested": False,
            "long": False,
            "short": False,
            "ticks": 0,
            "highs": [],
            "last_event": None,
            "lows": [],
            "prior_sar": None,
        }
        pairs_dict = {}
        for p in self.pairs:
            pairs_dict[p] = copy.deepcopy(attr_dict)
        return pairs_dict

    def in_uptrend(self, pd, event):
        return pd["direction"] > 0

    def in_downtrend(self, pd, event):
        return pd["direction"] < 0

    def calculate_signals(self, event):
        if event.type == 'TICK' and event.instrument == self.pairs[0]:
            pair = event.instrument
            # event.bid (price when selling)
            # event.ask (price when buying)
            pd = self.pairs_dict[pair]

            if event.ask_high.is_nan() or event.ask_low.is_nan() or \
               event.bid_high.is_nan() or event.bid_low.is_nan():
                # skip bad data
                return

            if pd["ticks"] == 0:
                pd["last_event"] = event
                pd["ticks"] += 1
                return

            elif pd["ticks"] == 1:
                pd["prior_sar"] = (event.ask_high + event.ask_low + \
                                   event.bid_high + event.bid_low) / Decimal(4)

                if (pd["prior_sar"] - event.bid_low) > (event.bid_high - pd["prior_sar"]):
                    pd["direction"] = 1
                    pd["highs"].append(pd["last_event"].ask_high)
                    pd["lows"].append(pd["last_event"].ask_low)
                    pd["af"] = self.af
                    pd["ep"] = max(pd["highs"])
                else:
                    pd["direction"] = -1
                    pd["highs"].append(pd["last_event"].bid_high)
                    pd["lows"].append(pd["last_event"].bid_low)
                    pd["af"] = -self.af
                    pd["ep"] = min(pd["lows"])

            if self.in_uptrend(pd, event):
                pd["highs"].append(event.ask_high)
                pd["lows"].append(event.ask_low)

                sar = pd["prior_sar"] + pd["af"] * (pd["ep"] - pd["prior_sar"])
                high = pd["highs"][-1]
                if high > pd["ep"]:
                    pd["af"] = min(pd["af"] + self.step, self.af_max)
                    pd["ep"] = high

                if min(pd["lows"]) < sar:
                    pd["direction"] = -2
                    pd["af"] = -self.af
                    sar = pd["ep"]

            elif self.in_downtrend(pd, event):
                pd["highs"].append(event.bid_high)
                pd["lows"].append(event.bid_low)

                sar = pd["prior_sar"] + pd["af"] * (pd["prior_sar"] - pd["ep"])

                low = pd["lows"][-1]
                if low < pd["ep"]:
                    pd["af"] = max(pd["af"] - self.step, -self.af_max)
                    pd["ep"] = low

                if max(pd["highs"]) > sar:
                    pd["af"] = self.af
                    sar = pd["ep"]

            if pd["direction"] == -2:
                pd["direction"] = -1

            if sar < event.ask:
                if not pd["long"]:
                    # buy signal
                    signal = SignalEvent(self.pairs[0], "market", "buy", event.time)
                    self.events.put(signal)
                    pd["long"] = True
                    pd["short"] = False

            if sar > event.bid:
                if not pd["short"]:
                    # sell signal
                    signal = SignalEvent(self.pairs[0], "market", "sell", event.time)
                    self.events.put(signal)
                    pd["short"] = True
                    pd["long"] = False

            pd["prior_sar"] = sar
            pd["last_event"] = event
            pd["ticks"] += 1

class MovingAverageCrossStrategy(object):
    """
    A basic Moving Average Crossover strategy that generates
    two simple moving averages (SMA), with default windows
    of 500 ticks for the short SMA and 2,000 ticks for the
    long SMA.

    The strategy is "long only" in the sense it will only
    open a long position once the short SMA exceeds the long
    SMA. It will close the position (by taking a corresponding
    sell order) when the long SMA recrosses the short SMA.

    The strategy uses a rolling SMA calculation in order to
    increase efficiency by eliminating the need to call two
    full moving average calculations on each tick.
    """
    def __init__(
        self, pairs, events,
        short_window=500, long_window=2000
    ):
        self.pairs = pairs
        self.pairs_dict = self.create_pairs_dict()
        self.events = events
        self.short_window = short_window
        self.long_window = long_window

    def create_pairs_dict(self):
        attr_dict = {
            "ticks": 0,
            "invested": False,
            "short_sma": None,
            "long_sma": None
        }
        pairs_dict = {}
        for p in self.pairs:
            pairs_dict[p] = copy.deepcopy(attr_dict)
        return pairs_dict

    def calc_rolling_sma(self, sma_m_1, window, price):
        return ((sma_m_1 * (window - 1)) + price) / window

    def calculate_signals(self, event):
        if event.type == 'TICK':
            pair = event.instrument
            price = event.bid
            pd = self.pairs_dict[pair]
            if pd["ticks"] == 0:
                pd["short_sma"] = price
                pd["long_sma"] = price
            else:
                pd["short_sma"] = self.calc_rolling_sma(
                    pd["short_sma"], self.short_window, price
                )
                pd["long_sma"] = self.calc_rolling_sma(
                    pd["long_sma"], self.long_window, price
                )
            # Only start the strategy when we have created an accurate short window
            if pd["ticks"] > self.short_window:
                if pd["short_sma"] > pd["long_sma"] and not pd["invested"]:
                    signal = SignalEvent(pair, "market", "buy", event.time)
                    self.events.put(signal)
                    pd["invested"] = True
                if pd["short_sma"] < pd["long_sma"] and pd["invested"]:
                    signal = SignalEvent(pair, "market", "sell", event.time)
                    self.events.put(signal)
                    pd["invested"] = False
            pd["ticks"] += 1
