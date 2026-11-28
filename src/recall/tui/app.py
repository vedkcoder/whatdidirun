"""Textual picker: type a description, pick a command, it lands on your prompt."""

from __future__ import annotations

import sqlite3

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from ..config import Config
from ..fmt import ago, short_path
from ..search import Result, Searcher


class RecallApp(App[str]):
    CSS = """
    Screen { background: $surface; }
    #query { border: tall $accent; margin: 0 1; }
    #status { color: $text-muted; padding: 0 2; height: 1; }
    #results { margin: 0 1; border: none; height: 1fr; }
    #explain { display: none; margin: 0 1; padding: 0 1; border: round $secondary; height: auto; max-height: 12; }
    #explain.show { display: block; }
    """
    BINDINGS = [
        Binding("escape", "quit_empty", "Cancel"),
        Binding("enter", "choose", "Use command", priority=True),
        Binding("down", "move(1)", "Down", show=False, priority=True),
        Binding("up", "move(-1)", "Up", show=False, priority=True),
        Binding("ctrl+s", "toggle_smart", "Smart (LLM)", priority=True),
        Binding("ctrl+t", "explain", "Explain", priority=True),  # ctrl+e is end-of-line in Input
    ]

    def __init__(self, cfg: Config, conn: sqlite3.Connection, query: str = ""):
        super().__init__()
        self.searcher = Searcher(cfg, conn)
        self.initial = query
        self.smart = False
        self.results: list[Result] = []
        self._refreshed = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Input(value=self.initial, placeholder="Describe the command… e.g. 'resize images failed last week'", id="query")
            yield Static("", id="status")
            yield OptionList(id="results")
            yield Static("", id="explain")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        self.run_search(self.initial)
        self._warm()

    @work(thread=True, group="warm")
    def _warm(self) -> None:
        self.searcher.warm()
        # Re-run the current query now that semantic search is available.
        self.call_from_thread(lambda: self.run_search(self.query_one(Input).value, debounce=False))

    @on(Input.Changed)
    def _changed(self, event: Input.Changed) -> None:
        self.run_search(event.value)

    @work(exclusive=True, thread=True)
    def run_search(self, q: str, debounce: bool = True) -> None:
        import time

        if debounce:
            time.sleep(0.2)  # exclusive worker: a newer keystroke cancels this one
        if self._workers_cancelled():
            return
        # Pull new commands from the spool once the vector index is up (so they get embedded too).
        refresh = not self._refreshed and self.searcher.vectors_ready
        self._refreshed = self._refreshed or refresh
        resp = self.searcher.search(q, limit=40, smart=self.smart and bool(q.strip()), refresh=refresh, wait_vectors=False)
        self.call_from_thread(self._show, resp)

    def _workers_cancelled(self) -> bool:
        from textual.worker import get_current_worker

        return get_current_worker().is_cancelled

    def _show(self, resp) -> None:
        self.results = resp.results
        ol = self.query_one(OptionList)
        ol.clear_options()
        for r in resp.results:
            t = Text()
            t.append("✗ " if r.last_exit not in (0, None) else "  ", style="red")
            t.append(r.cmd.replace("\n", " ⏎ "), style="bold")
            meta = " · ".join(x for x in [ago(r.last_ts), short_path(r.last_cwd) or (r.git_repo or ""), f"{r.runs}×" if r.runs > 1 else ""] if x)
            t.append(f"   {meta}", style="dim")
            ol.add_option(Option(t))
        if resp.results:
            ol.highlighted = 0
        bits = [resp.mode + (" + llm" if self.smart else "") + ("" if self.searcher.vectors_ready else " (loading semantic index…)")]
        if resp.notes:
            bits.append("filters: " + ", ".join(resp.notes))
        bits += resp.warnings[:1]
        self.query_one("#status", Static).update(f"{len(resp.results)} results · " + " · ".join(bits))
        self.query_one("#explain").remove_class("show")

    def _current(self) -> Result | None:
        idx = self.query_one(OptionList).highlighted
        return self.results[idx] if idx is not None and idx < len(self.results) else None

    def action_move(self, delta: int) -> None:
        ol = self.query_one(OptionList)
        if ol.option_count:
            ol.highlighted = max(0, min(ol.option_count - 1, (ol.highlighted or 0) + delta))

    def action_choose(self) -> None:
        r = self._current()
        self.exit(r.cmd if r else None)

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self.exit(self.results[event.option_index].cmd)

    def action_quit_empty(self) -> None:
        self.exit(None)

    def action_toggle_smart(self) -> None:
        self.smart = not self.smart
        self.notify(f"Smart mode {'on' if self.smart else 'off'}")
        self.run_search(self.query_one(Input).value)

    def action_explain(self) -> None:
        if r := self._current():
            box = self.query_one("#explain", Static)
            box.update("Explaining…")
            box.add_class("show")
            self._explain(r.cmd)

    @work(thread=True)
    def _explain(self, cmd: str) -> None:
        from ..llm import LLMError

        try:
            text = self.searcher.explain(cmd)
        except LLMError as e:
            text = str(e)
        self.call_from_thread(self.query_one("#explain", Static).update, text)


def run(cfg: Config, conn: sqlite3.Connection, query: str = "") -> str | None:
    return RecallApp(cfg, conn, query).run()
