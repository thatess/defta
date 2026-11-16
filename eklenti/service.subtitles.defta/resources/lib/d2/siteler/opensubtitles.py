"""OpenSubtitles REST API v1 (api.opensubtitles.com/api/v1).

Api-Key ve User-Agent her istekte; kullanıcı adı/parola verilmişse oturum jetonu (24 saat) Authorization başlığında
gider ve indirme hakkı 5'ten 20'ye çıkar. Arama parametreleri alfabetik ve küçük harfle gönderilir (yönlendirme
olmasın). İndirme isteği (POST /download) hakkı düşürdüğü için kendiliğinden tekrarlanmaz; gelen bağlantının indirilmesi
tekrarlanabilir. AI ve makine çevirisi altyazılar istenmez.
"""
import copy
import re
import time
from collections import Counter

from .. import ag, gizli, surum
from . import (EK_ARAMA_SINIRI, EK_ZAMAN_ASIMI, Aday, SiteHatasi, dil_kodu, hedef_dil_eksik, sade_ad,
               tam_altyazi_degil, yapim)

ADRES = "https://api.opensubtitles.com/api/v1/"
JETON_OMRU = 23 * 3600


_API_ADRESI = re.compile(r"https://(?:[a-z0-9-]+\.)+opensubtitles\.com/api/v1/")


def _guvenli_adres(adres):
    """Girişin bildirdiği sunucunun (VIP: vip-api.opensubtitles.com) API adresi; opensubtitles.com'un bir alt alanı
    değilse varsayılan adres. Api-Key ve oturum jetonu her istekte bu adrese gider."""
    return adres if isinstance(adres, str) and _API_ADRESI.fullmatch(adres) else ADRES


def _os_dili(kod):
    """OpenSubtitles'ın dil kodu; Çincede basitleştirilmiş ve geleneksel birlikte (ikisi de eklentinin "zh"si)."""
    return {"pt-BR": "pt-br", "pt": "pt-pt", "zh": "zh-cn,zh-tw"}.get(kod, kod.lower())


_kimlik = yapim.kimlik_no


def _tam(deger):
    try:
        return int(deger)
    except (TypeError, ValueError, OverflowError):
        return None


def _fps(deger):
    try:
        f = float(deger) if deger else None
    except (TypeError, ValueError, OverflowError):
        return None
    return f if f and 1 < f < 1000 else None


def _adla_aramalar(video):
    """Kimlik yoksa önce dosya adı, sonra yapım adı, adda "US/UK" ya da yıl eki varsa eksiz hâli (madde 9);
    kimlik varsa yalnız ad."""
    from ..video import ad_eksiz
    sira = []
    if not (video.imdb or video.tmdb or video.tvdb or yapim.elle(video)) and video.dosya_adi:
        sira.append(video.dosya_adi)                # elle aramada oynatılan dosyanın adı başka yapımın olabilir
    if video.ad:
        sira.append(video.ad)
        if ad_eksiz(video.ad) != video.ad:
            sira.append(ad_eksiz(video.ad))
    return [a for n, a in enumerate(sira) if a and a not in sira[:n]]


def _kimlikli(video, bulunan, dizi):
    """Yapımı öğrenilen kimliksiz videonun kopyası, kimlikleri yapımınki (yalnız kimlikle gelen altyazıları denetlemek
    için). Siteler aynı video nesnesini aynı anda kullandığı için asıl nesne değişmez."""
    v = copy.copy(video)
    v.imdb = "tt" + bulunan["imdb"].zfill(7) if bulunan.get("imdb") else ""
    v.tmdb = bulunan.get("tmdb") or ""
    v.bolum_imdb = ""
    v.dizi_kimlik_kesin = dizi
    return v


def _oy(oz, dizi):
    """Sonucun yapımı: (imdb, tmdb, yapım kaydı, ad, yıl); bölüm/film türü aranana uymuyorsa None."""
    tur = str(oz.get("feature_type") or "").lower()
    if dizi:
        if tur != "episode":
            return None
        return (_kimlik(oz.get("parent_imdb_id")), _kimlik(oz.get("parent_tmdb_id")), _kimlik(oz.get("parent_feature_id")),
                str(oz.get("parent_title") or ""), None)
    if tur != "movie":
        return None
    return (_kimlik(oz.get("imdb_id")), _kimlik(oz.get("tmdb_id")), _kimlik(oz.get("feature_id")),
            str(oz.get("title") or ""), _tam(oz.get("year")))


def _ortak_yapim(ogeler, video, dizi):
    """Kimliksiz videoyu adla (ya da dosya adıyla) arayınca gelen sonuçlardan yapımın kimliği. Adla aramak yalnız sürüm
    adında o ad geçen altyazıları getiriyor: animede romaji adla ("Shingeki no Kyojin") aranınca "Attack on Titan" diye
    yüklenen Türkçeler gelmiyordu. Karması tutmayan sonuçların en az ikisi aynı yapımınsa (kimliklerinden biri ortak
    kayıtlar bir sayılır) ve o yapım ötekilerden çoksa yapım odur. Filmde yılı ±1 tutmayan oy vermez. Adı videonunkini
    tutan birden çok yapım varsa ("Charmed" 1998 ve 2018) karar verilmez. -> {"imdb", "tmdb", "id", "ad"} ya da None."""
    v_yil = None if dizi else (video.yil or video.surum.yil)
    gruplar = []
    for a, oz in ogeler:
        if a.karma_tuttu or not oz:                     # karma kaydı yanlış olabiliyor: oy vermez
            continue
        oy = _oy(oz, dizi)
        if not oy or not any(oy[:3]):
            continue
        imdb, tmdb, kayit, ad, yil = oy
        try:
            if v_yil and yil and abs(yil - int(v_yil)) > 1:
                continue
        except (TypeError, ValueError):
            pass
        g = next((g for g in gruplar if (imdb and g["imdb"] == imdb) or (tmdb and g["tmdb"] == tmdb) or
                  (kayit and g["id"] == kayit)), None)
        if g is None:
            g = {"imdb": "", "tmdb": "", "id": "", "ad": ad, "yil": yil, "oy": 0}
            gruplar.append(g)
        g["oy"] += 1
        g["imdb"], g["tmdb"], g["id"] = g["imdb"] or imdb, g["tmdb"] or tmdb, g["id"] or kayit
    gruplar.sort(key=lambda g: -g["oy"])
    if not gruplar or gruplar[0]["oy"] < 2 or (len(gruplar) > 1 and gruplar[1]["oy"] >= gruplar[0]["oy"]):
        return None
    v_adlar = yapim.video_adlari(video)
    if sum(1 for g in gruplar if g["ad"] and yapim.ad_tutar(v_adlar, {sade_ad(g["ad"])}, kapsama=not dizi)) > 1:
        return None
    g = gruplar[0]
    return {"imdb": g["imdb"], "tmdb": g["tmdb"], "id": g["id"],
            "ad": f"{g['ad']} ({g['yil']})" if g["ad"] and g["yil"] else g["ad"]}


def _ekle(ogrenilenler, sonuc):
    """Öğrenilen yapımın kimliğiyle gelenler önde; adla gelenlerden aynı altyazı tekrarlanmaz."""
    gorulen = {a.kimlik for a in ogrenilenler}
    return ogrenilenler + [a for a in sonuc if a.kimlik not in gorulen]


# ------------------------------------------------------------------------------------- yapım denetimi
# OpenSubtitles'ın karma kaydı yanlış olabiliyor: Interstellar dosyasının karmasıyla başka filmler ve bir dizi bölümü
# de "karma tuttu" diye geldi. Altyazının yapımı (feature_details) videonunkiyle karşılaştırılır (siteler/yapim.py).
ad_guvenilir = yapim.ad_guvenilir


def _ozellik_adlari(oz, dizi):
    if dizi:
        adlar = [oz.get("parent_title")]
        if str(oz.get("feature_type") or "").lower() == "tvshow":
            adlar.append(oz.get("title"))
    else:
        adlar = [oz.get("title"), oz.get("original_title"),
                 re.sub(r"^\s*(?:19|20)\d\d\s*-\s*", "", str(oz.get("movie_name") or ""))]    # "2014 - Interstellar"
    return {s for s in (sade_ad(a) for a in adlar if a) if s}


def _yapim_anahtari(oz):
    return _kimlik(oz.get("imdb_id")) or str(oz.get("feature_id") or "") or sade_ad(oz.get("title"))


def _yapim_durumu(oz, video, yil=None):
    """Altyazının yapımı bu video mu: "kimlik" · "farkli" (kesin başka yapım) · "ad" · "ad_farkli" · None
    (siteler/yapim.py)."""
    if not oz:
        return None
    dizi = video.tur == "dizi"
    tur = str(oz.get("feature_type") or "").lower()
    if dizi and tur in ("movie", "tvshow"):            # bölüm değil: kimliği dizinin (ya da filmin) kendisinin
        k = yapim.kimlik_durumu(video, ust_imdb=oz.get("imdb_id"), tmdb=oz.get("tmdb_id") if tur == "tvshow" else "")
    elif dizi:
        k = yapim.kimlik_durumu(video, imdb=oz.get("imdb_id"), ust_imdb=oz.get("parent_imdb_id"),
                                tmdb=oz.get("parent_tmdb_id"))
    else:
        k = yapim.kimlik_durumu(video, imdb=oz.get("imdb_id"), tmdb=oz.get("tmdb_id") if tur == "movie" else "")
    if k:
        return k
    tur_tutmuyor = (video.tur == "film" and tur in ("episode", "tvshow")) or (dizi and tur == "movie")
    return yapim.ad_durumu(video, _ozellik_adlari(oz, dizi), oz.get("year"), tur_tutmuyor, yil)


def yapim_karari(ogeler, video):
    """[(aday, feature_details)] -> ({kimlik: "at" | "karmasiz"}, dogrulandi). Adayları değiştirmez.
    - Kimliği tutmayan her durumda atılır.
    - Yapım doğrulandıysa (kimlik tutan ya da güvenilir adı tutan var) başka yapımın karma eşleşmesi atılır. Aramadan
      gelen ama adı tutmayan kalır: yerel ya da romaji ad olabilir.
    - Doğrulanamadıysa ve ad güvenilirse karması tutup adı tutmayanların "senkron" işareti kalkar (arayan sıradaki
      sorguyu dener).
    - Ad güvenilmezse karması tutanların en çok tekrarlanan yapımı esas alınır, ötekilerin işareti kalkar; eşitlikte
      hiçbirine güvenilmez (yanlış filmin paralı çevirisi kendiliğinden başlamasın)."""
    guvenilir, yil = yapim.ad_guvenilir(video), yapim.video_yili(video)
    durumlar = [(a, oz or {}, _yapim_durumu(oz or {}, video, yil)) for a, oz in ogeler]
    karar = {a.kimlik: "at" for a, _, d in durumlar if d == "farkli"}
    durumlar = [x for x in durumlar if x[2] != "farkli"]
    dogrulandi = any(d == "kimlik" or (guvenilir and d == "ad") for _, _, d in durumlar)
    if dogrulandi:
        for a, _, d in durumlar:
            if a.karma_tuttu and d == "ad_farkli":
                karar[a.kimlik] = "at"
    elif guvenilir:
        for a, _, d in durumlar:
            if a.karma_tuttu and d == "ad_farkli":
                karar[a.kimlik] = "karmasiz"
    else:
        karmali = Counter(_yapim_anahtari(oz) for a, oz, _ in durumlar if a.karma_tuttu)
        karmali.pop("", None)
        if len(karmali) > 1:
            (esas, sayi), (_, ikinci) = karmali.most_common(2)
            esas = esas if sayi > ikinci else None          # yanlış karma kayıtları tek tük olur
            for a, oz, _ in durumlar:
                if a.karma_tuttu and _yapim_anahtari(oz) and _yapim_anahtari(oz) != esas:
                    karar[a.kimlik] = "karmasiz"
    return karar, dogrulandi


def yapim_suz(ogeler, video):
    """-> (kalan adaylar, atılan kimlikler, doğrulandı mı). Senkron işareti kalkan adayın kendisi değişir."""
    karar, dogrulandi = yapim_karari(ogeler, video)
    kalan, atilan = [], set()
    for a, _ in ogeler:
        k = karar.get(a.kimlik)
        if k == "at":
            atilan.add(a.kimlik)
            continue
        if k == "karmasiz":
            a.karma_tuttu = False
        kalan.append(a)
    return kalan, atilan, dogrulandi


class OpenSubtitles:
    kod, ad = "os", "OpenSubtitles"

    def __init__(self, anahtar, kullanici="", parola="", depo=None):
        self.anahtar, self.kullanici, self.parola, self.depo = anahtar, kullanici, parola, depo
        self.elenen, self.elenen_adlari = 0, []      # son aramada başka yapımın diye atılanlar (kayıt için)
        self.yapim = ""                              # son aramada adla aranınca sonuçlardan öğrenilen yapım (kayıt için)
        gizli.kaydet(anahtar, parola)

    def hazir(self):
        return bool(self.anahtar)

    # ---------------------------------------------------------------------------------------------- oturum
    def _oturum(self, yenile=False):
        """(jeton, adres). Kullanıcı adı yoksa (None, varsayılan adres)."""
        if not (self.kullanici and self.parola):
            return None, ADRES
        kayit = (self.depo.oku("os_oturum", {}) if self.depo else {}) or {}
        if not yenile and kayit.get("jeton") and time.time() - kayit.get("zaman", 0) < JETON_OMRU \
                and kayit.get("kullanici") == self.kullanici:
            gizli.kaydet(kayit["jeton"])
            return kayit["jeton"], _guvenli_adres(kayit.get("adres"))
        try:
            _, veri, _ = ag.istek("POST", ADRES + "login", self._basliklar(None),
                                  govde={"username": self.kullanici, "password": self.parola}, deneme=1)
        except ag.YanitHatasi as e:
            if e.kod in (400, 401, 403):
                raise SiteHatasi(self.ad, "anahtar", "kullanıcı adı ya da parola yanlış") from None
            if e.kod == 429:
                raise SiteHatasi(self.ad, "hiz") from None
            raise SiteHatasi(self.ad, "sunucu", f"giriş: HTTP {e.kod}") from None
        except ag.AgHatasi as e:
            raise SiteHatasi(self.ad, "ag", str(e)) from None
        veri = veri if isinstance(veri, dict) else {}
        jeton = veri.get("token") or ""
        sunucu = veri.get("base_url") or ""
        adres = _guvenli_adres(f"https://{sunucu}/api/v1/")
        gizli.kaydet(jeton)
        if self.depo and jeton:
            self.depo.yaz("os_oturum", {"jeton": jeton, "adres": adres, "zaman": time.time(), "kullanici": self.kullanici})
        return jeton or None, adres

    def _basliklar(self, jeton):
        b = {"Api-Key": self.anahtar}
        if jeton:
            b["Authorization"] = "Bearer " + jeton
        return b

    def _iste(self, yontem, yol, sorgu=None, govde=None, ham=False, deneme=2, tekrar=True, zaman_asimi=15):
        """tekrar=False: istek (indirme) oturum düşse de kendiliğinden yinelenmez; oturum yenilenir, hata döner."""
        jeton, adres = self._oturum()
        for tur in range(2):
            try:
                return ag.istek(yontem, adres + yol, self._basliklar(jeton), sorgu=sorgu, govde=govde, ham=ham,
                                deneme=deneme, zaman_asimi=zaman_asimi)
            except ag.YanitHatasi as e:
                if e.kod == 401 and jeton and tur == 0:          # oturum düşmüş: bir kez yeniden giriş
                    jeton, adres = self._oturum(yenile=True)
                    if tekrar:
                        continue
                raise
        raise SiteHatasi(self.ad, "anahtar")

    def _hata(self, e):
        if isinstance(e, ag.AgHatasi):
            return SiteHatasi(self.ad, "ag", str(e))
        if e.kod in (401, 403):
            return SiteHatasi(self.ad, "anahtar", f"HTTP {e.kod}")
        if e.kod == 406:
            veri = e.veri if isinstance(e.veri, dict) else {}
            return SiteHatasi(self.ad, "kota", veri.get("message", ""), yenilenme=veri.get("reset_time_utc"))
        if e.kod == 429:
            return SiteHatasi(self.ad, "hiz")
        return SiteHatasi(self.ad, "sunucu", f"HTTP {e.kod}")

    # ----------------------------------------------------------------------------------------------- arama
    def ara(self, video, diller):
        if not self.hazir():
            raise SiteHatasi(self.ad, "anahtar", "anahtar yok")
        temel = {"languages": ",".join(sorted({_os_dili(d) for d in diller})), "ai_translated": "exclude",
                 "machine_translated": "exclude", "foreign_parts_only": "exclude"}
        if video.karma:
            temel["moviehash"] = video.karma
        denemeler = []
        dizi = video.tur == "dizi" and video.bolum is not None
        self.yapim = ""
        # Kimliksiz videoda adla gelen sonuçlardan yapım öğrenilir (OpenSubtitles TVDB kimliğiyle aranamaz)
        ogrenilecek = not (video.imdb or video.tmdb) and ad_guvenilir(video) and (dizi or video.tur == "film")
        ogrenilenler = []
        bolum = {"season_number": video.sezon_no, "episode_number": video.bolum} if dizi else {}
        if dizi:
            if video.imdb:
                denemeler.append(dict(temel, parent_imdb_id=_kimlik(video.imdb), **bolum))
            bolum_imdb = getattr(video, "bolum_imdb", "") or video.imdb           # kimlik bölümünkü olabilir
            if bolum_imdb:
                denemeler.append(dict(temel, imdb_id=_kimlik(bolum_imdb)))
            if video.tmdb:
                denemeler.append(dict(temel, parent_tmdb_id=video.tmdb, **bolum))
            for ad in _adla_aramalar(video):
                denemeler.append(dict(temel, query=ad.lower(), type="episode", **bolum))
        else:
            if video.imdb:
                denemeler.append(dict(temel, imdb_id=_kimlik(video.imdb)))
            if video.tmdb:
                denemeler.append(dict(temel, tmdb_id=video.tmdb))
            for ad in _adla_aramalar(video):
                d = dict(temel, query=ad.lower(), type="movie")
                if video.yil and ad != video.dosya_adi:
                    d["year"] = video.yil
                denemeler.append(d)
        self.elenen, self.elenen_adlari = 0, []
        bas, yedek, atilanlar = time.time(), None, None
        for sira, sorgu in enumerate(denemeler):
            if yedek is not None and time.time() - bas > EK_ARAMA_SINIRI:
                break                                       # süre dolmak üzere: doğrulanamayan sonuç yeter
            try:
                veri = self._ara_sorgu(sorgu, **({"deneme": 1, "zaman_asimi": EK_ZAMAN_ASIMI} if yedek else {}))
            except (ag.YanitHatasi, ag.AgHatasi, SiteHatasi) as e:
                if yedek is not None:
                    self.eksik_kaldi = True                 # sonuç eksik: arama önbelleğe girmez
                    break                                   # sıradaki sorgu olmadı: elde olan döner
                raise e if isinstance(e, SiteHatasi) else self._hata(e) from None
            ogeler = self._ogeler(veri, video)
            if ogrenilecek and "query" in sorgu:
                ogrenilenler, ogrenilecek = self._ogren(ogeler, temel, bolum, video, dizi, diller, bas)
            karar, dogrulandi = yapim_karari(ogeler, video)
            if all(karar.get(a.kimlik) == "at" for a, _ in ogeler):
                if ogeler:
                    atilanlar = (ogeler, set(karar))        # hepsi başka yapımın: kayıt için
                continue
            if not dogrulandi and ad_guvenilir(video) and "karmasiz" in karar.values():
                yedek = yedek or (ogeler, veri, sorgu)      # karması tutanın yapımı doğrulanamadı: sıradaki sorgu
                continue
            sonuc = self._sonuc(ogeler, veri, sorgu, video, diller, bas)
            if ogrenilecek:                                 # bu sorgudan öğrenilemedi: sıradaki adla sorgu denenir
                ogrenilenler = self._sonrakinden_ogren(denemeler[sira + 1:], temel, bolum, video, dizi, diller, bas)
            return _ekle(ogrenilenler, sonuc)
        if yedek:
            return _ekle(ogrenilenler, self._sonuc(*yedek, video, diller, bas))
        if atilanlar:
            self._elenenleri_yaz(*atilanlar)
        return ogrenilenler

    def _ogren(self, ogeler, temel, bolum, video, dizi, diller, bas):
        """Adla gelen sonuçlardan yapım öğrenilirse onun kimliğiyle bir kez aranır; gelenler o kimlikle denetlenir.
        -> (eklenecek altyazılar, öğrenmeye devam edilsin mi). Adla gelenler asıl videoyla denetlenmeye devam eder:
        yanlış öğrenilen yapım doğru satırları silmez, yalnız satır ekler."""
        ogrenilen = _ortak_yapim(ogeler, video, dizi)
        if not ogrenilen:
            return [], True
        if time.time() - bas > EK_ARAMA_SINIRI:            # arama süresi dolmak üzere: eldeki sonuç kaybolmasın
            return [], False
        if dizi:
            anahtar = ("parent_imdb_id", ogrenilen["imdb"]) if ogrenilen["imdb"] else \
                ("parent_tmdb_id", ogrenilen["tmdb"]) if ogrenilen["tmdb"] else ("parent_feature_id", ogrenilen["id"])
        else:
            anahtar = ("imdb_id", ogrenilen["imdb"]) if ogrenilen["imdb"] else \
                ("tmdb_id", ogrenilen["tmdb"]) if ogrenilen["tmdb"] else ("id", ogrenilen["id"])
        sorgu = dict(temel, **{anahtar[0]: anahtar[1]}, **bolum)
        try:
            veri = self._ara_sorgu(sorgu, deneme=1, zaman_asimi=EK_ZAMAN_ASIMI)
        except Exception:                       # olmazsa adla gelenler kalır
            self.eksik_kaldi = True
            return [], False
        v = _kimlikli(video, ogrenilen, dizi)
        ogeler = self._ogeler(veri, v)
        if not ogeler:
            return [], False
        ogeler = ogeler + self._hedef_dili_tamamla(ogeler, veri, sorgu, v, diller, bas)
        kalan, _, _ = yapim_suz(ogeler, v)
        if kalan:
            self.yapim = ogrenilen["ad"]
        return kalan, False

    def _sonrakinden_ogren(self, kalanlar, temel, bolum, video, dizi, diller, bas):
        """İlk adla sorgudan yapım öğrenilemediyse (tek sonuç, bölünmüş oy) sıradaki adla sorgu yalnız öğrenmek için."""
        sorgu = next((s for s in kalanlar if "query" in s), None)
        if not sorgu or time.time() - bas > EK_ARAMA_SINIRI:
            return []
        try:
            veri = self._ara_sorgu(sorgu, deneme=1, zaman_asimi=EK_ZAMAN_ASIMI)
        except Exception:
            self.eksik_kaldi = True
            return []
        return self._ogren(self._ogeler(veri, video), temel, bolum, video, dizi, diller, bas)[0]

    def _ara_sorgu(self, sorgu, **k):
        _, veri, _ = self._iste("GET", "subtitles", sorgu=sorted((a, str(d).lower()) for a, d in sorgu.items()), **k)
        return veri

    def _sonuc(self, ogeler, veri, sorgu, video, diller, bas):
        ogeler = ogeler + self._hedef_dili_tamamla(ogeler, veri, sorgu, video, diller, bas)
        kalan, atilan, _ = yapim_suz(ogeler, video)
        self._elenenleri_yaz(ogeler, atilan)
        return kalan

    def _elenenleri_yaz(self, ogeler, atilan):
        """Kayıt için: atılan altyazı sayısı ve yapımlarının adları (en çok 5)."""
        self.elenen, self.elenen_adlari = len(atilan), []
        for a, oz in ogeler:
            ad = str((oz or {}).get("parent_title") or (oz or {}).get("title") or "") if a.kimlik in atilan else ""
            if ad and ad not in self.elenen_adlari and len(self.elenen_adlari) < 5:
                self.elenen_adlari.append(ad)

    def _hedef_dili_tamamla(self, ogeler, veri, sorgu, video, diller, bas):
        """Sonuç sayfası doluysa (öteki sayfalar var) hedef dilin altyazıları başka dilinkilerin arkasında kalmış olabilir
        (çok altyazısı olan filmde İngilizceler sayfayı doldurur). Sayfada hedef dilden az varsa hedef dil bir kez,
        kısa zaman aşımıyla ayrıca aranır; arama süresi dolmak üzereyse aranmaz (sitenin bütün sonucu kaybolmasın)."""
        veri = veri if isinstance(veri, dict) else {}
        ham = [o for o in veri.get("data") if isinstance(o, dict)] if isinstance(veri.get("data"), list) else []
        try:
            dolu = int(veri.get("total_pages") or 1) > 1 or int(veri.get("total_count") or 0) > len(ham)
        except (TypeError, ValueError, OverflowError):
            dolu = False
        hedef = hedef_dil_eksik(dolu, [o["attributes"].get("language") for o in ham if isinstance(o.get("attributes"), dict)],
                                diller)
        if not hedef or time.time() - bas > EK_ARAMA_SINIRI:
            return []
        try:
            ek = self._ogeler(self._ara_sorgu(dict(sorgu, languages=_os_dili(hedef)), deneme=1,
                                              zaman_asimi=EK_ZAMAN_ASIMI), video)
        except Exception:                       # ek arama olmazsa ilk sayfa yeter
            self.eksik_kaldi = True
            return []
        gorulen = {a.kimlik for a, _ in ogeler}
        return [x for x in ek if x[0].kimlik not in gorulen]

    def _adaylar(self, veri, video):
        """Sayfanın süzülmüş adayları (yapım denetiminden geçmiş)."""
        ogeler = self._ogeler(veri, video)
        kalan, atilan, _ = yapim_suz(ogeler, video)
        self._elenenleri_yaz(ogeler, atilan)
        return kalan

    def _ogeler(self, veri, video):
        """Sayfanın [(aday, feature_details)] listesi: bölüm denetimi yapılmış, yapım denetimi yapılmamış."""
        ogeler = []
        veri = veri if isinstance(veri, dict) else {}
        for oge in veri.get("data") if isinstance(veri.get("data"), list) else []:
            a = oge.get("attributes") if isinstance(oge, dict) else None
            if not isinstance(a, dict) or a.get("ai_translated") or a.get("machine_translated") or \
                    a.get("foreign_parts_only"):
                continue
            dosyalar = [d for d in a.get("files") if isinstance(d, dict)] if isinstance(a.get("files"), list) else []
            if not dosyalar:
                continue
            ozellik = a.get("feature_details") if isinstance(a.get("feature_details"), dict) else {}
            kesin = False
            if video.tur == "dizi" and video.bolum is not None:
                sezon, bolum = _tam(ozellik.get("season_number")), _tam(ozellik.get("episode_number"))
                if sezon is not None and bolum is not None and (sezon != video.sezon_no or bolum != video.bolum):
                    continue
                ad_tutuyor = surum.bolum_tutar_mi(a.get("release") or dosyalar[0].get("file_name") or "",
                                                  video.sezon, video.bolum)
                if ad_tutuyor is False:
                    continue
                kesin = (sezon is not None and bolum is not None) or ad_tutuyor is True
            dil = dil_kodu(a.get("language"))
            if not dil:
                continue
            adlar = [str(a.get("release") or ""), str(dosyalar[0].get("file_name") or "")]
            if tam_altyazi_degil(adlar, video):
                continue
            yukleyen = a.get("uploader") if isinstance(a.get("uploader"), dict) else {}
            ogeler.append((Aday(
                self.kod, oge.get("id") or dosyalar[0].get("file_id"), dil, adlar,
                sdh=bool(a.get("hearing_impaired")), karma_tuttu=bool(a.get("moviehash_match")), bolum_kesin=kesin,
                fps=_fps(a.get("fps")), yukleyen=str(yukleyen.get("name") or ""),
                indirme_sayisi=_tam(a.get("download_count")) or 0,
                indirme={"file_id": dosyalar[0].get("file_id"), "dosya_adi": dosyalar[0].get("file_name") or ""}),
                ozellik))
        return ogeler

    # ---------------------------------------------------------------------------------------------- indirme
    def indir(self, aday):
        try:
            _, veri, _ = self._iste("POST", "download", govde={"file_id": int(aday.indirme["file_id"])}, deneme=1,
                                    tekrar=False)
        except (ag.YanitHatasi, ag.AgHatasi) as e:
            raise self._hata(e) from None
        veri = veri if isinstance(veri, dict) else {}
        if self.depo and veri.get("remaining") is not None:
            self.depo.yaz("os_kalan", {"kalan": veri.get("remaining"), "yenilenme": veri.get("reset_time_utc"),
                                       "zaman": time.time()})
        baglanti = veri.get("link")
        if not baglanti:
            raise SiteHatasi(self.ad, "kota" if veri.get("remaining") == 0 else "sunucu", veri.get("message", ""),
                             yenilenme=veri.get("reset_time_utc"))
        try:
            _, icerik, _ = ag.istek("GET", baglanti, ham=True, zaman_asimi=30)
        except (ag.YanitHatasi, ag.AgHatasi) as e:
            raise self._hata(e) from None
        return icerik, veri.get("file_name") or aday.indirme.get("dosya_adi") or "altyazi.srt"

    # ------------------------------------------------------------------------------------------------- test
    def test(self):
        if not self.hazir():
            return {"calisiyor": False, "neden": "anahtar yok"}
        try:
            if self.kullanici and self.parola:
                _, veri, _ = self._iste("GET", "infos/user")
                bilgi = (veri or {}).get("data") or {}
                return {"calisiyor": True, "kalan": bilgi.get("remaining_downloads")}
            self._iste("GET", "infos/formats")
            kalan = (self.depo.oku("os_kalan", {}) or {}).get("kalan") if self.depo else None
            return {"calisiyor": True, "kalan": kalan}
        except SiteHatasi as e:
            return {"calisiyor": False, "neden": e.mesaj or e.tur}
        except (ag.YanitHatasi, ag.AgHatasi) as e:
            h = self._hata(e)
            return {"calisiyor": False, "neden": h.mesaj or h.tur}
