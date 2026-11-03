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
- İsteğe bağlı: bölümün %90'ı (ayarlanabilir: %80–95) izlenince sıradaki bölümü arkada çevirir (Ayarlar → Otomatik: Kapalı / Sor / Otomatik).
- Yapılan çeviriler saklanır: aynı bölüm ikinci kez açıldığında **Defta AI** satırından ücretsiz yüklenir.
- Jimaku açıksa her videoda sorulur: animenin Japonca altyazısı doğru sezondan listeye gelir.
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

1. **• Etkin altyazı**: Defta'nın bu videoya en son yüklediği (insan çevirisi ya da AI çevirisi). Aşağıda tekrar çıkmaz.
2. **Kaymış · sıradakine geç**: yüklenen altyazı senkron değilse; işaretler, sıradakini yükler ya da çevirir.
3. Hedef dildeki insan çevirileri (en fazla 10; senkron uyumlu, sonra önceki bölümün devamı, sonra sürümü uyan)
4. **AI (EN)**: daha önce yaptığın çeviriler (ücretsiz, en çok 3; parantezde hangi dilden çevrildiği)
5. **→ Çevir**: çevrilecek yabancı altyazılar. Seçince çeviri başlar.
6. **Baştan çevir (ücretli)**: yalnızca etkin AI çevirisi için. Başka bir çeviriyi baştan çevirmek için önce onu seç.
7. **Yükleneni çevir**: bu video başladıktan sonra başka bir eklentiyle (a4k vb.) altyazı seçtiysen
   dosya adıyla görünür ve onu çevirir; yoksa bu satır çıkmaz.

Kodi'nin *İlk altyazıyı otomatik indir* ayarı açıksa Kodi ilk satırı kendisi yükler: etkin altyazı varsa o
(ücretsiz), yoksa en iyi insan çevirisi; ikisi de yoksa zararsız bir bilgi satırı. Ücretli çeviri kendiliğinden başlamaz.

## Ayarlar

- **Genel:** çeviri dili (varsayılan Türkçe), kaynak dili (varsayılan İngilizce), ek kaynak dilleri, model,
  çeviri modu (*Hızlı önce, kaliteli sonra* / *Yalnızca kaliteli* / *Yalnızca hızlı*), künye adı
- **Kaynaklar:** her kaynak için aç/kapat ve anahtar, listede kaç altyazı çıkacağı (3/5/10/tümü), SDH'yi alta indir
- **Otomatik** (hepsi varsayılan kapalı): insan çevirisini, hazır AI çevirisini kendiliğinden yükle;
  hedef dilde altyazı yoksa çeviriyi kendiliğinden başlat; sıradaki bölümü önceden çevir (*Kapalı / Sor / Otomatik*).
  *Sor*: bölümün ayardaki kadarı (varsayılan %90) izlenince sağ üstte küçük bir kutu çıkar, video durmaz. İmleç *Çevir*de durur, alttaki
  çubuk 15 saniyede boşalınca çevrilir; *İptal* ya da Geri tuşuyla vazgeçersin.
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
- **Kendiliğinden temizlik** (Kodi açılınca, her çeviri bitince ve 6 saatte bir): geçici dosyalar, hızlı çeviriler,
  iptal edilen işlerin artıkları 6 saat sonra; indirilen yabancı altyazılar ve yarım çevirilerin parçaları 30 gün
  sonra silinir. Hazır AI çevirileri ve seri sözlükleri silinmez.

## Geliştirme

- `service.subtitles.defta/`: eklenti
- `repository.defta/`: Kodi deposu (otomatik güncelleme)
- `tools/build.py`: çeviri motorunu (subtrans) kopyalar, kuralları JSON'a çevirir, zip ve depo dosyalarını üretir
- `tests/`: Kodi'siz testler (sahte Kodi modülleri, sahte OpenAI sunucusu); Python 3.8 ve üstü

```
python tools/build.py --repo
python -m unittest tests/test_defta.py
```
