# Fonbot Nasıl Çalışır?

Fonbot, TEFAS fon evreninden veri çekip, nicel (quant) skorlamayla aylık fon dağılımı önerisi üretir. Kullanıcıdan gelen doğal Türkçe komutları işler, karar ve portföy yönetimini kolaylaştırır.

---

## 1. Temel Akış: Aylık Fon Seçimi

### Adım 1: Kullanıcı Komutu

```
"fonbot çalışsın, bu ayın fonunu seçelim"
```

### Adım 2: Fonbot Durumu Kontrol Eder

Fonbot AGENTS.md'yi okur ve `python3 main.py --status` komutunu çalıştırır, durumu özetler:

- **Cache yaşı:** 2 fon, en taze fiyat 15 Mayıs
- **Taze veri ihtiyacı:** Bu ay için full universe çekmek gerekli
- **Önceki karar:** Kayıtlı yok
- **Portföy pozisyonu:** Yok
- **Strateji:** v1, default ağırlıklar
- **Research notu:** 1 nota (Grok'tan AFT teknoloji) — rapora ek olarak eklenecek
- **Uyarı:** TEFAS rate-limit'i nedeniyle dakikalar sürecek

**Kullanıcıdan onay ister:**

```
Başlayayım mı?
```

### Adım 3: Kullanıcı Onayı

```
"evet başla"
```

### Adım 4: Fonbot Rapor Üretir

`python3 main.py --force-refresh` ile veri çeker, 3–5 dakika bekler, raporu üretir.

**Sonuç özeti:**

| Bileşen           | Değer                                    |
| ----------------- | ---------------------------------------- |
| **Aksiyon**       | BUY                                      |
| **Agresif Fon**   | AFT (Ak Portföy Yeni Teknolojiler) — %75 |
| **Para Piyasası** | TPP (TEB Para Piyasası) — %25            |
| **Güven Skoru**   | 78/100                                   |

**Engine'in Nedenleri:**

- 3 aylık momentum: +%18, 6 ay: +%34 — devamlılık güçlü
- Trend MA'lar üstünde, slope pozitif
- Volatilite yüksek (%42 annualized) ama hesaplanan risk cezası %75 bandını korumaya yetiyor
- Makro rejim 'mixed' (60/100), agresif bandı bir aşağıya çekmedi
- **Research notu:** Grok'tan yarı iletken Q3 toparlanma beklentisi — bağlam olarak eklendi, skoru etkilemedi

**Yatırım Örneği:**

```
100.000 TL yatıracaksan:
  75.000 TL → AFT
  25.000 TL → TPP

Tutarın farklıysa oranı uygula.
```

**Detaylı Rapor:**

```
reports/2026-05-17_fundbot-abc123.md
```

---

## 2. Portföy İşlemleri: Alım/Satım Kaydı

### Kullanıcı İşlem Bildiriyor

```
"tamam, 50k AFT aldım az önce"
```

### Fonbot Onay İster

```
Anladığım kadarıyla AFT'den 50.000 TL alım yaptın, bugün (17 Mayıs), doğru mu?
Onaylarsan portföy state'ine kaydedeceğim.
```

### Kullanıcı Onaylar

```
"evet"
```

### Fonbot Kaydeder

```bash
python3 main.py --record-transaction \
  --tx-code AFT \
  --tx-amount 50000 \
  --tx-date 2026-05-17 \
  --tx-confirmed \
  --tx-role main_opportunity
```

**İşlem tamamlandı:**

- Portföy state güncellendi
- Snapshot alındı
- Eksik kalan taraf (TPP) hakkında soruş yapıldı

---

## 3. Takip ve Durum Sorgulama

### Kullanıcı Pozisyon Sorgular

```
"AFT nasıl gidiyor?"
```

_(2 hafta sonra)_

### Fonbot Durumu Kontrol Eder

```bash
python3 main.py --status
# cache yaşı, portföy state, gerekirse tek-fon refresh
```

**Rapor:**

```
Cache 2 hafta yaşında, AFT pozisyonun 50.000 TL maliyet.
Son fiyat günlemesi gerekli. Taze veriye bakalım mı?
```

### Kullanıcı Onay Verir

```
"evet bak"
```

### Fonbot Veri Tazeler

```bash
python3 main.py --codes AFT,TPP --force-refresh
```

**Sonuç:**

```
Momentum hâlâ güçlü, AFT skoru 79 (önceki 81).
Hafif düşüş ama trend bozulmuş değil.

Portfolio decision: HOLD
Switch advantage düşük, yeni para girişin yoksa aksiyon önerimi yok.
```

---

## 4. Research Notu Ekleme

### Kullanıcı Dış Araştırma Paylaşır

```
"Grok'a soft-landing senaryosu için sordum, şu cevabı verdi:
[yapıştırılan uzun metin]"
```

### Fonbot Kaynağı ve Önem Derecesini Sorar

```
Notu işliyorum:
- Kaynak: Grok
- İlgili fonlar: AFT etrafında
- ABD makro etkili
→ Relevance: medium olarak işaretleyeyim, uygun mu?
```

### Kullanıcı Onaylar

```
"tamam"
```

### Fonbot Kaydeder

```bash
python3 main.py --record-research \
  --research-topic soft-landing-grok-may \
  --research-source grok \
  --research-relevance medium \
  --research-funds AFT \
  --research-body-file /tmp/note.md
```

**Sonuç:**

```
Kaydettim. Sonraki karar koşulduğunda otomatik bağlam olarak rapora girecek.
Skoru etkilemeyecek — quant primary, narrative tertiary.
```

---

## 5. Strateji Evrimi

### Kullanıcı Değişiklik Önerir

```
"3 aylık momentum ağırlığı bana çok yüksek geliyor.
0.45'ten 0.35'e düşürelim, 6 aylık 0.25'ten 0.35'e çıksın.
Daha sabırlı bir motor olsun."
```

### Fonbot Etkisini Açıklar

```
Bu değişiklik konvansiyonu tersine çevirmez ama motoru
momentum'un sürekliliğine daha çok güvenmeye iter.

Etki:
- Kısa süreli hızlı yükselen fonlar göreceli olarak daha az tercih edilir
- 6 aylık trendi tutarlı fonlar öne çıkar
- Volatil fırsatlar (kısa süreli rotasyon) gözden kaçabilir

strategy/weights.json'u değiştirip strategy/history.jsonl'a
şu reason ile loglayayım mı:
'kullanıcı daha sabırlı bir momentum istiyor, 3M↓ 6M↑'?
Testleri de koşturup raporlarım.
```

### Kullanıcı Onaylar

```
"yap"
```

### Fonbot Değişikliği Uygular

```bash
# 1. weights.json düzenlenir
# 2. history.jsonl'a log eklenir
# 3. Testler çalıştırılır
python3 -m unittest discover -s tests
# 4. Durum kontrol edilir
python3 main.py --status
```

**Sonuç:**

```
Yapıldı, 21/21 test geçti. Strategy v2, history'e kaydedildi.
Sonraki kararda yeni ağırlıklarla skorlanacak.
```

---

## 6. Temel Karakteristikler ve Kurallar

| Kural                      | Açıklama                                                                            |
| -------------------------- | ----------------------------------------------------------------------------------- |
| **Doğal Türkçe**           | Kullanıcı komut yazmaz, doğal dil konuşur                                           |
| **Durum → Onay → Aksiyon** | Fonbot önce durumu özetler, onay alır, sonra işlem yapar                            |
| **Engine'in Sözü**         | "Neden" sorularında engine'in kelimeleriyle yanıt verir                             |
| **Explicit Onay**          | Her state-mutating işlem (transaction, strateji) için kullanıcıdan açık onay alınır |
| **Research ≠ Skor**        | Research notları sadece bağlam olarak eklenir, skoru etkilemez                      |
| **Veri Uydurmaz**          | Kullanıcıdan veya engine'den gelen bilgi dışında hiçbir şey uydurmaz                |

---

## 📝 Özet

Fonbot, kullanıcıdan gelen doğal komutları engine'e çevirir, karar ve portföy yönetimini şeffaf ve izlenebilir şekilde yürütür. Tüm önemli adımlar kullanıcı onayıyla ilerler, engine'in ürettiği gerekçeler doğrudan aktarılır.
