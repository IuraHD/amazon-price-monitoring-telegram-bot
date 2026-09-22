import io
import threading
from datetime import datetime
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

_LOCK = threading.Lock()


def build_price_graph(history, title):
    if not history:
        return None
    with _LOCK:
        fig = Figure(figsize=(8, 3.8), dpi=120)
        FigureCanvasAgg(fig)
        ax = fig.subplots()
        times = [datetime.fromisoformat(r["ts"]) for r in history]
        prices = [r["price_pence"] / 100 for r in history]
        ax.scatter(times, prices, s=8)
        ax.set_title(title[:90])
        ax.set_ylabel("Recorded price (GBP)")
        ax.set_xlabel("UTC · recorded samples; gaps are not interpolated")
        ax.grid(alpha=0.25)
        fig.autofmt_xdate()
        fig.tight_layout()
        output = io.BytesIO()
        fig.savefig(output, format="png")
        fig.clear()
        return output.getvalue()
