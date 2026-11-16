"""Altyazı listesi: satırlar ve sıraları.

Sıra: seçili altyazı → senkron kaymış satırı (bu videoya altyazı yüklendiyse) → hedef dildeki altyazılar → Jimaku
(ayar açıksa) → öteki çevrilecek altyazılar → yeniden çevir (seçili bir AI çevirisiyse).

Hedef dil grubu: hazır AI çevirileri her zaman başta; sonra insan altyazıları senkron puanına göre (eşitlikte önceki
bölümde seçilen grup/çevirmen), sonra işitme engelli (SDH), sonra kurgusu tutmayanlar. Kaymış diye işaretlenenler AI
ya da insan fark etmeden en altta, işaretlendikleri sırayla. Çevrilecekler aynı mantıkla; eşitlikte kaynak dil sırası.
Site başına sonuç sayısı hedef dil ve çevrilecekler için ayrı sayılır; birden çok sitedeki aynı altyazı tek satırdır.
Aynı sitede adı, yükleyeni ve SDH durumu aynı altyazılar çoğu zaman aynı dosyanın yeniden yüklenmesidir: yalnız biri
görünür, ötekiler site sınırını harcamaz. Adı aynı ama yükleyeni farklı satırlar ayrı çeviri olabilir: ikisi de görünür,
yanlarında yükleyenin adı yazar.
"""
import re

from . import metin, puan, surum

SITE_ADLARI = {"os": "OpenSubtitles", "subdl": "SubDL", "subsource": "SubSource", "jimaku": "Jimaku"}
SITE_SIRASI = ("os", "subdl", "subsource", "jimaku")


class Satir:
    __slots__ = ("eylem", "yazi", "dil", "uyumlu", "sdh", "anahtar")

    def __init__(self, eylem, yazi, dil="", uyumlu=False, sdh=False, anahtar=""):
        self.eylem, self.yazi, self.dil, self.uyumlu, self.sdh, self.anahtar = eylem, yazi, dil, uyumlu, sdh, anahtar

    def __repr__(self):
        return f"Satir({self.eylem}, {self.yazi!r})"


def kaynak_anahtari(kaynak):
    """Kayıtlı adayın (çevirinin kaynağı) "site:kimlik" anahtarı; eski kayıtlarda anahtar alanı yoktu."""
    k = kaynak or {}
    if k.get("anahtar"):
        return k["anahtar"]
    return f"{k['site']}:{k['kimlik']}" if k.get("site") and k.get("kimlik") is not None else ""


def _ad_anahtari(ad):
    g = surum.govde(ad).casefold()
    g = re.sub(r"[\s._\-\[\]()]+", ".", g).strip(".")
    return g if len(g) >= 8 else ""


def birlestir(adaylar):
    """Aynı dil, aynı sürüm adı ve aynı SDH durumuyla başka sitede de çıkan altyazı tek aday olur; ötekiler yedeği
    olur (biri indirilemezse ya da sitenin hakkı dolmuşsa öbüründen indirilir). Aynı sitedeki iki altyazı burada
    birleşmez (çevirmenler dosyaya çoğu zaman videonun sürüm adını verir, ikisi ayrı çeviri olabilir); listede
    ayırt edilemeyenleri kopyalari_topla gizler."""
    sonuc, gruplar = [], {}
    for a in sorted(adaylar, key=lambda a: SITE_SIRASI.index(a.site) if a.site in SITE_SIRASI else 9):
        k = _ad_anahtari(a.ad)
        if not k:
            sonuc.append(a)
            continue
        anahtar = (a.dil, k, bool(a.sdh))
        ana = next((g for g in gruplar.get(anahtar, ())
                    if g.site != a.site and all(y.site != a.site for y in g.yedekler)), None)
        if ana is None:
            gruplar.setdefault(anahtar, []).append(a)
            sonuc.append(a)
            continue
        if a.karma_tuttu and not ana.karma_tuttu:
            ana.karma_tuttu = True
        ana.yedekler.append(a)
    return sonuc


def puanla(adaylar, video_adi):
    for a in adaylar:
        a.puan, a.kurgu_tutmuyor = puan.puanla(video_adi, a.adlar, a.karma_tuttu)
    return adaylar


def hak_dolanlari_ayikla(adaylar, hak_dolan):
    """Hakkı dolan siteden yalnız başka siteden indirilemeyen satırlar gizlenir; yedeği olan satır yedeğe geçer
    (senkron bilgisi korunur)."""
    sonuc = []
    for a in adaylar:
        if a.site not in hak_dolan:
            sonuc.append(a)
            continue
        yedek = next((y for y in a.yedekler if y.site not in hak_dolan), None)
        if yedek is None:
            continue
        yedek.puan, yedek.kurgu_tutmuyor = a.puan, a.kurgu_tutmuyor
        yedek.karma_tuttu, yedek.sdh = a.karma_tuttu, a.sdh
        yedek.yedekler = [y for y in a.yedekler if y is not yedek]
        sonuc.append(yedek)
    return sonuc


_BOLUM_ISARETI = re.compile(r"(?i)\bs\d{1,2}[ ._-]?e\d{1,3}\b|\b\d{1,2}x\d{1,3}\b|\b(?:ep?|bölüm|episode)[ ._-]?\d{1,3}\b| - \d{1,3}\b")


def aile_imzasi(a):
    """Bir altyazının "ailesi": aynı kişinin aynı sürüm için bölüm bölüm yüklediği dosyaları birbirine bağlayan
    özellikler. Bölüm numarası ve bölüm adı girmez. Kullanıcının son seçtiği altyazının ailesi kaydedilir; sonraki
    bölümde o ailenin devamı öne alınır."""
    sr = surum.ayristir(a.ad)
    on = _BOLUM_ISARETI.split(surum.govde(a.ad), 1)[0]
    return {"site": a.site, "yukleyen": str(a.yukleyen or "").casefold(), "sdh": bool(a.sdh),
            "grup": str(sr.grup or "").casefold(), "kaynak": str(sr.kaynak or ""), "cozunurluk": str(sr.cozunurluk or ""),
            "on_ek": re.sub(r"[\W_]+", "", on).casefold()}


def _tercih_disi(a, tercih):
    """0: son seçilen altyazının ailesinden (aynı site, yükleyen, sürüm kalıbı); 1: yalnız grubu ya da yükleyeni
    aynı; 2: tercihle ilgisi yok."""
    if not tercih:
        return 2
    aile = tercih.get("aile")
    if aile and aile == aile_imzasi(a):
        return 0
    grup = surum.ayristir(a.ad).grup
    if tercih.get("grup") and grup and grup.lower() == tercih["grup"].lower():
        return 1
    if tercih.get("yukleyen") and a.yukleyen and a.yukleyen.lower() == tercih["yukleyen"].lower():
        return 1
    return 2


def _kategori(a, kaymislar, sdh_alta):
    if a.anahtar in kaymislar:
        return 3
    if a.kurgu_tutmuyor:
        return 2
    if a.sdh and sdh_alta:
        return 1
    return 0


def kopyalari_topla(adaylar, kaymislar=None):
    """Sıralı listede aynı sitede aynı dil, sürüm adı, yükleyen ve SDH durumuyla birden çok altyazı (aynı dosyanın
    yeniden yüklenmesi): yalnız ilki (sırada en iyisi) kalır, ötekiler listeye girmez ve site sınırını boşa harcamaz.
    Gizlenenin başka sitedeki kopyaları kalanın yedeği olur (indirilemezse onlar denenir). Kaymış işaretliler
    işaretsizleri gizlemez (ayrı sayılır)."""
    kaymislar = kaymislar or {}
    gorulen, sonuc = {}, []
    for a in adaylar:
        k = _ad_anahtari(a.ad)
        anahtar = (a.site, a.dil, k, bool(a.sdh), str(a.yukleyen or "").casefold(), a.anahtar in kaymislar)
        ilk = gorulen.get(anahtar) if k else None
        if ilk is None:
            gorulen[anahtar] = a
            sonuc.append(a)
            continue
        for y in a.yedekler:
            if y.site != ilk.site and all(z.site != y.site for z in ilk.yedekler):
                ilk.yedekler.append(y)
    return sonuc


def _ayni_gorunenler(adaylar):
    """Listede aynı görünecek satırlar (aynı site, dil, sürüm adı ve SDH) yükleyenleri farklıysa yükleyenin adıyla
    ayırt edilir."""
    yukleyenler = {}
    for a in adaylar:
        yukleyenler.setdefault(_gorunum(a), set()).add(str(a.yukleyen or "").casefold())
    return {g for g, y in yukleyenler.items() if len(y) > 1}


def _gorunum(a):
    return a.site, a.dil, _ad_anahtari(a.ad) or a.ad.casefold(), bool(a.sdh)


def _site_sinirla(adaylar, sinir, kaymislar=None):
    """Sıralı listeden site başına en çok 'sinir' satır. Kaymış işaretliler sayılmaz (en altta kalırlar): bir sitenin
    ilk sonuçları kaymış çıkınca sıradakiler listeye girer ve Kaymış satırı onlara ulaşabilir."""
    sayac, sonuc = {}, []
    for a in adaylar:
        if kaymislar and a.anahtar in kaymislar:
            sonuc.append(a)
            continue
        sayac[a.site] = sayac.get(a.site, 0) + 1
        if sayac[a.site] <= sinir:
            sonuc.append(a)
    return sonuc


def hazir_puanla(hazirlar, video):
    """Hazır AI çevirisinin senkronu kaynağınınkidir. Karma eşleşmesi yalnız aynı video dosyası için sayılır (aynı
    içeriğin başka sürümünde kaynağın karması tutmaz)."""
    for h in hazirlar:
        k = h.get("kaynak") or {}
        karma = bool(k.get("karma_tuttu")) and bool(getattr(video, "karma", None)) and h.get("video_karma") == video.karma
        h["puan"], h["kurgu"] = puan.puanla(video.dosya_adi or None, k.get("adlar") or [], karma)
    return hazirlar


def hedef_sirasi(hazirlar, insanlar, kaymislar, tercih, sdh_alta):
    """Hedef dil grubu (AI + insan) sırası: [(tür, anahtar, nesne)]. Kaymış satırı da bu sırada dolaşır."""
    ai = [("ai", "ai:" + h["kimlik"], h) for h in hazirlar]
    insan = [("insan", a.anahtar, a) for a in insanlar]

    def anahtar(t):
        tur, k, nesne = t
        if k in kaymislar:
            return (4, kaymislar[k], 0, 0, 0)
        if tur == "ai":
            return (0, 0, -nesne.get("puan", 0), -nesne.get("zaman", 0), 0)
        return (1 + _kategori(nesne, {}, sdh_alta), 0, -nesne.puan, _tercih_disi(nesne, tercih), -nesne.indirme_sayisi)
    return sorted(ai + insan, key=anahtar)


def cevrilecek_sirasi(adaylar, kaymislar, tercih, sdh_alta, kaynak_diller):
    def anahtar(a):
        kat = _kategori(a, kaymislar, sdh_alta)
        if kat == 3:
            return (3, kaymislar[a.anahtar], 0, 0, 0)
        dil_sirasi = kaynak_diller.index(a.dil) if a.dil in kaynak_diller else len(kaynak_diller)
        t = _tercih_disi(a, tercih)
        if t == 0 and kat < 2:
            # Son seçilen altyazının devamı her zaman ilk sıradadır: "SDH alta" ve puan bunu ezmez (kullanıcı o
            # altyazıyı kendi seçti; sonraki bölümde başka aileden biri seçilince senkron tutmuyordu).
            return (-1, 0, -a.puan, 0, dil_sirasi)
        return (kat, 0, -a.puan, t, dil_sirasi)
    return sorted(adaylar, key=anahtar)


def olustur(video, adaylar, hazirlar, secili, suren, kaymislar, tercih, ayar):
    """Satır listesi ve Kaymış satırının dolaşacağı hedef dil sırası.

    ayar: hedef_dil, kaynak_diller, jimaku_oncelik, sonuc_hedef, sonuc_cevrilecek, sdh_alta, oto_ai, kodi_otomatik,
    hak_dolan (site -> yenilenme), dil_adi (kod -> yerel ad)."""
    hedef = ayar["hedef_dil"]
    adaylar = puanla(birlestir(adaylar), video.dosya_adi or None)
    adaylar = hak_dolanlari_ayikla(adaylar, ayar.get("hak_dolan") or {})
    secili_anahtar = (secili or {}).get("anahtar", "")
    # Çevrilmiş kaynak başka sitedeki kopyasıyla (yedek) da tanınır: sonraki aramada asıl site yanıt vermese de
    # aynı altyazı "Çevir" diye yeniden çıkmaz
    cevrilmis_kaynaklar = set()
    for h in hazirlar:
        k = h.get("kaynak") or {}
        cevrilmis_kaynaklar.update(kaynak_anahtari(x) for x in [k] + list(k.get("yedekler") or []))
    cevrilmis_kaynaklar.discard("")
    hazir_puanla(hazirlar, video)

    insanlar = [a for a in adaylar if a.dil == hedef]
    insanlar = kopyalari_topla([t[2] for t in hedef_sirasi([], insanlar, kaymislar, tercih, ayar["sdh_alta"])],
                               kaymislar)
    insanlar = _site_sinirla(insanlar, ayar["sonuc_hedef"], kaymislar)
    sira = hedef_sirasi(hazirlar, insanlar, kaymislar, tercih, ayar["sdh_alta"])

    kaynaklar = [d for d in ayar["kaynak_diller"] if d != hedef]
    cevrilecek = [a for a in adaylar if a.dil in kaynaklar
                  and not any(x.anahtar in cevrilmis_kaynaklar for x in [a] + a.yedekler)]
    jimaku = [a for a in cevrilecek if a.site == "jimaku"] if ayar.get("jimaku_oncelik") else []
    oteki = [a for a in cevrilecek if a not in jimaku]
    jimaku = _site_sinirla(kopyalari_topla(cevrilecek_sirasi(jimaku, kaymislar, tercih, ayar["sdh_alta"], kaynaklar),
                                           kaymislar), ayar["sonuc_cevrilecek"], kaymislar)
    oteki = _site_sinirla(kopyalari_topla(cevrilecek_sirasi(oteki, kaymislar, tercih, ayar["sdh_alta"], kaynaklar),
                                          kaymislar), ayar["sonuc_cevrilecek"], kaymislar)
    gorunen = [n for t, k, n in sira if t == "insan" and k != secili_anahtar]
    gorunen += [a for a in jimaku + oteki if a.anahtar != secili_anahtar]
    ayni = _ayni_gorunenler(gorunen)

    def yukleyen(a):
        return str(a.yukleyen or "") if _gorunum(a) in ayni else ""

    satirlar = []
    dil_adi = ayar.get("dil_adi") or (lambda k: k)
    if secili:
        if suren and suren.get("ceviri_anahtari") == secili_anahtar:
            yazi = f"• {metin.al(metin.ON_AI)} · {metin.al(metin.CEVRILIYOR, yuzde=int(suren.get('yuzde', 0)))}"
            if suren.get("ad"):
                yazi += " · " + suren["ad"]
            satirlar.append(Satir("suren", yazi, hedef, anahtar=secili_anahtar))
        else:
            satirlar.append(Satir("secili", "• " + _yazi(secili.get("tur"), secili.get("ad", ""), secili.get("site")),
                                  secili.get("dil") or hedef, uyumlu=bool(secili.get("uyumlu")),
                                  sdh=bool(secili.get("sdh")), anahtar=secili_anahtar))
        satirlar.append(Satir("kaymis", metin.al(metin.KAYMIS_SATIRI), hedef))
    for tur, anahtar, nesne in sira:
        if anahtar == secili_anahtar:
            continue
        if tur == "ai":
            k = nesne.get("kaynak") or {}
            satirlar.append(Satir("ai", _yazi("ai", (k.get("adlar") or [""])[0], k.get("site")), hedef,
                                  uyumlu=puan.uyumlu_mu(nesne.get("puan", 0), nesne.get("kurgu")), anahtar=anahtar))
        else:
            yazi = _yazi("insan", nesne.ad, nesne.site, yukleyen(nesne), nesne.paket, makine_yapimi(nesne))
            satirlar.append(Satir("indir", yazi, nesne.dil, uyumlu=puan.uyumlu_mu(nesne.puan, nesne.kurgu_tutmuyor),
                                  sdh=nesne.sdh, anahtar=anahtar))
    for a in jimaku + oteki:
        if a.anahtar == secili_anahtar:
            continue
        satirlar.append(Satir("cevir", _yazi("cevir", a.ad, a.site, yukleyen(a), a.paket, makine_yapimi(a)), a.dil,
                              uyumlu=puan.uyumlu_mu(a.puan, a.kurgu_tutmuyor), sdh=a.sdh, anahtar=a.anahtar))
    if secili and secili.get("tur") == "ai" and not (suren and suren.get("ceviri_anahtari") == secili_anahtar):
        satirlar.append(Satir("yeniden", metin.al(metin.YENIDEN_CEVIR), hedef))
    # Güvenlik satırı: Kodi'nin otomatik indirmesi paralı çeviri başlatamasın
    if ayar.get("kodi_otomatik") and not secili and not sira and (jimaku or oteki) and ayar.get("oto_ai") != 2:
        satirlar.insert(0, Satir("guvenlik", metin.al(metin.GUVENLIK_SATIRI, dil=dil_adi(hedef)), hedef))
    tum = {a.anahtar: a for a in adaylar}
    return satirlar, [(t, k) for t, k, _ in sira], tum


_MAKINE = re.compile(r"(?i)(?<![a-z])(os-auto|subdl ai|whisper|auto[- ]?generated|ai[- ]?generated|machine[- ]?generated)(?![a-z])")


def makine_yapimi(a):
    """Altyazıyı insan değil makine yazmış (konuşmadan yazıya döken hizmet): sitenin kendi otomatik yükleyicisi ya da
    adında bunu söyleyen dosya. Bu kaynakta cümleler ikişer saniyelik parçalara bölünür, süreler çok kısadır; çeviri
    de o ölçüde zayıf çıkar. Listede işaretlenir ki bilerek seçilsin."""
    return bool(_MAKINE.search(f"{a.yukleyen or ''} | {a.ad}"))


def _yazi(tur, ad, site, yukleyen="", paket=False, makine=False):
    """Satır yazısı. Sezon paketi ayrıca yazılır: paketin adı çoğu kez yalnız ilk dosyayı yazar ("..._01_...")."""
    site_adi = SITE_ADLARI.get(site or "", site or "")
    if paket:
        ad = f"{metin.al(metin.ON_PAKET)} · {ad}" if ad else metin.al(metin.ON_PAKET)
    if yukleyen:
        site_adi = f"{site_adi} ({yukleyen})" if site_adi else yukleyen
    if makine:
        site_adi = " · ".join(p for p in (site_adi, metin.al(metin.ON_MAKINE)) if p)
    if tur == "ai":
        return f"{metin.al(metin.ON_AI)} · {ad}" if ad else metin.al(metin.ON_AI)
    if tur == "cevir":
        return " · ".join(p for p in (metin.al(metin.ON_CEVIR), ad, site_adi) if p)
    return " · ".join(p for p in (ad, site_adi) if p)
