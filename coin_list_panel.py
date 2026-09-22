"""List-building UI, independent of strategy inputs and historical test results."""
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timezone
from coin_lists import (read_store, active_lists, save_list, delete_list, parse_symbols,
                        refresh_catalog, screen, selection_snapshot, utc_now)
from lab_safety import atomic_write_json, file_lock


class CoinListPanel(ttk.Frame):
    def __init__(self, parent, root, apply_selection, current_symbols):
        super().__init__(parent, padding=12)
        self.root = root
        self.apply_selection = apply_selection
        self.current_symbols = current_symbols
        self.store_path = root / "coin_watchlists.json"
        self.cache_path = root / "coin_catalog.json"
        self.tags_path = root / "coin_categories.json"
        self.events = queue.Queue()
        self.list_id = None
        self.draft = []
        self.provenance = []
        self.catalog = read_store(self.cache_path, {"rows": []})
        self.tags = read_store(self.tags_path, {})
        self.name = tk.StringVar(value="Yeni listem")
        self.saved_name = tk.StringVar()
        self.mode = tk.StringVar(value="Tümü")
        self.category = tk.StringVar(value="Tümü")
        self.quote = tk.StringVar(value="USDT")
        self.search = tk.StringVar()
        self.limit = tk.StringVar(value="2000")
        self.historical_enabled = tk.BooleanVar(value=False)
        self.historical_mode = tk.StringVar(value="Son 24 saat yükseliş")
        self.historical_top_n = tk.StringVar(value="20")
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="Kayıtlı listeler").pack(side="left")
        self.saved_combo = ttk.Combobox(top, textvariable=self.saved_name, state="readonly", width=24)
        self.saved_combo.pack(side="left", padx=5)
        for title, command in (("Aç", self.load), ("Yeni liste", self.new), ("Listeyi sil", self.delete)):
            ttk.Button(top, text=title, command=command).pack(side="left", padx=3)
        self.refresh_button = ttk.Button(top, text="Binance listesini yenile", command=self.fetch)
        self.refresh_button.pack(side="right")
        self.status = ttk.Label(self, text="", wraplength=1000)
        self.status.pack(fill="x", pady=6)
        ttk.Label(self, text="USD-M vadeli pariteler · Kategori kapsamı Binance etiketleriyle sınırlıdır. AI Trending doğrudan bağlı değildir.\nBugünkü liste geçmişte biliniyormuş gibi kullanılamaz; seçim zamanı teste kaydedilir.", wraplength=1000).pack(anchor="w")
        filters = ttk.Frame(self)
        filters.pack(fill="x", pady=7)
        for text, var, values, width in (("Liste", self.mode, ("Tümü", "Yükselenler (24s)", "Hacim (24s)", "Yeni listelenen"), 20), ("Kategori", self.category, ("Tümü",), 22), ("Kotasyon", self.quote, ("USDT", "USDC"), 8)):
            ttk.Label(filters, text=text).pack(side="left", padx=3)
            box = ttk.Combobox(filters, textvariable=var, values=values, state="readonly", width=width)
            box.pack(side="left", padx=3)
            box.bind("<<ComboboxSelected>>", lambda _: self.render())
            if var == self.category:
                self.category_combo = box
        search_line = ttk.Frame(self)
        search_line.pack(fill="x", pady=(0, 6))
        ttk.Label(search_line, text="Parite ara").pack(side="left")
        ttk.Entry(search_line, textvariable=self.search, width=22).pack(side="left", padx=5)
        ttk.Label(search_line, text="İlk kaç parite?").pack(side="left")
        ttk.Entry(search_line, textvariable=self.limit, width=7).pack(side="left", padx=4)
        ttk.Button(search_line, text="Filtrele", command=self.render).pack(side="left")
        self.search.trace_add("write", lambda *_: self.render())
        history = ttk.LabelFrame(self, text="Tarihsel dinamik filtre — bugünkü sıralamayı geçmişe taşımaz", padding=7)
        history.pack(fill="x", pady=(0, 7))
        ttk.Checkbutton(history, text="Test sırasında her karar anında yeniden hesapla", variable=self.historical_enabled).pack(side="left")
        ttk.Combobox(history, textvariable=self.historical_mode, values=("Son 24 saat yükseliş", "Son 24 saat hacim", "Yeni listelenen"), state="readonly", width=23).pack(side="left", padx=6)
        ttk.Label(history, text="Top N").pack(side="left")
        ttk.Entry(history, textvariable=self.historical_top_n, width=6).pack(side="left", padx=4)
        ttk.Label(history, text="Kategori ve kotasyondaki tüm aktif adaylar temel evren olur.", wraplength=430).pack(side="left", padx=6)
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)
        left = ttk.Frame(body)
        right = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right.grid(row=0, column=1, sticky="nsew")
        self.market_tree = self.tree(left, ("symbol", "change", "volume", "onboard", "category"), ("Parite", "24s %", "24s tutar", "Listelenme", "Kategori"))
        row = ttk.Frame(left)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text="Seçilenleri ekle", command=lambda: self.add_market(False)).pack(side="left")
        ttk.Button(row, text="Görünenlerin tümünü ekle", command=lambda: self.add_market(True)).pack(side="left", padx=4)
        ttk.Label(right, text="Test listem (Ctrl / Shift ile çoklu seçim)").pack(anchor="w")
        self.draft_tree = self.tree(right, ("symbol",), ("Parite",))
        row = ttk.Frame(right)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text="Seçileni listeden çıkar", command=self.remove).pack(side="left")
        ttk.Button(row, text="Taslağı boşalt", command=self.clear).pack(side="left", padx=3)
        manual = ttk.Frame(self)
        manual.pack(fill="x", pady=5)
        self.manual = tk.StringVar()
        self.manual_source = tk.StringVar(value="Elle seçtim")
        ttk.Label(manual, text="Pariteleri yapıştır").pack(side="left")
        ttk.Entry(manual, textvariable=self.manual, width=31).pack(side="left", padx=4)
        ttk.Combobox(manual, textvariable=self.manual_source, values=("Elle seçtim", "AI Trending (elle aktarıldı)", "Kategori (elle aktarıldı)"), state="readonly", width=25).pack(side="left")
        ttk.Button(manual, text="Ekle", command=self.add_manual).pack(side="left", padx=4)
        tags_row = ttk.Frame(self)
        tags_row.pack(fill="x", pady=3)
        self.tag_name = tk.StringVar()
        ttk.Label(tags_row, text="Kişisel kategori").pack(side="left")
        ttk.Entry(tags_row, textvariable=self.tag_name, width=20).pack(side="left", padx=4)
        ttk.Button(tags_row, text="Taslakta seçili paritelere etiket ver", command=self.tag).pack(side="left")
        ttk.Button(tags_row, text="Mevcut test seçimlerini al", command=self.import_current).pack(side="right")
        footer = ttk.Frame(self)
        footer.pack(fill="x", pady=5)
        ttk.Label(footer, text="Liste adı").pack(side="left")
        ttk.Entry(footer, textvariable=self.name, width=25).pack(side="left", padx=4)
        ttk.Button(footer, text="Kaydet / Güncelle", command=self.save).pack(side="left")
        ttk.Button(footer, text="Bu listeyle test hazırla →", command=self.use, style="Accent.TButton").pack(side="right")
        self.count_label = ttk.Label(self, text="")
        self.count_label.pack(anchor="w")
        self.refresh_saved()
        self.render()
        self.render_draft()
        self.poll_id = self.after(200, self.poll)

    def tree(self, parent, columns, headings):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended", height=7)
        tree.base_headings = dict(zip(columns, headings))
        tree.sort_directions = {}
        for key, title in zip(columns, headings):
            numeric = key in {"change", "volume"}
            tree.heading(key, text=title, command=lambda column=key, is_numeric=numeric: self.sort_tree(tree, column, is_numeric))
            tree.column(key, width=120, minwidth=75)
        scroll = ttk.Scrollbar(frame, command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        return tree

    def sort_tree(self, tree, column, numeric=False):
        """Sort a displayed table column; unavailable cells always stay last."""
        descending = not tree.sort_directions.get(column, False)
        tree.sort_directions[column] = descending
        available, unavailable = [], []
        for item in tree.get_children(""):
            raw = tree.set(item, column)
            text = str(raw).strip()
            if text in {"", "—", "Bilinmiyor"}:
                unavailable.append(item)
                continue
            if numeric:
                try:
                    value = float(text.replace(",", "").replace("%", "").split()[0])
                except (ValueError, IndexError):
                    unavailable.append(item)
                    continue
            else:
                value = text.casefold()
            available.append((value, item))
        available.sort(key=lambda pair: pair[0], reverse=descending)
        ordered = [item for _, item in available] + unavailable
        for position, item in enumerate(ordered):
            tree.move(item, "", position)
        for key, title in tree.base_headings.items():
            marker = (" ▼" if descending else " ▲") if key == column else ""
            tree.heading(key, text=title + marker)

    def render(self):
        if not hasattr(self, "market_tree"):
            return
        categories = {"Binance: " + tag for row in self.catalog.get("rows", []) for tag in row["categories"]}
        categories |= {"Kişisel: " + tag for tags in self.tags.values() for tag in tags}
        self.category_combo.configure(values=["Tümü"] + sorted(categories))
        self.visible = []
        self.market_tree.delete(*self.market_tree.get_children())
        try:
            self.visible = screen(self.catalog, self.mode.get(), self.search.get(), self.category.get(), self.quote.get(), int(self.limit.get()), self.tags)
        except ValueError as exc:
            self.status.configure(text=str(exc))
            return
        for row in self.visible:
            change = "—" if row["change_pct"] is None else f"{row['change_pct']:.2f}"
            volume = "—" if row["quote_volume"] is None else f"{row['quote_volume']:,.0f} {row['quote']}"
            labels = row["categories"] + ["Kişisel:" + tag for tag in self.tags.get(row["symbol"], [])]
            try:
                onboard = datetime.fromtimestamp(row["onboard_ms"] / 1000, timezone.utc).date().isoformat() if row.get("onboard_ms") else "Bilinmiyor"
            except (ValueError, OverflowError, OSError):
                onboard = "Bilinmiyor"
            self.market_tree.insert("", "end", iid=row["symbol"], values=(row["symbol"], change, volume, onboard, ", ".join(labels) or "Bilinmiyor"))
        observed = self.catalog.get("observed_at")
        self.status.configure(text=f"Katalog: {len(self.catalog.get('rows', []))} parite · Görünen: {len(self.visible)} (ilk {self.limit.get()}) · Alınma: {observed or 'Henüz yok — yenileyin'}. Önbellek anlık aktiflik garantisi değildir.")

    def fetch(self):
        self.refresh_button.configure(state="disabled")
        self.status.configure(text="Binance aktif vadeli pariteler ve 24 saatlik veriler alınıyor…")
        def worker():
            try: self.events.put((True, refresh_catalog(self.cache_path)))
            except Exception as exc: self.events.put((False, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            ok, value = self.events.get_nowait()
            self.refresh_button.configure(state="normal")
            if ok:
                self.catalog = value
                self.render()
            else:
                self.status.configure(text="Yenileme başarısız; varsa eski katalog korunuyor: " + value)
        except queue.Empty:
            pass
        self.poll_id = self.after(200, self.poll)

    def destroy(self):
        if hasattr(self, "poll_id"):
            self.after_cancel(self.poll_id)
        super().destroy()

    def add(self, symbols, event):
        self.draft = list(dict.fromkeys(self.draft + symbols))
        self.provenance.append({"at": utc_now(), **event, "symbols": list(symbols)})
        self.render_draft()

    def add_market(self, all_visible):
        selected = [row["symbol"] for row in self.visible] if all_visible else list(self.market_tree.selection())
        self.add(selected, {"source": self.mode.get(), "category": self.category.get(), "catalog_observed_at": self.catalog.get("observed_at"), "metrics": [row for row in self.visible if row["symbol"] in selected]})

    def add_manual(self):
        try:
            symbols = parse_symbols(self.manual.get(), self.quote.get())
            if not symbols: raise ValueError("Örnek: BTCUSDT SOLUSDT PHAUSDT")
            self.add(symbols, {"source": self.manual_source.get(), "exchange_membership": "NOT_VERIFIED"})
            self.manual.set("")
        except ValueError as exc: messagebox.showerror("Parite eklenemedi", str(exc), parent=self)

    def import_current(self):
        self.add(self.current_symbols(), {"source": "Mevcut test seçimleri"})

    def remove(self):
        selected = set(self.draft_tree.selection())
        self.draft = [symbol for symbol in self.draft if symbol not in selected]
        self.provenance.append({"at": utc_now(), "removed": sorted(selected)})
        self.render_draft()

    def clear(self):
        self.draft = []
        self.provenance.append({"at": utc_now(), "action": "cleared"})
        self.render_draft()

    def render_draft(self):
        self.draft_tree.delete(*self.draft_tree.get_children())
        for symbol in self.draft:
            self.draft_tree.insert("", "end", iid=symbol, values=(symbol,))
        self.count_label.configure(text=f"Taslak: {len(self.draft)} parite. Çıkarma/silme veri ve geçmiş sonuçları silmez. Değişiklikleri saklamak için Kaydet'e basın.")

    def refresh_saved(self):
        self.records = active_lists(self.store_path)
        self.saved_combo.configure(values=[row["name"] for row in self.records])

    def new(self):
        self.list_id = None
        self.name.set("Yeni listem")
        self.saved_name.set("")
        self.draft, self.provenance = [], []
        self.render_draft()

    def load(self):
        row = next((row for row in self.records if row["name"] == self.saved_name.get()), None)
        if row:
            self.list_id = row["id"]
            self.name.set(row["name"])
            self.draft = list(row["symbols"])
            self.provenance = list(row["provenance"])
            self.render_draft()

    def save(self):
        try:
            record = save_list(self.store_path, self.name.get(), self.draft, self.provenance, self.list_id)
            self.list_id = record["id"]
            self.refresh_saved()
            self.saved_name.set(record["name"])
            self.status.configure(text="Liste kaydedildi: " + record["name"])
        except ValueError as exc: messagebox.showerror("Liste kaydedilemedi", str(exc), parent=self)

    def delete(self):
        row = next((row for row in self.records if row["name"] == self.saved_name.get()), None)
        if row:
            delete_list(self.store_path, row["id"])
            self.new()
            self.refresh_saved()
            self.status.configure(text="Liste kaldırıldı; kayıt geri alınabilir. Veri ve sonuçlar korundu. Test ekranındaki eski seçim değişmedi; yeni listeyi ayrıca aktarın.")

    def tag(self):
        tag = self.tag_name.get().strip()
        symbols = list(self.draft_tree.selection())
        if not tag or not symbols:
            messagebox.showinfo("Kategori", "Kategori adı yazın ve sağdaki taslakta parite seçin.", parent=self)
            return
        with file_lock(self.tags_path.with_suffix(".lock")):
            self.tags = read_store(self.tags_path, {})
            for symbol in symbols:
                self.tags[symbol] = list(dict.fromkeys(self.tags.get(symbol, []) + [tag]))
            atomic_write_json(self.tags_path, self.tags)
        self.render()

    def use(self):
        symbols = list(self.draft)
        historical = None
        if self.historical_enabled.get():
            try:
                top_n = int(self.historical_top_n.get())
                if not 1 <= top_n <= 2000:
                    raise ValueError("Top N 1–2000 arasında olmalı")
                # Historical ranking starts from the full current catalog cohort,
                # never today's top-gainer/top-volume subset or the search box.
                cohort = screen(self.catalog, "Tümü", "", self.category.get(), self.quote.get(), 2000, self.tags)
                symbols = [row["symbol"] for row in cohort]
                modes = {"Son 24 saat yükseliş": "TOP_GAINERS_24H", "Son 24 saat hacim": "TOP_VOLUME_24H", "Yeni listelenen": "NEW_LISTED"}
                historical = {"enabled": True, "mode": modes[self.historical_mode.get()], "top_n": top_n,
                              "lookback_bars": 48, "decision_time": "BAR_CLOSE_NEXT_OPEN_ENTRY",
                              "entry_only": True, "category": self.category.get(), "quote": self.quote.get(),
                              "catalog_observed_at": self.catalog.get("observed_at"), "current_active_only": True,
                              "survivorship_warning": "Bugünkü aktif katalog geçmişte delist edilmiş sözleşmeleri içermez."}
            except (ValueError, KeyError) as exc:
                messagebox.showerror("Tarihsel filtre hazırlanamadı", str(exc), parent=self)
                return
        if not symbols:
            messagebox.showinfo("Liste boş", "Önce parite ekleyin.", parent=self)
            return
        snapshot = selection_snapshot(symbols, self.name.get(), self.provenance)
        snapshot["historical_filter"] = historical
        snapshot["historical_universe_validated"] = False
        snapshot["historical_metrics_causal"] = bool(historical)
        self.apply_selection(symbols, snapshot)
