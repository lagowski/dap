"""Fetch 1-year OHLCV data and compute technical indicators.

Usage::

    python scripts/fetch_technicals.py SOFI

Outputs a JSON object with keys:
    symbol, rsi_14, macd_signal, price_vs_50sma, price_vs_200sma, close_price
"""

from __future__ import annotations

import json
import sys

import pandas as pd
import yfinance as yf

MACD_NEUTRAL_THRESHOLD = 0.005  # 0.5 %


def _safe_float(value: object) -> float | None:
    """Cast to native Python float, returning None for NaN/None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        f = float(value)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def _classify_macd(macd_line: float | None, signal_line: float | None) -> str | None:
    """Return 'bullish', 'bearish', or 'neutral' based on MACD vs signal."""
    if macd_line is None or signal_line is None:
        return None
    if signal_line == 0:
        if macd_line > 0:
            return "bullish"
        if macd_line < 0:
            return "bearish"
        return "neutral"
    pct_diff = (macd_line - signal_line) / abs(signal_line)
    if pct_diff > MACD_NEUTRAL_THRESHOLD:
        return "bullish"
    if pct_diff < -MACD_NEUTRAL_THRESHOLD:
        return "bearish"
    return "neutral"


def fetch_technicals(symbol: str) -> dict:
    """Compute technical indicators for *symbol* and return a JSON-safe dict."""
    df: pd.DataFrame = yf.download(symbol, period="1y", progress=False)

    if df.empty or len(df) < 14:
        return {
            "symbol": symbol,
            "rsi_14": None,
            "macd_signal": None,
            "price_vs_50sma": None,
            "price_vs_200sma": None,
            "close_price": None,
        }

    close: pd.Series = df["Close"].squeeze()
    last_close = _safe_float(close.iloc[-1])

    # --- RSI-14 via pandas_ta ---
    import pandas_ta as ta

    rsi_series = ta.rsi(close, length=14)
    rsi_14 = _safe_float(rsi_series.iloc[-1]) if rsi_series is not None else None

    # --- MACD (12, 26, 9) via pandas_ta ---
    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    if macd_df is not None and not macd_df.empty:
        macd_line = _safe_float(macd_df.iloc[-1, 0])  # MACD_12_26_9
        signal_line = _safe_float(macd_df.iloc[-1, 2])  # MACDs_12_26_9
    else:
        macd_line = None
        signal_line = None
    macd_signal = _classify_macd(macd_line, signal_line)

    # --- SMA 50 / 200 ---
    sma_50 = _safe_float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
    sma_200 = _safe_float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

    price_vs_50 = _safe_float(last_close / sma_50) if last_close and sma_50 else None
    price_vs_200 = _safe_float(last_close / sma_200) if last_close and sma_200 else None

    return {
        "symbol": symbol,
        "rsi_14": rsi_14,
        "macd_signal": macd_signal,
        "price_vs_50sma": price_vs_50,
        "price_vs_200sma": price_vs_200,
        "close_price": last_close,
    }


def main() -> dict:
    """CLI entry-point: parse symbol from argv, print JSON, return result."""
    if len(sys.argv) < 2:
        print("Usage: python scripts/fetch_technicals.py SYMBOL", file=sys.stderr)
        sys.exit(1)
    symbol = sys.argv[1].upper()
    result = fetch_technicals(symbol)
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    main()
