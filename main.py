"""Capture Facile — captures photo et vidéo pour Windows."""
import json
import re
import ctypes
import os
import queue
import subprocess
import time
import imageio_ffmpeg
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, Canvas, Toplevel
from PIL import ImageTk, ImageOps

import customtkinter as ctk
from PIL import Image, ImageGrab
from pynput import keyboard
from trailer_studio import open_trailer_studio

APP_NAME = "Capture Facile"
CONFIG_FILE = Path(os.environ.get("APPDATA", str(Path.home()))) / "CaptureFacile" / "settings.json"
FORMATS = {"PNG": ".png", "JPEG": ".jpg"}
CAPTURE_ENGINES = ["Pillow (Windows)", "MSS (Windows GDI)", "DXGI (DirectX)", "Capture Native (C++ / GDI)", "Capture DirectX 11 (C++ / DXGI) [v6]"]
VIDEO_ENCODERS = ["CPU (H.264)", "NVIDIA NVENC (GPU)"]
AUDIO_MODES = ["Sans audio", "Microphone", "Son du PC", "PC + microphone"]
CAPTURE_TARGETS = ["Tous les écrans", "Écran principal"]
HOTKEYS = {"Impr. écran": "print_screen", **{f"F{i}": f"f{i}" for i in range(1, 13)}}
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
    defaults = {"folder": str(default_download_dir()), "image_hotkey": "F1", "video_hotkey": "F2", "format": "JPEG", "capture_engine": CAPTURE_ENGINES[0], "capture_target": CAPTURE_TARGETS[0], "video_encoder": VIDEO_ENCODERS[0], "video_fps": "60", "video_region": "", "audio_mode": "Sans audio", "audio_mic": "", "audio_pc": ""}
    try:
        settings = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(settings, dict):
            for key in defaults:
                if key in settings and isinstance(settings[key], str):
                    defaults[key] = settings[key]
    except (OSError, ValueError):
        pass
    if defaults["image_hotkey"] not in HOTKEYS:
        defaults["image_hotkey"] = "F1"
    if defaults["video_hotkey"] not in HOTKEYS or defaults["image_hotkey"] == defaults["video_hotkey"]:
        defaults["video_hotkey"] = "F2" if defaults["image_hotkey"] != "F2" else "F3"
    if defaults["audio_mode"] not in AUDIO_MODES:
        defaults["audio_mode"] = "Sans audio"
    if defaults["format"] not in FORMATS:
        defaults["format"] = "JPEG"
    if defaults["capture_engine"] not in CAPTURE_ENGINES:
        defaults["capture_engine"] = CAPTURE_ENGINES[0]
    if defaults["capture_target"] not in CAPTURE_TARGETS:
        defaults["capture_target"] = CAPTURE_TARGETS[0]
    if defaults["video_encoder"] not in VIDEO_ENCODERS:
        defaults["video_encoder"] = VIDEO_ENCODERS[0]
    if defaults["video_fps"] not in ("30", "60", "120", "240"):
        defaults["video_fps"] = "60"
    return defaults


def native_capture(target):
    """Win32 capture implemented by our own C++ DLL, no Pillow/MSS/DXGI grabber.

    Still uses the documented Win32 desktop API, respecting protected surfaces.
    """
    if sys.platform != "win32":
        raise RuntimeError("Capture Native est uniquement disponible sur Windows.")
    dll_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "capture_native.dll"
    if not dll_path.is_file():
        raise RuntimeError("Moteur C++ absent de cet EXE. Vérifiez la compilation GitHub Actions.")
    user32 = ctypes.windll.user32
    if target == "Tous les écrans":
        left = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        top = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
    else:
        left, top = 0, 0
        width = user32.GetSystemMetrics(0)
        height = user32.GetSystemMetrics(1)
    if width <= 0 or height <= 0 or width * height > 100_000_000:
        raise RuntimeError("Dimensions d’écran invalides.")
    dll = ctypes.WinDLL(str(dll_path))
    fn = dll.CaptureDesktopBGRA
    fn.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    fn.restype = ctypes.c_int
    buf = ctypes.create_string_buffer(width * height * 4)
    error_code = fn(buf, left, top, width, height)
    if error_code != 0:
        raise RuntimeError(f"Capture Native Win32 indisponible (erreur {error_code}).")
    return Image.frombytes("RGB", (width, height), buf.raw, "raw", "BGRX")



def native_dxgi_capture(target):
    """Independent D3D11/DXGI photo capture engine; primary display only."""
    if target != "Écran principal":
        raise RuntimeError("Le moteur DirectX C++ v6 prend en charge l'écran principal uniquement. Sélectionnez 'Écran principal'.")
    if sys.platform != "win32":
        raise RuntimeError("Ce moteur DirectX 11 nécessite Windows.")
    dll_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "capture_dxgi.dll"
    if not dll_path.is_file():
        raise RuntimeError("capture_dxgi.dll introuvable dans l'EXE.")
    dll = ctypes.WinDLL(str(dll_path))
    fn = dll.CaptureDXGIPrimaryBGRA
    fn.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
    fn.restype = ctypes.c_int
    width = ctypes.c_int(0)
    height = ctypes.c_int(0)
    # First call obtains actual dimensions; buffer is deliberately absent.
    result = fn(None, 0, ctypes.byref(width), ctypes.byref(height))
    messages = {
        1: "Arguments invalides", 2: "Initialisation DXGI impossible",
        3: "Écran principal introuvable", 4: "Périphérique Direct3D indisponible",
        5: "Duplication non autorisée (session ou pilote)",
        6: "Aucune nouvelle image disponible", 7: "Échec de récupération de l'image",
        8: "Tampon trop petit", 9: "Allocation mémoire graphique impossible",
        10: "Lecture de texture impossible", 11: "Format ou orientation d'écran non pris en charge"
    }
    if result != 8 or width.value < 1 or height.value < 1:
        raise RuntimeError(f"DirectX 11 : {messages.get(result, 'Erreur')} (code {result}).")
    pixels = width.value * height.value
    if pixels > 100_000_000:
        raise RuntimeError("Résolution de capture trop élevée.")
    buffer = ctypes.create_string_buffer(pixels * 4)
    # Retry one time if the desktop frame changed / timed out.
    for attempt in range(2):
        width2, height2 = ctypes.c_int(), ctypes.c_int()
        result = fn(buffer, len(buffer), ctypes.byref(width2), ctypes.byref(height2))
        if result == 6 and attempt == 0:
            continue
        if result != 0:
            raise RuntimeError(f"DirectX 11 : {messages.get(result, 'Erreur')} (code {result}).")
        if (width2.value, height2.value) != (width.value, height.value):
            raise RuntimeError("Résolution modifiée pendant la capture : réessayez.")
        return Image.frombytes("RGB", (width.value, height.value), buffer.raw, "raw", "BGRX")
    raise RuntimeError("DirectX 11 : délai de capture dépassé.")


def suspected_black_frame(image):
    """Heuristic only; dark scenes are allowed and should still be saved."""
    sample = image.convert("RGB")
    sample.thumbnail((96, 54))
    lo, hi = sample.getextrema()[0], sample.getextrema()[1], sample.getextrema()[2]
    return max(hi[1], lo[1], sample.getextrema()[2][1]) < 9

def grab_with_engine(engine, target):
    """Capture ordinary desktop pixels; protected surfaces remain protected."""
    all_screens = target == "Tous les écrans"
    if engine == CAPTURE_ENGINES[0]:
        return ImageGrab.grab(all_screens=all_screens)
    if engine == CAPTURE_ENGINES[1]:
        try:
            import mss
        except ImportError as exc:
            raise RuntimeError("MSS n'est pas inclus dans cette compilation.") from exc
        with mss.mss() as device:
            # monitor 0 is the entire virtual desktop; monitor 1 is primary.
            index = 0 if all_screens else 1
            if index >= len(device.monitors):
                raise RuntimeError("Moniteur introuvable.")
            shot = device.grab(device.monitors[index])
            return Image.frombytes("RGB", shot.size, shot.rgb)
    if engine == CAPTURE_ENGINES[2]:
        if all_screens:
            raise RuntimeError("DXGI capture seulement l'écran principal dans cette version. Choisissez 'Écran principal'.")
        try:
            import dxcam
        except ImportError as exc:
            raise RuntimeError("DXGI n'est pas inclus dans cette compilation.") from exc
        camera = None
        try:
            camera = dxcam.create(output_idx=0, output_color="RGB", backend="dxgi", processor_backend="numpy")
            frame = camera.grab()
            if frame is None:
                raise RuntimeError("DirectX n'a fourni aucune image (écran indisponible ou protégé).")
            return Image.fromarray(frame)
        finally:
            if camera is not None:
                camera.release()
    if engine == CAPTURE_ENGINES[3]:
        return native_capture(target)
    if engine == CAPTURE_ENGINES[4]:
        return native_dxgi_capture(target)
    raise RuntimeError(f"Moteur inconnu : {engine}")


def list_dshow_audio_devices():
    """Return actual Windows DirectShow audio capture device names reported by FFmpeg.

    A loopback/What-U-Hear/Stereo Mix device must already be installed/enabled
    to record PC audio; this function never disables audio content protection.
    """
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    args = [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]
    result = subprocess.run(args, capture_output=True, text=True, timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    devices = []
    audio_section = False
    for line in (result.stderr or "").splitlines():
        if "DirectShow audio devices" in line:
            audio_section = True
            continue
        if "DirectShow video devices" in line:
            audio_section = False
            continue
        if not audio_section or "Alternative name" in line:
            continue
        match = re.search(r'"([^"\r\n]+)"', line)
        if match:
            devices.append(match.group(1))
    return list(dict.fromkeys(devices))




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
        self.recording = False
        self.finalizing_video = False
        self.video_process = None
        self.video_path = None
        self.video_log = None
        self.video_log_path = None
        self.current_images = []
        self.video_region = None
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
        header.pack(fill="x", padx=28, pady=(20, 8))
        self._label(header, "◉  CAPTURE FACILE", 25, TEXT, "bold").pack(side="left")
        self._label(header, "PHOTO  •  VIDÉO  •  BIBLIOTHÈQUE", 11, MUTED).pack(side="right", pady=11)

        hero = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=20)
        hero.pack(fill="x", padx=28, pady=(5, 14))
        self._label(hero, "Une touche pour la photo. Une touche pour la vidéo.", 18, TEXT, "bold").pack(anchor="w", padx=24, pady=(17, 2))
        self._label(hero, "Appuyez de nouveau sur la touche vidéo pour terminer le MP4.", 12, MUTED).pack(anchor="w", padx=24)
        controls = ctk.CTkFrame(hero, fg_color="transparent")
        controls.pack(fill="x", padx=24, pady=(14, 12))
        self._button(controls, "📷  Capture JPEG / PNG", self.capture, primary=True, width=210).pack(side="left", padx=(0, 10))
        self._button(controls, "▣  Zone", self.capture_zone, width=100).pack(side="left", padx=(0, 10))
        self.video_button = ctk.CTkButton(controls, text="●  Démarrer la vidéo", command=self.toggle_video,
                                          width=215, height=38, fg_color="#B54560", hover_color="#96354D",
                                          text_color=TEXT, corner_radius=11, font=("Segoe UI", 13, "bold"))
        self.video_button.pack(side="left")
        self.record_label = self._label(controls, "●  Aucune vidéo en cours", 12, MUTED)
        self.record_label.pack(side="left", padx=16)

        opts = ctk.CTkFrame(hero, fg_color="transparent")
        opts.pack(fill="x", padx=24, pady=(0, 18))
        self._label(opts, "TOUCHE PHOTO", 11, MUTED, "bold").grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.image_hotkey_var = ctk.StringVar(value=self.settings["image_hotkey"])
        ctk.CTkOptionMenu(opts, values=list(HOTKEYS), variable=self.image_hotkey_var,
                          command=lambda value: self.change_hotkey("image_hotkey", value),
                          width=115, fg_color=CARD, button_color="#344661").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=(4, 0))
        self._label(opts, "TOUCHE VIDÉO", 11, MUTED, "bold").grid(row=0, column=1, sticky="w", padx=(0, 12))
        self.video_hotkey_var = ctk.StringVar(value=self.settings["video_hotkey"])
        ctk.CTkOptionMenu(opts, values=list(HOTKEYS), variable=self.video_hotkey_var,
                          command=lambda value: self.change_hotkey("video_hotkey", value),
                          width=115, fg_color=CARD, button_color="#344661").grid(row=1, column=1, sticky="w", padx=(0, 12), pady=(4, 0))
        self._label(opts, "FORMAT PHOTO", 11, MUTED, "bold").grid(row=0, column=2, sticky="w", padx=(0, 12))
        self.format_var = ctk.StringVar(value=self.settings["format"])
        ctk.CTkOptionMenu(opts, values=list(FORMATS), variable=self.format_var, command=self.change_format,
                          width=100, fg_color=CARD, button_color="#344661").grid(row=1, column=2, sticky="w", padx=(0, 15), pady=(4, 0))
        self._label(opts, "DOSSIER DE DESTINATION", 11, MUTED, "bold").grid(row=0, column=3, sticky="w")
        folder_row = ctk.CTkFrame(opts, fg_color="transparent")
        folder_row.grid(row=1, column=3, sticky="ew", pady=(4, 0))
        self.folder_label = self._label(folder_row, self._short_path(), 12, TEXT)
        self.folder_label.pack(side="left", padx=(0, 8))
        self._button(folder_row, "Modifier", self.choose_folder, width=85).pack(side="left")
        opts.grid_columnconfigure(3, weight=1)

        engine_row = ctk.CTkFrame(hero, fg_color="transparent")
        engine_row.pack(fill="x", padx=24, pady=(0, 17))
        self._label(engine_row, "MOTEUR PHOTO", 11, MUTED, "bold").pack(side="left", padx=(0, 9))
        self.engine_var = ctk.StringVar(value=self.settings["capture_engine"])
        ctk.CTkOptionMenu(engine_row, values=CAPTURE_ENGINES, variable=self.engine_var,
                          command=lambda value: self.change_capture_setting("capture_engine", value),
                          width=190, fg_color=CARD, button_color="#344661").pack(side="left", padx=(0, 20))
        self._label(engine_row, "ÉCRANS", 11, MUTED, "bold").pack(side="left", padx=(0, 9))
        self.target_var = ctk.StringVar(value=self.settings["capture_target"])
        ctk.CTkOptionMenu(engine_row, values=CAPTURE_TARGETS, variable=self.target_var,
                          command=lambda value: self.change_capture_setting("capture_target", value),
                          width=150, fg_color=CARD, button_color="#344661").pack(side="left")
        video_opts = ctk.CTkFrame(hero, fg_color="transparent")
        video_opts.pack(fill="x", padx=24, pady=(0, 12))
        self._label(video_opts, "ENCODAGE VIDÉO", 11, MUTED, "bold").pack(side="left", padx=(0, 9))
        self.encoder_var = ctk.StringVar(value=self.settings["video_encoder"])
        ctk.CTkOptionMenu(video_opts, values=VIDEO_ENCODERS, variable=self.encoder_var,
                          command=lambda value: self.change_capture_setting("video_encoder", value),
                          width=205, fg_color=CARD, button_color="#344661").pack(side="left")
        self._label(video_opts, "FPS", 11, MUTED, "bold").pack(side="left", padx=(14, 6))
        self.fps_var = ctk.StringVar(value=self.settings["video_fps"])
        ctk.CTkOptionMenu(video_opts, values=["30", "60", "120", "240"], variable=self.fps_var,
                          command=lambda value: self.change_capture_setting("video_fps", value),
                          width=82, fg_color=CARD, button_color="#344661").pack(side="left", padx=(0, 10))
        self._button(video_opts, "Zone vidéo", self.select_video_region, width=105).pack(side="left", padx=4)
        self._button(video_opts, "Écran entier", self.reset_video_region, width=105).pack(side="left", padx=4)
        audio_opts = ctk.CTkFrame(hero, fg_color="transparent")
        audio_opts.pack(fill="x", padx=24, pady=(0, 10))
        self._label(audio_opts, "SON VIDÉO", 11, MUTED, "bold").pack(side="left", padx=(0, 9))
        self.audio_mode_var = ctk.StringVar(value=self.settings["audio_mode"])
        ctk.CTkOptionMenu(audio_opts, values=AUDIO_MODES, variable=self.audio_mode_var,
                          command=lambda value: self.change_capture_setting("audio_mode", value),
                          width=155, fg_color=CARD, button_color="#344661").pack(side="left", padx=(0, 8))
        self._button(audio_opts, "Détecter les appareils", self.detect_audio, width=165).pack(side="left", padx=4)
        self._label(audio_opts, "Micro / Stereo Mix : sélectionnez les appareils ci-dessous", 11, MUTED).pack(side="left", padx=8)
        device_opts = ctk.CTkFrame(hero, fg_color="transparent")
        device_opts.pack(fill="x", padx=24, pady=(0, 11))
        self._label(device_opts, "MICRO", 11, MUTED, "bold").pack(side="left", padx=(0, 7))
        self.mic_var = ctk.StringVar(value=self.settings["audio_mic"] or "Non sélectionné")
        self.mic_menu = ctk.CTkOptionMenu(device_opts, values=["Non sélectionné"], variable=self.mic_var,
                            command=lambda value: self.select_audio_device("audio_mic", value), width=230,
                            fg_color=CARD, button_color="#344661")
        self.mic_menu.pack(side="left", padx=(0, 15))
        self._label(device_opts, "SON PC", 11, MUTED, "bold").pack(side="left", padx=(0, 7))
        self.pc_var = ctk.StringVar(value=self.settings["audio_pc"] or "Non sélectionné")
        self.pc_menu = ctk.CTkOptionMenu(device_opts, values=["Non sélectionné"], variable=self.pc_var,
                            command=lambda value: self.select_audio_device("audio_pc", value), width=230,
                            fg_color=CARD, button_color="#344661")
        self.pc_menu.pack(side="left")
        self._label(hero, "Audio v10 : les entrées DirectShow doivent être visibles dans Windows. Pour le son PC, Stereo Mix ou équivalent est nécessaire.",
                    11, MUTED).pack(anchor="w", padx=24, pady=(0, 7))
        self.after(400, self.detect_audio)
        self._label(hero, "Vidéo : 60 FPS conseillé. Capture de zone et MP4 audio AAC si un appareil est configuré.",
                    11, MUTED).pack(anchor="w", padx=24, pady=(0, 7))
        self._label(hero, "Éditeur v8 : recadrage et fonds d’écran. La capture de zone utilise Pillow (écran principal). Les contenus protégés peuvent rester noirs.",
                    11, MUTED).pack(anchor="w", padx=24, pady=(0, 13))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=30, pady=(0, 9))
        self._label(actions, "BIBLIOTHÈQUE PHOTO & VIDÉO", 17, TEXT, "bold").pack(side="left")
        self.count_label = self._label(actions, "", 12, MUTED)
        self.count_label.pack(side="left", padx=13)
        self._button(actions, "Ouvrir le dossier", self.open_folder, width=156).pack(side="right", padx=(8, 0))
        self._button(actions, "Actualiser", self.refresh_gallery, width=110).pack(side="right")
        self._button(actions, "🎬 Bande-annonce", self.open_trailer_studio, width=155).pack(side="right", padx=(0, 8))

        self.gallery = ctk.CTkScrollableFrame(self, fg_color=PANEL, corner_radius=16)
        self.gallery.pack(fill="both", expand=True, padx=28, pady=(0, 11))
        self.gallery.bind("<Configure>", self._gallery_resize)
        self.last_cols = 0
        bottom = ctk.CTkFrame(self, fg_color=BG)
        bottom.pack(fill="x", padx=30, pady=(0, 14))
        self.status = self._label(bottom, "Prêt à capturer", 12, ACCENT)
        self.status.pack(side="left")
        self._label(bottom, "MP4 H.264 + AAC selon appareils • touches F1/F2", 11, MUTED).pack(side="right")

    def open_trailer_studio(self):
        if self.recording or self.finalizing_video:
            messagebox.showwarning(APP_NAME, "Terminez la capture vidéo avant le montage.")
            return
        open_trailer_studio(self, self.settings["folder"], self._trailer_saved)

    def _trailer_saved(self, path):
        self.refresh_gallery()
        self._set_status(f"✓ Bande-annonce créée : {path.name}")

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
        # on_release avoids Windows auto-repeat toggling recording multiple times.
        for action, setting in (("capture", "image_hotkey"), ("video_toggle", "video_hotkey")):
            try:
                key_name = HOTKEYS[self.settings[setting]]
                if key == getattr(keyboard.Key, key_name):
                    self.events.put((action, None))
                    return
            except (AttributeError, ValueError):
                continue

    def _poll(self):
        while True:
            try:
                action, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if action == "capture":
                self.capture()
            elif action == "video_toggle":
                self.toggle_video()
            elif action == "video_stopped":
                self._set_status(f"✓ Vidéo enregistrée : {payload.name}")
                self.refresh_gallery()
            elif action == "record_idle":
                self.finalizing_video = False
                self.record_label.configure(text="●  Aucune vidéo en cours", text_color=MUTED)
            elif action == "editor_ready":
                editor, state, duration, start_slider, end_slider, info, export_button, refresh, preview, change_start, change_end = payload
                if not state["closed"] and editor.winfo_exists():
                    state["duration"] = duration
                    state["start"], state["end"] = 0.0, duration
                    for slider in (start_slider, end_slider):
                        slider.configure(to=duration, state="normal")
                    start_slider.set(0)
                    end_slider.set(duration)
                    info.configure(text=f"Durée totale : {duration:.1f} s • glissez les curseurs pour choisir l'extrait")
                    export_button.configure(state="normal")
                    refresh()
                    preview(0.0)
            elif action == "editor_error":
                editor, state, error, info = payload
                if not state["closed"] and editor.winfo_exists():
                    info.configure(text=f"Erreur : {error}")
            elif action == "editor_frame":
                editor, state, sequence, frame, label = payload
                if not state["closed"] and editor.winfo_exists() and sequence == state["seq"]:
                    preview_image = ctk.CTkImage(light_image=frame, dark_image=frame, size=frame.size)
                    state["preview_image"] = preview_image
                    label.configure(image=preview_image, text="")
            elif action == "editor_frame_error":
                editor, state, sequence, error, label = payload
                if not state["closed"] and editor.winfo_exists() and sequence == state["seq"]:
                    label.configure(text=f"Aperçu indisponible : {error}", image=None)
            elif action == "editor_export_finished":
                editor, state, info, button = payload
                if not state["closed"] and editor.winfo_exists():
                    info.configure(text="Export terminé. Choisissez un autre extrait si besoin.")
                    button.configure(state="normal")
            elif action == "trim_done":
                self.busy = False
                self._set_status(f"✓ Extrait MP4 enregistré : {payload.name}")
                self.refresh_gallery()
            elif action == "trim_error":
                self.busy = False
                self._set_status(str(payload), True)
                messagebox.showerror(APP_NAME, str(payload))
            elif action == "video_error":
                self._set_status(str(payload), True)
                messagebox.showerror(APP_NAME, str(payload))
            elif action == "saved":
                self.busy = False
                saved_path, black = payload
                self._set_status((f"⚠ Image très sombre : {saved_path.name} (vérifiez la protection vidéo)" if black else f"✓ Capture enregistrée : {saved_path.name}"))
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
        engine = self.settings["capture_engine"]
        target = self.settings["capture_target"]

        def job():
            try:
                folder.mkdir(parents=True, exist_ok=True)
                # all_screens=True covers extended Windows displays, not only the primary.
                screenshot = grab_with_engine(engine, target)
                name = f"capture_{datetime.now():%Y-%m-%d_%H-%M-%S_%f}" + suffix
                output = folder / name
                if image_format == "JPEG":
                    screenshot = screenshot.convert("RGB")
                    screenshot.save(output, "JPEG", quality=94)
                else:
                    screenshot.save(output, "PNG")
                self.events.put(("saved", (output, suspected_black_frame(screenshot))))
            except Exception as exc:
                self.events.put(("error", f"Capture impossible : {exc}"))
        threading.Thread(target=job, daemon=True).start()

    def toggle_video(self):
        if self.recording:
            self.stop_video()
        else:
            self.start_video()

    def select_audio_device(self, key, value):
        self.settings[key] = "" if value == "Non sélectionné" else value
        self._save_settings()
        self._set_status(f"Appareil audio sélectionné : {value}")

    def detect_audio(self):
        if self.recording or self.finalizing_video:
            return
        try:
            devices = list_dshow_audio_devices()
            values = ["Non sélectionné", *devices]
            self.mic_menu.configure(values=values)
            self.pc_menu.configure(values=values)
            for key, variable in (("audio_mic", self.mic_var), ("audio_pc", self.pc_var)):
                previous = self.settings[key]
                if previous not in devices:
                    previous = ""
                    self.settings[key] = ""
                variable.set(previous or "Non sélectionné")
            self._save_settings()
            if devices:
                self._set_status(f"{len(devices)} appareil(s) audio détecté(s). Choisissez votre micro et votre entrée Stereo Mix.")
            else:
                self._set_status("Aucune entrée audio DirectShow trouvée. Le mode sans audio reste disponible.", True)
        except Exception as exc:
            self._set_status(f"Détection audio indisponible : {exc}", True)

    def start_video(self):
        if self.finalizing_video or self.recording:
            return
        try:
            folder = Path(self.settings["folder"])
            folder.mkdir(parents=True, exist_ok=True)
            output = folder / f"video_{datetime.now():%Y-%m-%d_%H-%M-%S_%f}.mp4"
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            # gdigrab captures the Windows desktop. Even resolutions are required by yuv420p.
            use_nvenc = self.settings["video_encoder"] == VIDEO_ENCODERS[1]
            codec_options = (["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"]
                             if use_nvenc else ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23"])
            if use_nvenc:
                check = subprocess.run([ffmpeg, "-hide_banner", "-encoders"],
                                       capture_output=True, text=True, timeout=10,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if "h264_nvenc" not in check.stdout:
                    raise RuntimeError("Ce FFmpeg n'inclut pas NVENC. Sélectionnez CPU (H.264).")
            # A video region is applied by gdigrab, not by cropping a finished MP4.
            fps = int(self.settings.get("video_fps", "60"))
            region = self.video_region
            capture_args = []
            if region is not None:
                left, top, right, bottom = region
                width, height = right - left, bottom - top
                # H.264 4:2:0 requires even dimensions.
                width -= width % 2
                height -= height % 2
                if width < 16 or height < 16:
                    raise RuntimeError("La zone vidéo doit mesurer au moins 16 × 16 pixels.")
                capture_args = ["-offset_x", str(left), "-offset_y", str(top),
                                "-video_size", f"{width}x{height}"]
            mode = self.settings.get("audio_mode", "Sans audio")
            names = []
            if mode in ("Son du PC", "PC + microphone"):
                names.append(self.settings.get("audio_pc", ""))
            if mode in ("Microphone", "PC + microphone"):
                names.append(self.settings.get("audio_mic", ""))
            if any(not name for name in names):
                raise RuntimeError("Choisissez les appareils audio nécessaires dans les listes (bouton Détecter les appareils).")
            if len(set(names)) != len(names):
                raise RuntimeError("Choisissez deux appareils différents pour enregistrer PC + microphone.")
            command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                       "-thread_queue_size", "1024", "-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1",
                       *capture_args, "-i", "desktop"]
            for name in names:
                command.extend(["-thread_queue_size", "1024", "-f", "dshow", "-i", f"audio={name}"])
            command.extend(["-map", "0:v:0"])
            if len(names) == 2:
                command.extend(["-filter_complex", "[1:a:0][2:a:0]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[aout]",
                                "-map", "[aout]"])
            elif len(names) == 1:
                command.extend(["-map", "1:a:0"])
            command.extend(["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", *codec_options,
                            "-pix_fmt", "yuv420p"])
            if names:
                command.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000"])
            command.extend(["-movflags", "+faststart", str(output)])
            log_path = CONFIG_FILE.parent / "ffmpeg-last.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = log_path.open("wb")
            try:
                proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                        stderr=log_file, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:
                log_file.close()
                raise
            self.video_process = proc
            self.video_path = output
            self.video_log = log_file
            self.video_log_path = log_path
            self.recording = True
            self.record_started = time.monotonic()
            self.video_button.configure(text="■  Arrêter la vidéo", fg_color="#C73F50")
            self.record_label.configure(text="● REC  00:00", text_color="#FF7182")
            self._set_status("Enregistrement vidéo en cours…")
            self._update_recording_clock()
        except Exception as exc:
            self.events.put(("video_error", f"Démarrage vidéo impossible : {exc}"))

    def _update_recording_clock(self):
        if not self.recording:
            return
        seconds = int(time.monotonic() - self.record_started)
        self.record_label.configure(text=f"● REC  {seconds // 60:02d}:{seconds % 60:02d}")
        if self.video_process and self.video_process.poll() is not None:
            self.stop_video()
        else:
            self.after(500, self._update_recording_clock)

    def stop_video(self):
        if not self.recording:
            return
        self.recording = False
        self.finalizing_video = True
        self.video_button.configure(text="●  Démarrer la vidéo", fg_color="#B54560")
        self.record_label.configure(text="●  Finalisation MP4…", text_color=MUTED)
        proc = self.video_process
        output = self.video_path
        log_file = self.video_log
        log_path = self.video_log_path
        self.video_process = None
        self.video_log = None
        self._set_status("Finalisation de la vidéo…")

        def finalize():
            try:
                if proc.poll() is None:
                    try:
                        proc.stdin.write(b"q\n")
                        proc.stdin.flush()
                    except (OSError, ValueError):
                        pass
                    try:
                        proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                if log_file:
                    log_file.close()
                if proc.returncode != 0 or not output.exists() or output.stat().st_size == 0:
                    details = log_path.read_text(encoding="utf-8", errors="replace")[-700:] if log_path.exists() else ""
                    raise RuntimeError(details or f"FFmpeg code {proc.returncode}")
                self.events.put(("video_stopped", output))
            except Exception as exc:
                if log_file and not log_file.closed:
                    log_file.close()
                self.events.put(("video_error", f"Enregistrement vidéo échoué : {exc}"))
            finally:
                self.events.put(("record_idle", None))
        threading.Thread(target=finalize, daemon=True).start()

    def change_capture_setting(self, key, value):
        self.settings[key] = value
        self._save_settings()
        self._set_status(f"Réglage sauvegardé : {value}")

    def change_hotkey(self, setting, value):
        other = "video_hotkey" if setting == "image_hotkey" else "image_hotkey"
        if value == self.settings[other]:
            # Swap assignments rather than allowing two actions on the same key.
            self.settings[other] = self.settings[setting]
        self.settings[setting] = value
        self.image_hotkey_var.set(self.settings["image_hotkey"])
        self.video_hotkey_var.set(self.settings["video_hotkey"])
        self._save_settings()
        self._set_status("Touches mises à jour")

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
            self.current_images = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".mp4")),
                                         key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            self.current_images = []
        self.count_label.configure(text=f"{len(self.current_images)} fichier(s)")
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
                if path.suffix.lower() == ".mp4":
                    self._label(tile, "▶  VIDÉO MP4", 19, ACCENT, "bold").pack(padx=8, pady=(52, 46))
                else:
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
            ctk.CTkButton(row, text="Ouvrir", command=lambda p=path: self.open_image(p), width=52, height=28, fg_color="#33455E").pack(side="left", padx=2)
            ctk.CTkButton(row, text="Renommer", command=lambda p=path: self.rename_image(p), width=76, height=28, fg_color="#33455E").pack(side="left", padx=2)
            if path.suffix.lower() == ".mp4":
                ctk.CTkButton(row, text="Couper", command=lambda p=path: self.trim_video(p), width=53, height=28, fg_color="#33455E").pack(side="left", padx=2)
            else:
                ctk.CTkButton(row, text="Éditer", command=lambda p=path: self.edit_image(p), width=48, height=28, fg_color="#33455E").pack(side="left", padx=2)
            ctk.CTkButton(row, text="×", command=lambda p=path: self.delete_image(p), width=27, height=28, fg_color="#6C3548", hover_color="#AA4058").pack(side="left", padx=2)
        for col in range(cols):
            self.gallery.grid_columnconfigure(col, weight=1)
        if len(self.current_images) > 180:
            self._label(self.gallery, "180 images affichées. Ouvrez le dossier pour voir les autres.", 11, MUTED).grid(row=(180 // cols) + 1, column=0, columnspan=cols, pady=12)

    def reset_video_region(self):
        if self.recording:
            self._set_status("Arrêtez la vidéo pour modifier la zone.", True)
            return
        self.video_region = None
        self._set_status("Vidéo : écran entier sélectionné")

    def select_video_region(self):
        if self.busy or self.recording or self.finalizing_video:
            self._set_status("Terminez l'opération en cours avant de choisir une zone.", True)
            return
        self.busy = True
        self.withdraw()
        self.after(350, self._show_video_region_overlay)

    def _show_video_region_overlay(self):
        try:
            image = ImageGrab.grab()
            overlay = Toplevel(self)
            overlay.overrideredirect(True)
            overlay.attributes("-topmost", True)
            overlay.geometry(f"{image.width}x{image.height}+0+0")
            canvas = Canvas(overlay, width=image.width, height=image.height,
                            highlightthickness=0, cursor="crosshair")
            canvas.pack(fill="both", expand=True)
            preview = ImageTk.PhotoImage(image)
            canvas.image_ref = preview
            canvas.create_image(0, 0, image=preview, anchor="nw")
            canvas.create_rectangle(0, 0, image.width, 36, fill="#142035", outline="")
            canvas.create_text(14, 18, anchor="w", fill="white",
                               text="ZONE VIDÉO — glissez pour sélectionner, Échap pour annuler")
            state = {"start": None, "rectangle": None, "done": False}

            def close():
                overlay.destroy()
                self.deiconify()
                self.busy = False

            def cancel(_event=None):
                if not state["done"]:
                    state["done"] = True
                    close()
                    self._set_status("Sélection vidéo annulée")

            def press(event):
                state["start"] = (event.x, event.y)
                if state["rectangle"]:
                    canvas.delete(state["rectangle"])
                state["rectangle"] = canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                                outline="#29D3B0", width=3)

            def drag(event):
                if state["start"]:
                    x, y = state["start"]
                    canvas.coords(state["rectangle"], x, y, event.x, event.y)

            def finish(event):
                if state["done"] or not state["start"]:
                    return
                x, y = state["start"]
                rect = (max(0, min(x, event.x)), max(0, min(y, event.y)),
                        min(image.width, max(x, event.x)), min(image.height, max(y, event.y)))
                if rect[2] - rect[0] < 16 or rect[3] - rect[1] < 16:
                    return
                state["done"] = True
                self.video_region = rect
                close()
                self._set_status(f"Zone vidéo sélectionnée : {rect[2]-rect[0]} × {rect[3]-rect[1]} pixels")

            canvas.bind("<ButtonPress-1>", press)
            canvas.bind("<B1-Motion>", drag)
            canvas.bind("<ButtonRelease-1>", finish)
            overlay.bind("<Escape>", cancel)
            overlay.focus_force()
        except Exception as exc:
            self.deiconify()
            self.busy = False
            messagebox.showerror(APP_NAME, f"Sélection de zone impossible : {exc}")

    def trim_video(self, path):
        """Éditeur temporel non destructif, miniatures récupérées avec FFmpeg."""
        if self.busy or self.recording or self.finalizing_video:
            self._set_status("Terminez l'opération en cours avant de monter une vidéo.", True)
            return
        editor = ctk.CTkToplevel(self)
        editor.title(f"Montage vidéo • {path.name}")
        editor.geometry("850x690")
        editor.minsize(620, 560)
        editor.configure(fg_color=BG)
        editor.after(120, editor.lift)
        state = {"closed": False, "duration": 0.0, "seq": 0, "after_id": None,
                 "start": 0.0, "end": 0.0, "preview_image": None}

        def close():
            state["closed"] = True
            if state["after_id"] is not None:
                try:
                    editor.after_cancel(state["after_id"])
                except Exception:
                    pass
            editor.destroy()

        editor.protocol("WM_DELETE_WINDOW", close)
        body = ctk.CTkFrame(editor, fg_color=PANEL, corner_radius=16)
        body.pack(fill="both", expand=True, padx=18, pady=18)
        self._label(body, "MONTAGE EXPRESS", 21, TEXT, "bold").pack(anchor="w", padx=22, pady=(18, 2))
        self._label(body, path.name, 12, MUTED).pack(anchor="w", padx=22, pady=(0, 10))
        preview_label = ctk.CTkLabel(body, text="Chargement de la vidéo…", width=620,
                                     height=300, fg_color="#0C111D", corner_radius=10)
        preview_label.pack(padx=16, pady=(0, 10))
        info = self._label(body, "Analyse de la durée…", 12, MUTED)
        info.pack(pady=(0, 8))
        control = ctk.CTkFrame(body, fg_color="transparent")
        control.pack(fill="x", padx=22)
        self._label(control, "DÉBUT", 12, ACCENT, "bold").pack(anchor="w")
        start_label = self._label(control, "00:00.0", 12, TEXT)
        start_label.pack(anchor="e")
        start_slider = ctk.CTkSlider(control, from_=0, to=1, number_of_steps=1000,
                                     progress_color=ACCENT, button_color=ACCENT)
        start_slider.pack(fill="x", pady=(0, 12))
        self._label(control, "FIN", 12, ACCENT, "bold").pack(anchor="w")
        end_label = self._label(control, "00:00.0", 12, TEXT)
        end_label.pack(anchor="e")
        end_slider = ctk.CTkSlider(control, from_=0, to=1, number_of_steps=1000,
                                   progress_color=ACCENT, button_color=ACCENT)
        end_slider.pack(fill="x", pady=(0, 10))
        result_label = self._label(control, "", 12, MUTED)
        result_label.pack(anchor="w", pady=(0, 10))

        def timestamp(value):
            return f"{int(value // 60):02d}:{value % 60:04.1f}"

        def refresh_values():
            start_label.configure(text=timestamp(state["start"]))
            end_label.configure(text=timestamp(state["end"]))
            result_label.configure(text=f"Extrait sélectionné : {max(0, state['end'] - state['start']):.1f} seconde(s)")

        def render_preview(seconds):
            if state["closed"]:
                return
            state["seq"] += 1
            sequence = state["seq"]
            def job():
                try:
                    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
                    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                           "-ss", f"{seconds:.3f}", "-i", str(path), "-frames:v", "1",
                           "-vf", "scale=640:360:force_original_aspect_ratio=decrease",
                           "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
                    completed = subprocess.run(cmd, capture_output=True, timeout=25,
                                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if completed.returncode != 0 or not completed.stdout:
                        raise RuntimeError("Aucune image disponible à cette position")
                    from io import BytesIO
                    with Image.open(BytesIO(completed.stdout)) as im:
                        frame = im.convert("RGB").copy()
                    self.events.put(("editor_frame", (editor, state, sequence, frame, preview_label)))
                except Exception as exc:
                    self.events.put(("editor_frame_error", (editor, state, sequence, str(exc), preview_label)))
            threading.Thread(target=job, daemon=True).start()

        def request_preview(seconds):
            if state["after_id"] is not None:
                editor.after_cancel(state["after_id"])
            state["after_id"] = editor.after(220, lambda: render_preview(seconds))

        def adjust_start(value):
            state["start"] = min(float(value), max(0, state["end"] - 0.1))
            start_slider.set(state["start"])
            refresh_values()
            request_preview(state["start"])

        def adjust_end(value):
            state["end"] = max(float(value), min(state["duration"], state["start"] + 0.1))
            end_slider.set(state["end"])
            refresh_values()
            request_preview(max(0, state["end"] - 0.1))

        start_slider.configure(command=adjust_start, state="disabled")
        end_slider.configure(command=adjust_end, state="disabled")
        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x", padx=22, pady=(2, 15))
        play_button = self._button(buttons, "▶ Ouvrir la vidéo", lambda: self.open_image(path), width=174)
        play_button.pack(side="left", padx=(0, 10))
        export_button = self._button(buttons, "Exporter l'extrait MP4", lambda: export(), primary=True, width=230)
        export_button.pack(side="left")
        export_button.configure(state="disabled")

        def export():
            if state["closed"] or self.busy:
                return
            begin, end = state["start"], state["end"]
            if end - begin < 0.1:
                messagebox.showwarning(APP_NAME, "Sélectionnez au moins 0,1 seconde.", parent=editor)
                return
            self.busy = True
            export_button.configure(state="disabled")
            info.configure(text="Encodage de l’extrait en cours…")
            self._set_status("Export vidéo en cours…")
            def job():
                dest = None
                try:
                    dest = path.with_name(path.stem + "_extrait.mp4")
                    index = 2
                    while dest.exists():
                        dest = path.with_name(f"{path.stem}_extrait_{index}.mp4")
                        index += 1
                    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
                    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                           "-ss", f"{begin:.3f}", "-i", str(path), "-t", f"{end-begin:.3f}",
                           "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
                           "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p",
                           "-movflags", "+faststart", str(dest)]
                    completed = subprocess.run(cmd, capture_output=True, text=True,
                                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if completed.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
                        if dest.exists():
                            dest.unlink()
                        raise RuntimeError((completed.stderr or "Export impossible")[-650:])
                    self.events.put(("trim_done", dest))
                except Exception as exc:
                    self.events.put(("trim_error", f"Découpage impossible : {exc}"))
                finally:
                    self.events.put(("editor_export_finished", (editor, state, info, export_button)))
            threading.Thread(target=job, daemon=True).start()

        def analyze():
            try:
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
                result = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)],
                                        capture_output=True, text=True, timeout=20,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                import re
                match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
                if not match:
                    raise RuntimeError("La durée de cette vidéo n'a pas pu être déterminée.")
                h, m, sec = match.groups()
                duration = 3600 * int(h) + 60 * int(m) + float(sec)
                if duration <= 0:
                    raise RuntimeError("Durée vidéo invalide.")
                self.events.put(("editor_ready", (editor, state, duration, start_slider,
                                                  end_slider, info, export_button, refresh_values,
                                                  render_preview, adjust_start, adjust_end)))
            except Exception as exc:
                self.events.put(("editor_error", (editor, state, str(exc), info)))
        threading.Thread(target=analyze, daemon=True).start()

    def capture_zone(self):
        """Zone interactive sur l'écran principal : source Pillow, pas de contournement DRM."""
        if self.busy or self.recording:
            self._set_status("Terminez l'enregistrement ou la capture en cours.", True)
            return
        self.busy = True
        self._set_status("Sélectionnez une zone à la souris (Échap pour annuler).")
        self.withdraw()
        self.after(320, self._show_zone_overlay)

    def _show_zone_overlay(self):
        try:
            screenshot = ImageGrab.grab()
            overlay = Toplevel(self)
            overlay.overrideredirect(True)
            overlay.attributes("-topmost", True)
            # Photo initiale affichée telle quelle, pour un cadrage précis.
            overlay.geometry(f"{screenshot.width}x{screenshot.height}+0+0")
            canvas = Canvas(overlay, width=screenshot.width, height=screenshot.height,
                            highlightthickness=0, cursor="crosshair")
            canvas.pack(fill="both", expand=True)
            preview = ImageTk.PhotoImage(screenshot)
            canvas.create_image(0, 0, image=preview, anchor="nw")
            canvas.image_ref = preview
            canvas.create_rectangle(0, 0, screenshot.width, 33, fill="#142035", outline="")
            canvas.create_text(16, 16, anchor="w", fill="white",
                               text="Glissez pour sélectionner • Échap pour annuler")
            state = {"start": None, "rect": None, "done": False}

            def cancel(_event=None):
                if state["done"]:
                    return
                state["done"] = True
                overlay.destroy()
                self.deiconify()
                self.busy = False
                self._set_status("Sélection annulée")

            def press(event):
                state["start"] = (event.x, event.y)
                if state["rect"]:
                    canvas.delete(state["rect"])
                state["rect"] = canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                        outline="#29D3B0", width=3)
            def drag(event):
                if state["start"] and state["rect"]:
                    x, y = state["start"]
                    canvas.coords(state["rect"], x, y, event.x, event.y)
            def release(event):
                if not state["start"] or state["done"]:
                    return
                x, y = state["start"]
                box = (max(0, min(x, event.x)), max(0, min(y, event.y)),
                       min(screenshot.width, max(x, event.x)),
                       min(screenshot.height, max(y, event.y)))
                if box[2] - box[0] < 4 or box[3] - box[1] < 4:
                    return
                state["done"] = True
                overlay.destroy()
                self.deiconify()
                self.busy = False
                try:
                    folder = Path(self.settings["folder"])
                    folder.mkdir(parents=True, exist_ok=True)
                    kind = self.settings["format"]
                    output = folder / f"zone_{datetime.now():%Y-%m-%d_%H-%M-%S_%f}{FORMATS[kind]}"
                    result = screenshot.crop(box)
                    result.save(output, "JPEG" if kind == "JPEG" else "PNG",
                                **({"quality": 94} if kind == "JPEG" else {}))
                    self.refresh_gallery()
                    self._set_status(f"✓ Zone enregistrée : {output.name}")
                except Exception as exc:
                    messagebox.showerror(APP_NAME, f"Enregistrement de la zone impossible : {exc}")
            canvas.bind("<ButtonPress-1>", press)
            canvas.bind("<B1-Motion>", drag)
            canvas.bind("<ButtonRelease-1>", release)
            overlay.bind("<Escape>", cancel)
            overlay.focus_force()
        except Exception as exc:
            self.deiconify()
            self.busy = False
            messagebox.showerror(APP_NAME, f"Capture de zone impossible : {exc}")

    def edit_image(self, path):
        """Éditeur non destructif : zone libre et export en résolution fond d'écran."""
        try:
            with Image.open(path) as src:
                source = ImageOps.exif_transpose(src).convert("RGB")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Image illisible : {exc}")
            return
        editor = ctk.CTkToplevel(self)
        editor.title(f"Éditeur • {path.name}")
        editor.geometry("970x760")
        editor.minsize(630, 480)
        editor.after(150, editor.lift)
        frame = ctk.CTkFrame(editor, fg_color=PANEL)
        frame.pack(fill="both", expand=True, padx=14, pady=14)
        self._label(frame, "Glissez sur l’image pour choisir un recadrage (facultatif)", 14, TEXT, "bold").pack(pady=10)
        scale = min(850 / source.width, 490 / source.height, 1.0)
        pw, ph = max(1, int(source.width * scale)), max(1, int(source.height * scale))
        preview = source.resize((pw, ph), Image.Resampling.LANCZOS)
        canvas = Canvas(frame, width=pw, height=ph, bg="#121927", highlightthickness=0)
        canvas.pack(pady=8)
        ref = ImageTk.PhotoImage(preview)
        canvas.image_ref = ref
        canvas.create_image(0, 0, image=ref, anchor="nw")
        state = {"origin": None, "rect": None, "box": None}

        def start(event):
            state["origin"] = (event.x, event.y)
            state["box"] = None
            if state["rect"]:
                canvas.delete(state["rect"])
            state["rect"] = canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                    outline="#29D3B0", width=2)
        def move(event):
            if state["origin"]:
                x, y = state["origin"]
                canvas.coords(state["rect"], x, y, event.x, event.y)
        def finish(event):
            if state["origin"]:
                x, y = state["origin"]
                x1, y1, x2, y2 = max(0, min(x, event.x)), max(0, min(y, event.y)), min(pw, max(x, event.x)), min(ph, max(y, event.y))
                if x2 - x1 >= 3 and y2 - y1 >= 3:
                    state["box"] = tuple(int(v / scale) for v in (x1, y1, x2, y2))
                state["origin"] = None
        canvas.bind("<ButtonPress-1>", start)
        canvas.bind("<B1-Motion>", move)
        canvas.bind("<ButtonRelease-1>", finish)
        options = ctk.CTkFrame(frame, fg_color="transparent")
        options.pack(pady=13)
        self._label(options, "EXPORT", 12, MUTED, "bold").pack(side="left", padx=8)
        presets = {"Dimensions originales": None, "Full HD 1920 × 1080": (1920, 1080),
                   "QHD 2560 × 1440": (2560, 1440), "4K 3840 × 2160": (3840, 2160)}
        preset = ctk.StringVar(value="Dimensions originales")
        ctk.CTkOptionMenu(options, variable=preset, values=list(presets), width=225).pack(side="left", padx=8)
        output_format = ctk.StringVar(value="JPEG")
        ctk.CTkOptionMenu(options, variable=output_format, values=["JPEG", "PNG"], width=95).pack(side="left", padx=8)

        def export():
            try:
                result = source.crop(state["box"]) if state["box"] else source.copy()
                dims = presets[preset.get()]
                if dims:
                    # LANCZOS + crop centré : aucune déformation de l'image.
                    result = ImageOps.fit(result, dims, method=Image.Resampling.LANCZOS)
                extension = FORMATS[output_format.get()]
                destination = path.with_name(path.stem + "_edite" + extension)
                counter = 2
                while destination.exists():
                    destination = path.with_name(path.stem + f"_edite_{counter}" + extension)
                    counter += 1
                result.save(destination, "JPEG" if extension == ".jpg" else "PNG",
                            **({"quality": 95} if extension == ".jpg" else {}))
                editor.destroy()
                self.refresh_gallery()
                self._set_status(f"✓ Image exportée : {destination.name}")
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"Export impossible : {exc}", parent=editor)
        self._button(frame, "Enregistrer une nouvelle image", export, primary=True, width=285).pack(pady=(2, 16))

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
            self._set_status(f"Fichier supprimé : {path.name}")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Suppression impossible : {exc}")

    def _close(self):
        if self.recording or self.finalizing_video:
            messagebox.showwarning(APP_NAME, "Arrêtez la vidéo et attendez la finalisation avant de fermer l’application.")
            return
        if self.listener:
            self.listener.stop()
        self.destroy()


if __name__ == "__main__":
    CaptureApp().mainloop()
