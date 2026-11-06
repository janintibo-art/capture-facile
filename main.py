"""Capture Facile — captures d'écran simples sous Windows."""
import json
import os
import queue
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageGrab
from pynput import keyboard

APP_NAME = "Capture Facile"
CONFIG_FILE = Path(os.environ.get("APPDATA", str(Path.home()))) / "CaptureFacile" / "settings.json"
FORMATS = {"PNG": ".png", "JPEG": ".jpg"}
HOTKEYS = {"Impr. écran": "print_screen", "F8": "f8", "F9": "f9", "F10": "f10"}
BG, PANEL, CARD, ACCENT, TEXT, MUTED = "#101523", "#1A2234", "#232E43", "#29D3B0", "#F2F5FF", "#A1B0CA"


def default_download_dir():
    # Windows Downloads GUID, supports folder redirection / localization.
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            from uuid import UUID
            guid = UUID("374DE290-123F-4565-9164-39C4925E467B")
            raw = (ctypes.c_byte * 16).from_buffer_copy(guid.bytes_le)
            ptr = ctypes.c_void_p()
            shell = ctypes.windll.shell32
            if shell.SHGetKnownFolderPath(ctypes.byref(raw), 0, None, ctypes.byref(ptr)) == 0:
                result = ctypes.wstring_at(ptr.value)
                ctypes.windll.ole32.CoTaskMemFree(ptr)
                return Path(result)
        except Exception:
            pass
    return Path.home() / "Downloads"


def load_settings():
    defaults = {"folder": str(default_download_dir()), "hotkey": "Impr. écran", "format": "PNG"}
    try:
        settings = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(settings, dict):
            defaults.update({k: v for k, v in settings.items() if k in defaults and isinstance(v, str)})
    except (OSError, ValueError):
        pass
    if defaults["hotkey"] not in HOTKEYS:
        defaults["hotkey"] = "Impr. écran"
    if defaults["format"] not in FORMATS:
        defaults["format"] = "PNG"
    return defaults


class CaptureApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.configure(fg_color=BG)
        self.title("Capture Facile • Studio")
        self.geometry("1060x760")
        self.minsize(800, 570)
        self.settings = load_settings()
        self.events = queue.Queue()
        self.busy = False
        self.current_images = []
        self.selected = None
        self.thumb_refs = []
        self.listener = None
        self._build_ui()
        self._start_listener()
        self.after(120, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.refresh_gallery()

    def _save_settings(self):
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(self.settings, indent=2, ensure_ascii=False), encoding="utf-8")

    def _label(self, parent, text, size=13, color=TEXT, weight="normal", **kwargs):
        return ctk.CTkLabel(parent, text=text, text_color=color, font=("Segoe UI", size, weight), **kwargs)

    def _button(self, parent, text, command, primary=False, width=130):
        return ctk.CTkButton(parent, text=text, command=command, width=width, height=38,
                             fg_color=ACCENT if primary else CARD, hover_color="#17AF92" if primary else "#354661",
                             text_color="#10212A" if primary else TEXT, corner_radius=11,
                             font=("Segoe UI", 13, "bold"))

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        header.pack(fill="x", padx=28, pady=(23, 10))
        self._label(header, "◉  CAPTURE FACILE", 25, TEXT, "bold").pack(side="left")
        self._label(header, "CAPTURE  •  CLASSEMENT  •  LIBERTÉ", 11, MUTED).pack(side="right", pady=11)

        hero = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=20)
        hero.pack(fill="x", padx=28, pady=(5, 16))
        top = ctk.CTkFrame(hero, fg_color="transparent")
        top.pack(fill="x", padx=24, pady=(20, 12))
        left = ctk.CTkFrame(top, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        self._label(left, "Une touche. Une capture. C'est enregistré.", 19, TEXT, "bold").pack(anchor="w")
        self._label(left, "L'application reste ouverte pendant vos jeux ou logiciels.", 12, MUTED).pack(anchor="w", pady=(5, 0))
        self._button(top, "●  Capturer maintenant", self.capture, primary=True, width=220).pack(side="right", padx=(10, 0))
        opts = ctk.CTkFrame(hero, fg_color="transparent")
        opts.pack(fill="x", padx=24, pady=(3, 20))
        self._label(opts, "TOUCHE", 11, MUTED, "bold").grid(row=0, column=0, sticky="w", padx=(0, 14))
        self.hotkey_var = ctk.StringVar(value=self.settings["hotkey"])
        self.hotkey_menu = ctk.CTkOptionMenu(opts, values=list(HOTKEYS), variable=self.hotkey_var, command=self.change_hotkey,
                                              width=150, fg_color=CARD, button_color="#344661", button_hover_color="#425779")
        self.hotkey_menu.grid(row=1, column=0, sticky="w", padx=(0, 14), pady=(4, 0))
        self._label(opts, "FORMAT", 11, MUTED, "bold").grid(row=0, column=1, sticky="w", padx=(0, 14))
        self.format_var = ctk.StringVar(value=self.settings["format"])
        ctk.CTkOptionMenu(opts, values=list(FORMATS), variable=self.format_var, command=self.change_format,
                          width=108, fg_color=CARD, button_color="#344661", button_hover_color="#425779").grid(row=1, column=1, sticky="w", padx=(0, 22), pady=(4, 0))
        self._label(opts, "DOSSIER DE DESTINATION", 11, MUTED, "bold").grid(row=0, column=2, sticky="w")
        folder_row = ctk.CTkFrame(opts, fg_color="transparent")
        folder_row.grid(row=1, column=2, sticky="ew", pady=(4, 0))
        self.folder_label = self._label(folder_row, self._short_path(), 12, TEXT)
        self.folder_label.pack(side="left", padx=(0, 10))
        self._button(folder_row, "Modifier", self.choose_folder, width=95).pack(side="left")
        opts.grid_columnconfigure(2, weight=1)

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=30, pady=(0, 9))
        self._label(actions, "BIBLIOTHÈQUE D'IMAGES", 17, TEXT, "bold").pack(side="left")
        self.count_label = self._label(actions, "", 12, MUTED)
        self.count_label.pack(side="left", padx=13)
        self._button(actions, "Ouvrir le dossier", self.open_folder, width=156).pack(side="right", padx=(8, 0))
        self._button(actions, "Actualiser", self.refresh_gallery, width=110).pack(side="right")

        self.gallery = ctk.CTkScrollableFrame(self, fg_color=PANEL, corner_radius=16)
        self.gallery.pack(fill="both", expand=True, padx=28, pady=(0, 11))
        self.gallery.bind("<Configure>", self._gallery_resize)
        self.last_cols = 0
        bottom = ctk.CTkFrame(self, fg_color=BG)
        bottom.pack(fill="x", padx=30, pady=(0, 14))
        self.status = self._label(bottom, "Prêt à capturer", 12, ACCENT)
        self.status.pack(side="left")
        self._label(bottom, "Raccourci global actif tant que l'application est lancée", 11, MUTED).pack(side="right")

    def _short_path(self):
        p = self.settings["folder"]
        return ("…" + p[-47:]) if len(p) > 50 else p

    def _set_status(self, text, error=False):
        self.status.configure(text=text, text_color="#FF9A9A" if error else ACCENT)

    def _start_listener(self):
        if self.listener:
            self.listener.stop()
        self.listener = keyboard.Listener(on_release=self._on_key)
        self.listener.daemon = True
        self.listener.start()

    def _on_key(self, key):
        hotkey = HOTKEYS[self.settings["hotkey"]]
        try:
            if hotkey == "print_screen" and key == keyboard.Key.print_screen:
                self.events.put(("capture", None))
            elif hotkey != "print_screen" and key == getattr(keyboard.Key, hotkey):
                self.events.put(("capture", None))
        except (AttributeError, ValueError):
            pass

    def _poll(self):
        while True:
            try:
                action, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if action == "capture":
                self.capture()
            elif action == "saved":
                self.busy = False
                self._set_status(f"✓ Capture enregistrée : {payload.name}")
                self.refresh_gallery()
            elif action == "error":
                self.busy = False
                self._set_status(str(payload), True)
                messagebox.showerror(APP_NAME, str(payload))
        self.after(120, self._poll)

    def capture(self):
        if self.busy:
            return
        self.busy = True
        self._set_status("Capture en cours…")
        folder = Path(self.settings["folder"])
        suffix = FORMATS[self.settings["format"]]
        image_format = self.settings["format"]

        def job():
            try:
                folder.mkdir(parents=True, exist_ok=True)
                # all_screens=True covers extended Windows displays, not only the primary.
                screenshot = ImageGrab.grab(all_screens=True)
                name = f"capture_{datetime.now():%Y-%m-%d_%H-%M-%S_%f}" + suffix
                output = folder / name
                if image_format == "JPEG":
                    screenshot = screenshot.convert("RGB")
                    screenshot.save(output, "JPEG", quality=94)
                else:
                    screenshot.save(output, "PNG")
                self.events.put(("saved", output))
            except Exception as exc:
                self.events.put(("error", f"Capture impossible : {exc}"))
        threading.Thread(target=job, daemon=True).start()

    def change_hotkey(self, value):
        self.settings["hotkey"] = value
        self._save_settings()
        self._set_status(f"Raccourci modifié : {value}")

    def change_format(self, value):
        self.settings["format"] = value
        self._save_settings()
        self._set_status(f"Format modifié : {value}")

    def choose_folder(self):
        result = filedialog.askdirectory(initialdir=self.settings["folder"], title="Choisir le dossier des captures")
        if result:
            self.settings["folder"] = result
            self._save_settings()
            self.folder_label.configure(text=self._short_path())
            self.refresh_gallery()

    def open_folder(self):
        folder = Path(self.settings["folder"])
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(folder))
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Ouverture impossible : {exc}")

    def _gallery_resize(self, event):
        columns = max(2, min(5, (event.width - 25) // 210))
        if columns != self.last_cols:
            self.last_cols = columns
            self.after(100, self._layout_gallery)

    def refresh_gallery(self):
        folder = Path(self.settings["folder"])
        try:
            self.current_images = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg")),
                                         key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            self.current_images = []
        self.count_label.configure(text=f"{len(self.current_images)} image(s)")
        self._layout_gallery()

    def _layout_gallery(self):
        if not hasattr(self, "gallery"):
            return
        for child in self.gallery.winfo_children():
            child.destroy()
        self.thumb_refs = []
        cols = self.last_cols or 4
        if not self.current_images:
            self._label(self.gallery, "Vos captures apparaîtront ici. Appuyez sur la touche choisie pour commencer.", 14, MUTED).pack(pady=75)
            return
        # Limit live thumbnails for smooth browsing in large capture folders.
        for index, path in enumerate(self.current_images[:180]):
            tile = ctk.CTkFrame(self.gallery, fg_color=CARD, corner_radius=13)
            tile.grid(row=index // cols, column=index % cols, padx=8, pady=9, sticky="nsew")
            try:
                with Image.open(path) as source:
                    image = source.copy()
                image.thumbnail((185, 115))
                ctk_image = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
                self.thumb_refs.append(ctk_image)
                thumbnail = ctk.CTkLabel(tile, image=ctk_image, text="", width=185, height=118)
                thumbnail.pack(padx=8, pady=(10, 5))
                thumbnail.bind("<Button-1>", lambda _event, p=path: self.open_image(p))
            except Exception:
                self._label(tile, "Image illisible", 12, MUTED).pack(pady=40)
            self._label(tile, path.name[:25] + ("…" if len(path.name) > 25 else ""), 11, TEXT).pack(padx=6)
            row = ctk.CTkFrame(tile, fg_color="transparent")
            row.pack(pady=(7, 10))
            ctk.CTkButton(row, text="Voir", command=lambda p=path: self.open_image(p), width=52, height=28, fg_color="#33455E").pack(side="left", padx=2)
            ctk.CTkButton(row, text="Renommer", command=lambda p=path: self.rename_image(p), width=76, height=28, fg_color="#33455E").pack(side="left", padx=2)
            ctk.CTkButton(row, text="×", command=lambda p=path: self.delete_image(p), width=27, height=28, fg_color="#6C3548", hover_color="#AA4058").pack(side="left", padx=2)
        for col in range(cols):
            self.gallery.grid_columnconfigure(col, weight=1)
        if len(self.current_images) > 180:
            self._label(self.gallery, "180 images affichées. Ouvrez le dossier pour voir les autres.", 11, MUTED).grid(row=(180 // cols) + 1, column=0, columnspan=cols, pady=12)

    def open_image(self, path):
        try:
            os.startfile(str(path)) if sys.platform == "win32" else subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Ouverture impossible : {exc}")

    def rename_image(self, path):
        from tkinter import simpledialog
        new_name = simpledialog.askstring("Renommer", "Nouveau nom (sans extension) :", initialvalue=path.stem, parent=self)
        if new_name is None:
            return
        new_name = new_name.strip()
        if not new_name or any(c in new_name for c in '<>:"/\\|?*') or new_name.endswith((".", " ")):
            messagebox.showwarning(APP_NAME, "Nom invalide. Évitez les caractères interdits par Windows.")
            return
        new_path = path.with_name(new_name + path.suffix)
        if new_path.exists() and new_path != path:
            messagebox.showwarning(APP_NAME, "Un fichier porte déjà ce nom.")
            return
        try:
            path.rename(new_path)
            self.refresh_gallery()
            self._set_status(f"✓ Renommée : {new_path.name}")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Renommage impossible : {exc}")

    def delete_image(self, path):
        if not messagebox.askyesno("Supprimer", f"Supprimer définitivement {path.name} ?", parent=self):
            return
        try:
            path.unlink()
            self.refresh_gallery()
            self._set_status(f"Image supprimée : {path.name}")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Suppression impossible : {exc}")

    def _close(self):
        if self.listener:
            self.listener.stop()
        self.destroy()


if __name__ == "__main__":
    CaptureApp().mainloop()
