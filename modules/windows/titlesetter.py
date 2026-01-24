from gi.repository import Gtk, Gdk, GLib
from loguru import logger
from ..widgets import RevealerWindow
from ..utils.template import gtk_template, gtk_template_child, gtk_template_callback
from ..utils.widget import connect_window
import os, re, threading, requests, datetime

try:
    import pyatspi
    HAS_MAGIC = True
except:
    HAS_MAGIC = False

from ..constants import WindowName
from ignis.services.niri import NiriService

@gtk_template(filename="titlesetter")
class TitleSetterView(Gtk.Box):
    __gtype_name__ = "TitleSetterView"
    revealer: Gtk.Revealer = gtk_template_child()
    title_entry: Gtk.Entry = gtk_template_child()
    status_label: Gtk.Label = gtk_template_child()
    window_details_label: Gtk.Label = gtk_template_child()
    magic_button: Gtk.Button = gtk_template_child()
    save_button: Gtk.Button = gtk_template_child()

    @gtk_template_callback
    def on_save_button_clicked(self, *_):
        self.get_ancestor(TitleSetter).handle_save_button_click()

    @gtk_template_callback
    def on_magic_button_clicked(self, *_):
        self.get_ancestor(TitleSetter).handle_magic_click()

class TitleSetter(RevealerWindow):
    def __init__(self):
        view = TitleSetterView()
        super().__init__(
            namespace=WindowName.title_setter.value, 
            layer="overlay", 
            kb_mode="exclusive",
            popup=True,
            visible=False, # FORCE HIDDEN ON STARTUP
            revealer=view.revealer
        )
        self.set_child(view)
        self.add_css_class("rounded")
        
        # Close on Escape
        shortcut = Gtk.Shortcut.new(
            trigger=Gtk.ShortcutTrigger.parse_string("Escape"),
            action=Gtk.CallbackAction.new(lambda *_: self.set_visible(False) or True)
        )
        self.add_shortcut(shortcut)

        self._niri = NiriService.get_default()
        self._last_win = None
        self._last_title = ""
        self._is_generating = False
        self._generation_id = 0
        
        # Connect signals
        self._niri.connect("notify::active-window", self._on_focus)
        connect_window(self, "notify::visible", self._on_show)
        
        # Input validation
        self.child.title_entry.connect("notify::text", self._validate_input)
        self._validate_input()

    def _on_focus(self, *args):
        win = self._niri.active_window
        # Ignore empty focus and the tool itself
        if win and win.app_id and win.app_id != WindowName.title_setter.value:
            self._last_win = win
            self._last_title = win.title

    def _validate_input(self, *args):
        text = self.child.title_entry.get_text().strip()
        is_valid = bool(text) and not self._is_generating
        self.child.save_button.set_sensitive(is_valid)

    def _set_busy(self, busy):
        self._is_generating = busy
        self._validate_input() # Updates save_button sensitivity
        self.child.title_entry.set_sensitive(not busy)
        
        if busy:
            self.child.magic_button.set_label("🛑")
            self.child.magic_button.set_tooltip_text("Cancel")
            self.child.status_label.set_text("🤖 AI is thinking...")
        else:
            self.child.magic_button.set_label("✨")
            self.child.magic_button.set_tooltip_text("AI Name Suggestion")

    def _on_show(self, *_):
        if self.get_visible():
            if self._last_win:
                self.child.window_details_label.set_text(f"Target: {self._last_win.app_id}")
                self.child.title_entry.set_text(self._last_title)
                self.child.title_entry.grab_focus()
            
            self._validate_input()
            
            # Disable magic button if dependencies are missing
            self.child.magic_button.set_sensitive(HAS_MAGIC)
            if not HAS_MAGIC:
                self.child.magic_button.set_tooltip_text("Missing 'pyatspi' dependency")

    def handle_magic_click(self):
        if self._is_generating:
            self._generation_id += 1
            self._set_busy(False)
            self.child.status_label.set_text("🚫 Cancelled")
            return

        win = self._last_win
        if not win:
            self.child.status_label.set_text("No window focused")
            return
            
        self._set_busy(True)
        self._generation_id += 1
        target_info = {"app_id": win.app_id, "title": self._last_title}
        # Running in thread prevents UI hanging
        threading.Thread(target=self._magic_worker, args=(target_info, self._generation_id), daemon=True).start()

    def _magic_worker(self, target_info, gen_id):
        if gen_id != self._generation_id: return
        tabs = self._scan_tabs(target_info)
        
        if gen_id != self._generation_id: return
        if not tabs:
            GLib.idle_add(self._finish_magic, "❌ No tabs found")
            return
        
        name = self._ask_alpaca(tabs)
        
        if gen_id != self._generation_id: return
        if name:
            GLib.idle_add(self._apply_name, name)
        else:
            GLib.idle_add(self._finish_magic, "❌ AI Offline (Ollama/Alpaca)")

    def _scan_tabs(self, target):
        try:
            reg = pyatspi.Registry
            found_tabs = []
            
            app_id = (target.get("app_id") or "").lower()
            win_title = (target.get("title") or "")
            
            for app in reg.getDesktop(0):
                if not app or not app.name: continue
                
                if any(x in app.name.lower() or x in app_id.lower() for x in ["edge", "firefox", "chromium"]):
                    # NEW: Find the specific window frame inside the app
                    target_frame = self._find_window_frame(app, win_title)
                    
                    if target_frame:
                        self._recursive_search(target_frame, found_tabs)
                    break
            
            # Filter and deduplicate
            unique_tabs = sorted(list(set([t for t in found_tabs if len(t) > 3])))
            return unique_tabs[:15] 
        except Exception as e:
            logger.warning(f"Scanner Error: {e}")
            return []

    def _find_window_frame(self, app, win_title):
        """Looks for a ROLE_FRAME that matches the Niri window title."""
        if not win_title: return None
        
        for i in range(app.childCount):
            child = app.getChildAtIndex(i)
            if not child: continue
            
            # In Linux, browser windows are usually ROLE_FRAME
            if child.getRole() == pyatspi.ROLE_FRAME:
                # We check if the Niri title is contained in the accessibility name
                if win_title in child.name or child.name in win_title:
                    return child
        return None

    def _recursive_search(self, acc, collector):
        try:
            if acc.getRole() == pyatspi.ROLE_PAGE_TAB:
                if acc.name: collector.append(acc.name)
                return

            # Keep search depth reasonable to avoid 800+ tab leakage
            for i in range(min(acc.childCount, 100)):
                child = acc.getChildAtIndex(i)
                if child:
                    self._recursive_search(child, collector)
        except: pass

    def _ask_alpaca(self, tabs):
        try:
            url = "http://localhost:11435/v1/chat/completions"
            prompt = (
                f"You are a workspace manager. I have these browser tabs open: {', '.join(tabs)}. "
                "Suggest a short, 1-2 word kebab-case name for this workspace. "
                "Output ONLY the name. Do not explain. Do not use quotes. Output exactly one word."
            )
            payload = {
                "model": "llama3.2:latest",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1, # Keep it predictable
            }
            # Set a 10-second timeout to prevent permanent hanging
            res = requests.post(url, json=payload, timeout=10)
            if res.status_code == 200:
                raw_name = res.json()['choices'][0]['message']['content'].strip().lower()
                # Final cleanup: remove trailing dots or markdown
                return re.sub(r'[^a-z0-9\-]', '', raw_name.split('\n')[0])
        except Exception as e:
            logger.error(f"LLM Error: {e}")
            return None

    def _finish_magic(self, status_text):
        self._set_busy(False)
        self.child.status_label.set_text(status_text)

    def _apply_name(self, name):
        self._set_busy(False)
        self.child.title_entry.set_text(name)
        self.child.status_label.set_text(f"✨ Copied '{name}' to clipboard")
        
        try:
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.set(name)
        except Exception as e:
            logger.error(f"Clipboard Error: {e}")

    def handle_save_button_click(self):
        new_title = self.child.title_entry.get_text().strip()
        win = self._last_win
        ws = self._get_ws()
        if not (new_title and win and ws): return
        
        path = os.path.expanduser("~/.config/niri/window-rules.kdl")
        
        # Check for duplicates before appending
        if os.path.exists(path):
            with open(path, "r") as f:
                content = f.read()
            # Simple check to see if this specific match rule already exists
            match_str = f'match app-id="^{re.escape(win.app_id)}$" title="^{re.escape(new_title)}$"'
            if match_str in content:
                self.child.status_label.set_text("⚠️ Rule already exists")
                return

        # Ensure we don't duplicate rules if you click multiple times
        rule = (
            f'\n// Session: {datetime.date.today()}\n'
            f'window-rule {{\n'
            f'    match app-id="^{re.escape(win.app_id)}$" title="^{re.escape(new_title)}$"\n'
            f'    open-on-workspace "{ws}"\n'
            f'    open-maximized true\n'
            f'}}\n'
        )
        with open(path, "a") as f: f.write(rule)
        self.set_visible(False)

    def _get_ws(self):
        for ws in self._niri.workspaces:
            if ws.is_active: return ws.name or str(ws.id)
        return None