"""User-triggered local screen OCR and reviewed, multi-page settings drafts."""
import json
import subprocess
import tempfile
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


def recognize(image):
    from PIL import ImageOps
    image = ImageOps.exif_transpose(image).convert('RGB')
    image.thumbnail((2400, 2400))
    with tempfile.TemporaryDirectory(prefix='quant-ocr-') as folder:
        path = Path(folder) / 'page.png'
        image.save(path)
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
             str(Path(__file__).with_name('screen_ocr.ps1')), '-ImagePath', str(path)],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or 'Windows OCR çalışmadı.')
        return json.loads(result.stdout)


class SettingsScanner(tk.Toplevel):
    def __init__(self, app, groups):
        super().__init__(app)
        self.app, self.groups = app, groups
        self.title('TradingView · Ekrandan ayar tara')
        self.geometry('1000x730')
        self.pages, self.draft = [], {}
        self.busy = False
        self.group = tk.StringVar(value=next(iter(groups)))
        ttk.Label(self, text='İndikatör seç → görüntü tara → okunan metinden parametre/değeri kontrol et → taslağa ekle.').pack(anchor='w', padx=12, pady=8)
        ttk.Label(self, text='Kutular OCR ile güvenilir okunmaz: true/false değerini görüntüden doğrula. Aşağı kaydırıp tekrar taramak taslağı silmez.').pack(anchor='w', padx=12)
        bar = ttk.Frame(self); bar.pack(fill='x', padx=12, pady=8)
        box = ttk.Combobox(bar, textvariable=self.group, values=list(groups), state='readonly', width=20)
        box.pack(side='left'); box.bind('<<ComboboxSelected>>', self.change_group)
        ttk.Button(bar, text='Ekranı tara (3 sn)', command=self.capture).pack(side='left', padx=6)
        ttk.Button(bar, text='Panodaki görüntüyü tara', command=self.clipboard).pack(side='left')
        ttk.Button(bar, text='Görüntü dosyası seç', command=self.open_image).pack(side='left', padx=6)
        self.status = ttk.Label(self, text='Henüz tarama yok.'); self.status.pack(anchor='w', padx=12)
        self.raw = tk.Text(self, height=12, wrap='word'); self.raw.pack(fill='both', expand=True, padx=12, pady=8)
        edit = ttk.Frame(self); edit.pack(fill='x', padx=12)
        self.key, self.value = tk.StringVar(), tk.StringVar()
        self.key_box = ttk.Combobox(edit, textvariable=self.key, state='readonly', width=40)
        self.key_box.pack(side='left')
        ttk.Entry(edit, textvariable=self.value, width=24).pack(side='left', padx=6)
        ttk.Button(edit, text='Doğruladım · Taslağa ekle', command=self.add).pack(side='left')
        self.tree = ttk.Treeview(self, columns=('group','key','value'), show='headings', height=8)
        for key, label in [('group','Bileşen'),('key','Parametre'),('value','Doğrulanan değer')]: self.tree.heading(key, text=label)
        self.tree.pack(fill='both', expand=True, padx=12, pady=8)
        actions = ttk.Frame(self); actions.pack(fill='x', padx=12, pady=8)
        ttk.Button(actions, text='Seçili taslak satırını kaldır', command=self.remove).pack(side='left')
        ttk.Button(actions, text='JSON kaydet', command=self.save).pack(side='left', padx=6)
        ttk.Button(actions, text='Doğrulananları test formuna uygula', command=self.apply).pack(side='right')
        self.change_group()

    def change_group(self, *_):
        self.key_box.configure(values=self.groups[self.group.get()])
        self.key.set(''); self.value.set('')

    def capture(self):
        if self.busy: return
        self.status.configure(text='3 saniye içinde TradingView ayarlar penceresine geç. Görünen ekran okunacak.')
        self.after(3000, self.capture_now)

    def capture_now(self):
        from PIL import ImageGrab
        try: self.start(ImageGrab.grab())
        except Exception as exc: messagebox.showerror('Ekran alınamadı', str(exc), parent=self)

    def clipboard(self):
        from PIL import ImageGrab, Image
        value = ImageGrab.grabclipboard()
        if isinstance(value, Image.Image): self.start(value)
        else: messagebox.showinfo('Görüntü gerekli', 'Win+Shift+S ile ayarlar bölümünü seçip bu düğmeye bas.', parent=self)

    def open_image(self):
        from PIL import Image
        path = filedialog.askopenfilename(parent=self, filetypes=[('Görüntü','*.png *.jpg *.jpeg *.bmp')])
        if path:
            try:
                with Image.open(path) as image: self.start(image.copy())
            except Exception as exc: messagebox.showerror('Görüntü açılamadı', str(exc), parent=self)

    def start(self, image):
        if self.busy: return
        self.busy = True
        group = self.group.get()
        self.status.configure(text='Windows OCR görüntüyü yerel olarak okuyor…')
        def worker():
            try:
                lines = recognize(image)
                self.after(0, lambda: self.finish(group, lines))
            except Exception as exc:
                self.after(0, self.failed, str(exc))
        threading.Thread(target=worker, daemon=True).start()

    def finish(self, group, lines):
        self.busy = False
        self.pages.append({'component':group, 'lines':lines})
        self.raw.insert('end', f'\n--- Sayfa {len(self.pages)} · {group} ---\n' + '\n'.join(lines) + '\n')
        self.raw.see('end')
        self.status.configure(text=f'{len(self.pages)} sayfa okundu. Parametreyi seç, değeri doğrula ve taslağa ekle.')

    def failed(self, error):
        self.busy = False
        self.status.configure(text='Tarama başarısız; mevcut taslak korundu.')
        messagebox.showerror('OCR hatası', error, parent=self)

    def add(self):
        key, value = self.key.get(), self.value.get().strip()
        if not key or not value: return
        identity = (self.group.get(), key)
        if identity in self.draft and self.draft[identity] != value:
            if not messagebox.askyesno('Çakışan değer', f'{key}: {self.draft[identity]} → {value} değiştirilsin mi?', parent=self): return
        self.draft[identity] = value
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for (group,key), value in self.draft.items(): self.tree.insert('', 'end', values=(group,key,value))

    def remove(self):
        for item in self.tree.selection():
            group,key,_ = self.tree.item(item,'values'); self.draft.pop((group,key),None)
        self.refresh()

    def save(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.json', filetypes=[('JSON','*.json')])
        if path:
            from lab_safety import atomic_write_json
            atomic_write_json(Path(path), {'schema_version':1, 'source':'user_reviewed_screen_ocr', 'pages':self.pages,
                'settings':[{'component':g,'key':k,'value':v} for (g,k),v in self.draft.items()]})

    def apply(self):
        if not self.draft: return
        old = {k:(self.app.grid_modes[k].get(), self.app.grid_vars[k]['single'].get()) for _,k in self.draft}
        try:
            for (_,key), value in self.draft.items():
                self.app.grid_modes[key].set('Tek değer / liste')
                self.app.grid_vars[key]['single'].set(value)
            self.app._grid(set(old))
        except Exception as exc:
            for key,(mode,value) in old.items():
                self.app.grid_vars[key]['single'].set(value); self.app.grid_modes[key].set(mode)
            messagebox.showerror('Geçersiz değer; aktarım geri alındı',str(exc),parent=self)
            return
        self.app._update_count()
        self.status.configure(text=f'{len(self.draft)} doğrulanmış ayar test formuna aktarıldı. Test başlatılmadı.')
