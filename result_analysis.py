"""Closed-trade statistics and a read-only result inspection panel."""
import csv
import math
import statistics
import tkinter as tk
from tkinter import ttk
from collections import defaultdict


def trade_statistics(trades):
    values = [float(t['net_pnl']) for t in trades]
    if not all(math.isfinite(v) for v in values):
        raise ValueError('İşlem kaydında geçersiz P&L var')
    if not values:
        return {}, [0.0]
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    curve = [0.0]
    ws = ls = max_ws = max_ls = 0
    for v in values:
        curve.append(curve[-1] + v)
        ws = ws + 1 if v > 0 else 0
        ls = ls + 1 if v < 0 else 0
        max_ws, max_ls = max(ws, max_ws), max(ls, max_ls)
    top = sum(sorted(wins, reverse=True)[:max(1, math.ceil(len(values) * .1))])
    result = {
        'Kapanan net sonuç · USDT': sum(values),
        'İşlem başına ortalama · USDT': statistics.mean(values),
        'Medyan işlem · USDT': statistics.median(values),
        'En yüksek işlem sonucu · USDT': max(values),
        'En düşük işlem sonucu · USDT': min(values),
        'Ortalama kazanan · USDT': statistics.mean(wins) if wins else None,
        'Ortalama kaybeden · USDT': statistics.mean(losses) if losses else None,
        'Kazanç / kayıp büyüklüğü': statistics.mean(wins) / abs(statistics.mean(losses)) if wins and losses else None,
        'Kazanan / kaybeden / başabaş': f'{len(wins)} / {len(losses)} / {values.count(0)}',
        'En uzun kazanç / kayıp serisi': f'{max_ws} / {max_ls}',
        'En iyi %10 işlemin pozitif kârdaki payı · %': 100 * top / sum(wins) if wins else None,
        'En iyi %10 çıkarılınca net · USDT': sum(values) - top,
    }
    return result, curve


class ResultAnalysis(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.note = ttk.Label(self, text='Detay için bir sonuç seçin.', wraplength=1050)
        self.note.pack(fill='x', pady=5)
        tabs = ttk.Notebook(self)
        tabs.pack(fill='both', expand=True)
        stats = ttk.Frame(tabs)
        charts = ttk.Frame(tabs)
        trades = ttk.Frame(tabs)
        settings = ttk.Frame(tabs)
        for frame, title in [(stats, 'Kâr ve risk'), (charts, 'Getiri dağılımı'), (trades, 'İşlemler'), (settings, 'Ayarlar ve veri')]:
            tabs.add(frame, text=title)
        self.metrics = self.table(stats, ('Ölçüm', 'Değer'))
        self.canvas = tk.Canvas(charts, height=230, background='#172033', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda e: self.draw())
        self.trades = self.table(trades, ('İşlem', 'Giriş UTC', 'Çıkış UTC', 'Net USDT', 'Getiri %', 'Çıkış nedeni'))
        self.trade_note = ttk.Label(trades, text='', wraplength=1050)
        self.trade_note.pack(fill='x')
        self.trades.bind('<<TreeviewSelect>>', self.select_trade)
        self.settings = self.table(settings, ('Alan', 'Değer'))
        self.records = {}
        self.curve = []
        self.values = []

    def table(self, parent, columns):
        box = ttk.Frame(parent)
        box.pack(fill='both', expand=True)
        tree = ttk.Treeview(box, columns=columns, show='headings', height=8)
        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=170, minwidth=80)
        scroll = ttk.Scrollbar(box, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        return tree

    def select_trade(self, event=None):
        chosen = self.trades.selection()
        if chosen:
            row = self.records[chosen[0]]
            self.trade_note.configure(text=' · '.join(f'{k}: {v}' for k, v in row.items()))

    def load(self, row, folder):
        for tree in (self.metrics, self.trades, self.settings):
            tree.delete(*tree.get_children())
        self.records, self.values, self.curve = {}, [], []
        self.trade_note.configure(text='')
        for key, value in row.items():
            self.settings.insert('', 'end', values=(key, value))
        labels = {'closed_net_profit_usdt': 'Gerçekleşmiş net · USDT', 'open_pnl_usdt': 'Açık pozisyon · USDT', 'total_pnl_usdt': 'Açık dahil toplam · USDT', 'max_drawdown_usdt': 'Motor maksimum DD · USDT', 'win_rate_pct': 'Kazanma oranı · %', 'profit_factor': 'Profit factor', 'closed_trades': 'Kapanan işlem', 'commission_usdt': 'Komisyon · USDT'}
        for key, label in labels.items():
            self.metrics.insert('', 'end', values=(label, row.get(key) or '—'))
        note = 'İşlem kayıtları yok; eski koşuyu yeniden çalıştırınca ayrıntılar oluşur.'
        try:
            relative = row.get('trade_ledger')
            if relative:
                path = (folder / relative).resolve()
                if not path.is_relative_to(folder.resolve()):
                    raise ValueError('İşlem dosyası rapor klasörü dışında')
                with path.open(encoding='utf-8-sig', newline='') as handle:
                    records = list(csv.DictReader(handle))
                metrics, self.curve = trade_statistics(records)
                if len(records) != int(float(row['closed_trades'])):
                    raise ValueError('Özet ve işlem sayısı uyuşmuyor')
                self.values = [float(t['net_pnl']) for t in records]
                for key, value in metrics.items():
                    shown = '—' if value is None else f'{value:.2f}' if isinstance(value, float) else value
                    self.metrics.insert('', 'end', values=(key, shown))
                reasons = defaultdict(list)
                for t in records:
                    item = self.trades.insert('', 'end', values=tuple(t.get(k, '') for k in ('trade_number', 'entry_time', 'exit_time', 'net_pnl', 'return_pct', 'exit_reason')))
                    self.records[item] = t
                    reasons[t['exit_reason']].append(float(t['net_pnl']))
                for reason, pnl in reasons.items():
                    self.metrics.insert('', 'end', values=(f'Çıkış: {reason} · adet / net USDT', f'{len(pnl)} / {sum(pnl):.2f}'))
                note = f'{len(records)} işlem · Tüm işlem istatistikleri komisyon sonrası. Grafik kapanan işlemlerin birikimli sonucudur; açık pozisyon dalgalanmasını içermez.'
        except (OSError, ValueError, KeyError) as exc:
            self.curve, self.values = [], []
            note = f'İşlem ayrıntısı kullanılamıyor: {exc}'
        self.note.configure(text=note)
        self.draw()

    def draw(self):
        c = self.canvas
        c.delete('all')
        w, h = max(c.winfo_width(), 300), max(c.winfo_height(), 200)
        if not self.values:
            c.create_text(w/2, h/2, text='İşlem verisi bekleniyor', fill='#e5e7eb')
            return
        # Two separately labelled plots, using actual value bounds.
        mid = h/2
        for ys, title, top, bottom in [(self.curve, 'Birikimli net · USDT / kapanan işlem sırası', 30, mid-20), (self.values, 'İşlem sonucu · USDT / işlem sırası', mid+25, h-25)]:
            lo, hi = min(0, min(ys)), max(0, max(ys))
            span = hi-lo or 1
            x = lambda i: 80 + i * (w-105) / max(1, len(ys)-1)
            y = lambda v: bottom - (v-lo)/span*(bottom-top)
            c.create_text(80, top-13, anchor='w', text=title, fill='#e5e7eb')
            for v in (lo, hi):
                c.create_text(73, y(v), anchor='e', text=f'{v:.2f}', fill='#e5e7eb')
            c.create_line(80, y(0), w-25, y(0), fill='#64748b')
            if ys is self.curve:
                points = [coord for i, v in enumerate(ys) for coord in (x(i), y(v))]
                c.create_line(*points, fill='#60a5fa', width=2)
            else:
                for i, v in enumerate(ys):
                    c.create_line(x(i), y(0), x(i), y(v), fill='#34d399' if v>=0 else '#f87171', width=2)
            c.create_text(80, bottom+12, text='0' if ys is self.curve else '1', fill='#e5e7eb')
            c.create_text(w-25, bottom+12, text=str(len(self.values)), fill='#e5e7eb')
