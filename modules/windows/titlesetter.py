from gi.repository import Gtk, GObject
from ignis.widgets import Window # Outer class will inherit from this
from ..utils.template import gtk_template, gtk_template_child, gtk_template_callback
from ..utils.widget import connect_window
import os
import re

from ..constants import WindowName
from ignis.services.niri import NiriService

# New nested View class that uses the Blueprint
@gtk_template(filename="titlesetter")
class TitleSetterView(Gtk.Box): # Inherits Gtk.Box as per Blueprint template
    __gtype_name__ = "TitleSetterView"

    title_entry: Gtk.Entry = gtk_template_child()
    status_label: Gtk.Label = gtk_template_child()
    save_button: Gtk.Button = gtk_template_child()

    def __init__(self):
        super().__init__()
        # No explicit signal connections here; Blueprint handles clicked.
        # Other logic for this View, if any, can go here.

    # Callback methods for the View's UI elements
    @gtk_template_callback
    def on_save_button_clicked(self, *_):
        # This will be handled by the outer class or by explicit passing
        # For now, it will be a placeholder and the outer class will call it.
        # Or, we can pass a reference to the outer class's logic.
        # Let's make it call a method on the parent window for now.
        parent_window = self.get_ancestor(TitleSetter)
        if isinstance(parent_window, TitleSetter):
            parent_window.handle_save_button_click()


# Outer Window class
class TitleSetter(Window): # Inherits ignis.widgets.Window
    __gtype_name__ = "IgnisTitleSetter" # GObject type name for the window itself

    def __init__(self):
        self.__view = TitleSetterView() # Instantiate the View
        super().__init__(
            namespace=WindowName.title_setter.value,
            layer="overlay",
            popup=False,
            visible=False,
            kb_mode="exclusive"
        )
        self.add_css_class("rounded")
        self.set_child(self.__view) # Set the View as the child of the Window
        self._niri_service = NiriService.get_default()

        # Connect window property signals
        connect_window(self, "notify::visible", self._on_visibility_changed)
        # ADD THIS LINE for Enter key press
        self.__view.title_entry.connect("activate", self.handle_save_button_click)

    def _on_visibility_changed(self, window: Window, _):
        if window.get_visible():
            self.__view.title_entry.set_text("")
            self.__view.status_label.set_text("")
            focused_window = self._get_focused_window_details()
            if focused_window and focused_window.get("title"):
                self.__view.title_entry.set_text(focused_window["title"])

    # Method to handle the save button click, called by the View
    def handle_save_button_click(self, *_): # Added *_ for compatibility with Gtk signal
        new_title = self.__view.title_entry.get_text().strip()
        if not new_title:
            self.__view.status_label.set_text("Title cannot be empty.")
            return

        focused_window = self._get_focused_window_details()
        if not focused_window:
            self.__view.status_label.set_text("No focused window found.") # Re-add for clarity
            return

        workspace_name = focused_window.get("workspace_name")
        if not workspace_name:
            self.__view.status_label.set_text("Could not determine current workspace.")
            return

        if self._update_niri_config(new_title, workspace_name):
            self.__view.status_label.set_text(f"Rule added for '{new_title}' on workspace '{workspace_name}'. Please rename your window to '{new_title}'.")
            GObject.timeout_add_seconds(3, self.set_visible, False)
        else:
            pass # Error message set by _update_niri_config

    def _get_focused_window_details(self):
        if not self._niri_service.is_available:
            self.__view.status_label.set_text("Niri service is not available.")
            return None

        # Get the name of the currently active workspace
        current_workspace_name = self._niri_service.get_property("current-workspace")
        if not current_workspace_name:
            self.__view.status_label.set_text("Could not determine current workspace.")
            return None

        focused_niri_window_to_manage = None
        for window in self._niri_service.get_windows():
            if window.is_focused and window.app_id != WindowName.title_setter.value:
                focused_niri_window_to_manage = window
                break

        if focused_niri_window_to_manage:
            return {
                "app_id": focused_niri_window_to_manage.app_id,
                "title": focused_niri_window_to_manage.title,
                "workspace_name": current_workspace_name
            }
        else:
            return None


    def _update_niri_config(self, new_title: str, workspace_name: str):
        niri_config_path = os.path.expanduser("~/.config/niri/config.kdl")
        try:
            with open(niri_config_path, "r") as f:
                content = f.read()

            new_rule = f"""
	window-rule {{
		match title=r#"^{re.escape(new_title)}$"#
		open-on-workspace "{workspace_name}"
		open-maximized true
		open-focused false
	}}
"""
            pip_rule_marker_1 = '\twindow-rule {\n\t    match title="^Picture-in-Picture$"' # Corrected escaping for literal backslashes
            pip_rule_marker_2 = '    window-rule {\n        match app-id=r#"firefox$"# title="^Picture-in-Picture$"' # Corrected escaping for literal backslashes
            geometry_rule_marker = '    window-rule { geometry-corner-radius 8'

            insertion_point_index = -1

            if pip_rule_marker_1 in content:
                insertion_point_index = content.find(pip_rule_marker_1)
            elif pip_rule_marker_2 in content:
                insertion_point_index = content.find(pip_rule_marker_2)
            elif geometry_rule_marker in content:
                insertion_point_index = content.find(geometry_rule_marker)

            if insertion_point_index != -1:
                new_content = content[:insertion_point_index] + new_rule + content[insertion_point_index:]
            else:
                new_content = content + new_rule

            with open(niri_config_path, "w") as f:
                f.write(new_content)

            if self._niri_service.is_available:
                self.__view.status_label.set_text("Niri config updated and reloaded.")
                self._niri_service.reload_config()
            else:
                self.__view.status_label.set_text("Niri config updated, but could not reload Niri (service not available).")
            return True
        except Exception as e:
            self.__view.status_label.set_text(f"Error updating niri config: {e}")
            return False
