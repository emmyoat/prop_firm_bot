"""
Rich Terminal UI for Prop Firm Signal Bot
Renders a live updating dashboard in the terminal using the `rich` library.
"""

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.text import Text
from rich.columns import Columns
from rich import box
from rich.align import Align
from rich.rule import Rule
from rich.style import Style
from datetime import datetime
import threading
import time
import os
import sys

# Force UTF-8 output on Windows to support emoji/box-drawing characters
if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    # Reconfigure stdout/stderr to UTF-8 if possible (Python 3.7+)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

console = Console(force_terminal=True, highlight=False)

# ── Colour palette ────────────────────────────────────────────────────────────
CLR_BUY      = "bold bright_green"
CLR_SELL     = "bold bright_red"
CLR_NEUTRAL  = "dim white"
CLR_HEADER   = "bold cyan"
CLR_GOLD     = "bold yellow"
CLR_ACCENT   = "bold bright_cyan"
CLR_WARNING  = "bold orange1"
CLR_DIM      = "dim"
CLR_SUCCESS  = "bold green"
CLR_ERROR    = "bold red"


class TerminalUI:
    """
    Live terminal dashboard for the signal bot.
    Uses Rich's Live context manager to update in-place without scrolling.
    """

    def __init__(self, config: dict):
        self.config = config
        self._lock = threading.Lock()
        self._live: Live | None = None

        # State that the bot updates at runtime
        self.signals: list[dict] = []        # Recent signal history
        self.status: dict = {
            "mode": "SIGNAL-ONLY",
            "state": "STARTING",
            "symbols": config["system"].get("symbol_list", []),
            "virtual_balance": config.get("virtual_account", {}).get("balance", 10000.0),
            "paper_pnl": 0.0,
            "signals_today": 0,
            "wins_today": 0,
            "losses_today": 0,
            "session": "—",
            "news_blocked": False,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_scan": "—",
            "api_calls_used": 0,
        }
        self.prices: dict[str, float] = {}   # Last known price per symbol
        self.log_lines: list[str] = []        # Rolling log (last N lines)
        self.MAX_SIGNALS = 20
        self.MAX_LOGS    = 14

    # ── Public update methods (thread-safe) ───────────────────────────────────

    def update_status(self, **kwargs):
        with self._lock:
            self.status.update(kwargs)
        self._refresh()

    def update_price(self, symbol: str, price: float):
        with self._lock:
            self.prices[symbol] = price
        self._refresh()

    def add_signal(self, signal_dict: dict):
        """Push a new signal to the top of the signals table."""
        with self._lock:
            self.signals.insert(0, signal_dict)
            if len(self.signals) > self.MAX_SIGNALS:
                self.signals = self.signals[:self.MAX_SIGNALS]
            self.status["signals_today"] += 1
        self._refresh()

    def add_log(self, message: str, level: str = "INFO"):
        """Add a line to the rolling log panel."""
        ts = datetime.now().strftime("%H:%M:%S")
        colour = {
            "INFO":    "white",
            "WARNING": "yellow",
            "ERROR":   "red",
            "SUCCESS": "bright_green",
            "DEBUG":   "dim white",
        }.get(level.upper(), "white")

        line = f"[dim]{ts}[/dim]  [{colour}]{message}[/{colour}]"
        with self._lock:
            self.log_lines.append(line)
            if len(self.log_lines) > self.MAX_LOGS:
                self.log_lines = self.log_lines[-self.MAX_LOGS:]
        self._refresh()

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _build_header(self) -> Panel:
        state = self.status.get("state", "RUNNING")
        state_style = CLR_SUCCESS if state == "RUNNING" else CLR_WARNING if state == "PAUSED" else CLR_ERROR
        now = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")

        header_text = Text(justify="center")
        header_text.append("⚡  PROP FIRM SIGNAL BOT  ⚡", style="bold bright_white")
        header_text.append(f"   [{state}]", style=state_style)
        header_text.append(f"   {now}", style="dim white")

        return Panel(
            Align.center(header_text),
            style="bold cyan",
            box=box.DOUBLE,
            padding=(0, 2),
        )

    def _build_stats_bar(self) -> Columns:
        vb      = self.status.get("virtual_balance", 0)
        pnl     = self.status.get("paper_pnl", 0.0)
        sigs    = self.status.get("signals_today", 0)
        wins    = self.status.get("wins_today", 0)
        losses  = self.status.get("losses_today", 0)
        wr      = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0.0
        session = self.status.get("session", "—")
        news    = self.status.get("news_blocked", False)
        scan    = self.status.get("last_scan", "—")

        pnl_style   = CLR_BUY if pnl >= 0 else CLR_SELL
        news_label  = "[bold red]⛔ NEWS BLOCK[/bold red]" if news else "[dim green]✓ News Clear[/dim green]"

        panels = [
            Panel(
                f"[dim]Virtual Balance[/dim]\n[{CLR_GOLD}]${vb:,.2f}[/{CLR_GOLD}]",
                box=box.ROUNDED, style="on grey11", padding=(0, 2)
            ),
            Panel(
                f"[dim]Paper P&L[/dim]\n[{pnl_style}]{'+' if pnl>=0 else ''}{pnl:,.2f}[/{pnl_style}]",
                box=box.ROUNDED, style="on grey11", padding=(0, 2)
            ),
            Panel(
                f"[dim]Signals Today[/dim]\n[{CLR_ACCENT}]{sigs}[/{CLR_ACCENT}]",
                box=box.ROUNDED, style="on grey11", padding=(0, 2)
            ),
            Panel(
                f"[dim]Win Rate[/dim]\n[{CLR_ACCENT}]{wr:.1f}%[/{CLR_ACCENT}] [dim]({wins}W / {losses}L)[/dim]",
                box=box.ROUNDED, style="on grey11", padding=(0, 2)
            ),
            Panel(
                f"[dim]Session[/dim]\n[{CLR_HEADER}]{session}[/{CLR_HEADER}]",
                box=box.ROUNDED, style="on grey11", padding=(0, 2)
            ),
            Panel(
                f"[dim]News Filter[/dim]\n{news_label}",
                box=box.ROUNDED, style="on grey11", padding=(0, 2)
            ),
            Panel(
                f"[dim]Last Scan[/dim]\n[dim white]{scan}[/dim white]",
                box=box.ROUNDED, style="on grey11", padding=(0, 2)
            ),
        ]
        return Columns(panels, equal=False, expand=True)

    def _build_prices_table(self) -> Table:
        table = Table(
            title="📊  Live Prices",
            box=box.SIMPLE_HEAD,
            title_style=CLR_HEADER,
            header_style=CLR_HEADER,
            show_lines=False,
            expand=True,
        )
        table.add_column("Symbol",  style="bold white",  min_width=10)
        table.add_column("Price",   style="bright_white", justify="right", min_width=12)
        table.add_column("TF Pair", style="dim white",   min_width=10)

        symbols   = self.status.get("symbols", [])
        tf_pairs  = self.config.get("strategy", {}).get("active_pairs", [])
        tf_label  = " / ".join(p.get("label", "") for p in tf_pairs)

        for sym in symbols:
            price = self.prices.get(sym)
            price_str = f"{price:.5f}" if price else "[dim]—[/dim]"
            table.add_row(sym, price_str, tf_label)

        return table

    def _build_signals_table(self) -> Table:
        table = Table(
            title="📡  Recent Signals",
            box=box.SIMPLE_HEAD,
            title_style=CLR_HEADER,
            header_style=CLR_HEADER,
            show_lines=True,
            expand=True,
        )
        table.add_column("Time",    style="dim white",   min_width=8)
        table.add_column("Symbol",  style="bold white",  min_width=8)
        table.add_column("TF",      style="dim cyan",    min_width=5)
        table.add_column("Signal",  min_width=6,         justify="center")
        table.add_column("Entry",   style="white",       justify="right", min_width=9)
        table.add_column("SL",      style="bright_red",  justify="right", min_width=9)
        table.add_column("TP",      style="bright_green",justify="right", min_width=9)
        table.add_column("R:R",     style="dim yellow",  justify="right", min_width=5)
        table.add_column("Strategy",style="dim white",   min_width=20)

        with self._lock:
            signals_copy = self.signals[:15]

        for s in signals_copy:
            direction = s.get("direction", "NEUTRAL")
            sig_text  = Text(f"▲ {direction}" if direction == "BUY" else f"▼ {direction}", style=CLR_BUY if direction == "BUY" else CLR_SELL)

            rr  = s.get("rr", 0.0)
            rr_style = CLR_SUCCESS if rr >= 2.0 else CLR_WARNING if rr >= 1.0 else CLR_ERROR
            rr_text  = Text(f"{rr:.2f}R", style=rr_style)

            table.add_row(
                s.get("time", "—"),
                s.get("symbol", "—"),
                s.get("timeframe", "—"),
                sig_text,
                f"{s.get('entry', 0):.5f}",
                f"{s.get('sl', 0):.5f}",
                f"{s.get('tp', 0):.5f}",
                rr_text,
                s.get("comment", "—"),
            )

        if not signals_copy:
            table.add_row("[dim]—[/dim]", "[dim]Scanning for setups...[/dim]", "", "", "", "", "", "", "")

        return table

    def _build_log_panel(self) -> Panel:
        with self._lock:
            lines = list(self.log_lines)

        content = "\n".join(lines) if lines else "[dim]Bot log will appear here...[/dim]"
        return Panel(
            content,
            title="[bold cyan]📋  Bot Log[/bold cyan]",
            box=box.ROUNDED,
            style="on grey7",
            padding=(0, 1),
        )

    def _build_footer(self) -> Text:
        t = Text(justify="center")
        t.append("  TwelveData API  ", style="dim")
        t.append("│", style="dim cyan")
        t.append("  Telegram Alerts  ", style="dim")
        t.append("│", style="dim cyan")
        t.append("  Signal-Only Mode — No MT5  ", style="dim")
        t.append("│", style="dim cyan")
        t.append("  Ctrl+C to stop  ", style="dim")
        return t

    def _render(self):
        """Builds the full layout to render."""
        layout = Layout()

        layout.split_column(
            Layout(self._build_header(),      name="header",  size=3),
            Layout(self._build_stats_bar(),   name="stats",   size=5),
            Layout(name="main",               ratio=1),
            Layout(self._build_log_panel(),   name="log",     size=self.MAX_LOGS + 4),
            Layout(self._build_footer(),      name="footer",  size=1),
        )

        layout["main"].split_row(
            Layout(self._build_prices_table(),  name="prices",  ratio=1),
            Layout(self._build_signals_table(), name="signals", ratio=3),
        )

        return layout

    def _refresh(self):
        if self._live:
            try:
                self._live.update(self._render())
            except Exception:
                pass  # Suppress render errors (e.g. terminal resize)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        """Start the Rich Live display. Call once from the main thread."""
        self._live = Live(
            self._render(),
            console=console,
            refresh_per_second=2,
            screen=True,
        )
        self._live.start()
        self.update_status(state="RUNNING")

    def stop(self):
        """Stop the Live display (call on shutdown)."""
        if self._live:
            self._live.stop()

    def print_startup_banner(self):
        """Prints a one-time startup banner before the Live UI takes over."""
        try:
            console.rule("[bold cyan]** Prop Firm Signal Bot **[/bold cyan]")
            console.print(
                "  [bold]Mode:[/bold] [bright_cyan]SIGNAL-ONLY[/bright_cyan]   "
                "[bold]Data:[/bold] [yellow]TwelveData API[/yellow]   "
                "[bold]Alerts:[/bold] [bright_green]Telegram + Terminal[/bright_green]"
            )
            console.rule()
            time.sleep(0.5)
        except Exception:
            # Fallback for minimal terminals
            print("\n=== Prop Firm Signal Bot === Signal-Only | TwelveData | Telegram\n")
