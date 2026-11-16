"""Loglar: her çalıştırma ayrı tarihli klasöre yazılır. API anahtarı hiçbir yere yazılmaz."""
import json
import os
import threading
import time

from .model import Model


class Gunluk:
    def __init__(self, kok):
        self.klasor = os.path.join(kok, time.strftime("%Y-%m-%d_%H-%M-%S"))
        os.makedirs(self.klasor, exist_ok=True)
        self.hata_sayisi = 0
        self._kilit = threading.Lock()

    def _yol(self, goreli, ek):
        yol = os.path.join(self.klasor, os.path.splitext(goreli)[0] + ek)
        os.makedirs(os.path.dirname(yol), exist_ok=True)
        return yol

    def hata(self, dosya, mesaj):
        with self._kilit:
            self.hata_sayisi += 1
            with open(os.path.join(self.klasor, "hatalar.txt"), "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {dosya}: {mesaj}\n")

    def uyari(self, dosya, mesaj):
        """Hata olmayan, bilgi amaçlı notlar (okuma hızı gibi)."""
        with self._kilit:
            with open(os.path.join(self.klasor, "uyarilar.txt"), "a", encoding="utf-8") as f:
                f.write(f"{dosya}: {mesaj}\n")

    def ham(self, dosya, kayit):
        sure = getattr(Model.yerel, "sure", None)
        if sure is not None:            # isteğin süresi ve yanıtın geldiği saat: hangi istek yavaş, logdan görülsün
            kayit = {"sn": sure, "saat": time.strftime("%H:%M:%S"), **kayit}
            Model.yerel.sure = None
        with self._kilit:
            with open(self._yol(dosya, "_ham.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(kayit, ensure_ascii=False) + "\n")

    def ayarlar(self, ayar):
        """Bu çalıştırmada geçerli olan bütün ayarlar (varsayılanlar dahil): hangi denemede ne açıktı, buradan okunur."""
        with open(os.path.join(self.klasor, "ayarlar.json"), "w", encoding="utf-8") as f:
            json.dump(ayar, f, ensure_ascii=False, indent=2)

    def istem(self, dosya, metin):
        """Bu dosya için modele giden sistem metni (kurallar + sözlük), olduğu gibi."""
        with open(self._yol(dosya, "_istem.txt"), "w", encoding="utf-8") as f:
            f.write(metin)

    def karsilastirma(self, dosya, ciftler):
        """ciftler: (numara, zaman, kaynak, çeviri) listesi."""
        with open(self._yol(dosya, "_karsilastirma.txt"), "w", encoding="utf-8") as f:
            for no, zaman, kaynak, ceviri in ciftler:
                f.write(f"#{no}  {zaman}\nKAYNAK: {kaynak}\nÇEVİRİ: {ceviri}\n\n")

    def ozet(self, metin):
        with open(os.path.join(self.klasor, "ozet.txt"), "w", encoding="utf-8") as f:
            f.write(metin)
