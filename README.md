# fonbot-ai

**AI agent'lar tarafından çalıştırılmak üzere tasarlanmış, taktiksel TEFAS fon tahsis motoru.**

[📖 Sistem nasıl çalışır?](HOW_IT_WORKS.md) · [🤖 AI Operatör Kılavuzu](AGENTS.md)

---

Fonbot bir CLI değil, bir **engine**. İnsan kullanıcı terminale komut yazmaz; Claude Code / Codex / Gemini CLI gibi bir **AI operator** fonbot'u çalıştırır, çıktısını okur, kullanıcıya insan dilinde açıklar.

Saf Python her seferinde aynı kararı verir ama stratejiyi evrimleştiremez. AI agent stratejiyi evrimleştirir ama deterministik skorlama yapamaz. İkisi birlikte: Python skorlar, AI yorumlar ve geliştirir.

```
Kullanıcı  ──"bu ayın fonunu seçelim"──►  AI agent  ──python3 main.py──►  Engine
                                              │                              │
                                              ◄──── markdown rapor ──────────┘
                                              │
Kullanıcı  ◄──"şu fonu %75, şuna %25; ana ──┘
              gerekçesi şu..."
```

## Kapsam

**Şu an**: yalnızca **TEFAS** (Türkiye yatırım fonları). Hedef kitle: TL ile sistematik yatırım yapmak isteyen amatör yatırımcılar.

**Yol haritası**: `BaseDataProvider` interface'i provider-agnostik. Sonraki turlarda NASDAQ / NYSE (yfinance), BIST hisse, kripto (CoinGecko / CCXT) provider'ları eklenince motor değişmeden çalışır. Çok varlık sınıfı portföy ve web UI sonraki adımlar.

## Çıktı

Her run tek bir karar üretir: **1 agresif ana fon + 1 düşük-risk para piyasası fonu + oran + aksiyon**. Tutar değil, sadece oran — TL hesabını kullanıcı yapar.

```
BUY: zero-based AFT %75 + TPP %25 | confidence 78/100 | report: reports/2026-05-17_fundbot-abc123.md
```

Markdown raporu içerir: top 3 aday + skorları, neden bu dağılım, portföy sürekliliği aksiyonu (BUY/HOLD/SWITCH/REDUCE/INCREASE/PARTIAL SWITCH), data integrity bloğu (hangi provider hangi veriyi getirdi), KAP/calendar/breadth gerekçeleri, yeniden çalıştırma tetikleyicileri.

## Otonom veri katmanları

Engine her karar koşumunda **kullanıcı girişi olmadan** şunları çeker:

| Katman | Kaynak | Ne sağlar |
|---|---|---|
| TEFAS fiyat geçmişi | pytefas → direct TEFAS → manuel snapshot | Tüm YAT evreni, rate-limit aware |
| TEFAS asset breakdown | pytefas `breakdown` view | Her fonun gerçek asset allocation'ı (deterministik money market detection) |
| Makro proxy | Yahoo Finance (10 sembol × 1M/3M/6M) | USDTRY, EURTRY, Nasdaq, SP500, Gold, BIST100, US10Y, VIX, Brent, EM_Equity |
| Resmi TR makro | TCMB EVDS + BDDK haftalık bülten | EVDS API key varsa resmi USDTRY/EURTRY/faiz/enflasyon; BDDK kredi/bankacılık bülteni |
| Türkiye haber/faiz/enflasyon | Google News RSS | TCMB faiz, TÜİK enflasyon, market news, fon-spesifik aramalar |
| KAP disclosure | KAP API → Google News `site:kap.org.tr` fallback | Otoriter TR fon disclosure'ları (tasfiye, yönetim değişikliği, vb.) |
| Calendar | Hard-coded (yıllık güncellenir) | TCMB MPC, TÜİK CPI, FOMC tarihleri; 7 gün içindeyse pre-event risk |
| Breadth (evren-içi) | Skorlanan fonlardan türetilir | TR-spesifik rejim sinyali: % pozitif 3M momentum |

Tüm bunlar **bounded modifier**'lara dönüşür (risk delta, regime delta, confidence cap, avoid_funds). Cross-source confirmation: tek kaynaktan yapısal haber "candidate", 2+ kaynak veya KAP onayı "confirmed avoid".

Opsiyonel olarak kullanıcı `--record-research` ile manuel notlar (Grok cevabı, X yorumu) ekleyebilir, ama **engine onsuz da %100 çalışır**.

## Felsefe

- **Momentum birincil.** 3M en yüksek ağırlık, 6M devamlılık teyidi.
- **Trend / volatilite / drawdown / makro rejim modifier.** Yön değil, kanaat ve pozisyon büyüklüğünü etkilerler.
- **Dış veri tertiary.** Otomatik scanner'dan gelen sentiment-like sinyaller bile quant skoru asla geçersiz kılmaz; sadece confidence ve risk'i modifiye eder.
- **Eksik veri eksik raporlanır.** Engine görmediği şeyi uydurmaz.

## Kimin için

- Elinde nakit duruyor, "hangi fona?" kararını her ay yeniden vermek istemeyen amatör yatırımcılar.
- Claude Code / Codex / Gemini CLI gibi bir AI assistant kullanan, bu assistant'ın aylık yatırım kararını sistematik bir şekilde yönetmesini isteyen kullanıcılar.

**Kimin için değil**: aktif trader'lar, "garantili kazandır" bekleyenler, AI assistant kullanmak istemeyenler.

## Kurulum

```bash
git clone https://github.com/berkinduz/fonbot-ai.git
cd fonbot-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytefas yfinance pyyaml  # opsiyonel, şiddetle önerilir
```

Sonrasında **Claude Code / Codex / Gemini CLI** ile bu dizini aç. AI agent [`AGENTS.md`](AGENTS.md)'yi okuyup ne yapacağını bilir.

## CLI komutları

Bu komutlar normalde AI agent tarafından çağrılır.

```bash
python3 main.py --status                     # Engine durumu (her oturumun ilk adımı)
python3 main.py                              # Aylık karar — full TEFAS evreni
python3 main.py --force-refresh              # Cache'i atla, taze çek
python3 main.py --scan-only --codes AFT,TPP  # Sadece external scanner'ı çalıştır
python3 main.py --refresh-external-context   # Karar üretirken external'i zorla yenile
python3 main.py --healthcheck                # Data provider smoke test
python3 main.py --backtest                   # Geçmiş kararları cache'lenmiş fiyatlarla değerlendir
python3 main.py --fetch-article URL          # AI operator için tek-makale fetcher
python3 main.py --record-transaction ...     # Kullanıcı onaylı işlem kaydı
python3 main.py --record-research ...        # (Opsiyonel) manuel bağlam notu
python3 main.py --explain                    # Stratejiyi açıkla
python3 main.py --help                       # Tam komut listesi
```

TCMB EVDS entegrasyonu opsiyoneldir. Resmi EVDS verisini kullanmak için ortam değişkeni tanımla:

```bash
export TCMB_EVDS_API_KEY="..."
```

Key yoksa engine çalışmaya devam eder; raporda resmi EVDS verisi “yok” kabul edilir, sahte makro değer üretilmez.

## Mimari

```
main.py / cli.py                 entrypoint + AI operator komut yüzeyi
config.py / strategy/            parametreler (kod-dışı, mutable, append-only history)
data_fetcher.py / data_providers tefas fetch + provider orchestration + rate-limit
cache.py                         SQLite (fiyat + metadata persistence)
universe_builder.py              yatırılabilir evren filtreleme
analyzer.py                      momentum/trend/volatilite/drawdown + anomaly detection
scorer.py / allocator.py         skorlama + iki-bacaklı dağılım kararı
breadth_analyzer.py              baz rejim sinyali (% pozitif 3M)
regime_detector.py               legacy makro rejim helper (ana karar akışında kullanılmaz)
external_scan.py                 Yahoo (10 sembol × 1M/3M/6M) + Google News + KAP scanner
official_macro.py                TCMB EVDS + BDDK resmi makro scanner
external_intelligence.py         scan → bounded modifier (cross-source confirmation)
external_context.py              gate + freshness + calendar entegrasyonu
external_calendar.py             pre-known event awareness (TCMB MPC / TÜİK CPI / FOMC)
kap_provider.py                  KAP API + Google News fallback
fund_profiler.py                 TEFAS breakdown → deterministik asset class detection
article_fetcher.py               AI agent için tek-makale fetcher
research_store.py                OPSİYONEL kullanıcı sağlamalı dış bağlam
portfolio_store.py               append-only işlem defteri + state + snapshots
portfolio_manager.py             stateful süreklilik (snapshot diff dahil)
backtester.py                    decisions.jsonl replay + realized return değerlendirme
reporter.py                      markdown rapor + decisions.jsonl
AGENTS.md                        AI operator manual
PROVIDER_TEMPLATE.md             yeni provider eklerken contract
SIGNAL_TEMPLATE.md               yeni sinyal eklerken contract
tests/                           57 davranış testi
```

## Strateji evrimi

`strategy/weights.json` mutable; AI operator backtest veya kullanıcı geri bildirimine göre değişiklik önerir. **Her değişiklik kullanıcı onayı gerektirir** — otomatik tuning yok (overfitting). Değişiklikler `strategy/history.jsonl`'a append-only loglanır (kim/ne zaman/neden).

Yeni provider veya sinyal: [`PROVIDER_TEMPLATE.md`](PROVIDER_TEMPLATE.md) / [`SIGNAL_TEMPLATE.md`](SIGNAL_TEMPLATE.md).

## Portföy state

Gerçeklik kaynağı kullanıcının açık onayı. Broker senkronu yok. Runtime dosyaları:

```
portfolio/transaction_history.jsonl   # append-only defter
portfolio/portfolio_state.json        # sadece onaylı işlemlerden türetilir
portfolio/snapshots/*.json            # her mutation'da snapshot
```

Her aylık analiz iki ayrı soruya cevap verir:
- **A)** Sıfırdan başlasaydım hangi dağılım?
- **B)** Mevcut pozisyonlarıma göre ne yapmalıyım?

Cevaplar farklı olabilir. Mevcut pozisyona sadakat ikramiyesi yok.

## Testler

```bash
python3 -m unittest discover -s tests
# 57 tests, <0.2s
```

## Bilinen sınırlar

- TEFAS public API'si haber vermeden değişebilir; provider katmanı güncellenmesi gerek.
- TEFAS rate-limit (~6 req/dk) — geniş evren fetch'i kasıtlı yavaş.
- Google News RSS yapısı değişebilir; scanner failure sessizce loglanır, engine çalışmaya devam eder.
- KAP direct API user-agent bloğu düşürür; engine otomatik Google News `site:kap.org.tr` fallback'ine geçer.
- Backtester değerlendirme penceresi cache'deki fiyat geçmişine bağlıdır; pencere için fiyat yoksa o karar "skipped" olarak raporlanır, sahte sonuç üretilmez.
- Calendar katmanı yıllık güncellenir (TCMB/TÜİK/FOMC tarihleri).

## Felsefe (tek cümlede)

> Veri net olduğunda agresif, olmadığında savunmacı, ve aradaki farkı söylerken dürüst ol. Karar Python'da, evrim AI agent'ta, onay insanda.

## Lisans

MIT.

## Sorumluluk reddi

Bu kişisel bir karar-destek aracıdır, yatırım tavsiyesi değildir. Senin vergi durumunu, likidite ihtiyacını veya risk kapasiteni bilmez. Açtığın her işlemden sen sorumlusun.
