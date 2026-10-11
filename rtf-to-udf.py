# -*- coding: utf-8 -*-
VERSION = "2.2.0"
import sys
import re
import os
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                               QTextEdit, QPushButton, QLabel, QFileDialog,
                               QMessageBox, QMainWindow, QTabWidget)
from PySide6.QtCore import Qt
from PySide6.QtGui import (QTextCharFormat, QTextFormat, QTextBlockFormat, QTextListFormat,
                           QPixmap, QIcon)
import xml.etree.ElementTree as ET
from sablon_editor import TemplateEditorWidget
import udf_motor


def resource_path(relative_path):
    """PyInstaller ile paketlendiğinde dosya yollarını düzgün bulma fonksiyonu"""
    try:
        # PyInstaller ile çalışırken _MEIPASS kullanılır
        base_path = sys._MEIPASS
    except Exception:
        # Normal çalıştırma sırasında mevcut dizini kullan
        base_path = os.path.abspath(".")
    
    return os.path.join(base_path, relative_path)


# cm/mm/in/pt -> px yaklasik donusum carpanlari (96 dpi)
_UNIT_PX = {'cm': 37.795, 'mm': 3.7795, 'in': 96.0, 'pt': 1.3333}


def fix_pasted_html(html):
    """Word panosundan gelen HTML'i Qt import etmeden ONCE onar.

    Qt'nin HTML alt kumesi iki seyi kaybeder; yapistirma aninda duzeltiriz:
    1) margin/text-indent/padding birimleri cm/mm/in/pt ise px'e cevir (Qt yalnizca
       px'i tanir; aksi halde girinti 0 olur).
    2) 'text-align:justify' iceren acilis etiketine align="justify" ekle (Qt CSS
       justify'i tanimaz ama align="justify" oznitel/igini tanir).
    Yalnizca paragraf bicimini onarir; metni DEGISTIRMEZ (offset/length guvenli).
    """
    def _conv_decl(m):
        prop, num, unit = m.group(1), m.group(2), m.group(3).lower()
        return f"{prop}{float(num) * _UNIT_PX[unit]:.2f}px"

    html = re.sub(
        r'(?i)((?:margin|text-indent|padding)[\w-]*\s*:\s*)(\d*\.?\d+)\s*(cm|mm|in|pt)\b',
        _conv_decl, html)

    def _add_justify(m):
        tag = m.group(0)
        if re.search(r'text-align\s*:\s*justify', tag, re.I) and not re.search(r'\balign\s*=', tag):
            return re.sub(r'^<(\w+)', r'<\1 align="justify"', tag, count=1)
        return tag

    html = re.sub(r'<\w+[^>]*>', _add_justify, html)
    return html


# Qt belge ölçüleri piksel (96 dpi), UDF ölçüleri puntodur (72 dpi).
PX_PT = 0.75

# Tipografik karakter -> UYAP editörünün sorunsuz gösterdiği düz karşılık (uzunluk korunur).
_TIPOGRAFI = str.maketrans({
    '–': '-', '“': '"', '”': '"', '’': "'", '‘': "'",
    '\u000B': ' ', '\u000C': ' ',
})


def _pg_metni(pg):
    return ''.join(r['metin'] for r in pg['kosular'])


def _kosu_bicimi(cf):
    """QTextCharFormat -> udf_motor koşu sözlüğü anahtarları. Yazı tipi/punto/renk yalnız
    kaynakta AÇIKÇA verilmişse taşınır; verilmemişse belge gövde stili (Times New Roman 12)
    geçerli olur."""
    font = cf.font()
    r = {
        'bold': font.bold(),
        'italic': font.italic(),
        'underline': font.underline(),
        'ustuCizili': font.strikeOut(),
        'ustSimge': cf.verticalAlignment() == QTextCharFormat.AlignSuperScript,
        'altSimge': cf.verticalAlignment() == QTextCharFormat.AlignSubScript,
    }
    if cf.hasProperty(QTextFormat.FontFamilies):
        aileler = cf.fontFamilies() or []
        if aileler:
            r['family'] = str(aileler[0])
    if cf.hasProperty(QTextFormat.FontPointSize) and cf.fontPointSize() > 0:
        r['size'] = int(round(cf.fontPointSize()))
    elif cf.hasProperty(QTextFormat.FontPixelSize) and font.pixelSize() > 0:
        r['size'] = int(round(font.pixelSize() * PX_PT))
    if cf.hasProperty(QTextFormat.ForegroundBrush):
        renk = cf.foreground().color()
        if renk.isValid() and renk.rgb() & 0xFFFFFF:
            r['renk'] = renk.name()
    return r


def _hizalama(blok_bicimi):
    h = int(blok_bicimi.alignment()) & int(Qt.AlignHorizontal_Mask)
    if h & int(Qt.AlignHCenter):
        return 1
    if h & int(Qt.AlignRight):
        return 2
    if h & int(Qt.AlignJustify):
        return 3
    return 0


def qt_paragraflari(doc):
    """QTextDocument -> udf_motor paragraf sözlükleri (her görünen satır bir paragraf).

    Qt listeleri ve elle yazılmış "1) " / "a) " maddeleri UDF'in yerel liste özniteliklerine
    (`ek_nitelik`) çevrilir; çok boşluklu eski hizalama sekmeye dönüşür (künye kolonu motorda
    hesaplanır)."""
    paragraflar = []
    list_id_counter = 1
    current_lists = {}
    num_list_id = None
    num_last = 0
    char_list_id = None
    char_last = 0

    block = doc.begin()
    while block.isValid():
        bf = block.blockFormat()
        text_list = block.textList()

        list_info = None
        if text_list:
            lf = text_list.format()
            seviye = lf.indent()
            if lf.style() == QTextListFormat.ListDecimal:
                tur = 'numbered'
            elif lf.style() in (QTextListFormat.ListDisc, QTextListFormat.ListCircle,
                                QTextListFormat.ListSquare):
                tur = 'bulleted'
            else:
                tur = None
            if tur:
                anahtar = f"{tur}_{seviye}"
                if anahtar not in current_lists:
                    current_lists[anahtar] = list_id_counter
                    list_id_counter += 1
                list_info = {'type': tur, 'level': seviye, 'list_id': current_lists[anahtar],
                             'left_indent': 25.0 * seviye}

        # Blok koşuları; U+2028/U+2029 satır ayırıcıları blok içinde ayrı paragraf demektir.
        satirlar = [[]]
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid():
                bicim = _kosu_bicimi(frag.charFormat())
                for i, parca in enumerate(re.split('[  ]', frag.text())):
                    if i:
                        satirlar.append([])
                    if parca:
                        satirlar[-1].append(dict(bicim, metin=parca))
            it += 1

        for satir_idx, kosular in enumerate(satirlar):
            satir_metni = ''.join(r['metin'] for r in kosular)
            manual_list_info = None
            strip_count = 0
            if not text_list:
                m_num = re.match(r'(\d+)\)\s', satir_metni)
                m_chr = re.match(r'([a-zçğıöşü])\)\s', satir_metni)
                if m_num:
                    n = int(m_num.group(1))
                    if num_list_id is None or n == 1 or n != num_last + 1:
                        list_id_counter += 1
                        num_list_id = list_id_counter
                    num_last = n
                    char_list_id = None
                    char_last = 0
                    manual_list_info = {'type': 'numbered',
                                        'number_type': 'NUMBER_TYPE_NUMBER_PARANTHESE',
                                        'level': 1, 'list_id': num_list_id, 'left_indent': 25.0}
                    strip_count = m_num.end()
                elif m_chr:
                    ch = m_chr.group(1)
                    ordv = (ord(ch) - ord('a') + 1) if 'a' <= ch <= 'z' else None
                    if char_list_id is None or ch == 'a' or ordv is None or ordv != char_last + 1:
                        list_id_counter += 1
                        char_list_id = list_id_counter
                    char_last = ordv if ordv is not None else char_last + 1
                    manual_list_info = {'type': 'numbered',
                                        'number_type': 'NUMBER_TYPE_CHAR_SMALL_PARANTHESE',
                                        'level': 1, 'list_id': char_list_id, 'left_indent': 25.0}
                    strip_count = m_chr.end()

            temiz = []
            kalan = strip_count
            for r in kosular:
                t = r['metin']
                if kalan:
                    al = min(kalan, len(t))
                    t = t[al:]
                    kalan -= al
                # Çok boşluklu (eski) hizalama sekmeye döner; Word bunu &nbsp; dizisiyle verir.
                t = re.sub('[  ]{2,}', '\t', t.translate(_TIPOGRAFI))
                if t:
                    temiz.append(dict(r, metin=t))

            pg = {
                'kosular': temiz,
                'hizala': _hizalama(bf),
                'solGirinti': round(bf.leftMargin() * PX_PT, 2),
                'sagGirinti': round(bf.rightMargin() * PX_PT, 2),
            }
            girinti = bf.textIndent() * PX_PT
            if girinti > 0:
                pg['ilkSatir'] = round(girinti, 2)
            elif girinti < 0:
                pg['asiliGirinti'] = round(-girinti, 2)
            if bf.lineHeightType() == QTextBlockFormat.ProportionalHeight:
                artis = bf.lineHeight() / 100.0 - 1.0
                if artis >= 0.1:
                    pg['satirAraligi'] = round(artis, 2)

            liste = (list_info if satir_idx == 0 else None) or manual_list_info
            if liste:
                ek = {'ListLevel': str(liste['level']), 'ListId': str(liste['list_id']),
                      'LeftIndent': udf_motor.olcu(liste['left_indent'])}
                if liste['type'] == 'numbered':
                    nt = liste.get('number_type', 'NUMBER_TYPE_NUMBER_PARANTHESE')
                    ek.update({'NumberType': nt, 'SecListTypeLevel1': nt, 'Numbered': 'true'})
                else:
                    ek.update({'Bulleted': 'true', 'BulletType': 'BULLET_TYPE_ELLIPSE'})
                pg['ek_nitelik'] = ek
            paragraflar.append(pg)

        block = block.next()
    return paragraflar


def udf_uret(paragraflar, sayfa=None):
    """Paragraf sözlüklerinden UDF baytları (Adalex `udf_motor.belge_uret` ile aynı yol).

    `belge_uret`in üretim kipini adım adım çağırır; tek fark, her paragrafın `ek_nitelik`
    sözlüğünün (UDF liste öznitelikleri) offset döşemesinden SONRA paragrafa eklenmesidir -
    liste öznitelikleri metne dokunmaz, `dogrula` kapısı aynen çalışır."""
    M = udf_motor
    kolon = M.etiket_kolonu(s for d in paragraflar for s in M._dugum_metni(d).split('\n'))
    govde = [dict(d, etiket_kolonu=kolon) for d in M.kunye_duzenle(paragraflar, kolon)]
    kayit = []
    dugumler = M._dugum_agaci(govde, kayit)
    cdata, _ = M._duzen_yaz(kayit, ozgun_metin=None)
    for p, pg in kayit:
        p.nitelikler.update(pg.get('ek_nitelik') or {})
    belge = M.UdfBelge(metin=cdata, dugumler=dugumler)
    if sayfa:
        belge.properties_xml = M._properties_uret(sayfa)
    return M._udf_seri(belge)


class PasteAwareTextEdit(QTextEdit):
    """Yapistirilan HTML'i Qt import etmeden once fix_pasted_html ile onaran QTextEdit."""

    def insertFromMimeData(self, source):
        if source.hasHtml():
            self.insertHtml(fix_pasted_html(source.html()))
        else:
            super().insertFromMimeData(source)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"AdaLex UDF Dönüştürücü v{VERSION}")
        self.setGeometry(100, 100, 900, 650)

        # Uygulama ikonu ayarla
        logo_icon_path = resource_path('icons/solo-logo-32.png')
        if os.path.exists(logo_icon_path):
            self.setWindowIcon(QIcon(logo_icon_path))

        self.init_ui()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Üst header bar
        header = QWidget()
        header.setFixedHeight(56)
        header.setStyleSheet("""
            QWidget {
                background-color: #ffffff;
                border-bottom: 1px solid #d1d9e6;
            }
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 0, 16, 0)

        # Logo
        logo_path = resource_path('logo.png')
        if os.path.exists(logo_path):
            logo_label = QLabel()
            pixmap = QPixmap(logo_path)
            scaled_pixmap = pixmap.scaled(160, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo_label.setPixmap(scaled_pixmap)
            header_layout.addWidget(logo_label)

        header_layout.addStretch()

        # Versiyon bilgisi
        version_label = QLabel(f"v{VERSION}")
        version_label.setStyleSheet("color: #95a5a6; font-size: 11px;")
        header_layout.addWidget(version_label)

        main_layout.addWidget(header)

        # Tab widget
        self.tab_widget = QTabWidget()
        self.tab_widget.setDocumentMode(True)
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane {
                border: none;
                background-color: #f5f7fa;
            }
            QTabBar {
                background-color: #edf0f5;
            }
            QTabBar::tab {
                background-color: #edf0f5;
                color: #5a6a7a;
                padding: 12px 28px;
                font-size: 13px;
                font-weight: bold;
                border: none;
                border-bottom: 3px solid transparent;
                min-width: 160px;
            }
            QTabBar::tab:selected {
                background-color: #f5f7fa;
                color: #1a2332;
                border-bottom: 3px solid #3498db;
            }
            QTabBar::tab:hover:!selected {
                background-color: #e4e8ee;
                color: #2c3e50;
            }
        """)

        # Metin dönüştürücü sayfası
        self.text_converter = RichTextToUDFConverter()
        self.tab_widget.addTab(self.text_converter, "Metin \u2192 UDF")

        # Şablon düzenleyici sayfası
        self.template_editor = TemplateEditorWidget()
        self.tab_widget.addTab(self.template_editor, "Şablon Düzenleyici")

        main_layout.addWidget(self.tab_widget)
        central_widget.setLayout(main_layout)


class RichTextToUDFConverter(QWidget):
    DEFAULT_PAGE_PROPERTIES = {
        'mediaSizeName': '1',
        'leftMargin': '56.69291305541992',
        'rightMargin': '56.69291305541992',
        'topMargin': '56.69291305541992',
        'bottomMargin': '56.69291305541992',
        'paperOrientation': '1',
        'headerFOffset': '20.0',
        'footerFOffset': '20.0'
    }

    def __init__(self):
        super().__init__()
        self.page_properties = self.load_template_properties()
        self.init_ui()

    def load_template_properties(self):
        """Sablon dosyasindan sayfa ozelliklerini yukle"""
        template_path = resource_path(os.path.join('sablonlar', 'content.xml'))
        try:
            if os.path.exists(template_path):
                tree = ET.parse(template_path)
                root = tree.getroot()
                props = root.find('properties')
                if props is not None:
                    page_format = props.find('pageFormat')
                    if page_format is not None:
                        return dict(page_format.attrib)
        except Exception:
            pass
        return dict(self.DEFAULT_PAGE_PROPERTIES)
    
    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(12)

        # Başlık ve açıklama
        title_label = QLabel("Biçimlendirilmiş Metin Dönüştürücüsü")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #1a2332;")
        layout.addWidget(title_label)

        desc_label = QLabel("Word, Google Docs veya diğer kaynaklardan kopyaladığınız metinleri UDF formatına dönüştürün.")
        desc_label.setStyleSheet("color: #6b7a8d; font-size: 12px; margin-bottom: 4px;")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        # Metin giriş alanı
        input_label = QLabel("Metninizi buraya yapıştırın:")
        input_label.setStyleSheet("font-weight: 600; font-size: 13px; color: #2c3e50;")
        layout.addWidget(input_label)

        self.input_text = PasteAwareTextEdit()
        self.input_text.setPlaceholderText("Word, Google Docs veya başka bir kaynaktan kopyaladığınız biçimlendirilmiş metni buraya yapıştırın...")
        self.input_text.setMinimumHeight(280)
        self.input_text.setStyleSheet("""
            QTextEdit {
                border: 1px solid #d1d9e6;
                border-radius: 8px;
                padding: 12px;
                font-size: 12px;
                background-color: #ffffff;
            }
            QTextEdit:focus {
                border: 2px solid #3498db;
            }
        """)
        layout.addWidget(self.input_text)

        # Alt butonlar
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)
        button_layout.addStretch()

        self.save_button = QPushButton("UDF Olarak Kaydet")
        self.save_button.clicked.connect(self.convert_and_save)
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                font-weight: bold;
                padding: 10px 32px;
                font-size: 13px;
                border-radius: 8px;
                border: none;
            }
            QPushButton:hover {
                background-color: #219a52;
            }
            QPushButton:pressed {
                background-color: #1e8449;
            }
        """)

        self.clear_button = QPushButton("Temizle")
        self.clear_button.clicked.connect(self.clear_all)
        self.clear_button.setCursor(Qt.PointingHandCursor)
        self.clear_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #e74c3c;
                font-weight: bold;
                padding: 10px 24px;
                font-size: 13px;
                border-radius: 8px;
                border: 1px solid #e74c3c;
            }
            QPushButton:hover {
                background-color: #fdf0ef;
            }
            QPushButton:pressed {
                background-color: #f9e0de;
            }
        """)

        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.clear_button)
        button_layout.addStretch()

        layout.addLayout(button_layout)

        # Durum çubuğu
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #27ae60; font-size: 12px;")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        self.setLayout(layout)

        # Üretilen UDF baytları (kaydet diyaloğuna kadar)
        self.generated_udf = b""
    
    def convert_and_save(self):
        """Dönüştür ve hemen kaydet - tek adımda"""
        try:
            paragraflar = qt_paragraflari(self.input_text.document())
            if not any(_pg_metni(pg).strip() for pg in paragraflar):
                QMessageBox.warning(self, "Uyarı", "Lütfen dönüştürmek istediğiniz metni yapıştırın!")
                return
            self.generated_udf = udf_uret(paragraflar, self.page_properties)
            self.save_as_udf()
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"İşlem hatası: {str(e)}")

    def save_as_udf(self):
        if not self.generated_udf:
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "UDF Dosyası Kaydet",
            "document.udf",
            "UDF Files (*.udf);;All Files (*)"
        )
        if not file_path:
            return
        try:
            with open(file_path, 'wb') as f:
                f.write(self.generated_udf)
            self.status_label.setText("✅ UDF dosyası başarıyla oluşturuldu!")
            self.status_label.setStyleSheet("color: #27ae60; font-weight: bold;")
        except Exception as e:
            self.status_label.setText("❌ UDF dosyası oluşturulamadı!")
            self.status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
            QMessageBox.critical(self, "Hata", f"Dosya kaydetme hatası: {str(e)}")

    def clear_all(self):
        self.input_text.clear()
        self.generated_udf = b""
        self.status_label.setText("")


def main():
    app = QApplication(sys.argv)
    
    # Uygulama stilini ayarla
    app.setStyleSheet("""
        * {
            font-family: 'Segoe UI', Arial, sans-serif;
        }
        QMainWindow {
            background-color: #f5f7fa;
        }
        QTextEdit, QPlainTextEdit {
            border: 1px solid #d1d9e6;
            border-radius: 6px;
            padding: 8px;
            font-size: 12px;
            background-color: #ffffff;
            selection-background-color: #3498db;
        }
        QTextEdit:focus, QPlainTextEdit:focus {
            border: 2px solid #3498db;
        }
        QLineEdit {
            border: 1px solid #d1d9e6;
            border-radius: 6px;
            padding: 8px 10px;
            font-size: 12px;
            background-color: #ffffff;
            min-height: 20px;
        }
        QLineEdit:focus {
            border: 2px solid #3498db;
        }
        QPushButton {
            background-color: #f0f0f0;
            border: 1px solid #d1d9e6;
            border-radius: 6px;
            padding: 8px 16px;
            font-size: 12px;
            min-width: 100px;
            min-height: 32px;
        }
        QPushButton:hover {
            background-color: #e4e8ee;
        }
        QPushButton:pressed {
            background-color: #d0d5dd;
        }
        QPushButton:disabled {
            background-color: #f5f5f5;
            color: #aaaaaa;
        }
        QScrollArea {
            border: none;
            background-color: transparent;
        }
        QGroupBox {
            font-weight: bold;
            border: 1px solid #d1d9e6;
            border-radius: 8px;
            margin-top: 14px;
            padding: 16px 12px 12px 12px;
            background-color: #ffffff;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 2px 10px;
            background-color: #f5f7fa;
            color: #2c3e50;
            border-radius: 4px;
        }
        QLabel {
            color: #2c3e50;
        }
    """)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()