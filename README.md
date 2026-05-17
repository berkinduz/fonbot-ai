# fonbot-ai

**AI agent'lar tarafından çalıştırılmak üzere tasarlanmış, taktiksel TEFAS fon tahsis motoru.**

Fonbot bir CLI tool değil — bir **engine**. İnsan kullanıcı doğrudan terminale girip Python komutu yazmaz. Bunun yerine bir **AI operator** (Claude Code, Codex, Gemini CLI, Hermes, OpenHands, vs.) fonbot'u çalıştırır, çıktısını okur, kullanıcıya insan dilinde açıklar.

Saf Python karar veremez. AI agent karar veremez (her sefer farklı çıkarır). İkisi bir arada → güçlü: Python deterministik skorlama yapar, AI agent stratejiyi evrimleştirir, harici bağlamı entegre eder, kullanıcıyla konuşur.

## Kullanım modeli

```
Kullanıcı  ──"bu ayın fonunu seçelim"──►  AI agent  ──python3 main.py──►  Engine
                                              │                              │
                                              ◄──── markdown rapor ──────────┘
                                              │
Kullanıcı  ◄──"şu fonu %75, şuna %25 öner, ──┘
                ana sebebi şu..."
```

Engine ne yapar:

- TEFAS'tan yüzlerce fonu çeker
- Momentum / trend / volatilite / rejim üzerinden skorlar
- 1 agresif ana fon + 1 düşük-risk para piyasası fonu seçer
- Oran üretir (tutar değil — TL hesabını kullanıcı yapar)
- Markdown rapor ve append-only JSONL karar history'si yazar

AI operator ne yapar:

- Doğru zamanda doğru komutu çağırır (`--status`, `--healthcheck`, `--record-research`, vs.)
- Raporu okur, sebep-sonuç ilişkilerini kullanıcıya açıklar
- Harici araştırmayı (Grok cevabı, X yorumu, haber) `research/` altına entegre eder
- Kullanıcı strateji ayarı önerirse `strategy/weights.json`'u değiştirmek için onay alır, değiştirir, log'lar
- Yeni provider / sinyal eklenmesi gerekirse `PROVIDER_TEMPLATE.md` / `SIGNAL_TEMPLATE.md`'yi takip eder

Detaylı operator manual: `AGENTS.md`.
Sistem akışının adım adım anlatımı: `HOW_IT_WORKS.md`.

## Mevcut kapsam ve yol haritası

**Şu an**: yalnızca **TEFAS** (Türkiye yatırım fonları). Pratik kullanıcı kitlesi: Türkiye'de TL ile yatırım yapanlar.

**Yol haritası**: provider katmanı zaten "primary + fallback" mantığıyla `BaseDataProvider` interface'i üzerinden yazıldı. Yeni provider sınıfları eklenince motor değişmeden çalışır:

- **NASDAQ / NYSE** — yfinance / Alpha Vantage / Polygon
- **BIST hisse** — alternatif API'ler
- **Kripto** — Binance / CoinGecko / CCXT
- Daha sonra: çok varlık sınıfı portföy (şu anki "1 agresif + 1 money market" yerine N varlık × M aday)
- Web UI / dashboard (şu an CLI-only, AI agent'lar üzerinden çalıştırılıyor)

## Neden var

Çoğu "fon önerisi" scripti son 3 ayın getirisi en yüksek fonu seçip işi bitiriyor. Bu strateji değil; sadece son 3 ayın backtest'i.

Fonbot küçük ama savunulabilir bir tez üzerine kurulu:

- **Momentum birincil sinyaldir.** 3 aylık momentum en yüksek ağırlığı taşır; 6 aylık devamlılığı teyit eder.
- **Trend, volatilite, drawdown ve makro rejim modifier'dır** — yönü değil, _kanaat_ ve _pozisyon büyüklüğünü_ etkilerler.
- **Sosyal / haber / sentiment üçüncül kaynaktır.** AI operator harici bağlam getirebilir ama bu, quant skorlamayı asla geçersiz kılamaz.
- **Eksik veri, eksik veri olarak raporlanır.** Sistem görmediği şeyi uydurmaz.

Çıktı bilinçli olarak dar: 1 ana fon, 1 para piyasası tamponu, 1 oran, 1 aksiyon.

## Kimin için

- "Elimde para var, nakit beklesin istemiyorum, hangi fona koyacağıma sistematik karar verilsin" diyen amatör yatırımcılar.
- AI assistant (Claude Code / Codex / Gemini CLI / vs.) kullanan, bu assistant'ın aylık yatırım kararını yönetmesini isteyen kullanıcılar.

## Kimin için değil

- Aktif trader'lar (günlük alım-satım botu değil).
- "Bana garantili kazandıracak fonu söyle" diyenler (garanti yok).
- AI assistant kullanmak istemeyen, doğrudan terminal kullanıcısı olmayı tercih edenler (mümkün ama hedef akış değil).

## Kurulum

```bash
git clone https://github.com/berkinduz/fonbot-ai.git
cd fonbot-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytefas yfinance pyyaml  # opsiyonel ama önerilir
```

Sonrasında **Claude Code / Codex / Gemini CLI** içinden bu dizini aç ve AI agent'a "bu ayın fonunu seçelim" / "Grok'tan şunu aldım, sisteme ekle" / "şu ağırlığı değiştirelim" gibi şeyler söyle. AI agent [`AGENTS.md`](AGENTS.md)'yi okuyup ne yapacağını bilir.

## Engine CLI komutları (AI agent için)

Bu komutlar normalde **AI agent tarafından çağrılır**. İnsan da elle çalıştırabilir ama hedef akış değil.

```bash
# Engine durumu (her oturumun ilk adımı)
python3 main.py --status

# Aylık karar — tüm TEFAS YAT evrenini analiz eder, ratio üretir
python3 main.py

# Cache'i atla, taze çek (TEFAS rate-limit'i nedeniyle dakikalar sürebilir)
python3 main.py --force-refresh

# Veri provider katmanını doğrula (karar üretmeden)
python3 main.py --healthcheck

# Kullanıcı bağlamını (Grok cevabı vs.) sisteme ekle
echo "..." | python3 main.py --record-research \
  --research-topic tech-fonlari-grok-q3 \
  --research-source grok \
  --research-relevance medium \
  --research-funds AFT

# Kullanıcının yaptığı işlemi kaydet (sadece confirmed olanlar state'i değiştirir)
python3 main.py --record-transaction \
  --tx-code AFT --tx-action BUY --tx-amount 42000 \
  --tx-date 2026-05-20 --tx-confirmed --tx-role main_opportunity

# Stratejiyi açıkla
python3 main.py --explain
```

Tam komut listesi: `python3 main.py --help`.

## Veri bütünlüğü

Provider sırası:

1. **pytefas** — birincil, TEFAS resmi JSON uçları; rate-limit aware.
2. **Direct TEFAS JSON wrapper** — fallback (429 / boş body / decode hatasına karşı jitter'lı backoff).
3. **Crawler placeholder** — devre dışı (TEFAS web şeması değişirse hazır).
4. **Manuel CSV / XLSX snapshot** — son çare, kullanıcı sağlamalı.

Ardışık TEFAS-backed provider'lar arasında konfigüre edilebilir cooldown (default 12s) — aynı backend hammer'lanmaz.

Cache yalnızca performans yardımcısıdır:

- Taze cache tekrar provider çağrısı yapmadan iş görür.
- Bayat cache (default 7 gün) **bayat olarak raporlanır** ve **karar üretmeye uygun veri olarak engellenir**.
- Provider çatışması (latest price'da tolerans üstü fark) varsa o fonun history'si bloklanır.

Her rapor şunları **açıkça ayırır**: doğrulanmış veri / erişilemeyen veri / tahmini veri / kullanıcı sağlamalı veri.

Engine şunları **yapmaz**: piyasa/haber/sosyal sentiment uydurmak, erişimi olmayan API'lere erişiyormuş gibi davranmak, makro bağlam uydurmak, kullanıcı sağlamalı anlatıyı ana karar kaynağı olarak kullanmak.

## Stratejinin evrimi

`strategy/weights.json` mutable. AI operator backtest sonuçlarını veya kullanıcı geri bildirimini değerlendirir, parametre değişikliği önerir. **Kullanıcı her değişikliği tek tek onaylar** — otomatik tuning yok (overfitting tuzağı).

Her değişiklik `strategy/history.jsonl`'a append-only loglanır: ne değişti, ne zaman, kim onayladı, neden.

Yeni provider veya sinyal eklemek için: [`PROVIDER_TEMPLATE.md`](PROVIDER_TEMPLATE.md) ve [`SIGNAL_TEMPLATE.md`](SIGNAL_TEMPLATE.md).

## Portföy state modeli

Gerçeklik kaynağı **kullanıcının açık onayı**. Broker senkronu yok.

Runtime dosyaları:

```
portfolio/transaction_history.jsonl   # append-only defter
portfolio/portfolio_state.json        # sadece onaylı işlemlerden türetilir
portfolio/snapshots/*.json            # her onaylı değişiklikte snapshot
```

Her aylık analiz iki ayrı soruya cevap verir:

- **A)** _Sıfırdan başlasaydım hangi dağılımı seçerdim?_
- **B)** _Mevcut pozisyonlarıma göre ne yapmalıyım?_

Aksiyonlar: `BUY`, `HOLD`, `INCREASE`, `REDUCE`, `SWITCH`, `PARTIAL SWITCH`. Mevcut pozisyona sadakat ikramiyesi yok; fon yalnızca momentum/sıralama/rejim onu hâlâ destekliyorsa tutulur.

## Mimari

```
main.py / cli.py                 entrypoint + AI operator komut yüzeyi
config.py                        parametreler ve yollar
data_fetcher.py                  provider orkestrasyonu + cache güvenliği
data_providers.py                pytefas, direct TEFAS, crawler, manuel snapshot
data_provider_healthcheck.py     provider smoke check'leri
cache.py                         SQLite + kaynak atfı + yaş metadata
universe_builder.py              yatırılabilir evren filtreleme
analyzer.py                      momentum / trend / volatilite / drawdown
scorer.py                        skorlama (ağırlıklar strategy/weights.json'dan)
regime_detector.py               makro rejim modifier
allocator.py                     iki-bacaklı dağılım (band'lar weights.json'dan)
reporter.py                      markdown rapor + decisions.jsonl
portfolio_store.py               append-only işlem defteri + türetilmiş state
portfolio_manager.py             stateful süreklilik katmanı
research_store.py                kullanıcı sağlamalı dış bağlam (research/)
strategy_loader.py               weights.json yükleme + default fallback
strategy/                        weights.json + history.jsonl
research/                        kullanıcı sağlamalı notlar (gitignored)
prompts/                         dış araştırma için kullanıcı promptları
tests/                           davranış testleri
AGENTS.md                        AI operator manual
PROVIDER_TEMPLATE.md             yeni provider eklerken takip edilecek
SIGNAL_TEMPLATE.md               yeni sinyal eklerken takip edilecek
```

## Testler

```bash
python3 -m unittest discover -s tests
```

21 test, saniyenin çok altında koşuyor.

## Bilinen sınırlar

- TEFAS public API'si haber vermeden değişebilir; provider katmanı güncellenmesi gerek.
- TEFAS rate-limit (~6 req/dk) — geniş evren fetch'i kasıtlı yavaş.
- Cache hit'te metadata kayboluyor (`name=code, category="cached"`) — money market keyword matching o durumda çalışmıyor. Bilinen bug, yakında düzelir.
- Backtester şu an minimal. Gerçek aylık rebalance simülatörü yol haritasında.
- Macro rejim katmanı bir modifier, tahmin motoru değil.

## Felsefe (tek cümlede)

> Veri net olduğunda agresif, olmadığında savunmacı, ve aradaki farkı söylerken dürüst ol. Karar Python'da, evrim AI agent'ta, onay insanda.

## Lisans

MIT.

## Sorumluluk reddi

Kişisel karar-destek aracı, yatırım tavsiyesi değil. Açtığın her işlemden sen sorumlusun.
