"""Capture Facile v12 — atelier de bande-annonce original, non destructif.

Assemble des MP4 autorisés, avec titre, fondus d'entrée, musique facultative.
Utilise FFmpeg embarqué par imageio-ffmpeg, sans installation externe.
"""
import os
import queue
import re
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from tkinter import Listbox, END, SINGLE, filedialog, messagebox

import customtkinter as ctk
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

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


def _normalization_args(ffmpeg, source, out, size, fps, has_audio=True, still=False):
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
          f'setsar=1,fps={fps},format=yuv420p,fade=t=in:st=0:d=0.25')
    common += ['-map', '0:v:0', '-map', '0:a:0' if has_audio and not still else '1:a:0',
               '-vf', vf, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21',
               '-pix_fmt', 'yuv420p', '-r', str(fps), '-c:a', 'aac', '-ar', '48000',
               '-ac', '2', '-b:a', '160k', '-shortest', '-movflags', '+faststart', str(out)]
    return common


def export_trailer(clips, output, title, music, resolution, fps, on_progress):
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
            has_audio = False if still else _input_info(ffmpeg, source)[1]
            normalized_path = work/f'part_{i:03d}.mp4'
            _run(_normalization_args(ffmpeg, source, normalized_path, resolution, fps,
                                     has_audio=has_audio, still=still))
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
                  '-filter_complex', '[1:a:0]volume=0.22[m];[0:a:0][m]amix=inputs=2:duration=first:dropout_transition=0[a]',
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
        self.title('Capture Facile • Atelier bande-annonce v12')
        self.geometry('910x690')
        self.minsize(700, 580)
        self.configure(fg_color=BG)
        self.folder = Path(folder)
        self.on_saved = on_saved
        self.clips = []
        self.music = None
        self.running = False
        self.updates = queue.Queue()
        self._build()
        self.after(100, self._poll)
        self.after(120, self.lift)

    def _build(self):
        ctk.CTkLabel(self, text='ATELIER BANDE-ANNONCE', font=('Segoe UI', 24, 'bold'),
                     text_color=TEXT).pack(anchor='w', padx=24, pady=(22, 4))
        ctk.CTkLabel(self, text='Choisissez les vidéos, organisez les scènes, ajoutez un titre et exportez.',
                     text_color=MUTED).pack(anchor='w', padx=24, pady=(0, 16))
        panel = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=16)
        panel.pack(fill='both', expand=True, padx=24, pady=(0, 10))
        clips_label = ctk.CTkLabel(panel, text='SÉQUENCES (ordre de lecture)',
                                   text_color=TEXT, font=('Segoe UI', 14, 'bold'))
        clips_label.pack(anchor='w', padx=18, pady=(14, 3))
        self.listbox = Listbox(panel, height=10, selectmode=SINGLE, bg='#232E43', fg='#F2F5FF',
                               selectbackground='#247D78', selectforeground='#FFFFFF',
                               borderwidth=0, highlightthickness=0, font=('Segoe UI', 11))
        self.listbox.pack(fill='both', expand=True, padx=18, pady=(0, 8))
        buttons = ctk.CTkFrame(panel, fg_color='transparent')
        buttons.pack(fill='x', padx=14, pady=(0, 10))
        for title, fn in [('Ajouter MP4', self.add_clips), ('Monter ↑', lambda: self.move(-1)),
                          ('Descendre ↓', lambda: self.move(1)), ('Retirer', self.remove_clip),
                          ('Vider', self.clear)]:
            ctk.CTkButton(buttons, text=title, command=fn, fg_color=CARD, hover_color='#354661',
                          width=105).pack(side='left', padx=4)
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
        self.status = ctk.CTkLabel(self, text='Prêt • MP4 H.264 + AAC • fondu d’entrée de chaque plan',
                                   text_color=MUTED)
        self.status.pack(anchor='w', padx=27, pady=(0, 5))
        self.export_button = ctk.CTkButton(self, text='Exporter la bande-annonce MP4', command=self.export,
                                           fg_color=ACCENT, text_color=BG,
                                           height=44, font=('Segoe UI', 14, 'bold'))
        self.export_button.pack(fill='x', padx=24, pady=(0, 18))

    def _refresh(self, selected=None):
        self.listbox.delete(0, END)
        for i, p in enumerate(self.clips):
            self.listbox.insert(END, f'{i+1:02d}.  {p.name}')
        if selected is not None and self.clips:
            pos = min(max(selected,0),len(self.clips)-1)
            self.listbox.selection_set(pos)
            self.listbox.see(pos)

    def add_clips(self):
        chosen = filedialog.askopenfilenames(parent=self, title='Choisir les vidéos MP4',
                                             initialdir=self.folder, filetypes=[('Vidéo MP4','*.mp4')])
        for p in chosen:
            path = Path(p)
            if path not in self.clips:
                self.clips.append(path)
        self._refresh(len(self.clips)-1 if self.clips else None)

    def move(self, direction):
        selected = self.listbox.curselection()
        if not selected:
            return
        i = selected[0]
        j = i+direction
        if 0 <= j < len(self.clips):
            self.clips[i],self.clips[j] = self.clips[j],self.clips[i]
            self._refresh(j)

    def remove_clip(self):
        selected = self.listbox.curselection()
        if selected:
            i = selected[0]
            self.clips.pop(i)
            self._refresh(i)

    def clear(self):
        self.clips.clear()
        self._refresh()

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
        self.running = True
        self.export_button.configure(state='disabled', text='Export en cours…')
        clips = list(self.clips)
        music = self.music
        title = self.title_entry.get()
        resolution = (1920,1080) if self.res_var.get() == '1080p' else (1280,720)
        fps = 60 if self.fps_var.get() == '60 FPS' else 30

        def job():
            try:
                export_trailer(clips, dest, title, music, resolution, fps,
                               lambda msg: self.updates.put(('progress',msg)))
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
        super().destroy()


def open_trailer_studio(master, folder, on_saved):
    return TrailerStudio(master, folder, on_saved)
