from gi.repository import Gtk, GObject, Gdk
from ignis.widgets import Window
from ..utils.template import gtk_template, gtk_template_child, gtk_template_callback
from ..utils.widget import connect_window
import os
import shutil
import re
import datetime
import threading
import json

try:
    import pyatspi
    import requests
    HAS_MAGIC = True
except ImportError:
    HAS_MAGIC = False

from ..constants import WindowName
from ignis.services.niri import NiriService

@gtk_template(filename="titlesetter")
class TitleSetterView(Gtk.Box):
    __gtype_name__ = "TitleSetterView"

    title_entry: Gtk.Entry = gtk_template_child()
    status_label: Gtk.Label = gtk_template_child()
    save_button: Gtk.Button = gtk_template_child()
    magic_button: Gtk.Button = gtk_template_child()
    window_details_label: Gtk.Label = gtk_template_child()

    def __init__(self):
        super().__init__()

    @gtk_template_callback
    def on_save_button_clicked(self, *_):
        parent_window = self.get_ancestor(TitleSetter)
        if isinstance(parent_window, TitleSetter):
            parent_window.handle_save_button_click()

    @gtk_template_callback
    def on_magic_button_clicked(self, *_):
        parent_window = self.get_ancestor(TitleSetter)
        if isinstance(parent_window, TitleSetter):
            parent_window.handle_magic_click()

class TitleSetter(Window):
    __gtype_name__ = "IgnisTitleSetter"

    def __init__(self):
        self.__view = TitleSetterView()
        super().__init__(
            namespace=WindowName.title_setter.value,
            layer="overlay",
            popup=False,
            visible=False,
            kb_mode="exclusive"
        )
        self.add_css_class("rounded")
        self.set_child(self.__view)
        
        self._niri_service = NiriService.get_default()
        self._last_valid_window = None

        # Track window changes constantly
        if self._niri_service.is_available:
            self._niri_service.connect("notify::active-window", self._on_active_window_changed)

        connect_window(self, "notify::visible", self._on_visibility_changed)
        self.__view.title_entry.connect("activate", self.handle_save_button_click)

    def _on_active_window_changed(self, *args):
        window = self._niri_service.active_window
        # IMPORTANT: Only save focus if the new window is NOT this tool
        if window and window.app_id != WindowName.title_setter.value:
            self._last_valid_window = window

    def _on_visibility_changed(self, window: Window, _):
        if window.get_visible():
            self.__view.title_entry.set_text("")
            self.__view.status_label.set_text("")
            
            # Use the saved window from before the tool opened
            target = self._get_target_window_details()
            if target:
                app_id = target.get("app_id") or "Unknown App"
                title = target.get("title") or "Unknown Title"
                self.__view.window_details_label.set_text(f"Target App: {app_id}\nTitle: {title}")
                
                if title and title != "Unknown Title":
                    self.__view.title_entry.set_text(title)
                
                self.__view.title_entry.grab_focus()
            else:
                self.__view.window_details_label.set_text("No target window found.\nFocus a window, then press Mod+S.")

    def handle_magic_click(self, *_):
        if not HAS_MAGIC: return
        target = self._get_target_window_details()
        if not target:
            self.__view.status_label.set_text("No window context.")
            return

        self.__view.status_label.set_text("Scanning browser...")
        # Use both app_id and title to find the right Edge window
        threading.Thread(target=self._magic_worker, args=(target,), daemon=True).start()

    def _magic_worker(self, target):
        tabs = self._scan_browser_tabs(target)
        if not tabs:
            GObject.idle_add(lambda: self.__view.status_label.set_text("No tabs found in Edge."))
            return
        
        GObject.idle_add(lambda: self.__view.status_label.set_text("Asking AI (Alpaca)..."))
        name = self._query_llm(tabs)
        
        if name:
            GObject.idle_add(lambda: self._apply_magic_name(name))
        else:
            GObject.idle_add(lambda: self.__view.status_label.set_text("AI Error (Check Alpaca port 11435)"))

    def _apply_magic_name(self, name):
        self.__view.title_entry.set_text(name)
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(name)
        self.__view.status_label.set_text(f"✨ '{name}' copied to clipboard!")

    def _scan_browser_tabs(self, target):
        try:
            reg = pyatspi.Registry
            desktop = reg.getDesktop(0)
            found_tabs = []
            
            # Edge-specific matching logic
            app_id = (target.get("app_id") or "").lower()
            title = (target.get("title") or "").lower()
            
            for app in desktop:
                if not app or not app.name: continue
                app_name = app.name.lower()
                
                # Check if this app looks like Microsoft Edge
                if "edge" in app_name or "edge" in app_id:
                    self._recursive_tab_search(app, found_tabs)
                    if found_tabs: break
            return list(set(found_tabs))
        except: return []

    def _recursive_tab_search(self, accessible, collector):
        try:
            if accessible.getRole() == pyatspi.ROLE_PAGE_TAB:
                if accessible.name and accessible.name != "New Tab":
                    collector.append(accessible.name)
                return
            for i in range(min(accessible.childCount, 60)):
                self._recursive_tab_search(accessible.getChildAtIndex(i), collector)
        except: pass

    def _query_llm(self, tabs):
        try:
            prompt = f"Tabs: {', '.join(tabs[:12])}. Suggest a 1-2 word kebab-case name. Output ONLY the name."
            # Port fixed to 11435 per Alpaca settings
            url = "http://localhost:11435/v1/chat/completions" 
            payload = {
                "model": "llama3.2:latest", 
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1
            }
            res = requests.post(url, json=payload, timeout=5)
            if res.status_code == 200:
                content = res.json()['choices'][0]['message']['content'].strip().lower()
                return re.sub(r'[^a-z0-9\-]', '', content.replace(" ", "-"))
        except: return None

    def _get_target_window_details(self):
        # We exclusively trust _last_valid_window because when the tool 
        # is visible, it IS the active window.
        if not self._last_valid_window:
            return None
        return {
            "app_id": self._last_valid_window.app_id,
            "title": self._last_valid_window.title
        }

    def handle_save_button_click(self, *_):
        new_title = self.__view.title_entry.get_text().strip()
        target = self._get_target_window_details()
        ws = self._get_active_workspace_name()
        
        if not new_title or not target or not ws:
            self.__view.status_label.set_text("Error: Focus a window first.")
            return

        if self._write_niri_rule(new_title, target, ws):
            self.__view.status_label.set_text(f"Saved to '{ws}'!")
            GObject.timeout_add_seconds(1.5, self.set_visible, False)

    def _get_active_workspace_name(self):
        items = list(self._niri_service.workspaces.values()) if isinstance(self._niri_service.workspaces, dict) else self._niri_service.workspaces
        for ws in items:
            if getattr(ws, "is_focused", False) or (isinstance(ws, dict) and ws.get("is_focused")):
                if hasattr(ws, "name") and ws.name: return ws.name
                return str(getattr(ws, "id", ""))
        return None

    def _write_niri_rule(self, new_title, win, ws):
        path = os.path.expanduser("~/.config/niri/window-rules.kdl")
        # Handle cases where app-id is missing
        match = f'app-id="^{re.escape(win["app_id"])}$" title="^{re.escape(new_title)}$"' if win["app_id"] else f'title="^{re.escape(new_title)}$"'
        rule = f'\nwindow-rule {{\n    match {match}\n    open-on-workspace "{ws}"\n    open-maximized true\n    open-focused false\n}}\n'
        try:
            with open(path, "a") as f: f.write(rule)
            return True
        except: return False