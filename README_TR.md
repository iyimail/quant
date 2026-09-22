# Local Quant Lab

Bu program TradingView dışa aktarımlarını ve Binance'in herkese açık mum verisini yerel bilgisayarda inceler. Dil modeli veya ücretli API kullanmaz.

## Ne yapar?

1. `Downloads` klasöründeki TradingView Strategy Tester XLSX dosyalarını bulur.
2. Ayarları, sonuçları ve işlem listesini denetler.
3. Aynı sembol, zaman aralığı ve timeframe içindeki A/B testlerini otomatik eşleştirir.
4. İşlemlerin gerçekten değişip değişmediğini satır satır karşılaştırır.
5. Binance Public Data arşivinden 30 dakikalık USD-M futures mumlarını API anahtarı olmadan indirir.
6. Veride eksik mum, yinelenen zaman, bozuk OHLC ve tamamlanmamış son mum kontrolü yapar.
7. Bağımsız SHADOW WEIGHTED araştırma matrisi dahil yerel Python backtestleri çalıştırır.
8. `codex_handoff.json` ve okunabilir `rapor.md` üretir.
9. Pine kaynaklarının SHA-256 kimliğini rapora yazar; kod değişince eski testin yanlış sürüme bağlanmasını önler.

## En kolay kullanım

### Test listeleri ile çalışmak

1. **Test Listeleri** sekmesini açın. **Binance listesini yenile**, herkese açık USD-M aktif sürekli vadeli paritelerini ve 24 saatlik değişim/hacim bilgilerini alır. Hesap/API anahtarı veya emir yetkisi gerekmez. Ağ erişimi yoksa eski katalog korunur; alınma zamanı gösterilir. Yenileme otomatize edilmemiştir.
2. **Tümü**, **Yükselenler (24s)**, **Hacim (24s)** veya **Yeni listelenen** seçin. Kategori, kotasyon ve parite adıyla daraltın. **İlk kaç parite?** varsayılan 20'dir; daha büyük liste için artırın. USDT ve USDC hacimleri ayrı sıralanır. Yeni listelenen sıralaması sözleşme listeleme tarihidir; tokenın doğum tarihi değildir.
3. Soldaki listeden Ctrl/Shift ile parite seçip **Seçilenleri ekle** veya **Görünenlerin tümünü ekle** düğmesine basın. Tek tek ya da toplu sembol girmek için **Pariteleri yapıştır** kutusunu kullanın: `BTC SOLUSDT PHAUSDT`. Elle yazılan sembolün borsada aktifliği henüz doğrulanmış sayılmaz; veri kontrolü test öncesinde yapılır.
4. Sağdaki taslakta **Seçileni listeden çıkar** veya **Taslağı boşalt** kullanılabilir. İsim verip **Kaydet / Güncelle** ile saklayın. Farklı bir bağımsız liste için önce **Yeni liste** seçin. Aynı adlı başka liste sessizce ezilmez.
5. **Bu listeyle test hazırla →**, Yeni Test ekranındaki önceki parite seçimini tamamen değiştirir; önceki listeden gizli seçili coin kalmaz. Tarih/strateji ayarlarını kontrol edin, gerekirse veriyi indirin ve **Ekle ve Başlat** seçin. Listeyi aktarmak kendi başına test başlatmaz.
6. **Listeyi sil** yalnız kaydedilmiş listeyi aktif menüden kaldırır. Veri, rapor ve kuyruktaki işlerin parite kopyaları değişmez. Silinen kayıt `coin_watchlists.json` içinde silinme tarihiyle korunur; bu sürümde geri alma ayrı arayüz düğmesi değildir. Test ekranına önceden aktarılmış seçim de aynı kalır; başka liste kullanmak için yeni listeyi aktarın.

Kategori kaynağı Binance `underlyingSubType` alanıdır; eksik etiketler **Bilinmiyor** gösterilir. Gerekirse taslakta parite seçip **Kişisel kategori** atayın; bunlar **Kişisel:** etiketiyle ayrılır. CoinGecko kategori eşleştirmesi henüz yoktur.

**AI Trending (elle aktarıldı)** yalnız kullanıcının Binance ekranından aldığı sembollerin kaynak notudur. Binance AI listesine otomatik bağlantı veya yüzde bullish puanını alma yoktur. Yerel yükseliş/hacim sıralaması Binance AI Trending diye sunulmaz.

Her testte liste üyeleri, liste adı, kaynak olayları, katalog alınma zamanı ve nihai seçim kopyası `universe_snapshot` olarak saklanır. Listeyi daha sonra değiştirmek/eski kaydı kaldırmak testi değiştirmez. Güncel listeyi geçmiş dönemde test etmek ileri dönem seçim başarısını kanıtlamaz; raporda tarihsel coin evreninin doğrulanmadığı açıkça yazılır.

- Pencereli uygulamayı açmak için **`Local_Quant_Lab.bat`** dosyasına çift tıklayın.
- Uygulamada coinleri, tarih aralığını, çıkış ailesini, ER ve Shadow/Winrate seçeneklerini seçin.
- Ekrandaki **Planlanan koşu** sayısını kontrol edip **Ekle ve Başlat** düğmesine basın.
- **Çalışan Testler** sekmesinden ilerlemeyi izleyin. Bir iş seçip **Sonuçları Göster** düğmesine basın; **Sonuçları İncele** sekmesinde sonuçları sıralayıp ayarları okuyun.
- Yalnız yeni TradingView dosyalarını incelemek için `run_scan.bat` dosyasına çift tıklayın.
- Açık veriyi indirip bütün yerel testleri çalıştırmak için `run_all.bat` dosyasına çift tıklayın.
- Benim hazırladığım sıradaki test paketini çalıştırmak için `run_next_job.bat` dosyasına çift tıklayın.
- READY durumuna gelen bütün işleri kullanıcı beklemeden çalıştırmak için `run_supervisor.bat` dosyasını açık bırakın.
- İşlem bittiğinde `reports` içindeki en yeni klasörü açın.
- Bana yalnız `codex_handoff.json` dosyasını gönderin. Büyük ham verileri tekrar yüklemeniz gerekmez.

Codex'in hazır Python ortamı bulunmayan başka bir bilgisayarda önce `install_python_packages.bat` çalıştırılabilir.

## Ayarlar

`config.json` içinde semboller, tarih aralığı, komisyon, SL, trailing, ER ve winrate eşikleri bulunur. İlk sürüm USD-M perpetual futures ve 30 dakikalık mumlar içindir.

`jobs.json` test kuyruğudur. Kuyruk değişiklikleri işletim sistemi kilidi ve atomik kayıt ile korunur; test sürerken eklenen iş korunur. Ortak motor kilidi iki yürütücünün aynı anda kuyruk testi başlatmasını engeller. Yeni işler ayar ve motor kimliği kopyasıyla kaydedilir. Motor değişmişse eski iş sessizce yeni motorla çalıştırılmaz; yeni test oluşturulması gerekir.

Her deneme `reports/jobs/<iş-kimliği>/attempt-<zaman>/` altında ayrı kaydedilir. Önceki denemeler silinmez. Tamamlanan denemelerde `job_results.csv`, `codex_handoff.json`, `rapor.md` ve Excel üretimi başarılıysa `test_sonuclari.xlsx` bulunur. Excel üretimi sorunu kuyrukta gösterilir; CSV sonuçları uygulama içinde yine okunabilir. `run_manifest.json` motor/ayar kimliğini; `data_preflight.json` istenen dönemin kontrolünü ve veri dosyası kimliklerini içerir.

`run_supervisor.bat` kuyruktaki hazır işleri kontrol eder. Arayüzde **Gözetmeni Durdur (iş bitince)** yeni iş alınmasını durdurur; mevcut işi yarıda kesmez. Elektrik kesilmesi veya zorla kapatma sonrası **Kesilenleri Kurtar**, yalnız motor çalışmıyorsa eski RUNNING kayıtlarını yeniden denenebilir hale getirir. **Yeniden Dene** kaldığı satırdan devam etmez; ayrı klasörde yeni deneme başlatır. Ara sonuçlar korunur. Kullanıcı tarafından yapılan sıradan JSON dosyası düzenlemeleri kilide uymayacağı için çalışan kuyruğu elle düzenlemeyin.

## Masaüstü arayüzü

`gui.py`, mevcut test motorunu değiştirmeden yöneten Windows kontrol panelidir. Yeni iş oluşturma, güvenlik üst sınırı, kuyruk izleme, seçili işi çalıştırma, yerel gözetmeni başlatma/durdurma, canlı kayıt ve rapor klasörünü açma işlemlerini tek pencerede toplar. Arayüz Pine dosyalarını değiştirmez ve bütün testleri `RESEARCH_APPROXIMATION` olarak kaydeder.

Coin bölümündeki **Ara** listede filtreleme yapar; gizlenen seçimler seçili kalır ve seçim özetinde görünür. BTC/ETH dahil bütün kayıtlı pariteler aynı listeden seçilebilir. Eski referans/kalibrasyon grupları dosyada korunur fakat arayüzde test kısıtı değildir. Yeni parite `WCTUSDT`, `OPUSDT`, `BINANCE:BTCUSDT.P` veya `ETHUSDC` biçiminde eklenebilir. Formatın kabulü borsada veri bulunduğunu kanıtlamaz. Kapsam Binance USD-M vadeli piyasası ve 30 dakikadır; spot, coin-margined ve başka borsalar desteklenmez. BTC/ETH seçmek onları otomatik piyasa filtresi yapmaz: her seçili parite ayrı test edilir.

**Seçili Veriyi İndir**, ekrandaki başlangıç ve bitişi kullanır. Tarihler UTC, bitiş günü hariçtir. Testten önce bütün seçili paritelerde istenen zaman aralığının tamlığı, çelişkili tekrarlar, mum zamanları, sonlu/geçerli fiyat-hacim, tamamlanmış mum ve en az 100 mum kontrol edilir. Eksik veriyle test başlamaz; dönem kendiliğinden daraltılmaz. Arşiv doğrulama açıksa checksum alınamayan veya eşleşmeyen yeni arşiv açılmaz. Eski yerel CSV kimliklerinin kaydı, bağımsız kaynak doğruluğu garantisi değildir.

Her parametre satırında önce **Tek değer / liste** veya **Aralık** yöntemini seçin. Kullanılmayan kutular pasifleşir. Ayrı kutular:

- `Tek değer / liste`: yalnız bir değer için `2,3`, özel değer listesi için `2;2,5;3`
- `Başlangıç`: aralığın ilk değeri
- `Bitiş`: aralığın son değeri
- `Adım`: iki test değeri arasındaki fark

Örneğin Stop Loss satırında Aralık seçip Başlangıç `2`, Bitiş `5`, Adım `0,25` girilirse 2.00, 2.25, 2.50 ve devam ederek 5.00 dahil değerler üretilir. Üç kutu da doldurulmalıdır. Bitişe adımla ulaşılamıyorsa bitiş ayrıca eklenmez: 2–3 / 0,4 → 2;2,4;2,8. Türkçe ondalık virgül desteklenir. Özel liste: `0,15;0,17;0,20`. Birden fazla aralıkta bütün kombinasyonlar çalıştırılır. Geçersiz eşikler, negatif değerler, sonsuz sayılar ve kesirli tam-sayı alanları reddedilir. **Planlanan koşu** sayacı yükü gösterir; üst sınır kontrolü büyük kombinasyon listesi oluşturulmadan önce de yapılır.

Aralık/listeler başlangıç sermayesi, işlem tutarı, komisyon, SL, trailing, ER, winrate ve gate hesap parametrelerinde kullanılabilir. Tick/miktar adımları optimizasyon parametresi değildir; sembolün kayıtlı borsa kurallarından alınır. Pine kodu otomatik yorumlanmaz; aşağıda açıklanan Python hesap modelleri çalışır.

**Yeni Kod / Sürüm** sekmesinde `master.pine`, `master gate.pine`, `bias.pine`, `hl gate.pine` veya `vwap gate.pine` için yeni Pine kodu yapıştırılabilir. Program kodu `candidate_versions` altında değişmez bir aday kopya olarak saklar, SHA-256 kimliği ve temel statik risk işaretlerini üretir. `sources` altındaki güncel kaynaklar otomatik olarak değiştirilmez. Pine kodu Python tarafından doğrudan çalıştırılamadığı için her yeni aday `PARITY_REQUIRED` durumunda başlar; Python karşılığı ve TradingView golden-export eşliği kurulmadan yeni sürümün sonucu kabul edilmez.

**Seçili Pine'ı test kaynağı yap**, seçimi ana ekranda görünür kılar; mevcut sürümde bu adayların yürütücüsü yoktur ve test engellenir. **Yerleşik Python modeline dön**, mevcut sınırlı araştırma motoruyla çalışmaya döner. Arayüzü yeniden başlatmak Pine kodunu motora bağlamaz. Genel amaçlı Pine çalıştırma/çevirme bu sürümde YOKTUR; yalnız metin kontrolü derleme, nedensellik veya eşlik testi değildir.

## Devam eden sınırlamalar

### SOLUSDT veri okuyucu düzeltmesi

USD-M test dosyaları artık yalnız `binance_um` ağacından seçilir. Spot ve coin-margined dosyalar aynı sembolü taşısa bile seçilmez; ortak okuyucuya farklı piyasa ağaçları birlikte verilirse işlem reddedilir. Her dosyanın milisaniye/mikrosaniye birimi birleştirme öncesinde ayrı doğrulanır. Aynı dosyada karışık birim, dosya ayı/günüyle uyuşmayan tarih veya mum aralığına uymayan zaman reddedilir. Bilinmeyen klasördeki dosyalar USD-M taramasına otomatik alınmaz.

2025-09-01 dahil / 2026-09-01 hariç SOLUSDT dönemi mevcut yerel vadeli arşivlerle 17.520 mum ve sıfır eksik aralık olarak doğrulandı. Arşivler ve eski hata raporu değiştirilmedi. Bu düzeltmeden önce kuyruğa alınmış motor kimliği sabit işler için yeni test oluşturun; uygulamayı yeniden açın. 34 otomatik kontrol ve dondurulmuş dört koşuluk WCT karşılaştırması geçti. Bu, veri okuyucusu doğrulamasıdır; strateji kârlılığı kanıtı değildir.

- Başlangıç öncesi indikatör/Shadow ısınma geçmişi henüz eklenmedi; motor istenen başlangıçta sıfırlanır. Bu politika manifestte yazılıdır.
- Sonuç sıralaması OOS veya strateji doğrulaması değildir. Dönem dışı test, rejim ve çoklu deneme düzeltmeleri ayrıca gerekir.
- Piyasa bazında tick/miktar adımları, funding ve gerçekleşme ayrıntıları tam modellenmiş değildir; genel fallback ayarlarını gerçek borsa kuralları sanmayın.
- Sonuç detayları henüz kısmen teknik alan adları içerir. Grafikler ve referans ayara göre otomatik fark analizi sonraki aşamadır.

## Kanıt sınırı

### Tarihsel dinamik coin evreni

- Test Listeleri ekranında "Test sırasında her karar anında yeniden hesapla" seçilirse bugünkü Top Gainer/Hacim listesinin sembolleri geçmişe yapıştırılmaz. Seçilen kategori ve kotasyondaki tüm güncel aktif pariteler aday evren olur.
- Modlar: son 24 saat fiyat yükselişi, son 24 saat quote-volume ve yeni listelenenler. Top N kullanıcı tarafından belirlenir.
- 30 dakikalık testte fiyat/hacim ölçüsü 48 tamamlanmış mumdan hesaplanır. Sıralama karar barının kapanışında bilinir ve yalnız sonraki bar açılışındaki yeni giriş iznini etkiler. Listeden düşen açık pozisyon zorla kapatılmaz; SL, trailing ve seçilen exit tarafından yönetilmeye devam eder.
- İlk 47 mumda 24 saatlik ölçü hazır değildir ve yeni giriş izni verilmez. Veri bulunmayan pariteler sıralamaya katılmaz; diğer pariteleri durdurmaz.
- Rapor; istenen aday sayısını, kullanılabilir veri bulunan aday sayısını, üyelik barlarını ve filtrelenen nihai giriş barlarını kaydeder.
- Binance kategori etiketleri ve aktif sözleşme kataloğu mevcut katalog anlık görüntüsünden gelir. Geçmişte delist edilmiş sözleşmeler katalogda bulunmadığı için sonuç `CURRENT_ACTIVE_CATALOG_BIASED` olarak işaretlenir; bu kusursuz tarihsel point-in-time evren değildir.
- Kategori filtresi statik etikettir; geçmişte kategori etiketinin değişip değişmediği bilinmez. Top Gainer ve hacim değerleri ise mumlardan nedensel biçimde yeniden hesaplanır.
- Dinamik Top N, bağımsız coin backtestlerine giriş filtresi uygular; ortak portföy sermayesi, eşzamanlı pozisyon limiti ve sermaye paylaşımı henüz modellenmez.

### Gate sekmeleri ve test kapsamı

Yeni Test ekranında beş sekme vardır: **Master Strategy**, **Master Gate**, **Bias Gate**, **High/Low Gate**, **VWAP Gate**. MOST, MOST2, Tillson, Bollinger, Momentum, PMAX ve CORE ayarları Master Gate içindedir. Sayılar sabit/liste/aralık; metin seçimleri noktalı virgülle liste; boolean değerler false/true/false;true destekler. Liste değerleri çapraz çarpılır; güvenlik koşu sınırı korunur.

Yeni arayüz işi `EXPLICIT` bağlantı ve `SOURCE_MTF` çıkış modelini seçer. Eski kayıtların `LEGACY`/`LEGACY_REF00` yolu korunur. G6 bir önceki chart plot değerini okur; Master kendi SET/reset/latch durumunu üretir; EXT1/EXT2 Master veya standalone plotunu bir bar daha geciktirerek AND/OR/ONLY_1/ONLY_2 uygular. Devre dışı EXT, Pine gibi true kabul edilir: OR modunda diğer gate'i etkisiz bırakabilir. Hiç aktif Master bileşeni yoksa Master açılmaz.

G0–G8, Bias ve HL artık bilgi sayfası değil hesap modülleridir. G1/G5 ve G2 Live Cross için `CAUSAL_CONFIRMED` geçmiş onaylı çizgiyi kullanır; `SOURCE_HISTORICAL_LOOKAHEAD` kaynak tarihsel davranışının geleceği görebilen tanı modudur. İkinci mod performans kanıtı değildir ve sonuçta risk işaretlenir. PMAX TF=0 Master TF'yi devralır. Makro ve CORE varsayılanları eski araştırma ayarları korunarak kapalıdır; Pine dosyasının varsayılan profili oldukları iddia edilmez.

Master Strategy: SL/TP/trailing aç-kapat, Daily Limit, WR kaynak/mod, MTF MOST/RSI/T3 çıkış ayarları kullanılabilir. `COMBINED` çıkış ailesinde `exit_use_most/rsi/t3` birlikte OR çalışır; diğer aileler ayrı deneydir. Kaynaktaki kullanılmayan Tillson çıkış TF girdisi hesap parametresi olarak sunulmaz: etkin TF, MOST TF'dir. Grafik/etiket/renk gibi hesap etkilemeyen girdiler tarama boyutu değildir.

Bias veri kimliği Spot/Perp ve venue bazında kesindir. Varsayılan Spot + Binance/Bybit/OKX verilerini vadeli Binance mumu yerine koymaz. Ek OHLCV CSV dosyalarını JSON manifestiyle bağlayın; biçim [Gate veri sözleşmesi](GATE_DATA_CONTRACT.md) dosyasındadır. 24m hesabı için 30m veri yeterli değildir: ince veri verilmeli veya test TF'si bilinçli seçilmelidir. Eksik/gapli/kimliği uyuşmayan veri açık hata üretir. HL kaynak kuralı Chart < MTF < HTF; 30m chart + varsayılan 15m MTF gate'i kapalı tutar.

Bar bazlı denetim kutusu seçilirse üretici plotları, G6 pass, Master SET/reset ve EXT değerleri sıkıştırılmış CSV'ye kaydedilir. Sonuçlarda kod/kaynak/veri kimlikleri, gate ayarları, birleşim ve açık bar oranı bulunur. **Entegrasyon testi PASS, TradingView sayısal/fill eşliği PASS demek değildir.** Golden karşılaştırma ve OOS/robustness ayrı doğrulama aşamalarıdır.

### Yeni test varsayılanları

- Yeni Test ekranı açıldığında coin kataloğu görünür ancak hiçbir parite otomatik seçilmez. Parite elle seçildiğinde veya Test Listeleri ekranından aktarıldığında test seçimi dolar.
- Shadow "engelde pozisyonu koru" ve "engelde pozisyonu kapat" birlikte işaretlenebilir; bu iki kural aynı pozisyonda eşzamanlı çalışmaz. Program karşılaştırma için iki ayrı koşu üretir. HOLD koşusunda engel yalnız yeni girişi durdurur; CLOSE koşusunda aynı engel açık pozisyon için kapanış kararı da üretir.

### 10 Eylül 2026 — Mevcut veriyle test

- Kullanıcının seçtiği başlangıç dahil, bitiş hariç aralık değişmez. Her coin yalnız bu aralıkta bulunan verisiyle çalışır; başlangıç veya sondaki eksik tarih kapsamı tüm işi durdurmaz.
- Sonuç ekranı ve raporlarda gerçek test başlangıcı/bitişi (UTC), kullanılan mum sayısı ve tam/mevcut dönem ayrımı bulunur. Farklı süreli getiriler doğrudan karşılaştırılmamalıdır.
- Hiç verisi olmayan, 100 mumdan az geçmişi bulunan veya iç veri boşluğu/bozuk fiyat taşıyan coin neden belirtilerek atlanır. Diğer coinler devam eder; eksik mumlar uydurulmaz. LIT geçmişindeki boşluk ve sözleşme kimliği belirsizliği bu kapsamda hâlâ araştırılmalıdır.
- İndirme hataları coin bazında kaydedilir, sonraki coin denenir. İndirme sürecinin bitmesi bütün coinlerin kullanılabilir olduğu anlamına gelmez; download_coverage.json ve rapor bunu ayrı gösterir.
- Atlanan koşular başarılı test veya sıfır kâr sayılmaz. Ekranda uyarılı tamamlanma ve atlanan koşu sayısı gösterilir.
- Açık uygulamayı kapatıp yeniden açın ve yeni test oluşturun. Eski başarısız kayıtlar korunur; eski motor kimliğiyle kaydedilmiş bir işi yeniden denemek yerine yeni iş kullanın.
- Doğrulama: 51 otomatik test geçti. Gerçek arşiv testinde BR tam dönem, CHIP/STBL kısmi dönem işlendi; LIT ve verisiz örnek atlandı. Ayrı dört koşuluk kuyruk/rapor kontrolü de geçti. Kullanıcı kuyruğu ve Pine kaynakları değiştirilmedi.

Yerel backtest TradingView'in kapalı broker emülatörünün tam kopyası değildir. Sonuçlar `RESEARCH_APPROXIMATION` olarak işaretlenir. Pine'a veya gerçek paraya geçmeden önce seçilen adaylar TradingView golden export ile eşitlik testinden geçmelidir.

TradingView XLSX/CSV dosyaları kalibrasyon ve parity kanıtı olarak saklanır. Binance mumları günlük araştırma ve kombinasyon eleme işini hızlandırır.
