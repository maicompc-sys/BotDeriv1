from bot.database import get_setting, get_trades
from datetime import date

class RiskManager:
    def __init__(self):
        self.refresh()

    def refresh(self):
        self.default_stake    = float(get_setting("default_stake",       "1.00"))
        self.max_stake        = float(get_setting("max_stake",           "100.00"))
        self.daily_loss_limit = float(get_setting("daily_loss_limit",    "50.00"))
        self.martingale_mult  = float(get_setting("martingale_multiplier","2.1"))
        self.max_mart_steps   = int(  get_setting("max_martingale_steps", "4"))
        self.kelly_fraction   = float(get_setting("kelly_fraction",      "0.25"))

    def kelly_stake(self, win_rate, avg_win, avg_loss, balance):
        # FIX: protege contra avg_win=0 que causava ZeroDivisionError
        if avg_loss == 0 or avg_win == 0 or win_rate <= 0:
            return self.default_stake
        b     = avg_win / avg_loss
        # b garantidamente > 0 aqui
        kelly = max(0, (b * win_rate - (1 - win_rate)) / b) * self.kelly_fraction
        return round(min(max(balance * kelly, self.default_stake), self.max_stake), 2)

    def get_stake(self, strategy, balance, step=0):
        if step > 0:
            stake = self.default_stake * (self.martingale_mult ** min(step, self.max_mart_steps))
            return round(min(stake, self.max_stake), 2)
        return round(min(self.default_stake, self.max_stake), 2)

    def check_daily_limit(self):
        today  = date.today().isoformat()
        trades = get_trades(limit=1000)
        today_trades = [t for t in trades if (t.get("entry_time","") or "").startswith(today)]
        today_pnl    = sum(t.get("profit", 0) or 0 for t in today_trades)
        return today_pnl <= -self.daily_loss_limit, today_pnl

    def check_max_drawdown(self, balance, peak_balance):
        if peak_balance == 0: return False
        return (peak_balance - balance) / peak_balance >= 0.20
