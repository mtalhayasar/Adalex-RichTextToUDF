# -*- coding: utf-8 -*-
import sys
import zipfile
import tempfile
import os
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                               QTextEdit, QPushButton, QLabel, QFileDialog, 
                               QMessageBox, QSplitter)
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextDocument, QTextCursor, QFont, QTextListFormat, QPixmap, QIcon
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape


def resource_path(relative_path):
    """PyInstaller ile paketlendiğinde dosya yollarını düzgün bulma fonksiyonu"""
    try:
        # PyInstaller ile çalışırken _MEIPASS kullanılır
        base_path = sys._MEIPASS
    except Exception:
        # Normal çalıştırma sırasında mevcut dizini kullan
        base_path = os.path.abspath(".")
    
    return os.path.join(base_path, relative_path)


class RichTextToUDFConverter(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AdaLex UDF Dönüştürücüsü")
        self.setGeometry(100, 100, 900, 500)
        
        # Uygulama ikonu ayarla
        logo_icon_path = resource_path('icons/solo-logo-32.png')
        if os.path.exists(logo_icon_path):
            self.setWindowIcon(QIcon(logo_icon_path))
        
        self.init_ui()
    
    def init_ui(self):
        layout = QVBoxLayout()
        
        # Üst kısım - Logo ve başlık üst-alt
        header_layout = QVBoxLayout()
        header_layout.setAlignment(Qt.AlignLeft)
        
        # Logo ekle - daha büyük boyutta
        logo_path = resource_path('logo.png')
        if os.path.exists(logo_path):
            logo_label = QLabel()
            pixmap = QPixmap(logo_path)
            # Logo boyutunu büyüt
            scaled_pixmap = pixmap.scaled(250, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo_label.setPixmap(scaled_pixmap)
            logo_label.setAlignment(Qt.AlignLeft)
            header_layout.addWidget(logo_label)
        
        # Ana başlık - logonun altında
        title_label = QLabel("Biçimlendirilmiş Metin Dönüştürücüsü")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #2c3e50; margin-top: 5px;")
        header_layout.addWidget(title_label)
        
        layout.addLayout(header_layout)
        
        # Açıklama metni
        desc_label = QLabel("Word, Google Docs veya diğer kaynaklardan kopyaladığınız metinleri UDF formatına dönüştürün")
        desc_label.setStyleSheet("color: #7f8c8d; margin: 5px 0 10px 0;")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)
        
        # Metin giriş alanı - Tek alan olarak
        input_label = QLabel("📝 Metninizi Buraya Yapıştırın:")
        input_label.setStyleSheet("font-weight: bold; font-size: 12px; margin-top: 10px;")
        layout.addWidget(input_label)
        
        self.input_text = QTextEdit()
        self.input_text.setPlaceholderText("Word, Google Docs veya başka bir kaynaktan kopyaladığınız biçimlendirilmiş metni buraya yapıştırın...")
        self.input_text.setMinimumHeight(250)
        layout.addWidget(self.input_text)
        
        # XML önizleme için gizli alan (arka planda kullanılacak)
        self.output_text = QTextEdit()
        self.output_text.setVisible(False)  # Kullanıcıya gösterme
        
        # Alt butonlar
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        # Tek buton: Dönüştür ve Kaydet
        self.save_button = QPushButton("💾 UDF Olarak Kaydet")
        self.save_button.clicked.connect(self.convert_and_save)
        self.save_button.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                font-weight: bold;
                padding: 10px 30px;
                font-size: 14px;
                border-radius: 5px;
                border: none;
            }
            QPushButton:hover {
                background-color: #229954;
            }
            QPushButton:pressed {
                background-color: #1e8449;
            }
        """)
        
        self.clear_button = QPushButton("🗑️ Temizle")
        self.clear_button.clicked.connect(self.clear_all)
        self.clear_button.setStyleSheet("""
            QPushButton {
                background-color: #e74c3c;
                color: white;
                padding: 10px 20px;
                font-size: 13px;
                border-radius: 5px;
                border: none;
            }
            QPushButton:hover {
                background-color: #c0392b;
            }
        """)
        
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.clear_button)
        button_layout.addStretch()
        
        layout.addLayout(button_layout)
        
        # Durum çubuğu
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #27ae60; margin: 10px; font-size: 12px;")
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
        
        while block.isValid():
            block_format = block.blockFormat()
            text_list = block.textList()
            
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
                paragraph_info = {
                    'alignment': block_format.alignment(),
                    'content_elements': [],
                    'list_info': list_info if line_idx == 0 else None  # Liste bilgisi sadece ilk satıra
                }
                
                # Bu satır için fragment'ları hesapla
                line_start = sum(len(lines[i]) + 1 for i in range(line_idx))  # +1 separator için
                if line_idx == 0:
                    line_start = 0
                line_end = line_start + len(line_text)
                
                # Fragment'ları bu satıra dağıt
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
        # XML root element
        root = ET.Element("template", format_id="1.8")
        
        # Content element (CDATA)
        content_elem = ET.SubElement(root, "content")
        content_elem.text = text_content
        
        # Properties element (sayfa formatı)
        properties = ET.SubElement(root, "properties")
        properties.set("mediaSizeName", "1")
        properties.set("leftMargin", "42.525000000000006")
        properties.set("rightMargin", "42.525000000000006")
        properties.set("topMargin", "42.525000000000006")
        properties.set("bottomMargin", "42.52500000000006")
        properties.set("paperOrientation", "1")
        properties.set("headerFOffset", "20.0")
        properties.set("footerFOffset", "20.0")
        
        # Elements section
        elements = ET.SubElement(root, "elements", resolver="hvl-default")
        
        # Her paragraph için element oluştur
        for para_info in paragraphs:
            paragraph_elem = ET.SubElement(elements, "paragraph")
            
            # Alignment ayarla - ornek.xml'deki gibi
            alignment = para_info.get('alignment', Qt.AlignLeft)
            if alignment == Qt.AlignCenter:
                paragraph_elem.set("Alignment", "1")
            elif alignment == Qt.AlignRight:
                paragraph_elem.set("Alignment", "2")
            elif alignment == Qt.AlignJustify:
                paragraph_elem.set("Alignment", "3")
            else:
                # Varsayılan olarak justify (3) yap
                paragraph_elem.set("Alignment", "3")
            
            # Content elementları ekle
            for content_info in para_info['content_elements']:
                content_elem = ET.SubElement(paragraph_elem, "content")
                content_elem.set("startOffset", str(content_info['startOffset']))
                content_elem.set("length", str(content_info['length']))
                
                if content_info['bold']:
                    content_elem.set("bold", "true")
                if content_info['italic']:
                    content_elem.set("italic", "true")
                if content_info['underline']:
                    content_elem.set("underline", "true")
        
        # Styles section
        styles = ET.SubElement(root, "styles")
        
        # Default style
        default_style = ET.SubElement(styles, "style", name="default")
        default_style.set("description", "Geçerli")
        default_style.set("family", "Dialog")
        default_style.set("size", "12")
        default_style.set("bold", "false")
        default_style.set("italic", "false")
        default_style.set("foreground", "-13421773")
        default_style.set("FONT_ATTRIBUTE_KEY", "javax.swing.plaf.FontUIResource[family=Dialog,name=Dialog,style=plain,size=12]")
        
        # HVL default style
        hvl_style = ET.SubElement(styles, "style", name="hvl-default")
        hvl_style.set("family", "Times New Roman")
        hvl_style.set("size", "12")
        hvl_style.set("description", "Gövde")
        
        # XML'i ornek.xml formatına uygun şekilde manuel oluştur
        xml_lines = []
        xml_lines.append('<?xml version="1.0" encoding="UTF-8" ?> ')
        xml_lines.append('')
        xml_lines.append('<template format_id="1.8" >')
        
        # CDATA content - satır satır koru
        xml_lines.append(f'<content><![CDATA[{text_content}]]></content>')
        
        # Properties - pageFormat wrapper ile
        props_line = '<properties><pageFormat'
        props_line += ' mediaSizeName="1"'
        props_line += ' leftMargin="42.525000000000006"' 
        props_line += ' rightMargin="42.525000000000006"'
        props_line += ' topMargin="42.525000000000006"'
        props_line += ' bottomMargin="42.52500000000006"'
        props_line += ' paperOrientation="1"'
        props_line += ' headerFOffset="20.0"'
        props_line += ' footerFOffset="20.0" /></properties>'
        xml_lines.append(props_line)
        
        # Elements - ornek.xml formatında kompakt
        xml_lines.append('<elements resolver="hvl-default" >')
        
        # Her paragraph'ı tek satırda oluştur
        for para_info in paragraphs:
            para_line = '<paragraph'
            
            # Liste bilgilerini ekle
            list_info = para_info.get('list_info')
            if list_info:
                if list_info['type'] == 'numbered':
                    # Numaralı liste
                    para_line += f' NumberType="NUMBER_TYPE_NUMBER_TRE"'
                    para_line += f' SecListTypeLevel1="NUMBER_TYPE_NUMBER_TRE"'
                    para_line += f' ListLevel="{list_info["level"]}"'
                    para_line += f' Numbered="true"'
                    para_line += f' ListId="{list_info["list_id"]}"'
                    para_line += f' LeftIndent="{list_info["left_indent"]}"'
                    
                elif list_info['type'] == 'bulleted':
                    # Madde işaretli liste
                    para_line += f' Bulleted="true"'
                    para_line += f' BulletType="BULLET_TYPE_ELLIPSE"'
                    para_line += f' ListLevel="{list_info["level"]}"'
                    para_line += f' ListId="{list_info["list_id"]}"'
                    para_line += f' LeftIndent="{list_info["left_indent"]}"'
            
            # Alignment ekle - sadece center, right, justify için
            alignment = para_info.get('alignment', Qt.AlignLeft)
            if alignment == Qt.AlignCenter:
                para_line += ' Alignment="1"'
            elif alignment == Qt.AlignRight:
                para_line += ' Alignment="2"'
            elif alignment == Qt.AlignJustify:
                para_line += ' Alignment="3"'
            # Left alignment için Alignment attribute ekleme
            
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
        QTextEdit {
            border: 1px solid #cccccc;
            border-radius: 4px;
            padding: 5px;
            font-family: 'Segoe UI', Arial, sans-serif;
            font-size: 11px;
        }
        QPushButton {
            background-color: #f0f0f0;
            border: 1px solid #cccccc;
            border-radius: 4px;
            min-width: 100px;
            min-height: 30px;
        }
        QPushButton:hover {
            background-color: #e0e0e0;
        }
        QPushButton:pressed {
            background-color: #d0d0d0;
        }
        QPushButton:disabled {
            background-color: #f5f5f5;
            color: #999999;
        }
    """)
    
    converter = RichTextToUDFConverter()
    converter.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()