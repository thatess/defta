# Defta

Kodi için altyazı eklentisi. OpenSubtitles, SubDL, SubSource ve Jimaku'da altyazı arar. İstediğin dilde
(varsayılan Türkçe) altyazı yoksa yabancı bir altyazıyı OpenAI ile doğal bir dille çevirir.

- Önce orijinal altyazı görünür, birkaç saniyede hızlı çeviri, anlam kontrolü bitince kontrol edilmiş hâli
  yüklenir (en çok iki yükleme). Bir dizi bölümü genelde 30 saniyede ve birkaç sentle çevrilir.
- Senkron: video dosyasıyla birebir eşleşen altyazı (OpenSubtitles video karması) **senkron uyumlu** yazısıyla en
  üstte. Altyazı videodan uzunsa uyarır; fark kare hızındansa (25 / 23.976) zamanlamayı kendisi düzeltir.
  Yüklenen altyazı kaymışsa listedeki **Şu anki altyazı kaymış** satırı onu işaretler ve sıradakine geçer.
- Dizide bir önceki bölümde seçtiğin çevirmenin/sürümün devamı **önceki bölümde kullandığın** yazısıyla öne alınır.
- Başka bir satır seçersen süren çeviri durur (son seçilen kazanır); aynı satırı yeniden seçersen kaldığı yerden sürer.
- İsteğe bağlı: bölümün %90'ı izlenince sıradaki bölümü arkada çevirir (Ayarlar → Otomatik: Kapalı / Sor / Otomatik).
- Yapılan çeviriler saklanır: aynı bölüm ikinci kez açıldığında **Defta AI** satırından ücretsiz yüklenir.
- Seri sözlüğü: isimler her bölümde aynı yazılır. Hitap ekleri (-san, senpai) korunur, şarkılar çevrilir.
- Kodi 21 (Omega), Windows ve Android/Nvidia Shield'da çalışır. Ek kütüphane gerekmez.

## Kurulum (Nvidia Shield ve diğer cihazlar)

1. **Bilinmeyen kaynaklara izin ver:** Ayarlar → Sistem → Eklentiler → *Bilinmeyen kaynaklar* → Aç.
2. **Kaynağı ekle:** Ayarlar → Dosya yöneticisi → *Kaynak ekle* → `<Yok>` satırına
   `https://thatess.github.io/defta/` yaz → ad olarak `defta` yaz → Tamam.
3. **Depoyu kur:** Eklentiler → *Eklenti tarayıcısı* (kutu simgesi) → *Zip dosyasından yükle* → `defta` →
   `repository.defta.zip`.
4. **Defta'yı kur:** *Depodan yükle* → *Defta Deposu* → *Altyazılar* → *Defta* → Yükle.
   Bundan sonra yeni sürümler Kodi tarafından kendiliğinden güncellenir.
5. **Varsayılan altyazı servisi:** Ayarlar → Oynatıcı → Dil → *Varsayılan film servisi* ve
   *Varsayılan dizi servisi* → **Defta**.

## Anahtarlar

Defta ayarları → **Genel** ve **Kaynaklar**:

| Ne | Nereden | Zorunlu mu |
|---|---|---|
| OpenAI API anahtarı | platform.openai.com → API keys | Çeviri için evet |
| OpenSubtitles API anahtarı | opensubtitles.com → hesabım → API consumers (*Allow anonymous downloads* ve *Under dev* işaretli) | Hayır |
| OpenSubtitles kullanıcı adı/şifre | kendi hesabın | Hayır (girersen indirme hakkı hesabından düşer) |
| SubDL API anahtarı | subdl.com → hesap → API | Hayır |
| SubSource API anahtarı | subsource.net → hesap → API sekmesi | Hayır |
| Jimaku API anahtarı | jimaku.cc → hesap | Hayır (anime, Japonca) |

Anahtarı girilmeyen kaynak atlanır. Her kaynak ayrı ayrı kapatılabilir.

**Uzun anahtarları kumandayla yazmak zorsa:** anahtarları bir metin dosyasına satır satır yaz:

```
OPENAI_API_KEY=sk-...
OPENSUBTITLES_API_KEY=...
OPENSUBTITLES_USERNAME=...
OPENSUBTITLES_PASSWORD=...
SUBDL_API_KEY=...
SUBSOURCE_API_KEY=...
JIMAKU_API_KEY=...
```

Dosyayı `defta-anahtarlar.txt` adıyla USB belleğe ya da ağ klasörüne koy. Sonra Defta ayarları → Genel →
**Anahtar dosyasını seç** ile göster. Ayarlara yazılan anahtar her zaman dosyadakinin önüne geçer.
Anahtarlar hiçbir zaman kayda yazılmaz; ekranda yalnızca uzunlukları görünür.

## Kullanım

Video oynarken altyazı simgesi → **İndir** → Defta. Liste yukarıdan aşağı şöyledir:

0. **Şu anki altyazı kaymış** (yalnızca Defta bu videoya altyazı yüklediyse): işaretler, sıradakini yükler/çevirir.
   Kodi'nin *İlk altyazıyı otomatik indir* ayarı açıksa bu satır en altta durur.
1. Hedef dildeki insan çevirileri (en fazla 10; senkron uyumlu, sonra önceki bölümün devamı, sonra sürümü uyan)
2. **Defta AI**: daha önce yaptığın çeviriler (ücretsiz) ve *Baştan yeniden çevir*
3. **→ AI çeviri**: çevrilecek yabancı altyazılar. Seçince çeviri başlar.
4. **Yüklenen altyazıyı çevir**: başka bir eklentiyle (a4k vb.) seçtiğin altyazıyı çevirir.

Kodi'nin *İlk altyazıyı otomatik indir* ayarı açıksa, ücretli çeviri satırı (otomatik çeviri kapalıyken)
hiçbir zaman ilk sırada olmaz.

## Ayarlar

- **Genel:** çeviri dili (varsayılan Türkçe), kaynak dili (varsayılan İngilizce), ek kaynak dilleri, model,
  çeviri modu (*Hızlı önce, kaliteli sonra* / *Yalnızca kaliteli* / *Yalnızca hızlı*), künye adı
- **Kaynaklar:** her kaynak için aç/kapat ve anahtar, listede kaç altyazı çıkacağı (3/5/10/tümü), SDH'yi alta indir
- **Otomatik** (hepsi varsayılan kapalı): insan çevirisini, hazır AI çevirisini kendiliğinden yükle;
  hedef dilde altyazı yoksa çeviriyi kendiliğinden başlat; sıradaki bölümü önceden çevir (*Kapalı / Sor / Otomatik*).
  *Sor*: bölümün %90'ı izlenince 15 saniyelik soru çıkar, imleç *Çevir*de durur; bir şeye basmazsan çevrilir.
  Sıradaki bölümde Türkçe altyazı ya da hazır çeviri varsa sorulmaz.
- **Altyazı:** ses notları, konuşan etiketleri, tireler, şarkı sözleri, italik/biçim etiketleri (varsayılan *Kaldır*)
- **Temizlik:** AI çevirileri, arama sonuçları, indirilen altyazılar, seri sözlükleri, çeviri hafızası,
  yarım işler, API önbelleği, senkron işaretleri ve *Tümünü sil* (anahtarlar ve ayarlar silinmez)
- **Yardım:** bağlantı testi, kurulum rehberi, Kodi kayıtlarını kopyala, AI çevirilerini dışa aktar, ayrıntılı kayıt,
  kayıt klasörü (bilgisayarın paylaşılan klasörü olabilir: `smb://BILGISAYAR/subtitles`)

## Sorun giderme

- **Bağlantı testi:** Ayarlar → Yardım → *Bağlantıları ve anahtarları test et*. Her kaynağın durumunu gösterir.
- **Bir site açılmıyor:** İnternet sağlayıcısı sitenin adını engelliyorsa Defta, Cloudflare/Google güvenli
  DNS'i ile adresi kendisi bulur.
- **Kodi kapanırsa:** süren çeviri, Kodi açılınca kaldığı yerden devam eder (biten parçalar tekrar ödenmez).

## Geliştirme

- `service.subtitles.defta/`: eklenti
- `repository.defta/`: Kodi deposu (otomatik güncelleme)
- `tools/build.py`: çeviri motorunu (subtrans) kopyalar, kuralları JSON'a çevirir, zip ve depo dosyalarını üretir
- `tests/`: Kodi'siz testler (sahte Kodi modülleri, sahte OpenAI sunucusu); Python 3.8 ve üstü

```
python tools/build.py --repo
python -m unittest tests/test_defta.py
```
