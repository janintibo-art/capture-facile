"""Capture Facile v12 — atelier de bande-annonce original, non destructif.

Assemble des MP4 autorisés, avec titre, fondus d'entrée, musique facultative.
Utilise FFmpeg embarqué par imageio-ffmpeg, sans installation externe.
"""
import os
import time
import queue
import re
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from io import BytesIO
from tkinter import Listbox, END, SINGLE, filedialog, messagebox

import customtkinter as ctk
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont, ImageTk

BG = '#101523'
PANEL = '#1A2234'
CARD = '#232E43'
ACCENT = '#29D3B0'
TEXT = '#F2F5FF'
MUTED = '#A1B0CA'


def _run(args):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if p.returncode:
        raise RuntimeError((p.stderr or 'FFmpeg a échoué')[-1100:])
    return p


def _input_info(ffmpeg, source):
    # ffmpeg -i returns nonzero without an output file, but reports the streams and duration.
    p = subprocess.run([ffmpeg, '-hide_banner', '-i', str(source)], capture_output=True,
                       text=True, errors='replace', timeout=20,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    output = p.stderr or ''
    found = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', output)
    if not found:
        raise RuntimeError(f'Durée impossible à lire : {source.name}')
    duration = int(found[1])*3600 + int(found[2])*60 + float(found[3])
    if duration <= 0:
        raise RuntimeError(f'Durée invalide : {source.name}')
    return duration, bool(re.search(r'Stream #\d+:\d+(?:\([^)]*\))?: Audio:', output))


def _title_image(text, destination, width, height):
    picture = Image.new('RGB', (width, height), '#101523')
    brush = ImageDraw.Draw(picture)
    try:
        font = ImageFont.truetype('arial.ttf', max(34, width//24))
    except OSError:
        font = ImageFont.load_default()
    words = text.strip().split()
    lines = []
    line = ''
    max_width = int(width*.83)
    for word in words:
        proposal = f'{line} {word}'.strip()
        if brush.textbbox((0, 0), proposal, font=font)[2] > max_width and line:
            lines.append(line)
            line = word
        else:
            line = proposal
    if line:
        lines.append(line)
    # Avoid a title running off screen, cap display lines.
    lines = lines[:4]
    height_line = max(52, height//12)
    top = (height - len(lines)*height_line)//2
    for i, line in enumerate(lines):
        brush.text((width//2, top+i*height_line), line, fill='#f2f5ff', font=font, anchor='mt',
                  stroke_width=1, stroke_fill='#29D3B0')
    picture.save(destination, 'PNG')


def _normalization_args(ffmpeg, source, out, size, fps, has_audio=True, still=False,
                        duration=None, transition="Fondu au noir", clip_volume=1.0):
    width, height = size
    common = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y']
    if still:
        common += ['-loop', '1', '-framerate', str(fps), '-i', str(source),
                   '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000',
                   '-t', '2']
    else:
        common += ['-i', str(source)]
        if not has_audio:
            common += ['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000']
    vf = (f'scale={width}:{height}:force_original_aspect_ratio=decrease,'
          f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,'
          f'setsar=1,fps={fps},format=yuv420p')
    if transition == 'Fondu au noir':
        vf += ',fade=t=in:st=0:d=0.25'
        if duration and duration > 0.6:
            vf += f',fade=t=out:st={max(0, duration-0.25):.3f}:d=0.25'
    af = f'volume={clip_volume:.3f}'
    if transition == 'Fondu au noir' and duration and duration > 0.6:
        af += f',afade=t=out:st={max(0, duration-0.25):.3f}:d=0.25'
    common += ['-map', '0:v:0', '-map', '0:a:0' if has_audio and not still else '1:a:0',
               '-vf', vf, '-af', af, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21',
               '-pix_fmt', 'yuv420p', '-r', str(fps), '-c:a', 'aac', '-ar', '48000',
               '-ac', '2', '-b:a', '160k', '-shortest', '-movflags', '+faststart', str(out)]
    return common


def export_trailer(clips, output, title, music, resolution, fps, on_progress,
                   transition="Fondu au noir", clip_volume=1.0, music_volume=0.22):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    width, height = resolution
    with tempfile.TemporaryDirectory(prefix='capture_facile_studio_') as tmp:
        work = Path(tmp)
        normalized = []
        sources = []
        if title.strip():
            title_png = work/'intro.png'
            _title_image(title, title_png, width, height)
            sources.append((title_png, True))
        sources += [(Path(p), False) for p in clips]
        for i, (source, still) in enumerate(sources):
            on_progress(f'Préparation du clip {i+1}/{len(sources)}…')
            if not source.is_file():
                raise RuntimeError(f'Fichier introuvable : {source.name}')
            duration, has_audio = (2.0, False) if still else _input_info(ffmpeg, source)
            normalized_path = work/f'part_{i:03d}.mp4'
            _run(_normalization_args(ffmpeg, source, normalized_path, resolution, fps,
                                     has_audio=has_audio, still=still, duration=duration,
                                     transition=transition, clip_volume=clip_volume))
            normalized.append(normalized_path)
        on_progress('Assemblage des séquences…')
        manifest = work/'sequence.txt'
        manifest.write_text(''.join(f"file '{p.as_posix()}'\n" for p in normalized), encoding='utf-8')
        assembled = work/'assembled.mp4'
        _run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0',
              '-i', str(manifest), '-c', 'copy', '-movflags', '+faststart', str(assembled)])
        if music:
            on_progress('Mixage de la musique (volume réduit)…')
            _run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(assembled),
                  '-stream_loop', '-1', '-i', str(music),
                  '-filter_complex', f'[1:a:0]volume={music_volume:.3f}[m];[0:a:0][m]amix=inputs=2:duration=first:dropout_transition=0[a]',
                  '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac',
                  '-b:a', '192k', '-movflags', '+faststart', '-shortest', str(output)])
        else:
            import shutil
            shutil.copyfile(assembled, output)
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError('Export vide : aucun MP4 créé')


class TrailerStudio(ctk.CTkToplevel):
    def __init__(self, master, folder, on_saved):
        super().__init__(master)
        self.title('Capture Facile • Atelier bande-annonce v14')
        self.geometry('1130x875')
        self.minsize(900, 720)
        self.configure(fg_color=BG)
        self.folder = Path(folder)
        self.on_saved = on_saved
        self.clips = []
        self.music = None
        self.running = False
        self.preview_process = None
        self.preview_stop = None
        self.preview_generation = 0
        self.preview_photo = None
        self.preview_clip = None
        self.updates = queue.Queue()
        self.thumb_cache = {}
        self.thumb_pending = set()
        self.thumb_refs = {}
        self.timeline_widgets = {}
        self.timeline_cards = []
        self.drag_source = None
        self.thumb_semaphore = threading.Semaphore(2)
        self._build()
        self.after(100, self._poll)
        self.after(120, self.lift)

    def _build(self):
        ctk.CTkLabel(self, text='ATELIER BANDE-ANNONCE', font=('Segoe UI', 24, 'bold'),
                     text_color=TEXT).pack(anchor='w', padx=24, pady=(22, 4))
        ctk.CTkLabel(self, text='Ajoutez des vidéos, déplacez leurs miniatures à la souris et exportez votre montage.',
                     text_color=MUTED).pack(anchor='w', padx=24, pady=(0, 16))
        panel = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=16)
        panel.pack(fill='both', expand=True, padx=24, pady=(0, 10))
        clips_label = ctk.CTkLabel(panel, text='SÉQUENCES (ordre de lecture)',
                                   text_color=TEXT, font=('Segoe UI', 14, 'bold'))
        clips_label.pack(anchor='w', padx=18, pady=(14, 3))
        self.listbox = Listbox(panel, height=5, selectmode=SINGLE, bg='#232E43', fg='#F2F5FF',
                               selectbackground='#247D78', selectforeground='#FFFFFF',
                               borderwidth=0, highlightthickness=0, font=('Segoe UI', 11))
        # Keep Listbox as selection model for v13 actions; v14 shows visual timeline instead.
        self.timeline = ctk.CTkScrollableFrame(panel, orientation='horizontal',
                                                fg_color=CARD, height=132,
                                                corner_radius=12)
        self.timeline.pack(fill='x', padx=18, pady=(0, 8))
        self.timeline_hint = ctk.CTkLabel(self.timeline,
                            text='Ajoutez des MP4 puis glissez une miniature pour réorganiser les scènes.',
                            text_color=MUTED)
        self.timeline_hint.pack(side='left', padx=12, pady=40)
        self.listbox.bind('<<ListboxSelect>>', self._preview_selection)
        buttons = ctk.CTkFrame(panel, fg_color='transparent')
        buttons.pack(fill='x', padx=14, pady=(0, 10))
        for title, fn in [('Ajouter MP4', self.add_clips), ('Monter ↑', lambda: self.move(-1)),
                          ('Descendre ↓', lambda: self.move(1)), ('Retirer', self.remove_clip),
                          ('Vider', self.clear)]:
            ctk.CTkButton(buttons, text=title, command=fn, fg_color=CARD, hover_color='#354661',
                          width=105).pack(side='left', padx=4)
        viewing = ctk.CTkFrame(panel, fg_color=CARD, corner_radius=10)
        viewing.pack(fill='x', padx=18, pady=(0, 8))
        self.preview_label = ctk.CTkLabel(viewing, text='Choisissez un clip pour voir son aperçu',
                                           width=600, height=338, text_color=MUTED)
        self.preview_label.pack(side='left', padx=10, pady=8)
        player = ctk.CTkFrame(viewing, fg_color='transparent')
        player.pack(side='left', fill='both', expand=True, padx=8)
        self.preview_name = ctk.CTkLabel(player, text='Lecture d’aperçu sans son',
                                          text_color=MUTED, wraplength=210)
        self.preview_name.pack(anchor='w', pady=12)
        ctk.CTkButton(player, text='▶ Lire', command=self.play_preview,
                      fg_color=ACCENT, text_color=BG, width=130).pack(anchor='w', pady=4)
        ctk.CTkButton(player, text='■ Arrêter', command=self.stop_preview,
                      fg_color=PANEL, width=130).pack(anchor='w', pady=4)
        self.preview_time = ctk.CTkLabel(player, text='00:00', text_color=MUTED)
        self.preview_time.pack(anchor='w', pady=6)
        settings = ctk.CTkFrame(panel, fg_color='transparent')
        settings.pack(fill='x', padx=18, pady=(3, 9))
        ctk.CTkLabel(settings, text='Titre d’ouverture (facultatif)', text_color=MUTED).grid(row=0,column=0,sticky='w',padx=4)
        self.title_entry = ctk.CTkEntry(settings, width=350, placeholder_text='Ex. Les Mondes de Travers',
                                         fg_color=CARD)
        self.title_entry.grid(row=1,column=0,sticky='w',padx=4,pady=4)
        self.music_button = ctk.CTkButton(settings, text='Ajouter une musique…', command=self.select_music,
                                           fg_color=CARD, width=190)
        self.music_button.grid(row=1,column=1,sticky='w',padx=10)
        self.music_info = ctk.CTkLabel(settings, text='Aucune musique', text_color=MUTED)
        self.music_info.grid(row=2,column=1,sticky='w',padx=10)
        ctk.CTkButton(settings, text='Retirer musique', command=self.clear_music,
                       fg_color=CARD, width=130).grid(row=3,column=1,sticky='w',padx=10)
        self.res_var = ctk.StringVar(value='1080p')
        self.fps_var = ctk.StringVar(value='60 FPS')
        ctk.CTkLabel(settings, text='Résolution', text_color=MUTED).grid(row=2,column=0,sticky='w',padx=4)
        options = ctk.CTkFrame(settings, fg_color='transparent')
        options.grid(row=3,column=0,sticky='w')
        ctk.CTkOptionMenu(options, values=['720p', '1080p'], variable=self.res_var,
                          width=130).pack(side='left',padx=4)
        ctk.CTkOptionMenu(options, values=['30 FPS', '60 FPS'], variable=self.fps_var,
                          width=130).pack(side='left',padx=4)
        settings.grid_columnconfigure(0, weight=1)
        levels = ctk.CTkFrame(panel, fg_color='transparent')
        levels.pack(fill='x', padx=18, pady=(0, 12))
        ctk.CTkLabel(levels, text='Transition', text_color=MUTED).grid(row=0, column=0, padx=6)
        self.transition_var = ctk.StringVar(value='Fondu au noir')
        ctk.CTkOptionMenu(levels, values=['Fondu au noir', 'Coupe franche'],
                          variable=self.transition_var, width=165).grid(row=1, column=0, padx=6)
        ctk.CTkLabel(levels, text='Son des clips', text_color=MUTED).grid(row=0, column=1, padx=9)
        self.clip_volume = ctk.CTkSlider(levels, from_=0, to=1.5, number_of_steps=30, width=150)
        self.clip_volume.set(1.0)
        self.clip_volume.grid(row=1, column=1, padx=9)
        ctk.CTkLabel(levels, text='Musique de fond', text_color=MUTED).grid(row=0, column=2, padx=9)
        self.music_volume = ctk.CTkSlider(levels, from_=0, to=1, number_of_steps=20, width=150)
        self.music_volume.set(0.22)
        self.music_volume.grid(row=1, column=2, padx=9)
        self.status = ctk.CTkLabel(self, text='Prêt • aperçu sans audio • export H.264 + AAC',
                                   text_color=MUTED)
        self.status.pack(anchor='w', padx=27, pady=(0, 5))
        self.export_button = ctk.CTkButton(self, text='Exporter la bande-annonce MP4', command=self.export,
                                           fg_color=ACCENT, text_color=BG,
                                           height=44, font=('Segoe UI', 14, 'bold'))
        self.export_button.pack(fill='x', padx=24, pady=(0, 18))

    def _stop_player(self):
        self.preview_generation += 1
        if self.preview_stop:
            self.preview_stop.set()
        proc = self.preview_process
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        self.preview_process = None
        self.preview_stop = None

    def stop_preview(self):
        self._stop_player()
        self.preview_time.configure(text='Arrêt')

    def _preview_selection(self, _event=None):
        selected = self.listbox.curselection()
        if not selected:
            return
        chosen = self.clips[selected[0]]
        self._highlight_timeline()
        if chosen == self.preview_clip:
            return
        self._stop_player()
        self.preview_clip = chosen
        self.preview_name.configure(text=chosen.name[:55] + '\nLecture sans son')
        self.preview_time.configure(text='Aperçu prêt')
        self.preview_label.configure(image=None, text='▶ Cliquez sur Lire')

    def play_preview(self):
        selected = self.listbox.curselection()
        if not selected:
            return
        path = self.clips[selected[0]]
        if not path.is_file():
            messagebox.showerror('Capture Facile', 'Vidéo introuvable', parent=self)
            return
        self._stop_player()
        generation = self.preview_generation
        stopped = threading.Event()
        self.preview_stop = stopped
        self.preview_name.configure(text=path.name[:55] + '\nLecture d’aperçu sans son')
        self.preview_time.configure(text='Chargement…')
        def play():
            proc = None
            try:
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
                width, height = 600, 338
                cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', str(path),
                       '-an', '-vf', f'fps=12,scale={width}:{height}:force_original_aspect_ratio=decrease,'
                       f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2',
                       '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-']
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, bufsize=0,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.preview_process = proc
                frame_bytes = width * height * 3
                number = 0
                start = time.monotonic()
                while not stopped.is_set():
                    buf = bytearray()
                    while len(buf) < frame_bytes and not stopped.is_set():
                        chunk = proc.stdout.read(frame_bytes - len(buf))
                        if not chunk:
                            break
                        buf.extend(chunk)
                    if len(buf) != frame_bytes:
                        break
                    image = Image.frombytes('RGB', (width, height), bytes(buf))
                    if number % 2 == 0:
                        self.updates.put(('preview_frame', (generation, image, number / 12)))
                    number += 1
                    deadline = start + number / 12
                    delay = deadline - time.monotonic()
                    if delay > 0:
                        stopped.wait(delay)
                if not stopped.is_set():
                    self.updates.put(('preview_end', generation))
            except Exception as exc:
                self.updates.put(('preview_error', (generation, str(exc))))
            finally:
                if proc:
                    try:
                        proc.kill() if proc.poll() is None else None
                        proc.wait(timeout=2)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
        threading.Thread(target=play, daemon=True).start()


    def _queue_thumbnail(self, path):
        """Decode one small thumbnail outside Tk main thread."""
        if path in self.thumb_cache or path in self.thumb_pending:
            return
        self.thumb_pending.add(path)
        def work():
            picture = None
            with self.thumb_semaphore:
                try:
                    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
                    args = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
                            '-ss', '0.35', '-i', str(path), '-frames:v', '1',
                            '-vf', 'scale=160:90:force_original_aspect_ratio=decrease,'
                            'pad=160:90:(ow-iw)/2:(oh-ih)/2:black',
                            '-f', 'image2pipe', '-vcodec', 'mjpeg', '-']
                    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            timeout=18, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    if result.returncode == 0 and result.stdout:
                        with Image.open(BytesIO(result.stdout)) as im:
                            picture = im.convert('RGB')
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
            self.updates.put(('thumb_ready', (path, picture)))
        threading.Thread(target=work, daemon=True).start()

    def _render_timeline(self):
        if not hasattr(self, 'timeline'):
            return
        for child in self.timeline.winfo_children():
            child.destroy()
        self.timeline_widgets.clear()
        self.timeline_cards.clear()
        self.thumb_refs.clear()
        if not self.clips:
            label = ctk.CTkLabel(self.timeline, text='Ajoutez des vidéos MP4 pour créer votre première séquence.',
                                 text_color=MUTED)
            label.pack(side='left', padx=16, pady=40)
            return
        selection = self.listbox.curselection()
        active = selection[0] if selection else -1
        for i, path in enumerate(self.clips):
            card = ctk.CTkFrame(self.timeline, width=170, height=120, corner_radius=9,
                                fg_color='#225A61' if i == active else PANEL,
                                border_width=2 if i == active else 0,
                                border_color=ACCENT)
            card.pack(side='left', padx=5, pady=4)
            card.pack_propagate(False)
            image = self.thumb_cache.get(path)
            if image:
                picture = ctk.CTkImage(light_image=image, dark_image=image, size=(160, 90))
                self.thumb_refs[path] = picture
                visual = ctk.CTkLabel(card, text='', image=picture, width=160, height=90)
            else:
                visual = ctk.CTkLabel(card, text='▶  VIDÉO', width=160, height=90,
                                       text_color=ACCENT)
                self._queue_thumbnail(path)
            visual.pack(pady=(3, 0))
            caption = ctk.CTkLabel(card, text=f'{i+1:02d}  {path.name[:19]}',
                                    font=('Segoe UI', 10), text_color=TEXT, width=160)
            caption.pack(pady=(0, 2))
            self.timeline_cards.append(card)
            for widget in (card, visual, caption):
                self.timeline_widgets[widget] = i
                widget.bind('<ButtonPress-1>', self._timeline_press)
                widget.bind('<ButtonRelease-1>', self._timeline_release)

    def _highlight_timeline(self):
        if not hasattr(self, 'timeline_cards'):
            return
        selection = self.listbox.curselection()
        active = selection[0] if selection else -1
        for i, card in enumerate(self.timeline_cards):
            try:
                card.configure(fg_color='#225A61' if i == active else PANEL,
                               border_width=2 if i == active else 0)
            except Exception:
                pass

    def _timeline_press(self, event):
        self.drag_source = self.timeline_widgets.get(event.widget)
        if self.drag_source is not None:
            self.listbox.selection_clear(0, END)
            self.listbox.selection_set(self.drag_source)
            self.listbox.see(self.drag_source)
            self._preview_selection()

    def _timeline_release(self, event):
        start = self.drag_source
        self.drag_source = None
        if start is None or start >= len(self.clips) or self.running:
            return
        # Determine target from the position of the mouse over the timeline.
        if not self.timeline_cards:
            return
        x, y = event.x_root, event.y_root
        cards = self.timeline_cards
        if not (cards[0].winfo_rooty() - 10 <= y <= cards[0].winfo_rooty() + cards[0].winfo_height() + 10):
            return
        candidates = [(abs(x - (c.winfo_rootx() + c.winfo_width()/2)), i)
                      for i, c in enumerate(cards)]
        if not candidates:
            return
        target = min(candidates)[1]
        if start != target:
            item = self.clips.pop(start)
            self.clips.insert(target, item)
            self._refresh(target)
            self._preview_selection()
            self.status.configure(text=f'Scène déplacée en position {target+1}')
        else:
            self._highlight_timeline()

    def _refresh(self, selected=None):
        self.listbox.delete(0, END)
        for i, p in enumerate(self.clips):
            self.listbox.insert(END, f'{i+1:02d}.  {p.name}')
        if selected is not None and self.clips:
            pos = min(max(selected,0),len(self.clips)-1)
            self.listbox.selection_set(pos)
            self.listbox.see(pos)
        self._render_timeline()

    def add_clips(self):
        chosen = filedialog.askopenfilenames(parent=self, title='Choisir les vidéos MP4',
                                             initialdir=self.folder, filetypes=[('Vidéo MP4','*.mp4')])
        for p in chosen:
            path = Path(p)
            if path not in self.clips:
                self.clips.append(path)
        self._refresh(len(self.clips)-1 if self.clips else None)
        self._preview_selection()

    def move(self, direction):
        selected = self.listbox.curselection()
        if not selected:
            return
        i = selected[0]
        j = i+direction
        if 0 <= j < len(self.clips):
            self.clips[i],self.clips[j] = self.clips[j],self.clips[i]
            self._refresh(j)
            self._preview_selection()

    def remove_clip(self):
        selected = self.listbox.curselection()
        if selected:
            i = selected[0]
            self.clips.pop(i)
            self.preview_clip = None
            self._refresh(i)
            self._preview_selection()

    def clear(self):
        self._stop_player()
        self.preview_clip = None
        self.clips.clear()
        self._refresh()
        self._render_timeline()

    def select_music(self):
        source = filedialog.askopenfilename(parent=self, title='Choisir une musique',
                                             filetypes=[('Audio', '*.mp3 *.wav *.m4a *.flac *.ogg')])
        if source:
            self.music = Path(source)
            self.music_info.configure(text=self.music.name[:34])

    def clear_music(self):
        self.music = None
        self.music_info.configure(text='Aucune musique')

    def export(self):
        if self.running:
            return
        if not self.clips:
            messagebox.showwarning('Capture Facile', 'Ajoutez au moins une vidéo MP4.', parent=self)
            return
        output = filedialog.asksaveasfilename(parent=self, title='Enregistrer la bande-annonce',
                                              defaultextension='.mp4', initialdir=self.folder,
                                              initialfile=f'bande_annonce_{datetime.now():%Y%m%d_%H%M%S}.mp4',
                                              filetypes=[('Vidéo MP4','*.mp4')])
        if not output:
            return
        dest = Path(output)
        if dest in self.clips:
            messagebox.showerror('Capture Facile', 'Choisissez un nom différent des vidéos sources.', parent=self)
            return
        if self.music and not self.music.is_file():
            messagebox.showerror('Capture Facile', 'Fichier de musique introuvable.', parent=self)
            return
        self.stop_preview()
        self.running = True
        self.export_button.configure(state='disabled', text='Export en cours…')
        clips = list(self.clips)
        music = self.music
        title = self.title_entry.get()
        resolution = (1920,1080) if self.res_var.get() == '1080p' else (1280,720)
        fps = 60 if self.fps_var.get() == '60 FPS' else 30
        transition = self.transition_var.get()
        clip_level = self.clip_volume.get()
        music_level = self.music_volume.get()

        def job():
            try:
                export_trailer(clips, dest, title, music, resolution, fps,
                               lambda msg: self.updates.put(('progress',msg)),
                               transition=transition, clip_volume=clip_level, music_volume=music_level)
                self.updates.put(('done',dest))
            except Exception as e:
                # An incomplete MP4 must not be mistaken for a successful export.
                try:
                    if dest.exists():
                        dest.unlink()
                except OSError:
                    pass
                self.updates.put(('error',str(e)))
        threading.Thread(target=job,daemon=True).start()

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                kind, value = self.updates.get_nowait()
                if kind == 'thumb_ready':
                    path, pic = value
                    self.thumb_pending.discard(path)
                    self.thumb_cache[path] = pic
                    self._render_timeline()
                    continue
                if kind == 'preview_frame':
                    generation, frame, t = value
                    if generation == self.preview_generation:
                        photo = ImageTk.PhotoImage(frame)
                        self.preview_photo = photo
                        self.preview_label.configure(image=photo, text='')
                        self.preview_time.configure(text=f'{int(t)//60:02d}:{int(t)%60:02d}')
                    continue
                if kind == 'preview_end':
                    if value == self.preview_generation:
                        self.preview_time.configure(text='Fin de l’aperçu')
                    continue
                if kind == 'preview_error':
                    if value[0] == self.preview_generation:
                        self.preview_time.configure(text=f'Lecture impossible : {value[1][:55]}')
                    continue
                if kind == 'progress':
                    self.status.configure(text=value)
                else:
                    self.running = False
                    self.export_button.configure(state='normal', text='Exporter la bande-annonce MP4')
                    if kind == 'done':
                        self.status.configure(text=f'✓ Export réussi : {value.name}')
                        self.on_saved(value)
                        messagebox.showinfo('Capture Facile',f'Bande-annonce enregistrée :\n{value}',parent=self)
                    else:
                        self.status.configure(text=f'Erreur : {value[:135]}')
                        messagebox.showerror('Capture Facile',f'Export impossible :\n{value}',parent=self)
        except queue.Empty:
            pass
        self.after(130,self._poll)

    def destroy(self):
        if self.running:
            messagebox.showwarning('Capture Facile', 'Attendez la fin de l’export avant de fermer cet atelier.',parent=self)
            return
        self._stop_player()
        super().destroy()


def open_trailer_studio(master, folder, on_saved):
    return TrailerStudio(master, folder, on_saved)
