"""Capture Facile — captures photo et vidéo pour Windows."""
import json
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
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageGrab
from pynput import keyboard

APP_NAME = "Capture Facile"
CONFIG_FILE = Path(os.environ.get("APPDATA", str(Path.home()))) / "CaptureFacile" / "settings.json"
FORMATS = {"PNG": ".png", "JPEG": ".jpg"}
CAPTURE_ENGINES = ["Pillow (Windows)", "MSS (Windows GDI)", "DXGI (DirectX)", "Capture Native (C++ / GDI)", "Capture DirectX 11 (C++ / DXGI) [v6]"]
VIDEO_ENCODERS = ["CPU (H.264)", "NVIDIA NVENC (GPU)"]
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
    defaults = {"folder": str(default_download_dir()), "image_hotkey": "F1", "video_hotkey": "F2", "format": "JPEG", "capture_engine": CAPTURE_ENGINES[0], "capture_target": CAPTURE_TARGETS[0], "video_encoder": VIDEO_ENCODERS[0]}
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
    if defaults["format"] not in FORMATS:
        defaults["format"] = "JPEG"
    if defaults["capture_engine"] not in CAPTURE_ENGINES:
        defaults["capture_engine"] = CAPTURE_ENGINES[0]
    if defaults["capture_target"] not in CAPTURE_TARGETS:
        defaults["capture_target"] = CAPTURE_TARGETS[0]
    if defaults["video_encoder"] not in VIDEO_ENCODERS:
        defaults["video_encoder"] = VIDEO_ENCODERS[0]
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
        self._label(video_opts, "NVENC nécessite un FFmpeg compatible et un pilote NVIDIA à jour.",
                    11, MUTED).pack(side="left", padx=12)
        self._label(hero, "DXGI C++ v6 : écran principal uniquement. Les contenus protégés peuvent rester noirs.",
                    11, MUTED).pack(anchor="w", padx=24, pady=(0, 13))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=30, pady=(0, 9))
        self._label(actions, "BIBLIOTHÈQUE PHOTO & VIDÉO", 17, TEXT, "bold").pack(side="left")
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
        self._label(bottom, "MP4 vidéo sans audio • raccourcis globaux", 11, MUTED).pack(side="right")

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
            command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                       "-f", "gdigrab", "-framerate", "20", "-draw_mouse", "1", "-i", "desktop",
                       "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                       *codec_options, "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output)]
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
