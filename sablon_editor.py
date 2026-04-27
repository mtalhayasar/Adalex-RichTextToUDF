# -*- coding: utf-8 -*-
import sys
import zipfile
import tempfile
import os
import shutil
from pathlib import Path
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
                               QLabel, QPushButton, QLineEdit, QPlainTextEdit,
                               QFileDialog, QMessageBox, QFormLayout, QGroupBox)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
import xml.etree.ElementTree as ET
import json
from datetime import datetime, timedelta


class FieldInfo:
    def __init__(self, field_element):
        self.field_name = field_element.get('fieldName', '')
        self.start_offset = int(field_element.get('startOffset', 0))
        self.length = int(field_element.get('length', 0))
        self.field_type = field_element.get('fieldType', '1')
        self.is_list = field_element.get('isList', 'false') == 'true'
        self.is_erasable = field_element.get('isErasable', 'false') == 'true'
        self.bold = field_element.get('bold', 'false') == 'true'
        self.group_name = None
        self.element = field_element
        
    def __repr__(self):
        return f"Field({self.field_name}, offset={self.start_offset}, len={self.length})"


class TemplateEditorWidget(QWidget):
    closed = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("UYAP Şablon Düzenleyici")
        self.setMinimumSize(600, 700)
        
        self.usf_path = None
        self.xml_tree = None
        self.content_cdata = ""
        self.fields = []
        self.field_inputs = []
        self.temp_dir = None
        self.field_config = {}
        
        self.load_field_config()
        self.init_ui()
    
    def load_field_config(self):
        """JSON yapılandırma dosyasını yükle"""
        config_path = 'field_config.json'
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                    self.field_config = config_data.get('field_mappings', {})
                    print(f"Field config loaded: {len(self.field_config)} mappings")
        except Exception as e:
            print(f"Field config yüklenemedi: {str(e)}")
            self.field_config = {}
    
    def get_field_display_info(self, field_name):
        """Alan adına göre görüntülenecek bilgileri al"""
        if field_name in self.field_config:
            config = self.field_config[field_name]
            return {
                'label': config.get('alan_adi', field_name),
                'placeholder': config.get('placeholder', 'Değer giriniz'),
                'field_type': config.get('field_type', 'text'),
                'handler': config.get('handler', None)
            }
        return {
            'label': field_name,
            'placeholder': 'Değer giriniz',
            'field_type': 'text',
            'handler': None
        }
    
    def get_special_field_value(self, handler_name):
        """Özel alan işleyicilerini çağır"""
        if handler_name == 'get_tarih_bugun':
            return datetime.now().strftime('%d/%m/%Y')
        elif handler_name == 'get_tarih_yarin':
            return (datetime.now() + timedelta(days=1)).strftime('%d/%m/%Y')
        elif handler_name == 'get_tarih_dun':
            return (datetime.now() - timedelta(days=1)).strftime('%d/%m/%Y')
        return ""
        
    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(10)
        
        # Başlık
        title = QLabel("📋 UYAP Şablon Düzenleyici")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #2c3e50; padding: 10px;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        
        # Açıklama
        desc = QLabel("USF şablon dosyasını seçin ve alanları doldurun")
        desc.setStyleSheet("color: #7f8c8d; padding: 5px;")
        desc.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc)
        
        # Dosya seçim butonu
        self.select_button = QPushButton("📂 USF Dosyası Seç")
        self.select_button.clicked.connect(self.select_usf_file)
        self.select_button.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                font-weight: bold;
                padding: 10px;
                font-size: 14px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
        """)
        layout.addWidget(self.select_button)
        
        # Dosya adı etiketi
        self.file_label = QLabel("")
        self.file_label.setStyleSheet("color: #27ae60; font-weight: bold; padding: 5px;")
        self.file_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.file_label)
        
        # Scroll area for fields
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: 1px solid #bdc3c7; border-radius: 5px; }")
        
        # Fields container
        self.fields_widget = QWidget()
        self.fields_layout = QVBoxLayout()
        self.fields_widget.setLayout(self.fields_layout)
        scroll.setWidget(self.fields_widget)
        
        layout.addWidget(scroll, 1)
        
        # Alt butonlar
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        # Kaydet butonu
        self.save_button = QPushButton("💾 UDF Olarak Kaydet")
        self.save_button.clicked.connect(self.save_as_udf)
        self.save_button.setEnabled(False)
        self.save_button.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                font-weight: bold;
                padding: 10px 30px;
                font-size: 14px;
                border-radius: 5px;
            }
            QPushButton:hover:enabled {
                background-color: #229954;
            }
            QPushButton:disabled {
                background-color: #95a5a6;
            }
        """)
        button_layout.addWidget(self.save_button)
        
        button_layout.addStretch()
        layout.addLayout(button_layout)
        
        # Durum etiketi
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("padding: 5px;")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)
        
        self.setLayout(layout)
        
    def select_usf_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "USF Şablon Dosyası Seç",
            "",
            "USF Files (*.usf);;All Files (*)"
        )
        
        if not file_path:
            return
            
        self.usf_path = file_path
        self.file_label.setText(f"📄 {os.path.basename(file_path)}")
        self.load_usf_file()
        
    def load_usf_file(self):
        try:
            # Geçici klasör oluştur
            if self.temp_dir:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
            self.temp_dir = tempfile.mkdtemp()
            
            # USF dosyasını aç (ZIP formatında)
            with zipfile.ZipFile(self.usf_path, 'r') as zip_file:
                # content.xml'i oku
                if 'content.xml' not in zip_file.namelist():
                    raise ValueError("content.xml bulunamadı!")
                    
                # Tüm dosyaları geçici klasöre çıkar
                zip_file.extractall(self.temp_dir)
                
                # content.xml'i parse et
                content_path = os.path.join(self.temp_dir, 'content.xml')
                self.parse_content_xml(content_path)
                
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"USF dosyası yüklenemedi:\n{str(e)}")
            self.status_label.setText("❌ Dosya yüklenemedi")
            self.status_label.setStyleSheet("color: #e74c3c;")
            
    def parse_content_xml(self, xml_path):
        try:
            # XML'i parse et
            tree = ET.parse(xml_path)
            self.xml_tree = tree
            root = tree.getroot()
            
            # CDATA içeriğini al
            content_elem = root.find('content')
            if content_elem is not None and content_elem.text:
                self.content_cdata = content_elem.text
            else:
                raise ValueError("content elementi bulunamadı!")
                
            # Field'ları topla
            self.fields = []
            elements = root.find('elements')
            
            if elements is not None:
                # Tüm paragraph'ları tara
                for paragraph in elements.findall('paragraph'):
                    # Paragraph'ın GroupName'ini al
                    group_name = paragraph.get('GroupName', None)
                    
                    # Field'ları bul
                    for field in paragraph.findall('field'):
                        field_info = FieldInfo(field)
                        field_info.group_name = group_name
                        self.fields.append(field_info)
                        
            # Field'ları offset'e göre sırala
            self.fields.sort(key=lambda f: f.start_offset)
            
            # GUI'yi oluştur
            self.create_field_inputs()
            self.save_button.setEnabled(True)
            
            self.status_label.setText(f"✅ {len(self.fields)} alan bulundu")
            self.status_label.setStyleSheet("color: #27ae60;")
            
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"XML parse hatası:\n{str(e)}")
            self.status_label.setText("❌ XML okunamadı")
            self.status_label.setStyleSheet("color: #e74c3c;")
            
    def create_field_inputs(self):
        # Mevcut widget'ları temizle
        while self.fields_layout.count():
            child = self.fields_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
                
        self.field_inputs = []
        
        # GroupName'lere göre grupla
        grouped_fields = {}
        ungrouped_fields = []
        
        for field in self.fields:
            if field.group_name:
                if field.group_name not in grouped_fields:
                    grouped_fields[field.group_name] = []
                grouped_fields[field.group_name].append(field)
            else:
                ungrouped_fields.append(field)
                
        # Grupsuz alanları ekle
        if ungrouped_fields:
            form_layout = QFormLayout()
            for field in ungrouped_fields:
                input_widget = self.create_field_input(field)
                display_info = self.get_field_display_info(field.field_name)
                label = QLabel(display_info['label'] + ":")
                label.setStyleSheet("font-weight: bold; color: #2c3e50;")
                form_layout.addRow(label, input_widget)
                self.field_inputs.append((field, input_widget))
            self.fields_layout.addLayout(form_layout)
            
        # Gruplu alanları ekle
        for group_name, group_fields in grouped_fields.items():
            group_box = QGroupBox(group_name)
            group_box.setStyleSheet("""
                QGroupBox {
                    font-weight: bold;
                    border: 2px solid #bdc3c7;
                    border-radius: 5px;
                    margin-top: 10px;
                    padding-top: 10px;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    subcontrol-position: top left;
                    padding: 0 5px;
                    background-color: white;
                    color: #34495e;
                }
            """)
            
            group_layout = QFormLayout()
            for field in group_fields:
                input_widget = self.create_field_input(field)
                display_info = self.get_field_display_info(field.field_name)
                label = QLabel(display_info['label'] + ":")
                label.setStyleSheet("font-weight: bold; color: #2c3e50;")
                group_layout.addRow(label, input_widget)
                self.field_inputs.append((field, input_widget))
                
            group_box.setLayout(group_layout)
            self.fields_layout.addWidget(group_box)
            
    def create_field_input(self, field):
        display_info = self.get_field_display_info(field.field_name)
        
        # Özel alan işleyicisi varsa değeri al
        if display_info['handler']:
            special_value = self.get_special_field_value(display_info['handler'])
        else:
            special_value = None
        
        # Alan tipine göre widget seç
        if display_info['field_type'] == 'multiline' or field.length > 50:
            widget = QPlainTextEdit()
            widget.setMaximumHeight(100)
            placeholder = display_info['placeholder']
            if 'Maksimum' not in placeholder:
                placeholder = f"{placeholder} (Maks. {field.length} karakter)"
            widget.setPlaceholderText(placeholder)
        else:
            widget = QLineEdit()
            placeholder = display_info['placeholder']
            if 'Maksimum' not in placeholder and 'Otomatik' not in placeholder:
                placeholder = f"{placeholder} (Maks. {field.length} karakter)"
            widget.setPlaceholderText(placeholder)
        
        # Değeri ayarla
        if special_value:
            # Özel işleyiciden gelen değer
            if isinstance(widget, QPlainTextEdit):
                widget.setPlainText(special_value)
            else:
                widget.setText(special_value)
        else:
            # CDATA'dan mevcut değer
            current_value = self.content_cdata[field.start_offset:field.start_offset + field.length]
            if isinstance(widget, QPlainTextEdit):
                widget.setPlainText(current_value)
            else:
                widget.setText(current_value)
        
        # Özel alan ise salt okunur yap (otomatik doldurulan alanlar)
        if display_info['field_type'] == 'special':
            widget.setReadOnly(True)
            widget.setStyleSheet("background-color: #f0f0f0;")
            
        return widget
        
    def save_as_udf(self):
        if not self.xml_tree or not self.field_inputs:
            QMessageBox.warning(self, "Uyarı", "Önce bir USF dosyası yükleyin!")
            return
            
        # Kayıt yeri seç
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "UDF Dosyası Kaydet",
            os.path.splitext(os.path.basename(self.usf_path))[0] + "_doldurulmus.udf",
            "UDF Files (*.udf);;All Files (*)"
        )
        
        if not save_path:
            return
            
        try:
            # CDATA'yı güncelle
            new_content = self.update_content_with_values()
            
            # XML'i güncelle
            root = self.xml_tree.getroot()
            content_elem = root.find('content')
            content_elem.text = new_content
            
            # Guncelenmis XML'i CDATA korunarak kaydet
            updated_xml_path = os.path.join(self.temp_dir, 'content.xml')
            self.write_xml_with_cdata(root, updated_xml_path)
            
            # ZIP olarak paketle
            with zipfile.ZipFile(save_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                # Geçici klasördeki tüm dosyaları ekle
                for file_name in os.listdir(self.temp_dir):
                    file_path = os.path.join(self.temp_dir, file_name)
                    if os.path.isfile(file_path):
                        zip_file.write(file_path, file_name)
                        
            self.status_label.setText(f"✅ Dosya kaydedildi: {os.path.basename(save_path)}")
            self.status_label.setStyleSheet("color: #27ae60; font-weight: bold;")
            
            QMessageBox.information(self, "Başarılı", f"Dosya başarıyla kaydedildi:\n{save_path}")
            
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"Kaydetme hatası:\n{str(e)}")
            self.status_label.setText("❌ Dosya kaydedilemedi")
            self.status_label.setStyleSheet("color: #e74c3c;")
            
    def update_content_with_values(self):
        """GUI'deki değerleri alıp CDATA'yı güncelle ve offset'leri yeniden hesapla"""
        
        # Değişiklikleri topla (offset'e göre sıralı)
        changes = []
        for field, widget in self.field_inputs:
            if isinstance(widget, QPlainTextEdit):
                new_value = widget.toPlainText()
            else:
                new_value = widget.text()
                
            changes.append({
                'field': field,
                'new_value': new_value,
                'original_offset': field.start_offset,
                'original_length': field.length
            })
            
        # Offset'e göre sırala (tersten, sondan başa doğru işlem yapacağız)
        changes.sort(key=lambda x: x['original_offset'], reverse=True)
        
        # CDATA'yı güncelle
        new_content = self.content_cdata

        # Sondan başa doğru değiştir (offset'leri bozmamak için)
        for change in changes:
            field = change['field']
            new_value = change['new_value']
            start = field.start_offset
            end = field.start_offset + field.length
            
            # Yeni değeri yerleştir
            new_content = new_content[:start] + new_value + new_content[end:]
            
            # Uzunluk farkını hesapla
            length_diff = len(new_value) - field.length
            
            # Field'ın yeni uzunluğunu güncelle
            field.length = len(new_value)
            field.element.set('length', str(field.length))
            
            # Bu field'dan sonraki tüm offset'leri güncelle
            if length_diff != 0:
                self.update_offsets_after(field.start_offset, length_diff)
                
        return new_content
        
    def update_offsets_after(self, changed_offset, length_diff):
        """Belirli bir offset'ten sonraki tüm element'lerin offset'lerini güncelle"""
        
        root = self.xml_tree.getroot()
        elements = root.find('elements')
        
        if elements is None:
            return
            
        # Tüm paragraph'ları tara
        for paragraph in elements.findall('paragraph'):
            # Paragraph içindeki tüm element'leri kontrol et
            for elem in paragraph:
                if 'startOffset' in elem.attrib:
                    elem_offset = int(elem.get('startOffset'))
                    
                    # Bu element değiştirilen alandan sonraysa offset'ini güncelle
                    if elem_offset > changed_offset:
                        new_offset = elem_offset + length_diff
                        elem.set('startOffset', str(new_offset))
                        
                        # Field objelerini de güncelle
                        if elem.tag == 'field':
                            field_name = elem.get('fieldName', '')
                            for field in self.fields:
                                if field.field_name == field_name and field.start_offset == elem_offset:
                                    field.start_offset = new_offset
                                    break
                                    
    def write_xml_with_cdata(self, root, file_path):
        """XML'i CDATA korunarak dosyaya yaz"""
        lines = []
        lines.append('<?xml version="1.0" encoding="UTF-8" ?> ')
        lines.append('')
        lines.append(f'<template format_id="{root.get("format_id")}" >')

        # Content with CDATA
        content = root.find('content')
        content_text = content.text or ''
        lines.append(f'<content><![CDATA[{content_text}]]></content>')

        # Properties
        props = root.find('properties')
        if props is not None:
            props_str = '<properties>'
            for child in props:
                attrs = ' '.join(f'{k}="{v}"' for k, v in child.attrib.items())
                props_str += f'<{child.tag} {attrs} />'
            props_str += '</properties>'
            lines.append(props_str)

        # Elements
        elements = root.find('elements')
        if elements is not None:
            elem_attrs = ' '.join(f'{k}="{v}"' for k, v in elements.attrib.items())
            lines.append(f'<elements {elem_attrs} >')
            for para in elements:
                para_attrs = ' '.join(f'{k}="{v}"' for k, v in para.attrib.items())
                para_line = f'<paragraph {para_attrs}>' if para_attrs else '<paragraph>'
                for child in para:
                    child_attrs = ' '.join(f'{k}="{v}"' for k, v in child.attrib.items())
                    para_line += f'<{child.tag} {child_attrs} />'
                para_line += '</paragraph>'
                lines.append(para_line)
            lines.append('</elements>')

        # Styles
        styles = root.find('styles')
        if styles is not None:
            styles_line = '<styles>'
            for style in styles:
                attrs = ' '.join(f'{k}="{v}"' for k, v in style.attrib.items())
                styles_line += f'<style {attrs} />'
            styles_line += '</styles>'
            lines.append(styles_line)

        lines.append('</template>')

        with open(file_path, 'w', encoding='utf-8', newline='') as f:
            f.write('\n'.join(lines))

    def closeEvent(self, event):
        # Geçici klasörü temizle
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.closed.emit()
        event.accept()