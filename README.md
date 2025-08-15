# AdaLex UDF Dönüştürücü

Word, Google Docs ve diğer kaynaklardan kopyalanan zengin metin formatındaki içerikleri UDF (Universal Document Format) formatına dönüştüren masaüstü uygulaması.

## 🚀 Hızlı Başlangıç

### Yöntem 1: Hazır Windows Uygulaması (.exe)

1. [Releases](https://github.com/mtalhayasar/Adalex-RichTextToUDF/releases) sayfasına gidin
2. En son sürümden `AdaLexUDF.exe` dosyasını indirin
3. İndirilen `.exe` dosyasını çalıştırın (kurulum gerektirmez)

### Yöntem 2: Kaynak Koddan Çalıştırma

#### Ön Gereksinimler
- Python 3.8 veya üzeri
- pip (Python paket yöneticisi)

#### Kurulum Adımları

1. **Projeyi İndirin**
   ```bash
   git clone https://github.com/mtalhayasar/Adalex-RichTextToUDF.git
   cd Adalex-RichTextToUDF
   ```

2. **Bağımlılıkları Yükleyin**
   ```bash
   pip install -r requirements.txt
   ```
   
   **VEYA** Windows için tek tıkla kurulum:
   ```bash
   install.bat
   ```

3. **Uygulamayı Çalıştırın**
   ```bash
   python rtf-to-udf.py
   ```

## 📋 Özellikler

- ✅ Word, Google Docs ve diğer kaynaklardan metin kopyalama
- ✅ Metin biçimlendirmelerini koruma (kalın, italik, altı çizili)
- ✅ Liste desteği (numaralı ve madde işaretli)
- ✅ Paragraf hizalama desteği
- ✅ Kullanıcı dostu görsel arayüz
- ✅ Tek tıkla UDF dönüştürme ve kaydetme

## 🎯 Kullanım

1. Uygulamayı açın
2. Word veya Google Docs'tan metninizi kopyalayın
3. Uygulamadaki metin alanına yapıştırın
4. "UDF Olarak Kaydet" butonuna tıklayın
5. Dosyanızı kaydedin

## 🔧 Geliştirici Notları

### Kendi .exe Dosyanızı Oluşturma

Kaynak koddan kendi Windows uygulamanızı oluşturmak için:

```bash
# PyInstaller'ı yükleyin
pip install pyinstaller

# .exe dosyasını oluşturun
pyinstaller --onefile --windowed --icon=icons/solo-logo-manual.ico --add-data "logo.png;." --add-data "solo-logo.png;." --add-data "icons;icons" --name AdaLexUDF rtf-to-udf.py
```

Oluşturulan `.exe` dosyası `dist/` klasöründe bulunur.

### Proje Yapısı

```
Adalex-RichTextToUDF/
├── rtf-to-udf.py          # Ana uygulama kodu
├── requirements.txt       # Python bağımlılıkları
├── install.bat           # Windows kurulum scripti
├── README.md             # Bu dosya
├── icons/                # Uygulama ikonları
│   └── *.png, *.ico     
├── logo.png              # Ana logo
└── solo-logo.png         # Alternatif logo
```

## 🐛 Sorun Bildirimi

Hata veya öneri için [Issues](https://github.com/mtalhayasar/Adalex-RichTextToUDF/issues) sayfasını kullanın.

## 📄 Lisans

Bu proje özel lisansa sahiptir. Tüm hakları saklıdır.