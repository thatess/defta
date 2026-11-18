"""settings.xml üretici: ayarların tek tanımı burada (sıra, tür, varsayılan, seçenekler, düzey).
Kodi'nin yeni ayar biçimi (version 1) yazılır; yazılar metin.py'deki kimliklerle gelir."""
import os
import sys
from xml.sax.saxutils import escape

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(KOK, "service.subtitles.defta", "resources", "lib"))
from d2 import metin as M  # noqa: E402

EYLEM = "RunScript(special://home/addons/service.subtitles.defta/ayar_eylemi.py,{})"
DILLER = [("tr", M.DIL_TR), ("en", M.DIL_EN), ("ja", M.DIL_JA), ("de", M.DIL_DE), ("fr", M.DIL_FR), ("es", M.DIL_ES),
          ("it", M.DIL_IT), ("pt", M.DIL_PT), ("pt-BR", M.DIL_PT_BR), ("ru", M.DIL_RU), ("ar", M.DIL_AR),
          ("ko", M.DIL_KO), ("zh", M.DIL_ZH), ("nl", M.DIL_NL), ("pl", M.DIL_PL), ("el", M.DIL_EL), ("fa", M.DIL_FA),
          ("az", M.DIL_AZ), ("uk", M.DIL_UK), ("sv", M.DIL_SV)]
SAYILAR = [(1, M.SAYI_1), (3, M.SAYI_3), (5, M.SAYI_5), (10, M.SAYI_10)]
# "Yok" seçeneğinin değeri boş olamaz: Kodi 21 boş <option></option> görünce çöküyor (ayarlar ilk okunduğunda).
YOK = "yok"
UC_MOD = [("orijinal", M.SECIM_ORIJINAL), ("orijinal_ceviri", M.SECIM_ORIJINAL_CEVIRI), ("ceviri", M.SECIM_CEVIRI)]


def secim(ad, etiket, varsayilan, secenekler, tur="string", duzey=0, bagli=None):
    return ("secim", ad, etiket, varsayilan, secenekler, tur, duzey, bagli)


def anahtar(ad, etiket, varsayilan, duzey=0, bagli=None):
    return ("bool", ad, etiket, varsayilan, None, "boolean", duzey, bagli)


def yazi(ad, etiket, gizli=False, duzey=0, bagli=None):
    return ("yazi", ad, etiket, gizli, None, "string", duzey, bagli)


def eylem(ad, etiket, duzey=0):
    return ("eylem", ad, etiket, None, None, "action", duzey, None)


def klasor(ad, etiket, duzey=0):
    return ("klasor", ad, etiket, None, None, "path", duzey, None)


# Ayar yazan düğmeler: pencere kapanır (açık pencere, düğmenin yazdığı ayarı kendi eski değeriyle ezerdi) ve iş
# bitince aynı sekmede yeniden açılır. Öteki düğmeler pencereyi kapatmaz: sonuç pencerenin üstünde görünür.
KAPATAN_EYLEMLER = ("anahtar_yukle", "fabrika")

# Sekmeler kullanıcının sorusuna göre: hangi dil, hangi hesap, listede ne görünsün, kendiliğinden ne olsun,
# çeviri nasıl yapılsın (ve altyazıda ne kalsın), bakım. Altı sekme: Kodi'nin ayar penceresinde sekme sütununa altı
# düğme sığar; yedincisi (Bakım) kaydırmadan görünmüyordu.
SEKMELER = [
    ("diller", M.SEKME_DILLER, [
        (M.GRUP_DIL, [secim("hedef_dil", M.AYAR_HEDEF_DIL, "tr", DILLER)]),
        (M.GRUP_KAYNAK_DIL, [
            secim("kaynak_1", M.AYAR_KAYNAK_1, "en", DILLER),
            secim("kaynak_2", M.AYAR_KAYNAK_2, "ja", [(YOK, M.SECIM_YOK)] + DILLER),
            secim("kaynak_3", M.AYAR_KAYNAK_3, YOK, [(YOK, M.SECIM_YOK)] + DILLER, duzey=2),
        ]),
    ]),
    # Bütün anahtarlar tek yerde: çeviri (OpenAI) ve altyazı siteleri
    ("anahtarlar", M.SEKME_ANAHTARLAR, [
        # En sık kullanılan iki düğme en üstte: önce test, altında dosyadan yükleme
        (M.GRUP_ANAHTARLAR, [eylem("baglanti_testi", M.AYAR_BAGLANTI_TESTI),
                             eylem("anahtar_yukle", M.AYAR_ANAHTAR_YUKLE)]),
        (M.GRUP_OPENAI, [yazi("openai_anahtar", M.AYAR_OPENAI_ANAHTAR, gizli=True)]),
        (M.GRUP_OPENSUBTITLES, [
            anahtar("os_kullan", M.AYAR_KULLAN, True),
            yazi("os_anahtar", M.AYAR_API_ANAHTARI, gizli=True, bagli="os_kullan"),
            yazi("os_kullanici", M.AYAR_KULLANICI, bagli="os_kullan"),
            yazi("os_parola", M.AYAR_PAROLA, gizli=True, bagli="os_kullan"),
        ]),
        (M.GRUP_SUBDL, [anahtar("subdl_kullan", M.AYAR_KULLAN, True),
                        yazi("subdl_anahtar", M.AYAR_API_ANAHTARI, gizli=True, bagli="subdl_kullan")]),
        (M.GRUP_SUBSOURCE, [anahtar("subsource_kullan", M.AYAR_KULLAN, True),
                            yazi("subsource_anahtar", M.AYAR_API_ANAHTARI, gizli=True, bagli="subsource_kullan")]),
        (M.GRUP_JIMAKU, [anahtar("jimaku_kullan", M.AYAR_KULLAN, True),
                         yazi("jimaku_anahtar", M.AYAR_API_ANAHTARI, gizli=True, bagli="jimaku_kullan")]),
    ]),
    ("liste", M.SEKME_LISTE, [
        (M.GRUP_SONUC, [
            secim("sonuc_hedef", M.AYAR_SONUC_HEDEF, 3, SAYILAR, tur="integer"),
            secim("sonuc_cevrilecek", M.AYAR_SONUC_CEVRILECEK, 3, SAYILAR, tur="integer"),
        ]),
        (M.GRUP_LISTE, [
            anahtar("jimaku_oncelik", M.AYAR_JIMAKU_ONCELIK, True),
            anahtar("sdh_alta", M.AYAR_SDH_ALTA, True, duzey=2),
        ]),
        (M.GRUP_ESLEME, [secim("esleme_siklik", M.AYAR_ESLEME_SIKLIK, 7, [(1, M.SECIM_GUNLUK), (7, M.SECIM_HAFTALIK),
                                                                          (30, M.SECIM_AYLIK)], tur="integer"),
                         eylem("esleme_simdi", M.AYAR_ESLEME_SIMDI)]),
        (M.GRUP_DISA, [eylem("disa_ceviri", M.AYAR_DISA_CEVIRI), eylem("disa_hata", M.AYAR_DISA_HATA)]),
    ]),
    ("otomatik", M.SEKME_OTOMATIK, [
        (M.GRUP_OTOMATIK, [
            anahtar("oto_hedef", M.AYAR_OTOMATIK_HEDEF, False),
            secim("oto_ai", M.AYAR_OTOMATIK_AI, 0, [(0, M.SECIM_KAPALI), (1, M.SECIM_YALNIZ_HAZIR),
                                                     (2, M.SECIM_HAZIR_YOKSA_CEVIR)], tur="integer"),
        ]),
        (M.GRUP_SIRADAKI, [
            secim("siradaki", M.AYAR_SIRADAKI, 1, [(0, M.SECIM_KAPALI), (1, M.SECIM_SOR), (2, M.SECIM_OTOMATIK)],
                  tur="integer"),
            secim("siradaki_yer", M.AYAR_SIRADAKI_YER, 90, [(80, M.YUZDE_80), (85, M.YUZDE_85), (90, M.YUZDE_90),
                                                             (95, M.YUZDE_95)], tur="integer", duzey=2),
            secim("siradaki_sure", M.AYAR_SIRADAKI_SURE, 15, [(10, M.SANIYE_10), (15, M.SANIYE_15),
                                                               (20, M.SANIYE_20), (30, M.SANIYE_30)],
                  tur="integer", duzey=2),
        ]),
    ]),
    ("ceviri", M.SEKME_CEVIRI, [
        (M.GRUP_HIZLI, [
            anahtar("hizli_cevir", M.AYAR_HIZLI_CEVIR, True),
            secim("hizli_sure", M.AYAR_HIZLI_SURE, 2, [(1, M.DAKIKA_1), (2, M.DAKIKA_2), (3, M.DAKIKA_3),
                                                        (5, M.DAKIKA_5), (10, M.DAKIKA_10)], tur="integer"),
        ]),
        (M.GRUP_TERCIH, [
            anahtar("b_sen_siz", M.AYAR_SEN_SIZ, True),
            secim("b_hitap_ekleri", M.AYAR_HITAP_EKLERI, "orijinal", UC_MOD),
            secim("b_kurgu", M.AYAR_KURGU_DILI, "orijinal", UC_MOD),
            secim("b_sarki", M.AYAR_SARKI, "orijinal", UC_MOD),
            secim("b_ses_sozcukleri", M.AYAR_SES_SOZCUKLERI, "orijinal", [("orijinal", M.SECIM_ORIJINAL),
                                                                          ("cevir", M.SECIM_CEVRILSIN)]),
            secim("b_ekran", M.AYAR_EKRAN_YAZISI, "cevir", [("cevir", M.SECIM_CEVRILSIN), ("birak", M.SECIM_BIRAKILSIN),
                                                             ("sil", M.SECIM_SILINSIN)]),
        ]),
        # Altyazıdan ne silinsin, ne kalsın
        (M.GRUP_NOTLAR, [
            secim("b_ses_etiketi", M.AYAR_SES_ETIKETI, "sil", [("sil", M.SECIM_SILINSIN), ("cevir", M.SECIM_CEVRILSIN)]),
            secim("b_konusan", M.AYAR_KONUSAN, "sil", [("sil", M.SECIM_SILINSIN), ("kalsin", M.SECIM_KALSIN)]),
            secim("b_reklam", M.AYAR_REKLAM, "sil", [("sil", M.SECIM_SILINSIN), ("kalsin", M.SECIM_KALSIN)], duzey=2),
        ]),
        (M.GRUP_YAZIM, [
            secim("b_tire", M.AYAR_TIRE, "korunsun", [("korunsun", M.SECIM_KORUNSUN), ("sil", M.SECIM_SILINSIN)]),
            secim("b_italik", M.AYAR_ITALIK, "korunsun", [("korunsun", M.SECIM_KORUNSUN), ("sil", M.SECIM_SILINSIN)]),
            secim("b_ustte", M.AYAR_USTTE, "sil", [("sil", M.SECIM_SILINSIN), ("korunsun", M.SECIM_KORUNSUN)], duzey=2),
        ]),
    ]),
    ("bakim", M.SEKME_BAKIM, [
        (M.GRUP_SOZLUK, [eylem("sozluk_duzelt", M.AYAR_SOZLUK_DUZELT)]),
        (M.GRUP_TEMIZLIK, [eylem("tumunu_temizle", M.AYAR_TUMUNU_TEMIZLE)]),
        (M.GRUP_AYRI_SIL, [eylem("sil_ceviri", M.AYAR_SIL_CEVIRI), eylem("sil_sozluk", M.AYAR_SIL_SOZLUK),
                           eylem("sil_yarim", M.AYAR_SIL_YARIM), eylem("sil_indirilen", M.AYAR_SIL_INDIRILEN),
                           eylem("sil_kayit", M.AYAR_SIL_KAYIT), eylem("sil_gecici", M.AYAR_SIL_GECICI),
                           eylem("sil_isaret", M.AYAR_SIL_ISARET), eylem("sil_esleme", M.AYAR_SIL_ESLEME)]),
        (M.GRUP_SIFIRLA, [eylem("fabrika", M.AYAR_FABRIKA)]),
    ]),
]
SEKME_SIRASI = [sekme_id for sekme_id, _, _ in SEKMELER]


def _ayar(t):
    cesit, ad, etiket, deger, secenekler, tur, duzey, bagli = t
    s = [f'        <setting id="{ad}" type="{tur}" label="{etiket}" help="">', f"          <level>{duzey}</level>"]
    if cesit == "secim":
        bos = [e for v, e in secenekler if str(v) == ""]
        if bos:
            raise ValueError(f"{ad}: boş seçenek değeri Kodi'yi çökertir")
        s.append(f"          <default>{escape(str(deger))}</default>")
        s.append("          <constraints>")
        if tur == "string":
            s.append("            <allowempty>true</allowempty>")
        s.append("            <options>")
        s += [f'              <option label="{e}">{escape(str(v))}</option>' for v, e in secenekler]
        s += ["            </options>", "          </constraints>"]
        s.append(f'          <control type="spinner" format="{"string" if tur == "string" else "integer"}"/>')
    elif cesit == "bool":
        s += [f"          <default>{'true' if deger else 'false'}</default>", '          <control type="toggle"/>']
    elif cesit == "yazi":
        s += ["          <default/>", "          <constraints><allowempty>true</allowempty></constraints>",
              '          <control type="edit" format="string">', f"            <heading>{etiket}</heading>"]
        if deger:
            s.append("            <hidden>true</hidden>")
        s.append("          </control>")
    elif cesit == "eylem":
        s += [f"          <data>{escape(EYLEM.format(ad))}</data>",
              "          <constraints><allowempty>true</allowempty></constraints>",
              '          <control type="button" format="action">'
              + ("<close>true</close>" if ad in KAPATAN_EYLEMLER else "") + "</control>"]
    elif cesit == "klasor":
        s += ["          <default/>", "          <constraints><allowempty>true</allowempty><writable>true</writable></constraints>",
              '          <control type="button" format="path">', f"            <heading>{etiket}</heading>",
              "          </control>"]
    if bagli:
        s += ["          <dependencies>",
              f'            <dependency type="visible" setting="{bagli}">true</dependency>', "          </dependencies>"]
    s.append("        </setting>")
    return s


def uret():
    s = ['<?xml version="1.0" encoding="utf-8" standalone="yes"?>',
         "<!-- araclar/ayarlar_uret.py ile üretildi; burada düzenlemeyin -->",
         '<settings version="1">', '  <section id="service.subtitles.defta">']
    for sekme_id, etiket, gruplar in SEKMELER:
        s.append(f'    <category id="{sekme_id}" label="{etiket}" help="">')
        for n, (grup_etiket, ayarlar) in enumerate(gruplar, 1):
            s.append(f'      <group id="{n}" label="{grup_etiket}">')
            for t in ayarlar:
                s += _ayar(t)
            s.append("      </group>")
        s.append("    </category>")
    s += ["  </section>", "</settings>", ""]
    return "\n".join(s)


def varsayilanlar():
    """Ayar adı -> varsayılan değer (testlerin sahte Kodi'si ve fabrika ayarları için)."""
    v = {}
    for _, _, gruplar in SEKMELER:
        for _, ayarlar in gruplar:
            for cesit, ad, _, deger, _, _, _, _ in ayarlar:
                if cesit == "secim" or cesit == "bool":
                    v[ad] = deger
                elif cesit in ("yazi", "klasor"):
                    v[ad] = ""
    return v


if __name__ == "__main__":
    hedef = os.path.join(KOK, "service.subtitles.defta", "resources", "settings.xml")
    with open(hedef, "w", encoding="utf-8", newline="\n") as f:
        f.write(uret())
    print("yazıldı:", hedef)
