#!/usr/bin/env python3
"""
HP OMEN 17 RGB Controller for Linux
Modes: Static, Rainbow (исправлен)
Features:
 - Smooth static transitions (fast)
 - Rainbow mode (very fast)
 - Single-instance system tray
 - Persist last mode via QSettings
 - High-DPI scaling
"""

import os
import re
import sys
import time
from pathlib import Path
from threading import Event
from PyQt5.QtCore import Qt, QThread, QSettings, QCoreApplication, pyqtSlot, QTimer
from PyQt5.QtGui import QColor, QIcon
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
    QGroupBox, QPushButton, QColorDialog, QSystemTrayIcon, QMenu, QAction,
    QTreeWidgetItemIterator
)

# ---------- CONFIGURATION ---------- #
APP_NAME = "hp-omen-rgb"
ORG_NAME = "OmenTools"
ZONE_PATH = Path("/sys/devices/platform/hp-wmi/rgb_zones/zone00")
THEME_COLOR_PATH = Path.home() / ".config/hypr/themes/colors.conf"
STATIC_DURATION_MS = 100   # fast static transition
RAIN_DELAY_MS = 10         # very fast rainbow tick delay
PRESETS = {
    "Red": "ff0000", "Green": "00ff00", "Blue": "0000ff",
    "Purple": "800080", "White": "ffffff", "Orange": "ff7f00",
}

# ---------- UTILITIES ---------- #
def get_wallpaper_primary_color():
    """Reads the hyprland color config and returns the primary wallpaper color ($wallbash_pry1)."""
    try:
        with open(THEME_COLOR_PATH, "r") as f:
            content = f.read()
        match = re.search(r"^\$wallbash_pry1\s*=\s*([0-9a-fA-F]{6})", content, re.MULTILINE)
        if match:
            color = match.group(1)
            print(f"[DEBUG] Wallpaper primary color found: {color}")
            return color
        else:
            print("[DEBUG] $wallbash_pry1 not found in theme file.")
    except FileNotFoundError:
        print(f"[ERR] Theme color file not found at {THEME_COLOR_PATH}", file=sys.stderr)
    except Exception as e:
        print(f"[ERR] Error reading theme color: {e}", file=sys.stderr)
    return None # Return None on failure

def hex_to_rgb(h):
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def rgb_to_hex(c):
    return f"{c[0]:02x}{c[1]:02x}{c[2]:02x}"

_current_hardware_color = None # Renamed from _last_color

def write_color(col_hex):
    """Writes a color directly to the sysfs file, assuming udev rules are set."""
    global _current_hardware_color
    try:
        with open(ZONE_PATH, "w") as f:
            f.write(col_hex)
        _current_hardware_color = col_hex
    except PermissionError:
        print(f"[ERR] Permission denied writing to {ZONE_PATH}. Are udev rules set up correctly?", file=sys.stderr)
    except Exception as e:
        print(f"[ERR] Error writing color: {e}", file=sys.stderr)

def set_color(col_hex, duration_ms=None):
    global _current_hardware_color
    if duration_ms and _current_hardware_color and _current_hardware_color.lower() != col_hex.lower():
        r0, g0, b0 = hex_to_rgb(_current_hardware_color)
        r1, g1, b1 = hex_to_rgb(col_hex)
        steps = 20
        delay = duration_ms / steps / 1000.0
        for i in range(1, steps + 1):
            t = i / steps
            ir = int(r0 + (r1 - r0) * t)
            ig = int(g0 + (g1 - g0) * t)
            ib = int(b0 + (b1 - b0) * t)
            write_color(rgb_to_hex((ir, ig, ib)))
            time.sleep(delay)
    else:
        write_color(col_hex)
    # The write_color function now updates _current_hardware_color
    # So no need to set it here again.

# ---------- THREADS ---------- #
class ModeThread(QThread):
    """Base class for all keyboard lighting effect threads."""
    def __init__(self, delay_ms=100):
        super().__init__()
        self.delay_s = delay_ms / 1000.0
        self.stop_event = Event()

    def run(self):
        try:
            print(f"[DEBUG] Thread {self.__class__.__name__} started run loop.", file=sys.stderr)
            while not self.stop_event.is_set():
                self.update_color()
                self.stop_event.wait(self.delay_s)
        except Exception as e:
            print(f"[ERR] Error in {self.__class__.__name__}: {e}", file=sys.stderr)


    def update_color(self):
        """This method should be implemented by subclasses to set the keyboard color."""
        raise NotImplementedError

    def stop(self):
        self.stop_event.set()
        self.wait()

# --- RainbowThread ---
class RainbowThread(QThread):
    def __init__(self, delay_ms):
        super().__init__()
        self.delay_s = delay_ms / 1000.0
        self.hue = 0
        self.stop_event = Event()

    def run(self):
        print("[DEBUG] Standalone RainbowThread started run loop.", file=sys.stderr)
        try:
            while not self.stop_event.is_set():

                col = QColor.fromHsv(self.hue % 360, 255, 255).name()[1:]
                write_color(col)
                self.hue += 1
                self.stop_event.wait(self.delay_s)
        except Exception as e:
            print(f"[ERR] Error in RainbowThread: {e}", file=sys.stderr)

    def stop(self):
        print("[DEBUG] Stopping RainbowThread.", file=sys.stderr)
        self.stop_event.set()
        self.wait()


class NewYearsThread(ModeThread):
    def __init__(self, delay_ms=500):
        super().__init__(delay_ms)
        self.colors = ["ff0000", "00ff00"]
        self.color_index = 0

    def update_color(self):
        write_color(self.colors[self.color_index])
        self.color_index = (self.color_index + 1) % len(self.colors)

class PoliceThread(ModeThread):
    def __init__(self, delay_ms=100):
        super().__init__(delay_ms)
        self.colors = ["ff0000", "0000ff"]
        self.color_index = 0

    def update_color(self):
        write_color(self.colors[self.color_index])
        self.color_index = (self.color_index + 1) % len(self.colors)

# ---------- GUI ---------- #
class RGBController(QWidget):
    def __init__(self):
        super().__init__()
        self.active_thread = None
        self.settings = QSettings(ORG_NAME, APP_NAME)

        self.init_ui()
        self.init_tray()

        # Defer initialization until after the event loop starts
        QTimer.singleShot(100, self.initialize_state)

    def initialize_state(self):
        """Sets the initial color and UI state after the application starts."""
        theme_applied = self.initial_color_setup()
        if not theme_applied:
            self.load_settings()
        self.update_ui_from_settings()

    def initial_color_setup(self):
        """Tries to get and apply the wallpaper's primary color on startup. Returns True on success."""
        wallpaper_color_hex = get_wallpaper_primary_color()
        if wallpaper_color_hex:
            try:
                set_color(wallpaper_color_hex, duration_ms=None)
                self.settings.setValue("lastMode", "Static Color")
                self.settings.setValue("lastStaticColor", wallpaper_color_hex)
                return True
            except Exception as e:
                print(f"[ERR] Failed to apply wallpaper color '{wallpaper_color_hex}': {e}", file=sys.stderr)
        return False

    def update_ui_from_settings(self):
        """Sets the initial UI selection based on stored settings."""
        current_mode_name = self.settings.value("lastMode", "Static Color")
        
        target_item = None
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            item = it.value()
            if item.data(0, Qt.UserRole) == current_mode_name:
                target_item = item
                break
            it += 1

        if target_item and target_item.flags() & Qt.ItemIsEnabled:
            self.tree.setCurrentItem(target_item)
            self.rebuild_options_ui(current_mode_name)
        else:
            it = QTreeWidgetItemIterator(self.tree)
            while it.value():
                item = it.value()
                if item.data(0, Qt.UserRole) == "Static Color":
                    self.tree.setCurrentItem(item)
                    self.rebuild_options_ui("Static Color")
                    break
                it += 1

    def init_tray(self):
        icon = QIcon.fromTheme("keyboard")
        self.tray = QSystemTrayIcon(icon, self)
        menu = QMenu()
        menu.addAction(QAction("Show", self, triggered=self.show))
        menu.addAction(QAction("Exit", self, triggered=self.exit_app))
        self.tray.setContextMenu(menu)
        self.tray.show()

    def init_ui(self):
        self.setWindowTitle("HP OMEN RGB Controller")
        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        
        modes_structure = {
            " Static": {
                "Static Color": ""
            },
            " Animated": {
                "Rainbow": "徭",
                "New Year's": "喝",
                "Police": ""
            }
        }

        for category_name, modes in modes_structure.items():
            category_item = QTreeWidgetItem(self.tree, [category_name])
            category_item.setFlags(category_item.flags() & ~Qt.ItemIsSelectable)
            for mode_name, icon in modes.items():
                full_name = f"{icon} {mode_name}" if icon else mode_name
                mode_item = QTreeWidgetItem(category_item, [full_name])
                mode_item.setData(0, Qt.UserRole, mode_name)

        self.tree.expandAll()
        self.tree.itemClicked.connect(self.on_mode)
        layout.addWidget(self.tree)
        
        self.options_box = QGroupBox("Options")
        self.options_layout = QVBoxLayout(self.options_box)
        layout.addWidget(self.options_box)
        self.hide()

    def clear_opts(self):
        while self.options_layout.count():
            w = self.options_layout.takeAt(0).widget()
            if w:
                w.deleteLater()

    def rebuild_options_ui(self, mode):
        self.clear_opts()
        if mode == "Static Color":
            for name, hexc in PRESETS.items():
                btn = QPushButton(name)
                btn.clicked.connect(lambda _, c=hexc: self.apply_static_color(c))
                self.options_layout.addWidget(btn)
            cust = QPushButton("Choose Custom Color")
            cust.clicked.connect(self.pick_color)
            self.options_layout.addWidget(cust)
            btn_wallpaper = QPushButton("Apply Wallpaper Color")
            btn_wallpaper.clicked.connect(self.apply_wallpaper_color)
            self.options_layout.addWidget(btn_wallpaper)

    def activate_mode(self, mode, is_startup=False):
        # No longer print debug here, let the thread do it.
        self.stop_active_thread()
        if mode == "Static Color":
            last_color = self.settings.value("lastStaticColor", "ffffff")
            duration = None if is_startup else STATIC_DURATION_MS
            set_color(last_color, duration)
        elif mode == "Rainbow":
            self.start_thread(RainbowThread(RAIN_DELAY_MS))
        elif mode == "New Year's":
            self.start_thread(NewYearsThread(500))
        elif mode == "Police":
            self.start_thread(PoliceThread(100))

    def on_mode(self, item, _):
        if not item.parent():
            return
        mode = item.data(0, Qt.UserRole)
        if mode:
            self.settings.setValue("lastMode", mode)
            self.rebuild_options_ui(mode)
            self.activate_mode(mode, is_startup=False)

    def apply_static_color(self, color_hex):
        self.stop_active_thread() # Stop any running animation.
        set_color(color_hex, STATIC_DURATION_MS)
        self.settings.setValue("lastStaticColor", color_hex)
        
        # If we weren't in static mode, update the UI to reflect the change.
        if self.settings.value("lastMode") != "Static Color":
            self.settings.setValue("lastMode", "Static Color")
            it = QTreeWidgetItemIterator(self.tree)
            while it.value():
                item = it.value()
                if item.data(0, Qt.UserRole) == "Static Color":
                    # Silently update the current item without triggering on_mode again.
                    self.tree.blockSignals(True)
                    self.tree.setCurrentItem(item)
                    self.tree.blockSignals(False)
                    break
                it += 1

    @pyqtSlot()
    def pick_color(self):
        c = QColorDialog.getColor()
        if c.isValid():
            self.apply_static_color(c.name()[1:])

    @pyqtSlot()
    def apply_wallpaper_color(self):
        color_hex = get_wallpaper_primary_color()
        if color_hex:
            self.apply_static_color(color_hex)
        else:
            print("[WARN] Could not get wallpaper primary color. Applying default white.", file=sys.stderr)
            self.apply_static_color("ffffff")

    def start_thread(self, thread_instance):
        self.stop_active_thread()
        self.active_thread = thread_instance
        self.active_thread.start()

    def stop_active_thread(self):
        if self.active_thread:
            self.active_thread.stop()
            self.active_thread = None

    @pyqtSlot()
    def exit_app(self):
        self.stop_active_thread()
        self.tray.hide()
        QApplication.quit()

    def load_settings(self):
        mode = self.settings.value("lastMode", "Static Color")
        self.activate_mode(mode, is_startup=True)

    def closeEvent(self, event):
        self.hide()
        event.ignore()

# ---------- ENTRY ---------- #
if __name__ == "__main__":
    import traceback
    import sys # Нужно импортировать sys для sys.stderr

    # Эту строку теперь можно удалить, так как она больше не нужна
    # log_file = "/home/user/.gemini/tmp/b98d692c4574a80e508cf6fa38e6f27ae5348e616448edaa7fe675e363669fd2/kbrgb_crash.log"

    try:
        if os.environ.get("WAYLAND_DISPLAY"):
            os.environ.setdefault("QT_QPA_PLATFORM", "wayland")
        
        QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
        QCoreApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
        app = QApplication(sys.argv)
        app.setOrganizationName(ORG_NAME)
        app.setApplicationName(APP_NAME)
        controller = RGBController()
        sys.exit(app.exec_())
    except Exception as e:
        # --- ИЗМЕНЕННЫЙ БЛОК EXCEPT ---
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        # -----------------------------
        
        # Старый код, который вы удалили:
        # with open(log_file, "w") as f:
        #     f.write(f"An unexpected error occurred: {e}\n")
        #     f.write(traceback.format_exc())
