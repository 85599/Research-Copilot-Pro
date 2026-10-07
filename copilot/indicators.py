"""Technical indicators on a pandas OHLCV frame (Open/High/Low/Close/Volume).
Wilder smoothing for RSI/ATR, same indicator names as TradingAgents' market analyst."""
import pandas as pd


def sma(s, n):
    return s.rolling(n).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, 1e-12)
    return 100 - 100 / (1 + rs)


def macd(close, fast=12, slow=26, sig=9):
    line = ema(close, fast) - ema(close, slow)
    signal = ema(line, sig)
    return line, signal, line - signal


def bollinger(close, n=20, k=2):
    mid = sma(close, n)
    sd = close.rolling(n).std(ddof=0)
    return mid, mid + k * sd, mid - k * sd


def atr(df, n=14):
    pc = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def vwma(df, n=20):
    pv = (df["Close"] * df["Volume"]).rolling(n).sum()
    return pv / df["Volume"].rolling(n).sum().replace(0, float("nan"))


def compute_all(df):
    """Returns a DataFrame with every indicator the Market Analyst may cite."""
    c = df["Close"]
    out = pd.DataFrame(index=df.index)
    out["close_10_ema"] = ema(c, 10)
    out["close_50_sma"] = sma(c, 50)
    out["close_200_sma"] = sma(c, 200)
    out["rsi"] = rsi(c)
    out["macd"], out["macds"], out["macdh"] = macd(c)
    out["boll"], out["boll_ub"], out["boll_lb"] = bollinger(c)
    out["atr"] = atr(df)
    out["vwma"] = vwma(df)
    return out


def latest(ind):
    row = ind.iloc[-1]
    return {k: (None if pd.isna(v) else float(v)) for k, v in row.items()}
