# #
from __future__ import annotations
import curses, os, locale; import Mintray_Core as core; from Mintray_Core import App
class Main:
    # Classes
    class Version:
        ManageVersion = 7
        Version = 1.2
        SubVersion = 1
        SubComment = ''
        BuildType = 'Stable'  # Could be: Unstable (a default release, but may contain major/small bugs), Stable, Alpha (early versions, mostly very unstable or contains unfinished parts)
        __build_type_show__ = {'Alpha': 'ALPH', 'Stable': 'STBL', 'Unstable': 'BETA'}[BuildType]
        BuildShow = f'{ManageVersion}{__build_type_show__}-{SubVersion}{SubComment}'
    class Activities:
        @classmethod
        def SanitizeForCurses(cls, s: str) -> str:
            cleaned = " ".join(s.encode("ascii", errors="ignore").decode("ascii").strip().split())
            return cleaned or "(unnamed)"
        @classmethod
        def SafeAddStr(cls, stdscr: "curses._CursesWindow", y: int, x: int, s: str, attr: int = 0) -> None:
            try: stdscr.addstr(y, x, s, attr) if attr else stdscr.addstr(y, x, s)
            except curses.error: pass
        @classmethod
        def InitColors(cls) -> None:
            global HAS_COLOR
            if not curses.has_colors(): return
            curses.start_color()
            try: curses.use_default_colors(); bg = -1
            except curses.error: bg = curses.COLOR_BLACK
            curses.init_pair(COLOR_ACCENT, curses.COLOR_GREEN, bg); curses.init_pair(COLOR_GOOD, curses.COLOR_GREEN, bg)
            curses.init_pair(COLOR_WARN, curses.COLOR_YELLOW, bg); curses.init_pair(COLOR_BAD, curses.COLOR_RED, bg); curses.init_pair(COLOR_GREAT, curses.COLOR_BLUE, bg)
            HAS_COLOR = True
        @classmethod
        def Pair(cls, n: int) -> int: return curses.color_pair(n) if HAS_COLOR else 0
        @classmethod
        def PingColor(cls, ms: float | None) -> int:
            return curses.A_DIM if ms is None else cls.Pair(COLOR_GREAT) if ms < 100 else cls.Pair(COLOR_GOOD) if ms < 200 else cls.Pair(COLOR_WARN) if ms <= 500 else cls.Pair(COLOR_BAD)
        @classmethod
        def DrawSegments(cls, stdscr: "curses._CursesWindow", y: int, x: int, segments: list[tuple[str, int]]) -> None:
            cur_x = x
            for text, attr in segments: cls.SafeAddStr(stdscr, y, cur_x, text, attr); cur_x += len(text)
        @classmethod
        def DrawDivider(cls, stdscr: "curses._CursesWindow", y: int, w: int) -> None: cls.SafeAddStr(stdscr, y, 0, "─" * max(w - 1, 0), curses.A_DIM)
        @classmethod
        def DrawSettings(cls, stdscr, w, h, fields, selected, editing, edit_buffer, app):
            cls.SafeAddStr(stdscr, 0, 0, " settings", cls.Pair(COLOR_ACCENT) | curses.A_BOLD)
            cls.DrawDivider(stdscr, 1, w)
            cls.SafeAddStr(stdscr, 3, 0, "enter to edit a field (starts blank) \u00b7 enter again to save \u00b7 esc to cancel", curses.A_DIM)
            y = 5
            for i, field in enumerate(fields):
                label = {"xray_bin": "xray binary path", "tun2socks_bin": "tun2socks binary path", "hysteria_bin": "hysteria2 binary path", "ping_url": "ping url", "user_agent": "user agent"}[field]; is_selected = i == selected; marker = "› " if is_selected else "  "; cls.SafeAddStr(stdscr, y, 0, f"{marker}{label}", (curses.A_BOLD if is_selected else curses.A_DIM))
                if is_selected and editing: cls.SafeAddStr(stdscr, y + 1, 4, (edit_buffer + "\u2588")[: max(w - 5, 0)], cls.Pair(COLOR_ACCENT)); shown_value = edit_buffer
                else: shown_value = str(getattr(app.args, field, core.SETTINGS_DEFAULTS[field])); cls.SafeAddStr(stdscr, y + 1, 4, shown_value[: max(w - 5, 0)], curses.A_DIM)
                y += 2
                if field == "ping_url" and shown_value == core.SETTINGS_DEFAULTS["ping_url"]: cls.SafeAddStr(stdscr, y, 4, "tip: we recommend keeping default internet check provider, GrapheneOS respects privacy, Google doesn't"[: max(w - 5, 0)], curses.A_DIM); y += 1
                y += 1
            footer_y = h - 2
            cls.DrawDivider(stdscr, footer_y - 1, w)
            cls.SafeAddStr(stdscr, footer_y, 0, "\u2191\u2193 select field   \u23ce edit/save   esc back to servers", curses.A_DIM)
        @classmethod
        def RunTui(cls, stdscr: "curses._CursesWindow", app: App) -> None:
            curses.curs_set(0); stdscr.nodelay(True); stdscr.timeout(500); cls.InitColors()
            selected = 0; scroll_offset = 0; mode = "main"; settings_selected = 0; editing = False; edit_buffer = ""
            while True:
                stdscr.erase(); h, w = stdscr.getmaxyx()
                if mode == "settings":
                    cls.DrawSettings(stdscr, w, h, core.gc.SettingsFields, settings_selected, editing, edit_buffer, app)
                    stdscr.refresh()
                    try: key = stdscr.getch()
                    except curses.error: key = -1
                    if editing:
                        if key in (10, 13):
                            field = core.gc.SettingsFields[settings_selected]
                            if edit_buffer.strip(): core.Main.Activities.SaveSettings({field: edit_buffer}); setattr(app.args, field, edit_buffer); app.status_msg = f"{ {"xray_bin": "xray binary path", "tun2socks_bin": "tun2socks binary path", "hysteria_bin": "hysteria2 binary path", "ping_url": "ping url", "user_agent": "user agent"}[field]} saved"
                            editing = False
                        elif key == 27: editing = False
                        elif key in (curses.KEY_BACKSPACE, 127, 8): edit_buffer = edit_buffer[:-1]
                        elif 32 <= key < 127: edit_buffer += chr(key)
                    else:
                        if key in (curses.KEY_UP,) and settings_selected > 0: settings_selected -= 1
                        elif key in (curses.KEY_DOWN,) and settings_selected < len(core.gc.SettingsFields) - 1: settings_selected += 1
                        elif key in (10, 13): field = core.gc.SettingsFields[settings_selected]; edit_buffer = ""; editing = True
                        elif key == 27 or key in (ord("s"), ord("S")): mode = "main"
                    continue
                cls.SafeAddStr(stdscr, 0, 0, " mintray", cls.Pair(COLOR_ACCENT) | curses.A_BOLD)
                subtitle = Main.Version.BuildShow; cls.SafeAddStr(stdscr, 0, max(w - len(subtitle) - 1, 10), subtitle, curses.A_DIM)
                cls.DrawDivider(stdscr, 1, w)
                xray_ok, t2s_ok = app.proc.Alive()
                state_color = cls.Pair(COLOR_GOOD) if app.connected else cls.Pair(COLOR_BAD)
                xray_color = cls.Pair(COLOR_GOOD) if xray_ok else cls.Pair(COLOR_BAD); t2s_color = cls.Pair(COLOR_GOOD) if t2s_ok else cls.Pair(COLOR_BAD)
                cls.DrawSegments(stdscr, 2, 0, [("● ", state_color | curses.A_BOLD), ("connected" if app.connected else "disconnected", state_color | curses.A_BOLD), (f"    {app.proc.upstream_label} ", curses.A_DIM), ("●", xray_color), (" up" if xray_ok else " down", xray_color), ("    tun2socks ", curses.A_DIM), ("●", t2s_color), (" up" if t2s_ok else " down", t2s_color), ])
                if app.connected:
                    line3 = f"uptime {core.Main.Activities.FormatDuration(app.proc.Uptime())}    device {app.net.tun_device}"
                    cls.SafeAddStr(stdscr, 3, 0, line3, curses.A_DIM)
                    if app.speed_up_bps or app.speed_down_bps:
                        cls.DrawSegments(stdscr, 3, len(line3) + 4, [("↑ ", cls.Pair(COLOR_ACCENT)), (core.Main.Activities.FormatSpeed(app.speed_up_bps), cls.Pair(COLOR_ACCENT)), ("   ↓ ", cls.Pair(COLOR_ACCENT)), (core.Main.Activities.FormatSpeed(app.speed_down_bps), cls.Pair(COLOR_ACCENT)), ])
                cls.SafeAddStr(stdscr, 4, 0, cls.SanitizeForCurses(app.status_msg)[: max(w - 1, 0)], curses.A_DIM)
                cls.SafeAddStr(stdscr, 6, 0, "servers", cls.Pair(COLOR_ACCENT) | curses.A_BOLD)
                header_rows, footer_rows = 8, 3; list_top = header_rows; footer_divider_y = h - footer_rows; scroll_info_y = footer_divider_y - 1
                visible_rows = max(scroll_info_y - list_top, 1)
                if selected < scroll_offset: scroll_offset = selected
                elif selected >= scroll_offset + visible_rows: scroll_offset = selected - visible_rows + 1
                scroll_offset = max(0, min(scroll_offset, max(len(app.servers) - visible_rows, 0)))
                if len(app.servers) > visible_rows:
                    shown_end = min(scroll_offset + visible_rows, len(app.servers))
                    info = f"[{scroll_offset + 1}-{shown_end} of {len(app.servers)}]"; cls.SafeAddStr(stdscr, 6, max(w - len(info) - 1, 8), info, curses.A_DIM)
                cls.DrawDivider(stdscr, 7, w)
                marker_w, ping_w, badge_w = 2, 9, 9; name_w = max(w - marker_w - ping_w - badge_w - 1, 10)
                for row_i, srv in enumerate(app.servers[scroll_offset: scroll_offset + visible_rows]):
                    i = scroll_offset + row_i; y = list_top + row_i; is_selected = i == selected; is_active = srv is app.current_server; base = curses.A_REVERSE if is_selected else 0
                    if is_selected: cls.SafeAddStr(stdscr, y, 0, " " * max(w - 1, 0), curses.A_REVERSE)
                    name = cls.SanitizeForCurses(srv.get("tag", f"server-{i}"))
                    name = (name[: max(name_w - 1, 0)] + "…" if len(name) > name_w else name).ljust(name_w)
                    badge = "active".rjust(badge_w) if is_active else "".rjust(badge_w)
                    badge_attr = base | (0 if is_selected else (cls.Pair(COLOR_ACCENT) if is_active else curses.A_DIM))
                    if i in app.pings:
                        val = app.pings[i]; ping_str = (f"{val:.0f}ms" if val is not None else "n/a").rjust(ping_w); ping_attr = base if is_selected else cls.PingColor(val)
                    else: ping_str = "".rjust(ping_w); ping_attr = base
                    cls.SafeAddStr(stdscr, y, 0, "› " if is_selected else "  ", base | cls.Pair(COLOR_ACCENT))
                    cls.SafeAddStr(stdscr, y, marker_w, name, base); cls.SafeAddStr(stdscr, y, marker_w + name_w, badge, badge_attr); cls.SafeAddStr(stdscr, y, marker_w + name_w + badge_w, ping_str, ping_attr)
                cls.DrawDivider(stdscr, footer_divider_y, w)
                cls.SafeAddStr(stdscr, footer_divider_y + 1, 0, "↑↓ move   pgup/pgdn page   ⏎ switch   c connect   d disconnect", curses.A_DIM)
                cls.SafeAddStr(stdscr, footer_divider_y + 2, 0, "p ping    o sort   s settings   r refresh  q quit", curses.A_DIM)
                stdscr.refresh()
                try: key = stdscr.getch()
                except curses.error: key = -1
                if key in (curses.KEY_UP,) and selected > 0: selected -= 1
                elif key in (curses.KEY_DOWN,) and selected < len(app.servers) - 1: selected += 1
                elif key == curses.KEY_NPAGE: selected = min(selected + visible_rows, max(len(app.servers) - 1, 0))
                elif key == curses.KEY_PPAGE: selected = max(selected - visible_rows, 0)
                elif key in (10, 13): app.SwitchServer(selected)
                elif key in (ord("c"), ord("C")):
                    try: app.Connect()
                    except Exception as e: app.status_msg = f"connect failed: {e}"; core.Main.Activities.Log(app.status_msg)
                elif key in (ord("d"), ord("D")): app.Disconnect()
                elif key in (ord("r"), ord("R")): app.RefreshSubscription()
                elif key in (ord("p"), ord("P")): app.PingAll()
                elif key in (ord("o"), ord("O")): app.SortByPing()
                elif key in (ord("s"), ord("S")): mode = "settings"; settings_selected = 0; editing = False
                elif key in (ord("q"), ord("Q")): app.Disconnect(); break
        @classmethod
        def main(cls) -> None:
            args = core.Main.Activities.ParseArgs(); core.Main.Activities.ApplySettings(args); core.Main.Activities.ResolveBinaryDefaults(args)
            if core.Main.Activities.HandleCliCommands(args): return
            app = core.Main.Activities.StartApp(args)
            locale.setlocale(locale.LC_ALL, ""); os.environ.setdefault("ESCDELAY", "25")
            curses.wrapper(cls.RunTui, app)
# Init
COLOR_ACCENT, COLOR_GOOD, COLOR_WARN, COLOR_BAD, COLOR_GREAT = 1, 2, 3, 4, 5
HAS_COLOR = False
# Main
if __name__ == "__main__": Main.Activities.main()