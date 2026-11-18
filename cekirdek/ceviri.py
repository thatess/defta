"""Defta başlatıcı. Kullanım:  python ceviri.py   (ayarlar config.toml, anahtar .env)"""
import json
import os
import re
import sys
import threading
import time
import traceback
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait

from defta import ayar as ayar_modulu
from defta.analiz import dizi_analiz
from defta.bicim import UZANTILAR, BicimHatasi
from defta.dosya import dosya_cevir, dosya_oku
from defta.gunluk import Gunluk
from defta.istem import istem_olustur
from defta.model import Iptal, Model, ModelHatasi
from defta.sozluk import Sozluk, seri_adi
from defta.terim import dil_sor

KOK = os.path.dirname(os.path.abspath(__file__))
_DIL = r"(?:en|eng|ja|jpn|jp|ko|kor|zh|chi|fr|fre|de|ger|es|spa|it|ita|ru|rus|pt|por|ar|ara|tr|tur|sdh|hi|cc|forced)"
DIL_EKI = re.compile(rf"\.{_DIL}(?:[-_+]{_DIL})?$", re.I)          # ".en", ".ja-en" (çift dilli)
ON_ETIKET = re.compile(r"^(?:\s*(?:\[[^\]]*\]|【[^】]*】|\([^)]*\)|（[^）]*）))+\s*")     # baştaki "[Grup]" etiketleri

def yol(p):
    return p if os.path.isabs(p) else os.path.join(KOK, p)


def hedef_adi(dosya_adi, kod):
    govde = os.path.splitext(dosya_adi)[0]
    while DIL_EKI.search(govde):
        govde = DIL_EKI.sub("", govde)
    return f"{govde}.{kod}.srt"


BASLIK_SONU = re.compile(
    r"(?i)[\s._\-]*(?:\s-\s+\d{1,3}(?:v\d)?(?!\d)|\bS\d{1,2}[\s._-]?E\d{1,3}\b|\b\d{1,2}x\d{2,3}\b|\bS\d{1,2}\b|\bEP?\d{1,3}\b"
    r"|\b(?:19|20)\d{2}\b|\b\d{3,4}p\b|\b(?:WEB|WEBRip|BluRay|BRRip|HDTV|REMUX|MULTi|DVDRip|x264|x265)\b"
    r"|[\[【(（])")


def yapim_adi(goreli):
    """Yapımın adı: alt klasördeyse klasör adı, değilse dosya adından grup etiketi, dil eki ve bölüm/yıl/kalite
    bilgisi atılarak. Aynı dizinin bölümleri aynı adı vermelidir: sözlükleri ortaktır."""
    parcalar = goreli.split(os.sep)
    if len(parcalar) > 1:
        return parcalar[0]
    govde = os.path.splitext(parcalar[0])[0]
    while DIL_EKI.search(govde):
        govde = DIL_EKI.sub("", govde)
    govde = ON_ETIKET.sub("", govde) or govde          # "[Grup] Dizi - 01 [720p]": grup adı yapımın adı değildir
    m = BASLIK_SONU.search(govde)
    baslik = govde[:m.start()] if m and m.start() > 0 else govde
    baslik = re.sub(r"[._]+", " ", baslik).strip(" -")
    return baslik or govde


def mevcut_basliklar(klasor):
    """Sözlük klasöründeki sözlüklerin başlıkları (seri eşleştirmesi için)."""
    basliklar = []
    try:
        adlar = sorted(os.listdir(klasor))
    except OSError:
        return basliklar
    for ad in adlar:
        if ad.endswith(".json"):
            try:
                with open(os.path.join(klasor, ad), encoding="utf-8") as f:
                    baslik = json.load(f).get("baslik")
            except (OSError, ValueError, AttributeError):
                continue
            if isinstance(baslik, str) and baslik.strip():
                basliklar.append(baslik.strip())
    return basliklar


def sozluk_anahtari(baslik):
    """Dosya adı olarak güvenli, yazım farklarına dayanıklı anahtar (Spy.x.Family = SPY×FAMILY)."""
    anahtar = re.sub(r"[^\w]+", "", baslik.replace("×", "x").casefold(), flags=re.UNICODE)
    return anahtar or "adsiz"


def main():
    for akis in (sys.stdout, sys.stderr):   # Windows konsolu Japonca dosya adlarında çökmesin
        try:
            akis.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        ayar_modulu.env_yukle(yol(".env"))
        # İsteğe bağlı: python ceviri.py baska_ayar.toml  (deneme ayarları için)
        ayar = ayar_modulu.ayar_yukle(yol(sys.argv[1] if len(sys.argv) > 1 else "config.toml"))
    except ayar_modulu.AyarHatasi as e:
        sys.exit(f"Ayar hatası: {e}")
    anahtar = os.environ.get(ayar["model"]["anahtar_degiskeni"], "")
    if not anahtar:
        sys.exit(f".env dosyasında {ayar['model']['anahtar_degiskeni']} bulunamadı.")

    k = ayar["klasorler"]
    kaynak, ceviri = yol(k["kaynak"]), yol(k["ceviri"])
    if not os.path.isdir(kaynak):
        sys.exit(f"Kaynak klasör bulunamadı: {kaynak}")
    gunluk = Gunluk(yol(k["logs"]))
    gunluk.ayarlar(ayar)
    model = Model(ayar, anahtar)
    kod = ayar["ceviri"]["hedef_dil"]
    dil_eki = "" if ayar_modulu.hedef_kodu(ayar) == "tr" else "." + ayar_modulu.hedef_kodu(ayar)
    deneme = ayar["kontrol"]["deneme_satir"]

    # Çevrilecek dosyaları topla
    isler, atlanan, hedefler = [], 0, {}
    for klasor, _, adlar in os.walk(kaynak):
        adlar = [ad for ad in adlar if ad.lower().endswith(UZANTILAR)]
        # Aynı altyazının iki biçimi varsa (film.srt + film.ass) çıktıları aynı dosyadır: önceliği olan çevrilir.
        # Çıktı adına göre sıralanır: dil eki (film.ass + film.en.srt) biçim önceliğinin önüne geçmesin.
        for ad in sorted(adlar, key=lambda x: (hedef_adi(x, kod), UZANTILAR.index(os.path.splitext(x)[1].lower()), x)):
            goreli = os.path.relpath(os.path.join(klasor, ad), kaynak)
            hedef = os.path.join(ceviri, os.path.dirname(goreli),
                                 hedef_adi(ad, ("deneme." if deneme > 0 else "") + kod))
            tam = os.path.join(ceviri, os.path.dirname(goreli), hedef_adi(ad, kod))
            if hedef in hedefler:
                atlanan += 1
                print(f"atlandı (aynı altyazının başka biçimi çevriliyor: {hedefler[hedef]}): {goreli}")
                continue
            hedefler[hedef] = goreli
            # Deneme modunda tam çevirisi olan dosya da atlanır: yoksa bütün arşiv yeniden çevrilirdi.
            if (os.path.exists(hedef) or os.path.exists(tam)) and not k["uzerine_yaz"]:
                atlanan += 1
                print(f"atlandı (çevirisi var): {goreli}")
            else:
                isler.append((goreli, hedef))
    isler.sort()
    if not isler:
        sys.exit(f"Çevrilecek altyazı dosyası yok ({', '.join(UZANTILAR)})." if not atlanan else "Tüm dosyaların çevirisi zaten var.")

    basla = time.time()
    cevrilen = satir = tum_satir = 0
    sozluk_suresi = 0.0
    sozlukler = {}
    kilit = threading.Lock()
    durum = {}            # dosya -> (biten satır, toplam satır): tek ilerleme satırı için

    # Aynı serinin filmleri tek sözlüğü paylaşır: başlık, sözlüğü olan ya da bu çalıştırmadaki bir seri başlığıyla başlıyorsa
    # o serinin sözlüğü kullanılır ("The Lord of the Rings The Two Towers" -> "The Lord of the Rings").
    basliklar = mevcut_basliklar(yol(k["sozluk"])) + sorted({yapim_adi(g) for g, _ in isler})

    def seri(goreli):
        return seri_adi(yapim_adi(goreli), basliklar)

    def sozluk_al(goreli):
        baslik = seri(goreli)
        ad = sozluk_anahtari(baslik)
        with kilit:
            if ad not in sozlukler:
                # Sözlükteki karşılıklar hedef dildedir: Türkçe dışındaki hedefin dosyası ayrı tutulur (ad.en.json).
                sozlukler[ad] = Sozluk(os.path.join(yol(k["sozluk"]), ad + dil_eki + ".json"))
                sozlukler[ad].baslik = sozlukler[ad].baslik or baslik
                if sozlukler[ad].eski_duzen:        # eski düzendeki dosya bir kez, hemen yeni düzene çevrilir
                    sozlukler[ad].kaydet()
                    sozlukler[ad].eski_duzen = False
        return sozlukler[ad]

    def yaz(metin="", bitti=False):
        """İlerleme tek satırda güncellenir; biten dosya kendi satırına yazılır."""
        with kilit:
            ozet = (f"  {sum(b for b, _ in durum.values())}/{sum(t for _, t in durum.values())} satır"
                    if durum else "")
            print("\r" + (metin.ljust(78) + "\n" if bitti else "") + ozet.ljust(30), end="", flush=True)

    def bekleme_bildir(mesaj):
        """Hız sınırı, sunucu hatası, yanıtsız istek: konsolda ve uyarilar.txt'de görünür, çalışma donmuş sanılmaz."""
        gunluk.uyari("-", f"[{time.strftime('%H:%M:%S')}] bekleniyor: {mesaj}")
        yaz(f"bekleniyor: {mesaj}", True)
    model.bildir = bekleme_bildir

    okunmus = {}           # sözlük adımının okuduğu dosyalar: çeviri bir daha okumaz

    def oku(goreli):
        """Dosya bir kez okunur. Okunamazsa None: hatayı çeviri adımı (dosya_cevir) yeniden okuyup rapora yazar."""
        try:
            return dosya_oku(os.path.join(kaynak, goreli), ayar)
        except BicimHatasi:
            return None

    def dosya_isle(is_):
        goreli, hedef = is_
        sozluk, t0 = sozluk_al(goreli), time.time()
        okunan = okunmus.pop(goreli, None) or oku(goreli)
        dil = sozluk.dil.get(goreli) or (dil_sor(okunan[1], ayar, model, gunluk, goreli) if okunan and okunan[1] else "")
        if dil.split("-")[0] == ayar_modulu.hedef_kodu(ayar):
            gunluk.uyari(goreli, "kaynak dil hedef dille aynı, çevrilmedi")
            yaz(f"atlandı (zaten {ayar_modulu.yerel_dil_adi(ayar)}): {goreli}", True)
            return None

        def ilerleme(biten, toplam):
            with kilit:                         # yaz() aynı sözlüğü başka iş parçacığında dolaşıyor olabilir
                durum[goreli] = (biten, toplam)
            yaz()
        gorunum = sozluk.gorunum(ayar)          # ayarla kapatılan bölümler çeviriye hiç ulaşmaz
        basvuru = bool(okunan) and any(s.ref for s in okunan[1])   # çift dilli dosyada "ref" kuralı da isteme yazılır
        sistem = istem_olustur(ayar, gorunum, basvuru)
        gunluk.istem(goreli, sistem)
        sonuc = dosya_cevir(os.path.join(kaynak, goreli), hedef, goreli, ayar, model, sistem,
                            gunluk, yol(".onbellek"), ilerleme, gorunum, okunan)
        if not sonuc["toplam_satir"]:           # okunamadı ya da altyazısı yok: çıktı yazılmadı, çevrildi sayılmaz
            yaz(f"atlandı (okunamadı, nedeni hatalar.txt'de): {goreli}", True)
            return None
        yaz(f"✓ {goreli}: {sonuc['satir']} satır, {time.time() - t0:.0f} sn", True)
        return sonuc

    def yapim_isle(dosyalar):
        """Bir yapım: önce sözlüğü (1. geçiş), biter bitmez dosyalarının çevirisi (2. geçiş). Yapımlar birbirini
        beklemez; bütün istekler aynı eşzamanlılık sınırını paylaşır."""
        sozluk = sozluk_al(dosyalar[0][0])
        sure, t0 = 0.0, time.time()
        if ayar["sozluk"]["otomatik"]:
            okunan = []
            for goreli, _ in dosyalar:
                if goreli in sozluk.analiz_edilen:
                    continue
                okunmus[goreli] = oku(goreli)
                if okunmus[goreli] and okunmus[goreli][1]:
                    okunan.append((goreli, okunmus[goreli][1],
                                   f"{sozluk.baslik} / {os.path.splitext(os.path.basename(goreli))[0]}"))
            if okunan:
                dizi_analiz(okunan, ayar, model, sozluk, gunluk)
                if deneme > 0:   # deneme modunda dosyaların tamamı okunmadı; sonra yeniden analiz edilsin
                    sozluk.analiz_edilen = [d for d in sozluk.analiz_edilen if d not in dict(dosyalar)]
                    sozluk.kaydet()
                sure = time.time() - t0
                yaz(f"sözlük hazır: {sozluk.baslik} ({len(okunan)} dosya, {sure:.0f} sn)", True)
        with ThreadPoolExecutor(max_workers=min(16, len(dosyalar))) as havuz:   # dosyalar aynı anda
            sonuclar = list(havuz.map(dosya_isle, dosyalar))
        return sure, sonuclar

    print(f"{len(isler)} dosya çevrilecek. Model: {ayar['model']['model']}")
    if ayar["unvan"]["acik"]:
        tablo = ayar_modulu.unvan_tablosu(ayar)
        print(f"Unvan tablosu: {ayar_modulu.hedef_kodu(ayar)}, {len(tablo)} satır" if tablo else
              "Unvan tablosu yok ya da kapalı: " + ("kararı model veriyor" if ayar["unvan"]["model_karari"]
                                                     else "unvanlar yönetilmiyor"))
    yapimlar = {}
    for is_ in isler:
        yapimlar.setdefault(sozluk_anahtari(seri(is_[0])), []).append(is_)
    havuz = ThreadPoolExecutor(max_workers=min(8, len(yapimlar)))
    gelecekler = [havuz.submit(yapim_isle, d) for d in yapimlar.values()]
    durduruldu = False
    try:
        # Kısa aralıklarla beklenir: Windows'ta Ctrl+C ancak beklemenin arasında gelir. Bir yapım hatayla biterse
        # beklemek gereksizdir; model düzelmeyecek hatada kalan istekleri zaten kendisi durdurur.
        bekleyen = set(gelecekler)
        while bekleyen and not any(g.exception() for g in gelecekler if g.done()):
            _, bekleyen = wait(bekleyen, timeout=0.2, return_when=FIRST_EXCEPTION)
    except KeyboardInterrupt:
        durduruldu = True
        print("\nDurduruluyor: gönderilmiş istekler bırakılıyor...")
    if durduruldu or any(g.exception() for g in gelecekler if g.done()):
        model.iptal.set()                       # kalan işler yeni istek göndermeden biter
    havuz.shutdown(wait=True, cancel_futures=True)
    hatalar = [g.exception() for g in gelecekler if not g.cancelled() and g.exception() is not None]
    # Asıl neden: Iptal değil, onu başlatan hata (aynı yapımın başka dosyası önce Iptal ile bitmiş olabilir). Model
    # hatalarında çalışmayı ilk durduran (model.hata): yolda olan öteki istekler de sonradan hata vermiş olabilir.
    asil = (next((e for e in hatalar if not isinstance(e, (Iptal, ModelHatasi))), None) or getattr(model, "hata", None)
            or next((e for e in hatalar if not isinstance(e, Iptal)), None))
    for g in gelecekler:
        if g.cancelled() or g.exception() is not None:
            continue
        sure, sonuclar = g.result()
        sozluk_suresi = max(sozluk_suresi, sure)
        for sonuc in sonuclar:
            if sonuc is None:
                atlanan += 1
                continue
            cevrilen += 1
            satir += sonuc["satir"]
            tum_satir += sonuc["toplam_satir"]
    if isinstance(asil, ModelHatasi):
        print(f"\nModel hatası, çalışma durdu: {asil}\nBiten bölümler saklandı; yeniden çalıştırınca kaldığı yerden devam eder.")
        gunluk.hata("-", f"model hatası: {asil}")
    elif asil is not None:                      # programın kendi hatası: kayda geçer, özet yine yazılır
        iz = "".join(traceback.format_exception(asil))
        print(f"\nBeklenmeyen hata, çalışma durdu:\n{iz}")
        gunluk.hata("-", f"beklenmeyen hata:\n{iz}")
    elif durduruldu or hatalar:
        print("\nDurduruldu. Yeniden çalıştırınca kaldığı yerden devam eder.")
    else:
        print()

    for ad, sozluk in sozlukler.items():
        hedef = os.path.join(gunluk.klasor, "sozluk_" + ad + ".json")
        if os.path.exists(sozluk.yol):
            with open(sozluk.yol, encoding="utf-8") as f, open(hedef, "w", encoding="utf-8") as g:
                g.write(f.read())

    sure = time.time() - basla
    ozet = [f"Sözlük adımı: {sozluk_suresi:.0f} sn", f"Çevrilen dosya: {cevrilen}", f"Atlanan dosya: {atlanan}", f"Çevrilen satır: {satir}",
            f"Süre: {sure:.0f} sn", f"İstek sayısı: {model.istek}",
            f"Girdi tokeni: {model.girdi} (önbellekten: {model.onbellekten})",
            f"Çıktı tokeni: {model.cikti}", f"Rapora yazılan uyarı/hata: {gunluk.hata_sayisi}"]
    if model.sureler:
        ozet.append(f"İstek süresi: ortalama {sum(model.sureler) / len(model.sureler):.1f} sn, "
                    f"en uzun {max(model.sureler):.1f} sn")
    if model.sayac:
        ozet.append("Sayaçlar: " + ", ".join(f"{k} {v}" for k, v in sorted(model.sayac.items())))
    ozet.append(f"Hız sınırı/sunucu beklemesi: {model.bekleme_sayisi} kez, toplam {model.bekleme_suresi:.0f} sn")
    ozet.append(f"Geciktiği için kopyası gönderilen istek: {model.yeniden_gonderilen}")
    if ayar["model"]["fiyat_girdi"] or ayar["model"]["fiyat_onbellek"] or ayar["model"]["fiyat_cikti"]:
        ozet.append(f"Maliyet: {model.maliyet():.4f} USD")
        if deneme > 0 and satir:
            ozet.append(f"Tüm dosyalar için tahmini maliyet: {model.maliyet() * tum_satir / satir:.2f} USD")
    gunluk.ozet("\n".join(ozet) + "\n")
    print("\n" + "\n".join(ozet))
    print(f"Loglar: {gunluk.klasor}")


if __name__ == "__main__":
    main()
