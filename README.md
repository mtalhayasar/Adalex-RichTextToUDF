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
- ✅ Metin biçimlendirmelerini koruma (kalın, italik, altı çizili, üstü çizili, üst/alt simge,
  renk, kaynakta açıkça verilmiş yazı tipi ve punto)
- ✅ Liste desteği (numaralı, harfli ve madde işaretli; elle yazılmış "1)" / "a)" maddeleri dahil)
- ✅ Paragraf hizalama, sol/sağ girinti, ilk satır / asılı girinti ve satır aralığı
- ✅ Künye hizası: "DOSYA NO : ..." gibi sekmeli satırlar tek bir etiket kolonuna oturur;
  kolon en uzun etikete göre seçilir, sığmayan etiket sarılır, değerin sarma satırı kolona hizalanır
- ✅ Yazmadan önce doğrulama: paragraf/offset döşemesi bozuksa dosya hiç yazılmaz
- ✅ Kullanıcı dostu görsel arayüz
- ✅ Tek tıkla UDF dönüştürme ve kaydetme

## 🧩 UDF motoru (`udf_motor.py`)

Üretim, okuma ve PDF çizimi **Adalex-UYAP** projesinin saf (yalnız standart kütüphane) UDF
motorunu kullanır; `udf_motor.py`, Adalex'teki `adalex/udf.py` dosyasının **birebir kopyasıdır**
ve elle değiştirilmez - Adalex'te motor güncellenince dosya yeniden kopyalanır. Böylece iki
uygulama aynı UDF'i üretir ve aynı UDF'i aynı biçimde okur (gerçek UYAP evrakıyla doğrulanmış
paragraf modeli, künye sekme durağı, tablo / üstbilgi / altbilgi, form alanları).

## 🆕 v2.2.0

- Dönüştürücü Adalex UDF motoruna geçti: gövde stili UYAP'ın beklediği tam `hvl-default`
  tanımıyla yazılır, metinde `]]>` geçse bile CDATA bozulmaz, son paragrafın üstüne binen fazladan
  paragraf kaldırıldı, girintiler pikselden puntoya doğru çevrilir.
- Sekme hizası sabit 1,5 cm durakları yerine künye kolonuyla yapılır; Word'ün `&nbsp;` dizileriyle
  verdiği hizalama da sekmeye çevrilir.
- UDF → PDF: tablolar (kenarlıklı/kenarlıksız, satır başına sütun düzeni), üstbilgi/altbilgi,
  sekme koşuları, UYAP form belgelerinin alan değerleri, renk, yazı tipi/punto, satır aralığı,
  paragraf boşlukları, ilk satır ve asılı girinti artık çizilir; yatay sayfa (`paperOrientation`
  0/2) doğru tanınır.
- Şablon düzenleyici: tablo içindeki alanlar da bulunur ve kaydırılır, tablolar kaydederken
  bozulmaz, öznitelik ve CDATA kaçışı yapılır.

## 🎯 Kullanım

1. Uygulamayı açın
2. Word veya Google Docs'tan metninizi kopyalayın
3. Uygulamadaki metin alanına yapıştırın
4. "UDF Olarak Kaydet" butonuna tıklayın
5. Dosyanızı kaydedin

## 🖱️ UDF → PDF (Sağ Tık ile Dönüştürme)

UDF dosyalarını Windows Gezgini'nde **sağ tıklayarak** doğrudan PDF'e
çevirebilirsiniz. Harici bir araca (UYAP Editörü, Java vb.) gerek yoktur;
dönüştürme `content.xml` doğrudan okunarak yapılır (Times New Roman 12, A4,
hizalama/girinti/tab/kalın-italik-altıçizili ve liste biçimleri korunur).

### Kurulum

`dist/udf2pdf/` klasöründeki **`Kurulum-SagTik-PDF.bat`** dosyasını çalıştırın
(yönetici gerekmez - yalnızca geçerli kullanıcıya kaydeder). Kaldırmak için
**`Kaldir-SagTik-PDF.bat`**.

Komut satırından:
```bash
udf2pdf.exe install       # sağ tık menüsünü ekle
udf2pdf.exe uninstall     # sağ tık menüsünü kaldır
```

### Kullanım

Bir veya birden fazla `.udf` dosyasını seçip sağ tıklayın:

- **PDF'e Dönüştür** - seçilen her dosyayı yanına aynı adla `.pdf` olarak kaydeder.
- **Tek PDF'te Birleştir** - seçilen tüm UDF'leri (dosya adına göre sıralı, her
  belge yeni sayfada) tek bir PDF'te birleştirir; kayıt yerini sorar.

> Windows 11'de bu seçenekler **"Diğer seçenekleri göster"** (Shift+F10) klasik
> menüsünün altında yer alabilir.

### Komut satırı

```bash
udf2pdf.exe convert a.udf [b.udf ...]   # her dosya -> yanında .pdf
udf2pdf.exe merge   a.udf [b.udf ...]   # tek PDF'te birleştir
```

### Kendi udf2pdf.exe'nizi derleme

```bash
pyinstaller udf2pdf.spec --noconfirm
```
Çıktı `dist/udf2pdf/` klasöründe oluşur (onedir; sağ tıkta hızlı açılış için).

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
├── rtf-to-udf.py          # Ana uygulama kodu (zengin metin -> UDF)
├── udf_motor.py           # Adalex-UYAP UDF motoru (adalex/udf.py kopyası; elle değiştirilmez)
├── sablon_editor.py       # Şablon düzenleyici sekmesi
├── udf_to_pdf.py          # UDF -> PDF dönüştürücü + sağ tık menüsü
├── udf2pdf.spec           # udf2pdf.exe için PyInstaller yapılandırması
├── installer/             # Sağ tık menüsü kurulum/kaldırma .bat dosyaları
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