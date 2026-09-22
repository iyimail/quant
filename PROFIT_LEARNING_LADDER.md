# Kâr Modeli Öğrenme Merdiveni

Yerel Quant Lab, çok sayıda kombinasyonu "en yüksek P&L" optimizasyonuna dönüştürmeden çalıştırmak için bu protokolü uygular. Pine kaynakları değişmez; tüm sonuçlar `RESEARCH_APPROXIMATION` niteliğindedir.

## Sabit çerçeve

- Winrate Gate: **OFF**. Gate/exit sonucunu path-dependent Winrate filtresinin gizlice iyileştirmesi engellenir.
- ER: Kullanıcının seçtiği tek ER sürümü korunur; **Persist = 1** zorlanır.
- Bir job yalnız bir coin ve bir ayar kombinasyonudur.
- Aynı anda iki parametre, iki topology veya iki gate ailesi değiştirilmez.
- Her iddia için önce baseline, sonra kontrol/ablation, sonra ancak OOS vardır.

`ER persist=1`, ER eşiğinin üstündeki ilk onaylı barın giriş izni verebilmesi demektir. Bu, "ER v1" ile aynı kavram değildir: v1/v2 ER hesap biçimidir; persist ise kaç ardışık doğrulanmış bar istendiğidir.

## Merdiven

| Aşama | Değişen şey | Öğrendiğimiz | Başarı değildir |
|---|---|---|---|
| S0 | Hiçbir şey | Donmuş referansın trade sayısı, expectancy, PF, DD ve tail davranışı | Tek coin yüksek P&L |
| S1 | Bir aktif gate kapalı | Gate'in marjinal katkısı | Gate açıkken daha çok trade |
| S2 | Bir açık routing alternatifi | Direkt EXT ile G6→Master→EXT gecikme/latch farkı | Fazla fit edilmiş topology |
| S3 | Bir sayısal parametre, en çok üç değer | Stabil plato mu tek-nokta mı | En yüksek hücre |
| S4 | Yeni dönem/coin/maliyet/parity | Edge'in dayanıklılığı | IS sonuç tekrarı |

## Gate aileleri ve ölçülen mekanizma

| Aile | Değiştirilecek ana anlam | Hipotez | Özel risk |
|---|---|---|---|
| Core / G3 / G4 | Trend, band, momentum veto | Daha seçici giriş kötü işlemleri azaltabilir | Tekrarlı filtreler trade sayısını yok edebilir |
| G1/G5 MOST | Onaylı trend çizgisinin üstü | Trend rejiminde stop oranı düşebilir | Geç giriş ve MTF gecikmesi |
| G2 Tillson | T3 trend veya slope | Chop filtresi olabilir | Live/confirmed seçimi parity gerektirir |
| G6 | Harici kaynağın Master Gate'e veto/izin olması | Üretici gate ek değer katabilir | Kaynak `[1]` ile okunur |
| EXT1/EXT2 | Strategy'nin son izin birleşimi | AND kaliteyi, OR kapsama alanını artırabilir | OR devre dışı gate ile beklenmeyen biçimde açılabilir |
| VWAP | Günlük/haftalık rejim ve persist | Trend bağlamını ayırabilir | Persist + downstream gecikmesi |
| HL | Doğrulanmış yapı/pivot | Support sonrası daha iyi risk girişi olabilir | Pivot confirmation ve `Chart < MTF < HTF` zorunluluğu |
| Bias | Likidite/katılım/HTF/BTC bağlamı | Piyasa kalitesi filtresi olabilir | Harici feed kimliği ve HTF confirmation |
| PMAX | ATR trend yönü | Genel rejim filtresi olabilir | Geniş optimizasyon uzayı |

## Zamanlama testi zorunluluğu

Her topology sonucu şu saatleri kaydetmelidir: üretici gate bilgisi oluştu → üretici plot değişti → G6 varsa G6 `[1]` → Master latch SET/reset → Strategy EXT1/EXT2 `[1]` → ER=1/Daily/tarih → strategy.entry → fill barı.

Direkt standalone gate → Strategy hattında en az bir strategy tüketici gecikmesi; G6 → Master → Strategy hattında en az iki tüketici gecikmesi vardır. HL'nin pivot doğrulama gecikmesi bunlara eklenir. Local motor bu ilişkileri test edebilir; TradingView golden export olmadan tam Pine fill eşliği iddia edemez.

## Sonuç kabul/ret kuralları

Bir gate veya topology yalnız şu koşullarla **CANDIDATE** olabilir:

1. Baseline karşısında paired expectancy iyileşmesi ya da anlamlı DD/tail azalması göstermeli.
2. En az 30 closed trade; çoklu coin aşamasında en az beş coin ve her blokta yeterli işlem olmalı.
3. İyileşme tek sembol, tek ay veya beş büyük kazanca bağlı olmamalı.
4. En az üç komşu hassasiyet değerinde makul plato bulunmalı.
5. Daha yüksek maliyet/slippage ve leave-one-symbol-out altında bozulmamalı.
6. Ayrı görülmemiş dönem ve Pine golden-export clock/fill testinden geçmeli.

Tek parametre değerinde çalışan, trade sayısını aşırı azaltan, aynı dönemde aşırı optimize edilen veya parity belirsizliği bulunan aday **RESEARCH MORE** ya da **REJECT** olur; production değişikliği değildir.

## Program kullanımı

`experiment_plan.build_profit_learning_ladder()` S0–S3 joblarını üretir. Fonksiyon Winrate'i OFF'a, ER persistence'ı 1'e sabitler; açık gate'ler için S1 ablation, yalnız açıkça verilen topology override'ları için S2 ve her hassasiyet alanında en fazla üç S3 değeri üretir. Kaynak/topology tahmin edilmez.

Örnek topology override:

```python
[{"g6_enabled": False, "ext1_source": "VWAP"},
 {"g6_enabled": True, "g6_source": "VWAP", "ext1_source": "MASTER_GATE"}]
```

Bu yollar, ancak aynı VWAP üretici ve aynı strategy ayarlarıyla karşılaştırılabilir.
