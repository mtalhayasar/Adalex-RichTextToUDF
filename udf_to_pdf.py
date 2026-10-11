# -*- coding: utf-8 -*-
"""
udf_to_pdf.py - UDF (UYAP Universal Document Format) dosyalarini PDF'e cevirir.

UDF, icinde content.xml bulunan bir ZIP arsividir. content.xml:
  - <content><![CDATA[...]]></content>  : tum duz metin
  - <elements> ... <paragraph> ...      : metindeki offset/length'lere gore
                                          bicimlenmis run'lar (content/field/space)
  - <properties><pageFormat .../>       : sayfa boyutu + kenar bosluklari (point)

Bu modul content.xml'i parse edip QTextDocument kurar ve QPdfWriter ile A4 PDF
uretir. Sadece PySide6'ya bagimlidir (harici arac/Java gerekmez).

Komut satiri modlari:
  udf2pdf.exe convert  a.udf [b.udf ...]   -> her dosya yaninda .pdf (sessiz)
  udf2pdf.exe merge    a.udf [b.udf ...]   -> coklu secimi tek PDF'te birlestir
  udf2pdf.exe install                      -> sag-tik menusunu kaydet (HKCU)
  udf2pdf.exe uninstall                    -> sag-tik menusunu kaldir
"""
import sys
import os
import re
import time
import glob
import tempfile

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from PySide6.QtGui import (QTextDocument, QTextCursor, QFont, QTextBlockFormat,
                           QTextCharFormat, QTextOption, QTextListFormat, QTextFormat,
                           QTextFrameFormat, QTextTableFormat, QTextTableCellFormat,
                           QTextLength, QBrush,
                           QPdfWriter, QPageSize, QPageLayout, QColor)
from PySide6.QtCore import QSizeF, QMarginsF, Qt

import udf_motor


# ----------------------------------------------------------------------------
# Yardimci yollar (PyInstaller uyumlu)
# ----------------------------------------------------------------------------
def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(base_path, relative_path)


def app_executable():
    """Sag-tik komutuna yazilacak calistirilabilir yol.

    Frozen (exe) ise exe yolu; degilse 'pythonw script' kombinasyonu.
    Donen deger registry'ye gomulecek sablon parcasidir.
    """
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}"'
    # Gelistirme modunda venv pythonw + bu script
    pyw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
    if not os.path.exists(pyw):
        pyw = sys.executable
    return f'"{pyw}" "{os.path.abspath(__file__)}"'


# ----------------------------------------------------------------------------
# UDF parse (Adalex-UYAP SAF UDF motoru: udf_motor.py)
#
# Ayrıştırma Adalex'in `udf_ayristir`ıdır: tablo / üstbilgi / altbilgi düğümleri, form
# alanı (`field`), boşluk (`space`) ve sekme (`tab`) koşuları ile UYAP şablon belgelerinin
# `<data>` değerleri (tevzi formu, ödeme emri ...) korunur. Eskiden yalnız üst düzey
# `<paragraph>` + content/field/space okunuyordu: tablo içindeki metin (duruşma zaptı,
# tensip) ve sekme koşuları PDF'te kayboluyordu.
# ----------------------------------------------------------------------------
class UdfDosya:
    """Ayrıştırılmış UDF + çizim için gereken bağlam."""
    __slots__ = ('belge', 'veri', 'sayfa', 'govde')

    def __init__(self, belge, veri, sayfa, govde):
        self.belge = belge
        self.veri = veri      # şablon belgenin <data> kökü (yoksa None)
        self.sayfa = sayfa    # pageFormat öznitelikleri
        self.govde = govde    # hvl-default gövde stili öznitelikleri


def _oznitelikler(etiket_ici):
    return dict(re.findall(r'([\w-]+)\s*=\s*"([^"]*)"', etiket_ici or ''))


def _sayfa_bicimi(properties_xml):
    m = re.search(r'<pageFormat\b([^>]*)>', properties_xml or '')
    return _oznitelikler(m.group(1)) if m else {}


def _govde_stili(styles_xml):
    """`<styles>` içindeki gövde stilinin (`hvl-default`) belge geneli varsayılanları."""
    for m in re.finditer(r'<style\b([^>]*)>', styles_xml or ''):
        oz = _oznitelikler(m.group(1))
        if oz.get('name') == udf_motor.GOVDE_STIL:
            return oz
    return {}


def parse_udf(path):
    """UDF dosyasını `UdfDosya` olarak okur (ayrıştırılamazsa ValueError)."""
    with open(path, 'rb') as f:
        veri = f.read()
    belge = udf_motor.udf_ayristir(veri)
    return UdfDosya(belge, udf_motor.udf_sablon_verisi(veri),
                    _sayfa_bicimi(belge.properties_xml), _govde_stili(belge.styles_xml))


# ----------------------------------------------------------------------------
# PDF render
# ----------------------------------------------------------------------------
_ALIGN_MAP = {
    0: Qt.AlignLeft,
    1: Qt.AlignHCenter,
    2: Qt.AlignRight,
    3: Qt.AlignJustify,
}

# Swing TabStop hizalaması: 0 sol, 1 sağ, 2 orta.
_SEKME_TURU = {0: QTextOption.LeftTab, 1: QTextOption.RightTab, 2: QTextOption.CenterTab}

# Java MediaSizeName indeksi -> QPageSize (yaygin degerler; varsayilan A4)
_MEDIA_MAP = {
    '0': QPageSize.A4,   # ISO A serisi UYAP'ta cogunlukla A4
    '1': QPageSize.A4,
}


def _bool(v):
    return str(v).lower() == 'true'


def _sayi(v, varsayilan=0.0):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return varsayilan


def _renk(deger):
    """UDF `foreground` (Java signed int ARGB ya da #rrggbb) -> QColor; çözülemezse None."""
    ham = str(deger or '').strip()
    if not ham:
        return None
    if re.fullmatch(r'#?[0-9a-fA-F]{6}', ham):
        return QColor('#' + ham.lstrip('#'))
    try:
        n = int(ham)
    except ValueError:
        return None
    return QColor((n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF)


# Punto -> 96 dpi MANTIKSAL piksel. Sekme konumları (QTextOption.Tab / setTabStopDistance)
# ve tablo hücre kenarlığı yazıcı aygıtında bile mantıksal pikseldir - Qt
# onları aygıt çözünürlüğüne kendisi ölçekler (paragraf kenar boşlukları ve hücre
# dolgusu ise aygıt pikselidir). Ölçüldü: 300 dpi yazıcıda 100 birimlik durak 312.5 aygıt pikseline düşer.
MANTIKSAL_PT = 96.0 / 72.0


def _sekme_duraklari(nit):
    out = []
    for parca in str(nit.get('TabSet') or '').split(','):
        a = parca.strip().split(':')
        if not a[0]:
            continue
        try:
            konum = float(a[0])
            tur = int(a[1]) if len(a) > 1 and a[1].strip() else 0
        except ValueError:
            continue
        out.append(QTextOption.Tab(konum * MANTIKSAL_PT, _SEKME_TURU.get(tur, QTextOption.LeftTab)))
    return sorted(out, key=lambda t: t.position)


class _Cizici:
    """UDF düğüm ağacını QTextDocument'e döker. Birden çok belge aynı belgeye art arda
    çizilebilir (birleştirme); her yeni belge yeni sayfada başlar."""

    def __init__(self, doc, scale):
        self.doc = doc
        self.scale = scale
        self.cursor = QTextCursor(doc)
        self.bos_blok = True        # imleçteki blok henüz kullanılmadı (setBlockFormat yeter)
        self.sayfa_kes = False      # sıradaki blok yeni sayfada başlasın
        self.open_lists = {}
        self.udf = None

    # --- belge ---------------------------------------------------------------
    def belge(self, udf):
        self.udf = udf
        self.open_lists = {}
        if self.doc.characterCount() > 1 or not self.bos_blok:
            self.sayfa_kes = True
        b = udf.belge
        govde_dugumler = [d for d in b.dugumler if not isinstance(d, udf_motor.Bolum)]
        for dugumler in (b.ust_dugumler, govde_dugumler, b.alt_dugumler):
            self.agac(udf_motor.belge_agaci(dugumler, udf.veri))

    def agac(self, agac):
        for d in agac:
            if d[0] == 'p':
                self.paragraf(d[1], d[2])
            else:
                self.tablo(d[1], d[2])

    # --- paragraf ------------------------------------------------------------
    def _yeni_blok(self, fmt):
        if self.sayfa_kes:
            fmt.setPageBreakPolicy(QTextFormat.PageBreak_AlwaysBefore)
            self.sayfa_kes = False
        if self.bos_blok:
            self.cursor.setBlockFormat(fmt)
            self.bos_blok = False
        else:
            self.cursor.insertBlock(fmt)

    def _blok_bicimi(self, nit):
        s = self.scale
        g = self.udf.govde
        fmt = QTextBlockFormat()
        try:
            hiza = int(str(nit.get('Alignment', g.get('Alignment', 0))).strip())
        except ValueError:
            hiza = 0
        fmt.setAlignment(_ALIGN_MAP.get(hiza, Qt.AlignLeft))
        sol = _sayi(nit.get('LeftIndent', g.get('LeftIndent')))
        asili = _sayi(nit.get('Hanging'))
        if asili > 0:
            # UYAP: ilk satır LeftIndent'te, devam satırları LeftIndent + Hanging'de; sekme
            # durağı satır başından sayılır (Qt de öyle sayar - ölçüldü).
            fmt.setLeftMargin((sol + asili) * s)
            fmt.setTextIndent(-asili * s)
        else:
            if sol:
                fmt.setLeftMargin(sol * s)
            ilk = _sayi(nit.get('FirstLineIndent'))
            if ilk:
                fmt.setTextIndent(ilk * s)
        sag = _sayi(nit.get('RightIndent', g.get('RightIndent')))
        if sag:
            fmt.setRightMargin(sag * s)
        fmt.setTopMargin(_sayi(nit.get('SpaceAbove', g.get('SpaceAbove'))) * s)
        fmt.setBottomMargin(_sayi(nit.get('SpaceBelow', g.get('SpaceBelow'))) * s)
        # Swing LineSpacing çarpan ARTIŞIdır: 0 tek, 0.5 bir buçuk, 1 çift satır.
        ls = _sayi(nit.get('LineSpacing', g.get('LineSpacing')))
        if ls > 0.01:
            fmt.setLineHeight(100.0 * (1.0 + ls), 1)  # 1 = ProportionalHeight
        tabs = _sekme_duraklari(nit)
        if tabs:
            fmt.setTabPositions(tabs)
        return fmt

    def _liste(self, nit):
        if _bool(nit.get('Numbered')):
            tur = 'numbered'
        elif _bool(nit.get('Bulleted')):
            tur = 'bulleted'
        else:
            self.open_lists.clear()
            return
        try:
            seviye = int(nit.get('ListLevel', 1) or 1)
        except ValueError:
            seviye = 1
        anahtar = (nit.get('ListId'), seviye, tur)
        mevcut = self.open_lists.get(anahtar)
        if mevcut is not None:
            mevcut.add(self.cursor.block())
            return
        lf = QTextListFormat()
        lf.setIndent(seviye)
        if tur == 'numbered':
            nt = nit.get('NumberType') or ''
            if 'CHAR' in nt or 'LETTER' in nt or 'ALPHA' in nt:
                lf.setStyle(QTextListFormat.ListLowerAlpha)
            else:
                lf.setStyle(QTextListFormat.ListDecimal)
            lf.setNumberSuffix(')' if 'PARANTHESE' in nt or 'PAREN' in nt else '.')
        else:
            lf.setStyle(QTextListFormat.ListDisc)
        self.open_lists[anahtar] = self.cursor.createList(lf)

    def _karakter_bicimi(self, nit):
        g = self.udf.govde
        cf = QTextCharFormat()
        f = QFont(str(nit.get('family') or g.get('family') or 'Times New Roman'))
        f.setPointSizeF(_sayi(nit.get('size'), _sayi(g.get('size'), 12.0)) or 12.0)
        f.setBold(_bool(nit.get('bold')))
        f.setItalic(_bool(nit.get('italic')))
        f.setUnderline(_bool(nit.get('underline')))
        f.setStrikeOut(_bool(nit.get('StrikeThrough')))
        cf.setFont(f)
        if _bool(nit.get('Superscript')):
            cf.setVerticalAlignment(QTextCharFormat.AlignSuperScript)
        elif _bool(nit.get('Subscript')):
            cf.setVerticalAlignment(QTextCharFormat.AlignSubScript)
        renk = _renk(nit.get('foreground'))
        if renk is not None:
            cf.setForeground(renk)
        return cf

    def paragraf(self, p, ctx):
        nit = p.nitelikler
        metin = self.udf.belge.metin
        self._yeni_blok(self._blok_bicimi(nit))
        self._liste(nit)
        # Paragrafın yazı biçimi koşulara VARSAYILANdır (UYAP formlarında madde başlığı
        # `<paragraph bold="true">` taşır); koşu kendi değerini yazıyorsa onu ezer.
        p_yazi = {k: nit[k] for k in ('family', 'size', 'bold', 'italic') if k in nit}
        kosular = []
        for k in sorted(p.kosular, key=lambda x: x.start):
            alan = ctx is not None and k.etiket == 'field'
            if ctx is not None and k.etiket in ('field', 'content', 'space', 'tab'):
                ham = udf_motor.sablon_kosu_metni(k, ctx, metin)
            elif k.etiket == 'image' or k.uzunluk <= 0:
                continue
            else:
                ham = metin[max(0, k.start): max(0, k.start) + k.uzunluk]
            ham = ham.replace(udf_motor.ESKI_BOS_PARAGRAF, '')
            if ham:
                kosular.append([ham, dict(p_yazi, **k.nitelikler), alan])
        # Paragraf sonu ('\n') paragrafın kendisidir, metni değil. Form alanının satır sonu
        # (`eol`) ise içeriktir: aynı paragrafta satır kırılır.
        if kosular and not kosular[-1][2]:
            kosular[-1][0] = re.sub(r'\r?\n$', '', kosular[-1][0])
        for ham, k_nit, _alan in kosular:
            ham = ham.replace('\r\n', '\n').replace('\n', ' ')
            if ham:
                self.cursor.insertText(ham, self._karakter_bicimi(k_nit))

    # --- tablo ---------------------------------------------------------------
    def tablo(self, t, satirlar):
        """UDF tablosu. Her satır kendi sütun düzenini taşıyabilir (`row columnSpans`), bu
        yüzden satır başına tek satırlık bir tablo çizilir (Adalex önizlemesiyle aynı model).
        Kenarlık: borderNone yok, borderCell / borderTable çizgili."""
        s = self.scale
        nit = t.nitelikler
        kenar = str(nit.get('border') or 'borderNone')
        tablo_span = self._spanlar(nit.get('columnSpans'))
        self.open_lists.clear()
        for satir, hucreler in satirlar:
            if not hucreler:
                continue
            spanlar = self._spanlar(satir.nitelikler.get('columnSpans')) or tablo_span
            if len(spanlar) != len(hucreler) or sum(spanlar) <= 0:
                spanlar = [1.0] * len(hucreler)
            toplam = sum(spanlar)
            tf = QTextTableFormat()
            tf.setWidth(QTextLength(QTextLength.PercentageLength, 100))
            tf.setColumnWidthConstraints(
                [QTextLength(QTextLength.PercentageLength, g / toplam * 100) for g in spanlar])
            tf.setCellSpacing(0)
            tf.setCellPadding(2.0 * s)
            tf.setLeftMargin(_sayi(nit.get('LeftIndent')) * s)
            tf.setRightMargin(_sayi(nit.get('RightIndent')) * s)
            # Qt tablo düzeyindeki kenarlığı tek başına ÇİZMEZ (ölçüldü); çizgi ancak çöken
            # kenar (border-collapse) + hücre kenarlığıyla görünür.
            tf.setBorder(0)
            if kenar != 'borderNone':
                tf.setBorderCollapse(True)
            if self.sayfa_kes:
                tf.setPageBreakPolicy(QTextFormat.PageBreak_AlwaysBefore)
                self.sayfa_kes = False
            # Qt tabloyu imleçteki bloğun ÖNÜNE koyar ve blok tablodan sonra kalır; dolu
            # paragrafın arkasına önce boş bir blok açılır, yoksa önceki boş satır tablonun
            # altına kayıp sıradaki paragrafa yutulurdu.
            if not self.bos_blok:
                self.cursor.insertBlock(QTextBlockFormat())
            tablo = self.cursor.insertTable(1, len(hucreler), tf)
            ilk_satir = satir is satirlar[0][0]
            son_satir = satir is satirlar[-1][0]
            for i, hucre in enumerate(hucreler):
                if kenar != 'borderNone':
                    # borderCell: her hücre çerçeveli; borderTable: yalnız dış çerçeve.
                    hepsi = kenar != 'borderTable'
                    cizgiler = (
                        (QTextTableCellFormat.setLeftBorder, hepsi or i == 0),
                        (QTextTableCellFormat.setRightBorder, hepsi or i == len(hucreler) - 1),
                        (QTextTableCellFormat.setTopBorder, ilk_satir),  # satırlar üst üste: çift çizgi olmasın
                        (QTextTableCellFormat.setBottomBorder, hepsi or son_satir),
                    )
                    hf = tablo.cellAt(0, i).format().toTableCellFormat()
                    hf.setBorderStyle(QTextFrameFormat.BorderStyle_Solid)
                    hf.setBorderBrush(QBrush(QColor(0, 0, 0)))
                    for ayarla, var in cizgiler:
                        ayarla(hf, 0.75 * MANTIKSAL_PT if var else 0)
                    tablo.cellAt(0, i).setFormat(hf)
                self.cursor = tablo.cellAt(0, i).firstCursorPosition()
                self.bos_blok = True
                self.agac(hucre)
            # Tablodan sonra Qt daima bir blok bırakır; sıradaki paragraf onu kullanır.
            self.cursor = QTextCursor(self.doc)
            self.cursor.setPosition(tablo.lastPosition() + 1)
            self.bos_blok = True
            self.open_lists.clear()

    @staticmethod
    def _spanlar(deger):
        try:
            return [float(x) for x in str(deger or '').split(',') if x.strip()]
        except ValueError:
            return []


def _build_document(udf_dosyalari, writer):
    """UdfDosya listesi -> QTextDocument (writer çözünürlüğünde). Birden çok belge art arda
    çizilir; her biri yeni sayfada başlar."""
    scale = writer.resolution() / 72.0  # point -> cihaz pikseli

    doc = QTextDocument()
    doc.setDocumentMargin(0)
    # Font metriklerinin de sayfa geometrisiyle ayni cozunurlukte (writer dpi)
    # hesaplanmasi icin belge duzeninin boya aygitini writer'a bagla. Aksi halde
    # QTextDocument fontlari varsayilan 96 dpi'de dizer, sayfa ise writer dpi'de
    # olceklenir ve metin ~4x kucuk cikar.
    doc.documentLayout().setPaintDevice(writer)

    ilk = udf_dosyalari[0].govde if udf_dosyalari else {}
    base_font = QFont(str(ilk.get('family') or 'Times New Roman'))
    base_font.setPointSizeF(_sayi(ilk.get('size'), 12.0) or 12.0)
    doc.setDefaultFont(base_font)

    # TabSet'siz paragrafta UYAP editörünün varsayılan durak adımı 72 pt'dir (Swing
    # `nextTabStop`: durak yoksa bir sonraki inç).
    opt = QTextOption()
    opt.setTabStopDistance(72.0 * MANTIKSAL_PT)
    doc.setDefaultTextOption(opt)

    cizici = _Cizici(doc, scale)
    cizici.cursor.beginEditBlock()
    for udf in udf_dosyalari:
        cizici.belge(udf)
    cizici.cursor.endEditBlock()
    return doc


def _make_writer(out_path, page_format):
    writer = QPdfWriter(out_path)
    writer.setResolution(300)

    media = page_format.get('mediaSizeName', '1')
    page_id = _MEDIA_MAP.get(str(media), QPageSize.A4)
    writer.setPageSize(QPageSize(page_id))

    # paperOrientation: Java PageFormat sabiti - LANDSCAPE=0, PORTRAIT=1, REVERSE_LANDSCAPE=2
    orient = str(page_format.get('paperOrientation', '1')).strip()
    if orient in ('0', '2'):
        writer.setPageOrientation(QPageLayout.Landscape)
    else:
        writer.setPageOrientation(QPageLayout.Portrait)

    def _m(key, default):
        try:
            return float(page_format.get(key, default))
        except (ValueError, TypeError):
            return default

    left = _m('leftMargin', 56.69)
    right = _m('rightMargin', 56.69)
    top = _m('topMargin', 56.69)
    bottom = _m('bottomMargin', 56.69)
    writer.setPageMargins(QMarginsF(left, top, right, bottom), QPageLayout.Point)
    return writer


def _layout_and_print(doc, writer):
    page_rect = writer.pageLayout().paintRectPixels(writer.resolution())
    doc.setPageSize(QSizeF(page_rect.width(), page_rect.height()))
    doc.print_(writer)


def render_udf_to_pdf(udf_path, pdf_path):
    """Tek bir UDF dosyasini PDF'e cevirir."""
    udf = parse_udf(udf_path)
    writer = _make_writer(pdf_path, udf.sayfa)
    doc = _build_document([udf], writer)
    _layout_and_print(doc, writer)


def render_many_to_pdf(udf_paths, pdf_path):
    """Birden fazla UDF dosyasini tek bir PDF'te birlestirir (her dosya yeni
    sayfada baslar). Sayfa formati ilk basariyla okunan dosyadan alinir."""
    if not udf_paths:
        return

    okunan = []
    for p in udf_paths:
        try:
            okunan.append(parse_udf(p))
        except Exception:
            continue

    if not okunan:
        raise ValueError("Hicbir UDF dosyasi okunamadi.")

    writer = _make_writer(pdf_path, okunan[0].sayfa)
    doc = _build_document(okunan, writer)
    _layout_and_print(doc, writer)


# ----------------------------------------------------------------------------
# Kullanici bildirimleri (QMessageBox)
# ----------------------------------------------------------------------------
_APP = None


def _ensure_app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication(sys.argv)
        icon_path = resource_path('icons/solo-logo-32.png')
        if os.path.exists(icon_path):
            from PySide6.QtGui import QIcon
            _APP.setWindowIcon(QIcon(icon_path))
    return _APP


def _info(title, text):
    _ensure_app()
    QMessageBox.information(None, title, text)


def _error(title, text):
    _ensure_app()
    QMessageBox.critical(None, title, text)


# ----------------------------------------------------------------------------
# convert: her UDF -> yaninda .pdf (sessiz)
# ----------------------------------------------------------------------------
def cmd_convert(paths):
    _ensure_app()
    errors = []
    for p in paths:
        if not p.lower().endswith('.udf'):
            continue
        try:
            out = os.path.splitext(p)[0] + '.pdf'
            render_udf_to_pdf(p, out)
        except Exception as e:
            errors.append(f"{os.path.basename(p)}: {e}")
    if errors:
        _error("PDF'e Dönüştürme Hatası",
               "Bazı dosyalar dönüştürülemedi:\n\n" + "\n".join(errors))


# ----------------------------------------------------------------------------
# merge: coklu secimi tek PDF'te birlestir
#
# Windows Gezgini, "merge" verb'ini secilen her dosya icin AYRI bir surec
# olarak baslatir. Bu surecler bir named-mutex ile lider secer; lider kisa
# bir "durulma" penceresi boyunca kardes sureclerin biraktigi dosya yollarini
# toplar ve hepsini tek PDF'te birlestirir. Lider olmayanlar yolunu birakip
# cikar.
# ----------------------------------------------------------------------------
_MERGE_DIR = os.path.join(tempfile.gettempdir(), 'adalex_udf_merge')
_MUTEX_NAME = "Global\\AdaLexUdfMergeLeader"
_ERROR_ALREADY_EXISTS = 183


def _acquire_leader():
    """Named mutex ile lider olmayi dener. Lider ise handle, degilse None."""
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        if handle:
            kernel32.CloseHandle(handle)
        return None
    return handle


def _release_leader(handle):
    if handle:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


def _drop_path(path):
    """Bu surecin dosya yolunu batch dizinine atomik olarak birakir."""
    os.makedirs(_MERGE_DIR, exist_ok=True)
    stamp = f"{os.getpid()}_{int(time.monotonic() * 1000)}"
    final = os.path.join(_MERGE_DIR, stamp + '.txt')
    tmp = final + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(os.path.abspath(path))
    os.replace(tmp, final)


def _collect_paths_when_stable(stable_for=0.8, timeout=10.0):
    """Batch dizinindeki dosya sayisi 'stable_for' saniye sabit kalana dek
    bekler, sonra tum yollari (sirali, tekil) doner."""
    last_count, last_change, start = -1, time.monotonic(), time.monotonic()
    while True:
        files = glob.glob(os.path.join(_MERGE_DIR, '*.txt'))
        now = time.monotonic()
        if len(files) != last_count:
            last_count, last_change = len(files), now
        if last_count > 0 and (now - last_change) >= stable_for:
            break
        if (now - start) > timeout:
            break
        time.sleep(0.1)

    paths, seen = [], set()
    for fp in files:
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                p = f.read().strip()
        except OSError:
            continue
        key = p.lower()
        if p and key not in seen and os.path.exists(p):
            seen.add(key)
            paths.append(p)
    paths.sort(key=lambda s: os.path.basename(s).lower())
    return paths, files


def cmd_merge(paths):
    _ensure_app()
    for p in paths:
        if p.lower().endswith('.udf'):
            _drop_path(p)

    leader = _acquire_leader()
    if leader is None:
        # Lider degiliz: yolumuzu biraktik, cikiyoruz.
        return

    try:
        udf_paths, batch_files = _collect_paths_when_stable()
        if not udf_paths:
            return

        default_dir = os.path.dirname(udf_paths[0])
        default_name = os.path.join(default_dir, "Birlestirilmis.pdf")
        out_path, _ = QFileDialog.getSaveFileName(
            None, "Birleştirilmiş PDF'i Kaydet", default_name,
            "PDF Dosyaları (*.pdf)")
        if not out_path:
            return
        if not out_path.lower().endswith('.pdf'):
            out_path += '.pdf'

        try:
            render_many_to_pdf(udf_paths, out_path)
        except Exception as e:
            _error("Birleştirme Hatası", f"PDF oluşturulamadı:\n{e}")
            return

        # Islenen batch dosyalarini temizle
        for fp in batch_files:
            try:
                os.remove(fp)
            except OSError:
                pass
    finally:
        _release_leader(leader)


# ----------------------------------------------------------------------------
# install / uninstall: HKCU sag-tik menusu
# ----------------------------------------------------------------------------
def _shell_base():
    import winreg
    return (winreg.HKEY_CURRENT_USER,
            r"Software\Classes\SystemFileAssociations\.udf\shell")


def cmd_install(quiet=False):
    import winreg
    exe = app_executable()  # tirnakli calistirilabilir on-ek

    icon = sys.executable if getattr(sys, 'frozen', False) else resource_path('solo-logo.ico')

    verbs = [
        ("AdaLex.ToPDF", "PDF'e Dönüştür", "convert"),
        ("AdaLex.MergePDF", "Tek PDF'te Birleştir", "merge"),
    ]
    root, base = _shell_base()
    for key_name, label, mode in verbs:
        verb_key = f"{base}\\{key_name}"
        with winreg.CreateKey(root, verb_key) as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, label)
            winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, icon)
            # Coklu secimde her dosya icin ayri surec baslatilsin
            winreg.SetValueEx(k, "MultiSelectModel", 0, winreg.REG_SZ, "Document")
        with winreg.CreateKey(root, verb_key + r"\command") as ck:
            winreg.SetValueEx(ck, None, 0, winreg.REG_SZ, f'{exe} {mode} "%1"')

    if quiet:
        return
    _info("AdaLex UDF→PDF",
          "Sağ tık menüsü eklendi.\n\n"
          "Bir .udf dosyasına sağ tıklayın:\n"
          "  • PDF'e Dönüştür  (her dosya ayrı PDF)\n"
          "  • Tek PDF'te Birleştir  (seçilenleri birleştirir)\n\n"
          "Windows 11'de 'Diğer seçenekleri göster' altında olabilir.")


def cmd_uninstall(quiet=False):
    import winreg
    root, base = _shell_base()
    for key_name in ("AdaLex.ToPDF", "AdaLex.MergePDF"):
        verb_key = f"{base}\\{key_name}"
        try:
            winreg.DeleteKey(root, verb_key + r"\command")
        except OSError:
            pass
        try:
            winreg.DeleteKey(root, verb_key)
        except OSError:
            pass
    if quiet:
        return
    _info("AdaLex UDF→PDF", "Sağ tık menüsü kaldırıldı.")


# ----------------------------------------------------------------------------
# Giris noktasi
# ----------------------------------------------------------------------------
def main(argv):
    if len(argv) < 2:
        _info("AdaLex UDF→PDF Dönüştürücü",
              "Kullanım:\n"
              "  udf2pdf install     → sağ tık menüsünü ekle\n"
              "  udf2pdf uninstall   → sağ tık menüsünü kaldır\n"
              "  udf2pdf convert a.udf [b.udf ...]\n"
              "  udf2pdf merge   a.udf [b.udf ...]")
        return 0

    mode = argv[1].lower()
    rest = argv[2:]
    quiet = any(a.lower() in ('/quiet', '-q', 'quiet') for a in rest)

    if mode == 'install':
        cmd_install(quiet=quiet)
    elif mode == 'uninstall':
        cmd_uninstall(quiet=quiet)
    elif mode == 'convert':
        cmd_convert(rest)
    elif mode == 'merge':
        cmd_merge(rest)
    else:
        # Mod belirtilmemis: dosya yolu varsayilani convert
        files = [a for a in argv[1:] if a.lower().endswith('.udf')]
        if files:
            cmd_convert(files)
        else:
            _error("AdaLex UDF→PDF", f"Bilinmeyen komut: {mode}")
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
