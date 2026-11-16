"""SubDL API v2 (api.subdl.com/api/v2). Anahtar adreste değil başlıkta gider (Authorization: Bearer).

SubDL arama sonucundaki indirme bağlantısını çağıranın anahtarıyla imzalar (/subtitle/<kimlik>.zip?api_key=...): bu
yüzden bağlantının yalnız yolu saklanır, anahtar indirirken başlıkla eklenir. Kimlik varsa kimlikle, yoksa adla ve
gerekirse dosya adıyla aranır. Sezon paketinde SubDL'in açtığı tek bölüm dosyası (unpack) kullanılır.

Kimlikle aramada SubDL bizim kimliğimizi arar. Dosya adıyla ya da adla aramada yapımı kendisi tahmin eder: yanıttaki
"results" (eşleştirdiği yapımın adı, yılı, IMDb/TMDB kimliği ve türü) videoyla karşılaştırılır, başka yapımsa o yanıt
atılır (siteler/yapim.py).

v2 sezonu ve bölümü "season" ve "episode" adlarıyla süzer; v1'in "season_number" ve "episode_number" adlarını yok sayıyor
(canlı: Attack on Titan S01E03'te bütün dizinin 13 sayfası geliyordu, ilk sayfada bu bölüme ait 4 altyazı vardı).
"""
import time
from urllib.parse import urlsplit

from .. import ag, gizli, surum
from . import (EK_ARAMA_SINIRI, EK_ZAMAN_ASIMI, Aday, SiteHatasi, dil_kodu, hedef_dil_eksik, sade_ad,
               tam_altyazi_degil, yapim)

ADRES = "https://api.subdl.com/api/v2/"
INDIRME = "https://dl.subdl.com"
SAYFA = 30                              # SubDL'in sayfa başına en çok sonucu


def _subdl_dili(kod):
    return {"pt-BR": "BR_PT"}.get(kod, kod.upper())


def _yol(adres):
    """Bağlantının yalnız yolu (sorgu ve sunucu atılır). "/" ile başlamayan yol alınmaz: sunucu adının arkasına
    eklenince ("https://dl.subdl.com" + "@baska.sunucu/x") istek ve başlıktaki anahtar başka sunucuya giderdi."""
    if not adres or not isinstance(adres, str):
        return ""
    try:
        yol = urlsplit(adres).path if "://" in adres else adres.split("?", 1)[0]
    except ValueError:                      # bozuk adres ("https://[x/..."): aramayı düşürmesin
        return ""
    return yol if yol.startswith("/") else ""


def _sayi(deger):
    if isinstance(deger, bool):
        return None
    try:
        return int(deger)
    except (TypeError, ValueError, OverflowError):
        return None


def _liste(veri, alan):
    """Yanıttaki bir listenin (results, subtitles) sözlük öğeleri; biçim bozuksa boş."""
    r = veri.get(alan) if isinstance(veri, dict) else None
    return [x for x in r if isinstance(x, dict)] if isinstance(r, list) else []


def _sonuclar(veri):
    return _liste(veri, "results")


def _yil(sonuc):
    yil = _sayi(sonuc.get("year"))
    if not yil:
        tarih = str(sonuc.get("release_date") or sonuc.get("first_air_date") or "")
        yil = _sayi(tarih[:4]) if tarih[:4].isdigit() else None
    return yil


def _yapim_durumu(sonuc, video, yil):
    """SubDL'in eşleştirdiği tek yapım (results öğesi) bu video mu: "kimlik" · "farkli" (kimlik tutmuyor) · "ad" (ad
    tutuyor) · "baska" (tür, yılla birlikte ad ya da dizide yan dizi adı açıkça başka yapımı gösteriyor) · None. Yalnız
    ad farkı karar verdirmez: SubDL animede İngilizce adı yazar ("Attack on Titan"), video romaji adla gelir ("Shingeki
    no Kyojin")."""
    dizi = video.tur == "dizi"
    tur = str(sonuc.get("type") or "").lower()
    tv, film = tur in ("tv", "series", "show"), tur == "movie"
    ayni_tur = (dizi and tv) or (video.tur == "film" and film)
    k = yapim.kimlik_durumu(video, imdb="" if tv else sonuc.get("imdb_id"), ust_imdb=sonuc.get("imdb_id") if tv else "",
                            tmdb=sonuc.get("tmdb_id") if ayni_tur else "")
    if k:
        return k
    if (video.tur == "film" and tv) or (dizi and film):
        return "baska"
    v_adlar = yapim.video_adlari(video)
    s_adlar = {a for a in (sade_ad(sonuc.get(n)) for n in ("name", "original_name", "original_title")) if a}
    if not (v_adlar and s_adlar):
        return None
    farkli_yil = yapim.yil_farkli(video, yil, _yil(sonuc), s_adlar)
    if yapim.ad_tutar(v_adlar, s_adlar, kapsama=not dizi):
        return "baska" if farkli_yil else "ad"        # aynı adlı başka yıl: yeniden çekim
    if dizi and yapim.yan_dizi(v_adlar, s_adlar):
        return "baska"                                # "Law & Order" dizisi "Law & Order SVU" dosyasına
    return "baska" if farkli_yil else None


def yapim_karari(veri, video):
    """Yanıttaki yapım bu video mu: True, False (başka yapım: yanıt atılır) ya da None (bilgi yok ya da karar
    verilemiyor; yanıt olduğu gibi kullanılır). Ad güvenilmezse yalnız kimlik karar verir."""
    sonuclar = _sonuclar(veri)
    if not sonuclar:
        return None
    guvenilir, yil = yapim.ad_guvenilir(video), yapim.video_yili(video)
    durumlar = [_yapim_durumu(r, video, yil) for r in sonuclar]
    if "kimlik" in durumlar or (guvenilir and "ad" in durumlar):
        return True
    if all(d == "farkli" for d in durumlar) or (guvenilir and all(d in ("farkli", "baska") for d in durumlar)):
        return False
    return None


def yapim_adi(veri):
    """Kayıt için yanıttaki ilk yapımın adı ve yılı."""
    sonuclar = _sonuclar(veri)
    if not sonuclar:
        return ""
    ad, yil = str(sonuclar[0].get("name") or "").strip(), _yil(sonuclar[0])
    return f"{ad} ({yil})" if ad and yil else ad


def _surum_basligi(ad):
    """Sürüm adındaki yapım adı; ad bir sürüm adına benzemiyorsa (bölüm, yıl, kaynak ya da çözünürlük yazmıyorsa) boş."""
    if not isinstance(ad, str) or not ad.strip():
        return ""
    s = surum.ayristir(ad)
    if not (s.bolumler or s.sezon is not None or s.yil or s.kaynak or s.cozunurluk):
        return ""
    return sade_ad(s.baslik)


def _basliklar(veri):
    return {b for b in (_surum_basligi(o.get("release_name") or o.get("name")) for o in _liste(veri, "subtitles")) if b}


def _baska_yapimin_altyazilari(ek_veri, veri, video):
    """Hedef dil için ayrıca yapılan aramanın yanıtı başka bir yapımın altyazıları mı: öyleyse o yapımın adı, değilse
    boş. SubDL o dilde yapıma hiç altyazı yoksa ilgisiz bir yapımınkileri veriyor ("results" yine doğru yapımı yazıyor;
    canlı: Attack on Titan'ın Türkçe aramasına The Simpsons 12. sezon). Sürüm adlarının hiçbiri videonun, SubDL'in
    eşleştirdiği yapımın ya da ilk sayfadaki altyazıların adını taşımıyorsa yanıt başka yapımındır. Yalnız ek aramada
    bakılır: ilk sayfanın adları yapımın adlandırmasını (animede romaji/İngilizce) gösterir."""
    ek_basliklar = _basliklar(ek_veri)
    if not ek_basliklar:
        return ""
    bilinen = yapim.video_adlari(video) | _basliklar(veri)
    for r in _sonuclar(veri) + _sonuclar(ek_veri):
        bilinen |= {a for a in (sade_ad(r.get(n)) for n in ("name", "original_name", "original_title")) if a}
    if yapim.ad_tutar(ek_basliklar, bilinen) or yapim.ad_tutar(bilinen, ek_basliklar):
        return ""
    return sorted(ek_basliklar)[0].title()


def _paket_adi(adlar):
    """Sürüm adı bir sezon paketini mi anlatıyor (S01 / Season 1 / Complete, bölüm numarası olmadan)."""
    return any(surum.ayristir(a).paket for a in adlar if a)


class SubDL:
    kod, ad = "subdl", "SubDL"

    def __init__(self, anahtar, depo=None, **_):
        self.anahtar, self.depo = anahtar, depo
        self.elenen, self.elenen_adlari, self.yapim = 0, [], ""     # son arama (kayıt için)
        gizli.kaydet(anahtar)

    def hazir(self):
        return bool(self.anahtar)

    def _basliklar(self):
        return {"Authorization": "Bearer " + self.anahtar}

    def _hata(self, e):
        if isinstance(e, ag.AgHatasi):
            return SiteHatasi(self.ad, "ag", str(e))
        veri = e.veri if isinstance(e.veri, dict) else {}
        mesaj = str(veri.get("error") or veri.get("message") or "")
        if e.kod in (401, 403) and "limit" not in mesaj.lower():
            return SiteHatasi(self.ad, "anahtar", f"HTTP {e.kod}")
        if e.kod == 429 or "limit" in str(mesaj).lower():
            gunluk = any(k in str(mesaj).lower() for k in ("daily", "day", "günlük", "quota"))
            return SiteHatasi(self.ad, "kota" if gunluk else "hiz", str(mesaj))
        return SiteHatasi(self.ad, "sunucu", f"HTTP {e.kod}")

    def ara(self, video, diller):
        if not self.hazir():
            raise SiteHatasi(self.ad, "anahtar", "anahtar yok")
        temel = {"languages": ",".join(sorted({_subdl_dili(d) for d in diller})), "subs_per_page": SAYFA,
                 "releases": 1, "comment": 1, "unpack": 1, "client": "kodi"}
        dizi = video.tur == "dizi" and video.bolum is not None
        if dizi:
            temel.update({"type": "tv", "season": video.sezon_no, "episode": video.bolum})
        elif video.tur == "film":
            temel["type"] = "movie"
        denemeler = []
        if video.imdb:
            denemeler.append(dict(temel, imdb_id=video.imdb))
        if video.tmdb:
            denemeler.append(dict(temel, tmdb_id=video.tmdb))
        if video.dosya_adi and not getattr(video, "elle_arama", ""):    # kimlik yoksa ilk deneme, varsa yedek (madde 9);
            denemeler.append(dict(temel, file_name=video.dosya_adi))     # elle aramada dosya başka yapımın olabilir
        if not (video.imdb or video.tmdb) and video.ad:
            from ..video import ad_eksiz
            for ad in dict.fromkeys([video.ad, ad_eksiz(video.ad)]):
                d = dict(temel, film_name=ad)
                if video.yil and not dizi:
                    d["year"] = video.yil
                denemeler.append(d)
        bas = time.time()
        self.elenen, self.elenen_adlari, self.yapim = 0, [], ""
        atilan_yollar = set()
        for sorgu in denemeler:
            veri = self._ara_sorgu(sorgu)
            if veri is None:
                continue
            if self._baska_yapim(veri, sorgu, video):      # SubDL başka bir yapımı eşleştirmiş: sıradaki sorgu
                ad = yapim_adi(veri)
                atilan_yollar |= {_yol(o.get("url")) for o in _liste(veri, "subtitles")}
                self.elenen = len(atilan_yollar - {""})
                if ad and ad not in self.elenen_adlari:
                    self.elenen_adlari.append(ad)
                continue
            adaylar = self._adaylar(veri, video)
            if adaylar:
                self.yapim = yapim_adi(veri)
                return adaylar + self._hedef_dili_tamamla(adaylar, veri, sorgu, video, diller, bas)
        return []

    def _ara_sorgu(self, sorgu, **k):
        """Yanıt; SubDL "bulunamadı" (ya da başka bir yanıt hatası) dediyse None."""
        try:
            _, veri, _ = ag.istek("GET", ADRES + "subtitles/search", self._basliklar(), sorgu=sorgu, **k)
        except (ag.YanitHatasi, ag.AgHatasi) as e:
            raise self._hata(e) from None
        if isinstance(veri, dict) and veri.get("status") is False:
            hata = str(veri.get("error") or "")
            if "limit" in hata.lower():
                raise SiteHatasi(self.ad, "hiz", hata)
            return None
        return veri

    @staticmethod
    def _baska_yapim(veri, sorgu, video):
        """Yanıt başka bir yapımın mı. Kimlikle aramada SubDL bizim kimliğimizi aramıştır: denetlenmez. Denetim bir
        hata yüzünden çalışamazsa yanıt kullanılır."""
        if sorgu.get("imdb_id") or sorgu.get("tmdb_id"):
            return False
        try:
            return yapim_karari(veri, video) is False
        except Exception:
            return False

    def _hedef_dili_tamamla(self, adaylar, veri, sorgu, video, diller, bas):
        """Sayfa doluysa (sayfa başına 30) hedef dilin altyazıları başka dilinkilerin arkasında kalmış olabilir: çok
        altyazısı olan filmde 30 İngilizce sayfayı doldurur, Türkçe hiç gelmez. Sayfada hedef dilden az varsa hedef dil
        bir kez, kısa zaman aşımıyla ayrıca aranır; arama süresi dolmak üzereyse aranmaz."""
        ham = _liste(veri, "subtitles")
        dolu = bool(ham) and (len(ham) >= SAYFA or (_sayi(veri.get("totalPages")) or 1) > 1)
        hedef = hedef_dil_eksik(dolu, [o.get("language") or o.get("lang") for o in ham], diller)
        if not hedef or time.time() - bas > EK_ARAMA_SINIRI:
            return []
        try:
            ek_veri = self._ara_sorgu(dict(sorgu, languages=_subdl_dili(hedef)), deneme=1, zaman_asimi=EK_ZAMAN_ASIMI)
            ek = self._adaylar(ek_veri, video) if ek_veri and not self._baska_yapim(ek_veri, sorgu, video) else []
            yabanci = _baska_yapimin_altyazilari(ek_veri, veri, video) if ek else ""
        except Exception:                       # ek arama olmazsa ilk sayfa yeter
            self.eksik_kaldi = True                 # sonuç eksik: arama önbelleğe girmez
            return []
        gorulen = {a.kimlik for a in adaylar}
        ek = [a for a in ek if a.kimlik not in gorulen]
        if yabanci and ek:
            self.elenen += len(ek)
            if yabanci not in self.elenen_adlari:
                self.elenen_adlari.append(yabanci)
            return []
        return ek

    def _adaylar(self, veri, video):
        sonuc, gorulen = [], set()
        dizi = video.tur == "dizi" and video.bolum is not None
        for oge in _liste(veri, "subtitles"):
            if oge.get("ai_translated"):
                continue
            dil = dil_kodu(oge.get("language") or oge.get("lang"))
            yol = _yol(oge.get("url"))
            if not dil or not yol:
                continue
            adlar = [str(oge.get("release_name") or "")]
            adlar += [r for r in (oge.get("releases") if isinstance(oge.get("releases"), list) else []) if isinstance(r, str)]
            adlar.append(str(oge.get("name") or ""))
            if tam_altyazi_degil(adlar, video):
                continue
            paket, bolumler, kesin = False, (), False
            if dizi:
                sezon = _sayi(oge.get("season"))
                if sezon not in (None, 0) and sezon != video.sezon_no:
                    continue
                bas, son = _sayi(oge.get("episode_from")), _sayi(oge.get("episode_end"))
                bolum = _sayi(oge.get("episode"))
                ad_tutuyor = surum.bolum_tutar_mi(adlar[0] or adlar[-1], video.sezon, video.bolum)
                if oge.get("full_season") or (bas and son and son > bas):
                    if bas and son and not (bas <= video.bolum <= son):
                        continue
                    paket = True
                    acik = self._paketteki(oge.get("unpack_files") if isinstance(oge.get("unpack_files"), list) else [],
                                           video)
                    if acik is not None:
                        yol, paket, kesin = _yol(acik.get("url")) or yol, False, True
                        adlar.insert(0, str(acik.get("release_name") or acik.get("name") or ""))
                elif bolum not in (None, 0) and bolum != video.bolum:
                    continue
                elif bolum in (None, 0) and ad_tutuyor is False:
                    continue
                elif bolum in (None, 0) and ad_tutuyor is None and _paket_adi(adlar):
                    paket = True                    # bölüm yazmayan sezon paketi adı: kesin bölüm sayılmaz
                else:
                    kesin = ad_tutuyor is True or (bolum == video.bolum and sezon == video.sezon_no)
                bolumler = (video.bolum,) if kesin else ()
            if yol in gorulen:
                continue
            gorulen.add(yol)
            fps = oge.get("fps")
            try:
                fps = float(fps) if fps else None
            except (TypeError, ValueError, OverflowError):
                fps = None
            sonuc.append(Aday(self.kod, yol, dil, adlar, sdh=bool(oge.get("hi")), fps=fps,
                              sezon=video.sezon if dizi else None, bolumler=bolumler, paket=paket, bolum_kesin=kesin,
                              yukleyen=str(oge.get("author") or ""), indirme={"yol": yol}))
        return sonuc

    @staticmethod
    def _paketteki(dosyalar, video):
        for d in dosyalar:
            if not isinstance(d, dict):
                continue
            sezon, bolum = _sayi(d.get("season")), _sayi(d.get("episode"))
            if bolum == video.bolum and sezon in (None, 0, video.sezon_no):
                return d
            if bolum is None and surum.bolum_tutar_mi(d.get("name") or "", video.sezon, video.bolum):
                return d
        return None

    def indir(self, aday):
        yol = _yol(aday.indirme.get("yol"))            # kayıttan gelen yol da denetlenir (eski arama önbelleği)
        if not yol:
            raise SiteHatasi(self.ad, "yok", "beklenmeyen adres")
        try:
            # kendiliğinden yeniden denenmez: yanıtı gelmeyen ilk istek de günlük haktan düşmüş olabilir
            _, icerik, _ = ag.istek("GET", INDIRME + yol, self._basliklar(), ham=True, zaman_asimi=30, deneme=1)
        except (ag.YanitHatasi, ag.AgHatasi) as e:
            raise self._hata(e) from None
        return icerik, yol.rsplit("/", 1)[-1] or "altyazi.zip"

    def test(self):
        if not self.hazir():
            return {"calisiyor": False, "neden": "anahtar yok"}
        try:
            _, veri, _ = ag.istek("GET", ADRES + "me", self._basliklar())
        except (ag.YanitHatasi, ag.AgHatasi) as e:
            h = self._hata(e)
            return {"calisiyor": False, "neden": h.mesaj or h.tur}
        kalan = None
        if isinstance(veri, dict):
            for alan in ("download", "downloads"):
                bilgi = veri.get(alan) or (veri.get("usage") or {}).get(alan)
                if isinstance(bilgi, dict) and bilgi.get("remaining") is not None:
                    kalan = bilgi.get("remaining")
                    break
        return {"calisiyor": True, "kalan": kalan}
