# -*- coding: utf-8 -*-
VERSION = "2.1.0"
import sys
import re
import zipfile
import tempfile
import os
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                               QTextEdit, QPushButton, QLabel, QFileDialog,
                               QMessageBox, QMainWindow, QTabWidget)
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextDocument, QTextCursor, QFont, QTextListFormat, QPixmap, QIcon
import xml.etree.ElementTree as ET
from sablon_editor import TemplateEditorWidget


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
        version_label = QLabel("v2.1.0")
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

    # UDF sekme duraklari (sablon/edited.udf ile ayni)
    DEFAULT_TAB_SET = "42.519684:0:0,85.03937:0:0,127.55905:0:0"

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

        # XML önizleme için gizli alan
        self.output_text = QTextEdit()
        self.output_text.setVisible(False)

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

        # XML içeriğini saklamak için
        self.generated_xml = ""
    
    def convert_and_save(self):
        """Dönüştür ve hemen kaydet - tek adımda"""
        try:
            # Önce dönüştürme işlemini yap
            if not self.convert_to_udf_xml():
                return
            
            # Hemen kaydetme dialogunu aç
            self.save_as_udf()
            
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"İşlem hatası: {str(e)}")
    
    def convert_to_udf_xml(self):
        try:
            input_html = self.input_text.toHtml()
            
            # QTextDocument ile rich text'i parse et
            doc = QTextDocument()
            doc.setHtml(input_html)
            
            # Metni ve biçimlendirme bilgilerini çıkar
            text_content, formatting_elements = self.extract_text_and_formatting(doc)
            
            if not text_content.strip():
                QMessageBox.warning(self, "Uyarı", "Lütfen dönüştürmek istediğiniz metni yapıştırın!")
                return False
            
            # UDF XML formatını oluştur
            xml_content = self.create_udf_xml(text_content, formatting_elements)
            
            self.output_text.setPlainText(xml_content)
            self.generated_xml = xml_content
            return True  # Başarılı dönüşüm
            
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"Dönüştürme hatası: {str(e)}")
            return False
    
    def extract_text_and_formatting(self, doc):
        text_content = ""
        paragraphs = []
        current_offset = 0
        
        # Blokları iterate et
        block = doc.begin()
        list_id_counter = 1
        current_lists = {}  # Format -> ListId mapping
        # Manuel "1)" / "a)" liste otomasyonu icin gruplama durumu
        num_list_id = None
        num_last = 0
        char_list_id = None
        char_last = 0

        while block.isValid():
            block_format = block.blockFormat()
            text_list = block.textList()

            # Paragraf girintisi (round-trip'te punto olarak korunur)
            left_margin = round(block_format.leftMargin())
            right_margin = round(block_format.rightMargin())
            
            # Liste bilgilerini hazırla (varsa)
            list_info = None
            if text_list:
                list_format = text_list.format()
                list_style = list_format.style()
                list_indent = list_format.indent()
                
                # Liste türünü belirle
                if list_style == QTextListFormat.ListDecimal:
                    # Numaralı liste
                    list_key = f"numbered_{list_indent}"
                    if list_key not in current_lists:
                        current_lists[list_key] = list_id_counter
                        list_id_counter += 1
                    
                    list_info = {
                        'type': 'numbered',
                        'level': list_indent,
                        'list_id': current_lists[list_key],
                        'left_indent': 25.0 * list_indent
                    }
                    
                elif list_style in [QTextListFormat.ListDisc, QTextListFormat.ListCircle, QTextListFormat.ListSquare]:
                    # Madde işaretli liste
                    list_key = f"bulleted_{list_indent}"
                    if list_key not in current_lists:
                        current_lists[list_key] = list_id_counter
                        list_id_counter += 1
                    
                    list_info = {
                        'type': 'bulleted',
                        'level': list_indent,
                        'list_id': current_lists[list_key],
                        'left_indent': 25.0 * list_indent
                    }
            
            # Block içindeki tüm fragment'ları topla
            block_fragments = []
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                if fragment.isValid():
                    fragment_text = fragment.text()
                    char_format = fragment.charFormat()
                    
                    block_fragments.append({
                        'text': fragment_text,
                        'bold': char_format.font().bold(),
                        'italic': char_format.font().italic(),
                        'underline': char_format.font().underline()
                    })
                
                it += 1
            
            # Block'un tam metnini oluştur
            block_full_text = ''.join(f['text'] for f in block_fragments)
            
            # Block metnini satırlara böl (Line Separator'lara göre)
            # \u2028 = Line Separator, \u2029 = Paragraph Separator
            # Her ikisi de UDF'de yeni paragraf demek
            lines = []
            current_line = ""
            for char in block_full_text:
                if char in ['\u2028', '\u2029']:
                    lines.append(current_line)
                    current_line = ""
                else:
                    current_line += char
            if current_line or not lines:  # Son satırı veya boş block'u ekle
                lines.append(current_line)
            
            # Her satır için ayrı paragraf oluştur
            for line_idx, line_text in enumerate(lines):
                # Manuel "1)" / "a)" liste tespiti (yalnizca Qt listesi yoksa)
                manual_list_info = None
                strip_count = 0
                if not text_list:
                    m_num = re.match(r'(\d+)\)\s', line_text)
                    m_chr = re.match(r'([a-zçğıöşü])\)\s', line_text)
                    if m_num:
                        n = int(m_num.group(1))
                        if num_list_id is None or n == 1 or n != num_last + 1:
                            list_id_counter += 1
                            num_list_id = list_id_counter
                        num_last = n
                        # Numara gelince harf alt-listesi sifirlanir
                        char_list_id = None
                        char_last = 0
                        manual_list_info = {
                            'type': 'numbered',
                            'number_type': 'NUMBER_TYPE_NUMBER_PARANTHESE',
                            'level': 1,
                            'list_id': num_list_id,
                            'left_indent': 25.0,
                        }
                        strip_count = m_num.end()
                    elif m_chr:
                        ch = m_chr.group(1)
                        ordv = (ord(ch) - ord('a') + 1) if 'a' <= ch <= 'z' else None
                        if char_list_id is None or ch == 'a' or ordv is None or ordv != char_last + 1:
                            list_id_counter += 1
                            char_list_id = list_id_counter
                        char_last = ordv if ordv is not None else char_last + 1
                        manual_list_info = {
                            'type': 'numbered',
                            'number_type': 'NUMBER_TYPE_CHAR_SMALL_PARANTHESE',
                            'level': 1,
                            'list_id': char_list_id,
                            'left_indent': 25.0,
                        }
                        strip_count = m_chr.end()

                paragraph_info = {
                    'alignment': block_format.alignment(),
                    'left_indent': left_margin,
                    'right_indent': right_margin,
                    'content_elements': [],
                    # Qt listesi (yalnizca ilk satir) veya manuel tespit edilen liste
                    'list_info': (list_info if line_idx == 0 else None) or manual_list_info
                }

                # Bu satır için fragment'ları hesapla
                line_start = sum(len(lines[i]) + 1 for i in range(line_idx))  # +1 separator için
                if line_idx == 0:
                    line_start = 0
                line_end = line_start + len(line_text)

                # Fragment'ları bu satıra dağıt
                remaining_strip = strip_count  # ayiklanacak liste oneki ("1) ") karakter sayisi
                fragment_pos = 0
                for frag in block_fragments:
                    frag_text = frag['text']
                    frag_len = len(frag_text)
                    frag_end = fragment_pos + frag_len
                    
                    # Bu fragment bu satırla kesişiyor mu?
                    if fragment_pos < line_end and frag_end > line_start:
                        # Kesişen kısmı al
                        start_in_frag = max(0, line_start - fragment_pos)
                        end_in_frag = min(frag_len, line_end - fragment_pos)
                        
                        if start_in_frag < end_in_frag:
                            text_piece = frag_text[start_in_frag:end_in_frag]
                            
                            # Line separator'ları temizle
                            text_piece = text_piece.replace('\u2028', '').replace('\u2029', '')
                            
                            # Liste onekini ("1) " / "a) ") metinden ayikla (offset/length senkron)
                            if remaining_strip > 0 and text_piece:
                                take = min(remaining_strip, len(text_piece))
                                text_piece = text_piece[take:]
                                remaining_strip -= take

                            # Cok bosluklu (eski sekme) hizalamayi gercek sekmeye cevir
                            text_piece = re.sub(r' {2,}', '\t', text_piece)

                            if text_piece:  # Boş değilse ekle
                                text_content += text_piece

                                paragraph_info['content_elements'].append({
                                    'startOffset': current_offset,
                                    'length': len(text_piece),
                                    'bold': frag['bold'],
                                    'italic': frag['italic'],
                                    'underline': frag['underline']
                                })
                                current_offset += len(text_piece)
                    
                    fragment_pos += frag_len
                
                # Satır sonuna newline ekle
                # Her satırın sonuna newline ekliyoruz çünkü XML'de her satır ayrı paragraf
                text_content += "\n"
                if paragraph_info['content_elements']:
                    paragraph_info['content_elements'][-1]['length'] += 1
                else:
                    # Boş satır için sadece newline
                    paragraph_info['content_elements'].append({
                        'startOffset': current_offset,
                        'length': 1,
                        'bold': False,
                        'italic': False,
                        'underline': False
                    })
                current_offset += 1
                
                paragraphs.append(paragraph_info)
            
            block = block.next()
        
        # Text içindeki kalan özel karakterleri temizle (varsa)
        text_content = text_content.replace('\u000B', '\n')  # Vertical Tab -> Normal newline
        text_content = text_content.replace('\u000C', '\n')  # Form Feed -> Normal newline

        # Tipografik karakterleri standart karakterlere dönüştür
        text_content = text_content.replace('\u2013', '-')   # En dash (–) -> Normal tire
        text_content = text_content.replace('\u201C', '"')   # Sol çift tırnak (") -> Normal tırnak
        text_content = text_content.replace('\u201D', '"')   # Sağ çift tırnak (") -> Normal tırnak
        text_content = text_content.replace('\u2019', "'")   # Sağ tek tırnak (') -> Normal kesme işareti
        text_content = text_content.replace('\u2018', "'")   # Sol tek tırnak (') -> Normal kesme işareti
        
        # Offset'leri yeniden hesapla
        paragraphs = self.recalculate_paragraph_offsets(text_content, paragraphs)
        
        # Son boş paragraph için
        # text_content zaten her satır sonunda \n içeriyor
        final_offset = len(text_content) - 1
        empty_paragraph = {
            'alignment': Qt.AlignLeft,
            'content_elements': [{
                'startOffset': final_offset,
                'length': 1,
                'bold': False,
                'italic': False,
                'underline': False
            }]
        }
        paragraphs.append(empty_paragraph)
        
        return text_content, paragraphs
    

    def recalculate_paragraph_offsets(self, text_content, paragraphs):
        """Unicode karakter dönüşümü sonrası offset/length değerlerini düzelt"""
        # Artık paragraflar doğru sayıda olduğu için basit offset hesaplaması yeterli
        current_offset = 0
        
        for para_info in paragraphs:
            for content_elem in para_info['content_elements']:
                content_elem['startOffset'] = current_offset
                current_offset += content_elem['length']
        
        return paragraphs
    
    def create_udf_xml(self, text_content, paragraphs):
        xml_lines = []
        xml_lines.append('<?xml version="1.0" encoding="UTF-8" ?> ')
        xml_lines.append('')
        xml_lines.append('<template format_id="1.8" >')
        
        # CDATA content - satır satır koru
        xml_lines.append(f'<content><![CDATA[{text_content}]]></content>')
        
        # Properties - sablondan okunan degerler
        props_line = '<properties><pageFormat'
        for key, value in self.page_properties.items():
            props_line += f' {key}="{value}"'
        props_line += ' /></properties>'
        xml_lines.append(props_line)
        
        # Elements - ornek.xml formatında kompakt
        xml_lines.append('<elements resolver="hvl-default" >')
        
        # Her paragraph'ı tek satırda oluştur
        for para_info in paragraphs:
            para_line = '<paragraph'

            # Sekme duraklari - tum paragraflarda (offset etkilemez)
            para_line += f' TabSet="{self.DEFAULT_TAB_SET}"'

            # Liste bilgilerini ekle
            list_info = para_info.get('list_info')
            left_indent_val = para_info.get('left_indent', 0)
            if list_info:
                if list_info['type'] == 'numbered':
                    # Numaralı/harfli liste - numara tipi liste bilgisinden gelir
                    num_type = list_info.get('number_type', 'NUMBER_TYPE_NUMBER_PARANTHESE')
                    para_line += f' NumberType="{num_type}"'
                    para_line += f' SecListTypeLevel1="{num_type}"'
                    para_line += f' ListLevel="{list_info["level"]}"'
                    para_line += f' Numbered="true"'
                    para_line += f' ListId="{list_info["list_id"]}"'
                    left_indent_val = list_info['left_indent']

                elif list_info['type'] == 'bulleted':
                    # Madde işaretli liste
                    para_line += f' Bulleted="true"'
                    para_line += f' BulletType="BULLET_TYPE_ELLIPSE"'
                    para_line += f' ListLevel="{list_info["level"]}"'
                    para_line += f' ListId="{list_info["list_id"]}"'
                    left_indent_val = list_info['left_indent']

            # Girinti - liste varsa listeden, yoksa blok girintisinden (tek LeftIndent)
            if left_indent_val:
                para_line += f' LeftIndent="{left_indent_val}"'
            right_indent_val = para_info.get('right_indent', 0)
            if right_indent_val:
                para_line += f' RightIndent="{right_indent_val}"'

            # Alignment - birlesik Qt bayraklarini yatay maske ile coz
            h = int(para_info.get('alignment', Qt.AlignLeft)) & int(Qt.AlignHorizontal_Mask)
            if h & int(Qt.AlignHCenter):
                para_line += ' Alignment="1"'
            elif h & int(Qt.AlignRight):
                para_line += ' Alignment="2"'
            elif h & int(Qt.AlignJustify):
                para_line += ' Alignment="3"'
            # Sol (varsayilan) hizalama icin oznitelik eklenmez

            para_line += '>'
            
            # Content elementları aynı satıra ekle
            for content_info in para_info['content_elements']:
                content_part = '<content'
                
                # Attributes ekle
                if content_info['bold']:
                    content_part += ' bold="true"'
                if content_info['italic']:
                    content_part += ' italic="true"'
                if content_info['underline']:
                    content_part += ' underline="true"'
                
                content_part += f' startOffset="{content_info["startOffset"]}"'
                content_part += f' length="{content_info["length"]}"'
                content_part += ' />'
                
                para_line += content_part
            
            para_line += '</paragraph>'
            xml_lines.append(para_line)
        
        xml_lines.append('</elements>')

        # Sekme uzunlugu tanimi (UDF editorunun sekme duraklarini tanimasi icin)
        xml_lines.append('<tabLength length="1.5" resolver="hvl-default" >')
        xml_lines.append('')
        xml_lines.append('</tabLength>')

        # Styles - compare1.xml formatında (foreground sonda)
        styles_line = '<styles>'
        styles_line += '<style name="default" description="Geçerli" family="Dialog" size="12" bold="false" italic="false" FONT_ATTRIBUTE_KEY="javax.swing.plaf.FontUIResource[family=Dialog,name=Dialog,style=plain,size=12]" foreground="-13421773" />'
        styles_line += '<style name="hvl-default" family="Times New Roman" size="12" description="Gövde" />'
        styles_line += '</styles>'
        xml_lines.append(styles_line)
        
        xml_lines.append('</template>')
        xml_lines.append('')
        
        return '\n'.join(xml_lines)
    
    def save_as_udf(self):
        if not self.generated_xml:
            return
        
        file_path, _ = QFileDialog.getSaveFileName(
            self, 
            "UDF Dosyası Kaydet", 
            "document.udf", 
            "UDF Files (*.udf);;All Files (*)"
        )
        
        if file_path:
            try:
                # Geçici bir dizin oluştur
                with tempfile.TemporaryDirectory() as temp_dir:
                    # content.xml dosyasını oluştur
                    xml_file_path = os.path.join(temp_dir, "content.xml")
                    
                    # XML dosyasını yazarken daha dikkatli ol
                    try:
                        with open(xml_file_path, 'w', encoding='utf-8', newline='') as f:
                            f.write(self.generated_xml)
                        print(f"XML dosyası oluşturuldu: {xml_file_path}")
                        
                        # Dosyanın gerçekten oluşturulduğunu kontrol et
                        if not os.path.exists(xml_file_path):
                            raise Exception("content.xml dosyası oluşturulamadı")
                        
                        file_size = os.path.getsize(xml_file_path)
                        print(f"XML dosya boyutu: {file_size} bytes")
                        
                    except Exception as xml_error:
                        raise Exception(f"XML dosyası yazma hatası: {str(xml_error)}")
                    
                    # ZIP dosyası oluştur ve .udf uzantısı ver
                    try:
                        with zipfile.ZipFile(file_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
                            zipf.write(xml_file_path, "content.xml")
                        print(f"ZIP dosyası oluşturuldu: {file_path}")
                        
                        # ZIP dosyasının gerçekten oluşturulduğunu kontrol et
                        if not os.path.exists(file_path):
                            raise Exception("UDF dosyası oluşturulamadı")
                            
                        zip_size = os.path.getsize(file_path)
                        print(f"UDF dosya boyutu: {zip_size} bytes")
                        
                    except Exception as zip_error:
                        raise Exception(f"ZIP oluşturma hatası: {str(zip_error)}")
                
                self.status_label.setText("✅ UDF dosyası başarıyla oluşturuldu!")
                self.status_label.setStyleSheet("color: #27ae60; font-weight: bold;")
                
            except Exception as e:
                self.status_label.setText("❌ UDF dosyası oluşturulamadı!")
                self.status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
                QMessageBox.critical(self, "Hata", f"Dosya kaydetme hatası: {str(e)}")
    
    def clear_all(self):
        self.input_text.clear()
        self.output_text.clear()
        self.generated_xml = ""
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