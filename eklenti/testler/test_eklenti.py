"""defta Kodi eklentisinin testleri. İnternet, API anahtarı ve Kodi gerektirmez (sahte Kodi modülleri).

Çalıştırma (eklentinin kökünden):  python -m unittest discover -s testler
Çekirdek (defta paketi) ../cekirdek klasöründen alınır.
"""
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import types
import unittest
import zipfile
from unittest import mock

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(KOK, "testler", "sahte"))
sys.path.insert(0, os.path.join(KOK, "service.subtitles.defta", "resources", "lib"))
_CEKIRDEK = os.path.join(os.path.dirname(KOK), "cekirdek")
if os.path.isdir(os.path.join(_CEKIRDEK, "defta")):
    sys.path.insert(0, _CEKIRDEK)
sys.path.insert(0, os.path.join(KOK, "araclar"))

import xbmc  # noqa: E402
import xbmcaddon  # noqa: E402
import xbmcgui  # noqa: E402
import xbmcplugin  # noqa: E402
import xbmcvfs  # noqa: E402

from d2 import (ag, anahtar, arama, ceviri_isi, eslesme, gizli, indir, kayit, liste,  # noqa: E402
                metin, puan, surum)
from d2 import video as video_modulu  # noqa: E402
from d2.siteler import Aday, SiteHatasi, dil_kodu, yapim  # noqa: E402
from d2.siteler.jimaku import Jimaku  # noqa: E402
from d2.siteler.opensubtitles import OpenSubtitles  # noqa: E402
from d2.siteler.subdl import SubDL  # noqa: E402
from d2.siteler.subsource import SubSource  # noqa: E402


def _video(**alanlar):
    v = video_modulu.Video()
    for a, d in alanlar.items():
        setattr(v, a, d)
    v.surum = surum.ayristir(v.dosya_adi)
    return v


def _ayar(**ek):
    a = {"hedef_dil": "tr", "kaynak_diller": ["en", "ja"], "jimaku_oncelik": True, "sonuc_hedef": 3,
         "sonuc_cevrilecek": 3, "sdh_alta": True, "oto_ai": 0, "kodi_otomatik": False, "hak_dolan": {}}
    a.update(ek)
    return a


class SurumTestleri(unittest.TestCase):
    def test_parcalar(self):
        s = surum.ayristir("Ornek.S01E05.Bolum.Adi.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb.mkv")
        self.assertEqual((s.grup, s.kaynak, s.hizmet, s.cozunurluk, s.kodek, s.ses, s.sezon, s.bolumler),
                         ("NTb", "web", "amzn", "1080p", "h264", "eac3", 1, (5,)))
        self.assertEqual(s.baslik, "Ornek")

    def test_anime_ve_kurgu(self):
        s = surum.ayristir("[SubsPlease] Sousou no Frieren - 05 (1080p) [A1B2C3D4].mkv")
        self.assertEqual((s.grup, s.bolumler, s.sezon), ("SubsPlease", (5,), None))
        self.assertEqual(surum.ayristir("Film.2002.EXTENDED.1080p.BluRay.x264-GRP").kurgu, "extended")
        self.assertEqual(surum.ayristir("Blade.Runner.1982.Directors.Cut.720p").kurgu, "directors_cut")
        self.assertIsNone(surum.ayristir("Film.2002.1080p.BluRay.x264-GRP").kurgu)

    def test_anime_tiresiz_bolum_numarasi(self):
        beklenen = {"[Coalgirls]_Shingeki_no_Kyojin_01_(1280x720_Blu-ray_FLAC)": ("Shingeki no Kyojin", (1,)),
                    "[Grup] Ornek Ad 2 07v2 [720p]": ("Ornek Ad 2", (7,)),
                    "[Grup] Kimetsu no Yaiba (2019) [1080p]": ("Kimetsu no Yaiba", ()),
                    "Mob Psycho 100 (2016)": ("Mob Psycho 100", ()),           # baştaki [Grup] yoksa sayı adın parçası
                    # Paketteki dosya: baştaki [Grup] yok ama sondaki [CRC32] anime adı olduğunu gösteriyor
                    "Shingeki_no_Kyojin_03_(1280x720_Blu-ray_FLAC)_[FB1A088F]": ("Shingeki no Kyojin", (3,)),
                    "Ornek Film 2 (1080p)": ("Ornek Film 2", ())}
        for ad, (baslik, bolumler) in beklenen.items():
            s = surum.ayristir(ad)
            self.assertEqual((s.baslik, s.bolumler), (baslik, bolumler), ad)
        self.assertFalse(surum.bolum_tutar_mi("[Coalgirls]_Shingeki_no_Kyojin_01_(1280x720_Blu-ray_FLAC)", 1, 3))

    def test_bolum_siki_denetlenir(self):
        self.assertTrue(surum.bolum_tutar_mi("Ornek.S01E05.1080p", 1, 5))
        self.assertFalse(surum.bolum_tutar_mi("Ornek.S01E06.1080p", 1, 5))
        self.assertFalse(surum.bolum_tutar_mi("Ornek.S02E05.1080p", 1, 5))     # sezon finali hatası: sezon da tutmalı
        self.assertIsNone(surum.bolum_tutar_mi("Ornek.S01.COMPLETE.1080p", 1, 5))
        self.assertTrue(surum.bolum_tutar_mi("Ornek.S01E04E05.720p", 1, 5))
        self.assertIsNone(surum.bolum_tutar_mi("Interstellar.2014.1080p", None, None))

    def test_yinelenen_yil(self):
        s = surum.ayristir("Interstellar.2014.2014.1080p.BluRay.x264.YIFY.mp4")
        self.assertEqual((s.baslik, s.yil), ("Interstellar", 2014))
        self.assertEqual(surum.ayristir("Blade.Runner.2049.2017.1080p.BluRay").baslik, "Blade Runner 2049")
        self.assertEqual(surum.ayristir("Wonder.Woman.1984.2020.1080p.WEB-DL").baslik, "Wonder Woman 1984")

    def test_grup_yerinden_bulunur(self):
        beklenen = {
            "Interstellar.2014.2014.1080p.BluRay.x264.YIFY.mp4": "YIFY",          # tiresiz, teknik bilgiden sonra
            "Ornek.S01E05.720p.HDTV.x264.KILLERS.mkv": "KILLERS",
            "Film.2002.1080p.BluRay.x264-GRP": "GRP",                              # tireyle
            "Ornek Film (2019) [1080p] [BluRay] [5.1] [YTS.MX].mp4": "YTS",       # sondaki site etiketi
            "Ornek Film (2019) (1080p BluRay x265 HEVC 10bit AAC 5.1 Tigole).mkv": "Tigole",
            "[SubsPlease] Sousou no Frieren - 05 (1080p) [A1B2C3D4].mkv": "SubsPlease",
            # Grup yok: teknik parça, dil, adın parçası
            "Sacred.Games.S01E01.Hindi.1080p.NF.WEB-DL.H.264": None,
            "Andhadhun (2018)[Hindi - 1080p Proper HDRip - DD 5.1 - x265 - 1.6GB - ESubs]": None,
            "Ornek.Film.2019.1080p.WEB-DL.mkv": None,
            "Ornek.Film.2019.1080p.BluRay.x264.DTS.mkv": None,
            "Interstellar.2014.720p.BluRay.x264.Turkish.srt": None,
            "The Office (US).mkv": None,
            "Interstellar - Wishing Science-Fiction - Nolan - en": None,
            "Sousou no Frieren - 05 [1080p][HEVC].mkv": None,
            # Tireden sonraki bölüm numarası grup değil
            "Sousou no Frieren - 05 [BluRay 1080p].mkv": None,
            "One Piece - 1071 [1080p]": None,
            "Ornek S01 - E05": None,
            "Ornek Season 1 - 05v2": None,
        }
        for ad, grup in beklenen.items():
            self.assertEqual(surum.ayristir(ad).grup, grup, ad)
        # Bölüm numarası grup sayılınca aynı bölümün kaynağı tutan her altyazısı "senkron uyumlu" oluyordu
        p, kt = puan.puanla("Sousou no Frieren - 05 [BluRay 1080p].mkv", ["Sousou no Frieren - 05 [BluRay 720p x265]"])
        self.assertFalse(puan.uyumlu_mu(p, kt))
        p, kt = puan.puanla("Interstellar.2014.2014.1080p.BluRay.x264.YIFY.mp4", ["Interstellar.2014.1080p.BluRay.x264.YIFY"])
        self.assertTrue(puan.uyumlu_mu(p, kt))


    def test_dil_eki_her_hedef_dilde_taninir(self):
        # Dil eki listesinde İsveççe, Ukraynaca, Azerbaycanca ve Brezilya Portekizcesi yoktu (3 harfli kodların da
        # yarısı): "….x264-GRP.sv.srt" adında grup "sv" çıkıyor, sürümü tam tutan altyazı "senkron uyumlu" olmuyordu.
        from d2.siteler import DIL_ADLARI
        v = "Film.2019.1080p.WEB-DL.x264-GRP.mkv"
        ekler = set(DIL_ADLARI) | {"pb", "pob", "pt-br", "swe", "ukr", "aze", "deu", "fra", "nld", "ell", "fas", "zho",
                                   "zh-CN", "zh-TW", "Swedish", "Ukrainian", "German"}
        for ek in sorted(ekler):
            ad = f"Film.2019.1080p.WEB-DL.x264-GRP.{ek}.srt"
            self.assertEqual(surum.ayristir(ad).grup, "GRP", ad)
            self.assertTrue(puan.uyumlu_mu(*puan.puanla(v, [ad])), ad)
        self.assertEqual(surum.ayristir("The.Office.UK.S01E01.720p.HDTV.x264-GRP.mkv").baslik, "The Office UK")


class PuanTestleri(unittest.TestCase):
    V = "Ornek.S01E05.1080p.WEB-DL.x264-NTb"

    def test_agirliklar(self):
        self.assertEqual(puan.tek_puan(self.V, "Ornek.S01E05.720p.WEB-DL.x264-NTb"), 24)
        self.assertEqual(puan.tek_puan(self.V, "Ornek.S01E05.1080p.WEB-DL.x265-FLUX"), 9)
        self.assertEqual(puan.tek_puan(self.V, "Ornek.S01E05.1080p.HDTV.x264-KILLERS"), 4)
        self.assertEqual(puan.tek_puan(self.V, "Ornek.S01E05.Bolum.Adi.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb"), 26)

    def test_karma_her_seyin_ustunde(self):
        p, kt = puan.puanla(self.V, ["Baska.Ad"], karma_tuttu=True)
        self.assertEqual((p, kt), (puan.KARMA, False))
        self.assertTrue(puan.uyumlu_mu(p, kt))
        # Karması tutanlar arasında adı da tutan öne geçer (karma kaydı yanlış olabilir)
        ayni_ad, _ = puan.puanla(self.V, [self.V], karma_tuttu=True)
        self.assertGreater(ayni_ad, p)
        self.assertGreater(p, puan.puanla(self.V, [self.V])[0])                # karma her ad puanının üstünde

    def test_farkli_yayin_hizmeti_kaynak_tutmaz(self):
        self.assertEqual(puan.tek_puan("Ornek.S01E05.1080p.AMZN.WEB-DL.x264-A", "Ornek.S01E05.1080p.NF.WEB-DL.x264-B"), 4)

    def test_kurgu_tutmazsa_uyumlu_degil(self):
        p, kt = puan.puanla("Film.2002.EXTENDED.1080p.BluRay.x264-GRP", ["Film.2002.1080p.BluRay.x264-GRP"])
        self.assertTrue(kt)
        self.assertFalse(puan.uyumlu_mu(p, kt))
        p, kt = puan.puanla("Film.2002.1080p.BluRay.x264-GRP", ["Film.2002.THEATRICAL.1080p.BluRay.x264-GRP"])
        self.assertFalse(kt)                                   # adında kurgu yazmayan sinema kurgusu sayılır
        p, kt = puan.puanla("Film.2002.1080p.BluRay.x264-GRP", ["Film.2002.UNRATED.BluRay", "Film.2002.1080p.BluRay.x264-GRP"])
        self.assertFalse(kt)

    def test_video_adi_yoksa_puan_sifir(self):
        self.assertEqual(puan.puanla(None, ["Ornek.S01E05.1080p.WEB-DL.x264-NTb"]), (0, False))


def _aday(site, kimlik, dil, ad, **k):
    return Aday(site, kimlik, dil, [ad], **k)


class ListeTestleri(unittest.TestCase):
    def setUp(self):
        self.v = _video(dosya_adi="Ornek.S01E05.1080p.WEB-DL.x264-NTb.mkv", tur="dizi", dizi_adi="Ornek", sezon=1,
                        bolum=5, imdb="tt0000001")

    def _olustur(self, adaylar, hazirlar=(), secili=None, suren=None, kaymislar=None, tercih=None, **ay):
        return liste.olustur(self.v, adaylar, list(hazirlar), secili, suren, kaymislar or {}, tercih or {}, _ayar(**ay))

    def test_sira_ve_gruplar(self):
        adaylar = [
            _aday("subdl", "a", "tr", "Ornek.S01E05.1080p.HDTV.x264-KILLERS"),
            _aday("os", "b", "tr", "Ornek.S01E05.720p.WEB-DL.x264-NTb"),
            _aday("os", "c", "tr", "Ornek.S01E05.1080p.WEB-DL.x264-NTb", sdh=True),
            _aday("os", "d", "en", "Ornek.S01E05.1080p.WEB-DL.x264-NTb"),
            _aday("jimaku", "e", "ja", "[Grup] Ornek - 05.ass"),
        ]
        hazir = {"kimlik": "x1", "kaynak": {"site": "os", "kimlik": "z", "adlar": ["Ornek.S01E05.1080p.HDTV-LOL"]},
                 "zaman": 1}
        satirlar, sira, _ = self._olustur(adaylar, [hazir])
        eylemler = [(s.eylem, s.anahtar) for s in satirlar]
        self.assertEqual(eylemler, [("ai", "ai:x1"), ("indir", "os:b"), ("indir", "subdl:a"), ("indir", "os:c"),
                                    ("cevir", "jimaku:e"), ("cevir", "os:d")])
        self.assertTrue(satirlar[1].uyumlu)                     # grup + kaynak tuttu (24 puan)
        self.assertFalse(satirlar[2].uyumlu)
        self.assertTrue(satirlar[0].yazi.startswith("AI · "))
        self.assertTrue(satirlar[4].yazi.startswith("Çevir · "))

    def test_ayni_sitede_ayni_adli_satirlar_tek_satir(self):
        ad = "Ornek.S01E05.720p.WEB-DL.x264-NTb"
        adaylar = [_aday("os", "t1", "tr", ad, indirme_sayisi=5), _aday("os", "t2", "tr", ad, indirme_sayisi=50),
                   _aday("os", "t3", "tr", "Ornek.S01E05.1080p.HDTV.x264-KILLERS"),
                   _aday("os", "t4", "tr", "Ornek.S01E05.480p.HDTV.x264-LOL"),
                   _aday("subsource", "e1", "en", ad), _aday("subsource", "e2", "en", ad),
                   _aday("subsource", "e3", "en", ad.lower().replace(".", " ")),
                   _aday("subsource", "e4", "en", "Ornek.S01E05.1080p.HDTV.x264-KILLERS")]
        satirlar, sira, _ = self._olustur(adaylar, sonuc_hedef=3, sonuc_cevrilecek=3)
        self.assertEqual([s.anahtar for s in satirlar],
                         ["os:t2", "os:t3", "os:t4", "subsource:e1", "subsource:e4"])
        # Kaymış satırı yalnız görünen satırları dener (gizlenen kopya aynı dosyanın yeniden yüklenmesidir)
        self.assertEqual([k for _, k in sira], ["os:t2", "os:t3", "os:t4"])
        # Kaymış işaretli kopya işaretsizini gizlemez
        satirlar, _, _ = self._olustur(adaylar, kaymislar={"os:t2": 1}, sonuc_hedef=3)
        self.assertEqual([s.anahtar for s in satirlar if s.dil == "tr"], ["os:t1", "os:t3", "os:t4", "os:t2"])

    def test_ayni_adli_farkli_yukleyen_ayri_satir_yukleyen_yazar(self):
        ad = "Ornek.S01E05.720p.WEB-DL.x264-NTb"
        adaylar = [_aday("subsource", "1", "tr", ad, yukleyen="Ali"), _aday("subsource", "2", "tr", ad, yukleyen="ali"),
                   _aday("subsource", "3", "tr", ad, yukleyen="Veli"),
                   _aday("subsource", "4", "tr", ad + ".tr.hi.srt", yukleyen="Ali"),
                   _aday("os", "5", "tr", "Ornek.S01E05.1080p.HDTV.x264-KILLERS", yukleyen="Can")]
        satirlar, _, _ = self._olustur(adaylar, sonuc_hedef=5)
        yazi = {s.anahtar: s.yazi for s in satirlar}
        self.assertEqual(sorted(yazi), ["os:5", "subsource:1", "subsource:3", "subsource:4"])   # 2, 1'in kopyası
        self.assertTrue(yazi["subsource:1"].endswith("SubSource (Ali)"))
        self.assertTrue(yazi["subsource:3"].endswith("SubSource (Veli)"))
        self.assertTrue(yazi["subsource:4"].endswith("· SubSource"))      # SDH işaretinden ayırt edilir
        self.assertTrue(next(s for s in satirlar if s.anahtar == "subsource:4").sdh)
        self.assertTrue(yazi["os:5"].endswith("· OpenSubtitles"))

    def test_sezon_paketi_satirda_yazilir(self):
        # Paketin adı çoğu kez yalnız ilk dosyayı yazar ("..._01_..."): 5. bölümde yanlış bölüm sanılmasın
        ad = "[Grup]_Ornek_01_(1080p)_[ABCDEF12]"
        satirlar, _, _ = self._olustur([_aday("subsource", "p", "tr", ad, paket=True),
                                        _aday("subsource", "e", "en", ad, paket=True)])
        yazi = {s.anahtar: s.yazi for s in satirlar}
        self.assertEqual(yazi["subsource:p"], "Sezon paketi · " + ad + " · SubSource")
        self.assertEqual(yazi["subsource:e"], "Çevir · Sezon paketi · " + ad + " · SubSource")

    def test_cevrilecekte_yukleyen_kaymis_ikiz_ve_gizlenenin_yedegi(self):
        ad = "Ornek.S01E05.720p.WEB-DL.x264-NTb"
        e1, e2 = _aday("subsource", "e1", "en", ad, yukleyen="Ali"), _aday("subsource", "e2", "en", ad, yukleyen="Veli")
        satirlar, _, _ = self._olustur([e1, e2])
        self.assertEqual(sorted(s.yazi for s in satirlar), ["Çevir · " + ad + " · SubSource (Ali)",
                                                            "Çevir · " + ad + " · SubSource (Veli)"])
        # Aynı yükleyenin kaymış işaretli ikizi: yükleyen yazmak ayırt ettirmez, yazılmaz
        t1, t2 = _aday("os", "1", "tr", ad, yukleyen="Ali"), _aday("os", "2", "tr", ad, yukleyen="Ali")
        satirlar, _, _ = self._olustur([t1, t2], kaymislar={"os:2": 1})
        self.assertEqual([s.yazi for s in satirlar if s.dil == "tr"], [ad + " · OpenSubtitles"] * 2)
        # Gizlenen kopyanın başka sitedeki kopyası kalanın yedeği olur
        g1 = _aday("os", "g1", "tr", ad, yukleyen="Ali", indirme_sayisi=1)
        g2 = _aday("os", "g2", "tr", ad, yukleyen="Ali", indirme_sayisi=50)
        g1.yedekler = [_aday("subdl", "s1", "tr", ad)]
        satirlar, _, tum = self._olustur([g1, g2])
        self.assertEqual([s.anahtar for s in satirlar], ["os:g2"])
        self.assertEqual([y.anahtar for y in tum["os:g2"].yedekler], ["subdl:s1"])

    def test_secili_kaymis_ve_yeniden(self):
        adaylar = [_aday("os", "b", "tr", "Ornek.S01E05.720p.WEB-DL.x264-NTb")]
        hazir = {"kimlik": "x1", "kaynak": {"site": "os", "kimlik": "z", "adlar": ["Ornek.S01E05"]}, "zaman": 1}
        secili = {"tur": "ai", "anahtar": "ai:x1", "ad": "Ornek.S01E05", "site": "os", "dil": "tr", "yol": "/y"}
        satirlar, _, _ = self._olustur(adaylar, [hazir], secili=secili)
        self.assertEqual([s.eylem for s in satirlar], ["secili", "kaymis", "indir", "yeniden"])
        self.assertTrue(satirlar[0].yazi.startswith("• AI · "))
        suren = {"ceviri_anahtari": "ai:x1", "yuzde": 40, "ad": "Ornek.S01E05"}
        satirlar, _, _ = self._olustur(adaylar, [hazir], secili=secili, suren=suren)
        self.assertEqual(satirlar[0].eylem, "suren")
        self.assertIn("çevriliyor %40", satirlar[0].yazi)
        self.assertNotIn("yeniden", [s.eylem for s in satirlar])

    def test_kaymislar_hedef_grubun_en_altina_isaret_sirasiyla(self):
        adaylar = [_aday("os", "b", "tr", "Ornek.S01E05.720p.WEB-DL.x264-NTb"),
                   _aday("os", "c", "tr", "Ornek.S01E05.1080p.HDTV.x264-KILLERS")]
        hazir = {"kimlik": "x1", "kaynak": {"site": "os", "kimlik": "z", "adlar": ["A"]}, "zaman": 1}
        satirlar, sira, _ = self._olustur(adaylar, [hazir], kaymislar={"ai:x1": 2.0, "os:b": 1.0})
        self.assertEqual([s.anahtar for s in satirlar], ["os:c", "os:b", "ai:x1"])   # AI de insanın altına iner

    def test_ayni_altyazi_tek_satir_ve_hak_dolan_yedege_gecer(self):
        a1 = _aday("os", "1", "tr", "Ornek.S01E05.720p.WEB-DL.x264-NTb")
        a2 = _aday("subdl", "2", "tr", "Ornek.S01E05.720p.WEB-DL.x264-NTb")
        a3 = _aday("os", "3", "tr", "Ornek.S01E05.1080p.HDTV.x264-LOL")
        satirlar, _, tum = self._olustur([a1, a2, a3], hak_dolan={"os": "03:00"})
        self.assertEqual([s.anahtar for s in satirlar], ["subdl:2"])     # yedeği olmayan os:3 gizlendi
        self.assertTrue(satirlar[0].uyumlu)

    def test_site_basina_sinir_ayri_sayilir(self):
        adaylar = [_aday("os", str(i), "tr", f"Ornek.S01E05.{i}.WEB-DL") for i in range(5)]
        adaylar += [_aday("os", f"e{i}", "en", f"Ornek.S01E05.e{i}.WEB-DL") for i in range(5)]
        satirlar, _, _ = self._olustur(adaylar, sonuc_hedef=1, sonuc_cevrilecek=3)
        self.assertEqual(sum(1 for s in satirlar if s.eylem == "indir"), 1)
        self.assertEqual(sum(1 for s in satirlar if s.eylem == "cevir"), 3)

    def test_guvenlik_satiri(self):
        adaylar = [_aday("os", "d", "en", "Ornek.S01E05.1080p.WEB-DL.x264-NTb")]
        satirlar, _, _ = self._olustur(adaylar, kodi_otomatik=True)
        self.assertEqual(satirlar[0].eylem, "guvenlik")
        self.assertIn("altyazı yok", satirlar[0].yazi)
        satirlar, _, _ = self._olustur(adaylar, kodi_otomatik=True, oto_ai=2)
        self.assertNotEqual(satirlar[0].eylem, "guvenlik")

    def test_cevrilmis_kaynak_tekrar_cevir_olarak_gelmez(self):
        a = _aday("os", "d", "en", "Ornek.S01E05.1080p.WEB-DL.x264-NTb")
        hazir = {"kimlik": "x1", "kaynak": {"site": "os", "kimlik": "d", "anahtar": "os:d", "adlar": [a.ad]}, "zaman": 1}
        satirlar, _, _ = self._olustur([a], [hazir])
        self.assertEqual([s.eylem for s in satirlar], ["ai"])

    def test_cevrilmis_kaynagin_baska_sitedeki_kopyasi_cevir_olarak_gelmez(self):
        # Çevrilen altyazı iki sitede de vardı (tek satır: OpenSubtitles, yedeği SubDL). Sonraki aramada OpenSubtitles
        # yanıt vermezse ya da hakkı dolmuşsa SubDL'deki kopya "Çevir" diye yeniden çıkıyordu (aynı altyazıya ikinci para).
        ad = "Ornek.S01E05.1080p.WEB-DL.x264-NTb"
        o, s = _aday("os", "d", "en", ad), _aday("subdl", "/s/1.zip", "en", ad)
        kaynak = liste.birlestir([o, s])[0].kayit()
        self.assertEqual([y["anahtar"] for y in kaynak["yedekler"]], ["subdl:/s/1.zip"])
        hazir = {"kimlik": "x1", "kaynak": kaynak, "zaman": 1}
        satirlar, _, _ = self._olustur([_aday("subdl", "/s/1.zip", "en", ad)], [hazir])
        self.assertEqual([x.eylem for x in satirlar], ["ai"])
        # Çevrilen yedekten indirildiyse (kaynak: SubDL) asıl satır da çevrilmiş sayılır
        hazir2 = {"kimlik": "x2", "kaynak": _aday("subdl", "/s/1.zip", "en", ad).kayit(), "zaman": 1}
        satirlar, _, _ = self._olustur([_aday("os", "d", "en", ad), _aday("subdl", "/s/1.zip", "en", ad)], [hazir2])
        self.assertEqual([x.eylem for x in satirlar], ["ai"])
        satirlar, _, _ = self._olustur([_aday("os", "baska", "en", "Ornek.S01E05.720p.HDTV.x264-LOL")], [hazir])
        self.assertEqual([x.eylem for x in satirlar], ["ai", "cevir"])

    def test_kaymis_dongusu_basa_doner(self):
        from d2.giris import _yeni_sira
        sira = [("ai", "ai:1"), ("ai", "ai:2"), ("insan", "os:1"), ("insan", "os:2")]
        k = {}
        sirayla = []
        for zaman, simdiki in enumerate(["ai:1", "ai:2", "os:1", "os:2"]):
            k[simdiki] = zaman
            yeni = [x for x in _yeni_sira(sira, k) if x[1] != simdiki]
            sirayla.append(yeni[0][1])
        self.assertEqual(sirayla, ["ai:2", "os:1", "os:2", "ai:1"])


class AnahtarTestleri(unittest.TestCase):
    def test_adlar_yazim_farklarina_dayanikli(self):
        d = tempfile.mkdtemp()
        yol = os.path.join(d, ".env")
        with open(yol, "w", encoding="utf-8") as f:
            f.write("# yorum\nOPENAI_API_KEY=sk-aaaaaaaaaaaa1234\nOpenSubtitle_Api_Key = 'osk99998888'\n"
                    "SUB_DL_KEY=\"subdlanahtar5678\"\nsubsource_api_key: ssk-abcdef\nJIMAKU_KEY=jk-0000ZZZZ\n"
                    "OPENSUBTITLES_USERNAME=kullanici\nOPENSUBTITLES_PASSWORD=parola123\nBASKA=deger\n")
        sonuc = anahtar.oku(anahtar.dosya_bul(d))
        self.assertEqual(sonuc["openai_anahtar"], "sk-aaaaaaaaaaaa1234")
        self.assertEqual(sonuc["os_anahtar"], "osk99998888")
        self.assertEqual(sonuc["subdl_anahtar"], "subdlanahtar5678")
        self.assertEqual(sonuc["subsource_anahtar"], "ssk-abcdef")
        self.assertEqual(sonuc["jimaku_anahtar"], "jk-0000ZZZZ")
        self.assertEqual((sonuc["os_kullanici"], sonuc["os_parola"]), ("kullanici", "parola123"))
        shutil.rmtree(d)

    def test_satir_sonundaki_yorum_anahtara_katilmaz(self):
        # .env dosyalarında değerden sonra "# açıklama" yazılabilir; yorum anahtarın parçası sayılıp ayarlara yazılıyordu
        sonuc = anahtar.oku_metin("SUBDL_API_KEY=subdlanahtar5678   # SubDL hesabım\n"
                                  "OPENAI_API_KEY=\"sk-aaaaaaaaaaaa1234\" # ücretli\n"
                                  "JIMAKU_KEY='jk-0000ZZZZ'\t# jimaku\n"
                                  "OPENSUBTITLES_PASSWORD=par#ola123\n"            # boşluksuz # değerin parçasıdır
                                  "OPENSUBTITLES_USERNAME=\"ad # soyad\"\n")      # tırnak içindeki # de
        self.assertEqual(sonuc, {"subdl_anahtar": "subdlanahtar5678", "openai_anahtar": "sk-aaaaaaaaaaaa1234",
                                 "jimaku_anahtar": "jk-0000ZZZZ", "os_parola": "par#ola123",
                                 "os_kullanici": "ad # soyad"})

    def test_karsilastirma(self):
        k = {x[0]: x for x in anahtar.karsilastir({"openai_anahtar": "sk-yeni1111", "subdl_anahtar": "aynı0000"},
                                                   {"openai_anahtar": "sk-eski2222", "subdl_anahtar": "aynı0000",
                                                    "jimaku_anahtar": "jk-1234"})}
        self.assertEqual(k["openai_anahtar"][2:], ("degisti", "2222", "1111"))
        self.assertEqual(k["subdl_anahtar"][2], "ayni")
        self.assertEqual(k["jimaku_anahtar"][2], "dosyada_yok")
        self.assertEqual(k["os_anahtar"][2], "yok")


class GizliTestleri(unittest.TestCase):
    def test_anahtar_ve_adres_gizlenir(self):
        gizli.kaydet("sk-cokgizlianahtar9876")
        m = gizli.gizle("anahtar sk-cokgizlianahtar9876 ile istek")
        self.assertNotIn("cokgizli", m)
        self.assertIn("…9876", m)
        m = gizli.gizle("oynatılan https://abc.download.real-debrid.com/d/JETON123/Ornek.S01E05.mkv|User-Agent=x")
        self.assertNotIn("JETON123", m)
        self.assertIn("abc.download.real-debrid.com/…", m)
        # Adresin yolu ve sorgusu hiçbir sunucuda kayda geçmez: indirme yolu jeton, sorgu anahtar taşıyabilir
        m = gizli.gizle("https://api.subdl.com/api/v2/subtitles/search?api_key=GIZLI&imdb_id=tt1")
        self.assertEqual(m, "https://api.subdl.com/…")
        self.assertEqual(gizli.gizle("olmadı: https://jimaku.cc/entry/12/download/Ornek.S01E05.srt"),
                         "olmadı: https://jimaku.cc/…")
        self.assertNotIn("GIZLI", gizli.gizle("/subtitle/1-2.zip?api_key=GIZLI"))

    def test_indirme_hatasi_kaydinda_adres_yolu_yok(self):
        # Adayın anahtarı SubDL'de indirme yolu, Jimaku'da tam adrestir: kayıt satırına site ve ad yazılır
        from d2 import giris
        d = tempfile.mkdtemp()
        durum = kayit.Durum(d)
        aday = Aday("subdl", "/subtitle/987-654.zip", "tr", ["Ornek.S01E05.1080p.WEB-DL"],
                    indirme={"yol": "/subtitle/987-654.zip"})
        son = {"adaylar": {aday.anahtar: aday.kayit()}}
        del xbmc.kayitlar[:]

        def olmaz(*a, **k):
            raise RuntimeError("bağlantı kurulamadı")
        with mock.patch("d2.arama.insan_altyazisi", olmaz):
            self.assertIsNone(giris._insan_hazirla(durum, _video(tur="dizi", sezon=1, bolum=5), son, aday.anahtar))
        yazilan = " ".join(m for _, m in xbmc.kayitlar)
        self.assertIn("Ornek.S01E05.1080p.WEB-DL", yazilan)
        self.assertNotIn("987-654", yazilan)
        shutil.rmtree(d)

    def test_certifi_baglami_site_isteklerine_de_uygulanir(self):
        # Python 3.13'te (Kodi 22) HTTPS işleyicisi TLS bağlamını kurulurken alır: certifi bağlamı açıcıdan sonra
        # kurulursa site istekleri onu kullanmaz (sistem sertifikalarını göremeyen cihazda bütün siteler düşer).
        import subprocess
        ca = "/etc/ssl/certs/ca-certificates.crt"
        if not os.path.exists(ca):
            self.skipTest("sertifika dosyası yok")
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "certifi.py"), "w", encoding="utf-8") as f:
            f.write(f"def where():\n    return {ca!r}\n")
        betik = ("import ssl\nfrom d2 import ag, kodi\n"
                 "h = [x for x in ag._ACICI.handlers if isinstance(x, ag.KayitliHTTPSIsleyici)][0]\n"
                 "b = getattr(h, '_context', None) or ssl._create_default_https_context()\n"
                 "print(ag._BAGLAM is not None and b is ag._BAGLAM)\n")
        yollar = os.pathsep.join([d, os.path.join(KOK, "service.subtitles.defta", "resources", "lib"),
                                  os.path.join(KOK, "testler", "sahte")])
        cikti = subprocess.run([sys.executable, "-c", betik], env=dict(os.environ, PYTHONPATH=yollar),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(cikti.stdout.strip(), "True", cikti.stderr[-400:])
        shutil.rmtree(d)


    def test_anahtarin_parcasi_da_gizlenir(self):
        # 0.1.18'deki olay: tanı kaydı indirme bağlantısının sonunu yazdı; anahtarın bütünü ve "api_key=" ön eki yazıda
        # olmadığı için son 14 karakteri kayda geçti. Kayıtlı bir anahtarın uzun bir parçası da gizlenir.
        anahtar_ = "q7Zr2LmX9vB4cN1kT8wY5pHd3JfG6sAe"
        gizli.kaydet(anahtar_)
        for yazi in ("bağlantının sonu: " + anahtar_[-14:], "…zip?api_k=" + anahtar_[5:20] + "&x=1", anahtar_[:9] + " diye başlıyor"):
            m = gizli.gizle(yazi)
            for n in range(len(anahtar_) - 7):
                self.assertNotIn(anahtar_[n:n + 8], m, yazi)
        self.assertEqual(gizli.gizle("sonu " + anahtar_[-14:] + " idi"), "sonu … idi")
        self.assertEqual(gizli.gizle("anahtar " + anahtar_), "anahtar …" + anahtar_[-4:])       # bütünü: son 4 karakter
        self.assertEqual(gizli.gizle("yalnız " + anahtar_[-7:]), "yalnız " + anahtar_[-7:])      # kısa parça: dokunulmaz
        gizli.kaydet("parola12345")                       # kısa değerin (parola) parçası aranmaz: sözcükler bozulmasın
        self.assertEqual(gizli.gizle("kullanıcı adı ya da parola123 yanlış"), "kullanıcı adı ya da parola123 yanlış")


class IndirTestleri(unittest.TestCase):
    SRT = "1\n00:00:01,000 --> 00:00:02,000\nBir\n\n2\n00:00:03,000 --> 00:00:04,000\nİki\n\n" \
          "3\n00:00:05,000 --> 00:00:06,500\nÜç\n"

    def _zip(self, dosyalar):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            for ad, icerik in dosyalar.items():
                z.writestr(ad, icerik)
        return b.getvalue()

    def test_sezon_paketinden_dogru_bolum(self):
        veri = self._zip({"Ornek.S01E04.srt": self.SRT.replace("Bir", "Dört"), "Ornek.S01E05.srt": self.SRT,
                          "Ornek.S01E06.srt": self.SRT.replace("Bir", "Altı")})
        v = _video(tur="dizi", sezon=1, bolum=5)
        m, u = indir.altyazi_cikar(veri, "paket.zip", v)
        self.assertEqual(u, ".srt")
        self.assertIn("Bir", m)
        with self.assertRaises(indir.IndirmeHatasi):
            indir.altyazi_cikar(veri, "paket.zip", _video(tur="dizi", sezon=1, bolum=9))

    def test_tiresiz_anime_paketinden_dogru_bolum(self):
        # "[Grup]_Ad_03_(...)" adlarında bölüm numarası tanınmazsa en büyük dosya (yanlış bölüm) seçiliyordu
        ad = "[Coalgirls]_Shingeki_no_Kyojin_{:02d}_(1280x720_Blu-ray_FLAC).srt"
        veri = self._zip({ad.format(1): self.SRT.replace("Bir", "Birinci") * 3, ad.format(3): self.SRT,
                          ad.format(2): self.SRT.replace("Bir", "İkinci") * 2})
        m, _ = indir.altyazi_cikar(veri, "paket.zip", _video(tur="dizi", sezon=1, bolum=3))
        self.assertIn("Bir", m)
        self.assertNotIn("Birinci", m)

    def test_crc_adli_paketten_dogru_bolum(self):
        # SubSource'taki 25 bölümlük Türkçe paket: dosya adlarında baştaki [Grup] yok, sonda [CRC32] var; bölüm
        # tanınmayınca en büyük dosya (yanlış bölüm) seçiliyordu
        ad = "Shingeki_no_Kyojin_{:02d}_(1280x720_Blu-ray_FLAC)_[{:08X}].srt"
        veri = self._zip({ad.format(1, 0x3CB81308): self.SRT.replace("Bir", "Birinci") * 3,
                          ad.format(3, 0xFB1A088F): self.SRT, ad.format(2, 0xA071754C): self.SRT.replace("Bir", "İkinci") * 2,
                          "Beni Oku.txt": "okuyun"})
        m, _ = indir.altyazi_cikar(veri, "subsource_1.zip", _video(tur="dizi", sezon=1, bolum=3))
        self.assertIn("Bir", m)
        self.assertNotIn("Birinci", m)
        self.assertNotIn("İkinci", m)

    def test_adi_okunamayan_pakette_rastgele_dosya_verilmez(self):
        # Adlar yalnız bir numarada ayrışıyorsa o numara bölümdür; pakette bölüm yoksa yanlış dosya değil hata
        veri = self._zip({"Bolum 01 - Ad.srt": self.SRT.replace("Bir", "Birinci") * 3, "Bolum 02 - Ad.srt": self.SRT,
                          "Bolum 03 - Ad.srt": self.SRT.replace("Bir", "Üçüncü") * 2})
        m, _ = indir.altyazi_cikar(veri, "p.zip", _video(tur="dizi", sezon=1, bolum=2))
        self.assertIn("Bir", m)
        self.assertNotIn("Birinci", m)
        with self.assertRaises(indir.IndirmeHatasi):
            indir.altyazi_cikar(veri, "p.zip", _video(tur="dizi", sezon=1, bolum=7))
        # Biçim kopyaları (numarasız, ya da yalnız çözünürlükte ya da sürüm ekinde ayrışan) tek bölümlük arşivdir
        for kopyalar in ({"Turkish.srt": self.SRT, "Turkish.SDH.srt": self.SRT.replace("Bir", "Müzik Bir")},
                         {"Ad 03 [720p].srt": self.SRT, "Ad 03 [1080p].SDH.srt": self.SRT.replace("Bir", "Müzik Bir")},
                         {"Ad_03v2.srt": self.SRT, "Ad_03v1.SDH.srt": self.SRT.replace("Bir", "Müzik Bir")}):
            m, _ = indir.altyazi_cikar(self._zip(kopyalar), "k.zip", _video(tur="dizi", sezon=1, bolum=3))
            self.assertNotIn("Müzik", m, kopyalar)

    def test_altyazi_olmayan_dosya_reddedilir(self):
        with self.assertRaises(indir.IndirmeHatasi):
            indir.altyazi_cikar(b"<html>hata</html>", "x.srt")
        with self.assertRaises(indir.IndirmeHatasi):
            indir.altyazi_cikar(b"Rar!\x1a\x07\x00", "x.rar")
        # Engelleme ya da hata sayfası: HTML yorumları da "-->" ile biter, üç yorum altyazı sayılıyordu
        sayfa = b"<html><!-- bir --><body><!-- iki -->Erisim engellendi<!-- uc --></body></html>"
        for ad in ("x.srt", "x.zip", "x.vtt"):
            with self.assertRaises(indir.IndirmeHatasi):
                indir.altyazi_cikar(sayfa, ad)
        # Saat yazmayan kısa WebVTT zamanları ve boşluksuz ok yine altyazıdır
        vtt = "WEBVTT\n\n00:01.000 --> 00:02.000\nBir\n\n00:03.000 --> 00:04.000\nIki\n\n00:05.000 --> 00:06.000\nUc\n"
        self.assertEqual(indir.altyazi_cikar(vtt.encode("utf-8"), "x.vtt")[1], ".vtt")
        self.assertEqual(indir.altyazi_cikar(self.SRT.replace(" --> ", "-->").encode("utf-8"), "x.srt")[1], ".srt")

    def test_dev_dosya_bellege_alinmaz(self):
        # Arşivdeki dosyanın açılmış boyutu sınırsız okunuyordu: birkaç KB'lık bir ZIP bombası belleği dolduruyordu
        with mock.patch.object(indir, "EN_BUYUK_ALTYAZI", 2000):
            with self.assertRaises(indir.IndirmeHatasi):
                indir.altyazi_cikar(self._zip({"Ornek.srt": self.SRT * 40}), "a.zip")
            # Sınırı aşan dosya seçilmez; yanındaki altyazı kullanılır
            m, _ = indir.altyazi_cikar(self._zip({"Ornek.srt": self.SRT.replace("Bir", "Dev") * 40,
                                                  "Ornek.SDH.srt": self.SRT}), "a.zip")
            self.assertNotIn("Dev", m)

    def test_turkce_eski_kodlama(self):
        m, _ = indir.altyazi_cikar(self.SRT.encode("cp1254"), "x.srt")
        self.assertIn("İki", m)
        self.assertIn("Üç", m)

    def test_kare_hizi(self):
        oran = indir.kare_hizi_orani(23.976, 25.0, self.SRT, ".srt", 3600)
        self.assertAlmostEqual(oran, 23.976 / 25)
        self.assertIsNone(indir.kare_hizi_orani(23.976, 23.976, self.SRT, ".srt", 3600))
        self.assertIsNone(indir.kare_hizi_orani(23.976, 29.97, self.SRT, ".srt", 3600))     # başka hızlara dokunulmaz
        uzun = "1\n00:43:30,000 --> 00:43:31,000\nSon\n"                                      # 42 dk'lık videoyu aşıyor
        self.assertAlmostEqual(indir.kare_hizi_orani(None, 25.0, uzun, ".srt", 42 * 60), 23.976 / 25)
        self.assertIsNone(indir.kare_hizi_orani(None, 25.0, uzun, ".srt", 50 * 60))

    def test_zaman_olcekleme_bicimi_korur(self):
        self.assertEqual(indir.zaman_olcekle("00:01:00,000 --> 00:02:00,000", ".srt", 0.5), "00:00:30,000 --> 00:01:00,000")
        ass = "Dialogue: 0,0:01:00.00,0:02:00.50,Default,,0,0,0,,{\\i1}Merhaba"
        self.assertEqual(indir.zaman_olcekle(ass, ".ass", 2.0),
                         "Dialogue: 0,0:02:00.00,0:04:01.00,Default,,0,0,0,,{\\i1}Merhaba")


class VideoTestleri(unittest.TestCase):
    def test_adresten_yalniz_dosya_adi(self):
        self.assertEqual(video_modulu.adres_dosya_adi(
            "https://x.real-debrid.com/d/JETON/Ornek.S01E05.1080p.WEB-DL.x264-NTb.mkv?token=abc|User-Agent=K"),
            "Ornek.S01E05.1080p.WEB-DL.x264-NTb.mkv")
        self.assertEqual(video_modulu.adres_dosya_adi("https://cdn.example/play/abcdef123456"), "")
        self.assertEqual(video_modulu.adres_dosya_adi("plugin://plugin.video.redlight/?mode=play"), "")
        self.assertEqual(video_modulu.adres_dosya_adi("C:\\Filmler\\Interstellar.2014.mp4"), "Interstellar.2014.mp4")

    def test_karma_opensubtitles_algoritmasi(self):
        d = tempfile.mkdtemp()
        yol = os.path.join(d, "v.bin")
        veri = bytes(range(256)) * 1024                       # 256 KB
        with open(yol, "wb") as f:
            f.write(veri)
        beklenen = len(veri)
        for parca in (veri[:65536], veri[-65536:]):
            for (q,) in struct.iter_unpack("<Q", parca):
                beklenen = (beklenen + q) & 0xFFFFFFFFFFFFFFFF

        def ac():
            f = open(yol, "rb")
            return f, len(veri)
        self.assertEqual(video_modulu.karma_hesapla(ac), f"{beklenen:016x}")
        shutil.rmtree(d)

    def test_kodiden_okuma(self):
        xbmc.oynatici.update({"oynuyor": True, "sure": 2580.0,
                              "yol": "https://x.real-debrid.com/d/T/Ornek.S01E05.1080p.WEB-DL.x264-NTb.mkv",
                              "etiket": {"tvshowtitle": "Ornek", "title": "Bölüm", "season": 1, "episode": 5, "year": 2020,
                                         "mediatype": "episode", "uniqueid": {"tmdb": "123", "imdb": "tt0000001"}}})
        xbmc.infolabel["Player.Process(videofps)"] = "23.976"
        v = video_modulu.oynatilan(xbmc, xbmcvfs, karma=False)
        self.assertEqual((v.tur, v.dizi_adi, v.sezon, v.bolum, v.tmdb, v.imdb, v.fps, v.akis),
                         ("dizi", "Ornek", 1, 5, "123", "tt0000001", 23.976, True))
        self.assertEqual(v.dosya_adi, "Ornek.S01E05.1080p.WEB-DL.x264-NTb.mkv")
        self.assertNotIn("real-debrid", v.ozet())
        self.assertEqual(v.icerik_anahtari(), "imdb:tt0000001:s01e05")
        self.assertEqual(v.yapim_anahtari(), "ad:ornek")
        xbmc.oynatici["oynuyor"] = False

    def test_baslik_dosya_adiysa_yapim_adi_cikarilir(self):
        ad = "Interstellar.2014.2014.1080p.BluRay.x264.YIFY.mp4"
        for baslik in (ad, ad[:-4]):
            xbmc.oynatici.update({"oynuyor": True, "sure": 10144.0, "yol": "C:\\Filmler\\" + ad,
                                  "etiket": {"title": baslik}})
            v = video_modulu.oynatilan(xbmc, xbmcvfs, karma=False)
            self.assertEqual((v.ad, v.tur, v.yil, v.dosya_adi), ("Interstellar", "film", 2014, ad))
        xbmc.oynatici.update({"yol": "C:\\Filmler\\abc123.mkv", "etiket": {"title": "Ornek.Film.2019.1080p.WEB-DL.x264-GRP"}})
        self.assertEqual(video_modulu.oynatilan(xbmc, xbmcvfs, karma=False).ad, "Ornek Film")   # eklentinin sürüm adı
        xbmc.oynatici["oynuyor"] = False
        for gercek_ad in ("Se7en", "S.W.A.T.", "Mr. Robot", "Spider-Man: No Way Home", "Up", "Charlotte's Web"):
            self.assertEqual(video_modulu.yapim_adi(gercek_ad, "Film.2019.mkv"), gercek_ad)
        # Temiz dosya adı başlığa yazılmışsa yalnız uzantı atılır (yıl adın parçası olabilir)
        for temiz in ("Blade Runner 2049", "Charlotte's Web", "Wonder Woman 1984"):
            self.assertEqual(video_modulu.yapim_adi(temiz + ".mkv", temiz + ".mkv"), temiz)
            self.assertEqual(video_modulu.yapim_adi(temiz, temiz + ".mkv"), temiz)

    def test_akista_eklentinin_birakti_surum_adi(self):
        # Red Light (TorBox): adres dosya adı taşımaz, sürüm adı subs.player_filename özelliğinde
        anahtar = "Window(10000).Property(subs.player_filename)"
        xbmc.oynatici.update({"oynuyor": True, "sure": 10144.0, "yol": "https://store-1.example/dld/AbC123?token=gizli",
                              "etiket": {"title": "Interstellar", "year": 2014, "mediatype": "movie",
                                         "imdb": "tt0816692"}})
        try:
            xbmc.infolabel[anahtar] = "Interstellar.2014.2160p.PROPER.IMAX.REMUX.DV.HDR10.TrueHD.7.1-JennaOrtegaUHD.mkv"
            v = video_modulu.oynatilan(xbmc, xbmcvfs, karma=False)
            self.assertEqual(v.dosya_adi, "Interstellar.2014.2160p.PROPER.IMAX.REMUX.DV.HDR10.TrueHD.7.1-JennaOrtegaUHD.mkv")
            self.assertEqual((v.ad, v.surum.cozunurluk, v.surum.grup), ("Interstellar", "2160p", "JennaOrtegaUHD"))
            # Özellik adresse yalnız son parçası (video dosyasıysa) alınır; jeton hiçbir yere geçmez
            xbmc.infolabel[anahtar] = "https://store-1.example/dl/Tkn987/Interstellar.2014.1080p.BluRay.x264-SPARKS.mkv?t=1"
            v = video_modulu.oynatilan(xbmc, xbmcvfs, karma=False)
            self.assertEqual(v.dosya_adi, "Interstellar.2014.1080p.BluRay.x264-SPARKS.mkv")
            self.assertNotIn("Tkn987", v.ozet())
            xbmc.infolabel[anahtar] = "https://store-1.example/dld/Tkn987?x=1"
            self.assertEqual(video_modulu.oynatilan(xbmc, xbmcvfs, karma=False).dosya_adi, "")
            # Adres dosya adı taşıyorsa özelliğe bakılmaz; "%2F" ayraçtır (jeton parçası ada geçmez)
            xbmc.oynatici["yol"] = "https://store-1.example/d/Tkn%2FFilm.2019.1080p.WEB-DL.x264-GRP.mkv?token=gizli"
            v = video_modulu.oynatilan(xbmc, xbmcvfs, karma=False)
            self.assertEqual(v.dosya_adi, "Film.2019.1080p.WEB-DL.x264-GRP.mkv")
            xbmc.oynatici["yol"] = "https://store-1.example/dld/AbC123?token=gizli"
            xbmc.oynatici["sure"] = 0.0                          # süresiz akış canlı yayındır: özelliğe bakılmaz
            self.assertEqual(video_modulu.oynatilan(xbmc, xbmcvfs, karma=False).dosya_adi, "")
            xbmc.oynatici["sure"] = 10144.0
            # Sürüm adına benzemeyen noktalı değer (jeton olabilir) alınmaz
            xbmc.infolabel[anahtar] = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.SflKxwRJSMeKKF2QT4fw"
            self.assertEqual(video_modulu.oynatilan(xbmc, xbmcvfs, karma=False).dosya_adi, "")
        finally:
            xbmc.infolabel.pop(anahtar, None)
            xbmc.oynatici["oynuyor"] = False

    def test_elle_arama(self):
        v = video_modulu.elle_aramadan("Ornek Dizi S02E03")
        self.assertEqual((v.tur, v.dizi_adi, v.sezon, v.bolum), ("dizi", "Ornek Dizi", 2, 3))
        v = video_modulu.elle_aramadan("Interstellar 2014")
        self.assertEqual((v.tur, v.baslik, v.yil), ("", "Interstellar", 2014))


class SiteTestleri(unittest.TestCase):
    def test_dil_kodlari(self):
        self.assertEqual([dil_kodu(x) for x in ("EN", "english", "pt-br", "Brazilian_portuguese", "TR", "pt-PT", "zh-TW",
                                                "ZH_BG", "xyz-1")],
                         ["en", "en", "pt-BR", "pt-BR", "tr", "pt", "zh", "zh", ""])

    def test_opensubtitles_sonuclari(self):
        v = _video(tur="dizi", sezon=1, bolum=5, dosya_adi="Ornek.S01E05.mkv")
        veri = {"data": [
            {"id": "1", "attributes": {"language": "tr", "release": "Ornek.S01E05.WEB", "hearing_impaired": False,
                                       "moviehash_match": True, "fps": 23.976, "uploader": {"name": "u"},
                                       "feature_details": {"season_number": 1, "episode_number": 5},
                                       "files": [{"file_id": 11, "file_name": "a.srt"}]}},
            {"id": "2", "attributes": {"language": "tr", "release": "Ornek.S01E06.WEB",
                                       "feature_details": {"season_number": 1, "episode_number": 6},
                                       "files": [{"file_id": 12, "file_name": "b.srt"}]}},
            {"id": "3", "attributes": {"language": "en", "release": "Ornek.S01E05", "ai_translated": True,
                                       "feature_details": {"season_number": 1, "episode_number": 5},
                                       "files": [{"file_id": 13, "file_name": "c.srt"}]}},
        ]}
        a = OpenSubtitles("k")._adaylar(veri, v)
        self.assertEqual([x.kimlik for x in a], ["1"])
        self.assertTrue(a[0].karma_tuttu)
        self.assertEqual(a[0].indirme["file_id"], 11)

    def test_opensubtitles_parametreleri_ve_kota(self):
        v = _video(tur="film", baslik="Interstellar", imdb="tt0816692", karma="abc")
        istekler = []

        def sahte(yontem, adres, basliklar=None, sorgu=None, govde=None, **k):
            istekler.append((yontem, adres, sorgu, govde, basliklar))
            if adres.endswith("download"):
                raise ag.YanitHatasi(406, {"message": "limit", "reset_time_utc": "2026-11-17T00:00:00Z"})
            return 200, {"data": []}, {}
        with mock.patch("d2.ag.istek", sahte):
            os_ = OpenSubtitles("anahtar123")
            os_.ara(v, ["tr", "en"])
            sorgu = dict(istekler[0][2])
            self.assertEqual(sorgu["imdb_id"], "816692")
            self.assertEqual(sorgu["languages"], "en,tr")
            self.assertEqual((sorgu["ai_translated"], sorgu["machine_translated"]), ("exclude", "exclude"))
            self.assertEqual([k for k, _ in istekler[0][2]], sorted(k for k, _ in istekler[0][2]))
            with self.assertRaises(SiteHatasi) as h:
                os_.indir(Aday("os", "1", "tr", ["x"], indirme={"file_id": 5}))
            self.assertEqual(h.exception.tur, "kota")
        self.assertEqual(sum(1 for i in istekler if i[1].endswith("download")), 1)   # indirme tekrarlanmaz

    def test_opensubtitles_bozuk_govde_hatayi_gizlemez(self):
        # Hata ya da indirme yanıtının gövdesi sözlük değilse (yazı, liste) AttributeError çıkıyordu: "hak doldu" (406)
        # tanınmıyor, kota kaydı yazılmıyor, kullanıcıya Python hatası gösteriliyordu.
        aday = Aday("os", "1", "tr", ["x"], indirme={"file_id": 5})
        for govde in ("limit", ["limit"], 5):
            def sahte(yontem, adres, basliklar=None, **k):
                raise ag.YanitHatasi(406, govde)
            with mock.patch("d2.ag.istek", sahte), self.assertRaises(SiteHatasi) as h:
                OpenSubtitles("k").indir(aday)
            self.assertEqual(h.exception.tur, "kota")
        for govde in (["x"], "x", 5):
            with mock.patch("d2.ag.istek", lambda *a, **k: (200, govde, {})), self.assertRaises(SiteHatasi) as h:
                OpenSubtitles("k").indir(aday)
            self.assertEqual(h.exception.tur, "sunucu")
            with mock.patch("d2.ag.istek", lambda *a, **k: (200, govde, {})):       # giriş yanıtı bozuk: oturumsuz
                self.assertEqual(OpenSubtitles("k", "u", "parola1")._oturum(), (None, "https://api.opensubtitles.com/api/v1/"))

    def test_opensubtitles_dusen_oturumda_indirme_bir_kez_yinelenir(self):
        """Başka cihazda giriş yapılınca eski jeton düşer: 401 alan indirme hak harcamaz, yeni girişle bir kez yinelenir."""
        istekler = []

        def sahte(yontem, adres, basliklar=None, **k):
            istekler.append((adres.rsplit("/", 1)[-1], (basliklar or {}).get("Authorization", "")))
            if adres.endswith("login"):
                return 200, {"token": "jeton-yeni99", "base_url": "api.opensubtitles.com"}, {}
            if adres.endswith("download"):
                if (basliklar or {}).get("Authorization") == "Bearer jeton-eski11":
                    raise ag.YanitHatasi(401, {}, {})
                return 200, {"link": "https://dl.opensubtitles.org/x/1", "file_name": "a.srt", "remaining": 19}, {}
            return 200, b"1\n00:00:01,000 --> 00:00:02,000\nA\n", {}
        depo = kayit.Depo(tempfile.mkdtemp())
        depo.yaz("os_oturum", {"jeton": "jeton-eski11", "adres": "https://api.opensubtitles.com/api/v1/",
                               "zaman": time.time(), "kullanici": "kullanici"})
        aday = types.SimpleNamespace(indirme={"file_id": 7})
        try:
            with mock.patch("d2.ag.istek", sahte):
                icerik, ad = OpenSubtitles("anahtar123", "kullanici", "parola123", depo).indir(aday)
            self.assertEqual(ad, "a.srt")
            self.assertEqual([a for a, _ in istekler], ["download", "login", "download", "1"])
            self.assertEqual(istekler[2][1], "Bearer jeton-yeni99")
            self.assertEqual(depo.oku("os_oturum")["jeton"], "jeton-yeni99")
        finally:
            shutil.rmtree(depo.klasor)

    def test_opensubtitles_giris_sunucusu_denetlenir(self):
        # Giriş yanıtındaki "base_url" içinde "opensubtitles" geçmesi yetiyordu: "opensubtitles.baska.invalid" ya da
        # yol taşıyan bir değer kabul ediliyor, Api-Key ve oturum jetonu 23 saat boyunca o adrese gidiyordu.
        def giris(sunucu):
            adresler = []

            def sahte(yontem, adres, basliklar=None, **k):
                adresler.append(adres)
                if adres.endswith("login"):
                    return 200, {"token": "jeton-abcdef", "base_url": sunucu}, {}
                return 200, {"data": []}, {}
            depo = kayit.Depo(tempfile.mkdtemp())
            with mock.patch("d2.ag.istek", sahte):
                OpenSubtitles("anahtar123", "kullanici", "parola123", depo)._iste("GET", "infos/user")
                OpenSubtitles("anahtar123", "kullanici", "parola123", depo)._iste("GET", "infos/user")   # kayıttan
            shutil.rmtree(depo.klasor)
            return adresler[1:]
        asil = "https://api.opensubtitles.com/api/v1/infos/user"
        self.assertEqual(giris("vip-api.opensubtitles.com"), ["https://vip-api.opensubtitles.com/api/v1/infos/user"] * 2)
        for bozuk in ("opensubtitles.baska.invalid", "baska.invalid/opensubtitles.com", "https://vip-api.opensubtitles.com",
                      "baska.invalid?x=.opensubtitles.com", "kullanici@baska.invalid#.opensubtitles.com", 5, ""):
            self.assertEqual(giris(bozuk), [asil] * 2, bozuk)

    @staticmethod
    def _os_oge(kimlik, dil, ozellik, karma=False, ad="x"):
        return {"id": kimlik, "attributes": {"language": dil, "release": ad, "moviehash_match": karma,
                                             "feature_details": ozellik,
                                             "files": [{"file_id": int(kimlik), "file_name": ad + ".srt"}]}}

    INTER = {"feature_type": "Movie", "title": "Interstellar", "year": 2014, "imdb_id": 816692,
             "movie_name": "2014 - Interstellar"}
    ANDHA = {"feature_type": "Movie", "title": "Andhadhun", "year": 2018, "imdb_id": 8108198}
    SACRED = {"feature_type": "Episode", "title": "Ashwathama", "parent_title": "Sacred Games", "imdb_id": 7431594,
              "season_number": 1, "episode_number": 1}
    YIFY = "Interstellar.2014.2014.1080p.BluRay.x264.YIFY.mp4"

    def _karisik(self):
        return {"data": [self._os_oge("1", "tr", self.INTER, karma=True), self._os_oge("2", "en", self.ANDHA, karma=True),
                         self._os_oge("3", "en", self.SACRED, karma=True), self._os_oge("4", "en", self.INTER),
                         self._os_oge("5", "en", self.INTER, karma=True)]}

    def test_opensubtitles_karmasi_tutan_baska_yapim_atilir(self):
        # Kitaplıkta olmayan dosya: kimlik yok, ad sürüm adından (güvenilir)
        v = _video(tur="film", dosya_adi=self.YIFY, yil=2014, karma="abc")
        os_ = OpenSubtitles("k")
        self.assertEqual([x.kimlik for x in os_._adaylar(self._karisik(), v)], ["1", "4", "5"])
        self.assertEqual(os_.elenen, 2)
        # Kimlik varsa kimlik karar verir (yerel başlık sorun olmaz)
        v = _video(tur="film", baslik="Yıldızlararası", imdb="tt0816692", dosya_adi="a.mkv")
        self.assertEqual([x.kimlik for x in OpenSubtitles("k")._adaylar(self._karisik(), v)], ["1", "4", "5"])
        # Anlamsız dosya adı: hiçbir şey atılmaz; karması tutanlardan en çok tekrarlanan yapımın işareti kalır
        v = _video(tur="film", dosya_adi="video_0001.mp4", karma="abc")
        a = OpenSubtitles("k")._adaylar(self._karisik(), v)
        self.assertEqual([(x.kimlik, x.karma_tuttu) for x in a],
                         [("1", True), ("2", False), ("3", False), ("4", False), ("5", True)])
        # Aynı adlı başka yılın karma eşleşmesi atılır; aramadan gelen kalır (yerel ad da olabilir)
        dune = [self._os_oge("6", "tr", {"feature_type": "Movie", "title": "Dune", "year": 1984}, karma=True),
                self._os_oge("7", "tr", {"feature_type": "Movie", "title": "Dune", "year": 2021}),
                self._os_oge("8", "tr", {"feature_type": "Movie", "title": "Dune", "year": 1984})]
        v = _video(tur="film", dosya_adi="Dune.2021.1080p.WEB-DL.x264-GRP.mkv")
        self.assertEqual([x.kimlik for x in OpenSubtitles("k")._adaylar({"data": dune}, v)], ["7", "8"])
        # Elle aramada oynatılan dosyanın adı ve yılı kullanılmaz
        v = video_modulu.elle_aramadan("Dune")
        v.surum, v.karma = surum.ayristir("Dune.1984.1080p.BluRay.x264-GRP.mkv"), "abc"
        dune2 = [self._os_oge("9", "tr", {"feature_type": "Movie", "title": "Dune", "year": 2021}, karma=True)]
        self.assertEqual([(x.kimlik, x.karma_tuttu) for x in OpenSubtitles("k")._adaylar({"data": dune2}, v)],
                         [("9", True)])

    def test_opensubtitles_dizide_dizi_adi_karsilastirilir(self):
        law = {"feature_type": "Episode", "title": "Payback", "parent_title": "Law & Order", "season_number": 1,
               "episode_number": 1}
        svu = {"feature_type": "Episode", "title": "Payback", "parent_title": "Law & Order: Special Victims Unit",
               "season_number": 1, "episode_number": 1}
        v = _video(tur="dizi", sezon=1, bolum=1, dosya_adi="Law.and.Order.SVU.S01E01.720p.HDTV.x264-GRP.mkv")
        v.dizi_adi = v.surum.baslik
        a = OpenSubtitles("k")._adaylar({"data": [self._os_oge("1", "tr", law, karma=True),
                                                  self._os_oge("2", "tr", svu, karma=True)]}, v)
        self.assertEqual([(x.kimlik, x.karma_tuttu) for x in a], [("1", False), ("2", False)])   # atılmaz, işaret kalkar
        # Bölüm başlığı ("Pilot") başka dizinin bölümünü tutturmaz
        ornek = {"feature_type": "Episode", "title": "Pilot", "parent_title": "Ornek", "season_number": 1,
                 "episode_number": 1}
        baska = dict(ornek, parent_title="Baska Dizi")
        v = _video(tur="dizi", sezon=1, bolum=1, dizi_adi="Ornek", baslik="Pilot",
                   dosya_adi="Ornek.S01E01.1080p.WEB-DL.x264-GRP.mkv")
        a = OpenSubtitles("k")._adaylar({"data": [self._os_oge("1", "tr", ornek),
                                                  self._os_oge("2", "tr", baska, karma=True)]}, v)
        self.assertEqual([x.kimlik for x in a], ["1"])
        # Dizinin kimliği yoksa bölümün kimliği tutmasa da karar verilmez (adla bakılır)
        v = _video(tur="dizi", sezon=1, bolum=1, dizi_adi="Ornek", imdb="tt0000001")
        a = OpenSubtitles("k")._adaylar({"data": [self._os_oge("1", "tr", {"feature_type": "Episode", "imdb_id": 99,
                                                                            "parent_title": "Ornek"})]}, v)
        self.assertEqual([x.kimlik for x in a], ["1"])

    def test_opensubtitles_dogrulanamayan_karmada_siradaki_sorgu(self):
        v = _video(tur="film", dosya_adi=self.YIFY, karma="abc")
        v.baslik, v.yil = v.surum.baslik, 2014
        sorgular = []

        def sahte(yontem, adres, basliklar=None, sorgu=None, **k):
            sorgu = dict(sorgu)
            sorgular.append(sorgu.get("query"))
            if sorgu.get("query", "").endswith(".mp4"):        # dosya adıyla: yalnız yanlış karma kayıtları
                return 200, {"data": [self._os_oge("2", "en", self.ANDHA, karma=True),
                                      self._os_oge("3", "en", self.SACRED, karma=True)]}, {}
            return 200, {"data": [self._os_oge("1", "tr", self.INTER, karma=True), self._os_oge("4", "en", self.INTER)]}, {}
        with mock.patch("d2.ag.istek", sahte):
            a = OpenSubtitles("k").ara(v, ["tr", "en"])
        self.assertEqual(sorgular, [self.YIFY.lower(), "interstellar"])
        self.assertEqual([(x.kimlik, x.karma_tuttu) for x in a], [("1", True), ("4", False)])

    def test_dolu_sayfada_hedef_dil_ayrica_aranir(self):
        v = _video(tur="film", baslik="Interstellar", yil=2014, imdb="tt0816692", dosya_adi="Interstellar.2014.mkv")
        istekler = []

        def sahte_subdl(yontem, adres, basliklar=None, sorgu=None, **k):
            istekler.append((dict(sorgu), k))
            if sorgu["languages"] == "TR":
                return 200, {"status": True, "subtitles": [
                    {"release_name": "Interstellar.2014.TR", "language": "TR", "url": "/subtitle/tr-1.zip"}]}, {}
            return 200, {"status": True, "subtitles": [
                {"release_name": f"Interstellar.2014.EN{n}", "language": "EN", "url": f"/subtitle/{n}-{n}.zip"}
                for n in range(30)]}, {}
        with mock.patch("d2.ag.istek", sahte_subdl):
            a = SubDL("k").ara(v, ["tr", "en", "ja"])
        self.assertEqual([i[0]["languages"] for i in istekler], ["EN,JA,TR", "TR"])     # yalnız hedef dil
        self.assertEqual((istekler[1][1].get("deneme"), istekler[1][1].get("zaman_asimi")), (1, 6))
        self.assertEqual((sum(1 for x in a if x.dil == "tr"), len(a)), (1, 31))

        istekler.clear()

        def sahte_os(yontem, adres, basliklar=None, sorgu=None, **k):
            istekler.append((dict(sorgu), k))
            if dict(sorgu)["languages"] == "tr":
                return 200, {"total_pages": 1, "data": [self._os_oge("900", "tr", self.INTER),
                                                        self._os_oge("901", "tr", self.ANDHA, karma=True)]}, {}
            return 200, {"total_pages": 3, "total_count": 150,
                         "data": [self._os_oge(str(n), "en", self.INTER) for n in range(1, 51)]}, {}
        with mock.patch("d2.ag.istek", sahte_os):
            a = OpenSubtitles("k").ara(v, ["tr", "en"])
        self.assertEqual([i[0]["languages"] for i in istekler], ["en,tr", "tr"])
        self.assertEqual([x.kimlik for x in a if x.dil == "tr"], ["900"])     # ek sayfadaki yanlış yapım da süzülür

        # Ek arama hata verirse ilk sayfa kalır; hedef dil yeterliyse ya da süre dolmak üzereyse ek arama yok
        def hatali(yontem, adres, basliklar=None, sorgu=None, **k):
            if dict(sorgu)["languages"] == "tr":
                raise ag.AgHatasi("zaman aşımı")
            return sahte_os(yontem, adres, basliklar, sorgu, **k)
        with mock.patch("d2.ag.istek", hatali):
            self.assertEqual(len(OpenSubtitles("k").ara(v, ["tr", "en"])), 50)
        istekler.clear()
        with mock.patch("d2.ag.istek", sahte_os), mock.patch("d2.siteler.opensubtitles.EK_ARAMA_SINIRI", -1):
            OpenSubtitles("k").ara(v, ["tr", "en"])
        self.assertEqual(len(istekler), 1)
        istekler.clear()
        bol_tr = {"total_pages": 2, "data": [self._os_oge(str(n), "tr" if n <= 5 else "en", self.INTER)
                                             for n in range(1, 51)]}
        with mock.patch("d2.ag.istek", lambda *a_, sorgu=None, **k: (istekler.append(sorgu) or (200, bol_tr, {}))):
            OpenSubtitles("k").ara(v, ["tr", "en"])
        self.assertEqual(len(istekler), 1)

    def test_ikinci_inceleme_senaryolari(self):
        from d2.siteler.opensubtitles import _adla_aramalar, ad_guvenilir
        wedding = {"feature_type": "Movie", "title": "The Wedding Singer", "year": 1998, "imdb_id": 120888}
        veri = {"data": [self._os_oge("1", "tr", self.INTER, karma=True), self._os_oge("2", "en", self.INTER, karma=True),
                         self._os_oge("3", "en", wedding)]}
        # Anlamsız ya da genel ad hiçbir yapımı doğrulamaz: doğru karma eşleşmeleri atılmaz
        for dosya in ("wedding.mp4", "Movie.1080p.mkv"):
            v = _video(tur="film", dosya_adi=dosya, karma="abc")
            self.assertFalse(ad_guvenilir(v), dosya)
            a = OpenSubtitles("k")._adaylar(veri, v)
            self.assertEqual([(x.kimlik, x.karma_tuttu) for x in a], [("1", True), ("2", True), ("3", False)], dosya)
        # Eşitlikte (1'e 1) hiçbir karmaya güvenilmez
        v = _video(tur="film", dosya_adi="video_0001.mp4", karma="abc")
        a = OpenSubtitles("k")._adaylar({"data": [self._os_oge("1", "tr", self.INTER, karma=True),
                                                  self._os_oge("2", "tr", self.ANDHA, karma=True)]}, v)
        self.assertEqual([x.karma_tuttu for x in a], [False, False])
        # Başka bir kimlik tutuyorsa "farklı" sayılmaz
        v = _video(tur="film", baslik="Ornek", imdb="tt1111111", tmdb="555", dosya_adi="a.mkv")
        oz = {"feature_type": "Movie", "title": "Ornek", "imdb_id": 2222222, "tmdb_id": 555}
        self.assertEqual([x.kimlik for x in OpenSubtitles("k")._adaylar({"data": [self._os_oge("1", "tr", oz)]}, v)],
                         ["1"])
        # Dizide oynatan eklentinin kimliği bölümün olabilir: yalnız dizinin kimliği yazan satır atılmaz
        v = _video(tur="dizi", sezon=1, bolum=1, dizi_adi="Ornek", imdb="tt0000099")
        oz = {"feature_type": "Episode", "parent_imdb_id": 12345, "parent_title": "Ornek", "season_number": 1,
              "episode_number": 1}
        self.assertEqual([x.kimlik for x in OpenSubtitles("k")._adaylar({"data": [self._os_oge("1", "tr", oz)]}, v)],
                         ["1"])
        v.dizi_kimlik_kesin = True                     # kimlik Kodi kitaplığından: dizinin kendisi
        self.assertEqual(OpenSubtitles("k")._adaylar({"data": [self._os_oge("1", "tr", oz)]}, v), [])
        # Düzenli temiz ad güvenilir ve yıl adı bozmaz
        xbmc.oynatici.update({"oynuyor": True, "sure": 10144.0, "yol": "C:\\Filmler\\Interstellar (2014).mkv",
                              "etiket": {"title": "Interstellar (2014).mkv"}})
        v = video_modulu.oynatilan(xbmc, xbmcvfs, karma=False)
        xbmc.oynatici["oynuyor"] = False
        self.assertEqual((v.ad, v.yil, v.ad_kaynagi), ("Interstellar", 2014, "dosya"))
        self.assertTrue(ad_guvenilir(v))
        self.assertEqual(video_modulu.yapim_adi("Charlotte's Web (2006)"), "Charlotte's Web")
        self.assertEqual(surum.ayristir("Charlottes.Web.2006.1080p.WEB-DL.x264-GRP.mkv").baslik, "Charlottes Web")
        # Elle aramada oynatılan dosyanın adı aranmaz
        e = video_modulu.elle_aramadan("Dune Part Two")
        e.dosya_adi = "Dune.1984.1080p.BluRay.x264-GRP.mkv"
        self.assertNotIn(e.dosya_adi, _adla_aramalar(e))

    def test_sonraki_sorgu_olmazsa_eldeki_doner(self):
        v = _video(tur="film", dosya_adi=self.YIFY, karma="abc")
        v.baslik, v.ad_kaynagi = v.surum.baslik, "surum"

        def sahte(yontem, adres, basliklar=None, sorgu=None, **k):
            if dict(sorgu).get("query", "").endswith(".mp4"):
                return 200, {"data": [self._os_oge("2", "en", self.ANDHA, karma=True)]}, {}
            raise ag.AgHatasi("zaman aşımı")
        with mock.patch("d2.ag.istek", sahte):
            a = OpenSubtitles("k").ara(v, ["tr", "en"])
        self.assertEqual([(x.kimlik, x.karma_tuttu) for x in a], [("2", False)])

    def test_site_etiketi_ve_teknik_sozcuk_grup_degil(self):
        for ad in ("Show.S01E01.720p.HDTV.x264[eztv]", "Movie.2019.1080p.BluRay.x264.Dual.Audio",
                   "Movie.2019.1080p.BluRay.DTS-HD.MA.5.1", "Movie (2019) (Criterion Collection) 1080p BluRay x264",
                   "Movie.2019.1080p.WEBRip.x264.Latino"):
            self.assertIsNone(surum.ayristir(ad).grup, ad)
        p, kt = puan.puanla(self.YIFY, ["Interstellar.2014.1080p.BluRay.x264-[YTS.AG]"])     # YIFY = YTS ailesi
        self.assertTrue(puan.uyumlu_mu(p, kt))

    def test_subdl_baska_yapimi_eslestirirse_atilir(self):
        andha = {"type": "movie", "name": "Andhadhun", "year": 2018, "imdb_id": "tt8108198", "tmdb_id": 534780}
        inter = {"type": "movie", "name": "Interstellar", "year": 2014, "imdb_id": "tt0816692", "tmdb_id": 157336}
        alt = [{"release_name": "Bir.Altyazi.1080p", "language": "TR", "url": "/subtitle/1-1.zip"}]
        sorgular = []

        def sahte(yontem, adres, basliklar=None, sorgu=None, **k):
            sorgular.append(dict(sorgu))
            sonuc = andha if "file_name" in sorgu else inter
            return 200, {"status": True, "results": [sonuc], "subtitles": alt}, {}
        v = _video(tur="film", dosya_adi=self.YIFY, karma="abc")
        v.baslik, v.ad_kaynagi, v.yil = v.surum.baslik, "surum", 2014
        s = SubDL("k")
        with mock.patch("d2.ag.istek", sahte):
            a = s.ara(v, ["tr"])
        self.assertEqual(["file_name" in q for q in sorgular], [True, False])
        self.assertEqual((len(a), s.elenen, s.elenen_adlari, s.yapim), (1, 1, ["Andhadhun (2018)"], "Interstellar (2014)"))
        # Kimlik varsa kimlik karar verir; bilgi yoksa ya da ad güvenilmezse yanıt olduğu gibi kullanılır
        from d2.siteler.subdl import yapim_karari
        v2 = _video(tur="film", baslik="Yıldızlararası", imdb="tt0816692", dosya_adi="a.mkv")
        self.assertIs(yapim_karari({"results": [inter]}, v2), True)
        self.assertIs(yapim_karari({"results": [andha]}, v2), False)
        self.assertIsNone(yapim_karari({"subtitles": alt}, v))
        self.assertIsNone(yapim_karari({"results": [andha]}, _video(tur="film", dosya_adi="video_0001.mp4")))
        # Film videosuna dizi, diziye başka dizi
        self.assertIs(yapim_karari({"results": [dict(inter, type="tv", imdb_id="")]}, v), False)
        d = _video(tur="dizi", sezon=1, bolum=5, dosya_adi="Ornek.S01E05.1080p.WEB-DL.x264-NTb.mkv")
        d.dizi_adi = d.surum.baslik
        self.assertIs(yapim_karari({"results": [{"type": "tv", "name": "Ornek", "year": 2011}]}, d), True)
        # Yalnız ad farkı karar verdirmez: SubDL animede İngilizce adı yazar
        aot = _video(tur="dizi", sezon=1, bolum=3,
                     dosya_adi="Shingeki no Kyojin - S01E03 - A Dim Light in the Darkness of Despair (1).mkv")
        aot.dizi_adi, aot.ad_kaynagi = aot.surum.baslik, "dosya"
        self.assertIsNone(yapim_karari({"results": [{"type": "tv", "name": "Attack on Titan", "year": 2013}]}, aot))
        self.assertIs(yapim_karari({"results": [{"type": "movie", "name": "Attack on Titan", "year": 2015}]}, aot),
                      False)                                          # diziye film
        # Filmde ad da yıl da tutmuyorsa başka yapım; yıl tutuyorsa (başka dilde ad olabilir) karar yok
        self.assertIs(yapim_karari({"results": [dict(andha, imdb_id="", tmdb_id="")]}, v), False)
        self.assertIsNone(yapim_karari({"results": [{"type": "movie", "name": "Yildizlararasi", "year": 2014}]}, v))
        # Adın içindeki sayı yıl sanılırsa fark sayılmaz; aynı adlı başka yılın filmi yine başka yapımdır
        br = _video(tur="film", dosya_adi="Blade.Runner.2049.1080p.BluRay.x264-SPARKS.mkv")
        br.baslik, br.ad_kaynagi = br.surum.baslik, "surum"
        self.assertIs(yapim_karari({"results": [{"type": "movie", "name": "Blade Runner 2049", "year": 2017}]}, br), True)
        self.assertIs(yapim_karari({"results": [{"type": "movie", "name": "Blade Runner", "year": 1982}]}, br), False)
        # Bozuk yanıt karar verdirmez
        for bozuk in ({"results": "x"}, {"results": [1, None]}, [1, 2], None, {"results": [{"year": float("inf")}]}):
            self.assertIsNone(yapim_karari(bozuk, v))

    def test_subdl_kimlikle_aramada_yapim_denetlenmez_atilan_bir_kez_sayilir(self):
        andha = {"type": "movie", "name": "Andhadhun", "year": 2018, "imdb_id": "tt8108198"}
        alt = [{"release_name": "Interstellar.2014.1080p.BluRay.x264-SPARKS", "language": "TR", "url": "/subtitle/1-1.zip"}]

        def sahte(yontem, adres, basliklar=None, sorgu=None, **k):
            return 200, {"status": True, "results": [andha], "subtitles": alt, "totalPages": float("inf")}, {}
        v = _video(tur="film", dosya_adi=self.YIFY, imdb="tt0816692")
        v.baslik, v.ad_kaynagi = v.surum.baslik, "surum"
        s = SubDL("k")
        with mock.patch("d2.ag.istek", sahte):
            self.assertEqual(len(s.ara(v, ["tr", "en"])), 1)       # kimlikle aranınca SubDL'in yanıtı esas
            v.imdb = ""                                              # kimliksiz: dosya adı ve ad sorgusu başka yapım
            self.assertEqual(s.ara(v, ["tr", "en"]), [])
        self.assertEqual((s.elenen, s.elenen_adlari), (1, ["Andhadhun (2018)"]))   # iki sorguda aynı altyazı: 1

    def test_subdl_ek_sayfa_ve_yan_dizi(self):
        inter = {"type": "movie", "name": "Interstellar", "year": 2014}
        andha = {"type": "movie", "name": "Andhadhun", "year": 2018}
        en = [{"release_name": f"Interstellar.2014.{i}.1080p", "language": "EN", "url": f"/subtitle/e-{i}.zip"}
              for i in range(30)]
        tr = [{"release_name": "Interstellar.2014.TR.1080p", "language": "TR", "url": "/subtitle/t-1.zip"}]

        def sahte(yontem, adres, basliklar=None, sorgu=None, **k):
            if sorgu.get("languages") == "TR":                   # ek sayfa başka yapımı eşleştirmiş
                return 200, {"status": True, "results": [andha], "subtitles": tr}, {}
            return 200, {"status": True, "results": [inter], "subtitles": en}, {}
        v = _video(tur="film", dosya_adi=self.YIFY)
        v.baslik, v.ad_kaynagi, v.yil = v.surum.baslik, "surum", 2014
        with mock.patch("d2.ag.istek", sahte):
            a = SubDL("k").ara(v, ["tr", "en"])
        self.assertEqual((len(a), {x.dil for x in a}), (30, {"en"}))
        # Dizide yan dizi başka yapımdır; baştaki ek ("Marvels") ve romaji/İngilizce ad farkı karar verdirmez
        from d2.siteler.subdl import yapim_karari
        svu = _video(tur="dizi", sezon=1, bolum=1, dosya_adi="Law.and.Order.SVU.S01E01.720p.WEB-DL.x264-NTb.mkv")
        svu.dizi_adi, svu.ad_kaynagi = svu.surum.baslik, "surum"
        self.assertIs(yapim_karari({"results": [{"type": "tv", "name": "Law & Order"}]}, svu), False)
        self.assertIs(yapim_karari({"results": [{"type": "tv", "name": "Law & Order: SVU"}]}, svu), True)
        dd = _video(tur="dizi", sezon=1, bolum=1, dosya_adi="Marvels.Daredevil.S01E01.720p.WEB-DL.x264-NTb.mkv")
        dd.dizi_adi, dd.ad_kaynagi = dd.surum.baslik, "surum"
        self.assertIsNone(yapim_karari({"results": [{"type": "tv", "name": "Daredevil"}]}, dd))

    def test_os_kimliksiz_videoda_yapim_sonuclardan_ogrenilir(self):
        bolum = {"feature_type": "Episode", "parent_title": "Attack on Titan", "parent_imdb_id": 2560140,
                 "parent_tmdb_id": 1429, "season_number": 1, "episode_number": 3}
        baska = dict(bolum, parent_title="Straight Title Robot Anime", parent_imdb_id=999, parent_tmdb_id=998)
        istekler = []

        def sahte(yontem, adres, basliklar=None, sorgu=None, **k):
            q = dict(sorgu or [])
            istekler.append(q)
            if q.get("parent_imdb_id") == "2560140":               # Türkçe yalnız yapımın kimliğiyle gelir
                return 200, {"data": [self._os_oge("1", "tr", bolum, ad="[Anime Time] Attack On Titan - 03"),
                                      self._os_oge("2", "en", bolum, ad="Shingeki no Kyojin - 03")]}, {}
            return 200, {"data": [self._os_oge("2", "en", bolum, ad="Shingeki no Kyojin - 03"),
                                  self._os_oge("3", "en", bolum, ad="Shingeki no Kyojin - 03 [1080p]"),
                                  self._os_oge("4", "en", baska, ad="[HorribleSubs] Straight Title Robot Anime - 03")]}, {}
        v = _video(tur="dizi", sezon=1, bolum=3,
                   dosya_adi="Shingeki no Kyojin - S01E03 - A Dim Light in the Darkness of Despair (1).mkv")
        v.dizi_adi, v.ad_kaynagi = v.surum.baslik, "dosya"
        os_ = OpenSubtitles("k")
        with mock.patch("d2.ag.istek", sahte):
            a = os_.ara(v, ["tr", "en"])
        # Kimlikle gelenler eklenir; adla gelenler asıl videoyla denetlenir (yanlış oy doğru satırı silemesin diye)
        self.assertEqual([(x.kimlik, x.dil) for x in a], [("1", "tr"), ("2", "en"), ("3", "en"), ("4", "en")])
        self.assertEqual(os_.yapim, "Attack on Titan")
        self.assertEqual([("query" in q, q.get("parent_imdb_id")) for q in istekler], [(True, None), (False, "2560140")])
        self.assertEqual((v.imdb, v.tmdb), ("", ""))                  # asıl video değişmez (siteler paylaşır)
        # Tek sonuç, bölünmüş oy ya da yalnız karma eşleşmesi yapımı öğretmez; filmde yılı tutmayan oy vermez
        from d2.siteler.opensubtitles import _ortak_yapim

        def oge(kimlik, oz, karma=False):
            return (Aday("os", kimlik, "en", ["x"], karma_tuttu=karma), oz)
        self.assertIsNone(_ortak_yapim([oge("1", bolum)], v, True))
        self.assertIsNone(_ortak_yapim([oge("1", bolum), oge("2", baska)], v, True))
        self.assertIsNone(_ortak_yapim([oge("1", bolum, True), oge("2", bolum, True)], v, True))
        self.assertEqual(_ortak_yapim([oge("1", bolum), oge("2", bolum), oge("3", baska)], v, True)["imdb"], "2560140")
        f = _video(tur="film", dosya_adi=self.YIFY)
        f.baslik, f.yil = f.surum.baslik, 2014
        eski = dict(self.INTER, year=2007, imdb_id=111)
        self.assertEqual(_ortak_yapim([oge("1", self.INTER), oge("2", self.INTER), oge("3", eski), oge("4", eski),
                                       oge("5", eski)], f, False)["ad"], "Interstellar (2014)")
        # Aynı yapımın iki kaydı (ortak IMDb) bir sayılır; adı videonunkini tutan iki yapım varsa karar yok
        kopya = dict(bolum, parent_feature_id=77, parent_tmdb_id=None)
        self.assertEqual(_ortak_yapim([oge("1", bolum), oge("2", kopya), oge("3", baska)], v, True)["imdb"], "2560140")
        ch = _video(tur="dizi", sezon=1, bolum=3, dosya_adi="Charmed.2018.S01E03.720p.HDTV.x264-KILLERS.mkv")
        ch.dizi_adi, ch.ad_kaynagi = ch.surum.baslik, "surum"
        eski_ch = dict(bolum, parent_title="Charmed", parent_imdb_id=158552, parent_tmdb_id=1981)
        yeni_ch = dict(bolum, parent_title="Charmed", parent_imdb_id=6394324, parent_tmdb_id=78881)
        self.assertIsNone(_ortak_yapim([oge("1", eski_ch), oge("2", eski_ch), oge("3", eski_ch), oge("4", yeni_ch)],
                                       ch, True))

    def test_os_ogrenme_siniri_ikinci_sorgu_ve_hata(self):
        bolum = {"feature_type": "Episode", "parent_title": "Attack on Titan", "parent_imdb_id": 2560140,
                 "season_number": 1, "episode_number": 3}
        v = _video(tur="dizi", sezon=1, bolum=3,
                   dosya_adi="Shingeki no Kyojin - S01E03 - A Dim Light in the Darkness of Despair (1).mkv")
        v.dizi_adi, v.ad_kaynagi = v.surum.baslik, "dosya"
        tr = self._os_oge("9", "tr", bolum, ad="[Anime Time] Attack On Titan - 03")

        def yanit(dosya_adiyla, adla, kimlikle):
            def sahte(yontem, adres, basliklar=None, sorgu=None, **k):
                q = dict(sorgu or [])
                istekler.append(q)
                if "parent_imdb_id" in q:
                    return kimlikle(q)
                return 200, {"data": dosya_adiyla if q.get("query", "").endswith(".mkv") else adla}, {}
            return sahte
        tek = [self._os_oge("1", "en", bolum, ad="Shingeki no Kyojin - 03")]
        iki = tek + [self._os_oge("2", "en", bolum, ad="Shingeki no Kyojin - 03 [1080p]")]
        # Dosya adıyla tek sonuç: yapım sıradaki adla sorgudan öğrenilir, dosya adıyla gelen sonuç kalır
        istekler = []
        with mock.patch("d2.ag.istek", yanit(tek, iki, lambda q: (200, {"data": [tr]}, {}))):
            a = OpenSubtitles("k").ara(v, ["tr", "en"])
        self.assertEqual([x.kimlik for x in a], ["9", "1"])
        self.assertEqual([("query" in q, "parent_imdb_id" in q) for q in istekler], [(True, False), (True, False),
                                                                                     (False, True)])
        # Kimlikle arama hata verirse ya da süre dolmak üzereyse adla gelenler olduğu gibi döner
        def hata(q):
            raise ag.AgHatasi("zaman aşımı")
        istekler = []
        with mock.patch("d2.ag.istek", yanit(iki, iki, hata)):
            self.assertEqual([x.kimlik for x in OpenSubtitles("k").ara(v, ["tr", "en"])], ["1", "2"])
        istekler = []
        with mock.patch("d2.ag.istek", yanit(iki, iki, lambda q: (200, {"data": [tr]}, {}))), \
                mock.patch("d2.siteler.opensubtitles.EK_ARAMA_SINIRI", -1):
            self.assertEqual([x.kimlik for x in OpenSubtitles("k").ara(v, ["tr", "en"])], ["1", "2"])
        self.assertFalse(any("parent_imdb_id" in q for q in istekler))
        # Adı güvenilmeyen videoda öğrenilmez
        u = _video(tur="dizi", sezon=1, bolum=3, dosya_adi="video_0001.mp4")
        istekler = []
        with mock.patch("d2.ag.istek", yanit(iki, iki, lambda q: (200, {"data": [tr]}, {}))):
            OpenSubtitles("k").ara(u, ["tr", "en"])
        self.assertFalse(any("parent_imdb_id" in q for q in istekler))

    def test_bozuk_yanit_aramayi_dusurmez(self):
        v = _video(tur="dizi", sezon=1, bolum=5, dosya_adi="Ornek.S01E05.1080p.WEB-DL.x264-NTb.mkv")
        bozuk = {"data": [1, {"attributes": 5}, {"attributes": {"files": 3}},
                          {"id": "9", "attributes": {"language": "tr", "release": "Ornek.S01E05.720p", "fps": "x",
                                                     "uploader": "ad", "download_count": "çok",
                                                     "feature_details": {"season_number": "bir", "episode_number": 5},
                                                     "files": [{"file_id": 9, "file_name": "a.srt"}]}}]}
        a = OpenSubtitles("k")._adaylar(bozuk, v)
        self.assertEqual([(x.kimlik, x.fps, x.yukleyen, x.indirme_sayisi) for x in a], [("9", None, "", 0)])
        bozuk["total_count"], bozuk["data"][3]["attributes"]["fps"] = 999, 10 ** 400        # dolu sayfa, dev sayı
        bozuk["data"].append({"id": "10", "attributes": {"language": "tr", "release": 12345, "files": [
            {"file_id": 10, "file_name": 678}]}})
        with mock.patch("d2.ag.istek", lambda *a_, **k: (200, bozuk, {})):
            self.assertEqual(sorted(x.kimlik for x in OpenSubtitles("k").ara(v, ["tr", "en"])), ["10", "9"])
        s = SubDL("k")
        for veri in ({"status": True, "results": "x", "subtitles": 5}, [1, 2],
                     {"status": True, "results": [1, None], "subtitles": [1, "a", {"url": 3}]}):
            with mock.patch("d2.ag.istek", lambda *a_, **k: (200, veri, {})):
                self.assertEqual(s.ara(v, ["tr", "en"]), [])

    def test_sdh_adindan_anlasilir(self):
        from d2.siteler import adda_sdh
        self.assertTrue(adda_sdh(["Film.2014.1080p.BluRay.x264-GRP.en.hi.srt"]))
        self.assertTrue(adda_sdh(["Turkish.SDH.srt"]))
        self.assertTrue(adda_sdh(["x", "Film [SDH].srt"]))
        self.assertFalse(adda_sdh(["Hi.Bye.Mama.S01E01.1080p.NF.WEB-DL-GRP"]))      # adın parçası
        self.assertFalse(adda_sdh(["Film.2014.1080p.BluRay.x264-GRP.srt"]))
        self.assertTrue(_aday("os", "1", "tr", "Film.2014.1080p-GRP.HI.srt").sdh)

    def test_os_dizide_dizi_ozelliginin_kimligi_dizinin_kendisinin(self):
        from d2.siteler.opensubtitles import _yapim_durumu
        d = _video(tur="dizi", sezon=1, bolum=5, imdb="tt0944947", dosya_adi="Ornek.S01E05.1080p.WEB-DL.x264-NTb.mkv")
        d.dizi_kimlik_kesin = True
        self.assertEqual(_yapim_durumu({"feature_type": "Tvshow", "imdb_id": 944947, "title": "Ornek"}, d), "kimlik")
        self.assertEqual(_yapim_durumu({"feature_type": "Tvshow", "imdb_id": 123, "title": "Ornek"}, d), "farkli")
        d.tmdb = "1399"
        self.assertEqual(_yapim_durumu({"feature_type": "Tvshow", "imdb_id": 123, "tmdb_id": 1399, "title": "X"}, d),
                         "kimlik")
        self.assertEqual(_yapim_durumu({"feature_type": "Movie", "imdb_id": 123, "tmdb_id": 1399, "title": "Ornek"}, d),
                         "farkli")                  # diziye film: film numarası dizininkiyle karşılaştırılmaz
        d.tmdb = ""
        d.dizi_kimlik_kesin = False                 # kimlik bölümün de olabilir: tutmaması karar verdirmez
        self.assertNotEqual(_yapim_durumu({"feature_type": "Tvshow", "imdb_id": 123, "title": "Ornek"}, d), "farkli")

    def test_adi_baska_filmi_anlatan_altyazi_atilir(self):
        v = _video(tur="film", dosya_adi=self.YIFY)
        v.baslik, v.ad_kaynagi, v.yil = v.surum.baslik, "surum", 2014
        adaylar = [_aday("os", "1", "tr", "2307 Winter's Dream 2016 720p BRRip 750 MB - iExTV"),
                   _aday("os", "2", "tr", "Yıldızlararası 2160p BluRay"),             # yerel ad, yıl yok: kalır
                   _aday("os", "3", "tr", "Yildizlararasi.2014.1080p.BluRay"),         # yerel ad, yıl tutuyor
                   _aday("os", "4", "tr", "[ www.Site.com ] Interstellar.2016.720p"),  # ad tutuyor
                   _aday("subdl", "5", "en", "Dunkirk.2017.1080p.BluRay.x264-SPARKS"),
                   _aday("os", "6", "tr", "Turkish.srt")]
        kalan, atilan = yapim.baska_filmin_adlari(adaylar, v)
        self.assertEqual([a.kimlik for a in kalan], ["2", "3", "4", "6"])
        self.assertEqual(atilan, ["2307 Winter's Dream (2016)", "Dunkirk (2017)"])
        # Dizide ya da adı güvenilmeyen videoda karar yok
        self.assertEqual(len(yapim.baska_filmin_adlari(adaylar, _video(tur="film", dosya_adi="video_0001.mp4"))[0]), 6)
        d = _video(tur="dizi", sezon=1, bolum=5, dosya_adi="Ornek.S01E05.1080p.WEB-DL.x264-NTb.mkv")
        self.assertEqual(len(yapim.baska_filmin_adlari(adaylar, d)[0]), 6)

    def test_filmde_dizi_bolumu_adli_altyazi_atilir(self):
        # SubDL'de bir dizinin bölüm altyazıları başka yapıma yüklenmiş (The Simpsons 12. sezon, Attack on Titan diye).
        # Filme gelirse bölüm denetimi yok: bölüm yazan ve videonun adını tutmayan ad başka yapımındır. Yıl bilinmese de.
        v = _video(tur="film", dosya_adi="Interstellar.1080p.BluRay.x264.mkv")
        v.baslik, v.ad_kaynagi = v.surum.baslik, "surum"
        adaylar = [_aday("subdl", "1", "tr", "The.Simpsons.S12E03.WEB-DL.DSNP"),
                   _aday("subdl", "2", "tr", "Interstellar.2014.1080p.BluRay"),
                   _aday("subdl", "3", "tr", "Interstellar.S01E01.Special"),          # adı tutuyor: kalır
                   _aday("subdl", "4", "tr", "Doctor.Who.S00E150.Special"),           # S00 özel bölüm: karar yok
                   _aday("subdl", "5", "tr", "Turkish.srt")]
        kalan, atilan = yapim.baska_filmin_adlari(adaylar, v)
        self.assertEqual(([a.kimlik for a in kalan], atilan), (["2", "3", "4", "5"], ["The Simpsons S12E03"]))

    def test_forced_ve_fragman_altyazisi_listeye_girmez(self):
        from d2.siteler import tam_altyazi_degil
        self.assertTrue(tam_altyazi_degil(["Yıldızlararası 2160p BluRay FORCED"]))
        self.assertTrue(tam_altyazi_degil(["Interstellar (2014) Official Teaser"]))
        self.assertTrue(tam_altyazi_degil(["Film.2019.1080p.WEB-DL.Foreign.Parts.Only"]))
        self.assertFalse(tam_altyazi_degil(["Interstellar.2014.1080p.BluRay.x264.YIFY"]))
        self.assertFalse(tam_altyazi_degil(["Trailer.Park.Boys.S01E01.720p"], "Trailer Park Boys"))
        self.assertTrue(tam_altyazi_degil(["Interstellar (2014) [2-Disc IMAX Edition] Blu-ray AVC DD5.DISC_2_BONUS"]))
        self.assertTrue(tam_altyazi_degil(["Interstellar.2014.EXTRA.Bluray.720p.DTS-HD.x264"]))
        self.assertTrue(tam_altyazi_degil(["Film.2019.Making.Of.1080p"]))
        self.assertFalse(tam_altyazi_degil(["Interstellar.2014.EXTENDED.1080p"]))
        self.assertFalse(tam_altyazi_degil(["Film.2019.1080p.NON-FORCED"]))
        d = _video(tur="dizi", sezon=1, bolum=5, dosya_adi="Ornek.S01E05.1080p.WEB-DL.x264-NTb.mkv")
        self.assertFalse(tam_altyazi_degil(["Ornek.S01E05.The.Teaser.1080p"], d))     # bölüm adı
        self.assertTrue(tam_altyazi_degil(["Ornek.S01E05.FORCED.1080p"], d))
        self.assertFalse(tam_altyazi_degil([12345, None], d))
        inter = {"feature_type": "Movie", "title": "Interstellar", "year": 2014}
        veri = {"data": [self._os_oge("1", "tr", inter, ad="Yildizlararasi 2160p BluRay FORCED"),
                         self._os_oge("2", "tr", inter, ad="Interstellar (2014) Official Teaser"),
                         self._os_oge("3", "tr", inter, ad="Interstellar.2014.720p.BluRay.x264-DAA")]}
        veri["data"].append(self._os_oge("4", "tr", inter, ad="Interstellar.2014.1080p"))
        veri["data"][3]["attributes"]["foreign_parts_only"] = True
        v = _video(tur="film", dosya_adi=self.YIFY)
        self.assertEqual([x.kimlik for x in OpenSubtitles("k")._adaylar(veri, v)], ["3"])

    def test_ad_ve_kimlik_bicimleri(self):
        from d2.siteler import sade_ad
        from d2.siteler.opensubtitles import _kimlik
        self.assertEqual(sade_ad("YILDIZLARARASI"), sade_ad("Yıldızlararası"))
        self.assertEqual(sade_ad("Law & Order: SVU"), "law and order svu")
        self.assertEqual(sade_ad("Amélie"), "amelie")
        self.assertEqual({_kimlik(x) for x in ("tt0816692", 816692, "816692.0", 816692.0, "0816692")}, {"816692"})
        self.assertEqual(_kimlik(None), "")
        self.assertEqual({_kimlik(x) for x in (float("nan"), float("inf"), True, "tt", "abc")}, {""})

    def test_subdl_imzali_adres_saklanmaz_paket_acilir(self):
        v = _video(tur="dizi", sezon=1, bolum=5)
        veri = {"status": True, "subtitles": [
            {"release_name": "Ornek.S01E05.WEB", "language": "TR", "url": "/subtitle/1-2.zip?api_key=GIZLI",
             "season": 1, "episode": 5, "hi": True},
            {"release_name": "Ornek.S01.WEB", "language": "EN", "url": "/subtitle/3-4.zip?api_key=GIZLI",
             "season": 1, "full_season": True, "episode_from": 1, "episode_end": 10,
             "unpack_files": [{"name": "Ornek.S01E05.srt", "season": 1, "episode": 5, "url": "/subtitle/3/44?api_key=G"}]},
            {"release_name": "Ornek.S01E07.WEB", "language": "TR", "url": "/subtitle/5-6.zip", "season": 1, "episode": 7},
        ]}
        a = SubDL("k")._adaylar(veri, v)
        self.assertEqual([x.indirme["yol"] for x in a], ["/subtitle/1-2.zip", "/subtitle/3/44"])
        self.assertTrue(a[0].sdh)
        self.assertFalse(a[1].paket)
        self.assertNotIn("GIZLI", json.dumps([x.kayit() for x in a]))

    def test_subdl_anahtari_baska_sunucuya_gitmez(self):
        # İndirme adresi "https://dl.subdl.com" + yol diye kurulur ve anahtar başlıkta gider. Yanıttaki "url" "/" ile
        # başlamıyorsa ("@baska.sunucu/x.zip", ".baska.sunucu/x.zip") sunucu adı değişiyor, anahtar oraya gidiyordu.
        v = _video(tur="film", baslik="Ornek", imdb="tt1")
        veri = {"status": True, "subtitles": [
            {"release_name": "Ornek.2019.A", "language": "TR", "url": "@baska.sunucu.invalid/a.zip?api_key=GIZLI"},
            {"release_name": "Ornek.2019.B", "language": "TR", "url": ".baska.sunucu.invalid/b.zip"},
            {"release_name": "Ornek.2019.D", "language": "TR", "url": "https://[bozuk/d.zip"},
            {"release_name": "Ornek.2019.C", "language": "TR", "url": "/subtitle/1-2.zip?api_key=GIZLI"}]}
        self.assertEqual([x.indirme["yol"] for x in SubDL("k")._adaylar(veri, v)], ["/subtitle/1-2.zip"])
        istekler = []

        def sahte(yontem, adres, basliklar=None, **k):
            istekler.append(adres)
            return 200, b"x", {}
        with mock.patch("d2.ag.istek", sahte):            # eski kayıttan (arama önbelleği) gelen yol da indirilmez
            for yol in ("@baska.sunucu.invalid/a.zip", ".baska.sunucu.invalid/b.zip", ""):
                with self.assertRaises(SiteHatasi):
                    SubDL("k").indir(Aday("subdl", yol, "tr", ["x"], indirme={"yol": yol}))
            SubDL("k").indir(Aday("subdl", "/subtitle/1-2.zip", "tr", ["x"], indirme={"yol": "/subtitle/1-2.zip"}))
        self.assertEqual(istekler, ["https://dl.subdl.com/subtitle/1-2.zip"])

    def test_subsource_sezon_ve_bolum(self):
        v = _video(tur="dizi", sezon=1, bolum=5, dizi_adi="Ornek", imdb="tt1")

        def sahte(yontem, adres, basliklar=None, sorgu=None, **k):
            self.assertEqual(basliklar, {"X-API-Key": "k"})
            if "movies/search" in adres:
                return 200, {"data": [{"movieId": 77, "title": "Ornek", "season": 1}]}, {}
            return 200, {"data": [
                {"subtitleId": 1, "language": "turkish", "releaseInfo": ["Ornek.S01E05.WEB"]},
                {"subtitleId": 2, "language": "turkish", "releaseInfo": ["Ornek.S01E06.WEB"]},
                {"subtitleId": 3, "language": "turkish", "releaseInfo": ["Ornek.S01.COMPLETE"]},
                {"subtitleId": 4, "language": "turkish", "releaseInfo": ["x"], "foreignParts": True}]}, {}
        with mock.patch("d2.ag.istek", sahte):
            a = SubSource("k").ara(v, ["tr"])
        self.assertEqual([(x.kimlik, x.paket) for x in a], [("1", False), ("3", True)])

    def test_subsource_saklanan_yapim_ayni_adli_baska_yilin_filmine_verilmez(self):
        # Kimliksiz filmde bulunan yapım bir hafta "ad" anahtarıyla saklanıyordu: "Dune" (1984) arandıktan sonra "Dune"
        # (2021) aynı kaydı alıyor, eski filmin altyazıları listeleniyordu.
        aramalar = []

        def sahte(yontem, adres, basliklar=None, sorgu=None, **k):
            if "movies/search" in adres:
                aramalar.append(sorgu["q"])
                return 200, {"data": [{"movieId": 1984, "title": "Dune", "releaseYear": 1984},
                                      {"movieId": 2021, "title": "Dune", "releaseYear": 2021}]}, {}
            return 200, {"data": [{"subtitleId": sorgu["movieId"], "language": "turkish",
                                   "releaseInfo": [f"Dune.{sorgu['movieId']}.1080p.BluRay"]}]}, {}
        depo = kayit.Depo(tempfile.mkdtemp())
        with mock.patch("d2.ag.istek", sahte):
            s = SubSource("k", depo)
            eski = s.ara(_video(tur="film", baslik="Dune", yil=1984), ["tr"])
            yeni = s.ara(_video(tur="film", baslik="Dune", yil=2021), ["tr"])
            s.ara(_video(tur="film", baslik="Dune", yil=2021), ["tr"])          # aynı film: saklanan kullanılır
        shutil.rmtree(depo.klasor)
        self.assertEqual(([a.kimlik for a in eski], [a.kimlik for a in yeni]), (["1984"], ["2021"]))
        self.assertEqual(len(aramalar), 2)

    def test_subsource_cok_dosyali_altyazi_paket_sayilir(self):
        # SubSource'taki 25 bölümlük Türkçe paketin adı yalnız ilk dosyayı yazıyor ("..._01_..."): 3. bölümde
        # "başka bölüm" diye atılıyordu. Tek dosyalıysa (ya da sayı bilinmiyorsa) gerçekten başka bölümdür.
        v = _video(tur="dizi", sezon=1, bolum=3, dizi_adi="Shingeki no Kyojin")
        ad = "[Coalgirls]_Shingeki_no_Kyojin_01_(1280x720_Blu-ray_FLAC)_[3CB81308]"

        def sahte(yontem, adres, basliklar=None, sorgu=None, **k):
            if "movies/search" in adres:
                return 200, {"data": [{"movieId": 77, "title": "Attack on Titan", "alternateTitle": "Shingeki no Kyojin",
                                       "season": 1}]}, {}
            return 200, {"data": [
                {"subtitleId": 1, "language": "turkish", "releaseInfo": [ad], "files": 26},
                {"subtitleId": 2, "language": "turkish", "releaseInfo": [ad], "files": 1},
                {"subtitleId": 3, "language": "turkish", "releaseInfo": [ad]},
                {"subtitleId": 4, "language": "turkish", "releaseInfo": [ad], "files": [ad + ".ass", "Oku.txt"]}]}, {}
        with mock.patch("d2.ag.istek", sahte):
            a = SubSource("k").ara(v, ["tr"])
        self.assertEqual([(x.kimlik, x.paket, x.bolum_kesin) for x in a], [("1", True, False), ("4", True, False)])

    def test_dil_kodlari_her_dil_icin(self):
        # Canlı yoklama (Interstellar): SubSource'ta "chinese" yok, Çince GB ve Big5 adlarıyla; OpenSubtitles'ta ikisi
        from d2.siteler import DIL_ADLARI
        from d2.siteler.opensubtitles import _os_dili
        from d2.siteler.subdl import _subdl_dili
        from d2.siteler.subsource import _ss_dilleri
        self.assertEqual(_ss_dilleri("zh"), ["chinese_bg_code", "big_5_code"])
        self.assertEqual(_ss_dilleri("pt-BR"), ["brazilian_portuguese"])
        self.assertEqual(_ss_dilleri("fa"), ["farsi_persian"])
        self.assertEqual(_os_dili("zh"), "zh-cn,zh-tw")
        for kod in DIL_ADLARI:                      # her dilin site adı ya da kodu, sitenin döndürdüğü dil olarak geri tanınır
            for site_dili in _ss_dilleri(kod) + _os_dili(kod).split(",") + [_subdl_dili(kod)]:
                self.assertEqual(dil_kodu(site_dili), kod, (kod, site_dili))

    def test_servisin_yukledigi_dosyada_dil_eki(self):
        # Servis altyazıyı Player.setSubtitles ile yükler; Kodi dili dosya adından okur: " .-" ile ayrılmış parçalar,
        # sondan başa, ilk tanıdığı dil kodu (Util.cpp, GetExternalStreamDetailsFromFilename). "os-..." Osetçe sanılıyordu.
        from d2.siteler import DIL_ADLARI
        iso1 = set(DIL_ADLARI) | {"os", "pb"}                # Kodi'nin tanıdığı kodlardan ilgili olanlar

        def kodi_dili(ad):
            import re as re_
            for p in reversed(re_.split(r"[ .\-]+", os.path.splitext(ad)[0])):
                if p.lower() in iso1:
                    return p.lower()
            return ""
        self.assertEqual(kodi_dili("os-fa3760cab4.1794818637.srt"), "os")       # eski ad: Osetçe
        for kod in DIL_ADLARI:
            ek = kayit.kodi_dil_eki(kod)
            self.assertEqual(kodi_dili(f"AI.os_fa3760cab4_1794818637.{ek}.srt"), "pb" if kod == "pt-BR" else kod)
            self.assertEqual(kodi_dili(f"ee18e436c6d66aa9.{ek}.srt"), "pb" if kod == "pt-BR" else kod)
        # İndirilen altyazının yolu dil ekli; eski (eksiz) adla indirilmiş dosya da bulunur
        durum_ = mock.Mock()
        kok = os.path.join(tempfile.mkdtemp(), "indirilen")
        os.makedirs(kok, exist_ok=True)
        durum_.yol = lambda *p: os.path.join(kok, *p[1:])
        v = _video(tur="film", baslik="Ornek", dosya_adi="Ornek.2019.mkv")
        a = Aday("os", "1", "de", ["Ornek.2019"])
        self.assertTrue(arama._indirilen_yolu(durum_, a, v, ".srt").endswith(".de.srt"))
        eski = arama._indirilen_kok(durum_, a, v) + ".srt"
        open(eski, "w").close()
        self.assertEqual(arama.onceki_indirme(durum_, a, v), (eski, a))

    def test_subdl_sezon_ve_bolum_v2_adiyla_gider(self):
        # v2 "season_number"/"episode_number" adlarını yok sayıyor: bütün dizinin 13 sayfası geliyordu
        sorgular = []

        def sahte(yontem, adres, basliklar=None, sorgu=None, **k):
            sorgular.append(dict(sorgu))
            return 200, {"status": True, "results": [{"type": "tv", "name": "Ornek"}], "subtitles": [
                {"release_name": "Ornek.S01E05.WEB", "language": "TR", "url": "/subtitle/1-2.zip", "season": 1,
                 "episode": 5}]}, {}
        v = _video(tur="dizi", sezon=1, bolum=5, dizi_adi="Ornek", imdb="tt1")
        with mock.patch("d2.ag.istek", sahte):
            a = SubDL("k").ara(v, ["tr"])
        self.assertEqual(len(a), 1)
        self.assertEqual((sorgular[0].get("season"), sorgular[0].get("episode")), (1, 5))
        self.assertNotIn("season_number", sorgular[0])
        self.assertNotIn("episode_number", sorgular[0])

    def test_subsource_sonraki_sayfalar(self):
        # SubSource bölüm süzmüyor: dizide bütün sezon gelir, sayfa başına 100. Bölümün altyazısı 2. sayfada kalıyordu.
        istekler = []

        def sahte(yontem, adres, basliklar=None, sorgu=None, **k):
            if "movies/search" in adres:
                return 200, {"data": [{"movieId": 77, "title": "Ornek", "season": 1}]}, {}
            istekler.append((sorgu["language"], sorgu.get("page", 1)))
            sayfa = sorgu.get("page", 1)
            if sayfa == 1:
                data = [{"subtitleId": i, "language": sorgu["language"], "releaseInfo": [f"Ornek.S01E{i % 9 + 6:02d}.WEB"]}
                        for i in range(100)]
            else:
                data = [{"subtitleId": 1000 + sayfa, "language": sorgu["language"], "releaseInfo": ["Ornek.S01E05.WEB"]}]
            return 200, {"data": data, "pagination": {"page": sayfa, "limit": 100, "total": 300, "pages": 3}}, {}
        with mock.patch("d2.ag.istek", sahte):
            a = SubSource("k").ara(_video(tur="dizi", sezon=1, bolum=5, dizi_adi="Ornek", imdb="tt1"), ["tr", "en"])
        self.assertEqual(sorted((x.dil, x.kimlik) for x in a), [("en", "1002"), ("en", "1003"), ("tr", "1002"),
                                                                  ("tr", "1003")])
        # Filmde yalnız hedef dilin öteki sayfaları alınır
        istekler.clear()
        with mock.patch("d2.ag.istek", sahte):
            SubSource("k").ara(_video(tur="film", baslik="Ornek", imdb="tt1"), ["tr", "en"])
        self.assertEqual(istekler, [("turkish", 1), ("turkish", 2), ("turkish", 3), ("english", 1)])

    def test_subdl_ek_aramada_baska_yapimin_altyazilari_atilir(self):
        # SubDL bir dilde yapıma hiç altyazı yoksa ilgisiz bir dizinin altyazılarını veriyor ("results" yine doğru
        # yapımı yazıyor; canlı: Attack on Titan'ın Türkçe aramasına The Simpsons 12. sezon). Bölümü tutan olursa
        # başka dizinin altyazısı listeye giriyordu.
        def ara(dizi_adi, en_adi, tr):
            sonuc = {"type": "tv", "name": dizi_adi, "year": 2005}
            en = [{"release_name": en_adi.format(i), "language": "EN", "url": f"/subtitle/e-{i}.zip", "season": 12,
                   "episode": 3} for i in range(30)]

            def sahte(yontem, adres, basliklar=None, sorgu=None, **k):
                return 200, {"status": True, "results": [sonuc],
                             "subtitles": tr if sorgu.get("languages") == "TR" else en}, {}
            v = _video(tur="dizi", sezon=12, bolum=3, dosya_adi=f"{dizi_adi} - S12E03.mkv")
            v.dizi_adi, v.ad_kaynagi = dizi_adi, "dosya"
            s = SubDL("k")
            with mock.patch("d2.ag.istek", sahte):
                a = s.ara(v, ["tr", "en"])
            return [x.kimlik for x in a if x.dil == "tr"], s
        simpsons = [{"release_name": f"The.Simpsons.S12E{i:02d}.WEB-DL.DSNP", "language": "TR",
                     "url": f"/subtitle/s-{i}.zip", "season": 12, "episode": i} for i in (1, 3, 6)]
        tr, s = ara("Greys Anatomy", "Greys.Anatomy.S12E03.{}.720p.HDTV", simpsons)
        self.assertEqual((tr, s.elenen, s.elenen_adlari), ([], 1, ["The Simpsons"]))
        dogru = [{"release_name": "Greys.Anatomy.S12E03.1080p.WEB", "language": "TR", "url": "/subtitle/t-1.zip",
                  "season": 12, "episode": 3}]
        self.assertEqual(ara("Greys Anatomy", "Greys.Anatomy.S12E03.{}.720p.HDTV", dogru)[0], ["/subtitle/t-1.zip"])
        # Animede Türkçe altyazı İngilizce adla değil romajiyle yüklenmiş olabilir: ilk sayfadaki adlar da sayılır
        romaji = [{"release_name": "[Grup] Kimetsu no Yaiba - 03 [1080p]", "language": "TR", "url": "/subtitle/t-2.zip",
                   "season": 12, "episode": 3}]
        self.assertEqual(ara("Demon Slayer", "[SubsPlease] Kimetsu no Yaiba - 03 ({}p)", romaji)[0], ["/subtitle/t-2.zip"])

    def test_jimaku_emin_degilse_sonuc_yok(self):
        def sahte(yontem, adres, basliklar=None, sorgu=None, **k):
            self.assertEqual(basliklar, {"Authorization": "j"})
            if "entries/search" in adres:
                return 200, [{"id": 9, "name": "Sousou no Frieren", "anilist_id": 154587}], {}
            return 200, [{"name": "Frieren 05.ass", "url": "https://jimaku.cc/entry/9/download/f05.ass", "size": 5000},
                         {"name": "f05.rar", "url": "https://jimaku.cc/entry/9/download/f05.rar", "size": 5000}], {}
        with mock.patch("d2.ag.istek", sahte):
            j = Jimaku("j")
            v2 = _video(tur="dizi", sezon=2, bolum=5, dizi_adi="Sousou no Frieren")
            self.assertEqual(j.ara(v2, ["en", "ja"]), [])                    # 2. sezonun AniList kaydı bilinmiyor
            v1 = _video(tur="dizi", sezon=1, bolum=5, dizi_adi="Sousou no Frieren")
            self.assertEqual([x.indirme["ad"] for x in j.ara(v1, ["en", "ja"])], ["Frieren 05.ass"])
            v3 = _video(tur="dizi", sezon=1, bolum=5, dizi_adi="Frieren Baska")
            self.assertEqual(j.ara(v3, ["ja"]), [])                          # ad birebir tutmalı
            self.assertEqual(j.ara(v1, ["en"]), [])                          # kaynak dillerde Japonca yok


class EslesmeTestleri(unittest.TestCase):
    def test_dizin_ve_ceviriler(self):
        ham = {"$meta": {}, "tmdb_show:1429:s1": {"anilist:16498": {"1-25": "1-25"}},
               "tmdb_show:1429:s3": {"anilist:99147": {"13-22": "1-10"}},
               "anilist:99147": {"tmdb_show:1429:s3": {"1-10": "13-22"}}}
        d = tempfile.mkdtemp()
        durum = kayit.Durum(d)
        kayit.yaz(durum.yol("esleme", "dizin.json"), eslesme.dizin_cikar(ham))
        kayit.yaz(durum.yol("esleme", "bilgi.json"), {"surum": eslesme.DIZIN_SURUMU, "zaman": 0})
        v = _video(tur="dizi", tmdb="1429", sezon=3, bolum=15)
        self.assertEqual(eslesme.anilist_bul(durum, v), ("99147", 3))
        self.assertEqual(eslesme.sezon_bolum_bul(durum, "99147", 3), ("tmdb", "1429", 3, 15))
        v2 = _video(tur="dizi", anilist="99147", sezon=1, bolum=3)
        eslesme.uygula(durum, v2)
        self.assertEqual((v2.tmdb, v2.sezon, v2.bolum, v2.anilist_bolum), ("1429", 3, 15, 3))
        shutil.rmtree(d)


    def test_bozuk_liste_calisan_dizini_silmez(self):
        # İndirilen liste bozuksa (yarım, HTML hata sayfası) güncelleme yakalanmayan hatayla bitiyordu; geçerli ama boş
        # bir JSON ("{}", hata iletisi) ise çalışan dizinin üzerine boş dizin yazılıyor ve bir hafta yeniden denenmiyordu.
        d = tempfile.mkdtemp()
        durum = kayit.Durum(d)
        iyi = {"$meta": {"generated_on": "2026-11-15T00:00:00Z"}, "tmdb_show:1429:s1": {"anilist:16498": {"1-25": "1-25"}}}
        v = _video(tur="dizi", tmdb="1429", sezon=1, bolum=3)
        kayitlar, bildirimler = [], []
        for govde, beklenen in ((json.dumps(iyi).encode(), True), (b"<html>hata</html>", False), (b'{"message": "x"}', False),
                                (b"[1, 2]", False), (json.dumps(iyi).encode()[:40], False)):
            with mock.patch("d2.ag.istek", lambda *a, **k: (200, govde, {})):
                self.assertIs(eslesme.guncelle(durum, bildir=True, zorla=True, kayit_yaz=kayitlar.append,
                                               bildirici=lambda *a: bildirimler.append(a)), beklenen, govde[:20])
            self.assertEqual(eslesme.anilist_bul(durum, v), ("16498", 3), govde[:20])       # dizin yerinde
        self.assertEqual(len(kayitlar), 5)
        self.assertEqual([b[2] for b in bildirimler], [False, True, True, True, True])       # hatalar bildirildi
        shutil.rmtree(d)


def _sahte_dosya_cevir(kaynak_yol, hedef_yol, goreli, ayar, model, sistem, gunluk, onbellek_klasoru=".onbellek",
                       ilerleme=None, sozluk=None, okunan=None, cevrilecek=None):
    """Çekirdeğin dosya_cevir'i yerine: satırları büyük harfe çevirir."""
    from defta import srt
    _, satirlar, _ = okunan
    cikti = [srt.Satir(n, s.bas, s.bit, s.metin.upper()) for n, s in enumerate(satirlar, 1)
             if cevrilecek is None or n - 1 in cevrilecek]       # çekirdek gibi: bağlam satırları yazılmaz
    os.makedirs(os.path.dirname(hedef_yol), exist_ok=True)
    with open(hedef_yol, "w", encoding="utf-8") as f:
        f.write(srt.srt_yaz(cikti))
    if ilerleme:
        ilerleme(len(satirlar), len(satirlar))
    return {"satir": len(satirlar), "yazilan": len(cikti), "toplam_satir": len(satirlar)}


class CeviriIsiTestleri(unittest.TestCase):
    def setUp(self):
        self.kok = tempfile.mkdtemp()
        self.durum = kayit.Durum(self.kok)
        satirlar = []
        for i in range(10):
            satirlar.append(f"{i + 1}\n00:{i:02d}:00,000 --> 00:{i:02d}:02,000\nsatır {i}\n")
        self.kaynak = "\n".join(satirlar)
        self.bilgi = ceviri_isi.is_kur(self.durum, {
            "is_id": "is1", "video_anahtari": "imdb:tt1:s01e05|x", "icerik": "imdb:tt1:s01e05", "yapim": "ad:ornek",
            "baslik": "Ornek", "bolum_etiketi": "S01E05", "goreli": "S01E05/os-1",
            "kaynak": {"site": "os", "kimlik": "1", "adlar": ["Ornek.S01E05"]}, "kaynak_dil": "en", "hedef_dil": "tr",
            "ceviri_kimligi": "os-1"}, self.kaynak, ".srt")

    def tearDown(self):
        shutil.rmtree(self.kok)

    def _is(self):
        return ceviri_isi.CeviriIsi(self.durum, dict(self.bilgi), "sk-test", {"hedef_dil": "tr", "b_sen_siz": True})

    def test_hizli_cevirisi_bulundugun_yerden(self):
        is_ = self._is()
        with mock.patch.object(ceviri_isi, "dosya_cevir", _sahte_dosya_cevir):
            yol = is_.hizli(baslangic_sn=181, sure_dk=2)      # 3:01'de: ekrandaki 3:00-3:02 satırı dahil
        with open(yol, encoding="utf-8") as f:
            m = f.read()
        self.assertIn("SATIR 3", m)                            # 3:01'den 5:01'e: 3:00, 4:00 ve 5:00 satırları
        self.assertIn("SATIR 5", m)
        self.assertNotIn("SATIR 6", m)
        self.assertIn("satır 2", m)
        self.assertIn("satır 6", m)
        self.assertEqual(m.count("-->"), 10)

    def test_hizli_cevrilmis_satirlari_korur(self):
        is_ = self._is()
        os.makedirs(os.path.join(is_.klasor, "onbellek"))
        kayit.yaz(os.path.join(is_.klasor, "onbellek", "x.json"), {"sonuc": {"3": "Çeviri üç", "4": "Çeviri dört"}})
        with mock.patch.object(ceviri_isi, "dosya_cevir", _sahte_dosya_cevir):
            yol = is_.hizli(baslangic_sn=181, sure_dk=2)      # 3:01-5:01: 3 ve 4 zaten çevrilmiş, 5 hızlı çevrilir
        with open(yol, encoding="utf-8") as f:
            m = f.read()
        self.assertIn("Çeviri üç", m)
        self.assertIn("Çeviri dört", m)
        self.assertIn("SATIR 5", m)
        self.assertIn("satır 6", m)
        self.assertEqual(m.count("-->"), 10)

    def test_ayar_degisince_yarim_is_bastan(self):
        is_ = self._is()
        is_.ayar_degistiyse_sifirla()
        os.makedirs(os.path.join(is_.klasor, "onbellek"))
        kayit.yaz(os.path.join(is_.klasor, "onbellek", "x.json"), {"sonuc": {"0": "Çeviri"}})
        ayni = self._is()
        ayni.ayar_degistiyse_sifirla()
        self.assertEqual(ayni.onbellek(), {0: "Çeviri"})
        bilgi = ceviri_isi.is_bilgisi(self.durum, "is1")
        farkli = ceviri_isi.CeviriIsi(self.durum, bilgi, "sk-test", {"hedef_dil": "tr", "b_sen_siz": False})
        farkli.ayar_degistiyse_sifirla()
        self.assertEqual(farkli.onbellek(), {})

    def test_kaliteli_biter_ve_kaydedilir(self):
        is_ = self._is()
        with mock.patch.object(ceviri_isi, "dosya_cevir", _sahte_dosya_cevir), \
                mock.patch.object(ceviri_isi, "dizi_analiz", lambda *a, **k: None):
            yol, bilgi = is_.kaliteli()
        self.assertTrue(os.path.exists(yol))
        self.assertFalse(os.path.exists(self.durum.yol("isler", "is1")))       # kalıntılar silindi
        hazirlar = self.durum.hazir_ceviriler("imdb:tt1:s01e05", "tr")
        self.assertEqual(len(hazirlar), 1)
        self.assertTrue(os.path.exists(hazirlar[0]["kaynak_yol"]))

    def test_baska_hedef_dilin_cevirisi_silinmez(self):
        # Aynı kaynak önce Türkçeye, sonra Almancaya çevrilince Türkçe çeviri (kalıcı, parası ödenmiş) yerinde kalır
        with mock.patch.object(ceviri_isi, "dosya_cevir", _sahte_dosya_cevir), \
                mock.patch.object(ceviri_isi, "dizi_analiz", lambda *a, **k: None):
            tr_yol, _ = self._is().kaliteli()
            for _ in range(2):                                 # ikincisi: aynı dilde yeniden çeviri eskisinin yerine geçer
                bilgi = ceviri_isi.is_kur(self.durum, dict(self.bilgi, is_id="is2", hedef_dil="de"), self.kaynak, ".srt")
                de_yol, _ = ceviri_isi.CeviriIsi(self.durum, bilgi, "sk-test", {"hedef_dil": "de"}).kaliteli()
        self.assertTrue(os.path.exists(tr_yol))
        self.assertEqual([h["yol"] for h in self.durum.hazir_ceviriler("imdb:tt1:s01e05", "tr")], [tr_yol])
        self.assertEqual([h["yol"] for h in self.durum.hazir_ceviriler("imdb:tt1:s01e05", "de")], [de_yol])

    def test_hizli_araligin_oncesi_ve_sonrasi_baglam(self):
        # 3:01'den 2 dk: 3, 4 ve 5. satırlar çevrilir; önündeki 3 ve arkasındaki 4 satır yalnız bağlam olarak gider
        is_ = self._is()
        gorulen = {}

        def sahte(kaynak_yol, hedef_yol, goreli, ayar, model, sistem, gunluk, onbellek_klasoru, ilerleme, sozluk,
                  okunan, cevrilecek=None):
            gorulen.update(metinler=[s.metin for s in okunan[1]], cevrilecek=list(cevrilecek))
            return _sahte_dosya_cevir(kaynak_yol, hedef_yol, goreli, ayar, model, sistem, gunluk, onbellek_klasoru,
                                      ilerleme, sozluk, okunan, cevrilecek)
        with mock.patch.object(ceviri_isi, "dosya_cevir", sahte):
            yol = is_.hizli(baslangic_sn=181, sure_dk=2)
        self.assertEqual(gorulen["metinler"], [f"satır {i}" for i in range(10)])    # 3-8 < 0, 5+4 = 9
        self.assertEqual([gorulen["metinler"][i] for i in gorulen["cevrilecek"]], ["satır 3", "satır 4", "satır 5"])
        with open(yol, encoding="utf-8") as f:
            m = f.read()
        self.assertEqual(m.count("-->"), 10)                   # bağlam satırları iki kez yazılmaz
        self.assertIn("SATIR 4", m)
        self.assertIn("satır 2", m)
        self.assertNotIn("SATIR 2", m)

    def test_hizli_dusunmeden_kucuk_parcalarla(self):
        is_ = self._is()
        gorulen = {}

        def sahte(kaynak_yol, hedef_yol, goreli, ayar, *a, **k):
            gorulen.update(dusunme=ayar["model"]["dusunme_seviyesi"], parca=ayar["parca"]["altyazi_sayisi"])
            return _sahte_dosya_cevir(kaynak_yol, hedef_yol, goreli, ayar, *a, **k)
        with mock.patch.object(ceviri_isi, "dosya_cevir", sahte):
            is_.hizli(baslangic_sn=181, sure_dk=2)
        self.assertEqual(gorulen, {"dusunme": "none", "parca": ceviri_isi.HIZLI_PARCA})
        self.assertEqual(is_.ayar["model"]["dusunme_seviyesi"], "low")     # kaliteli çeviri düşünerek sürer
        self.assertEqual(is_.ayar["parca"]["altyazi_sayisi"], 0)

    def test_hizli_ve_kaliteli_ayni_anda(self):
        # Kaliteli çeviri hızlıyı beklemeden başlar; bitince süren hızlıyı bekler: hızlı çeviri hep önce yüklenir.
        import threading
        from d2 import servis
        xbmcvfs.kok = self.kok
        s = servis.Servis()
        s.durum, s.izleyici = self.durum, xbmc.Monitor()
        self.durum.secim_yaz(self.bilgi["video_anahtari"], {"anahtar": "ai:os-1"})
        kaliteli_basladi, hizli_gordu, yuklenen = threading.Event(), [], []

        def sahte(kaynak_yol, hedef_yol, goreli, ayar, *a, **k):
            if ".hizli." in goreli:
                hizli_gordu.append(kaliteli_basladi.wait(5))   # sırayla gitseydi kaliteli henüz başlamamış olurdu
            else:
                kaliteli_basladi.set()
            return _sahte_dosya_cevir(kaynak_yol, hedef_yol, goreli, ayar, *a, **k)
        s._yukle = lambda va, yol: yuklenen.append(os.path.basename(yol))
        with mock.patch.object(ceviri_isi, "dosya_cevir", sahte), \
                mock.patch.object(ceviri_isi, "dizi_analiz", lambda *a, **k: None):
            s._calistir(self._is(), 181, 2, True, False, False)
        self.assertEqual(hizli_gordu, [True])
        # Hızlı çeviri, Kodi orijinali sonradan yükleyip üstüne yazmış olabilir diye bir kez daha yüklenebilir (1.0.2)
        self.assertIn(len(yuklenen), (2, 3))
        self.assertTrue(yuklenen[0].startswith("hizli."))      # eklenti almadı: servis yükledi, kaliteliden önce
        self.assertTrue(yuklenen[-1].startswith("AI."))
        self.assertEqual(len([y for y in yuklenen if y.startswith("AI.")]), 1)
        self.assertFalse(os.path.exists(self.durum.yol("isler", "is1")))       # kalıntılar yine silindi

    def _servis(self):
        import ayarlar_uret
        from d2 import servis
        xbmcvfs.kok = self.kok
        xbmcaddon.ayarlar.clear()
        xbmcaddon.ayarlar.update(ayarlar_uret.varsayilanlar())
        xbmcaddon.ayarlar["openai_anahtar"] = "sk-test"
        self.addCleanup(xbmcaddon.ayarlar.clear)
        self.addCleanup(xbmcgui.pencere.clear)
        s = servis.Servis()
        s.durum, s.izleyici = self.durum, xbmc.Monitor()
        return s

    def test_baska_surumde_surdurulen_is_o_videonun_olur(self):
        # Yarım iş aynı bölümün başka sürümünde (başka akış) yeniden seçildi: iş o videoya bağlanmazsa biten çeviri
        # yüklenmez, video kapanınca ya da başka altyazı seçilince de durmaz
        import time
        s = self._servis()
        yeni = "imdb:tt1:s01e05|baska.surum"
        self.durum.secim_yaz(yeni, {"anahtar": "ai:os-1"})
        yuklenen = []
        s._yukle = lambda va, yol: yuklenen.append((va, os.path.basename(yol)[:3]))
        with mock.patch.object(ceviri_isi, "dosya_cevir", _sahte_dosya_cevir), \
                mock.patch.object(ceviri_isi, "dizi_analiz", lambda *a, **k: None):
            s.mesaj("devam", {"is_id": "is1", "video": yeni, "baslangic": 181, "sure": 2, "hizli": True})
            self.assertEqual([i.bilgi["video_anahtari"] for i, _ in s.isler.values()] or [yeni], [yeni])
            son = time.time() + 10
            while s.isler and time.time() < son:
                time.sleep(0.01)
        self.assertEqual(yuklenen[-1], (yeni, "AI."))
        self.assertTrue(os.path.basename(self.durum.secim(yeni)["yol"]).startswith("AI."))

    def test_openai_anahtari_yokken_kendiliginden_ceviri_hazirlanmaz(self):
        # Anahtar yokken çeviri başlayamaz: kaynak indirilmez (indirme hakkı), soru sorulmaz, seçim değişmez
        from d2 import siradaki
        s = self._servis()
        xbmcaddon.ayarlar["openai_anahtar"] = ""
        v = _video(tur="dizi", imdb="tt1", sezon=1, bolum=5, dizi_adi="Ornek", dosya_adi="Ornek.S01E05.1080p.WEB-DL.mkv")
        kaynak = Aday("os", "9", "en", ["Ornek.S01E06.1080p.WEB-DL"], bolum_kesin=True)
        cagri = []
        with mock.patch.object(arama, "siteler", lambda *a, **k: []), \
                mock.patch.object(arama, "ara", lambda *a, **k: ([kaynak], [])), \
                mock.patch.object(arama, "indir_metin", lambda *a, **k: cagri.append("indir") or (self.kaynak, ".srt", kaynak)), \
                mock.patch.object(siradaki, "_soru", lambda sure, *yazilar: cagri.append("soru") or True):
            siradaki.bak(s, v, 1)                               # sıradaki bölüm: Sor
            s._otomatik_cevir(v, Aday("os", "8", "en", ["Ornek.S01E05.1080p.WEB-DL"]), [])
        self.assertEqual(cagri, [])
        self.assertIsNone(self.durum.secim(v.video_anahtari()))
        # Sıradaki bölümün yarım ön çevirisi: bölüm açılınca seçimin yerine geçmez, otomatik yükleme atlanmaz
        v5 = _video(tur="dizi", imdb="tt1", sezon=1, bolum=5, dosya_adi="Ornek.S01E05.720p.HDTV.mkv")
        kayit.yaz(self.durum.yol("isler", "is1", "is.json"), dict(self.bilgi, on_ceviri=True))
        self.assertFalse(s._yarim_isi_surdur(v5))
        self.assertIsNone(self.durum.secim(v5.video_anahtari()))

    def test_uc_kez_hata_veren_on_ceviri_kendiliginden_baslamaz(self):
        # Sıradaki bölüm "Otomatik" iken üst üste 3 kez hata veren ön çeviri sorusuz yeniden başlamaz, sayaç sıfırlanmaz
        from d2 import siradaki
        self._servis()
        n = _video(tur="dizi", imdb="tt1", sezon=1, bolum=6, dizi_adi="Ornek")
        kaynak = Aday("os", "9", "en", ["Ornek.S01E06.1080p.WEB-DL"])
        is_id = siradaki.is_kimligi(n, kaynak.anahtar, "tr")
        ceviri_isi.is_kur(self.durum, dict(self.bilgi, is_id=is_id, on_ceviri=True, hata_sayisi=3), self.kaynak, ".srt")
        baslatilan = []

        class Servis:
            durum = self.durum

            def is_baslat(self, is_id, **k):
                baslatilan.append(k)
        siradaki._baslat(Servis(), n, kaynak, [], kendiliginden=True)
        self.assertEqual(baslatilan, [])
        siradaki._baslat(Servis(), n, kaynak, [], kendiliginden=False)     # kullanıcı "Çevir" dedi: sürer
        self.assertEqual(baslatilan, [{"hizli": False, "kendiliginden": False}])

    def test_video_kapaninca_otomatik_ceviri_baslamaz(self):
        # Kaynak indirilirken ya da Kodi ayarı sorusu açıkken video kapandıysa (başka altyazı seçildiyse) paralı iş
        # başlamaz: başlasaydı onu durduracak kapanma olayı çoktan geçmiş olurdu
        s = self._servis()
        v = _video(tur="dizi", imdb="tt1", sezon=1, bolum=5, dizi_adi="Ornek", dosya_adi="Ornek.S01E05.1080p.WEB-DL.mkv")
        kaynak = Aday("os", "8", "en", ["Ornek.S01E05.1080p.WEB-DL"])
        baslatilan = []
        s.video, s.is_baslat = v, lambda *a, **k: baslatilan.append(a)
        xbmc.oynatici["oynuyor"] = True
        self.addCleanup(xbmc.oynatici.update, {"oynuyor": False})

        def indir(*a, **k):
            xbmc.oynatici["oynuyor"] = False                   # indirme sürerken video kapandı
            return self.kaynak, ".srt", kaynak
        with mock.patch.object(arama, "indir_metin", indir):
            s._otomatik_cevir(v, kaynak, [])
        self.assertEqual(baslatilan, [])
        self.assertIsNone(self.durum.secim(v.video_anahtari()))
        # Oynatma başında: soru kutusu kapanana kadar video kapandıysa yarım iş sürdürülmez
        xbmc.oynatici.update({"oynuyor": True, "yol": "", "etiket": {}})
        s._kodi_otomatik_sor = lambda: xbmc.oynatici.update({"oynuyor": False})
        s._yarim_isi_surdur = lambda v: baslatilan.append("yarim")
        s._otomatik = lambda *a: baslatilan.append("otomatik")
        s._oynatma_basladi()
        self.assertEqual(baslatilan, [])

    def test_ayni_isin_iki_hizli_cevirisi_sirayla_calisir(self):
        # Süren iş yeniden seçilince (ya da otomatik başlayanın üstüne elle) ikinci hızlı çeviri başlar: ikisi aynı
        # geçici dosyaya yazdığı için aynı anda çalışırsa biri ötekinin satırlarını okur
        import threading
        import time
        is_ = self._is()
        icerde, en_cok = [0], [0]
        kilit = threading.Lock()

        def sahte(*a, **k):
            with kilit:
                icerde[0] += 1
                en_cok[0] = max(en_cok[0], icerde[0])
            time.sleep(0.1)
            sonuc = _sahte_dosya_cevir(*a, **k)
            with kilit:
                icerde[0] -= 1
            return sonuc
        with mock.patch.object(ceviri_isi, "dosya_cevir", sahte):
            parcaciklar = [threading.Thread(target=is_.hizli, args=(bas, 2)) for bas in (61, 361)]
            for p in parcaciklar:
                p.start()
            for p in parcaciklar:
                p.join(10)
        self.assertEqual(en_cok[0], 1)

    def test_hizli_ceviriyi_servis_yukler(self):
        # Altyazı penceresi beklemez (orijinali yükleyip kapanır): hızlı çeviri hazır olunca servis yükler ve seçimin
        # dosyasını günceller. Arada başka altyazı seçildiyse yüklemez.
        s = self._servis()
        va = self.bilgi["video_anahtari"]
        yuklenen = []
        s._yukle = lambda v, yol: yuklenen.append((v, os.path.basename(yol)))
        self.durum.secim_yaz(va, {"anahtar": "ai:os-1", "yol": "orijinal.srt"})
        with mock.patch.object(ceviri_isi, "dosya_cevir", _sahte_dosya_cevir):
            is_ = self._is()
            is_.hizli_basladi()
            s._hizli_akis(is_, 0, 2, False, False, True)
            # ikinci yükleme: seçili altyazı hâlâ hızlı çeviriyse birkaç saniye sonra yeniden (1.0.2)
            self.assertEqual(yuklenen, [(va, "hizli.tr.srt")] * 2)
            self.assertEqual(os.path.basename(self.durum.secim(va)["yol"]), "hizli.tr.srt")
            self.durum.secim_yaz(va, {"anahtar": "os:9", "yol": "insan.srt"})
            is_.hizli_basladi()
            s._hizli_akis(is_, 0, 2, False, False, True)
        self.assertEqual(len(yuklenen), 2)                  # başka altyazı seçiliyken yüklenmedi
        self.assertEqual(self.durum.secim(va)["yol"], "insan.srt")

    def test_ceviri_bitince_kodinin_kaydettigi_orijinal_silinir(self):
        # "Çevir"e basınca Kodi orijinali videonun yanına <video>.<dil>.srt diye kaydeder ve sonraki açılışta onu
        # seçebilir. Çeviri bitince içeriği kaynağımızla aynı olan kopya silinir; kullanıcının kendi altyazısı kalır.
        s = self._servis()
        klasor = os.path.join(self.kok, "videolar")
        os.makedirs(klasor)
        video = os.path.join(klasor, "Ornek.S01E05.mkv")
        s.video = _video(tur="dizi", imdb="tt1", sezon=1, bolum=5, dosya_adi="x")
        s.video.yol = video
        s.video.video_anahtari = lambda: self.bilgi["video_anahtari"]
        ceviri_klasoru = self.durum.ceviri_klasoru(self.bilgi["icerik"])
        kayit.metin_yaz(os.path.join(ceviri_klasoru, "os-1.kaynak.srt"), self.kaynak)
        with open(os.path.join(klasor, "Ornek.S01E05.eng.srt"), "wb") as f:      # satır sonu farklı kopya da silinir
            f.write(self.kaynak.replace("\n", "\r\n").encode("utf-8"))
        for ad, icerik in (("Ornek.S01E05.en.srt", self.kaynak), ("Ornek.S01E05.tr.srt", "kullanıcının altyazısı"),
                           ("Ornek.S01E05.mkv", "video"), ("Baska.S01E05.en.srt", self.kaynak)):
            kayit.metin_yaz(os.path.join(klasor, ad), icerik)
        s._kodi_kopyasini_sil(self.bilgi, {"kaynak_dosya": "os-1.kaynak.srt"})
        self.assertEqual(sorted(os.listdir(klasor)), ["Baska.S01E05.en.srt", "Ornek.S01E05.mkv", "Ornek.S01E05.tr.srt"])
        # akışta ve başka video oynarken dokunulmaz
        kayit.metin_yaz(os.path.join(klasor, "Ornek.S01E05.en.srt"), self.kaynak)
        s.video.akis = True
        s._kodi_kopyasini_sil(self.bilgi, {"kaynak_dosya": "os-1.kaynak.srt"})
        s.video.akis, s.video.video_anahtari = False, lambda: "baska"
        s._kodi_kopyasini_sil(self.bilgi, {"kaynak_dosya": "os-1.kaynak.srt"})
        self.assertTrue(os.path.exists(os.path.join(klasor, "Ornek.S01E05.en.srt")))

    def test_sozlukle_yenilemede_karar_verilemeyen_girdi_kalir(self):
        # "Sözlükle birlikte yenile" bu bölümde geçen adların kararlarını siler ve bölümü yeniden inceler. Yeniden
        # karar verilemeyen (başka bölümde öğrenilmiş, burada aday olmayan) girdinin eski kararı kalır; yeniden
        # karar verilen yenisiyle değişir. Yarıda kalıp yeniden başlayan işte de saklananlar kaybolmaz.
        from defta.model import Iptal
        from defta.sozluk import Sozluk
        from defta import srt as cekirdek_srt
        bilgi = dict(self.bilgi, sozlukle_yenile=True)
        kayit.yaz(self.durum.yol("isler", "is1", "is.json"), bilgi)
        is_ = ceviri_isi.CeviriIsi(self.durum, bilgi, "sk-test", {"hedef_dil": "tr"})
        yol = is_._sozluk_yolu()
        sozluk = Sozluk(yol)
        sozluk.girdi_ekle("satır", "term", "eski satır")
        sozluk.girdi_ekle("Cadets", "descriptive", "askerî öğrenciler")
        sozluk.girdi_ekle("Baska", "name", "Baska")
        sozluk.kaydet()
        satirlar = cekirdek_srt.srt_ayristir(self.kaynak + "\n11\n00:11:00,000 --> 00:11:02,000\nThe Cadets left.\n")

        def yarida(dosyalar, ayar, model, sozluk, gunluk):
            raise Iptal()

        def analiz(dosyalar, ayar, model, sozluk, gunluk):         # yalnız "satır" yeniden karara bağlanır
            sozluk.girdi_ekle("satır", "term", "yeni satır")
            sozluk.kaydet()
        with mock.patch.object(ceviri_isi, "dizi_analiz", yarida), self.assertRaises(Iptal):
            is_.sozluk_adimi(satirlar, None, object())
        self.assertNotIn("Cadets", Sozluk(yol).girdiler)              # silindi, analiz yarıda kaldı
        with mock.patch.object(ceviri_isi, "dizi_analiz", analiz):
            is_.sozluk_adimi(satirlar, None, object())
        g = Sozluk(yol).girdiler
        self.assertEqual((g["satır"]["hedef"], g["Cadets"]["hedef"], g["Baska"]["hedef"]),
                         ("yeni satır", "askerî öğrenciler", "Baska"))

    def test_sozluk_adiminin_odenmis_yanitlari_is_klasorune_yazilir(self):
        # Sözlük adımı yarıda durursa (video kapandı) ödenmiş yanıtlar kayıttan döner: model kayıtlı sarmalla çağrılır
        from defta.model import Kayitli
        is_ = self._is()
        gelen = []
        with mock.patch.object(ceviri_isi, "dizi_analiz", lambda dosyalar, ayar, model, sozluk, gunluk: gelen.append(model)):
            is_.sozluk_adimi([], None, object())
        self.assertIsInstance(gelen[0], Kayitli)
        self.assertEqual(gelen[0]._yol, os.path.join(is_.klasor, "analiz_kayit.json"))

    def test_ayni_is_iki_kez_baslamaz(self):
        # İş kayda geçtiği anda parçacığı da başlamış olmalı: arada gelen ikinci istek işi "çalışmıyor" görüp ikinci
        # bir çeviri (iki kat ödeme) başlatmasın
        import threading
        from d2 import servis
        s = self._servis()
        kurulan = []

        class Parcacik:
            def __init__(self, **k):
                kurulan.append(self)
                self.basladi = False

            def start(self):
                self.basladi = True

            def is_alive(self):
                return self.basladi

        class Kilit:
            def __init__(self):
                self.kilit, self.derinlik, self.cikista = threading.RLock(), 0, None

            def __enter__(self):
                self.kilit.acquire()
                self.derinlik += 1

            def __exit__(self, *a):
                self.derinlik -= 1
                self.kilit.release()
                if not self.derinlik and self.cikista:
                    cikista, self.cikista = self.cikista, None
                    cikista()
        s.kilit = Kilit()
        s.kilit.cikista = lambda: s.is_baslat("is1", hizli=False)     # kilit bırakılır bırakılmaz ikinci istek
        with mock.patch.object(servis.threading, "Thread", Parcacik):
            s.is_baslat("is1", hizli=False)
        self.assertEqual(len(kurulan), 1)

    def test_yeniden_ceviri_video_karmasini_korur(self):
        # Kaynağın dosya karması bu videoyla tutmuştu: yeniden çevrilince "senkron uyumlu" işareti kaybolmasın
        from d2 import giris
        self._servis()
        v = _video(tur="dizi", imdb="tt1", sezon=1, bolum=5, dosya_adi="x.mkv")
        with mock.patch.object(ceviri_isi, "dosya_cevir", _sahte_dosya_cevir), \
                mock.patch.object(ceviri_isi, "dizi_analiz", lambda *a, **k: None):
            yol, _ = ceviri_isi.CeviriIsi(self.durum, dict(self.bilgi, video_karma="abc123"), "sk-test",
                                          {"hedef_dil": "tr"}).kaliteli()
        giris.yeniden(self.durum, v, {"tur": "ai", "anahtar": "ai:os-1", "yol": yol})
        isler = ceviri_isi.yarim_isler(self.durum)
        self.assertEqual([(b.get("yeniden"), b.get("video_karma")) for b in isler], [(True, "abc123")])

    def test_is_kendi_hedef_diliyle_surer(self):
        # Yarım Türkçe iş, hedef dil ayarı sonradan Almanca yapılsa da Türkçe sürer: yoksa biten (ödenmiş) satırlar
        # atılır, kimse seçmeden bölümün tamamı Almancaya çevrilirdi
        from d2 import servis
        s = self._servis()
        xbmcaddon.ayarlar["hedef_dil"] = "de"

        class Parcacik:
            def __init__(self, **k):
                pass

            def start(self):
                pass

            def is_alive(self):
                return True
        with mock.patch.object(servis.threading, "Thread", Parcacik):
            s.is_baslat("is1", hizli=False)
        self.assertEqual(s.isler["is1"][0].ayar["ceviri"]["hedef_dil"], "tr")

    def test_otomatik_yukleme_kullanicinin_secimini_ezmez(self):
        # Karma beklenirken (akışta 10 sn'ye kadar) kullanıcı kendisi altyazı seçtiyse hazır AI çevirisi onun yerine
        # yüklenmez, seçimi de değiştirilmez
        s = self._servis()
        xbmcaddon.ayarlar["oto_ai"] = 1
        v = _video(tur="dizi", imdb="tt1", sezon=1, bolum=5, dizi_adi="Ornek", dosya_adi="Ornek.S01E05.1080p.WEB-DL.mkv")
        with mock.patch.object(ceviri_isi, "dosya_cevir", _sahte_dosya_cevir), \
                mock.patch.object(ceviri_isi, "dizi_analiz", lambda *a, **k: None):
            ceviri_isi.CeviriIsi(self.durum, dict(self.bilgi, kaynak=Aday("os", "1", "en", ["Ornek.S01E05"]).kayit()),
                                 "sk-test", {"hedef_dil": "tr"}).kaliteli()
        s.video = v
        xbmc.oynatici.update({"oynuyor": True, "yuklenen": []})
        self.addCleanup(xbmc.oynatici.update, {"oynuyor": False})

        class Karma:
            def join(_, sure):
                self.durum.secim_yaz(v.video_anahtari(), {"tur": "insan", "anahtar": "os:1", "yol": "/yok.srt"})
        s._otomatik(v, Karma())
        self.assertEqual(xbmc.oynatici["yuklenen"], [])
        self.assertEqual(self.durum.secim(v.video_anahtari())["anahtar"], "os:1")

    def test_yarim_isin_biten_kismi(self):
        is_ = self._is()
        os.makedirs(os.path.join(is_.klasor, "onbellek"))
        kayit.yaz(os.path.join(is_.klasor, "onbellek", "x.json"), {"sonuc": {"0": "Çeviri sıfır", "1": "Çeviri bir"}})
        yol, biten = is_.kismi()
        with open(yol, encoding="utf-8") as f:
            m = f.read()
        self.assertEqual(biten, 2)
        self.assertIn("Çeviri sıfır", m)
        self.assertIn("satır 5", m)

    def test_sozluk_baslangictaki_haliyle_surer(self):
        is_ = self._is()
        sozluk_yolu = self.durum.sozluk_yolu("ad:ornek", "tr")
        from defta.sozluk import Sozluk
        s = Sozluk(sozluk_yolu)
        s.girdi_ekle("Frodo", "name", "Frodo")
        s.kaydet()
        _, satirlar, _ = is_._oku()
        with mock.patch.object(ceviri_isi, "dizi_analiz", lambda *a, **k: None):
            anlik = is_.sozluk_adimi(satirlar, None, None)
        s2 = Sozluk(sozluk_yolu)
        s2.girdi_ekle("Sam", "name", "Sam")                    # arada başka bölüm sözlüğe ad ekledi
        s2.kaydet()
        with mock.patch.object(ceviri_isi, "dizi_analiz", lambda *a, **k: None):
            ikinci = is_.sozluk_adimi(satirlar, None, None)
        self.assertEqual(anlik, ikinci)
        self.assertNotIn("Sam", Sozluk(ikinci).girdiler)        # iş başladığı sözlükle sürer


class GirisTestleri(unittest.TestCase):
    """Altyazı penceresi: arama ve indirme (sahte sitelerle)."""

    def setUp(self):
        self.kok = tempfile.mkdtemp()
        xbmcvfs.kok = self.kok
        import ayarlar_uret
        xbmcaddon.ayarlar.clear()
        xbmcaddon.ayarlar.update(ayarlar_uret.varsayilanlar())
        xbmcaddon.ayarlar.update({"os_anahtar": "osk", "subdl_kullan": False, "subsource_kullan": False,
                                  "jimaku_kullan": False})
        xbmcplugin.ogeler.clear()
        xbmcgui.pencere.clear()
        xbmc.oynatici.update({"oynuyor": True, "sure": 2580.0, "yer": 100.0,
                              "yol": os.path.join(self.kok, "Ornek.S01E05.1080p.WEB-DL.x264-NTb.mkv"),
                              "etiket": {"tvshowtitle": "Ornek", "season": 1, "episode": 5, "mediatype": "episode",
                                         "uniqueid": {"imdb": "tt0000001"}}})
        with open(xbmc.oynatici["yol"], "wb") as f:
            f.write(b"\0" * 200000)
        xbmc.infolabel["Player.Process(videofps)"] = "23.976"

    def tearDown(self):
        xbmc.oynatici["oynuyor"] = False
        shutil.rmtree(self.kok)

    def test_arama_ve_indirme(self):
        from d2 import giris
        srt_ = "1\n00:00:01,000 --> 00:00:02,000\nBir\n\n2\n00:00:03,000 --> 00:00:04,000\nİki\n\n3\n00:00:05,000 --> 00:00:06,000\nÜç\n"

        class SahteSite:
            kod, ad = "os", "OpenSubtitles"

            def ara(self, video, diller):
                return [Aday("os", "1", "tr", ["Ornek.S01E05.720p.WEB-DL.x264-NTb"], indirme={"file_id": 1}),
                        Aday("os", "2", "en", ["Ornek.S01E05.1080p.WEB-DL.x264-NTb"], indirme={"file_id": 2})]

            def indir(self, aday):
                return srt_.encode("utf-8"), "a.srt"
        with mock.patch.object(arama, "siteler", lambda *a, **k: [SahteSite()]):
            giris.calistir(["plugin://service.subtitles.defta/", "1", "?action=search"])
            self.assertEqual([o[1].label2 for o in xbmcplugin.ogeler],
                             ["Ornek.S01E05.720p.WEB-DL.x264-NTb · OpenSubtitles",
                              "Çevir · Ornek.S01E05.1080p.WEB-DL.x264-NTb · OpenSubtitles"])
            self.assertEqual(xbmcplugin.ogeler[0][1].ozellikler["sync"], "true")
            adres = xbmcplugin.ogeler[0][0]
            xbmcplugin.ogeler.clear()
            giris.calistir(["plugin://service.subtitles.defta/", "1", adres.split("/", 3)[3]])
            self.assertEqual(len(xbmcplugin.ogeler), 1)
            yol = xbmcplugin.ogeler[0][0]
            with open(yol, encoding="utf-8") as f:
                self.assertIn("İki", f.read())
            # Seçim kaydedildi: yeni aramada başta "•", ardından kaymış satırı
            xbmcplugin.ogeler.clear()
            giris.calistir(["plugin://service.subtitles.defta/", "1", "?action=search"])
            yazilar = [o[1].label2 for o in xbmcplugin.ogeler]
            self.assertTrue(yazilar[0].startswith("• Ornek.S01E05.720p"))
            self.assertEqual(yazilar[1], metin.al(metin.KAYMIS_SATIRI))
        with open(os.path.join(self.kok, "addon_data", "service.subtitles.defta", "gecici", "son_arama.json"),
                  encoding="utf-8") as f:
            son = json.load(f)
        self.assertNotIn(self.kok, json.dumps(son["video"]))     # yol (adres) yazılmaz


    def _cevrilecek_site(self):
        srt_ = "".join(f"{n}\n00:00:{n:02d},000 --> 00:00:{n:02d},800\nLine {n}\n\n" for n in range(1, 6))

        class SahteSite:
            kod, ad = "os", "OpenSubtitles"

            def ara(self, video, diller):
                return [Aday("os", "2", "en", ["Ornek.S01E05.1080p.WEB-DL.x264-NTb"], indirme={"file_id": 2})]

            def indir(self, aday):
                return srt_.encode("utf-8"), "a.srt"
        return SahteSite()

    def test_cevire_basinca_orijinal_hemen_yuklenir(self):
        # "Çevir" satırı çeviriyi beklemez: iş kurulur, servise "yukle" ile gönderilir, orijinal altyazı hemen döner
        import time
        from d2 import giris, kodi
        xbmcaddon.ayarlar["openai_anahtar"] = "sk-test"
        gonderilen = []

        def gonder(ad, veri=None):
            gonderilen.append((ad, veri))
            if ad in ("cevir", "devam"):
                kodi.ozellik("alindi." + veri["is_id"], "1")          # servis işi aldı
        with mock.patch.object(arama, "siteler", lambda *a, **k: [self._cevrilecek_site()]), \
                mock.patch.object(kodi, "servise_gonder", gonder):
            giris.calistir(["plugin://service.subtitles.defta/", "1", "?action=search"])
            adres = xbmcplugin.ogeler[0][0]
            self.assertTrue(xbmcplugin.ogeler[0][1].label2.startswith("Çevir · "))
            xbmcplugin.ogeler.clear()
            bas = time.time()
            giris.calistir(["plugin://service.subtitles.defta/", "1", adres.split("/", 3)[3]])
            self.assertLess(time.time() - bas, 2)
        mesaj = [v for ad, v in gonderilen if ad == "cevir"][0]
        self.assertTrue(mesaj["yukle"] and mesaj["hizli"])
        with open(xbmcplugin.ogeler[0][0], encoding="utf-8") as f:
            self.assertIn("Line 3", f.read())                             # dönen dosya orijinal altyazı

    def test_elle_aramanin_isi_oynayan_videoya_baglanir(self):
        # Elle aramada (arama kutusu) seçim ve çeviri işi arama metnine değil oynayan videoya bağlanır: video kapanınca
        # çeviri durur, bitince o videoya yüklenir, yeniden açılınca kaldığı yerden sürer
        from d2 import giris, kodi
        xbmcaddon.ayarlar["openai_anahtar"] = "sk-test"
        oynayan = video_modulu.oynatilan(xbmc, xbmcvfs, karma=False).video_anahtari()
        gonderilen = []

        def gonder(ad, veri=None):
            gonderilen.append((ad, veri))
            if ad in ("cevir", "devam"):
                kodi.ozellik("alindi." + veri["is_id"], "1")
        with mock.patch.object(arama, "siteler", lambda *a, **k: [self._cevrilecek_site()]), \
                mock.patch.object(kodi, "servise_gonder", gonder):
            giris.calistir(["plugin://service.subtitles.defta/", "1", "?action=manualsearch&searchstring=Ornek S01E05"])
            adres = xbmcplugin.ogeler[0][0]
            giris.calistir(["plugin://service.subtitles.defta/", "1", adres.split("/", 3)[3]])
        mesaj = [v for ad, v in gonderilen if ad == "cevir"][0]
        self.assertEqual(mesaj["video"], oynayan)
        durum = kayit.Durum(os.path.join(self.kok, "addon_data", "service.subtitles.defta"))
        self.assertEqual(ceviri_isi.is_bilgisi(durum, mesaj["is_id"])["video_anahtari"], oynayan)
        self.assertEqual((durum.secim(oynayan) or {}).get("is_id"), mesaj["is_id"])
        self.assertIn(("durdur", {"video": oynayan, "haric": durum.secim(oynayan)["anahtar"]}), gonderilen)


class Inceleme2Testleri(unittest.TestCase):
    """İki incelemenin bulduğu hataların testleri."""

    def setUp(self):
        self.kok = tempfile.mkdtemp()
        self.durum = kayit.Durum(self.kok)

    def tearDown(self):
        shutil.rmtree(self.kok)

    def test_cevrilmis_kaynak_yeniden_cevir_satiri_olmaz(self):
        v = _video(tur="dizi", imdb="tt1", sezon=1, bolum=5, dosya_adi="Ornek.S01E05.1080p.WEB-DL.x264-NTb.mkv")
        kaynak = Aday("os", "2", "en", ["Ornek.S01E05.1080p.WEB-DL.x264-NTb"])
        hazir = {"kimlik": "os-abc", "kaynak": kaynak.kayit(), "zaman": 1, "yol": "x.srt"}
        satirlar, _, _ = liste.olustur(v, [Aday("os", "2", "en", ["Ornek.S01E05.1080p.WEB-DL.x264-NTb"])], [hazir],
                                       None, None, {}, {}, _ayar())
        self.assertEqual([s.eylem for s in satirlar], ["ai"])          # kaynak bir daha "Çevir" diye çıkmaz
        eski_kayit = {"site": "os", "kimlik": "2", "dil": "en", "adlar": ["x"]}      # anahtar alanı olmayan eski kayıt
        self.assertEqual(liste.kaynak_anahtari(eski_kayit), "os:2")

    def test_ayni_sitedeki_ayni_adli_altyazilar_birlesmez(self):
        a1 = Aday("os", "1", "tr", ["Ornek.S01E05.1080p.WEB-DL.x264-NTb"])
        a2 = Aday("os", "2", "tr", ["Ornek.S01E05.1080p.WEB-DL.x264-NTb"])
        a3 = Aday("subdl", "/s/3.zip", "tr", ["Ornek.S01E05.1080p.WEB-DL.x264-NTb"])
        a4 = Aday("subsource", "4", "tr", ["Ornek.S01E05.1080p.WEB-DL.x264-NTb"], sdh=True)
        sonuc = liste.birlestir([a1, a2, a3, a4])
        self.assertEqual([a.anahtar for a in sonuc], ["os:1", "os:2", "subsource:4"])
        self.assertEqual([y.anahtar for y in sonuc[0].yedekler], ["subdl:/s/3.zip"])
        self.assertFalse(sonuc[0].sdh)

    def test_once_indirilen_altyazi_hak_harcamadan_kullanilir(self):
        v = _video(tur="dizi", imdb="tt1", sezon=1, bolum=5, dosya_adi="Ornek.S01E05.mkv")
        srt_ = "1\n00:00:01,000 --> 00:00:02,000\nBir\n\n2\n00:00:03,000 --> 00:00:04,000\nİki\n\n3\n00:00:05,000 --> 00:00:06,000\nÜç\n"
        cagri = []

        class Site:
            kod, ad = "os", "OpenSubtitles"

            def indir(self, aday):
                cagri.append(aday.anahtar)
                return srt_.encode("utf-8"), "a.srt"
        a = Aday("os", "1", "tr", ["Ornek.S01E05"], indirme={"file_id": 1})
        y1, _, o1 = arama.insan_altyazisi(a, v, [Site()], self.durum, lambda *x: None, lambda m: None)
        y2, _, o2 = arama.insan_altyazisi(a, v, [Site()], self.durum, lambda *x: None, lambda m: None)
        self.assertEqual((y1, o1), (y2, o2))
        self.assertEqual(cagri, ["os:1"])

    def test_kaymis_kopyalar_atlaninca_isaretliye_doner(self):
        # A seçili, B onun kopyası, C daha önce kaymış diye işaretlendi: B atlanınca sıra başa (C'ye) döner; "Başka
        # altyazı yok" denip A'da kalınmaz (C kendi imzası yüzünden kopya sanılıyordu)
        from d2 import giris
        xbmcvfs.kok = self.kok
        v = _video(tur="film", imdb="tt9", dosya_adi="Film.2019.1080p.WEB-DL.x264-GRP.mkv")
        srt_ = "1\n00:00:0{0},000 --> 00:00:0{0},900\n{1} bir\n\n2\n00:00:1{0},000 --> 00:00:1{0},900\n{1} iki\n\n" \
               "3\n00:00:2{0},000 --> 00:00:2{0},900\n{1} üç\n\n4\n00:00:3{0},000 --> 00:00:3{0},900\n{1} dört\n\n" \
               "5\n00:00:4{0},000 --> 00:00:4{0},900\n{1} beş\n"
        yollar = {}
        for ad, metin_ in (("os:A", srt_.format(1, "aynı")), ("os:B", srt_.format(1, "aynı")), ("os:C", srt_.format(5, "başka"))):
            yollar[ad] = kayit.metin_yaz(self.durum.yol("indirilen", ad[-1] + ".tr.srt"), metin_)
        son = {"sira": [["insan", "os:C"], ["insan", "os:A"], ["insan", "os:B"]]}
        sec = lambda ad: {"tur": "insan", "anahtar": ad, "yol": yollar[ad]}  # noqa: E731
        bildirim = len(xbmcgui.bildirimler)
        with mock.patch.object(giris, "_insan_hazirla", lambda d, v, s, ad: {"yol": yollar[ad], "secim": sec(ad)}):
            self.assertEqual(giris.kaymis(self.durum, v, son, sec("os:C")), yollar["os:A"])
            self.assertEqual(giris.kaymis(self.durum, v, son, sec("os:A")), yollar["os:C"])
        self.assertEqual(len(xbmcgui.bildirimler), bildirim)
        self.assertEqual(self.durum.secim(v.video_anahtari())["anahtar"], "os:C")

    def test_sezon_finalinde_belirsiz_bolum_kesin_sayilmaz(self):
        from d2 import siradaki
        n = _video(tur="dizi", imdb="tt1", sezon=1, bolum=11)
        belirsiz = Aday("subdl", "/a.zip", "en", ["Ornek.Season.1.1080p.WEB-DL"])
        self.assertFalse(siradaki._kesin(belirsiz, n, False))
        self.assertTrue(siradaki._kesin(Aday("os", "1", "en", ["x"], bolum_kesin=True), n, False))
        self.assertTrue(siradaki._kesin(Aday("os", "2", "en", ["Ornek.S01E11.720p"]), n, False))
        self.assertFalse(siradaki._kesin(Aday("os", "3", "en", ["Ornek.S01E11"], paket=True), n, False))
        v = _video(tur="dizi", anilist="99", anilist_bolum=12, sezon=1, bolum=12, dosya_adi="x.mkv")
        sonraki = siradaki._sonraki(v, 1, 13)
        self.assertEqual(sonraki.anilist_bolum, 13)
        yeni = siradaki._sonraki(v, 2, 1, yeni_sezon=True)
        self.assertEqual((yeni.anilist, yeni.anilist_bolum), ("", None))

    def test_jimaku_kimlikli_videoyu_adla_tahmin_etmez(self):
        j = Jimaku("anahtar1234", None)
        istekler = []

        def sahte(yontem, adres, basliklar=None, sorgu=None, **k):
            istekler.append(sorgu)
            return 200, [{"id": 1, "name": "One Piece", "anilist_id": 21}], {}
        with mock.patch("d2.ag.istek", sahte):
            self.assertEqual(j.kayit_bul(_video(tur="dizi", tmdb="111110", sezon=1, bolum=1, baslik="One Piece",
                                                dizi_adi="One Piece")), (None, None))
            self.assertNotIn({"query": "One Piece"}, istekler)
            kayit_, bolum = j.kayit_bul(_video(tur="dizi", sezon=1, bolum=1, dizi_adi="One Piece"))
            self.assertEqual((kayit_["id"], bolum), (1, 1))

    def test_esleme_acik_uclu_parcali_ve_oranli(self):
        ham = {"$meta": {"generated_on": "2026-11-15T00:00:00Z"},
               "tmdb_show:5:s1": {"anilist:7": {"1-12": "1-12", "14-": "13-"}},
               "tmdb_show:6:s1": {"anilist:8": {"1-12": "1-6,8-13"}},
               "tmdb_show:9:s1": {"anilist:10": {"13-": "14-|2"}}}
        kayit.yaz(self.durum.yol("esleme", "dizin.json"), eslesme.dizin_cikar(ham))
        kayit.yaz(self.durum.yol("esleme", "bilgi.json"), {"surum": eslesme.DIZIN_SURUMU, "zaman": 0})
        bul = lambda t, b: eslesme.anilist_bul(self.durum, _video(tur="dizi", tmdb=t, sezon=1, bolum=b))  # noqa: E731
        self.assertEqual(bul("5", 20), ("7", 19))
        self.assertIsNone(bul("5", 13))
        self.assertEqual(bul("6", 7), ("8", 8))
        self.assertEqual(bul("9", 14), ("10", 16))
        kayit.yaz(self.durum.yol("esleme", "bilgi.json"), {"zaman": 0})                # eski biçim kullanılmaz
        eslesme._dizin_onbellek.clear()
        self.assertIsNone(bul("5", 20))

    def test_surum_adi_kenar_durumlari(self):
        self.assertIsNone(surum.ayristir("Spider-Man.2002").grup)
        self.assertEqual(surum.ayristir("Show.S01E09-10bit.x265").bolumler, (9,))
        self.assertIsNone(surum.ayristir("Mad.Max.Fury.Road.2015.1080p.WEB-DL.H264-FGT").hizmet)
        self.assertEqual(surum.ayristir("Blade.Runner.2049.2017.1080p.BluRay.x264-SPARKS").yil, 2017)
        self.assertEqual(surum.ayristir("2001.A.Space.Odyssey.1968.1080p.BluRay").baslik, "2001 A Space Odyssey")
        self.assertIsNone(surum.bolum_tutar_mi("Ad - 15", 2, 3))           # 2. sezonda çıplak numara belirsiz
        self.assertTrue(surum.bolum_tutar_mi("Ad - 15", 2, 3, mutlak=15))
        self.assertFalse(surum.bolum_tutar_mi("Ad - 15", 1, 3))

    def test_arsiv_ve_kodlama(self):
        srt_ = "1\n00:00:01,000 --> 00:00:02,000\nBir\n\n2\n00:00:03,000 --> 00:00:04,000\nİki\n\n3\n00:00:05,000 --> 00:00:06,000\nÜç\n"
        v = _video(tur="dizi", sezon=1, bolum=5)
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, "w") as z:
            z.writestr("Turkish.SDH.srt", srt_ + "4\n00:00:07,000 --> 00:00:08,000\n[kapı]\n")
            z.writestr("Turkish.srt", srt_)
        metin_, _ = indir.altyazi_cikar(tampon.getvalue(), "a.zip", v)       # bölüm yazmayan tek bölümlük arşiv
        self.assertNotIn("[kapı]", metin_)
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, "w") as z:
            z.writestr("Ornek.S01E06.srt", srt_)
        with self.assertRaises(indir.IndirmeHatasi):
            indir.altyazi_cikar(tampon.getvalue(), "a.zip", v)               # tek dosya ama başka bölüm
        with mock.patch("tempfile.NamedTemporaryFile", side_effect=AssertionError("diske yazılmamalı")):
            metin_, uzanti = indir.altyazi_cikar(srt_.encode("cp1254"), "a.srt")
        self.assertIn("İki", metin_)
        self.assertEqual(indir._yakin_film_hizi(24.0), 24.0)
        self.assertAlmostEqual(indir.kare_hizi_orani(24.0, 25.0, srt_, ".srt", 3000), 24.0 / 25.0)

    def test_ikinci_sezon_paketinde_numarali_dosyalar(self):
        srt_ = "1\n00:00:01,000 --> 00:00:02,000\nBir\n\n2\n00:00:03,000 --> 00:00:04,000\nİki\n\n3\n00:00:05,000 --> 00:00:06,000\nÜç\n"
        v = _video(tur="dizi", sezon=2, bolum=2)
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, "w") as z:
            for n in (1, 2, 3):
                z.writestr(f"Show - {n:02d}.srt", srt_ + ("4\n00:00:07,000 --> 00:00:08,000\nek\n" * n))
        _, ad = indir._arsivden(tampon.getvalue(), v)
        self.assertEqual(ad, "Show - 02.srt")
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, "w") as z:
            z.writestr("Show - 05.srt", srt_)
            z.writestr("Show - 06.srt", srt_)
        with self.assertRaises(indir.IndirmeHatasi):                        # hiçbiri tutmuyor: yanlış bölüm verilmez
            indir._arsivden(tampon.getvalue(), v)

    def test_zamaninda_yanit_vermeyen_site_hata_sayilir(self):
        import threading

        class Yavas:
            kod, ad = "subdl", "SubDL"

            def ara(self, video, diller):
                threading.Event().wait(1.0)
                return []
        with mock.patch.object(arama, "ARAMA_SURESI", 0.2):
            adaylar, hatalar = arama.ara([Yavas()], _video(tur="film", baslik="x"), ["tr"], lambda m: None)
        self.assertEqual([(h.site, h.tur) for h in hatalar], [("SubDL", "ag")])

    def test_bozuk_yenilenme_zamani_siteyi_yillarca_kapatmaz(self):
        # Günlük hak en geç bir gün sonra yenilenir. Site uzak bir tarih bildirirse kota kaydı o tarihe kadar duruyor,
        # sitenin satırları gizleniyor ve indirme denenmiyordu (kayıt önbellek temizliğinde de silinmez).
        import time
        simdi = time.time()
        hata = SiteHatasi("OpenSubtitles", "kota", yenilenme="2099-01-01T00:00:00.000Z")
        arama.hatalari_isle([hata], self.durum, lambda *a: None, lambda m: None)
        self.assertIsNotNone(self.durum.hak_doldu("os"))
        self.assertLessEqual(self.durum.depo.oku("kota_os")["bitis"], simdi + 86400 + 60)
        # Sitenin bildirdiği yakın zaman olduğu gibi kullanılır
        yakin = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(simdi + 7200))
        self.assertAlmostEqual(arama._yenilenme(SiteHatasi("OpenSubtitles", "kota", yenilenme=yakin))[1], simdi + 7200,
                               delta=2)

    def test_aramada_adi_baska_filmin_atilir_ve_kayda_yazilir(self):
        class Site:
            kod, ad, elenen, elenen_adlari = "os", "OpenSubtitles", 2, ["Andhadhun"]

            def ara(self, video, diller):
                return [Aday("os", "1", "tr", ["Interstellar.2014.720p.BluRay.x264-DAA"]),
                        Aday("os", "2", "tr", ["2307 Winter's Dream 2016 720p BRRip 750 MB - iExTV"])]
        v = _video(tur="film", dosya_adi="Interstellar.2014.2014.1080p.BluRay.x264.YIFY.mp4")
        v.baslik, v.ad_kaynagi, v.yil = v.surum.baslik, "surum", 2014
        kayit_ = []
        adaylar, _ = arama.ara([Site()], v, ["tr"], kayit_.append)
        self.assertEqual([a.kimlik for a in adaylar], ["1"])
        self.assertIn("(2 tanesi başka yapımın, atıldı: Andhadhun) (1 tanesinin adı başka filmin, atıldı: "
                      "2307 Winter's Dream (2016))", kayit_[0])

    def test_silinen_isin_kaydi_yeniden_yaratilmaz(self):
        bilgi = ceviri_isi.is_kur(self.durum, {"is_id": "x1", "video_anahtari": "v", "icerik": "i", "yapim": "y",
                                               "goreli": "g", "kaynak": {}, "hedef_dil": "tr", "ceviri_kimligi": "k"},
                                  "1\n00:00:01,000 --> 00:00:02,000\nA\n", ".srt")
        is_ = ceviri_isi.CeviriIsi(self.durum, bilgi, "sk-test", {"hedef_dil": "tr"})
        shutil.rmtree(is_.klasor)
        is_.kaydet(durum="durdu")
        is_.oturumu_kaydet()
        self.assertFalse(os.path.exists(is_.klasor))

    def test_kaydi_olmayan_is_klasoru_de_temizlenir(self):
        # Bakım silerken geç biten işin yeniden yarattığı klasörde is.json yoktur: 30 gün sonra o da silinir
        import time
        from d2 import bakim
        kalinti = self.durum.yol("isler", "kalinti")
        kayit.yaz(os.path.join(kalinti, "onbellek", "x.json"), {"sonuc": {}})
        kayit.yaz(self.durum.yol("isler", "yeni", "hizli_kayit", "x.json"), {"sonuc": {}})
        eski = time.time() - bakim.KALINTI_OMRU - 60
        os.utime(kalinti, (eski, eski))
        bakim.kendiliginden(self.durum)
        self.assertEqual(os.listdir(self.durum.yol("isler")), ["yeni"])

    def test_yonlendirmede_anahtar_baska_sunucuya_gitmez(self):
        import urllib.request
        istek_ = urllib.request.Request("https://api.subdl.com/x", headers={"Authorization": "Bearer gizli123",
                                                                          "X-API-Key": "k"})
        y = ag._GuvenliYonlendirme().redirect_request(istek_, None, 302, "Found", {}, "https://cdn.example.com/f")
        self.assertNotIn("Authorization", y.headers)
        self.assertFalse(any(a.lower() == "x-api-key" for a in y.headers))
        y2 = ag._GuvenliYonlendirme().redirect_request(istek_, None, 302, "Found", {}, "https://api.subdl.com/y")
        self.assertIn("Authorization", y2.headers)

    def test_gecersiz_adres_hatasi_adresi_yazmaz(self):
        # OpenSubtitles'ın indirme bağlantısı jetonu yolunda taşır. Bağlantı boşluklu ya da şemasız gelirse Python'un
        # hata yazısı adresin kendisini içerir ("URL can't contain control characters. '/download/JETON/...'"); bu
        # yazı kayda ve bildirime gidiyordu. İstek bağlantı kurulmadan reddedilir (ağ gerekmez).
        for adres in ("https://www.site.invalid/download/JETON123456/subfile/Ornek Film 2014.srt",
                      "www.site.invalid/download/JETON123456/subfile/Ornek.srt",
                      "https://www.site.invalid:abc/download/JETON123456/Ornek.srt"):
            with self.assertRaises(ag.AgHatasi) as h:
                ag.istek("GET", adres, ham=True, deneme=1, zaman_asimi=2)
            for parca in ("JETON123456", "download", "Ornek", "site.invalid"):
                self.assertNotIn(parca, str(h.exception), adres)

    def test_dev_yanit_bellege_alinmaz(self):
        # Yanıt sınırsız okunuyordu: indirme bağlantısı yanlışlıkla bir video dosyası verirse hepsi belleğe alınıyordu
        class Yanit:
            status, headers = 200, {}

            def __init__(self, boyut):
                self.kalan = boyut

            def read(self, n=-1):
                n = self.kalan if n is None or n < 0 else min(n, self.kalan)
                self.kalan -= n
                return b"x" * n

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        with mock.patch.object(ag, "EN_BUYUK_YANIT", 1000):
            with mock.patch.object(ag._ACICI, "open", lambda *a, **k: Yanit(1000)):
                self.assertEqual(len(ag.istek("GET", "https://site.invalid/a", ham=True, deneme=1)[1]), 1000)
            dev = Yanit(10 ** 12)
            with mock.patch.object(ag._ACICI, "open", lambda *a, **k: dev):
                with self.assertRaises(ag.AgHatasi):
                    ag.istek("GET", "https://site.invalid/a", ham=True, deneme=1)
            self.assertGreater(dev.kalan, 10 ** 12 - 2000)          # yalnız sınır kadarı okundu

    def test_anahtar_adlari_siki_tanınir(self):
        self.assertIsNone(anahtar.tur_bul("OPENSUBTITLES_USER_AGENT"))
        self.assertIsNone(anahtar.tur_bul("OPENSUBTITLES_APP_NAME"))
        self.assertIsNone(anahtar.tur_bul("OPENAI_ORG_ID"))
        self.assertEqual(anahtar.tur_bul("OPENSUBTITLES_USERNAME"), "os_kullanici")
        self.assertEqual(anahtar.tur_bul("SUBDL"), "subdl_anahtar")
        # "OS" kısaltması yalnız adın başında OpenSubtitles'tır: başka hizmetin anahtarı ("…OS_API_KEY") ve sayı
        # sınırı ("…_TOKEN_LIMIT") dosyada önce geliyorsa asıl anahtarın yerine kaydediliyordu
        for baska in ("PHOTOS_API_KEY", "KRONOS_API_TOKEN", "OPENAI_TOKEN_LIMIT"):
            self.assertIsNone(anahtar.tur_bul(baska), baska)
        self.assertEqual(anahtar.tur_bul("OS_API_KEY"), "os_anahtar")
        self.assertEqual(anahtar.oku_metin("PHOTOS_API_KEY=baska\nOPENAI_TOKEN_LIMIT=4000\nOPENSUBTITLES_API_KEY=osk1\n"
                                           "OPENAI_API_KEY=sk-1\n"), {"os_anahtar": "osk1", "openai_anahtar": "sk-1"})
        self.assertEqual(anahtar.oku_metin("﻿OPENAI_API_KEY=sk-abc\nOPENSUBTITLES_APP_NAME=defta\n"),
                         {"openai_anahtar": "sk-abc"})

    def test_parolanin_son_karakterleri_dosyaya_yazilmaz(self):
        # Anahtar geçmişi (bağlantı testinde gösterilen son 4 karakter) yalnız API anahtarları içindir; parolanın son
        # 4 karakteri hiçbir yerde gösterilmez, durum dosyasına da yazılmaz (kısa parolanın tamamı olurdu)
        from d2 import eylemler, servis
        xbmcvfs.kok = self.kok
        xbmcaddon.ayarlar.clear()
        self.addCleanup(xbmcaddon.ayarlar.clear)
        s = servis.Servis()
        s.durum = self.durum
        s._anahtar_gecmisi_kur()
        xbmcaddon.ayarlar.update({"os_parola": "sifrem", "os_anahtar": "osk-abcd1234"})
        s.ayarlar_degisti()
        eylemler._gecmis_yaz(self.durum, {"os_parola": "yenisi", "subdl_anahtar": "sd-9999"}, {"os_parola": "sifrem"})
        son, gecmis = self.durum.depo.oku("anahtar_son4"), self.durum.depo.oku("anahtar_gecmisi")
        self.assertEqual((son["os_anahtar"], son["subdl_anahtar"]), ("1234", "9999"))
        self.assertNotIn("os_parola", son)
        self.assertNotIn("os_parola", gecmis)

    def test_latin_olmayan_hedef_dil_kendi_alfabesiyle(self):
        self.assertEqual(ceviri_isi.cekirdek_ayari({"hedef_dil": "ja"})["ceviri"]["cikti_alfabesi"], "serbest")
        self.assertEqual(ceviri_isi.cekirdek_ayari({"hedef_dil": "tr"})["ceviri"]["cikti_alfabesi"], "latin")

    def test_baglanti_testi_anahtarsiz_siteleri_de_listeler(self):
        import ayarlar_uret
        from d2 import eylemler
        xbmcaddon.ayarlar.clear()
        xbmcaddon.ayarlar.update(ayarlar_uret.varsayilanlar())
        xbmcvfs.kok = self.kok
        yazi = eylemler.baglanti_testi(goster=False)
        for ad in ("OpenAI", "OpenSubtitles", "SubDL", "SubSource", "Jimaku"):
            self.assertIn(ad + ":", yazi)
        self.assertEqual(yazi.count(metin.al(metin.P_TEST_ANAHTAR_YOK)), 5)

    def test_gomulu_dil_tanima(self):
        self.assertTrue(video_modulu.dil_eslesir("tur", "tr"))
        self.assertTrue(video_modulu.dil_eslesir("Arabic", "ar"))
        self.assertTrue(video_modulu.dil_eslesir("chi", "zh"))
        self.assertFalse(video_modulu.dil_eslesir("pt-br", "pt"))
        self.assertTrue(video_modulu.dil_eslesir("pob", "pt-BR"))
        self.assertFalse(video_modulu.dil_eslesir("eng", "tr"))


class Karar0126Testleri(unittest.TestCase):
    """0.1.26: denetimden sonra kullanıcının kararlaştırdığı düzeltmeler."""

    def setUp(self):
        self.kok = tempfile.mkdtemp()
        self.durum = kayit.Durum(self.kok)
        self.addCleanup(shutil.rmtree, self.kok, True)

    def test_temizlik_secili_altyaziyi_silmez(self):
        # 30 günü geçen indirilen altyazılar silinir; bir videoda seçili olan kalır (yeniden indirmek hak harcar)
        import time
        from d2 import bakim
        secili, oteki = self.durum.yol("indirilen", "a", "secili.srt"), self.durum.yol("indirilen", "a", "oteki.srt")
        for yol in (secili, oteki):
            kayit.metin_yaz(yol, "1\n00:00:01,000 --> 00:00:02,000\nA\n")
            eski = time.time() - bakim.KALINTI_OMRU - 60
            os.utime(yol, (eski, eski))
        self.durum.secim_yaz("video|x", {"anahtar": "os:1", "yol": secili})
        bakim.kendiliginden(self.durum)
        self.assertEqual((os.path.exists(secili), os.path.exists(oteki)), (True, False))

    def test_indirme_kendiliginden_yeniden_denenmez(self):
        # Yanıtı gelmeyen indirme isteği de günlük haktan düşmüş olabilir: SubDL, SubSource ve Jimaku'da indirme tek
        # kez gider (arama eskisi gibi bir kez yeniden denenir)
        denemeler = []

        def sahte(yontem, adres, basliklar=None, sorgu=None, ham=False, deneme=2, **k):
            denemeler.append((ham, deneme))
            return 200, b"PK" if ham else {"data": []}, {}
        with mock.patch("d2.ag.istek", sahte):
            SubDL("k").indir(Aday("subdl", "/subtitle/1.zip", "tr", ["x"], indirme={"yol": "/subtitle/1.zip"}))
            SubSource("k").indir(Aday("subsource", "5", "tr", ["x"], indirme={"kimlik": 5}))
            Jimaku("j").indir(Aday("jimaku", "9/f.ass", "ja", ["f.ass"], indirme={"yol": "/entry/9/download/f.ass", "ad": "f.ass"}))
            SubSource("k")._iste("movies/search", {"q": "x"})
        self.assertEqual(denemeler, [(True, 1), (True, 1), (True, 1), (False, 2)])

    def test_jimaku_kaydinda_tam_adres_yok(self):
        istenen = []

        def sahte(yontem, adres, basliklar=None, sorgu=None, **k):
            istenen.append(adres)
            if "entries/search" in adres:
                return 200, [{"id": 9, "name": "Sousou no Frieren", "anilist_id": 154587}], {}
            if "/files" in adres:
                return 200, [{"name": "Frieren 05.ass", "url": "https://jimaku.cc/entry/9/download/f05.ass", "size": 5000}], {}
            return 200, b"icerik", {}
        with mock.patch("d2.ag.istek", sahte):
            j = Jimaku("j")
            a = j.ara(_video(tur="dizi", sezon=1, bolum=5, dizi_adi="Sousou no Frieren"), ["ja"])[0]
            self.assertNotIn("://", json.dumps(a.kayit()))
            self.assertEqual(a.kimlik, "9/Frieren 05.ass")
            j.indir(Aday.kayittan(a.kayit()))
            self.assertEqual(istenen[-1], "https://jimaku.cc/entry/9/download/f05.ass")
            # eski sürümün kaydı (tam adres) da indirilir; başka sunucunun adresi reddedilir
            j.indir(Aday("jimaku", "x", "ja", ["f.ass"], indirme={"adres": "https://jimaku.cc/entry/9/download/eski.ass"}))
            self.assertTrue(istenen[-1].endswith("eski.ass"))
            with self.assertRaises(SiteHatasi):
                j.indir(Aday("jimaku", "x", "ja", ["f.ass"], indirme={"yol": "//kotu.example/f.ass"}))

    def test_jeton_gorunumlu_dosya_adi_alinmaz(self):
        # Akış adresinin son parçası video uzantılı olsa da jetonsa sürüm adı sayılmaz: kayda ve sitelere gitmez
        f = video_modulu.adres_dosya_adi
        self.assertEqual(f("https://s.example/dl/9f8a7c6b5d4e3f2a1b0c9d8e7f6a5b4c.mkv"), "")
        self.assertEqual(f("https://s.example/dl/eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.mp4?x=1"), "")
        for ad in ("Interstellar.2014.2160p.UHD.BluRay.REMUX.HEVC-EPSiLON.mkv", "video.mp4",
                   "[Coalgirls]_Shingeki_no_Kyojin_03_(1280x720_Blu-ray_FLAC)_[FB1A088F].mkv",
                   "Supercalifragilisticexpialidocious.1964.1080p.mkv"):
            self.assertEqual(f("https://s.example/dl/" + ad), ad)

    def test_eksik_kalan_arama_onbellege_girmez(self):
        # Ek sorgusu ya da öteki sayfası alınamayan sitenin sonucu eksiktir: 15 dakika saklanmaz
        from d2 import giris
        xbmcvfs.kok = self.kok

        class Site:
            kod, ad = "os", "OpenSubtitles"
            cagri = 0

            def __init__(self, eksik):
                self.eksik_kaldi = eksik

            def ara(self, video, diller):
                Site.cagri += 1
                return [Aday("os", "1", "tr", ["Ornek.2014.1080p"])]
        v = _video(tur="film", baslik="Ornek", yil=2014)
        with mock.patch.object(giris, "_kayit", lambda m: None):
            giris._onbellekle_ara([Site(True)], v, ["tr"])
            giris._onbellekle_ara([Site(True)], v, ["tr"])
            self.assertEqual(Site.cagri, 2)                     # saklanmadı: yeniden arandı
            giris._onbellekle_ara([Site(False)], v, ["tr"])
            giris._onbellekle_ara([Site(False)], v, ["tr"])
            self.assertEqual(Site.cagri, 3)                     # tam sonuç saklandı

    def test_arsivden_secilen_dilin_dosyasi_alinir(self):
        # Çok dilli arşivde (film.en.srt + film.tr.srt) listede seçilen dilin dosyası verilir; adı dil yazmayan arşivde
        # eski kural (srt, SDH olmayan, en büyük) geçerli
        govde = "".join(f"{n}\n00:00:0{n},000 --> 00:00:0{n},500\nx\n\n" for n in range(1, 5))
        z = io.BytesIO()
        with zipfile.ZipFile(z, "w") as arsiv:
            arsiv.writestr("Film.2014.en.srt", govde.replace("x", "English line that is longer"))
            arsiv.writestr("Film.2014.tr.srt", govde.replace("x", "Türkçe"))
            arsiv.writestr("Film.2014.Turkish.SDH.srt", govde.replace("x", "[ses] Türkçe"))
        film = _video(tur="film", baslik="Film", yil=2014)
        self.assertIn("Türkçe", indir.altyazi_cikar(z.getvalue(), "a.zip", film, dil="tr")[0])
        self.assertNotIn("[ses]", indir.altyazi_cikar(z.getvalue(), "a.zip", film, dil="tr")[0])
        self.assertIn("English", indir.altyazi_cikar(z.getvalue(), "a.zip", film, dil="en")[0])
        self.assertIn("English", indir.altyazi_cikar(z.getvalue(), "a.zip", film)[0])          # dil bilinmiyor: en büyük
        self.assertEqual([indir._dosya_dili(a) for a in ("It.2017.1080p.srt", "De.Film.srt", "Film.pt-BR.srt", "Turkish.srt")],
                         ["", "", "pt", "tr"])

    def test_senkron_uyumlu_icin_kaynak_da_tutmali(self):
        v = "Film.2014.1080p.BluRay.x264.DTS-GRUP"
        self.assertTrue(puan.uyumlu_mu(*puan.puanla(v, ["Film.2014.720p.BluRay.x265-GRUP"])))
        # aynı grubun WEB-DL sürümü: grup, ses, çözünürlük ve kodek tutsa da zamanlama kaynağa bağlıdır
        p, kurgu = puan.puanla(v, ["Film.2014.1080p.WEB-DL.x264.DTS-GRUP"])
        self.assertEqual((p, puan.uyumlu_mu(p, kurgu)), (puan.ESIK - 1, False))

    def test_subsource_ulke_eki_atilarak_yeniden_aranir_ve_test_hak_gostermez(self):
        aramalar = []

        def sahte(yontem, adres, basliklar=None, sorgu=None, **k):
            if "movies/search" in adres:
                aramalar.append(sorgu["q"])
                kayitlar = [{"movieId": 7, "title": "The Office", "season": 1}] if sorgu["q"] == "the office" else []
                return 200, {"data": kayitlar}, {"x-ratelimit-remaining": "58"}
            return 200, {"data": [{"subtitleId": 1, "language": "turkish", "releaseInfo": ["The.Office.S01E02.WEB"]}]}, {}
        with mock.patch("d2.ag.istek", sahte):
            a = SubSource("k").ara(_video(tur="dizi", sezon=1, bolum=2, dizi_adi="The Office (US)"), ["tr"])
            self.assertEqual((aramalar, [x.kimlik for x in a]), (["the office (us)", "the office"], ["1"]))
            self.assertEqual(SubSource("k").test(), {"calisiyor": True, "kalan": None})

    def _servis(self):
        import ayarlar_uret
        from d2 import servis
        xbmcvfs.kok = self.kok
        xbmcaddon.ayarlar.clear()
        xbmcaddon.ayarlar.update(ayarlar_uret.varsayilanlar())
        xbmcaddon.ayarlar["openai_anahtar"] = "sk-test"
        self.addCleanup(xbmcaddon.ayarlar.clear)
        self.addCleanup(xbmcgui.pencere.clear)
        s = servis.Servis()
        s.durum, s.izleyici = self.durum, xbmc.Monitor()
        return s

    def test_otomatik_kaymis_isaretliyi_yuklemez(self):
        # Bu videoda kaymış diye işaretlenen altyazı oynatma başında kendiliğinden yeniden indirilmez
        s = self._servis()
        xbmcaddon.ayarlar["oto_hedef"] = True
        v = _video(tur="film", baslik="Ornek", yil=2014, imdb="tt9", dosya_adi="Ornek.2014.1080p.BluRay.x264-GRP.mkv")
        s.video = v
        kaymis, saglam = Aday("os", "1", "tr", ["Ornek.2014.1080p.BluRay.x264-GRP"]), Aday("os", "2", "tr", ["Ornek.2014.720p.HDTV"])
        self.durum.kaymis_isaretle(v.video_anahtari(), kaymis.anahtar)
        indirilen = []
        xbmc.oynatici["oynuyor"] = True
        self.addCleanup(xbmc.oynatici.update, {"oynuyor": False})

        def insan(a, *b, **k):
            indirilen.append(a.kimlik)
            return os.path.join(self.kok, "x.srt"), a, "ozet"
        with mock.patch.object(arama, "siteler", lambda *a, **k: []), \
                mock.patch.object(arama, "ara", lambda *a, **k: ([kaymis, saglam], [])), \
                mock.patch.object(arama, "insan_altyazisi", insan):
            s._otomatik(v)
        self.assertEqual(indirilen, ["2"])

    def test_siradaki_soru_yinelenmez_ve_ozel_bolumden_sonra_ilk_sezon_gelmez(self):
        from d2 import siradaki
        s = self._servis()
        v = _video(tur="dizi", imdb="tt1", sezon=1, bolum=5, dizi_adi="Ornek", dosya_adi="Ornek.S01E05.1080p.WEB-DL.mkv")
        kaynak = Aday("os", "9", "en", ["Ornek.S01E06.1080p.WEB-DL"], bolum_kesin=True)
        sorular, baslayan, aranan = [], [], []

        def ara(siteler_, n, *a, **k):
            aranan.append((n.sezon, n.bolum))
            return [kaynak], []
        with mock.patch.object(arama, "siteler", lambda *a, **k: []), mock.patch.object(arama, "ara", ara), \
                mock.patch.object(siradaki, "_soru", lambda sure, *yazilar: sorular.append(1) or False), \
                mock.patch.object(siradaki, "_baslat", lambda *a, **k: baslayan.append(1)):
            siradaki.bak(s, v, 1)                   # İptal dendi
            siradaki.bak(s, v, 1)                   # aynı bölüm yeniden izlendi: sorulmaz
            self.assertEqual((sorular, baslayan), ([1], []))
            # Özel bölümden (S00) sonra: yalnız sıradaki özel bölüme bakılır, S01E01'e geçilmez
            del aranan[:]
            ozel = _video(tur="dizi", imdb="tt1", sezon=0, bolum=3, dizi_adi="Ornek")
            with mock.patch.object(arama, "ara", lambda siteler_, n, *a, **k: aranan.append((n.sezon, n.bolum)) or ([], [])):
                siradaki.bak(s, ozel, 1)
            self.assertEqual(aranan, [(0, 4)])

    def test_baslamis_on_ceviri_yeniden_sorulmaz_kaldigi_yerden_surer(self):
        from d2 import siradaki
        s = self._servis()
        v = _video(tur="dizi", imdb="tt1", sezon=1, bolum=5, dizi_adi="Ornek", dosya_adi="Ornek.S01E05.1080p.WEB-DL.mkv")
        kaynak = Aday("os", "9", "en", ["Ornek.S01E06.1080p.WEB-DL"], bolum_kesin=True)
        n = siradaki._sonraki(v, 1, 6)
        ceviri_isi.is_kur(self.durum, {"is_id": "on1", "video_anahtari": n.icerik_anahtari() + "|?",
                                       "icerik": n.icerik_anahtari(), "yapim": "ad:ornek", "goreli": "S01E06/os-9",
                                       "kaynak": kaynak.kayit(), "hedef_dil": "tr", "ceviri_kimligi": "os-9",
                                       "on_ceviri": True, "durum": "durdu"},
                          "1\n00:00:01,000 --> 00:00:02,000\nA\n", ".srt")
        sorular, baslayan = [], []
        s.is_baslat = lambda is_id, **k: baslayan.append((is_id, k))
        with mock.patch.object(arama, "siteler", lambda *a, **k: []), \
                mock.patch.object(arama, "ara", lambda *a, **k: ([kaynak], [])), \
                mock.patch.object(siradaki, "_soru", lambda sure, *yazilar: sorular.append(1) or True):
            siradaki.bak(s, v, 1)
        self.assertEqual((sorular, baslayan), ([], [("on1", {"hizli": False, "kendiliginden": True})]))

    def test_sag_ust_kutu_sure_dolunca_cevir_iptalde_hayir(self):
        from d2 import kutu
        xbmcvfs.kok = self.kok
        self.addCleanup(setattr, xbmcgui.WindowXMLDialog, "tiklanacak", None)
        yazilar = ("Sıradaki bölüm", "Örnek Dizi · S01E02", "Ornek.S01E02.WEB · OpenSubtitles", "Çevir", "İptal")
        xbmcgui.WindowXMLDialog.tiklanacak = kutu.IPTAL
        self.assertFalse(kutu.sor(*yazilar, 15))
        xbmcgui.WindowXMLDialog.tiklanacak = kutu.CEVIR
        self.assertTrue(kutu.sor(*yazilar, 15))
        xbmcgui.WindowXMLDialog.tiklanacak = None              # kimse basmadı: süre dolunca Çevir
        asil = kutu.threading.Event.wait             # geri sayımın beklemesi kısaltılır
        with mock.patch.object(kutu.threading.Event, "wait",
                               lambda self_, sure=None: asil(self_, 0.001 if sure == kutu.ADIM else sure)):
            self.assertTrue(kutu.sor(*yazilar, 2))
        xml = os.path.join(KOK, "service.subtitles.defta", "resources", "skins", "default", "1080i", kutu.DOSYA)
        import xml.etree.ElementTree as ET
        kok = ET.parse(xml).getroot()
        kimlikler = [c.get("id") for c in kok.iter("control") if c.get("id")]
        self.assertEqual(len(kimlikler), len(set(kimlikler)))                 # yinelenen kimlik yazıyı boş bırakır
        self.assertTrue({str(k) for k in (kutu.BASLIK, kutu.BASLIK_TEMA, kutu.BOLUM, kutu.ALTYAZI, kutu.KALAN,
                                          kutu.CEVIR, kutu.IPTAL) + kutu.CIZGILER} <= set(kimlikler))
        cizgi = next(c for c in kok.iter("control") if c.get("id") == str(kutu.CIZGILER[0]))
        self.assertEqual(int(cizgi.find("width").text), kutu.CIZGI_GENISLIK)  # kod çizgiyi bu genişlikten kısaltır
        medya = os.path.join(os.path.dirname(os.path.dirname(xml)), "media")
        eklenti = os.path.join(KOK, "service.subtitles.defta")
        for doku in {t.text for t in kok.iter() if t.tag in ("texture", "texturefocus") and t.text}:
            # eklentinin logosu kendi klasöründen (special://home/addons/<kimlik>/icon.png), ötekiler görünümün media'sından
            on_ek = "special://home/addons/service.subtitles.defta/"
            yol = os.path.join(eklenti, doku[len(on_ek):]) if doku.startswith(on_ek) else os.path.join(medya, doku)
            self.assertTrue(os.path.exists(yol), doku)

    def test_kart_bolumu_ve_cevrilecek_altyaziyi_yazar_cizgi_kisalir(self):
        from d2 import kutu, siradaki
        from d2.siteler import Aday
        n = self._video() if hasattr(self, "_video") else None
        if n is None:
            from d2 import video as vm
            n = vm.Video()
            n.tur, n.dizi_adi, n.sezon, n.bolum = "dizi", "Örnek Dizi", 1, 2
        aday = Aday("os", "1", "en", ["Ornek.Dizi.S01E02.1080p.WEB-DL"], yukleyen="os-auto", sdh=True)
        bolum, altyazi = siradaki._kart_yazilari(n, aday)
        self.assertIn("S01E02", bolum)
        self.assertEqual(altyazi, "Ornek.Dizi.S01E02.1080p.WEB-DL · OpenSubtitles (os-auto) · makine yapımı")
        k = kutu.SiradakiKutu()
        k.hazirla("Sıradaki bölüm", bolum, altyazi, "Çevir", "İptal", 10)
        k._kalan_yaz(10.0)
        self.assertEqual((k.getControl(kutu.CIZGILER[0]).genislik, k.getControl(kutu.KALAN).yazi), (kutu.CIZGI_GENISLIK, "10 sn"))
        k._kalan_yaz(2.5)
        self.assertEqual((k.getControl(kutu.CIZGILER[1]).genislik, k.getControl(kutu.KALAN).yazi), (96, "3 sn"))

    def test_yarida_kalan_yeniden_ceviri_kaldigi_yerden_surer(self):
        # "Yeniden çevir" yarıda kaldıysa (video kapandı, hata) tekrar seçilince yeni iş kurulmaz: soru sorulmaz,
        # aynı iş "devam" ile sürer (ödenen kısım yeniden ödenmez)
        from d2 import giris, kodi
        xbmcvfs.kok = self.kok
        durum = kayit.Durum(kodi.profil())
        v = _video(tur="dizi", imdb="tt1", sezon=1, bolum=5, dizi_adi="Ornek", dosya_adi="Ornek.S01E05.1080p.WEB-DL.mkv")
        klasor = durum.ceviri_klasoru(v.icerik_anahtari())
        kayit.metin_yaz(os.path.join(klasor, "AI.os_1_1.tr.srt"), "1\n00:00:01,000 --> 00:00:02,000\nA\n")
        kayit.metin_yaz(os.path.join(klasor, "os-1.kaynak.srt"), "1\n00:00:01,000 --> 00:00:02,000\nA\n")
        kayit.yaz(os.path.join(klasor, "os-1.json"), {"kimlik": "os-1", "hedef_dil": "tr", "dosya": "AI.os_1_1.tr.srt",
                                                      "kaynak_dosya": "os-1.kaynak.srt", "kaynak": {"site": "os", "kimlik": "1"}})
        secili = {"tur": "ai", "anahtar": "ai:os-1", "yol": os.path.join(klasor, "AI.os_1_1.tr.srt")}
        gonderilen, sorulan = [], []
        self.addCleanup(xbmcaddon.ayarlar.clear)
        with mock.patch.object(kodi, "servise_gonder", lambda ad, veri=None: gonderilen.append((ad, veri))), \
                mock.patch.object(kodi, "secim", lambda *a: sorulan.append(1) or 0), \
                mock.patch.object(giris, "_bildir", lambda *a, **k: None):
            giris.yeniden(durum, v, secili)
            giris.yeniden(durum, v, secili)
        self.assertEqual(([ad for ad, _ in gonderilen], len(sorulan)), (["cevir", "devam"], 1))
        self.assertEqual(gonderilen[0][1]["is_id"], gonderilen[1][1]["is_id"])
        self.assertEqual(len(os.listdir(durum.yol("isler"))), 1)


class Karar0134Testleri(unittest.TestCase):
    """0.1.34: altyazı seçimi (son seçilenin devamı), makine yapımı kaynak, ayar düğmeleri."""

    def setUp(self):
        self.kok = tempfile.mkdtemp()
        self.durum = kayit.Durum(self.kok)
        self.addCleanup(shutil.rmtree, self.kok, True)
        xbmcvfs.kok = self.kok

    def _adaylar(self, bolum):
        oto = Aday("os", f"oto{bolum}", "en", [f"Ornek.Dizi.S01E0{bolum}.Bolum.Adi.{bolum}.1080p.WEB-DL.H264"],
                   yukleyen="os-auto", sdh=True)
        eski = Aday("os", f"eski{bolum}", "en", [f"Ornek.Dizi.01x0{bolum} - Baska Ad {bolum}"], yukleyen="Anonymous",
                    fps=25.0)
        return oto, eski

    def test_son_secilen_altyazinin_devami_ilk_secilir(self):
        # 1. bölümde kullanıcı SDH'li "os-auto" altyazısını seçti. 2. bölümde aynı ailenin devamı, "SDH alta" açık
        # olsa da ilk sıradadır; tercih yokken SDH'siz olan öndedir.
        oto1, eski1 = self._adaylar(1)
        oto2, eski2 = self._adaylar(2)
        self.assertEqual(liste.aile_imzasi(oto1), liste.aile_imzasi(oto2))        # bölüm no ve bölüm adı aileye girmez
        self.assertNotEqual(liste.aile_imzasi(oto2), liste.aile_imzasi(eski2))
        sira = liste.cevrilecek_sirasi(liste.puanla([oto2, eski2], None), {}, {}, True, ["en"])
        self.assertEqual([a.kimlik for a in sira], ["eski2", "oto2"])
        self.durum.tercih_yaz("ad:ornekdizi", "", oto1.yukleyen, liste.aile_imzasi(oto1))
        tercih = self.durum.tercih("ad:ornekdizi")
        sira = liste.cevrilecek_sirasi(liste.puanla([eski2, oto2], None), {}, tercih, True, ["en"])
        self.assertEqual([a.kimlik for a in sira], ["oto2", "eski2"])
        # Son seçim kazanır: kullanıcı sonra ötekini seçerse onun devamı öne geçer
        self.durum.tercih_yaz("ad:ornekdizi", "", eski1.yukleyen, liste.aile_imzasi(eski1))
        sira = liste.cevrilecek_sirasi(liste.puanla([oto2, eski2], None), {}, self.durum.tercih("ad:ornekdizi"), False, ["en"])
        self.assertEqual([a.kimlik for a in sira], ["eski2", "oto2"])
        # Kaymış diye işaretlenen, ailenin devamı da olsa sona iner
        sira = liste.cevrilecek_sirasi(liste.puanla([oto2, eski2], None), {eski2.anahtar: 1.0},
                                       self.durum.tercih("ad:ornekdizi"), False, ["en"])
        self.assertEqual([a.kimlik for a in sira], ["oto2", "eski2"])

    def test_siradaki_bolumde_sureden_kare_hizi_tahmini_yapilmaz(self):
        from d2 import indir, siradaki
        v = _video(tur="dizi", dizi_adi="Ornek Dizi", sezon=1, bolum=1, imdb="tt0000001")
        v.sure, v.fps = 2902.0, 25.0
        n = siradaki._sonraki(v, 1, 2)
        self.assertEqual((n.sure, n.fps, n.bolum), (0, 25.0, 2))
        metin_ = "1\n00:48:40,000 --> 00:48:52,000\nSon satır.\n"               # izlenen bölümden 30 sn uzun
        self.assertIsNotNone(indir.kare_hizi_orani(None, v.fps, metin_, ".srt", v.sure))   # oynayan videoda tahmin sürer
        self.assertIsNone(indir.kare_hizi_orani(None, n.fps, metin_, ".srt", n.sure))
        self.assertIsNone(indir.kare_hizi_orani(25.0, n.fps, metin_, ".srt", n.sure))
        self.assertAlmostEqual(indir.kare_hizi_orani(23.976, n.fps, metin_, ".srt", n.sure), 23.976 / 25.0)   # sitenin bildirdiği

    def test_makine_yapimi_kaynak_listede_isaretlenir(self):
        oto, eski = self._adaylar(1)
        self.assertTrue(liste.makine_yapimi(oto) and not liste.makine_yapimi(eski))
        self.assertTrue(liste.makine_yapimi(Aday("subdl", "x", "en", ["Ornek S01E01"], yukleyen="SUBDL AI")))
        self.assertFalse(liste.makine_yapimi(Aday("os", "y", "en", ["Ornek.S01E01.AIR.Date"], yukleyen="Maria")))
        v = _video(tur="dizi", dizi_adi="Ornek Dizi", sezon=1, bolum=1, imdb="tt0000001")
        satirlar, _, _ = liste.olustur(v, [oto, eski], [], None, None, {}, {}, _ayar())
        yazilar = {s.anahtar: s.yazi for s in satirlar if s.eylem == "cevir"}
        self.assertIn("makine yapımı", yazilar[oto.anahtar])
        self.assertNotIn("makine yapımı", yazilar[eski.anahtar])

    def test_ayar_dugmeleri_pencereyi_kapatmaz_calisirken_isaret_verir(self):
        import ayarlar_uret
        import xml.etree.ElementTree as ET
        from d2 import eslesme, eylemler, kodi
        # Yalnız ayar yazan iki düğme pencereyi kapatır; yeniden açılan düğmenin sekmesi üreticideki sırayla aynıdır
        kok = ET.fromstring(ayarlar_uret.uret())
        kapatan = sorted(a.get("id") for a in kok.iter("setting") if a.find("control/close") is not None)
        self.assertEqual(kapatan, sorted(ayarlar_uret.KAPATAN_EYLEMLER))
        self.assertEqual(eylemler.YENIDEN_ACILAN, {"anahtar_yukle": ayarlar_uret.SEKME_SIRASI.index("anahtarlar")})
        self.assertEqual(ayarlar_uret.SEKME_SIRASI, ["diller", "anahtarlar", "liste", "otomatik", "ceviri", "bakim"])   # altısı kaydırmadan görünür
        acilan, xbmcgui.seritler[:], xbmcgui.bildirimler[:] = [], [], []
        with mock.patch.object(kodi, "ayarlari_ac", lambda sekme=None: acilan.append(sekme)), \
                mock.patch.object(eylemler, "_durum", lambda: self.durum), \
                mock.patch.object(eslesme, "guncelle", lambda *a, **k: True):
            eylemler.calistir(["x", "esleme_simdi"])
            self.assertEqual(acilan, [])                                    # pencere kapanmadı, yeniden açılmaz
            self.assertEqual([o for o, _, _ in xbmcgui.seritler], ["ac", "kapat"])
            self.assertIn("Çalışıyor", xbmcgui.bildirimler[0][1])            # basıldığı an bildirim
            with mock.patch.object(eylemler, "anahtar_yukle", lambda: None), \
                    mock.patch.dict(eylemler.EYLEMLER, {"anahtar_yukle": lambda: None}):
                del xbmc.komutlar[:]
                eylemler.calistir(["x", "anahtar_yukle"])
            self.assertEqual(acilan, [1])
            # Ayar yazan düğmeden sonra eklentinin bilgi sayfası kapatılır: o sayfa ayarların eski hâlini tutuyor,
            # oradan yeniden açılan ayar penceresi boş anahtarları gösterip üstüne yazdırıyordu.
            self.assertEqual(xbmc.komutlar, ["Dialog.Close(addoninformation,true)"])
            self.assertIn("fabrika", eylemler.AYAR_YAZANLAR)

    def test_disa_aktarma_klasoru_dugmeye_basinca_sorulur(self):
        from d2 import bakim, eylemler
        hedef = os.path.join(self.kok, "disari")
        os.makedirs(hedef)
        yazilan = []
        self.addCleanup(xbmcgui.cevaplar.update, {"browse": "", "select": 0})
        xbmcgui.cevaplar.update({"browse": hedef, "select": 0})
        with mock.patch.object(eylemler, "_durum", lambda: self.durum), \
                mock.patch.object(bakim, "disa_ceviriler", lambda d, klasor, *a: yazilan.append(klasor) or 3):
            eylemler.disa("ceviri")                                         # ilk kez: klasör seçtirilir
            self.assertEqual(self.durum.depo.oku("disa_klasor"), {"yol": hedef})
            xbmcgui.cevaplar.update({"browse": "", "select": 0})
            eylemler.disa("ceviri")                                         # sonra: "Son klasör" seçeneği
            xbmcgui.cevaplar.update({"select": -1})
            eylemler.disa("ceviri")                                         # vazgeçildi: yazılmaz
        self.assertEqual(yazilan, [hedef, hedef])


class AyarDosyasiTestleri(unittest.TestCase):
    """Kodi 21 ayarları ilk okurken bazı kalıplarda çöküyor (boş <option></option>); üretilen dosya bunlardan arınık olmalı."""

    def test_kodiyi_cokerten_kaliplar_yok(self):
        import xml.etree.ElementTree as ET
        import ayarlar_uret
        kok = ET.fromstring(ayarlar_uret.uret().encode("utf-8"))
        kimlikler = set()
        for ayar in kok.iter("setting"):
            kimlik = ayar.get("id")
            self.assertNotIn(kimlik, kimlikler)
            kimlikler.add(kimlik)
            self.assertIsNotNone(ayar.find("control"), kimlik)
            for secenek in ayar.iter("option"):
                self.assertTrue((secenek.text or "").strip(), f"{kimlik}: boş seçenek değeri")
                self.assertTrue(secenek.get("label", "").isdigit(), kimlik)
            varsayilan = ayar.find("default")
            secenekler = [s.text for s in ayar.iter("option")]
            if secenekler and varsayilan is not None:
                self.assertIn(varsayilan.text or "", secenekler, f"{kimlik}: varsayılan seçeneklerde yok")
        for bag in kok.iter("dependency"):
            self.assertIn(bag.get("setting"), kimlikler)
            self.assertTrue((bag.text or "").strip())

    def test_yazi_kimlikleri_benzersiz(self):
        kimlikler = [k for k, _, _ in metin.TABLO]
        adlar = [a for _, a, _ in metin.TABLO]
        self.assertEqual(len(kimlikler), len(set(kimlikler)))     # aynı kimlik Kodi'de yanlış yazıyı gösterir
        self.assertEqual(len(adlar), len(set(adlar)))

    @staticmethod
    def _kaynak_agaclari():
        """Eklentinin ve ayar üreticisinin kaynak dosyaları: (göreli ad, ast)."""
        import ast
        kok = os.path.join(KOK, "service.subtitles.defta")
        yollar = [os.path.join(k, a) for k, _, adlar in os.walk(kok) for a in adlar if a.endswith(".py")
                  and os.sep + "defta" + os.sep not in os.path.join(k, a)[len(kok):]]
        yollar.append(os.path.join(KOK, "araclar", "ayarlar_uret.py"))
        for yol in sorted(yollar):
            with open(yol, encoding="utf-8") as f:
                yield os.path.relpath(yol, KOK), ast.parse(f.read())

    def test_her_yazi_kullanilir_ve_yer_tutuculari_verilir(self):
        # Kod yazının adını kullanır (metin.X): olmayan ad ancak o satır çalışınca çöker, eksik yer tutucu da öyle
        # (bildirim yerine KeyError). Kullanılmayan yazı silinen bir özelliğin kalıntısıdır.
        import ast
        import string
        yazilar = {ad: yazi for _, ad, yazi in metin.TABLO}
        kullanilan = set()
        for dosya, agac in self._kaynak_agaclari():
            for d in ast.walk(agac):
                if isinstance(d, ast.Attribute) and isinstance(d.value, ast.Name) and d.value.id in ("metin", "M") \
                        and d.attr.isupper() and d.attr != "TABLO":
                    self.assertIn(d.attr, yazilar, f"{dosya}:{d.lineno}")
                    kullanilan.add(d.attr)
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "al" and d.args \
                        and isinstance(d.args[0], ast.Attribute) and d.args[0].attr in yazilar:
                    gereken = {a for _, a, _, _ in string.Formatter().parse(yazilar[d.args[0].attr]) if a}
                    self.assertEqual({k.arg for k in d.keywords}, gereken, f"{dosya}:{d.lineno} {d.args[0].attr}")
        self.assertEqual(sorted(set(yazilar) - kullanilan), [])

    def test_okunan_her_ayar_tanimli_tanimlanan_her_ayar_okunur(self):
        import ast
        import ayarlar_uret
        from d2 import eylemler
        tanimli = {t[1]: t[0] for _, _, gruplar in ayarlar_uret.SEKMELER for _, ayarlar in gruplar for t in ayarlar}
        okuyanlar = ("ayar", "ayar_bool", "ayar_int", "ayar_yaz", "ayar_oku")
        sabitler = set()
        for dosya, agac in self._kaynak_agaclari():
            if dosya.endswith("ayarlar_uret.py"):
                continue
            for d in ast.walk(agac):
                if isinstance(d, ast.Constant) and isinstance(d.value, str):
                    sabitler.add(d.value)
                ad = getattr(getattr(d, "func", None), "attr", getattr(getattr(d, "func", None), "id", ""))
                if isinstance(d, ast.Call) and ad in okuyanlar and d.args and isinstance(d.args[0], ast.Constant):
                    self.assertIn(d.args[0].value, tanimli, f"{dosya}:{d.lineno}")
        self.assertEqual(sorted(set(tanimli) - sabitler), [])         # hiçbir yerde okunmayan ayar
        # Ayar penceresindeki her düğmenin eylemi var; düğmesi olmayan eylem yok
        self.assertEqual(sorted(a for a, cesit in tanimli.items() if cesit == "eylem"), sorted(eylemler.EYLEMLER))

    def test_her_dil_ve_bicim_secenegi_cekirdekte_gecerli(self):
        # Ayar penceresindeki her seçenek çekirdeğe olduğu gibi gider: çekirdeğin tanımadığı bir değer çeviriyi
        # AyarHatasi ile başlamadan düşürür. Latin dışı yazılan hedef dillerde çıktı kendi alfabesiyle kalır.
        import ayarlar_uret
        secenekler = {t[1]: [v for v, _ in t[4]] for _, _, gruplar in ayarlar_uret.SEKMELER for _, ayarlar in gruplar
                      for t in ayarlar if t[0] == "secim"}
        for dil in secenekler["hedef_dil"]:
            alfabe = ceviri_isi.cekirdek_ayari({"hedef_dil": dil})["ceviri"]["cikti_alfabesi"]
            self.assertEqual(alfabe, "serbest" if dil in ("ja", "ru", "uk", "el", "ar", "fa", "ko", "zh") else "latin", dil)
        bicimler = [ad for ad in secenekler if ad.startswith("b_")]
        self.assertEqual(len(bicimler), 11)
        for ad in bicimler:
            for deger in secenekler[ad]:
                ceviri_isi.cekirdek_ayari({"hedef_dil": "tr", ad: deger})
        self.assertFalse(ceviri_isi.cekirdek_ayari({"hedef_dil": "tr", "b_sen_siz": False})["hitap"]["acik"])

    def test_servis_cekirdegin_acicisini_degistirir(self):
        # Servis çekirdeğin OpenAI isteklerini soketi kayda geçen açıcıyla gönderir (Kodi kapanırken kesilsin diye);
        # bunun için çekirdeğin model._ACICI ve model._YonlendirmeYok adlarına dayanır. Yönlendirme yine izlenmez.
        import urllib.request
        from d2 import servis
        from defta import model
        eski = model._ACICI
        try:
            servis._cekirdek_acicisini_kur()
            turler = [type(i) for i in model._ACICI.handlers]
            self.assertIn(ag.KayitliHTTPSIsleyici, turler)
            self.assertIn(model._YonlendirmeYok, turler)
            self.assertNotIn(urllib.request.HTTPRedirectHandler, turler)
        finally:
            model._ACICI = eski

    def test_uretilen_dosyalar_ureticiyle_ayni(self):
        # settings.xml ve strings.po elle düzenlenmez; üretici değişip dosya yeniden üretilmediyse Kodi eskisini okur
        import ayarlar_uret
        kaynak = os.path.join(KOK, "service.subtitles.defta", "resources")
        for yol, beklenen in ((os.path.join(kaynak, "settings.xml"), ayarlar_uret.uret()),
                              (os.path.join(kaynak, "language", "resource.language.en_gb", "strings.po"),
                               metin.po_metni())):
            with open(yol, encoding="utf-8", newline="") as f:
                self.assertEqual(f.read(), beklenen, os.path.basename(yol))

    def test_pakete_gizli_dosya_girmez_cekirdek_paket_klasorunden_alinir(self):
        # Çekirdek olarak deponun kökü verilirse ("../defta") kök değil içindeki paket kopyalanır: kökte kullanıcının
        # anahtar dosyası, .git ve loglar durur. Eklenti klasöründeki noktayla başlayan dosyalar da zip'e girmez.
        import paketle
        d = tempfile.mkdtemp()
        try:
            depo, eklenti = os.path.join(d, "defta"), os.path.join(d, "eklenti")
            for klasor in (os.path.join(depo, "defta"), os.path.join(eklenti, "lib", "__pycache__"),
                           os.path.join(eklenti, ".git")):
                os.makedirs(klasor)
            for ad in (os.path.join(depo, "defta", "__init__.py"), os.path.join(depo, ".gizli"),
                       os.path.join(eklenti, "addon.xml"), os.path.join(eklenti, ".gizli"),
                       os.path.join(eklenti, ".git", "config"), os.path.join(eklenti, "lib", "a.py"),
                       os.path.join(eklenti, "lib", "a.pyc"), os.path.join(eklenti, "lib", "__pycache__", "a.pyc")):
                open(ad, "w").close()
            self.assertEqual(paketle.cekirdek_klasoru(depo), os.path.join(depo, "defta"))
            self.assertEqual(paketle.cekirdek_klasoru(os.path.join(depo, "defta")), os.path.join(depo, "defta"))
            with self.assertRaises(SystemExit):
                paketle.cekirdek_klasoru(eklenti)                  # çekirdek paketi değil
            zip_yolu = paketle.zip_yaz(os.path.join(d, "p.zip"), eklenti)
            with zipfile.ZipFile(zip_yolu) as z:
                self.assertEqual(sorted(z.namelist()), ["service.subtitles.defta/addon.xml",
                                                        "service.subtitles.defta/lib/a.py"])
        finally:
            shutil.rmtree(d)
        with open(os.path.join(KOK, ".gitignore"), encoding="utf-8") as f:
            self.assertIn(".env", f.read().split())                 # anahtar dosyası depoya da girmez

    def test_bos_secenek_uretilemez(self):
        import ayarlar_uret
        with self.assertRaises(ValueError):
            ayarlar_uret._ayar(ayarlar_uret.secim("x", 30100, "", [("", 30230)]))

    def test_yok_secimi_kaynak_sayilmaz(self):
        import xbmcaddon
        import ayarlar_uret
        from d2 import giris
        xbmcaddon.ayarlar.update(ayarlar_uret.varsayilanlar())
        xbmcaddon.ayarlar.update({"hedef_dil": "tr", "kaynak_1": "en", "kaynak_2": "yok", "kaynak_3": "yok"})
        self.assertEqual(giris.ayarlar()["kaynak_diller"], ["en"])


class AnahtarKaynagiTestleri(unittest.TestCase):
    def test_secici_ag_konumlarini_da_gosterir(self):
        # Kodi "files" kaynağında ağ konumlarını (SMB, NFS) göstermiyor; kaynak adı boş verilince yerel sürücülerle
        # birlikte gösteriyor. Dosya ve klasör seçicinin ikisi de aynı kökten açılır.
        from d2 import eylemler, kodi
        for secim, beklenen in ((0, (1, "", ".env|.txt")), (1, (0, "", ""))):
            del xbmcgui.gozatilan[:]
            with mock.patch.dict(xbmcgui.cevaplar, {"select": secim, "browse": ""}):
                eylemler.anahtar_yukle()
            self.assertEqual(xbmcgui.gozatilan, [beklenen], secim)
        del xbmcgui.gozatilan[:]
        kodi.klasor_sec("x", yazilabilir=True)
        self.assertEqual(xbmcgui.gozatilan, [(3, "", "")])

    def test_ag_adresi_ayrilir_ve_kimlik_eklenir(self):
        self.assertEqual(anahtar.ag_adresi("\\\\192.168.1.1\\usb\\anahtar.txt"), ("smb://192.168.1.1/usb/anahtar.txt", "", ""))
        self.assertEqual(anahtar.ag_adresi(" 192.168.1.1/usb "), ("smb://192.168.1.1/usb", "", ""))
        self.assertEqual(anahtar.ag_adresi("SMB://ali:p%40s@nas/ortak/"), ("smb://nas/ortak/", "ali", "p@s"))
        self.assertIsNone(anahtar.ag_adresi("smb://"))
        self.assertEqual(anahtar.ag_adresi_kimlikli("smb://nas/ortak/", "", ""), "smb://nas/ortak/")
        self.assertEqual(anahtar.ag_adresi_kimlikli("smb://nas/ortak/", "ev;ali", "p@s:/x"),
                         "smb://ev%3Bali:p%40s%3A%2Fx@nas/ortak/")

    def test_adres_yazinca_parolali_adres_saklanmaz(self):
        from d2 import eylemler
        with tempfile.TemporaryDirectory() as d:
            durum = kayit.Durum(d)
            okunan = []
            xbmc.klavye_cevaplari[:] = ["192.168.1.1/usb", "admin", "gizliparola1"]
            del xbmc.klavye_sorulari[:]
            with mock.patch.dict(xbmcgui.cevaplar, {"select": 2}), mock.patch.object(eylemler, "_durum", lambda: durum), \
                    mock.patch.object(eylemler, "_anahtar_dosyasi_oku", lambda yol: okunan.append(yol)):
                eylemler.anahtar_yukle()
            self.assertEqual(okunan, ["smb://admin:gizliparola1@192.168.1.1/usb"])
            self.assertEqual(durum.depo.oku("anahtar_adresi"), {"adres": "smb://192.168.1.1/usb"})   # parolasız
            self.assertEqual([g for _, _, g in xbmc.klavye_sorulari], [False, False, True])           # parola gizli yazılır
            self.assertNotIn("gizliparola1", gizli.gizle("smb://admin:gizliparola1@192.168.1.1/usb"))
            xbmc.klavye_cevaplari[:] = [None]                                                        # vazgeçildi
            del okunan[:]
            with mock.patch.dict(xbmcgui.cevaplar, {"select": 2}), mock.patch.object(eylemler, "_durum", lambda: durum), \
                    mock.patch.object(eylemler, "_anahtar_dosyasi_oku", lambda yol: okunan.append(yol)):
                eylemler.anahtar_yukle()
            self.assertEqual(okunan, [])


class Karar102Testleri(unittest.TestCase):
    """1.0.2: Shield denemesinden (17.11.2026) çıkanlar."""

    def setUp(self):
        self.kok = tempfile.mkdtemp()
        self.durum = kayit.Durum(self.kok)

    def tearDown(self):
        shutil.rmtree(self.kok, ignore_errors=True)

    def test_kodi_kaydi_suzulur_adres_ve_baska_eklenti_satirlari_cikmaz(self):
        from d2 import bakim
        ornek = ("2026-11-17 03:12:01.578 T:1 info <general>: Starting Kodi (21.3)\n"
                 "2026-11-17 03:12:02.000 T:1 info <general>: loading skin\n"
                 "2026-11-17 03:15:19.132 T:2 info <general>: [defta] oynatma: tür=dizi\n"
                 "2026-11-17 03:15:20.000 T:3 info <general>: ###scraper###: Playing https://ornek.test/dl/abc?token=GIZLI\n"
                 "2026-11-17 03:15:22.929 T:2 error <general>: EXCEPTION Thrown (PythonToCppException)\n"
                 "Traceback (most recent call last):\n"
                 '  File "x.py", line 1, in <module>\n'
                 '    ac("smb://kullanici:parola@sunucu/x")\n'
                 "RuntimeError: olmadı\n"
                 "2026-11-17 03:15:23.000 T:1 info <general>: başka satır\n")
        cikti = bakim.kodi_kaydini_suz(ornek)
        for yok in ("GIZLI", "parola", "loading skin", "başka satır", "scraper"):
            self.assertNotIn(yok, cikti)
        for var in ("Starting Kodi", "[defta] oynatma", "EXCEPTION", 'File "x.py"', "[adres]", "RuntimeError: olmadı"):
            self.assertIn(var, cikti)

    def test_hata_dosyalarina_son_arama_ve_kodi_kaydi_eklenir(self):
        from d2 import bakim
        kayit.yaz(self.durum.yol("gecici", "son_arama.json"), {"video": {}, "adaylar": {}})
        hedef = os.path.join(self.kok, "disari")
        kopyalanan = []

        def kopyala(kaynak, hedef_yol):
            kopyalanan.append(hedef_yol.replace("\\", "/").rsplit("/", 1)[-1])
            return True
        sayi = bakim.disa_hata(self.durum, hedef, kopyala, lambda yol: None, {"kodi-defta.txt": "[defta] satır\n"})
        self.assertEqual(sorted(kopyalanan), ["kodi-defta.txt", "son_arama.json"])
        self.assertEqual(sayi, 2)
        self.assertFalse(os.path.exists(self.durum.yol("gecici", "kodi-defta.txt")))      # geçici kopya kalmaz

    def test_adi_bilinmeyen_uzun_akista_da_karma_hesaplanir(self):
        v = video_modulu.Video()
        v.yol, v.akis, v.canli, v.dosya_adi = "https://ornek.test/dl/5166-ee23", True, False, ""
        with mock.patch.object(video_modulu, "karma_hesapla", lambda ac: "0123456789abcdef"):
            v.sure = 1452.0
            self.assertEqual(video_modulu._karma(xbmcvfs, v), "0123456789abcdef")
            v.sure = 120.0                                               # kısa ve adsız: fragman olabilir
            self.assertIsNone(video_modulu._karma(xbmcvfs, v))
            v.sure, v.yol = 1452.0, "https://ornek.test/dl/liste.m3u8"   # oynatma listesi: başı ve sonu okunamaz
            self.assertIsNone(video_modulu._karma(xbmcvfs, v))

    def test_surum_adi_sunucunun_basligindan(self):
        """Adres de oynatan eklenti de ad vermiyorsa (Red Light'ta paket kaynağı) sunucunun başlığındaki dosya adı."""
        istekler = []

        class Yanit:
            def __init__(self, baslik):
                self.headers = {"Content-Disposition": baslik} if baslik else {}

            def close(self):
                pass

        def ac(baslik):
            def _ac(istek, timeout=None):
                istekler.append(istek)
                return Yanit(baslik)
            return _ac
        yol = "https://store-1.example/dld/AbC123?token=gizli|User-Agent=Oynatici%2F1&verifypeer=false"
        tani = []
        ad = video_modulu.sunucunun_dosya_adi(yol, tani, ac('attachment; filename="[Grup] Ornek Dizi S2 - 12 (BD 1080p).mkv"'))
        self.assertEqual((ad, tani), ("[Grup] Ornek Dizi S2 - 12 (BD 1080p).mkv", []))
        basliklar = {k.lower(): d for k, d in istekler[0].header_items()}
        self.assertEqual((basliklar["range"], basliklar["user-agent"]), ("bytes=0-0", "Oynatici/1"))
        self.assertNotIn("verifypeer", basliklar)
        self.assertEqual(istekler[0].full_url, "https://store-1.example/dld/AbC123?token=gizli")
        # Ad yoksa, jeton gibiyse ya da istek düşerse boş döner; tanıda adres ve hata metni olmaz
        for baslik, beklenen in (("", "başlıkta ad yok"), ('attachment; filename="9f8a7c6b5d4e3f2a1b0c9d8e7f6a5b4c.mkv"', "jeton gibi")):
            tani = []
            self.assertEqual(video_modulu.sunucunun_dosya_adi(yol, tani, ac(baslik)), "")
            self.assertIn(beklenen, tani[0])

        def dusen(istek, timeout=None):
            raise OSError("https://store-1.example/dld/AbC123?token=gizli açılamadı")
        tani = []
        self.assertEqual(video_modulu.sunucunun_dosya_adi(yol, tani, dusen), "")
        self.assertEqual(tani, ["sunucunun verdiği ad: alınamadı (OSError)"])
        self.assertEqual(video_modulu.sunucunun_dosya_adi("https://ornek.test/a/liste.m3u8", [], dusen), "")

    def test_sunucu_adi_bir_kez_sorulur_ve_oynatilan_onu_kullanir(self):
        bellek, sorulan = {}, []

        def ozellik(ad, deger=None):
            if deger is None:
                return bellek.get(ad, "")
            bellek[ad] = deger
            return deger

        def sahte(yol, tani=None, ac=None):
            sorulan.append(1)
            return "Ornek.Film.2014.1080p.BluRay.x264-GRP.mkv"
        xbmc.oynatici.update({"oynuyor": True, "sure": 7200.0, "yol": "https://store-1.example/dld/AbC123?token=gizli",
                              "etiket": {"title": "Ornek Film", "year": 2014, "mediatype": "movie"}})
        try:
            with mock.patch.object(video_modulu, "sunucunun_dosya_adi", sahte):
                bul = video_modulu.sunucu_adi_bul(ozellik)
                for _ in range(2):
                    v = video_modulu.oynatilan(xbmc, xbmcvfs, karma=False, ad_bul=bul)
                    self.assertEqual((v.dosya_adi, v.surum.grup), ("Ornek.Film.2014.1080p.BluRay.x264-GRP.mkv", "GRP"))
                self.assertEqual(len(sorulan), 1)
                self.assertFalse(any("store-1" in k or "gizli" in k + d for k, d in bellek.items()))
                xbmc.oynatici["sure"] = 120.0                           # kısa video (fragman): sunucuya sorulmaz
                xbmc.oynatici["yol"] = "https://store-1.example/dld/Zz9?token=gizli"
                self.assertEqual(video_modulu.oynatilan(xbmc, xbmcvfs, karma=False, ad_bul=bul).dosya_adi, "")
                self.assertEqual(len(sorulan), 1)
        finally:
            xbmc.oynatici.update({"oynuyor": False, "yol": "", "etiket": {}})

    def test_hizli_ceviri_ayari_bos_ya_da_true_ise_acik(self):
        from d2 import kodi
        for deger, beklenen in (("", True), ("true", True), ("false", False)):
            with mock.patch.object(kodi, "ayar", lambda ad, d=deger: d):
                self.assertEqual(kodi.hizli_acik(), beklenen)

    def test_opensubtitles_testi_bir_kez_yeniden_dener(self):
        from d2.siteler import opensubtitles
        site = opensubtitles.OpenSubtitles("a" * 32, "", "", None)
        cevaplar = [{"calisiyor": False, "neden": "hiz"}, {"calisiyor": True, "kalan": 5}]
        with mock.patch.object(site, "_test", lambda: cevaplar.pop(0)), mock.patch.object(opensubtitles.time, "sleep", lambda s: None):
            self.assertEqual(site.test(), {"calisiyor": True, "kalan": 5})
        self.assertEqual(cevaplar, [])

    def test_kaymis_ai_seciliyken_siradaki_kaynagi_cevirir_insanlar_kendi_arasinda_doner(self):
        from d2 import giris
        v = video_modulu.Video()
        v.tur, v.baslik, v.yil, v.imdb = "film", "Ornek", 2020, "tt0000001"
        va = v.video_anahtari()
        son = {"sira": [["ai", "ai:k1"], ["insan", "subdl:1"], ["insan", "subdl:2"]],
               "cevrilecek": ["os:9", "os:10"], "adaylar": {"os:9": {}, "os:10": {}, "subdl:1": {}, "subdl:2": {}}}
        cevrilen, yuklenen = [], []
        with mock.patch.object(giris, "cevir", lambda d, vv, s, a: cevrilen.append(a) or "/kaynak.srt"), \
                mock.patch.object(giris, "_insan_hazirla", lambda d, vv, s, a: None), \
                mock.patch.object(giris, "_hazir_hazirla", lambda d, vv, a: None), \
                mock.patch.object(giris, "_bildir", lambda *a, **k: None):
            # AI seçili: hazır başka çeviri yok -> listedeki ilk kaynak çevrilir; insan altyazısına geçilmez
            yol = giris.kaymis(self.durum, v, son, {"tur": "ai", "anahtar": "ai:k1", "kaynak": {"site": "os", "kimlik": "8"}})
            self.assertEqual((yol, cevrilen), ("/kaynak.srt", ["os:9"]))
            self.assertIn("os:8", self.durum.kaymislar(va))                      # kaymış çevirinin kaynağı da işaretli
            # sıradaki de kaymışsa (işaretli) ondan sonraki çevrilir
            self.durum.kaymis_isaretle(va, "os:9")
            giris.kaymis(self.durum, v, son, {"tur": "ai", "anahtar": "ai:k2", "kaynak": {"site": "os", "kimlik": "9"}})
            self.assertEqual(cevrilen, ["os:9", "os:10"])
        del cevrilen[:]
        dosya = os.path.join(self.kok, "insan2.srt")
        with open(dosya, "w", encoding="utf-8") as f:
            f.write("1\n00:00:01,000 --> 00:00:02,000\nMerhaba.\n")
        with mock.patch.object(giris, "cevir", lambda d, vv, s, a: cevrilen.append(a) or "/kaynak.srt"), \
                mock.patch.object(giris, "_insan_hazirla", lambda d, vv, s, a: {"yol": dosya, "secim": {"anahtar": a}} if a == "subdl:2" else None), \
                mock.patch.object(giris, "_hazir_hazirla", lambda d, vv, a: yuklenen.append(a)), \
                mock.patch.object(giris, "_uygula", lambda d, vv, h: h["yol"]), \
                mock.patch.object(giris, "_bildir", lambda *a, **k: None):
            # İnsan altyazısı seçili: öteki insan altyazısına geçilir; çeviri başlamaz, AI çevirisine bakılmaz
            yol = giris.kaymis(self.durum, v, son, {"tur": "insan", "anahtar": "subdl:1"})
            self.assertEqual((yol, cevrilen, yuklenen), (dosya, [], []))


if __name__ == "__main__":
    unittest.main()
