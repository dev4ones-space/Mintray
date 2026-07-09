#!/usr/bin/env python3
"""
Mintray_TUI.py - curses terminal renderer for MintRay.

Presentation only. All backend logic (Xray config, subscriptions, ping
testing, macOS route/DNS handling, the App state machine) lives in
Mintray_Core.py, imported unchanged.

Run: sudo -E python3 Mintray_TUI.py [--subscription-url ... | other flags]
"""
from __future__ import annotations
import curses
import os
import locale

import Mintray_Core as core
from Mintray_Core import App

def SanitizeForCurses(s: str) -> str:
    """Emoji/CJK column-width is unreliable across terminals - ncurses'
    addwstr() will hard-error (not just misrender) if a line's computed
    width overflows the window. Keep TUI display strings plain ASCII;
    the original unicode is still used for the actual xray outbound tag."""
    cleaned = s.encode("ascii", errors="ignore").decode("ascii").strip()
    cleaned = " ".join(cleaned.split())  # collapse leftover double-spaces from stripped emoji
    return cleaned or "(unnamed)"


def SafeAddStr(stdscr: "curses._CursesWindow", y: int, x: int, s: str, attr: int = 0) -> None:
    """addstr wrapped so a too-small terminal or edge-of-window write can't
    take down the whole TUI - worst case that one line just doesn't draw."""
    try:
        if attr:
            stdscr.addstr(y, x, s, attr)
        else:
            stdscr.addstr(y, x, s)
    except curses.error:
        pass


COLOR_ACCENT = 1  # mint green - branding/headers/active items
COLOR_GOOD = 2    # green - connected, low ping
COLOR_WARN = 3    # yellow - medium ping
COLOR_BAD = 4     # red - disconnected, high ping, timeout
HAS_COLOR = False


def InitColors() -> None:
    global HAS_COLOR
    if not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()  # -1 = terminal's own background, not forced black
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    curses.init_pair(COLOR_ACCENT, curses.COLOR_GREEN, bg)
    curses.init_pair(COLOR_GOOD, curses.COLOR_GREEN, bg)
    curses.init_pair(COLOR_WARN, curses.COLOR_YELLOW, bg)
    curses.init_pair(COLOR_BAD, curses.COLOR_RED, bg)
    HAS_COLOR = True


def Pair(n: int) -> int:
    return curses.color_pair(n) if HAS_COLOR else 0


def PingColor(ms: float | None) -> int:
    if ms is None:
        return Pair(COLOR_BAD)
    if ms < 150:
        return Pair(COLOR_GOOD)
    if ms < 300:
        return Pair(COLOR_WARN)
    return Pair(COLOR_BAD)


def DrawSegments(stdscr: "curses._CursesWindow", y: int, x: int, segments: list[tuple[str, int]]) -> None:
    """Draw several differently-colored/attributed text runs on one row."""
    cur_x = x
    for text, attr in segments:
        SafeAddStr(stdscr, y, cur_x, text, attr)
        cur_x += len(text)


def DrawDivider(stdscr: "curses._CursesWindow", y: int, w: int) -> None:
    SafeAddStr(stdscr, y, 0, "─" * max(w - 1, 0), curses.A_DIM)


def DrawSettings(stdscr, w, h, fields, selected, editing, edit_buffer, app):
    SafeAddStr(stdscr, 0, 0, " settings", Pair(COLOR_ACCENT) | curses.A_BOLD)
    DrawDivider(stdscr, 1, w)
    SafeAddStr(stdscr, 3, 0, "enter to edit a field (starts blank) \u00b7 enter again to save \u00b7 esc to cancel", curses.A_DIM)

    y = 5
    for i, field in enumerate(fields):
        label = core.SETTINGS_LABELS[field]
        is_selected = i == selected
        marker = "› " if is_selected else "  "
        SafeAddStr(stdscr, y, 0, f"{marker}{label}", (curses.A_BOLD if is_selected else curses.A_DIM))
        if is_selected and editing:
            value_line = edit_buffer + "\u2588"
            SafeAddStr(stdscr, y + 1, 4, value_line[: max(w - 5, 0)], Pair(COLOR_ACCENT))
        else:
            current_val = str(getattr(app.args, field, core.SETTINGS_DEFAULTS[field]))
            SafeAddStr(stdscr, y + 1, 4, current_val[: max(w - 5, 0)], curses.A_DIM)
        y += 3

    footer_y = h - 2
    DrawDivider(stdscr, footer_y - 1, w)
    SafeAddStr(stdscr, footer_y, 0, "\u2191\u2193 select field   \u23ce edit/save   esc back to servers", curses.A_DIM)


def RunTui(stdscr: "curses._CursesWindow", app: App) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(500)
    InitColors()
    selected = 0
    scroll_offset = 0
    mode = "main"
    settings_selected = 0
    editing = False
    edit_buffer = ""

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        if mode == "settings":
            DrawSettings(stdscr, w, h, core.SETTINGS_FIELDS, settings_selected, editing, edit_buffer, app)
            stdscr.refresh()
            try:
                key = stdscr.getch()
            except curses.error:
                key = -1

            if editing:
                if key in (10, 13):  # enter - save
                    field = core.SETTINGS_FIELDS[settings_selected]
                    if edit_buffer.strip():
                        core.SaveSettings({field: edit_buffer})
                        setattr(app.args, field, edit_buffer)
                        app.status_msg = f"{core.SETTINGS_LABELS[field]} saved"
                    editing = False
                elif key == 27:  # esc - cancel
                    editing = False
                elif key in (curses.KEY_BACKSPACE, 127, 8):
                    edit_buffer = edit_buffer[:-1]
                elif 32 <= key < 127:
                    edit_buffer += chr(key)
            else:
                if key in (curses.KEY_UP,) and settings_selected > 0:
                    settings_selected -= 1
                elif key in (curses.KEY_DOWN,) and settings_selected < len(core.SETTINGS_FIELDS) - 1:
                    settings_selected += 1
                elif key in (10, 13):  # enter - start editing
                    field = core.SETTINGS_FIELDS[settings_selected]
                    edit_buffer = ""
                    editing = True
                elif key == 27 or key in (ord("s"), ord("S")):  # esc or s - back to servers
                    mode = "main"
            continue

        # --- header ---
        SafeAddStr(stdscr, 0, 0, " mintray", Pair(COLOR_ACCENT) | curses.A_BOLD)
        subtitle = f"v{core.MINTRAY_VERSION}"
        SafeAddStr(stdscr, 0, max(w - len(subtitle) - 1, 10), subtitle, curses.A_DIM)
        DrawDivider(stdscr, 1, w)

        # --- status ---
        xray_ok, t2s_ok = app.proc.Alive()
        state_color = Pair(COLOR_GOOD) if app.connected else Pair(COLOR_BAD)
        xray_color = Pair(COLOR_GOOD) if xray_ok else Pair(COLOR_BAD)
        t2s_color = Pair(COLOR_GOOD) if t2s_ok else Pair(COLOR_BAD)
        DrawSegments(stdscr, 2, 0, [
            ("● ", state_color | curses.A_BOLD),
            ("connected" if app.connected else "disconnected", state_color | curses.A_BOLD),
            ("    xray ", curses.A_DIM),
            ("●", xray_color), (" up" if xray_ok else " down", xray_color),
            ("    tun2socks ", curses.A_DIM),
            ("●", t2s_color), (" up" if t2s_ok else " down", t2s_color),
        ])
        if app.connected:
            line3 = f"uptime {int(app.proc.Uptime())}s    device {app.net.tun_device}"
            SafeAddStr(stdscr, 3, 0, line3, curses.A_DIM)
            if app.speed_up_bps or app.speed_down_bps:
                DrawSegments(stdscr, 3, len(line3) + 4, [
                    ("↑ ", Pair(COLOR_ACCENT)), (core.FormatSpeed(app.speed_up_bps), Pair(COLOR_ACCENT)),
                    ("   ↓ ", Pair(COLOR_ACCENT)), (core.FormatSpeed(app.speed_down_bps), Pair(COLOR_ACCENT)),
                ])
        SafeAddStr(stdscr, 4, 0, SanitizeForCurses(app.status_msg)[: max(w - 1, 0)], curses.A_DIM)

        # --- server list ---
        SafeAddStr(stdscr, 6, 0, "servers", Pair(COLOR_ACCENT) | curses.A_BOLD)

        header_rows, footer_rows = 8, 3
        list_top = header_rows
        footer_divider_y = h - footer_rows
        scroll_info_y = footer_divider_y - 1
        visible_rows = max(scroll_info_y - list_top, 1)

        if selected < scroll_offset:
            scroll_offset = selected
        elif selected >= scroll_offset + visible_rows:
            scroll_offset = selected - visible_rows + 1
        scroll_offset = max(0, min(scroll_offset, max(len(app.servers) - visible_rows, 0)))

        if len(app.servers) > visible_rows:
            shown_end = min(scroll_offset + visible_rows, len(app.servers))
            info = f"[{scroll_offset + 1}-{shown_end} of {len(app.servers)}]"
            SafeAddStr(stdscr, 6, max(w - len(info) - 1, 8), info, curses.A_DIM)

        DrawDivider(stdscr, 7, w)

        marker_w, ping_w, badge_w = 2, 9, 9
        name_w = max(w - marker_w - ping_w - badge_w - 1, 10)

        for row_i, srv in enumerate(app.servers[scroll_offset: scroll_offset + visible_rows]):
            i = scroll_offset + row_i
            y = list_top + row_i
            is_selected = i == selected
            is_active = srv is app.current_server
            base = curses.A_REVERSE if is_selected else 0

            if is_selected:
                SafeAddStr(stdscr, y, 0, " " * max(w - 1, 0), curses.A_REVERSE)

            name = SanitizeForCurses(srv.get("tag", f"server-{i}"))
            if len(name) > name_w:
                name = name[: max(name_w - 1, 0)] + "…"
            name = name.ljust(name_w)

            badge = "active".rjust(badge_w) if is_active else "".rjust(badge_w)
            badge_attr = base | (0 if is_selected else (Pair(COLOR_ACCENT) if is_active else curses.A_DIM))

            if i in app.pings:
                val = app.pings[i]
                ping_str = (f"{val:.0f}ms" if val is not None else "timeout").rjust(ping_w)
                ping_attr = base if is_selected else PingColor(val)
            else:
                ping_str = "".rjust(ping_w)
                ping_attr = base

            SafeAddStr(stdscr, y, 0, "› " if is_selected else "  ", base | Pair(COLOR_ACCENT))
            SafeAddStr(stdscr, y, marker_w, name, base)
            SafeAddStr(stdscr, y, marker_w + name_w, badge, badge_attr)
            SafeAddStr(stdscr, y, marker_w + name_w + badge_w, ping_str, ping_attr)

        # --- footer ---
        DrawDivider(stdscr, footer_divider_y, w)
        SafeAddStr(stdscr, footer_divider_y + 1, 0,
                   "↑↓ move   pgup/pgdn page   ⏎ switch   c connect   d disconnect", curses.A_DIM)
        SafeAddStr(stdscr, footer_divider_y + 2, 0,
                   "p ping    o sort   s settings   r refresh  q quit", curses.A_DIM)

        stdscr.refresh()

        try:
            key = stdscr.getch()
        except curses.error:
            key = -1

        if key in (curses.KEY_UP,) and selected > 0:
            selected -= 1
        elif key in (curses.KEY_DOWN,) and selected < len(app.servers) - 1:
            selected += 1
        elif key == curses.KEY_NPAGE:
            selected = min(selected + visible_rows, max(len(app.servers) - 1, 0))
        elif key == curses.KEY_PPAGE:
            selected = max(selected - visible_rows, 0)
        elif key in (10, 13):  # enter
            app.SwitchServer(selected)
        elif key in (ord("c"), ord("C")):
            try:
                app.Connect()
            except Exception as e:
                app.status_msg = f"connect failed: {e}"
                core.Log(app.status_msg)
        elif key in (ord("d"), ord("D")):
            app.Disconnect()
        elif key in (ord("r"), ord("R")):
            app.RefreshSubscription()
        elif key in (ord("p"), ord("P")):
            app.PingAll()  # non-blocking now - results stream into app.pings live as each one finishes
        elif key in (ord("o"), ord("O")):
            app.SortByPing()
        elif key in (ord("s"), ord("S")):
            mode = "settings"
            settings_selected = 0
            editing = False
        elif key in (ord("q"), ord("Q")):
            app.Disconnect()
            break


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------


def main() -> None:
    args = core.ParseArgs()
    core.ApplySettings(args)
    core.ResolveBinaryDefaults(args)
    if core.HandleCliCommands(args):
        return
    app = core.StartApp(args)
    locale.setlocale(locale.LC_ALL, "")  # must happen before curses starts, for unicode server names
    os.environ.setdefault("ESCDELAY", "25")  # default ~1000ms makes Esc (settings view) feel broken
    curses.wrapper(RunTui, app)


if __name__ == "__main__":
    main()
