# HL Golden Export Contract

Amaç, `hl gate.pine` içindeki mevcut iki plotu (`Long Gate` ve `Gate
Diagnostic State`) yerel HL çevirisiyle bar-bar karşılaştırmaktır. Bu işlem
Pine dosyasını değiştirmez ve Strategy fill/parite testi değildir.

## Kullanıcıdan tek seferlik gerekenler

1. `BINANCE:BTCUSDT.P` için **1 dakikalık** grafikte `hl gate.pine` eklenir.
2. Manifestte yazan HL inputları aynen girilir. Varsayılan sözleşme:
   `left/right=5/5`, `MTF=15m`, `HTF=60m`, `HTF HL`, `Moderate`, kapanış
   kaynağı `HTF`, kapanış `Any Next Signal`.
3. Grafik verisi CSV olarak dışa aktarılır. CSV'de `time`, OHLC ve aynı
   indikatörün `Long Gate`, `Gate Diagnostic State` plotları bulunmalıdır.
   En az 500 ardışık 1m satır ve en az bir ON (durum 2) ile OFF (durum 3)
   olayı gerekir. CSV penceresi yerel 1m feed ile tam örtüşmelidir.

## Komutlar

Önce reproducible çalışma sözleşmesini üret:

```powershell
python local_quant_lab/hl_parity.py --write-template local_quant_lab/gate_feeds/hl_golden_manifest.json
```

Manifestte gerçek TradingView sembolü, inputlar ve gerekirse CSV başlıkları
kilitlenir. Sonra karşılaştır:

```powershell
python local_quant_lab/hl_parity.py `
  --manifest local_quant_lab/gate_feeds/hl_golden_manifest.json `
  --export-csv C:/Users/Serkan/Downloads/BTC_HL_1m_export.csv `
  --calculation-feed-csv local_quant_lab/gate_feeds/btc_perp_1m.csv `
  --output-dir local_quant_lab/reports/hl_golden_btc
```

Çıktılar:

- `hl_bar_comparison.csv`: her zaman damgasında TV/local output ve match.
- `hl_parity_summary.json`: input/source/data hashleri, mismatch sayıları ve
  `PASS` / `FAIL` / `INCONCLUSIVE` kararı.

`PASS`, yalnızca bu sabit tarih/preset için iki HL producer plotunun tarihsel
eşitliğini ifade eder. G6 `[1]`, Master latch, EXT `[1]`, Strategy emir fill'i
ve canlı/realtime davranış hâlâ ayrı doğrulamalardır.
