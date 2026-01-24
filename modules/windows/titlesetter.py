from gi.repository import Gtk, Gdk, GLib
from loguru import logger
from ..widgets import RevealerWindow
from ..utils import get_widget_monitor
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
    open_floating: Gtk.CheckButton = gtk_template_child()
    open_fullscreen: Gtk.CheckButton = gtk_template_child()
    open_maximized: Gtk.CheckButton = gtk_template_child()
    block_screencast: Gtk.CheckButton = gtk_template_child()
    block_capture: Gtk.CheckButton = gtk_template_child()
    monitor_combo: Gtk.ComboBoxText = gtk_template_child()
    workspace_combo: Gtk.ComboBoxText = gtk_template_child()
    save_button: Gtk.Button = gtk_template_child()
    delete_button: Gtk.Button = gtk_template_child()

    @gtk_template_callback
    def on_save_button_clicked(self, *_):
        self.get_ancestor(TitleSetter).handle_save_button_click()

    @gtk_template_callback
    def on_magic_button_clicked(self, *_):
        self.get_ancestor(TitleSetter).handle_magic_click()

    @gtk_template_callback
    def on_delete_button_clicked(self, *_):
        self.get_ancestor(TitleSetter).handle_delete_button_click()

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
        self._last_state = {}
        self._is_generating = False
        self._generation_id = 0
        
        # Connect signals
        self._niri.connect("notify::active-window", self._on_focus)
        connect_window(self, "notify::visible", self._on_show)
        self.child.monitor_combo.connect("changed", self._on_monitor_changed)
        
        # Input validation
        self.child.title_entry.connect("notify::text", self._validate_input)
        self._validate_input()

    def _on_focus(self, *args):
        win = self._niri.active_window
        # Ignore empty focus and the tool itself
        if win and win.app_id and win.app_id != WindowName.title_setter.value:
            self._last_win = win
            self._last_title = win.title
            self._last_state = {
                "floating": getattr(win, "is_floating", False),
                "fullscreen": getattr(win, "is_fullscreen", False),
                "maximized": getattr(win, "is_maximized", None),
                "default_column_width": getattr(win, "default_column_width", None),
            }

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
                
                # Check existing rule state
                rule_state = self._get_existing_rule_state(self._last_win.app_id, self._last_title) 
                self.child.open_floating.set_active(rule_state.get("floating", self._last_state.get("floating", False)))
                self.child.open_fullscreen.set_active(rule_state.get("fullscreen", self._last_state.get("fullscreen", False)))
                self.child.open_maximized.set_active(rule_state.get("maximized", self._last_state.get("maximized") or False))

                self.child.block_screencast.set_active(rule_state.get("block_screencast", False))
                self.child.block_capture.set_active(rule_state.get("block_capture", False))

                # Populate monitors and set the active one
                self._populate_monitors()

                # Get the active monitor from the combo
                selected_connector = self.child.monitor_combo.get_active_text()
                
                # Populate workspaces for the selected monitor
                if selected_connector:
                    self._populate_workspaces(selected_connector, rule_state.get("workspace"))
            
            self._validate_input()
            
            # Disable magic button if dependencies are missing
            self.child.magic_button.set_sensitive(HAS_MAGIC)
            if not HAS_MAGIC:
                self.child.magic_button.set_tooltip_text("Missing 'pyatspi' dependency")

    def _populate_monitors(self):
        combo = self.child.monitor_combo
        combo.remove_all()

        outputs = sorted(list(set([ws.output for ws in self._niri.workspaces])))
        
        current_monitor = get_widget_monitor(self)
        current_connector = current_monitor.get_connector() if current_monitor else None

        active_idx = -1
        for i, output in enumerate(outputs):
            combo.append_text(output)
            if output == current_connector:
                active_idx = i
        
        if active_idx != -1:
            combo.set_active(active_idx)
        elif outputs:
            combo.set_active(0)

    def _on_monitor_changed(self, combo):
        connector = combo.get_active_text()
        if connector:
            self._populate_workspaces(connector)

    def _populate_workspaces(self, connector, active_ws_from_rule=None):
        combo = self.child.workspace_combo
        combo.remove_all()

        workspaces_on_monitor = [ws for ws in self._niri.workspaces if ws.output == connector]

        active_ws_name = active_ws_from_rule

        if not active_ws_name and self._last_win and self._last_win.workspace_id:
            for ws in workspaces_on_monitor:
                if ws.id == self._last_win.workspace_id:
                    active_ws_name = ws.name or str(ws.id)
                    break
        
        if not active_ws_name:
            for ws in workspaces_on_monitor:
                if ws.is_active:
                    active_ws_name = ws.name or str(ws.id)
                    break

        active_idx = -1
        for i, ws in enumerate(workspaces_on_monitor):
            ws_name = ws.name or str(ws.id)
            combo.append_text(ws_name)
            if ws_name == active_ws_name:
                active_idx = i
        
        if active_idx == -1 and workspaces_on_monitor:
            combo.set_active(0)
        else:
            combo.set_active(active_idx)

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
        ws = self.child.workspace_combo.get_active_text()
        block_screencast = self.child.block_screencast.get_active()
        block_capture = self.child.block_capture.get_active()

        state = {
            "floating": self.child.open_floating.get_active(),
            "fullscreen": self.child.open_fullscreen.get_active(),
            "maximized": self.child.open_maximized.get_active(),
            "default_column_width": self._last_state.get("default_column_width"),
        }
        if not (new_title and win and ws): return
        
        path = os.path.expanduser("~/.config/niri/window-rules.kdl")
        try:
            self._update_niri_config(path, win.app_id, new_title, ws, state, block_screencast, block_capture)
            self.child.status_label.set_text("✅ Rule saved")
            self.set_visible(False)
        except Exception as e:
            logger.error(f"Config Update Error: {e}")
            self.child.status_label.set_text("❌ Failed to save rule")

    def handle_delete_button_click(self):
        win = self._last_win
        title = self.child.title_entry.get_text().strip()
        if not (win and title): return

        path = os.path.expanduser("~/.config/niri/window-rules.kdl")
        try:
            if self._delete_niri_rule(path, win.app_id, title):
                self.child.status_label.set_text("🗑️ Rule deleted")
                self.set_visible(False)
            else:
                self.child.status_label.set_text("⚠️ Rule not found")
        except Exception as e:
            logger.error(f"Config Delete Error: {e}")
            self.child.status_label.set_text("❌ Failed to delete")

    def _update_niri_config(self, path, app_id, title, workspace, state, block_screencast, block_capture):
        if not os.path.exists(path):
            with open(path, "w") as f: f.write("// Niri Window Rules\n\n")
        
        with open(path, "r") as f:
            lines = f.readlines()

        target_app_regex = f"^{re.escape(app_id)}$"
        target_title_regex = f"^{re.escape(title)}$"
        
        # Parse blocks to find match or insertion point
        blocks = self._parse_blocks(lines)

        # Prepare properties
        props = {
            "open-on-workspace": f'    open-on-workspace "{workspace}"\n',
            "open-floating": f'    open-floating {str(state.get("floating", False)).lower()}\n',
            "open-fullscreen": f'    open-fullscreen {str(state.get("fullscreen", False)).lower()}\n',
        }
        if state.get("maximized") is not None:
            props["open-maximized"] = f'    open-maximized {str(state["maximized"]).lower()}\n'
        
        block_values = []
        if block_screencast: block_values.append('"screencast"')
        if block_capture: block_values.append('"screen-capture"')
        props["block-out-from"] = f'    block-out-from {" ".join(block_values)}\n' if block_values else None
        
        dcw = state.get("default_column_width")
        if dcw:
            inner = ""
            if "proportion" in dcw:
                inner = f"proportion {dcw['proportion']}"
            elif "fixed" in dcw:
                inner = f"fixed {dcw['fixed']}"
            if inner:
                props["default-column-width"] = f'    default-column-width {{ {inner}; }}\n'

        # 1. Try to update existing rule
        for start, end, b_app, b_title in blocks:
            if b_app == target_app_regex and b_title == target_title_regex:
                self._update_rule_in_place(lines, start, end, props)
                with open(path, "w") as f: f.writelines(lines)
                return

        # 2. Insert new rule (Sorted by app-id)
        new_rule_lines = [
            f"// Session: {datetime.date.today()}\n",
            "window-rule {\n",
            f'    match app-id="{target_app_regex}" title="{target_title_regex}"\n',
        ]
        for key, p in props.items():
            if p is None: continue
            new_rule_lines.append(p)
        new_rule_lines.append("}\n\n")
        
        insert_idx = len(lines) # Default append
        for start, _, b_app, _ in blocks:
            # Simple alphabetical sort on the regex string
            if b_app > target_app_regex:
                insert_idx = start
                # If there's a comment above the block, try to include it in the shift
                if insert_idx > 0 and lines[insert_idx-1].strip().startswith("//"):
                    insert_idx -= 1
                break
        
        lines[insert_idx:insert_idx] = new_rule_lines
        
        with open(path, "w") as f: f.writelines(lines)

    def _update_rule_in_place(self, lines, start, end, properties):
        found_keys = set()
        i = start
        while i < end:
            line = lines[i].strip()
            matched_key = None
            for key, new_line in properties.items():
                # Check if line starts with key
                if line.startswith(key) and (len(line) == len(key) or line[len(key)] in [' ', '\t', '"', '{']):
                    matched_key = key
                    break
            
            if matched_key:
                found_keys.add(matched_key)
                new_line = properties[matched_key]
                
                # Handle replacing multi-line blocks
                brace_balance = 0
                j = i
                while j < end:
                    brace_balance += lines[j].count("{") - lines[j].count("}")
                    if brace_balance <= 0:
                        break
                    j += 1
                
                if new_line is None:
                    # Delete the property
                    del lines[i : j+1]
                    end -= (j + 1 - i)
                    i -= 1 # Adjust index since we deleted
                else:
                    # Replace the property
                    indent = lines[i][:lines[i].find(matched_key)]
                    lines[i] = indent + new_line.lstrip()
                    
                    if j > i:
                        del lines[i+1 : j+1]
                        end -= (j - i)
            
            i += 1

        for key, new_line in properties.items():
            if key not in found_keys and new_line is not None:
                lines.insert(end, new_line)
                end += 1

    def _delete_niri_rule(self, path, app_id, title):
        if not os.path.exists(path): return False
        
        with open(path, "r") as f:
            lines = f.readlines()

        target_app_regex = f"^{re.escape(app_id)}$"
        target_title_regex = f"^{re.escape(title)}$"
        
        blocks = self._parse_blocks(lines)

        for start, end, b_app, b_title in blocks:
            if b_app == target_app_regex and b_title == target_title_regex:
                # Check for comment above
                del_start = start
                if del_start > 0 and lines[del_start-1].strip().startswith("//"):
                    del_start -= 1
                
                del lines[del_start : end+1]
                with open(path, "w") as f: f.writelines(lines)
                return True
        return False

    def _parse_blocks(self, lines):
        blocks = []
        in_rule = False
        start_idx = -1
        brace_depth = 0
        
        for i, line in enumerate(lines):
            s = line.strip()
            if s.startswith("//"): continue

            if not in_rule:
                if s.startswith("window-rule") and "{" in line:
                    in_rule = True
                    start_idx = i
                    brace_depth = 0
            
            if in_rule:
                brace_depth += line.count("{") - line.count("}")
                if brace_depth <= 0:
                    in_rule = False
                    block_text = "".join(lines[start_idx:i+1])
                    m_app = re.search(r'app-id="([^"]+)"', block_text)
                    m_title = re.search(r'title="([^"]+)"', block_text)
                    blocks.append((
                        start_idx, i, 
                        m_app.group(1) if m_app else "", 
                        m_title.group(1) if m_title else "",
                        block_text
                    ))
        return blocks

    def _get_existing_rule_state(self, app_id, title):
        path = os.path.expanduser("~/.config/niri/window-rules.kdl")
        if not os.path.exists(path): return {}
        
        with open(path, "r") as f: lines = f.readlines()
        
        target_app = f"^{re.escape(app_id)}$"
        target_title = f"^{re.escape(title)}$"
        
        for _, _, b_app, b_title, text in self._parse_blocks(lines):
            if b_app == target_app and b_title == target_title:
                state = {}
                for line in text.splitlines():
                    s_line = line.strip()
                    parts = s_line.split()
                    if not parts: continue
                    key = parts[0]
                    
                    if key == "block-out-from":
                        if '"screencast"' in s_line: state["block_screencast"] = True
                        if '"screen-capture"' in s_line: state["block_capture"] = True
                    elif key in ["open-floating", "open-fullscreen", "open-maximized"]:
                        prop_name = key.split('-')[1]
                        state[prop_name] = len(parts) > 1 and parts[1].lower() == 'true'
                    elif key == "open-on-workspace":
                        match = re.search(r'"([^"]+)"', s_line)
                        if match: state["workspace"] = match.group(1)
                return state
        return {}