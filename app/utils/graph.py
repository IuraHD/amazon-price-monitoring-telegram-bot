import io
import math
import datetime as dt
from typing import Sequence
import matplotlib.pyplot as plt

# history rows: Row('ts','price')

def build_price_graph(history: Sequence, title: str) -> bytes:
    if not history:
        history = [(dt.datetime.now().isoformat(), math.nan)]
    times = [dt.datetime.fromisoformat(r["ts"]) for r in history]
    prices = [r["price"] for r in history]

    plt.style.use("seaborn-v0_8")
    fig, ax = plt.subplots(figsize=(6, 3.0), dpi=140)
    ax.plot(times, prices, marker="o", linewidth=1.3)
    ax.set_title(title, fontsize=10)
    ax.set_ylabel("Price (£)")
    ax.grid(alpha=0.35)
    ax.tick_params(axis="x", rotation=25, labelsize=8)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
