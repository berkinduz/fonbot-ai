# Fonbot Nasıl Çalışır?

Bu dosya bir **kullanıcı oturumunun** baştan sona nasıl ilerlediğini gösterir. Mimari özet için [README](README.md), AI operator'ün uyduğu kurallar için [AGENTS.md](AGENTS.md).

---

## Sistemin iki katmanı

| Katman | Kim | Ne yapar |
|---|---|---|
| **Engine** | Python (deterministik) | TEFAS verisi çeker, otonom dış kaynakları tarar, skorlar, oran üretir |
| **AI Operator** | Claude Code / Codex / Gemini CLI | Engine'i çağırır, raporu okur, kullanıcıyla insan dilinde konuşur |
| **Kullanıcı** | Sen | Kararı verir, işlemi elle yapar, strateji değişikliklerini onaylar |

Engine her seferinde aynı veriden aynı çıktıyı üretir. AI operator durumu yorumlar ve sıradaki adımı önerir. Kullanıcı son sözü söyler.

---

## Tipik bir oturum — aylık karar

**Sen**: "fonbot çalışsın, bu ayın fonunu seçelim"

**AI agent** (içsel: AGENTS.md okur, `python3 main.py --status` çağırır, çıktıyı sindirir)

> "Engine durumu:
> - Cache 2 hafta yaşında, full universe için taze fetch lazım.
> - External context: status=ready age=0d risk_delta=+9 regime_delta=-6.
> - 4 gün sonra TCMB PPK toplantısı — confidence bilinçli olarak cap'lenecek.
> - Portföyünde kayıtlı pozisyon yok.
> - Strateji v1.
>
> TEFAS rate-limit'i nedeniyle ilk fetch dakikalar sürer. Başlayayım mı?"

**Sen**: "evet"

**AI agent** (`python3 main.py --force-refresh` çalıştırır, ~3-5 dakika sürer)

Bu süre boyunca engine arka planda şunları yapar:
1. pytefas üzerinden tüm YAT fon evrenini çeker (~600 fon)
2. Fund profiler ile her fonun gerçek asset breakdown'ını okur (deterministik money market detection)
3. Yahoo Finance'ten 10 makro sembolü 3 farklı pencerede tarar
4. TCMB EVDS / BDDK resmi makro kaynaklarını dener
5. Google News RSS'ten TR faiz/enflasyon/fon haberlerini tarar
6. KAP disclosure'larını çeker (API blok atarsa Google News fallback)
7. Calendar'dan 7 gün içindeki TCMB/TÜİK/FOMC olaylarını kontrol eder
8. Breadth analyzer evren genelindeki momentum durumunu baz rejim olarak hesaplar
9. Tüm dış sinyalleri bounded modifier'lara çevirir (risk delta, regime delta, confidence cap, avoid_funds)
10. Skorlar, allocator'a verir, raporu yazar

**AI agent** (rapor hazır olunca)

> "Tamamlandı:
>
> **BUY** — Sıfırdan başlasaydık dağılım:
> - **Agresif**: AFT (Ak Portföy Yeni Teknolojiler) — %75
> - **Para piyasası**: TPP (TEB Para Piyasası) — %25
> - Güven: 72/100 *(TCMB toplantısı 4 gün sonra olduğu için 80'den 72'ye cap'lendi)*
>
> Engine'in kelimeleriyle ana sebepler:
> - 3 aylık momentum +%18, 6 ay +%34 — devamlılık güçlü
> - Trend MA'lar üstünde, slope pozitif
> - Breadth: 'constructive' (67/100, %62 fon pozitif 3M)
> - External: BIST 6M +%34 destekleyici, ancak Brent 6M +%70 enflasyon headwind'i için risk +5
>
> 100.000 TL yatıracaksan: 75.000 AFT + 25.000 TPP. Tutarın farklıysa oranı uygula.
>
> Rapor: `reports/2026-05-17_fundbot-abc123.md`"

---

## Senaryo: işlem kaydetme

**Sen**: "tamam, 50k AFT aldım az önce"

**AI agent**: "Anladığım kadarıyla AFT'den 50.000 TL alım yaptın, bugün (17 Mayıs), doğru mu? Onaylarsan portföy state'ine kaydedeceğim."

**Sen**: "evet"

**AI agent** (`python3 main.py --record-transaction --tx-code AFT --tx-amount 50000 --tx-date 2026-05-17 --tx-confirmed --tx-role main_opportunity`)

> "Kaydettim. Snapshot da alındı. TPP tarafını da alacak mısın?"

Hard rule: AI agent kullanıcının "evet" demediği bir işlemi state'e yazmaz. Pending kayıt history'e düşer ama `portfolio_state.json` değişmez.

---

## Senaryo: ay içinde takip

**Sen** (2 hafta sonra): "AFT nasıl gidiyor?"

**AI agent** (içsel: `--status`, ardından cache yaşına göre tek-fon refresh)

> "Status: AFT pozisyonun 50.000 TL maliyet, cache 2 hafta yaşında. Tek fon için taze fetch'leyim, hâlâ HOLD mu yoksa momentum bozulduysa REDUCE mı görürüz."

**Sen**: "bak"

**AI agent** (`python3 main.py --codes AFT,TPP --force-refresh`)

> "Momentum hâlâ güçlü, AFT skoru 79 (önceki 81'den hafif düşüş). Trend bozulmamış. Portfolio decision: **HOLD**. Yeni para girişin yoksa aksiyon önerimi yok.
>
> Geçen snapshot'tan bu yana değişim: yok (AFT maliyetinde değişiklik olmamış)."

---

## Senaryo: yeni Grok cevabı geldiğinde

**Sen**: "Grok'a yarı iletken sektörünü sordum, şu cevabı verdi: [uzun text]"

**AI agent**: "Kaynak Grok, AFT etrafında, relevance medium olarak işaretleyeyim mi?"

**Sen**: "olur"

**AI agent** (notu stdin üzerinden `--record-research`'e besler)

> "Kaydettim (`research/2026-05-31_grok_yariletken-q3.md`). Sonraki karar koşulduğunda otomatik bağlam olarak rapora girecek.
>
> Hatırlatma: Bu not quant skoru etkilemez — engine zaten TEFAS breadth + resmi makro + Yahoo + Google News + KAP'tan kendi başına bağlam topluyor. Senin notun rapora ek bir paragraf olarak çıkacak."

---

## Senaryo: geçmişin değerlendirilmesi

**Sen** (3 ay sonra): "şu ana kadarki önerilerimiz tutmuş mu?"

**AI agent** (`python3 main.py --backtest --backtest-window 30`)

> "3 karar değerlendirildi (30 günlük pencere):
>
> - **Ortalama portföy getirisi**: +%4.2
> - **Money market'e karşı outperformance**: +%2.8 (hit rate 67%)
> - **Top-3 equal-weight baseline'a karşı**: +%0.5 (hit rate 33%)
>
> Yorum: Money market'i geçtik ama top-3 baseline'a karşı zayıfız. Bu, ya konsantrasyonun çok yüksek (tek fona %75) ya da aday seçiminin top-3'ten farklılaşmasında değer kaybettiği anlamına gelir. Bir tur daha geçtikten sonra ağırlıkları gözden geçirmeyi öneririm."

Backtester sahte hassasiyet üretmez — yetersiz cache'lenmiş fiyat varsa "skipped" olarak rapor eder.

---

## Senaryo: strateji değişikliği

**Sen**: "3M ağırlığı bana çok yüksek geliyor, 0.45'ten 0.35'e indirelim. 6M 0.25'ten 0.35'e çıksın."

**AI agent**:
> "Bu değişiklik konvansiyonu tersine çevirmez ama motoru kısa vadeli rotasyondan ziyade trend persistence'ına yöneltir. Etki:
> - Kısa süreli hızlı yükselen fonlar daha az tercih edilir
> - 6 aylık trendi tutarlı fonlar öne çıkar
> - Volatil fırsatlar gözden kaçabilir
>
> `strategy/weights.json`'u değiştirip `strategy/history.jsonl`'a şu reason ile loglayayım mı: 'kullanıcı daha sabırlı momentum istiyor, 3M↓ 6M↑'? Testleri de çalıştırıp raporlarım."

**Sen**: "yap"

**AI agent** (weights.json edit → history.jsonl append → `python3 -m unittest discover -s tests` → `--status`)

> "Yapıldı, 57/57 test geçti. Strategy v2, history'e kaydedildi. Sonraki kararda yeni ağırlıklarla skorlanacak."

---

## Veri akışı — bir koşumda

```
[Engine başlar]
   │
   ├─► pytefas / direct TEFAS  ────► tüm fon fiyatları + asset breakdown
   ├─► Yahoo Finance (paralel)  ───► 10 sembol × 3 pencere
   ├─► TCMB EVDS / BDDK         ───► resmi TR makro context
   ├─► Google News RSS         ────► TR faiz/enflasyon/haber
   ├─► KAP API → fallback      ────► fon disclosure'ları
   ├─► Calendar (lokal)        ────► 7 gün içindeki olaylar
   │
   ├─► Universe builder + fund profiler  → yatırılabilir evren
   ├─► Analyzer (per-fund)              → momentum/trend/volatilite + anomaly
   ├─► Breadth analyzer                 → baz rejim sinyali
   ├─► External intelligence            → bounded modifier'lar (cross-source confirmation)
   ├─► External context gate            → freshness check + confidence cap
   │
   ├─► Scorer + Allocator               → 1 agresif + 1 money market + oran
   ├─► Portfolio manager                → mevcut state ile karşılaştır + snapshot diff
   ├─► Reporter                         → markdown rapor + decisions.jsonl append
   │
   └─► [Engine biter]  →  AI agent raporu okur → kullanıcıya açıklar
```

---

## Karakter ve sınırlar

- Kullanıcı **doğal Türkçe** konuşur, komut yazmaz.
- AI agent her oturumun ilk adımında **`--status`** çağırır (orient olmak için).
- AI agent quant'ın kelimeleriyle "neden" der; "ne yapayım" yorumunu kendisi katar.
- Her state-mutating aksiyon (transaction, strateji değişikliği) **kullanıcı onayı** ister.
- AI agent **uydurmaz**: external scanner'ın getirmediği hiçbir piyasa verisini icat etmez.
- Engine **otonom**: external scanner için kullanıcı girişi gerekmez. `research/` katmanı tamamen opsiyoneldir.
- Cache yalnızca performans için, gerçeklik kaynağı değil. Bayat cache otomatik bloklanır.
- Hard rule: dış veri quant skoru asla geçersiz kılmaz; sadece confidence ve risk delta uygular.

---

## Özet

Sen miktarı / niyeti söylersin. AI agent engine'i çağırır, raporu okur, sana Türkçe özetler. Engine arka planda TEFAS breadth + resmi makro + Yahoo + Google News + KAP + calendar verisini otonom toplar, bounded modifier'lara çevirir, ratio üretir. Trade'i sen elle yaparsın, onayını AI agent'a iletirsin, bir sonraki ay döngü baştan başlar.

Her şey görünür: kararlar `reports/decisions.jsonl`'da, işlemler `portfolio/transaction_history.jsonl`'da, strateji değişiklikleri `strategy/history.jsonl`'da. Hiçbir şey gizli değil, hiçbir şey kullanıcı onayı olmadan kalıcı değişmiyor.
