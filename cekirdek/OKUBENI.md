# Defta

Yapay zekâ ile altyazı çevirisi. Ek kütüphane gerektirmez; yalnızca Python 3.11 veya üstü yeterli.

## Kurulum

1. `.env.ornek` dosyasının adını `.env` yap, içine API anahtarını yaz.
2. Çevrilecek altyazıları (`.srt`, `.ass`, `.ssa`, `.vtt`, `.sub`) `kaynak` klasörüne koy. Çıktı her zaman `.srt` olur. Dizi için: `kaynak/Dizi Adı/Sezon 1/bolum01.srt`.
3. Gerekirse `config.toml` içindeki ayarları değiştir.

## Çalıştırma

```
python ceviri.py
```

`calistir.bat` dosyasına çift tıklamak aynı işi yapar.

- Çeviriler `ceviri` klasörüne, aynı klasör yapısıyla ve `bolum01.tr.srt` adıyla yazılır.
- Çevirisi zaten olan dosya atlanır (`uzerine_yaz = true` ile zorlanır).
- Kaynağı zaten hedef dilde olan dosya da atlanır. Dili sözlük adımının keşif isteği söyler; keşif kapalıysa ya da yanıt vermediyse dosyadan 20 satırlık küçük bir istekle sorulur.
- Ctrl+C ile durdurulabilir. Yarıda kesilirse (Ctrl+C, pencere kapanması, internet kesintisi, biten kredi) yeniden çalıştırınca kaldığı yerden devam eder. Çeviriyi etkileyen bir ayar değiştiyse baştan başlar.
- Sunucu yanıt vermezse ya da hız sınırına takılırsa konsolda ve `uyarilar.txt`'de "bekleniyor: …" satırı görünür. Hesabın kredisi bittiyse, anahtar yanlışsa ya da model 10 kez üst üste kullanılabilir yanıt vermezse (yanlış adres, JSON vermeyen model) beklemeden durur, nedenini yazar; biten parçalar saklanır.

## Önce deneme

`config.toml` içinde `[kontrol] deneme_satir = 80` yap. Her dosyanın yalnızca ilk 80 satırı çevrilir ve `bolum01.deneme.tr.srt` olarak yazılır. Beğenince `deneme_satir = 0` yap.

## Ayarlar

Bütün ayarlar `config.toml` dosyasında, konularına göre sıralı ve her satırın yanında açıklamasıyla durur: klasörler, çeviri, model, modlar, hitap, unvan, sözlük, kontrol, çıktı, parça, girdi. Dosyadaki değerler varsayılanların kendisidir; silinen satır aynı değeri alır. Yanlış yazılan değer (sayı yerine yazı, bilinmeyen seçenek) açılışta bildirilir. Kapatılabilen her özellik kapatıldığında istekten, istemden ve denetimlerden tümüyle çıkar.

En sık dokunacakların:

| Ayar | Ne yapar |
|---|---|
| `[model] dusunme_seviyesi` | `low` hızlı ve ucuz; `medium` daha az anlam hatası, yaklaşık 2,5 kat süre ve maliyet |
| `[modlar] kurgu_dili`, `kurgu_anlami` | Elfçe gibi sözler olduğu gibi kalır; kaynak anlamını parantezle vermişse çevrilip altına yazılır |
| `[modlar] sarki` | ♪ işaretli şarkı sözleri: orijinal / orijinal + çeviri / çeviri. Yalnız ilk dizesinin başında ve son dizesinin sonunda ♪ olan şarkının aradaki dizeleri de şarkı sayılır (en çok 40 altyazı, 3 dakika); hiç işaretsiz şarkı tanınmaz |
| `[modlar] konusmaci_tiresi` | İki kişinin konuştuğu altyazıda baştaki çizgi korunur ya da silinir |
| `[hitap]` | Sen/siz: `tablo` (çift kararları isteklere gider), `kanit` (script kaynaktan kesin okuduğunu tabloya yazar), `oy` |
| `[unvan]`, `[unvanlar.tr]` | Adın önündeki unvan (Mr, Captain, 隊長) her yerde aynı yazılır: önce tablo, tabloda yoksa modelin o yapım için verdiği tek karar; `denetim` = duzelt / sor / kapali |
| `[kontrol] kayma_kontrolu` | Çeviri yanlış altyazıya yazılmışsa o blok yeniden sorulur |
| `[cikti] sure_uzat_azami` | Okumaya yetmeyen altyazının süresi sessizliğe doğru uzatılır; metin kısalttırılmaz (kısalırken anlam gidiyordu) |
| `[sozluk] otomatik` | Sözlük adımını kapatır |

## Sözlük (otomatik)

Çeviriden önce her yapım için bir sözlük çıkarılır ve `sozluk` klasörüne yazılır. Her yapımın sözlüğü ayrıdır:

- Dosya bir alt klasördeyse yapım adı klasör adıdır (`kaynak/Yüzüklerin Efendisi/...`).
- Değilse dosya adından çıkarılır: bölüm, yıl ve kalite bilgisi atılır.
- Hedef dil Türkçe değilse dosya adı dil ekini alır (`ad.en.json`).

Nasıl kurulur: tekrar eden adayları script bulur; scriptin göremediklerini (kanjiyle yazılmış kod adı, küçük harfli dünya sözcüğü: halfling, wraith; hikâyenin üstüne kurulduğu kavram: 超能力者) keşif isteği bulur. Model her birinin türünü üç oyla sınıflar (ad, lakap, unvan, kurgu sözcüğü…). Adın yazımını kural belirler; halk adı, unvan, lakap, tanım ve kavramın Türkçesini ise yalnızca eserin adı ve terimin geçtiği satırlarla soran karşılık isteği verir (超能力者 → telepat; `[sozluk] karsilik_dusunme`); üç cevaptan en az ikisi birleşmezse aynı soru iki kez daha sorulur, yine birleşmezse terim sözlüğe girmez ve sonraki bölümde yeniden sorulur. Latin harfli yazım ancak çevrilecek bir sözse sorulur; özel ad gibi kalan (Dúnedain, Vardy) yazımıyla kalır. İlk karar kalıcıdır; sonraki bölümler aynı sözlüğü kullanır. Japonca adların yazımları birbirini tutar (ロイド → Loid ise ロイドマン → Loidman); kısa terim, uzun bir yazımın içinde geçtiği satırda aranmaz. Adın yazımı harf harf okunuş kaldıysa ("Sutera") resmî yazımı sorulur.

Dosyada her bilgi tek yerde durur; her girdi tek satırdır:

| Bölüm | İçerik |
|---|---|
| `girdiler` | Bütün ad ve terim kararları: `tur`, `hedef` (karşılık), `anlam` (bu yapımda neyi kastettiği), `siradan` (yazım aynı zamanda sıradan bir sözcükse o anlamı), öteki yazımlar, örnek satır. `hedef` boşsa sıradan sözcüktür, sözlüğe girmez |
| `unvanlar` | Bu yapımda bir adın yanında geçen unvan sözcükleri |
| `hitap` | Kim kime "samimi" (sen) ya da "resmi" (siz) konuşuyor ("Sam -> Frodo": "resmi") |
| `karakterler`, `adlar` | Karakter adları (yanında modelin kısa notu; isteme girmez); kaynaktaki ad yazımının Latin karşılığı |
| `latin` | Latin dışı kurgu dili parçalarının Latin yazımı |
| `dosyalar` | Dosya başına: kaynak dil, sahne özetleri, kurgu dili işaretleri |

Anlam notu (`[sozluk] anlam_notu`): sözlük adımında model iki şey daha söyler. `anlam`: girdinin bu yapımda neyi kastettiği ("tarlaya musallat olan ruh"). `siradan`: yazım aynı zamanda sıradan bir sözcükse o anlamı ("kıvrılarak"). İstemde ikisi karşılığın yanına yazılır; model satır satır karar verir: kastedilen şeyse sözlükteki karşılık, sıradan anlamdaysa normal çeviri. Sıradan anlamı da olan girdi satır denetimine girmez. `false`: ikisi de istenmez, eski notlar da kullanılmaz.

Elle düzeltmek gerekmez; istersen:

- Bir karşılığı değiştirmek için `girdiler`deki `hedef`i (ve gerekiyorsa `anlam`ı) düzelt. Script var olan kararı değiştirmez.
- `hitap`: yanlış gördüğün çifti düzelt.

Eski düzendeki sözlük dosyaları ilk çalıştırmada kendiliğinden yeni düzene çevrilir; karşılıklar değişmez.

Bir yapımın sözlüğünü baştan kurdurmak için dosyasını sil.

## Loglar

Her çalıştırma `logs/tarih_saat/` klasörüne yazar:

- `ozet.txt`: dosya, satır, süre, istek sayısı, token, bekleme süresi, maliyet (`[model] fiyat_…` ayarlarından).
- `hatalar.txt`: retler, kontrolü geçmeyen satırlar, kalıntı işaretler, model hatası.
- `uyarilar.txt`: yeniden sorulan satırlar ve nedenleri, beklemeler, okuma hızı yüksek satırlar.
- `..._karsilastirma.txt`: her satırın kaynağı ve çevirisi alt alta.
- `..._ham.jsonl`: modele giden ve modelden dönen ham veri.
- `..._istem.txt`: o dosya için modele giden sistem metni.
- `ayarlar.json`: o çalıştırmada geçerli olan bütün ayarlar; `sozluk_<yapım>.json`: yapım sözlüğünün çalıştırma sonundaki kopyası.

## Testler

```
python -m unittest test_defta
```

İnternet ve API anahtarı gerektirmez. Çekirdek (`defta` klasörü) Python 3.8'de de çalışır ve Kodi eklentisi onu kullanır (Kodi 21: Python 3.8); `config.toml` okuyan başlatıcı ve onun testleri Python 3.11 ister, eski sürümde atlanır.

## Şu an olanlar

- Girdi: srt, ass/ssa, vtt ve sub (MicroDVD, MPL2, SubViewer). Çıktı her zaman srt. Kodlama algılama (UTF-8, UTF-16, Japonca Shift-JIS, Türkçe Windows-1254; arada birkaç bozuk bayt olan UTF-8 de), klasör yapısını koruma, kaldığı yerden devam.
- ASS'ten okunanlar: italik, üstte gösterme, ekran yazısı (konumlanmış satır; `[modlar] ekran_yazisi` = cevir / birak / sil), şarkı (stil adından ya da karaoke kodundan; aynı anda birkaç stilde yazılmış dizeden biri kalır). Çizimler, yorum satırları ve üst üste katmanlardaki aynı yazı atılır.
- Çift dilli ASS (aynı replik hem Japonca hem İngilizce yazılmış): özgün dil çevrilir, öteki dilin satırı modele yalnızca başvuru olarak gider, çıktıda tek altyazı olur (`[girdi] cift_dil` = referans / asil / ikinci / kapali).
- Ekran yazısı stil adından da tanınır (sign, title, typeset, caption): `[girdi] ass_yazi_stili`.
- Yapım adı dosya adından çıkarılır: baştaki `[Grup]` etiketi, dil eki (`.ja`, `.ja-en`) ve bölüm numarası (`S01E02`, ` - 02`) atılır; aynı dizinin bölümleri aynı sözlüğü kullanır.
- Ses sözcükleri (Japonca kaynak): altyazının tamamı bir hayvan sesi ya da karakterin kendine özgü seslenmesiyse çevrilmez, Latin harfleriyle yazılır ("Bofu", "Waku waku!"). `[modlar] ses_sozcukleri = "cevir"` yapılırsa anlamı çevrilir ("Hav", "Çok heyecanlı"). Hangi sözcüğün ses olduğunu sözlük adımı belirler ve yapımın sözlüğüne yazar (`"tur": "sound"`); yazımı oradaki `hedef` alanından değiştirilebilir ("Bofu" yerine "Borf"). Daha önce çevrilmiş bir bölümde etkili olması için o yapımın sözlük dosyasını sil.
- VTT'den okunanlar: italik, üstte gösterme (`line:`), konuşmacı (`<v Ad>`). sub'da kare hızı dosyada yazmıyorsa `[girdi] sub_fps` kullanılır.
- Aynı altyazının iki biçimi varsa (film.srt + film.ass) yalnızca biri çevrilir: önce srt, sonra ass, ssa, vtt, sub.
- Yapım başına sözlük, unvan kayıtları ve sen/siz tablosu; ardından parçalı, eşzamanlı çeviri.
- Korunan öğeler: kurgu dili, Japonca ekler (-san, -senpai…) ve teknik adları.
- Japonca ekler (-san, -kun, -chan, -sama, -sensei, -senpai, -dono, -shi, -jou, -tan) yalnızca kişi adından sonra korunur; düşen ek, yeri kesinse script'çe eklenir. Arkasındaki Türkçe hâl eki ekin okunuşuna uydurulur ("Anya-sama'ya", "Sensei'ye", "Mob-kun'un").
- Sık geçen "sözcük + ek" (犬さん, ワンちゃん) sevimli ya da kibar bir adlandırmaysa sözlüğe ekisiz tek karşılıkla girer (köpecik); adın eklisi (黄昏さん) girmez.
- Satır denetimleri: numara, boş satır, Latin harf, italik, çizgi, sözlük terimi, unvan, kayma, okuma hızı. Yanıt yarıda kesilmişse son gelen satır da eksiklerle birlikte yeniden sorulur.
- Bölünmüş cümlenin altyazılara dağıtılması, satır sarma, süre uzatma, son temizlik.

### Birkaç altyazıya bölünmüş cümle (`[ceviri] bolunmus_cumle`)

- `dagit` (varsayılan): cümle modele kesintisiz, tek satır olarak gider. Model çevirir ve konuşmacının duraklayabileceği **bütün** yerleri işaretler; hangilerinden kesileceğini script seçer (süreye en iyi uyan, okuma hızını aşmayan, tek sözcük bırakmayan yerleşim). Duraklama yeri yoksa cümle tek altyazı olur: komşu altyazıların süresi birleşir, başlangıç zamanı değişmez.
- `kapali`: her altyazı kendi başına çevrilir; söz ile yazı birebir eşleşir ama Türkçede parçalar kırık okunabilir.
- Hangi altyazıların tek cümle olduğuna script karar verir (noktalama ve aradaki boşluk); en çok 5 altyazı.

## Henüz olmayanlar

- Görüntü tabanlı altyazılar (VobSub `.sub` + `.idx`, PGS): metin içermez; dosya atlanır ve `hatalar.txt`'ye yazılır.
- ASS çıktısı (stil ve konum korunmaz, çıktı srt'dir), çift dilli çıktı.
- YouTube'un otomatik VTT'lerindeki kayan (her satırı yineleyen) altyazıların tekilleştirilmesi.

## Kod dosyaları (hata ayıklarken nereye bakılır)

| Dosya | İşi |
|---|---|
| `ceviri.py` | Başlatıcı: dosyaları toplar, yapım başına sözlük ve çeviriyi yürütür, özeti yazar |
| `defta/bicim.py` | Biçim okuyucuları: srt dışındaki girdileri (ass, vtt, sub) satırlara çevirir |
| `defta/dosya.py` | Tek dosya: okuma, çevirmene verme, süre ve satır düzeniyle srt yazma |
| `defta/cevirmen.py` | Çeviri çekirdeği: hazırlık, istekler, satır kontrolü, yeniden deneme |
| `defta/cumle.py` | Bölünmüş cümleler: bulma, çeviriyi altyazılara dağıtma |
| `defta/denetim.py` | Şüpheli satırın yeniden sorulması: kayma, kısaltma, klişe, yankı; yinelenen satır (geri dönüş, alıntı) |
| `defta/hitap.py` | Sen/siz: kanıt, tablo oylaması, tablonun isteklere konması |
| `defta/unvan.py` | Unvan ve Japonca saygı eki denetimi |
| `defta/kaynak.py` | Kaynak metni okuma: ekler, konuşmacı etiketi, ses notu, şarkı |
| `defta/analiz.py`, `terim.py`, `sozluk.py` | Sözlük adımı: karakterler, sahneler, terimler, kalıcı sözlük dosyası |
| `defta/istem.py` | Modele giden kurallar (ayarlara göre kurulur) |
| `defta/model.py`, `ayar.py`, `temizlik.py`, `srt.py`, `gunluk.py` | Model istemcisi, ayarlar, metin temizliği, srt, loglar |
