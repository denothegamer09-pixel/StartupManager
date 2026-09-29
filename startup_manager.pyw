# -*- coding: utf-8 -*-
"""
Менеджер автозагрузки Windows.

Возможности:
• Встроенные источники: Run-ключи HKCU/HKLM, папки «Автозагрузка».
• Пользовательские источники: произвольные папки и ветки реестра.
• Включение/отключение записей, удаление с резервной копией или окончательно.
• Просмотр и восстановление резервных копий.
• Отслеживание новых записей, подсветка и уведомление Windows.
• Тёмная/светлая тема, размер шрифта, скрытие системных программ.
• Сворачивание в трей (WinAPI, без внешних зависимостей).
• Экспорт/импорт конфигурации.
"""

import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import os
import shutil
import subprocess
import tempfile
import uuid
import winreg
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox


# ---------- Пути и константы ----------

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "StartupManagerPy"
CONFIG_FILE = APP_DIR / "disabled.json"
SETTINGS_FILE = APP_DIR / "settings.json"
SNAPSHOT_FILE = APP_DIR / "snapshot.json"
CUSTOM_SOURCES_FILE = APP_DIR / "custom_sources.json"
BACKUPS_FILE = APP_DIR / "backups.json"
DISABLED_FILES_DIR = APP_DIR / "disabled_files"
BACKUPS_FILES_DIR = APP_DIR / "backups_files"

REG_SOURCES = {
    "HKCU":   (winreg.HKEY_CURRENT_USER,
               r"Software\Microsoft\Windows\CurrentVersion\Run"),
    "HKLM":   (winreg.HKEY_LOCAL_MACHINE,
               r"Software\Microsoft\Windows\CurrentVersion\Run"),
    "HKLM32": (winreg.HKEY_LOCAL_MACHINE,
               r"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Run"),
}

FOLDER_SOURCES = {
    "UserStartup":   Path(os.environ.get("APPDATA", ""))
                     / r"Microsoft\Windows\Start Menu\Programs\Startup",
    "CommonStartup": Path(os.environ.get("ProgramData", ""))
                     / r"Microsoft\Windows\Start Menu\Programs\Startup",
}

SOURCE_LABELS = {
    "HKCU":          "Реестр HKCU",
    "HKLM":          "Реестр HKLM",
    "HKLM32":        "Реестр HKLM (32-bit)",
    "UserStartup":   "Автозагрузка (пользователь)",
    "CommonStartup": "Автозагрузка (все пользователи)",
}

CHECK_ON  = "\u2611"
CHECK_OFF = "\u2610"
NEW_MARK  = "\U0001F195"

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------- Настройки ----------

DEFAULT_SETTINGS = {
    "theme": "light",
    "font_size": 10,
    "hide_system": False,
    "confirm_delete": True,
    "highlight_new": True,
    "notify_new": True,
    "minimize_to_tray": True,
    "tray_on_minimize": False,
    "window_geometry": "1000x600",
}


def load_settings() -> dict:
    result = dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for k in DEFAULT_SETTINGS:
                if k in data:
                    result[k] = data[k]
    except (OSError, ValueError):
        pass
    return result


def save_settings(s: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- Темы ----------

THEMES = {
    "light": {
        "bg":            "#f0f0f0",
        "fg":            "#1a1a1a",
        "field_bg":      "#ffffff",
        "field_fg":      "#1a1a1a",
        "select_bg":     "#0078d7",
        "select_fg":     "#ffffff",
        "tree_bg":       "#ffffff",
        "tree_fg":       "#1a1a1a",
        "tree_disabled": "#909090",
        "new_fg":        "#b34700",
        "button_bg":     "#e6e6e6",
        "button_active": "#d4d4d4",
        "border":        "#b0b0b0",
        "menu_bg":       "#f0f0f0",
    },
    "dark": {
        "bg":            "#2b2b2b",
        "fg":            "#e6e6e6",
        "field_bg":      "#3c3c3c",
        "field_fg":      "#e6e6e6",
        "select_bg":     "#0e639c",
        "select_fg":     "#ffffff",
        "tree_bg":       "#252525",
        "tree_fg":       "#e6e6e6",
        "tree_disabled": "#7a7a7a",
        "new_fg":        "#ffb84d",
        "button_bg":     "#3c3c3c",
        "button_active": "#505050",
        "border":        "#555555",
        "menu_bg":       "#333333",
    },
}


def apply_theme(root: tk.Misc, settings: dict) -> None:
    c = THEMES[settings["theme"]]
    size = int(settings["font_size"])

    for fname in ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                  "TkHeadingFont", "TkTooltipFont", "TkFixedFont"):
        try:
            tkfont.nametofont(fname).configure(size=size)
        except tk.TclError:
            pass

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".",
                    background=c["bg"], foreground=c["fg"],
                    fieldbackground=c["field_bg"],
                    bordercolor=c["border"],
                    lightcolor=c["bg"], darkcolor=c["bg"],
                    troughcolor=c["bg"], focuscolor=c["select_bg"])
    style.configure("TFrame", background=c["bg"])
    style.configure("TLabel", background=c["bg"], foreground=c["fg"])
    style.configure("TSeparator", background=c["border"])
    style.configure("TButton",
                    background=c["button_bg"], foreground=c["fg"],
                    borderwidth=1, focusthickness=0, padding=(10, 4))
    style.map("TButton",
              background=[("active", c["button_active"]),
                          ("pressed", c["select_bg"]),
                          ("disabled", c["bg"])],
              foreground=[("disabled", c["tree_disabled"])])
    style.configure("TCheckbutton",
                    background=c["bg"], foreground=c["fg"],
                    focusthickness=0)
    style.map("TCheckbutton",
              background=[("active", c["bg"])],
              foreground=[("disabled", c["tree_disabled"])])
    style.configure("TRadiobutton", background=c["bg"], foreground=c["fg"])
    style.configure("TEntry",
                    fieldbackground=c["field_bg"], foreground=c["field_fg"],
                    insertcolor=c["fg"], bordercolor=c["border"])
    style.map("TEntry",
              fieldbackground=[("readonly", c["field_bg"])],
              foreground=[("readonly", c["field_fg"])])
    style.configure("TCombobox",
                    fieldbackground=c["field_bg"], foreground=c["field_fg"],
                    background=c["button_bg"], arrowcolor=c["fg"],
                    bordercolor=c["border"], padding=(4, 2))
    style.map("TCombobox",
              fieldbackground=[("readonly", c["field_bg"])],
              foreground=[("readonly", c["field_fg"])],
              background=[("active", c["button_active"])])
    style.configure("TSpinbox",
                    fieldbackground=c["field_bg"], foreground=c["field_fg"],
                    background=c["button_bg"], arrowcolor=c["fg"],
                    bordercolor=c["border"])
    style.configure("Treeview",
                    background=c["tree_bg"], fieldbackground=c["tree_bg"],
                    foreground=c["tree_fg"], bordercolor=c["border"],
                    rowheight=int(size * 2.2 + 2))
    style.map("Treeview",
              background=[("selected", c["select_bg"])],
              foreground=[("selected", c["select_fg"])])
    style.configure("Treeview.Heading",
                    background=c["button_bg"], foreground=c["fg"],
                    relief="flat", padding=(6, 4))
    style.map("Treeview.Heading",
              background=[("active", c["button_active"])])
    style.configure("Vertical.TScrollbar",
                    background=c["button_bg"], troughcolor=c["bg"],
                    bordercolor=c["bg"], arrowcolor=c["fg"],
                    lightcolor=c["button_bg"], darkcolor=c["button_bg"])
    style.map("Vertical.TScrollbar",
              background=[("active", c["button_active"])])

    try:
        root.configure(bg=c["bg"])
    except tk.TclError:
        pass


def style_menu(menu: tk.Menu, settings: dict) -> None:
    c = THEMES[settings["theme"]]
    try:
        menu.configure(bg=c["menu_bg"], fg=c["fg"],
                       activebackground=c["select_bg"],
                       activeforeground=c["select_fg"],
                       bd=0, relief="flat")
    except tk.TclError:
        pass


# ---------- Утилиты ----------

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def hide_console() -> None:
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32")
        user32 = ctypes.WinDLL("user32")
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def is_process_running(image_name: str) -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/NH"],
            capture_output=True, text=True,
            creationflags=CREATE_NO_WINDOW, timeout=5)
        return image_name.lower() in (out.stdout or "").lower()
    except Exception:
        return False


# ---------- Модель записи ----------

@dataclass
class StartupEntry:
    name: str
    command: str
    source: str
    enabled: bool = True
    path: str = ""
    stored_path: str = ""


def entry_sig(e: StartupEntry) -> str:
    return f"{e.source}|{e.name}|{e.command}"


# ---------- Пользовательские источники ----------

def load_custom_sources() -> list:
    try:
        data = json.loads(CUSTOM_SOURCES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(data, dict) and isinstance(data.get("sources"), list):
        out = []
        for s in data["sources"]:
            if (isinstance(s, dict) and s.get("id")
                    and s.get("type") in ("folder", "registry")
                    and s.get("path")):
                out.append(s)
        return out
    return []


def save_custom_sources(items: list) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CUSTOM_SOURCES_FILE.write_text(
        json.dumps({"sources": items}, ensure_ascii=False, indent=2),
        encoding="utf-8")


def find_custom_source(sid: str):
    for s in load_custom_sources():
        if s["id"] == sid:
            return s
    return None


def custom_source_id_from_tag(source: str):
    if source.startswith("cf:"):
        return source[3:]
    if source.startswith("cr:"):
        return source[3:]
    return None


def get_reg_source(source: str):
    if source in REG_SOURCES:
        return REG_SOURCES[source]
    sid = custom_source_id_from_tag(source)
    if sid and source.startswith("cr:"):
        cs = find_custom_source(sid)
        if cs and cs.get("type") == "registry":
            hive = cs.get("hive", "HKCU")
            root = (winreg.HKEY_CURRENT_USER if hive == "HKCU"
                    else winreg.HKEY_LOCAL_MACHINE)
            return (root, cs.get("path", ""))
    return None


def get_folder_source(source: str):
    if source in FOLDER_SOURCES:
        return FOLDER_SOURCES[source]
    sid = custom_source_id_from_tag(source)
    if sid and source.startswith("cf:"):
        cs = find_custom_source(sid)
        if cs and cs.get("type") == "folder":
            p = cs.get("path", "")
            if p:
                return Path(p)
    return None


def is_registry_source(source: str) -> bool:
    return get_reg_source(source) is not None


def is_folder_source(source: str) -> bool:
    return get_folder_source(source) is not None


def get_source_label(source: str) -> str:
    if source in SOURCE_LABELS:
        return SOURCE_LABELS[source]
    sid = custom_source_id_from_tag(source)
    if sid:
        cs = find_custom_source(sid)
        if cs:
            return cs.get("label") or cs.get("path") or source
    return source


def all_known_source_tags() -> set:
    tags = set(REG_SOURCES) | set(FOLDER_SOURCES)
    for s in load_custom_sources():
        prefix = "cr:" if s["type"] == "registry" else "cf:"
        tags.add(prefix + s["id"])
    return tags


def parse_registry_path(s: str):
    s = s.strip()
    up = s.upper()
    for name, short in (("HKEY_CURRENT_USER", "HKCU"),
                        ("HKEY_LOCAL_MACHINE", "HKLM"),
                        ("HKCU", "HKCU"),
                        ("HKLM", "HKLM")):
        if up.startswith(name):
            rest = s[len(name):].lstrip("\\/").strip()
            if rest:
                return short, rest
    return None


# ---------- Отключённые записи ----------

def load_disabled() -> list:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_disabled(items: list) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def add_disabled_item(name, command, source, path="", stored_path=""):
    items = [i for i in load_disabled()
             if not (i["source"] == source and i["name"] == name)]
    items.append({"name": name, "command": command, "source": source,
                  "path": path, "stored_path": stored_path})
    save_disabled(items)


def remove_disabled_item(source, name):
    items = [i for i in load_disabled()
             if not (i["source"] == source and i["name"] == name)]
    save_disabled(items)


# ---------- Резервные копии ----------

def load_backups() -> list:
    try:
        data = json.loads(BACKUPS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_backups(items: list) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    BACKUPS_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _backup_id(item: dict) -> str:
    return (f'{item.get("deleted_at","")}|'
            f'{item.get("source","")}|{item.get("name","")}')


def backup_entry(e: StartupEntry) -> dict:
    """Удаляет запись из автозагрузки, сохраняя данные для восстановления."""
    if is_registry_source(e.source):
        reg = get_reg_source(e.source)
        root, path = reg
        try:
            with winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, e.name)
        except FileNotFoundError:
            pass
        info = {
            "name": e.name, "command": e.command, "source": e.source,
            "type": "registry", "path": "", "stored_path": "",
        }
    else:
        BACKUPS_FILES_DIR.mkdir(parents=True, exist_ok=True)
        src = Path(e.path if e.enabled else (e.stored_path or e.path))
        prefix = e.source.replace(":", "_")
        dst = BACKUPS_FILES_DIR / f"{prefix}__{src.name}"
        n = 1
        while dst.exists():
            dst = BACKUPS_FILES_DIR / f"{prefix}__{n}__{src.name}"
            n += 1
        shutil.move(str(src), str(dst))
        info = {
            "name": e.name, "command": e.command, "source": e.source,
            "type": "file", "path": str(src), "stored_path": str(dst),
        }

    info["deleted_at"] = datetime.now().isoformat(timespec="seconds")
    items = load_backups()
    items.append(info)
    save_backups(items)

    # чистим метаданные отключённой записи, если она была отключена
    if not e.enabled:
        remove_disabled_item(e.source, e.name)

    return info


def restore_backup(item: dict) -> None:
    if item.get("type") == "registry":
        reg = get_reg_source(item["source"])
        if reg is None:
            raise RuntimeError(
                f'источник «{item["source"]}» больше не существует — '
                f'добавьте его заново в «Управлении источниками»')
        root, path = reg
        with winreg.CreateKeyEx(root, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, item["name"], 0, winreg.REG_SZ,
                              item["command"])
    else:
        src = Path(item["stored_path"])
        dst = Path(item["path"])
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))

    my_id = _backup_id(item)
    save_backups([x for x in load_backups() if _backup_id(x) != my_id])


def purge_backup(item: dict) -> None:
    if item.get("type") == "file" and item.get("stored_path"):
        try:
            os.remove(item["stored_path"])
        except OSError:
            pass
    my_id = _backup_id(item)
    save_backups([x for x in load_backups() if _backup_id(x) != my_id])


def purge_all_backups() -> None:
    for item in load_backups():
        purge_backup(item)
    if BACKUPS_FILES_DIR.exists():
        shutil.rmtree(BACKUPS_FILES_DIR, ignore_errors=True)


# ---------- Снимок автозагрузки (для отслеживания новых) ----------

def load_snapshot() -> tuple:
    if not SNAPSHOT_FILE.exists():
        return None, set()
    try:
        data = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, set()
    if not isinstance(data, dict):
        return None, set()
    known = data.get("known")
    new = data.get("new", [])
    if not isinstance(known, list):
        return None, set()
    return (set(known),
            set(new) if isinstance(new, list) else set())


def save_snapshot(known: set, new: set) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_FILE.write_text(
        json.dumps({"known": sorted(known), "new": sorted(new)},
                   ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- Windows toast ----------

def show_windows_toast(title: str, message: str, timeout: int = 10) -> bool:
    if os.name != "nt":
        return False

    def esc_xml(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    def esc_ps(s):
        return esc_xml(s).replace("'", "''")

    ps = (
        '$ErrorActionPreference="Stop";try{'
        '[Windows.UI.Notifications.ToastNotificationManager,'
        'Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;'
        '[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,'
        'ContentType=WindowsRuntime]|Out-Null;'
        f"$t='<toast><visual><binding template=\"ToastGeneric\">"
        f"<text>{esc_ps(title)}</text><text>{esc_ps(message)}</text>"
        "</binding></visual></toast>';"
        '$x=New-Object Windows.Data.Xml.Dom.XmlDocument;$x.LoadXml($t);'
        '$n=New-Object Windows.UI.Notifications.ToastNotification $x;'
        '[Windows.UI.Notifications.ToastNotificationManager]::'
        'CreateToastNotifier("Startup Manager").Show($n);'
        '}catch{exit 1}'
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-WindowStyle", "Hidden", "-Command", ps],
            creationflags=CREATE_NO_WINDOW, timeout=timeout, capture_output=True)
        return r.returncode == 0
    except Exception:
        return False


# ---------- Трей ----------

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                   ctypes.c_void_p, ctypes.c_void_p]
_user32.DefWindowProcW.restype = ctypes.c_ssize_t
_user32.CreateWindowExW.restype = ctypes.c_void_p
_user32.LoadIconW.restype = ctypes.c_void_p
_user32.RegisterClassW.restype = ctypes.c_ushort
_user32.RegisterWindowMessageW.argtypes = [ctypes.c_wchar_p]
_user32.RegisterWindowMessageW.restype = ctypes.c_uint
_kernel32.GetModuleHandleW.restype = ctypes.c_void_p

WM_TRAYICON = 0x0400 + 20
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 1, 2, 4, 0x10
NIIF_INFO, NIIF_WARNING = 1, 2
IDI_APPLICATION = 32512


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("hWnd", ctypes.c_void_p),
        ("uID", ctypes.c_uint32),
        ("uFlags", ctypes.c_uint32),
        ("uCallbackMessage", ctypes.c_uint32),
        ("hIcon", ctypes.c_void_p),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", ctypes.c_uint32),
        ("dwStateMask", ctypes.c_uint32),
        ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", ctypes.c_uint32),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", ctypes.c_uint32),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", ctypes.c_void_p),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p,
                             ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
    ]


class TrayIcon:
    def __init__(self, root, tooltip, on_show, on_menu, on_exit):
        self.root = root
        self.tooltip = tooltip
        self.on_show = on_show
        self.on_menu = on_menu
        self.on_exit = on_exit
        self.hwnd = None
        self._nid = None
        self._class_name = f"PyStartupMgrTray_{id(self)}"
        self._wndproc = WNDPROC(self._proc)
        self._taskbar_created = _user32.RegisterWindowMessageW("TaskbarCreated")

    def install(self) -> None:
        hinst = _kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = self._class_name
        wc.hIcon = _user32.LoadIconW(None, IDI_APPLICATION)
        if not _user32.RegisterClassW(ctypes.byref(wc)):
            raise OSError("RegisterClassW: " +
                          ctypes.FormatError(ctypes.get_last_error()))
        self.hwnd = _user32.CreateWindowExW(
            0, self._class_name, self._class_name, 0,
            0, 0, 0, 0, None, None, hinst, None)
        if not self.hwnd:
            raise OSError("CreateWindowExW: " +
                          ctypes.FormatError(ctypes.get_last_error()))

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = _user32.LoadIconW(None, IDI_APPLICATION)
        nid.szTip = (self.tooltip or "")[:127]
        if not _shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            raise OSError("Shell_NotifyIcon NIM_ADD failed")
        self._nid = nid

    def remove(self) -> None:
        if self._nid is not None:
            try:
                _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            except Exception:
                pass
            self._nid = None
        if self.hwnd:
            try:
                _user32.DestroyWindow(self.hwnd)
            except Exception:
                pass
            self.hwnd = None

    def notify(self, title: str, message: str, warning: bool = False) -> bool:
        if self._nid is None:
            return False
        nid = self._nid
        nid.uFlags = NIF_INFO
        nid.szInfoTitle = (title or "")[:63]
        nid.szInfo = (message or "")[:255]
        nid.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
        nid.uVersion = 10000
        ok = bool(_shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))
        self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        return ok

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAYICON:
            if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                try:
                    self.root.after(0, self.on_show)
                except Exception:
                    pass
                return 0
            if lparam == WM_RBUTTONUP:
                try:
                    self.root.after(0, self.on_menu)
                except Exception:
                    pass
                return 0
        if self._taskbar_created and msg == self._taskbar_created:
            try:
                _shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid))
            except Exception:
                pass
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)


# ---------- Чтение записей ----------

def read_registry_values(root, path):
    result = []
    try:
        key = winreg.OpenKey(root, path, 0, winreg.KEY_READ)
    except OSError:
        return result
    with key:
        i = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
            except OSError:
                break
            if name:
                result.append((name, str(value)))
            i += 1
    return result


def collect_entries() -> list:
    entries = []

    for src, (root, path) in REG_SOURCES.items():
        for name, value in read_registry_values(root, path):
            entries.append(StartupEntry(name=name, command=value, source=src))

    for src, folder in FOLDER_SOURCES.items():
        if folder.is_dir():
            for f in sorted(folder.iterdir()):
                if f.is_file():
                    entries.append(StartupEntry(
                        name=f.stem, command=str(f), source=src, path=str(f)))

    for cs in load_custom_sources():
        sid = cs["id"]
        if cs["type"] == "registry":
            tag = "cr:" + sid
            root = (winreg.HKEY_CURRENT_USER
                    if cs.get("hive", "HKCU") == "HKCU"
                    else winreg.HKEY_LOCAL_MACHINE)
            for name, value in read_registry_values(root, cs.get("path", "")):
                entries.append(StartupEntry(name=name, command=value,
                                            source=tag))
        else:
            tag = "cf:" + sid
            folder = Path(cs.get("path", ""))
            if folder.is_dir():
                try:
                    for f in sorted(folder.iterdir()):
                        if f.is_file():
                            entries.append(StartupEntry(
                                name=f.stem, command=str(f),
                                source=tag, path=str(f)))
                except OSError:
                    pass

    valid = all_known_source_tags()
    for item in load_disabled():
        if item.get("source") not in valid:
            continue
        entries.append(StartupEntry(
            name=item["name"], command=item["command"], source=item["source"],
            enabled=False, path=item.get("path", ""),
            stored_path=item.get("stored_path", "")))

    return entries


def is_system_entry(e: StartupEntry) -> bool:
    if e.source in ("HKLM", "HKLM32", "CommonStartup"):
        return True
    lower = e.command.lower()
    for r in (os.environ.get("SystemRoot", r"C:\Windows"),
              os.environ.get("ProgramFiles", r"C:\Program Files"),
              os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        if r and r.lower() in lower:
            return True
    return False


# ---------- Отключение / включение / удаление ----------

def disable_entry(e: StartupEntry):
    reg = get_reg_source(e.source)
    if reg is not None:
        root, path = reg
        with winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, e.name)
        add_disabled_item(e.name, e.command, e.source)
    else:
        DISABLED_FILES_DIR.mkdir(parents=True, exist_ok=True)
        src = Path(e.path)
        prefix = e.source.replace(":", "_")
        dst = DISABLED_FILES_DIR / f"{prefix}__{src.name}"
        n = 1
        while dst.exists():
            dst = DISABLED_FILES_DIR / f"{prefix}__{n}__{src.name}"
            n += 1
        shutil.move(str(src), str(dst))
        add_disabled_item(e.name, e.command, e.source, str(src), str(dst))


def enable_entry(e: StartupEntry):
    reg = get_reg_source(e.source)
    if reg is not None:
        root, path = reg
        with winreg.CreateKeyEx(root, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, e.name, 0, winreg.REG_SZ, e.command)
        remove_disabled_item(e.source, e.name)
    else:
        src = Path(e.stored_path)
        dst = Path(e.path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        remove_disabled_item(e.source, e.name)


def delete_entry(e: StartupEntry):
    reg = get_reg_source(e.source)
    if e.enabled:
        if reg is not None:
            root, path = reg
            with winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, e.name)
        else:
            os.remove(e.path)
    else:
        if reg is None and e.stored_path:
            try:
                os.remove(e.stored_path)
            except OSError:
                pass
        remove_disabled_item(e.source, e.name)


# ---------- Пути к файлу / ключу реестра ----------

def entry_registry_key_path(e: StartupEntry) -> str:
    reg = get_reg_source(e.source)
    if reg is None:
        return ""
    root, sub = reg
    hive = ("HKEY_CURRENT_USER" if root == winreg.HKEY_CURRENT_USER
            else "HKEY_LOCAL_MACHINE")
    return f"{hive}\\{sub}"


def entry_file_path(e: StartupEntry) -> str:
    return e.path if e.enabled else (e.stored_path or e.path)


def entry_display_path(e: StartupEntry) -> str:
    if is_registry_source(e.source):
        return entry_registry_key_path(e)
    return entry_file_path(e)


# ---------- Открытие в проводнике / regedit ----------

def open_regedit_at(key_path: str) -> None:
    if is_process_running("regedit.exe"):
        try:
            subprocess.run(["taskkill", "/IM", "regedit.exe", "/F"],
                           capture_output=True,
                           creationflags=CREATE_NO_WINDOW, timeout=10)
        except Exception:
            pass
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Applets\Regedit",
            0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "LastKey", 0, winreg.REG_SZ, key_path)
    except OSError:
        pass
    try:
        subprocess.Popen(["regedit"])
    except OSError as ex:
        messagebox.showerror("Ошибка", f"Не удалось запустить regedit:\n{ex}")


def open_in_explorer(path: str) -> None:
    if not path:
        return
    if os.path.exists(path):
        subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
        return
    parent = os.path.dirname(path)
    if parent and os.path.isdir(parent):
        os.startfile(parent)
    else:
        messagebox.showinfo("Не найдено",
                            f"Файл или папка не найдены:\n{path}")


def open_entry_location(e: StartupEntry) -> None:
    if is_registry_source(e.source):
        open_regedit_at(entry_registry_key_path(e))
    else:
        open_in_explorer(entry_file_path(e))


# ---------- Добавление в автозагрузку ----------

def add_registry_entry(name: str, command: str):
    root, path = REG_SOURCES["HKCU"]
    with winreg.CreateKeyEx(root, path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)


def create_shortcut(lnk_path: str, target: str, arguments: str = ""):
    def esc(s):
        return str(s).replace('"', '""')

    vbs = (
        'Set sh = CreateObject("WScript.Shell")\n'
        f'Set lnk = sh.CreateShortcut("{esc(lnk_path)}")\n'
        f'lnk.TargetPath = "{esc(target)}"\n'
        f'lnk.Arguments = "{esc(arguments)}"\n'
        f'lnk.WorkingDirectory = "{esc(str(Path(target).parent))}"\n'
        'lnk.Save\n'
    )
    fd, tmp = tempfile.mkstemp(suffix=".vbs")
    os.close(fd)
    try:
        Path(tmp).write_text(vbs, encoding="utf-16")
        subprocess.run(["wscript", "//nologo", tmp], check=True, timeout=15,
                       creationflags=CREATE_NO_WINDOW)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def add_startup_shortcut(name: str, target: str, arguments: str = ""):
    folder = FOLDER_SOURCES["UserStartup"]
    folder.mkdir(parents=True, exist_ok=True)
    create_shortcut(str(folder / f"{name}.lnk"), target, arguments)


# ---------- Диалог "Добавить программу" ----------

class AddDialog(tk.Toplevel):
    def __init__(self, master, on_done):
        super().__init__(master)
        self.title("Добавить в автозагрузку")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.on_done = on_done

        c = THEMES[master.settings["theme"]]
        self.configure(bg=c["bg"])

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Название:").grid(row=0, column=0, sticky="w", pady=4)
        self.name_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.name_var, width=52).grid(
            row=0, column=1, columnspan=2, sticky="we", pady=4)

        ttk.Label(frm, text="Программа:").grid(row=1, column=0, sticky="w", pady=4)
        self.path_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.path_var, width=42).grid(
            row=1, column=1, sticky="we", pady=4)
        ttk.Button(frm, text="Обзор…", command=self.browse).grid(
            row=1, column=2, sticky="w", padx=(6, 0), pady=4)

        ttk.Label(frm, text="Аргументы:").grid(row=2, column=0, sticky="w", pady=4)
        self.args_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.args_var, width=52).grid(
            row=2, column=1, columnspan=2, sticky="we", pady=4)

        ttk.Label(frm, text="Куда добавить:").grid(row=3, column=0, sticky="w", pady=4)
        self.place_var = tk.StringVar(value="Реестр (HKCU)")
        ttk.Combobox(
            frm, textvariable=self.place_var, state="readonly",
            values=["Реестр (HKCU)", "Папка автозагрузки (пользователь)"],
        ).grid(row=3, column=1, columnspan=2, sticky="we", pady=4)

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Добавить", command=self.submit).pack(side="right")
        ttk.Button(btns, text="Отмена", command=self.destroy).pack(
            side="right", padx=(0, 6))
        frm.columnconfigure(1, weight=1)

        self.bind("<Escape>", lambda _e: self.destroy())

    def browse(self):
        path = filedialog.askopenfilename(
            title="Выберите программу",
            filetypes=[("Программы", "*.exe *.bat *.cmd *.com"),
                       ("Ярлыки", "*.lnk"),
                       ("Все файлы", "*.*")])
        if path:
            self.path_var.set(path)
            if not self.name_var.get().strip():
                self.name_var.set(Path(path).stem)

    def submit(self):
        name = self.name_var.get().strip()
        path = self.path_var.get().strip()
        args = self.args_var.get().strip()
        if not name:
            messagebox.showwarning("Внимание", "Укажите название.", parent=self)
            return
        if not path or not os.path.exists(path):
            messagebox.showwarning(
                "Внимание", "Укажите существующий путь к программе.",
                parent=self)
            return
        try:
            if self.place_var.get().startswith("Реестр"):
                command = f'"{path}"'
                if args:
                    command += " " + args
                add_registry_entry(name, command)
            else:
                add_startup_shortcut(name, path, args)
        except Exception as ex:
            messagebox.showerror(
                "Ошибка", f"Не удалось добавить запись:\n{ex}", parent=self)
            return
        self.on_done()
        self.destroy()


# ---------- Диалог удаления ----------

class DeleteDialog(tk.Toplevel):
    def __init__(self, master, entries: list):
        super().__init__(master)
        self.title("Удаление записей")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.result = None

        c = THEMES[master.settings["theme"]]
        self.configure(bg=c["bg"])

        frm = ttk.Frame(self, padding=16)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text=f"Будет обработано записей: {len(entries)}",
                  font=("TkDefaultFont",
                        int(master.settings["font_size"]) + 1)
                  ).pack(anchor="w", pady=(0, 10))

        self.mode_var = tk.StringVar(value="backup")
        ttk.Radiobutton(
            frm,
            text="Удалить с сохранением резервной копии (можно восстановить)",
            variable=self.mode_var, value="backup").pack(anchor="w", pady=2)
        ttk.Radiobutton(
            frm,
            text="Удалить окончательно (без резервной копии)",
            variable=self.mode_var, value="permanent").pack(anchor="w", pady=2)

        any_file = any(not is_registry_source(e.source) for e in entries)
        self.delete_file_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frm,
            text="Также удалить файлы программ с диска (необратимо)",
            variable=self.delete_file_var,
            state="normal" if any_file else "disabled",
        ).pack(anchor="w", pady=(12, 0))
        ttk.Label(frm,
                  text="(относится только к файлам из папок автозагрузки)",
                  foreground=c["tree_disabled"]).pack(anchor="w")

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(18, 0))
        ttk.Button(btns, text="Удалить", command=self._ok).pack(side="right")
        ttk.Button(btns, text="Отмена", command=self._cancel).pack(
            side="right", padx=(0, 6))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _ok(self):
        self.result = (self.mode_var.get(), bool(self.delete_file_var.get()))
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


# ---------- Окно резервных копий ----------

class BackupsDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Резервные копии удалённых записей")
        self.geometry("820x460")
        self.transient(master)
        self.grab_set()
        self.master_app = master

        c = THEMES[master.settings["theme"]]
        self.configure(bg=c["bg"])

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(
            frm,
            text="Записи, удалённые с сохранением резервной копии.\n"
                 "Их можно восстановить в автозагрузку или удалить окончательно.",
            justify="left").pack(anchor="w", pady=(0, 8))

        columns = ("name", "source", "deleted_at", "command")
        self.tree = ttk.Treeview(frm, columns=columns, show="headings",
                                 selectmode="extended")
        self.tree.heading("name", text="Название")
        self.tree.heading("source", text="Источник")
        self.tree.heading("deleted_at", text="Удалено")
        self.tree.heading("command", text="Команда / путь")
        self.tree.column("name", width=180, anchor="w")
        self.tree.column("source", width=170, anchor="w")
        self.tree.column("deleted_at", width=150, anchor="w")
        self.tree.column("command", width=380, anchor="w")
        self.tree.pack(fill="both", expand=True)

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="Восстановить",
                   command=self._restore).pack(side="left")
        ttk.Button(btns, text="Удалить окончательно",
                   command=self._purge).pack(side="left", padx=(6, 0))
        ttk.Separator(btns, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(btns, text="Очистить все",
                   command=self._purge_all).pack(side="left")
        ttk.Button(btns, text="Закрыть",
                   command=self.destroy).pack(side="right")

        self._reload()
        self.bind("<Escape>", lambda _e: self.destroy())

    def _reload(self):
        self.tree.delete(*self.tree.get_children())
        self._items = load_backups()
        for i, item in enumerate(self._items):
            self.tree.insert("", "end", iid=str(i),
                             values=(item.get("name", ""),
                                     get_source_label(item.get("source", "")),
                                     item.get("deleted_at", ""),
                                     item.get("command", "")))

    def _selected(self):
        return [self._items[int(i)] for i in self.tree.selection()]

    def _restore(self):
        sel = self._selected()
        if not sel:
            return
        errors = []
        for item in sel:
            try:
                restore_backup(item)
            except Exception as ex:
                errors.append(f'{item.get("name")}: {ex}')
        self._reload()
        self.master_app.refresh(notify=False)
        if errors:
            messagebox.showerror("Ошибки", "\n".join(errors), parent=self)

    def _purge(self):
        sel = self._selected()
        if not sel:
            return
        if not messagebox.askyesno(
                "Удаление",
                f"Удалить {len(sel)} резервных копий "
                f"без возможности восстановления?",
                parent=self):
            return
        for item in sel:
            purge_backup(item)
        self._reload()

    def _purge_all(self):
        if not self._items:
            return
        if not messagebox.askyesno(
                "Очистка",
                f"Удалить все резервные копии ({len(self._items)} шт.)?",
                parent=self):
            return
        purge_all_backups()
        self._reload()


# ---------- Простой диалог ввода строки ----------

class _PromptDialog(tk.Toplevel):
    def __init__(self, master, title, prompt, initial=""):
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.result = None

        settings = (master.master_app.settings
                    if hasattr(master, "master_app") else master.settings)
        c = THEMES[settings["theme"]]
        self.configure(bg=c["bg"])

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=prompt).pack(anchor="w", pady=(0, 6))
        self.var = tk.StringVar(value=initial)
        e = ttk.Entry(frm, textvariable=self.var, width=46)
        e.pack(fill="x")
        e.focus_set()
        e.select_range(0, "end")
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right")
        ttk.Button(btns, text="Отмена", command=self._cancel).pack(
            side="right", padx=(0, 6))
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _ok(self):
        self.result = self.var.get().strip()
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


# ---------- Диалог ввода ветки реестра ----------

class RegistrySourceDialog(tk.Toplevel):
    def __init__(self, master, initial=None):
        super().__init__(master)
        self.title("Ветка реестра")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.result = None

        settings = (master.master_app.settings
                    if hasattr(master, "master_app") else master.settings)
        c = THEMES[settings["theme"]]
        self.configure(bg=c["bg"])

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Ветка реестра:").grid(row=0, column=0,
                                                   sticky="w", pady=4)
        init_full = ""
        if initial:
            init_full = (f'{initial.get("hive","HKCU")}\\'
                         f'{initial.get("path","")}')
        self.path_var = tk.StringVar(value=init_full)
        ttk.Entry(frm, textvariable=self.path_var, width=56).grid(
            row=0, column=1, sticky="we", pady=4)

        ttk.Label(
            frm,
            text="Форматы: HKCU\\Software\\MyApp\\Run,\n"
                 "HKEY_LOCAL_MACHINE\\Software\\Vendor\\Product\\Run\n"
                 "или просто Software\\Vendor\\Product\\Run (тогда HKCU).",
            foreground=c["tree_disabled"], justify="left"
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 8))

        ttk.Label(frm, text="Название:").grid(row=2, column=0,
                                              sticky="w", pady=4)
        self.label_var = tk.StringVar(
            value=(initial.get("label", "") if initial else ""))
        ttk.Entry(frm, textvariable=self.label_var, width=56).grid(
            row=2, column=1, sticky="we", pady=4)

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right")
        ttk.Button(btns, text="Отмена", command=self._cancel).pack(
            side="right", padx=(0, 6))

        frm.columnconfigure(1, weight=1)

        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _ok(self):
        raw = self.path_var.get().strip()
        if not raw:
            messagebox.showwarning("Внимание", "Укажите ветку реестра.",
                                   parent=self)
            return
        parsed = parse_registry_path(raw)
        if parsed is None:
            hive, path = "HKCU", raw.lstrip("\\/").strip()
        else:
            hive, path = parsed
        if not path:
            messagebox.showwarning("Внимание", "Путь пустой.", parent=self)
            return
        label = self.label_var.get().strip() or path
        self.result = (hive, path, label)
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


# ---------- Диалог "Управление источниками" ----------

class SourceDialog(tk.Toplevel):
    def __init__(self, master, on_done):
        super().__init__(master)
        self.title("Управление источниками")
        self.geometry("760x460")
        self.transient(master)
        self.grab_set()
        self.master_app = master
        self.on_done = on_done

        c = THEMES[master.settings["theme"]]
        self.configure(bg=c["bg"])

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(
            frm,
            text="Дополнительные источники: произвольные папки\n"
                 "и ветки реестра, которые нужно показывать в общем списке.",
            justify="left").pack(anchor="w", pady=(0, 10))

        columns = ("type", "label", "path")
        self.tree = ttk.Treeview(frm, columns=columns, show="headings",
                                 selectmode="browse", height=10)
        self.tree.heading("type", text="Тип")
        self.tree.heading("label", text="Название")
        self.tree.heading("path", text="Путь")
        self.tree.column("type", width=90, anchor="center", stretch=False)
        self.tree.column("label", width=220, anchor="w")
        self.tree.column("path", width=380, anchor="w")
        self.tree.pack(fill="both", expand=True)

        self.tree.bind("<Double-1>", lambda _e: self._edit_selected())

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="Добавить папку…",
                   command=self._add_folder).pack(side="left")
        ttk.Button(btns, text="Добавить ветку реестра…",
                   command=self._add_registry).pack(side="left", padx=(6, 0))
        ttk.Separator(btns, orient="vertical").pack(side="left", fill="y",
                                                    padx=8)
        ttk.Button(btns, text="Изменить…",
                   command=self._edit_selected).pack(side="left")
        ttk.Button(btns, text="Удалить",
                   command=self._remove_selected).pack(side="left", padx=(6, 0))

        close_bar = ttk.Frame(frm)
        close_bar.pack(fill="x", pady=(10, 0))
        ttk.Button(close_bar, text="Закрыть",
                   command=self.destroy).pack(side="right")

        self._reload()

        self.bind("<Escape>", lambda _e: self.destroy())
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _reload(self):
        self.tree.delete(*self.tree.get_children())
        self._sources = load_custom_sources()
        for i, s in enumerate(self._sources):
            t = "Папка" if s["type"] == "folder" else "Реестр"
            if s["type"] == "registry":
                path_str = f'{s.get("hive", "HKCU")}\\{s.get("path", "")}'
            else:
                path_str = s.get("path", "")
            self.tree.insert("", "end", iid=str(i),
                             values=(t, s.get("label", ""), path_str))

    def _selected_source(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self._sources[int(sel[0])]

    def _add_folder(self):
        folder = filedialog.askdirectory(title="Выберите папку")
        if not folder:
            return
        dlg = _PromptDialog(self, "Название источника",
                            "Как назвать эту папку?", Path(folder).name)
        if dlg.result is None:
            return
        items = load_custom_sources()
        items.append({"id": uuid.uuid4().hex[:12], "type": "folder",
                      "label": dlg.result or Path(folder).name,
                      "path": folder})
        save_custom_sources(items)
        self._reload()
        self.on_done()

    def _add_registry(self):
        dlg = RegistrySourceDialog(self)
        if dlg.result is None:
            return
        hive, path, label = dlg.result
        items = load_custom_sources()
        items.append({"id": uuid.uuid4().hex[:12], "type": "registry",
                      "label": label or path, "hive": hive, "path": path})
        save_custom_sources(items)
        self._reload()
        self.on_done()

    def _edit_selected(self):
        s = self._selected_source()
        if not s:
            return
        if s["type"] == "registry":
            dlg = RegistrySourceDialog(self, initial=s)
            if dlg.result is None:
                return
            hive, path, label = dlg.result
            s["hive"] = hive
            s["path"] = path
            s["label"] = label or path
        else:
            dlg = _PromptDialog(self, "Название источника",
                                "Как назвать эту папку?",
                                s.get("label", ""))
            if dlg.result is None:
                return
            s["label"] = dlg.result or s.get("label", "")
        items = load_custom_sources()
        for i, x in enumerate(items):
            if x["id"] == s["id"]:
                items[i] = s
                break
        save_custom_sources(items)
        self._reload()
        self.on_done()

    def _remove_selected(self):
        s = self._selected_source()
        if not s:
            return
        if not messagebox.askyesno(
            "Удаление источника",
            f'Удалить источник "{s.get("label", s.get("path"))}"?\n\n'
            "Записи из этого источника пропадут из списка. "
            "Сами файлы/значения реестра затронуты не будут.",
            parent=self):
            return
        items = [x for x in load_custom_sources() if x["id"] != s["id"]]
        save_custom_sources(items)
        self._reload()
        self.on_done()


# ---------- Диалог настроек ----------

class SettingsDialog(tk.Toplevel):
    def __init__(self, master: "App"):
        super().__init__(master)
        self.title("Настройки")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.master_app = master

        c = THEMES[master.settings["theme"]]
        self.configure(bg=c["bg"])

        frm = ttk.Frame(self, padding=16)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Тема оформления:").grid(row=0, column=0,
                                                     sticky="w", pady=6)
        self.theme_var = tk.StringVar(
            value="Тёмная" if master.settings["theme"] == "dark" else "Светлая")
        combo = ttk.Combobox(frm, textvariable=self.theme_var, state="readonly",
                             values=["Светлая", "Тёмная"], width=22)
        combo.grid(row=0, column=1, sticky="we", pady=6)
        combo.bind("<<ComboboxSelected>>", self._live_preview)

        ttk.Label(frm, text="Размер шрифта:").grid(row=1, column=0,
                                                   sticky="w", pady=6)
        self.font_var = tk.IntVar(value=int(master.settings["font_size"]))
        sp = ttk.Spinbox(frm, from_=8, to=16, textvariable=self.font_var, width=8)
        sp.grid(row=1, column=1, sticky="w", pady=6)
        sp.bind("<FocusOut>", self._live_preview)
        sp.bind("<Return>", self._live_preview)
        self.font_var.trace_add("write", lambda *_: self._live_preview())

        ttk.Separator(frm, orient="horizontal").grid(
            row=2, column=0, columnspan=2, sticky="we", pady=10)

        self.hide_system_var = tk.BooleanVar(
            value=bool(master.settings["hide_system"]))
        ttk.Checkbutton(frm, text="Скрывать системные программы",
                        variable=self.hide_system_var,
                        command=self._live_preview).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=4)

        self.highlight_new_var = tk.BooleanVar(
            value=bool(master.settings["highlight_new"]))
        ttk.Checkbutton(frm, text="Подсвечивать новые записи в автозагрузке",
                        variable=self.highlight_new_var,
                        command=self._live_preview).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=4)

        self.notify_new_var = tk.BooleanVar(
            value=bool(master.settings["notify_new"]))
        ttk.Checkbutton(frm,
                        text="Показывать уведомление Windows о новых записях",
                        variable=self.notify_new_var,
                        command=self._live_preview).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=4)

        self.confirm_var = tk.BooleanVar(
            value=bool(master.settings["confirm_delete"]))
        ttk.Checkbutton(frm, text="Подтверждать удаление записей",
                        variable=self.confirm_var,
                        command=self._live_preview).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=4)

        ttk.Separator(frm, orient="horizontal").grid(
            row=7, column=0, columnspan=2, sticky="we", pady=10)

        self.minimize_to_tray_var = tk.BooleanVar(
            value=bool(master.settings["minimize_to_tray"]))
        ttk.Checkbutton(frm, text="Сворачивать в трей вместо закрытия",
                        variable=self.minimize_to_tray_var,
                        command=self._live_preview).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=4)

        self.tray_on_minimize_var = tk.BooleanVar(
            value=bool(master.settings["tray_on_minimize"]))
        ttk.Checkbutton(frm, text="Сворачивать в трей при минимизации окна",
                        variable=self.tray_on_minimize_var,
                        command=self._live_preview).grid(
            row=9, column=0, columnspan=2, sticky="w", pady=4)

        ttk.Label(
            frm,
            text="Системными считаются записи из HKLM, общей папки\n"
                 "автозагрузки и команды из Windows / Program Files.",
            foreground=c["tree_disabled"], justify="left"
        ).grid(row=10, column=0, columnspan=2, sticky="w", pady=(6, 0))

        actions = ttk.Frame(frm)
        actions.grid(row=11, column=0, columnspan=2, sticky="we", pady=(14, 0))
        ttk.Button(actions, text="Снять метки «новые»",
                   command=self._clear_new_marks).pack(side="left")
        ttk.Button(actions, text="Сбросить все данные…",
                   command=self._reset_all_data).pack(side="left", padx=(6, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=12, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Сохранить", command=self._save).pack(side="right")
        ttk.Button(btns, text="Отмена", command=self._cancel).pack(
            side="right", padx=(0, 6))
        ttk.Button(btns, text="Сбросить настройки",
                   command=self._reset_settings).pack(side="left")

        frm.columnconfigure(1, weight=1)

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._save())

        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _current_preview_settings(self) -> dict:
        s = dict(self.master_app.settings)
        s["theme"] = "dark" if self.theme_var.get() == "Тёмная" else "light"
        try:
            s["font_size"] = max(8, min(16, int(self.font_var.get())))
        except (tk.TclError, ValueError):
            s["font_size"] = int(self.master_app.settings["font_size"])
        s["hide_system"] = bool(self.hide_system_var.get())
        s["highlight_new"] = bool(self.highlight_new_var.get())
        s["notify_new"] = bool(self.notify_new_var.get())
        s["confirm_delete"] = bool(self.confirm_var.get())
        s["minimize_to_tray"] = bool(self.minimize_to_tray_var.get())
        s["tray_on_minimize"] = bool(self.tray_on_minimize_var.get())
        return s

    def _live_preview(self, _e=None):
        self.master_app.settings = self._current_preview_settings()
        self.master_app.apply_settings()
        c = THEMES[self.master_app.settings["theme"]]
        self.configure(bg=c["bg"])

    def _save(self):
        self.master_app.settings = self._current_preview_settings()
        save_settings(self.master_app.settings)
        self.master_app.apply_settings()
        self.destroy()

    def _cancel(self):
        self.master_app.settings = load_settings()
        self.master_app.apply_settings()
        self.destroy()

    def _reset_settings(self):
        self.theme_var.set("Светлая")
        self.font_var.set(10)
        self.hide_system_var.set(False)
        self.highlight_new_var.set(True)
        self.notify_new_var.set(True)
        self.confirm_var.set(True)
        self.minimize_to_tray_var.set(True)
        self.tray_on_minimize_var.set(False)
        self._live_preview()

    def _clear_new_marks(self):
        self.master_app.clear_new_marks()
        messagebox.showinfo("Готово", "Метки «новые» сняты.", parent=self)

    def _reset_all_data(self):
        if not messagebox.askyesno(
            "Подтверждение",
            "Будут удалены:\n"
            "• список отключённых записей,\n"
            "• все резервные копии удалённых записей,\n"
            "• снимок автозагрузки,\n"
            "• сохранённые файлы отключённых программ.\n\n"
            "Пользовательские источники затронуты не будут.\n\nПродолжить?",
            parent=self):
            return
        try:
            for f in (CONFIG_FILE, SNAPSHOT_FILE, BACKUPS_FILE):
                if f.exists():
                    f.unlink()
            for d in (DISABLED_FILES_DIR, BACKUPS_FILES_DIR):
                if d.exists():
                    shutil.rmtree(d, ignore_errors=True)
        except OSError as ex:
            messagebox.showerror("Ошибка", str(ex), parent=self)
            return
        self.master_app._known_signatures = None
        self.master_app._new_signatures = set()
        self.master_app.refresh(notify=False)
        messagebox.showinfo("Готово", "Данные сброшены.", parent=self)


# ---------- Основное окно ----------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self._startup_refresh = True
        self._brand_new_entries: list = []
        self._known_signatures, self._new_signatures = load_snapshot()
        self.tray: TrayIcon | None = None

        self.title("Менеджер автозагрузки")
        self.geometry(self.settings.get("window_geometry", "1000x600"))
        self.minsize(820, 520)

        self.entries: list[StartupEntry] = []

        apply_theme(self, self.settings)
        self._build_menu()
        self._build_ui()
        self._apply_menu_colors()
        self._setup_tray()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Unmap>", self._on_unmap)

        self.refresh(notify=True)

    # --- трей ---

    def _setup_tray(self):
        if os.name != "nt":
            return
        try:
            self.tray = TrayIcon(
                root=self, tooltip="Менеджер автозагрузки",
                on_show=self._show_from_tray,
                on_menu=self._show_tray_menu,
                on_exit=self._real_exit)
            self.tray.install()
        except Exception:
            self.tray = None

    def _show_from_tray(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _show_tray_menu(self):
        pt = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(pt))
        m = tk.Menu(self, tearoff=0)
        style_menu(m, self.settings)
        m.add_command(label="Открыть", command=self._show_from_tray)
        m.add_command(label="Проверить автозагрузку",
                      command=lambda: self.refresh(notify=True))
        m.add_separator()
        m.add_command(label="Настройки…",
                      command=lambda: (self._show_from_tray(),
                                       self.open_settings()))
        m.add_separator()
        m.add_command(label="Выход", command=self._real_exit)
        try:
            m.tk_popup(pt.x, pt.y)
        finally:
            m.grab_release()

    def _real_exit(self):
        if self.tray:
            try:
                self.tray.remove()
            except Exception:
                pass
            self.tray = None
        try:
            self.settings["window_geometry"] = self.geometry()
            save_settings(self.settings)
        except Exception:
            pass
        self.destroy()

    def _on_close(self):
        if self.settings.get("minimize_to_tray") and self.tray:
            self.withdraw()
            return
        self._real_exit()

    def _on_unmap(self, event):
        if event.widget is not self:
            return
        if not self.tray or not self.settings.get("tray_on_minimize"):
            return
        self.after(1, self._maybe_hide_on_minimize)

    def _maybe_hide_on_minimize(self):
        try:
            if self.state() == "iconic":
                self.withdraw()
        except tk.TclError:
            pass

    # --- меню ---

    def _build_menu(self):
        self.menubar = tk.Menu(self, tearoff=0)

        self.file_menu = tk.Menu(self.menubar, tearoff=0)
        self.file_menu.add_command(label="Обновить", accelerator="F5",
                                   command=lambda: self.refresh(notify=True))
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Добавить программу…",
                                   command=self.open_add_dialog)
        self.file_menu.add_command(label="Управление источниками…",
                                   command=self.open_sources_dialog)
        self.file_menu.add_command(label="Снять метки «новые»",
                                   command=self.clear_new_marks)
        self.file_menu.add_command(label="Резервные копии…",
                                   command=self.open_backups_dialog)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Экспорт конфигурации…",
                                   command=self.export_config)
        self.file_menu.add_command(label="Импорт конфигурации…",
                                   command=self.import_config)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Настройки…", accelerator="Ctrl+,",
                                   command=self.open_settings)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Выход", command=self._real_exit)
        self.menubar.add_cascade(label="Файл", menu=self.file_menu)

        self.help_menu = tk.Menu(self.menubar, tearoff=0)
        self.help_menu.add_command(label="О программе…",
                                   command=self.show_about)
        self.menubar.add_cascade(label="Справка", menu=self.help_menu)

        self.config(menu=self.menubar)

    def _apply_menu_colors(self):
        for m in (self.menubar, self.file_menu, self.help_menu):
            style_menu(m, self.settings)
        if hasattr(self, "context_menu"):
            style_menu(self.context_menu, self.settings)

    # --- интерфейс ---

    def _build_ui(self):
        toolbar = ttk.Frame(self, padding=(8, 8, 8, 4))
        toolbar.pack(fill="x")

        ttk.Button(toolbar, text="Обновить",
                   command=lambda: self.refresh(notify=True)).pack(side="left")
        ttk.Separator(toolbar, orient="vertical").pack(side="left",
                                                       fill="y", padx=8)
        ttk.Button(toolbar, text="Открыть расположение",
                   command=self.open_location_selected).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Удалить",
                   command=self.delete_selected).pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left",
                                                       fill="y", padx=8)
        ttk.Button(toolbar, text="Добавить программу…",
                   command=self.open_add_dialog).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Источники…",
                   command=self.open_sources_dialog).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Настройки…",
                   command=self.open_settings).pack(side="left", padx=2)

        self.hint_label = ttk.Label(
            toolbar,
            text="Клик по галочке — переключить • ПКМ — доп. действия",
            foreground="#888")
        self.hint_label.pack(side="right")

        container = ttk.Frame(self, padding=(8, 0, 8, 8))
        container.pack(fill="both", expand=True)

        columns = ("check", "name", "source", "command")
        self.tree = ttk.Treeview(container, columns=columns, show="headings",
                                 selectmode="extended")
        self.tree.heading("check", text="")
        self.tree.heading("name", text="Название")
        self.tree.heading("source", text="Источник")
        self.tree.heading("command", text="Команда / путь")

        self.tree.column("check", width=40, anchor="center", stretch=False)
        self.tree.column("name", width=240, anchor="w")
        self.tree.column("source", width=220, anchor="w")
        self.tree.column("command", width=460, anchor="w")

        vsb = ttk.Scrollbar(container, orient="vertical",
                            command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double)
        self.tree.bind("<Button-3>", self._on_right_click)

        self.bind("<F5>", lambda _e: self.refresh(notify=True))
        self.bind("<Control-c>", lambda _e: self.copy_path_selected())
        self.bind("<Delete>", lambda _e: self.delete_selected())
        self.bind("<Return>", lambda _e: self.open_location_selected())
        self.bind("<Control-comma>", lambda _e: self.open_settings())

        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Открыть расположение",
                                      command=self.open_location_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Копировать путь",
                                      command=self.copy_path_selected)
        self.context_menu.add_command(label="Копировать имя",
                                      command=self.copy_name_selected)
        self.context_menu.add_command(label="Копировать команду",
                                      command=self.copy_command_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Снять метку «новая»",
                                      command=self.clear_new_marks_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Удалить",
                                      command=self.delete_selected)

        self.status = ttk.Label(self, text="", padding=(10, 4), anchor="w")
        self.status.pack(fill="x")

    # --- данные и отрисовка ---

    def refresh(self, notify: bool = True):
        self.entries = collect_entries()
        brand_new = self._detect_new()
        self._render()
        if (notify and self._startup_refresh and brand_new
                and self.settings["notify_new"]):
            self._notify_new(brand_new)
        self._startup_refresh = False

    def _detect_new(self) -> list:
        current = {entry_sig(e) for e in self.entries}
        if self._known_signatures is None:
            self._known_signatures = set(current)
            save_snapshot(self._known_signatures, self._new_signatures)
            self._brand_new_entries = []
            return []
        brand_new_sigs = current - self._known_signatures
        self._known_signatures |= current
        self._new_signatures |= brand_new_sigs
        save_snapshot(self._known_signatures, self._new_signatures)
        self._brand_new_entries = [
            e for e in self.entries if entry_sig(e) in brand_new_sigs]
        return self._brand_new_entries

    def _render(self):
        c = THEMES[self.settings["theme"]]
        self.tree.tag_configure("disabled", foreground=c["tree_disabled"])
        self.tree.tag_configure("new", foreground=c["new_fg"])

        self.tree.delete(*self.tree.get_children())

        shown = hidden_sys = 0
        for idx, e in enumerate(self.entries):
            if self.settings["hide_system"] and is_system_entry(e):
                hidden_sys += 1
                continue
            sig = entry_sig(e)
            is_new = (self.settings["highlight_new"]
                      and sig in self._new_signatures)
            tags = []
            if is_new:
                tags.append("new")
            if not e.enabled:
                tags.append("disabled")
            prefix = f"{NEW_MARK} " if is_new else ""
            self.tree.insert(
                "", "end", iid=str(idx),
                values=(CHECK_ON if e.enabled else CHECK_OFF,
                        prefix + e.name,
                        get_source_label(e.source),
                        e.command),
                tags=tuple(tags))
            shown += 1

        parts = [f"Показано: {shown} из {len(self.entries)}"]
        if hidden_sys:
            parts.append(f"скрыто системных: {hidden_sys}")
        if self._new_signatures:
            parts.append(f"новых меток: {len(self._new_signatures)}")
        n_custom = len(load_custom_sources())
        if n_custom:
            parts.append(f"польз. источников: {n_custom}")
        parts.append(f"права администратора: {'да' if is_admin() else 'нет'}")
        self.status.config(text="   |   ".join(parts))

    def apply_settings(self):
        apply_theme(self, self.settings)
        self._apply_menu_colors()
        self.hint_label.configure(
            foreground=THEMES[self.settings["theme"]]["tree_disabled"])
        self._render()

    def selected_entries(self) -> list:
        return [self.entries[int(iid)] for iid in self.tree.selection()]

    # --- метки "новые" ---

    def clear_new_marks(self):
        self._new_signatures = set()
        save_snapshot(self._known_signatures or set(), self._new_signatures)
        self._render()

    def clear_new_marks_selected(self):
        sel = self.selected_entries()
        if not sel:
            return
        for e in sel:
            self._new_signatures.discard(entry_sig(e))
        save_snapshot(self._known_signatures or set(), self._new_signatures)
        self._render()

    # --- уведомления ---

    def _notify_new(self, new_entries: list):
        if not new_entries:
            return
        title = "Менеджер автозагрузки"
        if len(new_entries) == 1:
            msg = f"Новая запись в автозагрузке: {new_entries[0].name}"
        else:
            names = ", ".join(e.name for e in new_entries[:3])
            if len(new_entries) > 3:
                names += f" и ещё {len(new_entries) - 3}"
            msg = f"Новых записей: {len(new_entries)} — {names}"
        sent = False
        if self.tray:
            try:
                sent = self.tray.notify(title, msg, warning=True)
            except Exception:
                sent = False
        if not sent:
            show_windows_toast(title, msg)

    # --- клики ---

    def _on_tree_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self._toggle_entry(int(iid))
        return "break"

    def _on_tree_double(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) == "#1":
            return
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self._toggle_entry(int(iid))
        return "break"

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        sel = self.selected_entries()
        if sel and all(is_registry_source(e.source) for e in sel):
            self.context_menu.entryconfig(
                0, label="Открыть в редакторе реестра")
        elif sel and all(is_folder_source(e.source) for e in sel):
            self.context_menu.entryconfig(
                0, label="Открыть в проводнике")
        else:
            self.context_menu.entryconfig(
                0, label="Открыть расположение")

        any_new = any(entry_sig(e) in self._new_signatures for e in sel)
        try:
            self.context_menu.entryconfig(
                7, state="normal" if any_new else "disabled")
        except tk.TclError:
            pass

        style_menu(self.context_menu, self.settings)
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def _toggle_entry(self, idx: int):
        e = self.entries[idx]
        try:
            if e.enabled:
                disable_entry(e)
            else:
                enable_entry(e)
        except PermissionError:
            messagebox.showerror(
                "Недостаточно прав",
                f"Не удалось изменить запись «{e.name}».\n\n"
                f"Скорее всего, требуется запуск от имени администратора.")
        except FileNotFoundError:
            messagebox.showerror("Файл не найден",
                                 f"Запись «{e.name}» уже отсутствует.")
        except Exception as ex:
            messagebox.showerror("Ошибка",
                                 f"Не удалось изменить запись «{e.name}»:\n{ex}")
        self.refresh(notify=False)

    # --- открытие / копирование ---

    def open_location_selected(self):
        sel = self.selected_entries()
        if not sel:
            messagebox.showinfo("Менеджер автозагрузки",
                                "Сначала выберите запись в списке.")
            return
        for e in sel:
            try:
                open_entry_location(e)
            except Exception as ex:
                messagebox.showerror(
                    "Ошибка",
                    f"Не удалось открыть расположение для «{e.name}»:\n{ex}")

    def _set_clipboard(self, text: str):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()

    def copy_path_selected(self):
        sel = self.selected_entries()
        if sel:
            self._set_clipboard(
                "\n".join(entry_display_path(e) for e in sel))

    def copy_name_selected(self):
        sel = self.selected_entries()
        if sel:
            self._set_clipboard("\n".join(e.name for e in sel))

    def copy_command_selected(self):
        sel = self.selected_entries()
        if sel:
            self._set_clipboard("\n".join(e.command for e in sel))

    # --- удаление ---

    def delete_selected(self):
        sel = self.selected_entries()
        if not sel:
            messagebox.showinfo("Менеджер автозагрузки",
                                "Сначала выберите запись в списке.")
            return

        dlg = DeleteDialog(self, sel)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        mode, also_delete_file = dlg.result

        if also_delete_file:
            if not messagebox.askyesno(
                    "Опасное действие",
                    "Файлы выбранных программ будут удалены с диска "
                    "без возможности восстановления.\n\nПродолжить?"):
                return

        errors = []
        for e in sel:
            try:
                is_reg = is_registry_source(e.source)

                if also_delete_file and not is_reg:
                    p = entry_file_path(e)
                    try:
                        if p and os.path.exists(p):
                            os.remove(p)
                    except OSError as ex:
                        errors.append(
                            f"{e.name}: не удалось удалить файл: {ex}")
                        continue
                    remove_disabled_item(e.source, e.name)
                    self._new_signatures.discard(entry_sig(e))
                    continue

                if mode == "backup":
                    backup_entry(e)
                else:
                    delete_entry(e)
                self._new_signatures.discard(entry_sig(e))
            except PermissionError:
                errors.append(f"{e.name}: недостаточно прав "
                              f"(запустите от имени администратора)")
            except FileNotFoundError:
                errors.append(f"{e.name}: запись уже отсутствует")
            except Exception as ex:
                errors.append(f"{e.name}: {ex}")

        save_snapshot(self._known_signatures or set(), self._new_signatures)
        self.refresh(notify=False)
        if errors:
            messagebox.showerror("Ошибки", "\n".join(errors))

    # --- диалоги ---

    def open_add_dialog(self):
        AddDialog(self, on_done=lambda: self.refresh(notify=True))

    def open_sources_dialog(self):
        SourceDialog(self, on_done=lambda: self.refresh(notify=True))

    def open_backups_dialog(self):
        BackupsDialog(self)

    def open_settings(self):
        SettingsDialog(self)

    def show_about(self):
        messagebox.showinfo(
            "О программе",
            "Менеджер автозагрузки\n\n"
            "Поддерживает:\n"
            "• встроенные источники (Run-ключи HKCU/HKLM "
            "и папки «Автозагрузка»)\n"
            "• пользовательские источники "
            "(произвольные папки и ветки реестра)\n"
            "• удаление с резервной копией и восстановление\n\n"
            f"Файлы данных:\n{APP_DIR}")

    # --- экспорт / импорт ---

    def export_config(self):
        path = filedialog.asksaveasfilename(
            title="Экспорт конфигурации",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile="startup_manager_config.json")
        if not path:
            return
        data = {
            "version": 3,
            "settings": self.settings,
            "disabled": load_disabled(),
            "custom_sources": load_custom_sources(),
            "backups": load_backups(),
        }
        try:
            Path(path).write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError as ex:
            messagebox.showerror("Ошибка", f"Не удалось сохранить файл:\n{ex}")
            return
        messagebox.showinfo("Готово", f"Конфигурация сохранена:\n{path}")

    def import_config(self):
        path = filedialog.askopenfilename(
            title="Импорт конфигурации",
            filetypes=[("JSON", "*.json"), ("Все файлы", "*.*")])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as ex:
            messagebox.showerror("Ошибка", f"Не удалось прочитать файл:\n{ex}")
            return
        if not isinstance(data, dict) or "disabled" not in data:
            messagebox.showerror("Ошибка", "Некорректный файл конфигурации.")
            return
        has_sources = isinstance(data.get("custom_sources"), list)
        has_backups = isinstance(data.get("backups"), list)
        msg = "Будет заменён список отключённых записей"
        if has_sources:
            msg += " и набор пользовательских источников"
        if has_backups:
            msg += " и список резервных копий"
        msg += " (и, если есть в файле, настройки).\n\nПродолжить?"
        if not messagebox.askyesno("Импорт", msg):
            return
        save_disabled(data["disabled"])
        if has_sources:
            save_custom_sources([s for s in data["custom_sources"]
                                 if isinstance(s, dict) and s.get("id")
                                 and s.get("type") in ("folder", "registry")
                                 and s.get("path")])
        if has_backups:
            save_backups(data["backups"])
        if isinstance(data.get("settings"), dict):
            for k in DEFAULT_SETTINGS:
                if k in data["settings"]:
                    self.settings[k] = data["settings"][k]
            save_settings(self.settings)
            self.apply_settings()
        self.refresh(notify=False)
        messagebox.showinfo("Готово", "Конфигурация импортирована.")


# ---------- Точка входа ----------

def main():
    if os.name != "nt":
        print("Эта программа работает только под Windows.")
        return
    hide_console()
    App().mainloop()


if __name__ == "__main__":
    main()