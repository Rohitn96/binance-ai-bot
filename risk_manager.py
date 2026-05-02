import sys
import logging

import config

_OVERCONCENTRATION_THRESHOLD = 60.0


def check(portfolio: dict, logger: logging.Logger | None = None) -> dict:
    """
    Evaluate portfolio health.

    Returns:
        {
            safe_to_trade:     bool,
            current_value:     float,
            loss_percent:      float,
            overconcentrated:  list of (asset, pct) tuples,
        }

    Calls sys.exit(1) and logs EMERGENCY STOP if stop-loss is breached.
    """
    current_value: float = portfolio.get("total_usdt", 0.0)
    starting: float = config.STARTING_CAPITAL

    loss = starting - current_value
    loss_pct = (loss / starting * 100.0) if starting > 0 else 0.0

    if loss_pct >= config.STOP_LOSS_PERCENT:
        msg = (
            f"EMERGENCY STOP — Portfolio ${current_value:,.2f} is {loss_pct:.1f}% below"
            f" starting capital ${starting:,.2f}."
            f" Stop-loss threshold: {config.STOP_LOSS_PERCENT}%. Bot halted."
        )
        if logger:
            logger.critical(msg)
        else:
            print(f"[CRITICAL] {msg}", file=sys.stderr)
        sys.exit(1)

    balances = portfolio.get("balances", {})
    overconcentrated = [
        (asset, data.get("portfolio_pct", 0.0))
        for asset, data in balances.items()
        if asset != "USDT" and data.get("portfolio_pct", 0.0) > _OVERCONCENTRATION_THRESHOLD
    ]

    if overconcentrated and logger:
        for asset, pct in overconcentrated:
            logger.warning(
                f"RISK WARNING — {asset} is {pct:.1f}% of portfolio"
                f" (>{_OVERCONCENTRATION_THRESHOLD}%)"
            )

    return {
        "safe_to_trade": True,
        "current_value": round(current_value, 2),
        "loss_percent": round(loss_pct, 2),
        "overconcentrated": overconcentrated,
    }
