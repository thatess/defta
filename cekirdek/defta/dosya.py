"""Tek bir altyazı dosyası: okuma (her biçim), çevirmene verme, süre ve satır düzeniyle .srt olarak yazma."""
import hashlib
import json
import os

from . import bicim, srt, temizlik
from .ayar import hedef_kodu
from .cevirmen import Cevirmen
from .cumle import BIRLESTI
from .denetim import ARDISIK_UZAKLIK, ardisik_mi
from .kaynak import TIRELI, sarki_bloklari, sarki_mi


def _duz(metin):
    """Karşılaştırma için: etiketsiz, küçük harfli, tek boşluklu metin."""
    return " ".join(temizlik.ETIKET.sub("", metin).lower().split())


def tekrarlari_birlestir(satirlar):
    """Art arda gelen, metni aynı ve zamanı bitişik (arada boşluk yok) altyazıları tek altyazı yapar: aynı sözün
    sahne kesmesiyle ikiye bölünmüş gösterimidir (Spy M13'te 9). Arada birkaç karelik boşluk varsa söz yinelenmiştir
    ("And... pull!" / "And... pull!" tayfanın iki ayrı çekişi): ikisi de kalır."""
    sonuc = []
    for s in satirlar:
        if sonuc and s.metin and s.metin == sonuc[-1].metin and s.bas - sonuc[-1].bit <= 20:
            sonuc[-1].bit = max(sonuc[-1].bit, s.bit)
            continue
        sonuc.append(s)
    for n, s in enumerate(sonuc, 1):
        s.no = n
    return sonuc


def dosya_oku(kaynak_yol, ayar):
    """(ham içerik, hazırlanmış satırlar, dosyadaki toplam satır sayısı) döndürür. Biçimi (srt, ass, vtt, sub)
    bicim.ayristir çözer; okunamayan dosyada BicimHatasi yükselir."""
    modlar = ayar["modlar"]
    icerik = srt.metin_oku(kaynak_yol)
    satirlar = bicim.ayristir(icerik, os.path.splitext(kaynak_yol)[1], ayar)
    for s in satirlar:
        s.metin, s.ustte = temizlik.kaynak_hazirla(s.metin)
        s.ref = temizlik.ETIKET.sub("", temizlik.kaynak_hazirla(s.ref)[0]).replace("\n", " ") if s.ref else ""
    # Künye/reklam altyazıları ve (silinmesi istenmişse) ekran yazıları: sözlük adımına da çeviriye de gitmez.
    # Konuşmada da geçebilen künye kalıpları yalnızca ilk ve son 15 altyazıda ya da ilk ve son iki dakikada aranır.
    son = max((s.bit for s in satirlar), default=0)
    kenar = {i for i, s in enumerate(satirlar) if i < 15 or i >= len(satirlar) - 15 or s.bas < 120000 or s.bas > son - 120000}
    satirlar = [s for i, s in enumerate(satirlar)
                if not (modlar["reklam"] == "sil" and temizlik.reklam_mi(s.metin, i in kenar))
                and not (modlar["ekran_yazisi"] == "sil" and s.ekran)]
    for n, s in enumerate(satirlar, 1):
        s.no = n
    if ayar["girdi"]["tekrarlari_birlestir"]:
        satirlar = tekrarlari_birlestir(satirlar)
    # Yalnız ilk ve son dizesi ♪'li şarkının iç dizeleri de şarkıdır: şarkı ayarına uyar, cümle birleştirmeye girmez.
    # İşareti script koyar (bütün denetimler ♪'ye bakar); kaynakta olmadığı için çıktıdan yine kaldırılır.
    for i in sarki_bloklari(satirlar):
        satirlar[i].metin, satirlar[i].sarki_eki = f"♪ {satirlar[i].metin} ♪", True
    toplam = len(satirlar)
    if ayar["kontrol"]["deneme_satir"] > 0:
        satirlar = satirlar[:ayar["kontrol"]["deneme_satir"]]
    return icerik, satirlar, toplam


def dosya_cevir(kaynak_yol, hedef_yol, goreli, ayar, model, sistem, gunluk,
                onbellek_klasoru=".onbellek", ilerleme=None, sozluk=None, okunan=None, cevrilecek=None):
    """Tek bir altyazı dosyasını çevirir ve yazar. İstatistik sözlüğü döndürür. okunan: dosya_oku'nun önceden
    okunmuş sonucu (dosya bir daha okunmaz). cevrilecek: verilirse yalnız bu sıralardaki satırlar çevrilip yazılır;
    ötekiler modele yalnız bağlam olarak gider (Kodi'deki hızlı çeviri: aralığın öncesi ve sonrası)."""
    try:
        icerik, satirlar, toplam_satir = okunan or dosya_oku(kaynak_yol, ayar)
    except bicim.BicimHatasi as e:
        gunluk.hata(goreli, f"dosya okunamadı, atlandı: {e}")
        return {"satir": 0, "yazilan": 0, "toplam_satir": 0}
    if not satirlar:
        gunluk.hata(goreli, "altyazı satırı bulunamadı, dosya atlandı")
        return {"satir": 0, "yazilan": 0, "toplam_satir": 0}

    # Kayıt yalnızca aynı dosya, aynı istem (sözlük dahil), isteklere giden aynı sözlük bilgisi (sahneler, kurgu
    # dili, hitap tablosu) ve aynı ayarlarla sürdürülür: biri değiştiyse eski parçalar yenileriyle karışmasın diye
    # baştan başlanır.
    ayarlar = {b: d for b, d in ayar.items() if b != "klasorler"}
    ayarlar["model"] = {k: v for k, v in ayar["model"].items() if k not in (
        "es_zamanli_istek", "zaman_asimi", "fiyat_girdi", "fiyat_onbellek", "fiyat_cikti", "anahtar_degiskeni")}
    baglam = ({} if sozluk is None else
              {"sahne": sozluk.sahneler.get(goreli) or None, "kurgu": sozluk.kurgu.get(goreli) or None,
               "hitap": sozluk.hitap or None})              # boş ile yok aynıdır: kayıt boş alanı yazmaz
    if cevrilecek is not None:
        cevrilecek = set(cevrilecek)
    parca_bilgisi = [ayarlar, baglam] + ([sorted(cevrilecek)] if cevrilecek is not None else [])
    parmak_izi = hashlib.sha1("\x00".join(
        [goreli, icerik, sistem, json.dumps(parca_bilgisi, ensure_ascii=False, sort_keys=True, default=str)]
    ).encode("utf-8")).hexdigest()
    onbellek = os.path.join(onbellek_klasoru, parmak_izi + ".json")
    basvurulu = sum(1 for s in satirlar if s.ref)
    if basvurulu:
        gunluk.uyari(goreli, f"çift dilli altyazı: {len(satirlar)} satırın {basvurulu} tanesi ikinci dildeki "
                             f"karşılığı başvuru alınarak çevrildi; ikinci dilin o satırları çıktıya yazılmadı")
    cevirmen = Cevirmen(ayar, model, sistem, gunluk, sozluk)
    ceviriler = cevirmen.cevir(satirlar, goreli, onbellek, ilerleme, cevrilecek)

    modlar, k = ayar["modlar"], ayar["cikti"]
    latin = ayar["ceviri"]["cikti_alfabesi"] == "latin"
    ek_uyumu = hedef_kodu(ayar) == "tr" and modlar["anime_ekleri"] != "ceviri"
    cikti, ciftler, yazilanlar = [], [], []      # yazilanlar: [son kaynak satırının indeksi, Satir, metin]
    for i, s in enumerate(satirlar):
        if cevrilecek is not None and i not in cevrilecek:
            continue                           # yalnız bağlamdı: çıktıya yazılmaz
        if ceviriler.get(i) == BIRLESTI:       # süresi önceki altyazıya eklendi
            ciftler.append((s.no, f"{srt._zaman(s.bas)} --> {srt._zaman(s.bit)}", s.metin.replace("\n", " | "),
                            "(önceki altyazıyla birleşti)"))
            continue
        bit, sonraki = s.bit, i + 1
        while ceviriler.get(sonraki) == BIRLESTI:
            bit, sonraki = satirlar[sonraki].bit, sonraki + 1
        metin = temizlik.son_temizlik(ceviriler.get(i, s.metin), modlar, latin)
        if ek_uyumu:
            metin = temizlik.tr_ek_uyumu(metin)
        # İki kişinin konuştuğu altyazı: tireler silinmiş olsa da her konuşma kendi satırında kalır;
        # satır kaydırma ikisini tek akan metne karıştırmamalı.
        iki_kisi = sum(1 for r in s.metin.split("\n") if TIRELI.match(r)) >= 2
        if not (iki_kisi and metin.count("\n") == 1):
            metin = temizlik.sar(metin, k["satir_karakter"])        # şarkı dizesine dokunmaz (♪)
        if s.sarki_eki:
            metin = temizlik.isaretsiz(metin)
        zaman = f"{srt._zaman(s.bas)} --> {srt._zaman(s.bit)}"
        kaynak_metni = temizlik.isaretsiz(s.metin) if s.sarki_eki else s.metin
        ciftler.append((s.no, zaman, kaynak_metni.replace("\n", " | "), metin.replace("\n", " | ") or "(silindi)"))
        if not metin:
            continue
        # Kaynağın art arda iki altyazıya böldüğü aynı replik (süreler bitişik, metin ve çeviri aynı) tek altyazı
        # olur: ilkinin süresi uzar. Ekranda zaten kesintisiz duruyordu; ayrı kalınca ikinci parça "okunamayacak
        # kadar kısa" görünüyordu.
        ayni = next((y for y in reversed(yazilanlar[-ARDISIK_UZAKLIK:])
                     if y[2] == metin and ardisik_mi(satirlar[y[0]], s) and _duz(satirlar[y[0]].metin) == _duz(s.metin)), None)
        if ayni:
            ayni[0], ayni[1].bit = i, max(ayni[1].bit, bit)
            ciftler[-1] = ciftler[-1][:3] + ("(önceki altyazıyla birleşti)",)
            say = getattr(model, "say", lambda *_: None)
            say("art arda yinelenip tek altyazı olan replik")
            continue
        kalinti = temizlik.kalinti_tara(metin)
        if kalinti:
            gunluk.hata(goreli, f"satır {s.no}: kalıntı işaret olabilir ({kalinti}): {metin!r}")
        if latin and temizlik.latin_disi(metin):
            gunluk.hata(goreli, f"satır {s.no}: Latin dışı karakter kaldı ({temizlik.latin_disi(metin)}): {metin!r}")
        harf = len(temizlik.ETIKET.sub("", metin).replace("\n", ""))
        sarki = s.sarki_eki or sarki_mi(metin)        # şarkı dizesi okuma hızına göre uzatılmaz, uyarı da almaz
        if k["sure_uzat_azami"] > 0 and not sarki and harf / (max(bit - s.bas, 1) / 1000) > k["saniye_basina_karakter"]:
            # Okumaya yetmeyen altyazı, sonraki altyazı başlayana kadarki sessizliğe doğru uzatılır.
            # Başlangıç zamanı ve sonraki altyazı hiç değişmez; yalnızca ekranda kalma süresi artar.
            sinir = (satirlar[sonraki].bas - 80) if sonraki < len(satirlar) else bit + 10 ** 6
            istenen = s.bas + int(1000 * harf / k["saniye_basina_karakter"]) + 1
            yeni = min(istenen, sinir, bit + int(1000 * k["sure_uzat_azami"]))
            if yeni > bit:
                say = getattr(model, "say", lambda *_: None)
                say("süresi uzatılan altyazı")
                if yeni >= istenen:
                    say("süre uzatmayla okuma hızı düzelen altyazı")
                bit = yeni
        sure = max(bit - s.bas, 1) / 1000
        hiz = harf / sure
        if hiz > k["saniye_basina_karakter"] and not sarki:
            gunluk.uyari(goreli, f"satır {s.no}: okuma hızı yüksek ({hiz:.0f} karakter/sn)")
        yalin_metin = metin
        if s.ustte and modlar["ustte_gosterme"] == "korunsun":
            metin = "{\\an8}" + metin
        cikti.append(srt.Satir(len(cikti) + 1, s.bas, bit, metin))
        yazilanlar.append([i, cikti[-1], yalin_metin])

    os.makedirs(os.path.dirname(hedef_yol) or ".", exist_ok=True)
    with open(hedef_yol + ".yeni", "w", encoding="utf-8", newline="\n") as f:    # yarım yazılmış .srt kalmasın
        f.write(srt.srt_yaz(cikti))
    os.replace(hedef_yol + ".yeni", hedef_yol)
    gunluk.karsilastirma(goreli, ciftler)
    if os.path.exists(onbellek):
        os.remove(onbellek)
    return {"satir": len(satirlar) if cevrilecek is None else len(cevrilecek), "yazilan": len(cikti),
            "toplam_satir": toplam_satir}
