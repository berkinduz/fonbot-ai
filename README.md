# fonbot-ai

Local-first, taktiksel fon tahsis motoru. Tek bir işe odaklı: her ay maaşımdan ayırdığım TL'yi nereye koyacağıma karar vermek.

Bir miktar veriyorsun. Fonbot TEFAS verisini okuyor, yatırılabilir evreni momentum / trend / volatilite / rejim üzerinden skorluyor ve sana **1 agresif fırsat fonu + 1 para piyasası fonu** ile net bir oran döndürüyor. Alım-satımı sen banka / aracı kurum uygulamandan elle yapıyorsun.

Bu bir karar-destek aracı, otomatik trader değil. Hesabına bağlanmıyor, emir göndermiyor, para hareket ettirmiyor. Aynı zamanda **bilmediği şeyi biliyormuş gibi de yapmıyor** — uydurma haber yok, hayali sentiment yok, sahte "canlı veri" yok. TEFAS erişilemezse, açıkça söylüyor.

> **Not (Türkçe / Geçici kapsam):** Şu anki sürüm sadece **TEFAS** üzerinden çalışıyor, yani pratik olarak Türkiye'de yatırım yapanlar için kullanışlı. Yol haritasında provider katmanını genelleştirip NASDAQ / BIST / diğer borsalar ve kripto API'lerini eklemek var — sistem mimarisi zaten "primary + fallback provider" mantığıyla yazıldı, asıl iş yeni provider sınıfları eklemek.

## Neden var

Çoğu "fon önerisi" scripti son 3 ayın getirisi en yüksek fonu seçip işi bitiriyor. Bu strateji değil; sadece son 3 ayın backtest'i.

Fonbot küçük ama savunulabilir bir tez üzerine kurulu:

- **Momentum birincil sinyaldir.** 3 aylık momentum en yüksek ağırlığı taşır; 6 aylık devamlılığı teyit eder.
- **Trend, volatilite, drawdown ve makro rejim modifier'dır** — yönü değil, *kanaat* ve *pozisyon büyüklüğünü* etkilerler.
- **Sosyal / haber / sentiment üçüncül kaynaktır.** Kullanıcı dışarıdan bağlam getirebilir ama bu, quant skorlamayı asla geçersiz kılamaz.
- **Eksik veri, eksik veri olarak raporlanır.** Sistem görmediği şeyi uydurmaz.

Çıktı bilinçli olarak dar: 1 ana fon, 1 para piyasası tamponu, 1 oran, 1 aksiyon. Leaderboard yok. Tez yazısı yok.

## Bir run ne döner

```
SWITCH: zero-based AFT %75 + AFA %25 | report: reports/2026-05-17_fundbot-ab12cd34ef56.md
```

Bu tek satırın arkasındaki markdown raporu şunları içerir:

- Seçilen agresif fon + para piyasası fonu + oran + TL tutarları
- Skorlarıyla birlikte top 3 aday
- Neden bu dağılım (composite conviction kırılımı)
- Eğer kayıtlı işlemin varsa portföy sürekliliği değerlendirmesi (BUY / HOLD / INCREASE / REDUCE / SWITCH / PARTIAL SWITCH)
- Veri bütünlüğü bloğu: her fonu hangi provider verdi, ne doğrulandı, neye erişilemedi
- Yeniden çalıştırma tetikleri — yeniden değerlendirmeyi gerektirecek somut koşullar

Her karar `reports/decisions.jsonl`'a da append-only olarak yazılır.

## Portföy biçimi

Her karar tam olarak iki bacaktan oluşur:

1. **Ana fırsat fonu** — agresif, en yüksek kanaatli taktiksel seçim.
2. **Para piyasası fonu** — tampon / geçici park katmanı.

Composite conviction'a göre dağılım bantları:

| Kanaat | Agresif | Para Piyasası |
|---|---|---|
| Güçlü (≥80) | %90 | %10 |
| İyi (≥70) | %75 | %25 |
| Orta (≥58) | %65 | %35 |
| Zayıf (≥45) | %50 | %50 |
| Karışık | %35 | %65 |

Motor %100 para piyasasına çekilmez. Her şey kötü görünüyorsa azaltır — saklanmaz.

## Kurulum

Python 3.10+ önerilir (3.9 da `from __future__ import annotations` sayesinde çalışıyor ama hedef değil).

```bash
git clone https://github.com/berkinduz/fonbot-ai.git
cd fonbot-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytefas yfinance  # opsiyonel ama şiddetle önerilir; pytefas birincil TEFAS provider'ı
```

## Kullanım

```bash
# Aylık öneri
python3 main.py --amount 35000

# Evreni belirli kodlarla sınırla (en az bir para piyasası fonu içermeli)
python3 main.py --amount 35000 --codes AFT,AFA

# Cache'i atla, taze çek
python3 main.py --amount 35000 --force-refresh

# Karar üretmeden sadece veri katmanını doğrula
python3 main.py --healthcheck
python3 main.py --healthcheck --healthcheck-code AFT

# Stratejiyi tek cümlede açıkla
python3 main.py --explain
```

### İşlem kaydetme

Motor yalnızca senin açıkça onayladığın işlemlerden state tutar. Broker senkronu yok.

```bash
python3 main.py --record-transaction \
  --tx-code AFT --tx-name "Ak Portföy Yeni Teknolojiler" \
  --tx-action BUY --tx-amount 42000 --tx-date 2026-05-20 \
  --tx-confirmed --tx-role main_opportunity
```

Onaysız kayıtlar history'e eklenir ama `portfolio/portfolio_state.json`'u değiştirmez.

## Veri bütünlüğü

Provider sırası:

1. **pytefas** — birincil, TEFAS'ın resmi JSON uçlarını kullanır; rate-limit'i biliyor.
2. **Direct TEFAS JSON wrapper** — kendi 429 / boş body / decode hata yönetimi ve jitter'lı backoff'u olan fallback.
3. **Crawler placeholder** — TEFAS web şeması yeniden doğrulanana kadar devre dışı.
4. **Manuel CSV / XLSX snapshot** — son çare olarak kullanıcı sağlamalı import.

Ardışık iki TEFAS-backed provider arasında konfigüre edilebilir bir cooldown vardır (default 12s) ki başarısız bir birincil, aynı backend'i hemen hammer'lamasın.

Cache bir performans / degraded-mode yardımcısıdır, gerçeklik kaynağı değil:

- Taze cache, belirli kodlar için tekrar provider çağrısı yapmadan iş görür.
- Bayat cache (konfigüre edilebilir eşik, default 7 gün) **bayat olarak raporlanır** ve **karar-üretmeye uygun veri olarak engellenir**.
- İki provider en son fiyatta tolerans üstünde çatışırsa, o fonun geçmişi sessizce birinden seçilmek yerine bloklanır.

Her rapor şunları açıkça ayırır:

- doğrulanmış veri
- erişilemeyen veri
- tahmin / çıkarsanmış veri
- kullanıcı sağlamalı veri

Motor şunları **yapmaz**:

- piyasa / haber / sosyal sentiment uydurmak
- erişimi olmayan dış API'lere erişiyormuş gibi davranmak
- makro bağlam uydurmak
- kullanıcı sağlamalı anlatıyı ana karar kaynağı olarak kullanmak

Yatırılabilir bir evren kurulamıyorsa motor `veri yok` döner ve eksik veri listesini gösterir. Bütün görünmek için bir şey uydurmaz.

## Portföy state modeli

Gerçeklik kaynağı **sensin, ne yaptığını sen onaylarsın**. Otomatik reconciliation yoktur.

Runtime'da oluşan dosyalar:

```
portfolio/transaction_history.jsonl   # append-only defter
portfolio/portfolio_state.json        # sadece onaylanmış işlemlerden türetilir
portfolio/snapshots/*.json            # her onaylı değişiklikte snapshot
```

Her aylık analiz iki ayrı soruya cevap verir:

- **A)** *Eğer bugün hiç pozisyonum olmasaydı, motor hangi dağılımı seçerdi?*
- **B)** *Mevcut pozisyonlarıma göre en mantıklı aksiyon ne?*

İki cevap farklı olabilir. Mevcut pozisyonlara sadakat ikramiyesi yoktur. Bir fon yalnızca güncel momentum / sıralama / rejim onu hâlâ destekliyorsa ve daha taze bir adaya geçiş avantajı küçükse tutulur.

Desteklenen portföy aksiyonları: `BUY`, `HOLD`, `INCREASE`, `REDUCE`, `SWITCH`, `PARTIAL SWITCH`.

## Mimari

```
main.py                          ince giriş noktası
cli.py                           argüman ayrıştırma ve orkestrasyon
config.py                        parametreler ve yollar
data_fetcher.py                  provider orkestrasyonu + cache güvenliği
data_providers.py                pytefas, direct TEFAS, crawler, manuel snapshot provider'ları
data_provider_healthcheck.py     provider smoke check'leri (--healthcheck tarafından kullanılır)
cache.py                         kaynak atfı + yaş metadata'sıyla SQLite depolama
universe_builder.py              yatırılabilir evren filtreleme
analyzer.py                      momentum / trend / volatilite / drawdown metrikleri
scorer.py                        fırsat ve para piyasası skorlama
regime_detector.py               hafif makro rejim modifier'ı
allocator.py                     iki-bacaklı dağılım kararı
reporter.py                      markdown rapor + append-only JSONL karar history'si
backtester.py                    basit aylık getiri değerlendirme yardımcıları
portfolio_store.py               append-only işlem defteri + türetilmiş state
portfolio_manager.py             quant motorun üstünde stateful süreklilik katmanı
prompts/                         dış araştırma prompt'ları (sadece gerektiğinde)
tests/                           davranış testleri
```

## Testler

```bash
python3 -m unittest discover -s tests
```

15 test, saniyenin çok altında koşuyor.

## Yol haritası

Şu anki hedef: TEFAS akışını sağlamlaştırmak ve gerçek kullanımda yaşatmak. Sıradakiler:

- **Provider katmanını genelleştirmek** — `BaseDataProvider` arayüzü zaten taşınabilir. Bir sonraki tur yeni provider sınıfları:
  - **NASDAQ / NYSE** — yfinance / Alpha Vantage / Polygon adaptörü
  - **BIST hisse** — TradingView / Investing API'leri
  - **Kripto** — Binance / CoinGecko / CCXT
- **Çok varlık sınıfı portföy** — şu anki "1 agresif + 1 money market" yapısının yerine "N varlık sınıfı × M aday" matrisi.
- **Sentiment / news provider** — şu an üçüncül kalan kullanıcı sağlamalı bağlamı yapılandırılmış bir provider haline getirmek (yine ana karar kaynağı değil).
- **Daha akıllı backtester** — şu anki sürüm bilinçli olarak basit; gerçek bir aylık rebalance simülatörü gelmeli.
- **Web UI / dashboard** — şu an CLI-only; karar history'sini grafiksel görmek için küçük bir önyüz.

Yapı zaten "swappable provider" mantığıyla yazıldı; mesele TEFAS dışındaki dünyaları doğru abstract'lamak.

## Bilinen sınırlar ve dürüst uyarılar

- TEFAS public API'si haber vermeden değişebilir. Değiştiğinde provider katmanını güncellemek gerek.
- TEFAS agresif rate-limit uyguluyor (~6 istek/dakika). Geniş evren fetch'i kasıtlı olarak yavaş; motor hız yerine doğruluğu tercih eder.
- Para piyasası fonu seçimi temiz bir yield curve değil, son dönem getirisi proxy'si kullanıyor.
- Makro rejim katmanı bir **modifier**'dır, tahmin motoru değil. Öyle muamele et.
- Backtester bilinçli olarak basit. Parametre optimizasyonu için değil, sanity check için.
- Broker müsaitlik durumu, fon talep pencereleri, lot kuralları ve emir kısıtları fonbot'a görünmüyor — onları kendin kontrol et.

## Tek cümlede felsefe

> Veri net olduğunda agresif, olmadığında savunmacı, ve aradaki farkı söylerken dürüst ol.

## Lisans

MIT.

## Sorumluluk reddi

Bu kişisel bir karar-destek aracı, yatırım tavsiyesi değil. Senin vergi durumunu, likidite ihtiyacını veya risk kapasiteni bilmez. Açtığın her işlemden sen sorumlusun.
