# Gate verisi bağlama

Yeni Test sayfasında `Gate veri manifesti seç` düğmesiyle yerel JSON seçilir.
Dosya yolları manifest klasörüne göre çözümlenir. Örnek:

```json
{
  "feeds": {
    "BINANCE:WCTUSDT": {"path": "data/wct_spot_30m.csv"},
    "BYBIT:WCTUSDT": {"path": "data/wct_bybit_spot_30m.csv"},
    "OKX:WCTUSDT": {"path": "data/wct_okx_spot_30m.csv"},
    "BINANCE:BTCUSDT": {"path": "data/btc_spot_30m.csv"},
    "BINANCE:ETHUSDT": {"path": "data/eth_spot_30m.csv"},
    "BINANCE:WCTUSDT.P": {"path": "data/wct_perp_1m.csv"}
  }
}
```

Bu dosyalar örnek isimlerdir; mevcut veri iddiası değildir. Kullanılmayan feed'ler
eklenmek zorunda değildir. Aynı sembol için `.P` vadeli, suffixsiz spot demektir.

CSV şeması: `time,open,high,low,close,volume`. `time`, UTC ISO-8601 mum açılışıdır
(ör. `2025-06-01T00:00:00Z`); volume baz varlık hacmidir. Veriler sıralı,
benzersiz ve hesap için gerekli aralıkta kesintisiz olmalıdır. Bir dakikalık veri
desteklenen üst periyotlara toplanabilir. OHLC sınırları ve sonlu sayılar kontrol
edilir. Chart sembolünün dışarıdan verilen ince verisi mevcut execution mumlarına
toplanarak karşılaştırılır; uyuşmazlıkta koşu durur.

Her feed girdisine isteğe bağlı `sha256` eklenebilir. Arayüz kuyruğa alma anında
dosya kimliklerini kaydeder; sonradan değişen veriyle sessiz yeniden test yapmaz.
Doğrudan API işi için `gate_feed_manifest` alanı aynı yolu alır.

Bias önceki günlük notional hesabı kaynak gibi `daily volume * daily close`:
intraday `volume * close` toplamıyla aynı olduğu varsayılmaz. Aylık bias için
iki tamamlanmış aylık kapanış; EMA/pivot hesapları için yeterli geçmiş gerekir.
Erken dönem warmup sinyallerinin blok olması eksik veri yerine kazanç değildir.

Mevcut otomatik indirme yalnız ana Binance vadeli mum iş akışıdır. Bybit/OKX
ve spot/reference feed'leri otomatik indirilmiş sayılmaz. Eksik kaynak açık hata
üretir; venue iptal etmek bir veri tamamlama işlemi değil farklı strateji ayarıdır.
