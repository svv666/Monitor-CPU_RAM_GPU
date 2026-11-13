import os
import sys
import json
import time
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import ttk
import ctypes

try:
    import winreg
    WINREG_AVAILABLE = True
except ImportError:
    WINREG_AVAILABLE = False

# Enable High DPI awareness for crisp text and rendering on Windows
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
    except Exception:
        pass

import psutil

# GPU Monitoring (NVML)
try:
    import pynvml
    NVML_AVAILABLE = True
except ImportError:
    NVML_AVAILABLE = False

# Win32 APIs for fullscreen detection & window management
try:
    from win32gui import GetForegroundWindow, GetWindowRect, GetClassName, GetWindowLong, IsIconic
    from win32api import GetSystemMetrics
    import win32con
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False


# Theme Definitions
THEMES = {
    "Dark Neon": {
        "bg": "#0a0a0a",
        "card_bg": "#141414",
        "cpu": "#39FF14",    # Neon Green
        "ram": "#FFD700",    # Gold
        "gpu": "#00CEFF",    # Cyan
        "net": "#FF007F",    # Neon Pink
        "disk": "#A020F0",   # Purple
        "sep": "#333333",
        "text": "#E0E0E0",
        "bar_bg": "#222222",
    },
    "Cyberpunk": {
        "bg": "#0d0221",
        "card_bg": "#190535",
        "cpu": "#00ff9f",    # Mint
        "ram": "#ffea00",    # Electric Yellow
        "gpu": "#00b8ff",    # Deep Blue
        "net": "#ff0055",    # Neon Red
        "disk": "#b600ff",   # Neon Purple
        "sep": "#3a005d",
        "text": "#ffffff",
        "bar_bg": "#260943",
    },
    "Dracula": {
        "bg": "#282a36",
        "card_bg": "#343746",
        "cpu": "#50fa7b",    # Green
        "ram": "#f1fa8c",    # Yellow
        "gpu": "#8be9fd",    # Cyan
        "net": "#ff79c6",    # Pink
        "disk": "#bd93f9",   # Purple
        "sep": "#44475a",
        "text": "#f8f8f2",
        "bar_bg": "#44475a",
    },
    "Minimalist": {
        "bg": "#18181b",
        "card_bg": "#27272a",
        "cpu": "#e4e4e7",    # White-gray
        "ram": "#d4d4d8",    # Silver
        "gpu": "#a1a1aa",    # Gray
        "net": "#71717a",    # Darker gray
        "disk": "#a1a1aa",   # Gray
        "sep": "#3f3f46",
        "text": "#fafafa",
        "bar_bg": "#3f3f46",
    }
}

def get_base_dir():
    """Return the folder that contains the app (exe when frozen, script otherwise)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_FILE = os.path.join(get_base_dir(), "config.json")

DEFAULT_CONFIG = {
    "x": None,
    "y": None,
    "theme": "Dark Neon",
    "opacity": 0.95,
    "interval": 2.0,
    "mode": "compact",      # "compact" or "detailed"
    "locked": False,
    "autohide": True,
    "show_bars": True,
    "autostart": False
}


class SystemMonitor:
    def __init__(self, root):
        self.root = root
        self.root.title("SysMonitor Pro")
        self.set_window_icon()
        
        # Load user configuration
        self.config = self.load_config()
        
        # Window attributes
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", self.config.get("opacity", 0.95))
        
        # GPU handle
        self.gpu_handle = None
        self.init_gpu()

        # Thread queue for thread-safe UI updates
        self.update_queue = queue.Queue()

        # Network io state
        self.prev_net_io = psutil.net_io_counters()
        self.prev_net_time = time.time()
        
        # Warmup CPU measurement
        psutil.cpu_percent(interval=None)

        # Build UI
        self.setup_ui()
        self.apply_rounded_corners()

        # Bind events
        self.bind_events()

        # Position window
        self.init_position()

        # State flags
        self.is_hidden = False
        self.running = True
        self._queue_after_id = None

        # Start monitoring background thread
        self.start_worker_thread()

        # Process queue periodically on main Tkinter loop
        self.process_queue()

    def load_config(self):
        config = DEFAULT_CONFIG.copy()
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    config.update(loaded)
            except Exception:
                pass

        # Sanitize values so a corrupted/partial config can never crash the UI
        for key in ("x", "y"):
            try:
                value = config.get(key)
                config[key] = int(value) if value is not None else None
            except (TypeError, ValueError):
                config[key] = None

        try:
            config["opacity"] = min(1.0, max(0.2, float(config.get("opacity", 0.95))))
        except (TypeError, ValueError):
            config["opacity"] = 0.95

        try:
            config["interval"] = min(60.0, max(0.5, float(config.get("interval", 2.0))))
        except (TypeError, ValueError):
            config["interval"] = 2.0

        if config.get("theme") not in THEMES:
            config["theme"] = DEFAULT_CONFIG["theme"]
        if config.get("mode") not in ("compact", "detailed"):
            config["mode"] = DEFAULT_CONFIG["mode"]

        for key in ("locked", "autohide", "show_bars", "autostart"):
            config[key] = bool(config.get(key, DEFAULT_CONFIG[key]))

        return config

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4)
        except Exception:
            pass

    def set_window_icon(self):
        candidates = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(meipass)
        candidates.append(get_base_dir())
        for base in candidates:
            path = os.path.join(base, "icon.ico")
            if os.path.exists(path):
                try:
                    self.root.iconbitmap(default=path)
                    return
                except Exception:
                    pass

    def init_gpu(self):
        if NVML_AVAILABLE:
            try:
                pynvml.nvmlInit()
                self.gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            except Exception:
                self.gpu_handle = None

    def setup_ui(self):
        theme_name = self.config.get("theme", "Dark Neon")
        self.colors = THEMES.get(theme_name, THEMES["Dark Neon"])

        self.root.configure(bg=self.colors["bg"])

        # Rebuilding the UI must replace the previous container, not stack a new one on top
        if getattr(self, "main_frame", None) is not None:
            try:
                self.main_frame.destroy()
            except Exception:
                pass

        # Main container frame
        self.main_frame = tk.Frame(self.root, bg=self.colors["bg"])
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=3)

        self.build_metric_widgets()

    def build_metric_widgets(self):
        # Clear frame
        for widget in self.main_frame.winfo_children():
            widget.destroy()

        mode = self.config.get("mode", "compact")
        show_bars = self.config.get("show_bars", True)
        font_style = ("Segoe UI", 9, "bold")
        font_small = ("Segoe UI", 8)

        self.widgets = {}

        def create_sep():
            sep = tk.Label(self.main_frame, text="|", font=font_style, bg=self.colors["bg"], fg=self.colors["sep"])
            sep.pack(side=tk.LEFT, padx=2)
            return sep

        # --- CPU Block ---
        cpu_box = tk.Frame(self.main_frame, bg=self.colors["bg"])
        cpu_box.pack(side=tk.LEFT, padx=(6, 2))

        cpu_lbl = tk.Label(cpu_box, text="CPU: 0%", font=font_style, bg=self.colors["bg"], fg=self.colors["cpu"])
        cpu_lbl.pack(side=tk.LEFT)
        
        cpu_bar = None
        if show_bars:
            cpu_bar = tk.Canvas(cpu_box, width=32, height=6, bg=self.colors["bar_bg"], highlightthickness=0)
            cpu_bar.pack(side=tk.LEFT, padx=(4, 0))

        self.widgets["cpu"] = (cpu_lbl, cpu_bar)

        create_sep()

        # --- RAM Block ---
        ram_box = tk.Frame(self.main_frame, bg=self.colors["bg"])
        ram_box.pack(side=tk.LEFT, padx=2)

        ram_lbl = tk.Label(ram_box, text="RAM: 0.0 GB", font=font_style, bg=self.colors["bg"], fg=self.colors["ram"])
        ram_lbl.pack(side=tk.LEFT)

        ram_bar = None
        if show_bars:
            ram_bar = tk.Canvas(ram_box, width=32, height=6, bg=self.colors["bar_bg"], highlightthickness=0)
            ram_bar.pack(side=tk.LEFT, padx=(4, 0))

        self.widgets["ram"] = (ram_lbl, ram_bar)

        create_sep()

        # --- GPU Block ---
        gpu_box = tk.Frame(self.main_frame, bg=self.colors["bg"])
        gpu_box.pack(side=tk.LEFT, padx=2)

        gpu_lbl = tk.Label(gpu_box, text="GPU: 0% 0°C", font=font_style, bg=self.colors["bg"], fg=self.colors["gpu"])
        gpu_lbl.pack(side=tk.LEFT)

        gpu_bar = None
        if show_bars:
            gpu_bar = tk.Canvas(gpu_box, width=32, height=6, bg=self.colors["bar_bg"], highlightthickness=0)
            gpu_bar.pack(side=tk.LEFT, padx=(4, 0))

        self.widgets["gpu"] = (gpu_lbl, gpu_bar)

        # --- Detailed Mode Extra Blocks ---
        if mode == "detailed":
            create_sep()

            # Net Block
            net_box = tk.Frame(self.main_frame, bg=self.colors["bg"])
            net_box.pack(side=tk.LEFT, padx=2)

            net_lbl = tk.Label(net_box, text="NET: ↓0 KB/s", font=font_small, bg=self.colors["bg"], fg=self.colors["net"])
            net_lbl.pack(side=tk.LEFT)

            self.widgets["net"] = (net_lbl, None)

            create_sep()

            # Disk Block
            disk_box = tk.Frame(self.main_frame, bg=self.colors["bg"])
            disk_box.pack(side=tk.LEFT, padx=(2, 6))

            disk_lbl = tk.Label(disk_box, text="DISK: 0%", font=font_small, bg=self.colors["bg"], fg=self.colors["disk"])
            disk_lbl.pack(side=tk.LEFT)

            disk_bar = None
            if show_bars:
                disk_bar = tk.Canvas(disk_box, width=28, height=6, bg=self.colors["bar_bg"], highlightthickness=0)
                disk_bar.pack(side=tk.LEFT, padx=(4, 0))

            self.widgets["disk"] = (disk_lbl, disk_bar)
        else:
            # End padding for compact mode
            cpu_box.pack_configure(padx=(8, 2))
            gpu_box.pack_configure(padx=(2, 8))

        # Reserve room for the widest realistic readings once, so the window
        # keeps a fixed size while the numbers change.
        sizing_text = {
            "cpu": "CPU: 100%",
            "ram": "RAM: 100.0 GB",
            "gpu": "GPU: 100% 100°C",
            "net": "NET: ↓9999.9 MB/s",
            "disk": "DISK: 100%",
        }
        initial_text = {}
        for key, (lbl, _) in self.widgets.items():
            if key in sizing_text:
                initial_text[lbl] = lbl.cget("text")
                lbl.config(text=sizing_text[key])

        # Force geometry calculation
        self.root.update_idletasks()
        w = self.main_frame.winfo_reqwidth() + 10
        h = self.main_frame.winfo_reqheight() + 6
        self.win_width = max(w, 280)
        self.win_height = max(h, 30)

        # Restore the real initial texts; the geometry above stays fixed
        for lbl, text in initial_text.items():
            lbl.config(text=text)

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        cur_x = self.config.get("x")
        cur_y = self.config.get("y")
        if cur_x is None:
            cur_x = self.root.winfo_x() if self.root.winfo_ismapped() else max(0, screen_w - self.win_width - 40)
        if cur_y is None:
            cur_y = self.root.winfo_y() if self.root.winfo_ismapped() else max(0, screen_h - self.win_height - 80)

        self.root.geometry(f"{self.win_width}x{self.win_height}+{int(cur_x)}+{int(cur_y)}")
        self.apply_rounded_corners()

    def apply_rounded_corners(self):
        try:
            hwnd = self.root.winfo_id()
            region = ctypes.windll.gdi32.CreateRoundRectRgn(
                0, 0, self.win_width + 1, self.win_height + 1, 10, 10
            )
            if not ctypes.windll.user32.SetWindowRgn(hwnd, region, True):
                ctypes.windll.gdi32.DeleteObject(region)
        except Exception:
            pass

    def init_position(self):
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        x = self.config.get("x")
        y = self.config.get("y")

        if x is None or y is None or x < 0 or x > screen_w - 50 or y < 0 or y > screen_h - 50:
            x = max(0, screen_w - self.win_width - 40)
            y = max(0, screen_h - self.win_height - 80)

        self.config["x"] = x
        self.config["y"] = y
        self.root.geometry(f"{self.win_width}x{self.win_height}+{x}+{y}")

    def bind_events(self):
        self.root.bind("<Button-1>", self.start_move)
        self.root.bind("<B1-Motion>", self.do_move)
        self.root.bind("<ButtonRelease-1>", self.stop_move)
        self.root.bind("<Button-3>", self.show_menu)

    # --- Mouse Drag & Snapping ---
    def start_move(self, event):
        if self.config.get("locked", False):
            return
        self.offset_x = event.x_root - self.root.winfo_x()
        self.offset_y = event.y_root - self.root.winfo_y()

    def do_move(self, event):
        if self.config.get("locked", False):
            return

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        max_x = max(0, screen_w - self.win_width)
        max_y = max(0, screen_h - self.win_height)

        new_x = event.x_root - self.offset_x
        new_y = event.y_root - self.offset_y

        # Magnet Snap to screen edges (15px threshold)
        snap_margin = 15
        if abs(new_x) < snap_margin:
            new_x = 0
        elif abs(new_x - max_x) < snap_margin:
            new_x = max_x

        if abs(new_y) < snap_margin:
            new_y = 0
        elif abs(new_y - max_y) < snap_margin:
            new_y = max_y

        # Boundary clamping
        new_x = max(0, min(max_x, new_x))
        new_y = max(0, min(max_y, new_y))

        self.root.geometry(f"+{new_x}+{new_y}")

    def stop_move(self, event):
        if not self.config.get("locked", False):
            self.config["x"] = self.root.winfo_x()
            self.config["y"] = self.root.winfo_y()
            self.save_config()

    # --- Context Menu ---
    def show_menu(self, event):
        # Release menus from previous openings so they don't accumulate
        if getattr(self, "_menu", None) is not None:
            try:
                self._menu.destroy()
            except Exception:
                pass
            self._menu = None

        menu = tk.Menu(self.root, tearoff=0, bg=self.colors["card_bg"], fg=self.colors["text"],
                       activebackground=self.colors["sep"], activeforeground="#ffffff", bd=1)
        self._menu = menu

        # Persistent Tk variables to prevent Python garbage collection
        current_theme = self.config.get("theme", "Dark Neon")
        self.menu_theme_var = tk.StringVar(value=current_theme)

        theme_menu = tk.Menu(menu, tearoff=0, bg=self.colors["card_bg"], fg=self.colors["text"])
        for t_name in THEMES.keys():
            theme_menu.add_radiobutton(label=t_name, value=t_name,
                                       variable=self.menu_theme_var,
                                       command=lambda t=t_name: self.set_theme(t))
        menu.add_cascade(label="🎨 Темы оформления", menu=theme_menu)

        # Opacity Submenu
        curr_op = self.config.get("opacity", 0.95)
        self.menu_opacity_var = tk.DoubleVar(value=curr_op)
        opacity_menu = tk.Menu(menu, tearoff=0, bg=self.colors["card_bg"], fg=self.colors["text"])
        opacities = [("100%", 1.0), ("90%", 0.9), ("80%", 0.8), ("70%", 0.7), ("50%", 0.5)]
        for label, val in opacities:
            opacity_menu.add_radiobutton(label=label, value=val,
                                         variable=self.menu_opacity_var,
                                         command=lambda v=val: self.set_opacity(v))
        menu.add_cascade(label="👁️ Прозрачность", menu=opacity_menu)

        # Interval Submenu
        curr_int = self.config.get("interval", 2.0)
        self.menu_interval_var = tk.DoubleVar(value=curr_int)
        interval_menu = tk.Menu(menu, tearoff=0, bg=self.colors["card_bg"], fg=self.colors["text"])
        intervals = [("1 сек", 1.0), ("2 сек", 2.0), ("3 сек", 3.0), ("5 сек", 5.0)]
        for label, val in intervals:
            interval_menu.add_radiobutton(label=label, value=val,
                                          variable=self.menu_interval_var,
                                          command=lambda v=val: self.set_interval(v))
        menu.add_cascade(label="⏱️ Обновление", menu=interval_menu)

        # Mode Submenu
        curr_mode = self.config.get("mode", "compact")
        self.menu_mode_var = tk.StringVar(value=curr_mode)
        mode_menu = tk.Menu(menu, tearoff=0, bg=self.colors["card_bg"], fg=self.colors["text"])
        mode_menu.add_radiobutton(label="Компактный (CPU, RAM, GPU)", value="compact",
                                  variable=self.menu_mode_var,
                                  command=lambda: self.set_mode("compact"))
        mode_menu.add_radiobutton(label="Расширенный (+Сеть, Диск)", value="detailed",
                                  variable=self.menu_mode_var,
                                  command=lambda: self.set_mode("detailed"))
        menu.add_cascade(label="📊 Режим отображения", menu=mode_menu)

        menu.add_separator()

        # Options Checkboxes
        self.menu_show_bars_var = tk.BooleanVar(value=self.config.get("show_bars", True))
        menu.add_checkbutton(label="📊 Графические индикаторы", variable=self.menu_show_bars_var, command=self.toggle_bars)

        self.menu_locked_var = tk.BooleanVar(value=self.config.get("locked", False))
        menu.add_checkbutton(label="📌 Закрепить позицию", variable=self.menu_locked_var, command=self.toggle_locked)

        self.menu_autohide_var = tk.BooleanVar(value=self.config.get("autohide", True))
        menu.add_checkbutton(label="🙈 Скрывать в полноэкранном режиме", variable=self.menu_autohide_var, command=self.toggle_autohide)

        self.menu_autostart_var = tk.BooleanVar(value=self.is_autostart_enabled())
        menu.add_checkbutton(label="🚀 Автозапуск с Windows", variable=self.menu_autostart_var, command=self.toggle_autostart)

        menu.add_separator()

        # Quick Tools
        menu.add_command(label="💻 Диспетчер задач", command=self.open_task_manager)
        menu.add_command(label="🎯 Сбросить позицию", command=self.reset_position)

        menu.add_separator()
        menu.add_command(label="❌ Выход", command=self.quit_app)

        try:
            self.root.focus_force()
            menu.tk_popup(event.x_root, event.y_root)
        except tk.TclError:
            pass
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass

    # --- Menu Actions ---
    def set_theme(self, name):
        self.config["theme"] = name
        self.save_config()
        self.colors = THEMES[name]
        self.root.configure(bg=self.colors["bg"])
        self.main_frame.configure(bg=self.colors["bg"])
        self.setup_ui()

    def set_opacity(self, opacity):
        self.config["opacity"] = opacity
        self.root.attributes("-alpha", opacity)
        self.save_config()

    def set_interval(self, interval):
        self.config["interval"] = interval
        self.save_config()

    def set_mode(self, mode):
        self.config["mode"] = mode
        self.save_config()
        self.setup_ui()

    def toggle_bars(self):
        self.config["show_bars"] = not self.config.get("show_bars", True)
        self.save_config()
        self.setup_ui()

    def toggle_locked(self):
        self.config["locked"] = not self.config.get("locked", False)
        self.save_config()

    def toggle_autohide(self):
        new_val = not self.config.get("autohide", True)
        self.config["autohide"] = new_val
        self.save_config()
        if not new_val and self.is_hidden:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.apply_rounded_corners()
            self.is_hidden = False

    def is_autostart_enabled(self):
        if not WINREG_AVAILABLE:
            return False
        key = None
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
            value, _ = winreg.QueryValueEx(key, "SysMonitorPro")
            return bool(value)
        except Exception:
            return False
        finally:
            if key is not None:
                try:
                    winreg.CloseKey(key)
                except Exception:
                    pass

    def set_autostart(self, enable=True):
        if not WINREG_AVAILABLE:
            return False
        key = None
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            if enable:
                if getattr(sys, 'frozen', False):
                    cmd = f'"{sys.executable}"'
                else:
                    py_exec = sys.executable
                    if py_exec.endswith("python.exe"):
                        pyw = py_exec[:-10] + "pythonw.exe"
                        if os.path.exists(pyw):
                            py_exec = pyw
                    cmd = f'"{py_exec}" "{os.path.abspath(sys.argv[0])}"'
                winreg.SetValueEx(key, "SysMonitorPro", 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, "SysMonitorPro")
                except FileNotFoundError:
                    pass
            return True
        except Exception:
            return False
        finally:
            if key is not None:
                try:
                    winreg.CloseKey(key)
                except Exception:
                    pass

    def toggle_autostart(self):
        current_state = self.is_autostart_enabled()
        new_state = not current_state
        if self.set_autostart(new_state):
            self.config["autostart"] = new_state
            self.save_config()

    def open_task_manager(self):
        try:
            subprocess.Popen("taskmgr.exe")
        except Exception:
            pass

    def reset_position(self):
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, screen_w - self.win_width - 40)
        y = max(0, screen_h - self.win_height - 80)
        self.config["x"] = x
        self.config["y"] = y
        self.root.geometry(f"+{x}+{y}")
        self.save_config()

    def quit_app(self):
        if not self.running:
            return
        self.running = False
        if self._queue_after_id is not None:
            try:
                self.root.after_cancel(self._queue_after_id)
            except Exception:
                pass
            self._queue_after_id = None
        if NVML_AVAILABLE:
            try:
                if self.gpu_handle is not None:
                    pynvml.nvmlShutdown()
            except Exception:
                pass
        self.gpu_handle = None
        # Destroy the window after the context menu has fully closed. Destroying
        # it directly inside a menu command aborts tk_popup and raises TclError.
        try:
            self.root.after(100, self._destroy_root)
        except tk.TclError:
            pass

    def _destroy_root(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def is_fullscreen(self):
        if not WIN32_AVAILABLE or not self.config.get("autohide", True):
            return False
        try:
            hwnd = GetForegroundWindow()
            if not hwnd or IsIconic(hwnd):
                return False
            cls = GetClassName(hwnd)
            ignored_classes = [
                "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", 
                "FolderView", "DV2ControlHost", "Windows.UI.Core.CoreWindow", 
                "MultitaskingViewFrame", "SearchHost", "StartMenuExperienceHost"
            ]
            if cls in ignored_classes:
                return False

            style = GetWindowLong(hwnd, win32con.GWL_STYLE)
            WS_CAPTION = 0x00C00000
            if (style & WS_CAPTION) == WS_CAPTION:
                return False

            rect = GetWindowRect(hwnd)
            w, h = rect[2] - rect[0], rect[3] - rect[1]
            return w >= GetSystemMetrics(0) and h >= GetSystemMetrics(1)
        except Exception:
            return False

    # --- Background Data Collection Thread ---
    def start_worker_thread(self):
        def worker():
            while self.running:
                try:
                    # 1. Fullscreen check
                    fullscreen = self.is_fullscreen()

                    # 2. CPU Usage
                    cpu = int(round(psutil.cpu_percent(interval=None)))

                    # 3. RAM Usage
                    ram_info = psutil.virtual_memory()
                    ram_used_gb = ram_info.used / (1024 ** 3)
                    ram_pct = int(round(ram_info.percent))

                    # 4. GPU Usage & Temp
                    gpu_pct = 0
                    gpu_temp = 0
                    gpu_ok = False
                    if self.gpu_handle:
                        try:
                            util = pynvml.nvmlDeviceGetUtilizationRates(self.gpu_handle)
                            gpu_temp = pynvml.nvmlDeviceGetTemperature(self.gpu_handle, pynvml.NVML_TEMPERATURE_GPU)
                            gpu_pct = util.gpu
                            gpu_ok = True
                        except Exception:
                            gpu_ok = False

                    # 5. Network Speed
                    now = time.time()
                    dt = now - self.prev_net_time
                    net_io = psutil.net_io_counters()
                    dl_speed = 0
                    ul_speed = 0
                    if dt > 0 and net_io is not None and self.prev_net_io is not None:
                        # Counters can reset when adapters change - never show negative speed
                        dl_speed = max(0.0, (net_io.bytes_recv - self.prev_net_io.bytes_recv) / dt)
                        ul_speed = max(0.0, (net_io.bytes_sent - self.prev_net_io.bytes_sent) / dt)
                    if net_io is not None:
                        self.prev_net_io = net_io
                    self.prev_net_time = now

                    # 6. Disk Usage (system drive, not the process working directory)
                    try:
                        system_drive = os.environ.get("SystemDrive", "C:") + "\\"
                        disk_pct = int(round(psutil.disk_usage(system_drive).percent))
                    except Exception:
                        disk_pct = int(round(psutil.disk_usage("/").percent))

                    payload = {
                        "fullscreen": fullscreen,
                        "cpu": cpu,
                        "ram_gb": ram_used_gb,
                        "ram_pct": ram_pct,
                        "gpu_ok": gpu_ok,
                        "gpu_pct": gpu_pct,
                        "gpu_temp": gpu_temp,
                        "dl_speed": dl_speed,
                        "ul_speed": ul_speed,
                        "disk_pct": disk_pct
                    }

                    self.update_queue.put(payload)
                except Exception:
                    pass

                target_interval = float(self.config.get("interval", 2.0))
                elapsed = 0.0
                while self.running and elapsed < target_interval:
                    time.sleep(0.1)
                    elapsed += 0.1
                    target_interval = float(self.config.get("interval", 2.0))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    # --- Main-Thread Queue Consumer ---
    def process_queue(self):
        self._queue_after_id = None
        if not self.running:
            return
        try:
            while not self.update_queue.empty():
                data = self.update_queue.get_nowait()
                self.render_data(data)
        except Exception:
            pass

        if self.running:
            try:
                self._queue_after_id = self.root.after(200, self.process_queue)
            except tk.TclError:
                self._queue_after_id = None

    # --- Render Update UI ---
    def render_data(self, data):
        # Handle Fullscreen Visibility
        if data["fullscreen"]:
            if not self.is_hidden:
                self.root.withdraw()
                self.is_hidden = True
            return
        else:
            if self.is_hidden:
                self.root.deiconify()
                self.root.attributes("-topmost", True)
                self.is_hidden = False

        # Helper for color severity warnings
        def get_severity_color(val, default_color, warn_thresh=70, crit_thresh=85):
            if val >= crit_thresh:
                return "#FF3333"  # Red alert
            elif val >= warn_thresh:
                return "#FFA500"  # Orange warning
            return default_color

        # Helper to draw mini progress bar on Tk Canvas
        def update_bar(canvas, pct, color):
            if not canvas:
                return
            canvas.delete("all")
            w = canvas.winfo_width()
            h = canvas.winfo_height()
            if w <= 1:
                try:
                    w = int(canvas.cget("width"))
                except Exception:
                    w = 32
            if h <= 1:
                try:
                    h = int(canvas.cget("height"))
                except Exception:
                    h = 6
            pct = max(0.0, min(100.0, float(pct)))
            if pct <= 0:
                return
            fill_w = max(1, int(round((pct / 100.0) * w)))
            canvas.create_rectangle(0, 0, fill_w, h, fill=color, width=0)

        # Update CPU
        if "cpu" in self.widgets:
            cpu_lbl, cpu_bar = self.widgets["cpu"]
            c_val = data["cpu"]
            c_color = get_severity_color(c_val, self.colors["cpu"])
            cpu_lbl.config(text=f"CPU: {c_val}%", fg=c_color)
            update_bar(cpu_bar, c_val, c_color)

        # Update RAM
        if "ram" in self.widgets:
            ram_lbl, ram_bar = self.widgets["ram"]
            r_gb = data["ram_gb"]
            r_pct = data["ram_pct"]
            r_color = get_severity_color(r_pct, self.colors["ram"])
            ram_lbl.config(text=f"RAM: {r_gb:.1f} GB", fg=r_color)
            update_bar(ram_bar, r_pct, r_color)

        # Update GPU
        if "gpu" in self.widgets:
            gpu_lbl, gpu_bar = self.widgets["gpu"]
            if data["gpu_ok"]:
                g_pct = data["gpu_pct"]
                g_temp = data["gpu_temp"]
                g_color = get_severity_color(max(g_pct, g_temp), self.colors["gpu"], 75, 85)
                gpu_lbl.config(text=f"GPU: {g_pct}% {g_temp}°C", fg=g_color)
                update_bar(gpu_bar, g_pct, g_color)
            else:
                gpu_lbl.config(text="GPU: N/A", fg=self.colors["gpu"])
                update_bar(gpu_bar, 0, self.colors["gpu"])

        # Update Net (if detailed mode)
        if "net" in self.widgets:
            net_lbl, _ = self.widgets["net"]
            dl = data["dl_speed"]
            if dl > 1024 * 1024:
                net_text = f"↓{dl / (1024 * 1024):.1f} MB/s"
            else:
                net_text = f"↓{int(dl / 1024)} KB/s"
            net_lbl.config(text=f"NET: {net_text}")

        # Update Disk (if detailed mode)
        if "disk" in self.widgets:
            disk_lbl, disk_bar = self.widgets["disk"]
            d_pct = data["disk_pct"]
            d_color = get_severity_color(d_pct, self.colors["disk"])
            disk_lbl.config(text=f"DISK: {d_pct}%", fg=d_color)
            update_bar(disk_bar, d_pct, d_color)


if __name__ == "__main__":
    root = tk.Tk()
    app = SystemMonitor(root)
    root.mainloop()
