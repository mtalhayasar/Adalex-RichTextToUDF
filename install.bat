@echo off
chcp 65001 > nul
echo.
echo ====================================
echo   AdaLex UDF Dönüştürücü Kurulumu
echo ====================================
echo.

REM Python kurulu mu kontrol et
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [HATA] Python kurulu değil!
    echo.
    echo Python'u indirmek için: https://www.python.org/downloads/
    echo Kurulum sırasında "Add Python to PATH" seçeneğini işaretlemeyi unutmayın.
    echo.
    pause
    exit /b 1
)

echo [✓] Python kurulu
echo.

REM pip'i güncelle
echo [→] pip güncelleniyor...
python -m pip install --upgrade pip --quiet

REM Bağımlılıkları yükle
echo [→] Bağımlılıklar yükleniyor...
pip install -r requirements.txt --quiet

if %errorlevel% neq 0 (
    echo.
    echo [HATA] Bağımlılıklar yüklenemedi!
    pause
    exit /b 1
)

echo.
echo [✓] Tüm bağımlılıklar başarıyla yüklendi!
echo.
echo ====================================
echo   Kurulum Tamamlandı!
echo ====================================
echo.
echo Uygulamayı çalıştırmak için:
echo   python rtf-to-udf.py
echo.
echo Veya run.bat dosyasını çalıştırın.
echo.

REM run.bat dosyası oluştur
echo @echo off > run.bat
echo python rtf-to-udf.py >> run.bat
echo [✓] run.bat dosyası oluşturuldu

pause