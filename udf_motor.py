"""SAF UDF (UYAP Doküman Formatı) motoru - stdlib ile oku/doldur/üret.

UDF bir ZIP kabıdır; içinde `content.xml` (ana belge) + opsiyonel yan dosyalar (gömülü
resim vb.) bulunur. `content.xml` yapısı Adalex evrak felsefesiyle AYNI mantıktadır:

  - `<content><![CDATA[...]]></content>`  ->  belgenin TÜM düz metni (tek CDATA bloğu).
  - `<elements>` içinde `<paragraph>` düğümleri, her paragrafta `<content startOffset length .../>`
    biçim koşuları (run) -> CDATA metnine offset/uzunluk ile bağlanan biçim overlay'i.

Bu modül SAF'tır: UYAP/DB/ağ YOK; yalnız stdlib (`zipfile`/`io`/`xml.etree`/`re`/`dataclasses`
+ `datetime`). Özel (türetilmiş) tarih değişkenlerinin TEK kaynağı buradaki `ozel_deger`dir;
`adalex/belge.py` aynı fonksiyonu re-export eder (iki motor arasında drift olmaz). Böylece
`tests/test_udf.py` ile izole test edilebilir (takvim.py deseni).

UTF-16 UYARISI: UDF offsetleri UYAP tarafında UTF-16 kod birimi sayabilir; Python `len()` kod
NOKTASI sayar. İkisi yalnız BMP-dışı (supplementary, U+10000+) karakterlerde ayrışır. Türkçe
metin (ç ş ğ ü ö ı İ dahil) tamamen BMP'dedir -> güvenli. Emoji vb. supplementary karakter
girerse offsetler kayabilir; kurumsal dilekçe metninde bu beklenmez (bilinçli sınır)."""

from __future__ import annotations

import hashlib
import io
import math
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from xml.etree import ElementTree as ET

# --- Değişken yer tutucu deseni: {{ anahtar }} ya da {{ anahtar | filtre | filtre }} ---
# Filtre ZİNCİRİ YAKALANMAZ (udf_doldur HAM dolgu yapar, biçimleyici uygulamaz); ama filtreli
# yer tutucu da ALAN olarak görülür/doldurulur - belge.YER_TUTUCU_DESEN ile gramer AYNI kalır
# (eskiden `{{ tutar | para }}` udf tarafında görünmez kalıp belgede ham bırakılıyordu; zincir
# `{{ mahkeme | datif | buyuk }}` de tek alan olarak eşleşir).
DEGISKEN_DESEN = re.compile(r"\{\{\s*([\w.]+)\s*(?:\|\s*\w+\s*)*\}\}")

# Türkçe ay adları (belge.py bunu re-export eder; TEK kaynak burası).
AYLAR_TR = [
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
]


def ozel_deger(key: str) -> str | None:
    """Türetilmiş değişken -> değer; değilse None (açık değer sözlüğüne bakılır).

    TEK KAYNAK: hem `udf_doldur` hem `belge.render_sablon` bu fonksiyonu kullanır - iki motor
    aynı şablon metnine aynı özel değerleri üretir (eskiden udf yalnız 3 anahtar biliyordu,
    `{{yil}}` udf_doldur'da ham kalıyordu)."""
    bugun = date.today()
    if key in ("tarih_bugun", "bugun"):
        return _tarih_bicim(bugun)
    if key == "tarih_yarin":
        return _tarih_bicim(bugun + timedelta(days=1))
    if key == "tarih_dun":
        return _tarih_bicim(bugun - timedelta(days=1))
    if key == "yil":
        return str(bugun.year)
    if key == "ay":
        return AYLAR_TR[bugun.month - 1]
    if key == "ay_no":
        return f"{bugun.month:02d}"
    if key == "gun":
        return f"{bugun.day:02d}"
    return None


# Geriye dönük sözlük görünümü (eski çağıranlar/testler için; kaynak `ozel_deger`).
OZEL_DEGERLER = {
    "tarih_bugun": lambda: _tarih_bicim(date.today()),
    "tarih_yarin": lambda: _tarih_bicim(date.today() + timedelta(days=1)),
    "tarih_dun": lambda: _tarih_bicim(date.today() - timedelta(days=1)),
}

# PARAGRAF SONU = '\n'. UDF Java Swing belge modeline dayanır: GÖRÜNEN paragrafı <paragraph>
# öğesi DEĞİL, CDATA metnindeki '\n' karakteri belirler; <paragraph> yalnız o karakter aralığına
# hizalama/girinti BAĞLAR. Metni '\n' ile bitmeyen paragraf bir öncekine YAPIŞIR ve kendi
# Alignment/LeftIndent değerini KAYBEDER (ör. sağa yaslı imza bloğu gövdeye yapışır).
#
# GERÇEK UYAP ÇIKTISIYLA DOĞRULANDI (2026-09-03; `indirilenler/` altındaki gerçek UYAP evrakı:
# iddianame, gerekçeli karar, istinaf/temyiz dilekçesi, kesinleşme şerhi): hepsinde her paragraf
# aralığı '\n' ile biter, span'lar bitişik döşenir ve U+200B HİÇ GEÇMEZ. Eski model (paragraf
# ayırıcı yok + boş satır U+200B) yalnız BİZİM ürettiğimiz dosyalarda vardı - UYAP'ın kendi
# çıktısında değil; bu yüzden hizalama/satır yapısı kayboluyordu.
PARAGRAF_SONU = "\n"
# Yalnız GERİYE DÖNÜK OKUMA için: eski Adalex üretimi boş satırı U+200B ile temsil ediyordu.
# Yeni üretimde KULLANILMAZ; önizleme (`udf_html`) eski belgelerde bu işareti temizler.
ESKI_BOS_PARAGRAF = chr(0x200B)

VARSAYILAN_FORMAT_ID = "1.8"

# content.xml İSKELETİ (kalıp). Yeniden ürettiğimiz DÖRT blok (CDATA, properties, elements,
# styles) birer imle ile temsil edilir; ARALARINDAKİ her şey - XML bildirimi, `<template>`
# açılış etiketi, blok sırası, satır sonları, boşluklar - AYNEN taşınır.
#
# Neden ayrı ayrı alanlar değil de kalıp: gerçek UYAP çıktısı iskelette şaşırtıcı biçimde
# çeşitlidir (2026-09-03, 121 evrak sayıldı): `standalone` taşımayan bildirim, bildirimin HİÇ
# olmaması, bildirimden sonra fazladan boş satır, `<template format_id="1.8" >` (kapanıştan
# önce boşluk), `</content><properties>` (araya satır sonu koymadan). Her varyantı ayrı alan
# olarak kovalamak bitmeyen bir listeydi; kalıp hepsini tek hamlede çözer ve YARIN çıkacak
# varyantı da taşır.
#
# Bu bayt titizliği kozmetik DEĞİL: ayrık e-imza (`sign.sgn`) tam o content.xml baytlarının
# özetine bağlıdır - tek bir boşluk değişirse imza doğrulaması düşer.
KALIP_ICERIK = "\x00ICERIK\x00"
KALIP_OZELLIK = "\x00OZELLIK\x00"
KALIP_ELEMENTS = "\x00ELEMENTS\x00"
KALIP_STIL = "\x00STIL\x00"


def _oznitelik_kacis(s: str) -> str:
    """XML öznitelik değeri kaçışı. Seri hale getirme bölümünün yardımcısıdır ama BURADA
    tanımlıdır: `styles_uret` onu MODÜL YÜKLENİRKEN çağırır (`VARSAYILAN_STYLES`)."""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def olcu(deger) -> str:
    """UDF ÖLÇÜ değeri (punto/çarpan) biçimi: DAİMA en az bir ondalık (`0` değil `0.0`).

    UYAP kendi çıktısında ölçüleri hep ondalıklı yazar (`LeftIndent="0.0"`, `SpaceAbove="2.0"`);
    `%g` bunu `0`/`2`ye indirir. Java tarafı ikisini de okur ama biçim sapması dosyaları
    karşılaştırırken gürültü yaratır - üretilen belge UYAP'ınkiyle aynı şekilde okunmalı."""
    try:
        s = f"{float(deger):.6g}"
    except (TypeError, ValueError):
        return "0.0"
    return s if ("." in s or "e" in s or "E" in s) else s + ".0"


# UYAP'ın kabul ettiği asgari sabit bloklar (referans şablondan replike; kenar boşlukları
# KULLANICI KARARI ile her yönden 2 cm = 56.69 pt - referansın dar marjları yetersizdi).
# Programatik üretimde (`belge_uret`/`duz_metin_udf`) bu iskelet kullanılır.
VARSAYILAN_PROPERTIES = (
    '<properties>\n'
    '<pageFormat mediaSizeName="1" leftMargin="56.69" rightMargin="56.69" '
    'topMargin="56.69" bottomMargin="56.69" paperOrientation="1" '
    'headerFOffset="20.0" footerFOffset="20.0" />\n'
    '</properties>'
)
# KRİTİK: `<elements resolver="hvl-default">` "hvl-default" ADLI stili çözümler; bu stil
# `<styles>` içinde TANIMLI OLMAK ZORUNDA - yoksa UYAP editörü belgeyi açarken hata verir
# (çalışan referans UDF-Toolkit çıktısıyla birebir; eski tek-"default" blok bu yüzden bozuktu).
# İki stil: "default" (Swing Dialog + FONT_ATTRIBUTE_KEY) + "hvl-default" (Times New Roman gövde).
#: Gövde stilinin ADI - `<elements resolver="...">` bunu çözer.
GOVDE_STIL = "hvl-default"

#: Satır aralığı seçeneği -> UDF `LineSpacing`. Swing'de değer ÇARPAN ARTIŞIdır: 0.0 tek satır,
#: 0.5 bir buçuk, 1.0 çift (gerçek evrakta 0.0=6541, 0.5=74 kullanım). Ayar anahtarı ile UDF
#: değeri arasındaki TEK eşleme burasıdır; frontend seçenek etiketleri bunun ikizidir.
SATIR_ARALIGI: dict[str, float] = {"tek": 0.0, "bir_bucuk": 0.5, "cift": 1.0}


def styles_uret(*, family: str = "Times New Roman", size: int = 12,
                satir_araligi: float = 0.0, bosluk_ust: float = 0.0,
                bosluk_alt: float = 0.0) -> str:
    """`<styles>` bloğu: belge GENELİ biçim varsayılanları (SAF).

    Üç katmanlı biçim modelinin (Faz 1) EN ÜST katmanı: yazı tipi, punto, satır aralığı ve
    paragraf boşluğu her paragrafa tek tek yazılmaz, gövde stilinde BİR KEZ tanımlanır - UYAP'ın
    kendi çıktısı da böyledir (`hvl-default` içinde `SpaceAbove="2.0" SpaceBelow="2.0"
    LineSpacing="0.0"`). Paragraf düzeyindeki sapma `<paragraph>` özniteliğiyle bunu ezer.
    Eskiden bu blok SABİTti ve `hvl-default` yalnız aile/punto taşıyordu; belge geneli hiçbir
    aralık tanımı yoktu, bu yüzden paragraf boşluğu boş paragrafla taklit ediliyordu."""
    return (
        '<styles>\n'
        '<style name="default" description="Geçerli" family="Dialog" size="12" bold="false" '
        'italic="false" foreground="-13421773" '
        'FONT_ATTRIBUTE_KEY="javax.swing.plaf.FontUIResource[family=Dialog,name=Dialog,style=plain,size=12]" />\n'
        f'<style name="{GOVDE_STIL}" family="{_oznitelik_kacis(str(family))}" '
        f'size="{int(size)}" description="Gövde" Alignment="0" LeftIndent="0.0" '
        f'RightIndent="0.0" LineSpacing="{olcu(satir_araligi)}" '
        f'SpaceAbove="{olcu(bosluk_ust)}" SpaceBelow="{olcu(bosluk_alt)}" />\n'
        '</styles>'
    )


#: Ayar verilmeyen çağrılar için varsayılan stil bloğu (UYAP varsayılanı: tek satır, boşluksuz).
VARSAYILAN_STYLES = styles_uret()


def _tarih_bicim(d: date) -> str:
    """GG.AA.YYYY (UYAP/Türk hukuk biçimi)."""
    return f"{d.day:02d}.{d.month:02d}.{d.year:04d}"


# --- Veri modeli ---


#: Bir `<paragraph>` içinde offset TAŞIYAN öğeler. Hepsinin `startOffset`+`length`i vardır ve
#: CDATA'yı birlikte döşerler - `<content>` yalnız en sık olanıdır. Gerçek UYAP evrakından
#: sayıldı (2026-09-03, `indirilenler/` altındaki 120 evrak): content 19.619, field 10.030,
#: space 8.920, tab 269, image 126. Eskiden yalnız `<content>` okunuyordu; kalanı SESSİZCE
#: düşüyordu - bu yüzden form alanlı/sekmeli evrak yeniden yazıldığında metni kayboluyordu.
KOSU_ETIKETLERI = ("content", "field", "space", "tab", "image")


@dataclass
class Kosu:
    """Bir paragraf içindeki biçim koşusu (run): CDATA metnine offset/uzunluk ile bağlı.

    `etiket` koşunun XML öğe adıdır (`KOSU_ETIKETLERI`); UYAP form alanı (`field`), boşluk
    (`space`), sekme (`tab`) ve gömülü görsel (`image`) de birer koşudur ve TÜRÜ korunmalıdır -
    hepsini `<content>` olarak geri yazmak alanı düz metne çevirirdi."""

    start: int
    uzunluk: int
    nitelikler: dict[str, str] = field(default_factory=dict)  # family/size/bold/...
    etiket: str = "content"
    #: Koşunun HAM XML etiketi - yalnız yeniden üretim baytı tutmadığında doldurulur.
    #: Gerek: XML ayrıştırıcısı öznitelik DEĞERLERİNDEKİ satır sonunu boşluğa çevirir
    #: (standart davranış). Gömülü görselin `imageData` base64'ü satır sonu taşıdığı için
    #: geri yazınca bayt değişiyordu; anlamı aynı ama ayrık e-imza bayta bağlı.
    ham: str = ""


@dataclass
class Paragraf:
    nitelikler: dict[str, str] = field(default_factory=dict)  # Alignment/LeftIndent/...
    kosular: list[Kosu] = field(default_factory=list)


@dataclass
class Hucre:
    """`<cell>`: paragraf VE iç içe tablo taşıyabilir (gerçek evrakta 4 seviye görüldü)."""

    nitelikler: dict[str, str] = field(default_factory=dict)
    dugumler: list = field(default_factory=list)      # Paragraf | Tablo


@dataclass
class Satir:
    nitelikler: dict[str, str] = field(default_factory=dict)
    hucreler: list[Hucre] = field(default_factory=list)


@dataclass
class Tablo:
    """`<table>`: duruşma zaptı, tensip tutanağı, dava bilgi formu, reddiyat fişi gibi UYAP
    evrakının İSKELETİ tablodur. Düğüm olarak korunmazsa belgenin yarısı kaybolur."""

    nitelikler: dict[str, str] = field(default_factory=dict)
    satirlar: list[Satir] = field(default_factory=list)


@dataclass
class Bolum:
    """`<header>` / `<footer>`: gövde düğümleriyle AYNI listede, KENDİ YERİNDE durur.

    Ayrı alan olarak tutulmuyor çünkü ne sayısı ne yeri sabit: gerçek bir Yargıtay ilamında
    İKİ `<footer>` vardı ve biri son tablodan ÖNCE geliyordu. Tekil alan modeli ikincisini
    sessizce düşürüyordu."""

    etiket: str = "footer"                # header | footer
    nitelikler: dict[str, str] = field(default_factory=dict)
    dugumler: list = field(default_factory=list)


def duz_paragraflar(dugumler: list, *, bolum_dahil: bool = False) -> list[Paragraf]:
    """Düğüm ağacındaki paragrafları belge (CDATA) sırasıyla düzleştir.

    `bolum_dahil=False` (varsayılan) üstbilgi/altbilgi İÇERİĞİNİ atlar - `UdfBelge.paragraflar`
    yalnız GÖVDEyi verir. Doğrulama gibi "her paragraf" gereken yerlerde True geçilir.

    Kopya DEĞİL, aynı nesnelere REFERANS döner: `udf_doldur` offsetleri bu liste üzerinden
    kaydırır ve mutasyon ağaca yansır (tek kaynak korunur)."""
    out: list[Paragraf] = []
    for d in dugumler:
        if isinstance(d, Paragraf):
            out.append(d)
        elif isinstance(d, Tablo):
            for s in d.satirlar:
                for h in s.hucreler:
                    out.extend(duz_paragraflar(h.dugumler, bolum_dahil=bolum_dahil))
        elif isinstance(d, Bolum) and bolum_dahil:
            out.extend(duz_paragraflar(d.dugumler, bolum_dahil=bolum_dahil))
    return out


@dataclass
class Alan:
    """Şablonda bulunan bir `{{...}}` yer tutucusu (doldurulabilir alan)."""

    ad: str
    start: int
    uzunluk: int  # ham `{{ ... }}` metninin uzunluğu (kod noktası)
    bold: bool = False
    grup: str = ""  # yer tutucuyu içeren paragrafın hizalaması (bağlam ipucu)


@dataclass
class UdfBelge:
    """Ayrıştırılmış UDF: düz metin + biçim overlay + korunması gereken yan içerik.

    CDATA metni TEK süreklidir: [üstbilgi metni][gövde metni][altbilgi metni]. `paragraflar`
    yalnız GÖVDE koşularıdır; `ust_paragraflar`/`alt_paragraflar` `<header>`/`<footer>` içindeki
    paragraflardır (offsetleri AYNI paylaşılan CDATA'ya bağlanır). `footer_nitelik` altbilgi
    öğesinin öznitelikleridir (sayfa numarası `pageNumber-spec` vb. burada yaşar)."""

    metin: str  # CDATA (üstbilgi + gövde + altbilgi sürekli)
    dugumler: list = field(default_factory=list)       # GÖVDE yapısı: Paragraf | Tablo (SIRALI)
    format_id: str = VARSAYILAN_FORMAT_ID
    properties_xml: str = VARSAYILAN_PROPERTIES
    styles_xml: str = VARSAYILAN_STYLES
    yan_dosyalar: dict[str, bytes] = field(default_factory=dict)  # content.xml DIŞI ZIP üyeleri
    elements_nitelik: dict[str, str] = field(default_factory=dict)  # <elements> öznitelikleri (resolver)
    #: `<elements ...>` AÇILIŞ ETİKETİ ham haliyle. UYAP bu tek etiketi tutarsız yazıyor
    #: (121 evrakta 97 kez `<elements >`, 24 kez `<elements resolver="hvl-default">`);
    #: diğer tüm etiketler tutarlı olduğu için yalnız bunu ham taşımak yetiyor.
    elements_acilis: str = ""
    #: `<elements>` içindeki üst düzey düğümler ARASINDAKİ ayıraç. UYAP hepsini bitişik
    #: yazar (ayıraç ''), bizim ürettiğimiz belgede satır sonu var. Açılış etiketinden
    #: SONRAKİ ve `</elements>` ÖNCESİNDEKİ satır sonu ise değişken değil: 121 evrağın
    #: hepsinde var, o yüzden sabit yazılır.
    elements_ayirac: str = "\n"
    #: Üst düzey düğümlerin ARDINDAN gelen boşluklar, sırayla. `elements_ayirac` tek bir
    #: stil varsayar; gerçek bir Yargıtay ilamı KARIŞIK yazıyordu (bazı düğümlerden sonra
    #: satır sonu, bazılarından sonra hiçbir şey). Liste kısa kalırsa `elements_ayirac`a
    #: düşülür.
    elements_ayiraclar: list = field(default_factory=list)
    #: HİÇBİR koşunun kapsamadığı CDATA KUYRUĞU. Gerçek UYAP evrakının çoğunda (120 evrağın 95'i)
    #: metin, son paragrafın bittiği yerden sonra tek bir satır sonu daha taşır; ikisinde ise
    #: paragraf işaretlemesi olmayan bir imza dipnotu bulunur. Modelde yeri olmadığı için
    #: yeniden yazarken DÜŞÜYORDU - belge her turda bir karakter kısalıyordu.
    kuyruk: str = ""
    kalip: str = ""        # content.xml iskeleti (boşsa varsayılan iskelet kurulur)

    # Aşağıdakilerin HEPSİ `dugumler`den TÜRETİLİR (tek kaynak). Salt-okunur DEĞİL: dönen
    # Paragraf nesneleri ağacın ta kendisidir, `udf_doldur` offsetleri üstünde kaydırır.
    # Ama LİSTEYE ekleme yapmak etkisizdir - yapı `dugumler` üzerinden değiştirilir.
    @property
    def paragraflar(self) -> list[Paragraf]:
        """Yalnız GÖVDE paragrafları (üstbilgi/altbilgi hariç, tablo hücreleri dahil)."""
        return duz_paragraflar(self.dugumler)

    @property
    def tum_paragraflar(self) -> list[Paragraf]:
        """Üstbilgi/altbilgi dahil TÜM paragraflar (doğrulama ve dolgu için)."""
        return duz_paragraflar(self.dugumler, bolum_dahil=True)

    def bolum(self, etiket: str) -> "Bolum | None":
        """İlk `<header>` / `<footer>` düğümü (yoksa None). Belgede birden çok olabilir;
        tekil görünümler geriye dönük uyumluluk içindir, seri hale getirme `dugumler`i
        kullanır ve HEPSİNİ yazar."""
        return next((d for d in self.dugumler
                     if isinstance(d, Bolum) and d.etiket == etiket), None)

    @property
    def ust_dugumler(self) -> list:
        b = self.bolum("header")
        return b.dugumler if b else []

    @property
    def alt_dugumler(self) -> list:
        b = self.bolum("footer")
        return b.dugumler if b else []

    @property
    def ust_nitelik(self) -> dict:
        b = self.bolum("header")
        return b.nitelikler if b else {}

    @property
    def footer_nitelik(self) -> dict:
        b = self.bolum("footer")
        return b.nitelikler if b else {}

    @property
    def ust_paragraflar(self) -> list[Paragraf]:
        return duz_paragraflar(self.ust_dugumler, bolum_dahil=True)

    @property
    def alt_paragraflar(self) -> list[Paragraf]:
        return duz_paragraflar(self.alt_dugumler, bolum_dahil=True)


# --- Ayrıştırma ---


def _cdata_cikar(ham_xml: str) -> str:
    """Üst düzey <content><![CDATA[...]]></content> metnini HAM olarak çıkar.

    ET metni okurken CDATA'yı korur ama CDATA bloğunu güvenle almak için önce ham regex
    denenir (offsetlerin birebir korunması kritik); bulunamazsa ET'ye düşülür. Metinde ']]>'
    geçtiğinde `_cdata_sar` standart deyimle BÖLDÜĞÜNDEN burada TÜM CDATA parçaları birleştirilir
    (eskiden tek non-greedy grup bölünmüş CDATA'da ara işaretleri metne karıştırıyordu)."""
    m = re.search(r"<content>(.*?)</content>", ham_xml, re.DOTALL)
    if m is not None:
        parcalar = re.findall(r"<!\[CDATA\[(.*?)\]\]>", m.group(1), re.DOTALL)
        if parcalar:
            return "".join(parcalar)
    return ""


def ciplak_icerik_mi(veri: bytes) -> bool:
    """ZIP kabı OLMAYAN, doğrudan `content.xml` gövdesi mi? UYAP bazı form evraklarını
    (ör. "E-Duruşma Talebi", `.dat` uzantılı) böyle verir: `<?xml ...?><template ...>`.
    Eskiden bu baytlar hiçbir okuyucuya uymuyor, görsel okumaya PDF diye gidip sağlayıcıdan
    400 alıyordu (canlı bulgu 2026-09-25)."""
    bas = (veri or b"")[:512].lstrip(b"\xef\xbb\xbf \t\r\n")
    return bas.startswith(b"<?xml") and b"<template" in bas


def ciplak_icerik_metni(veri: bytes) -> str:
    """ZIP'siz `content.xml` gövdesinin düz metni (CDATA); biçim uymazsa boş dizgi."""
    if not ciplak_icerik_mi(veri):
        return ""
    try:
        return _cdata_cikar(veri.decode("utf-8", errors="replace")).strip()
    except Exception:
        return ""


# --- UYAP ŞABLON BELGESİ (form evrakı) metni (v162) ---
# UYAP'ın dairede ürettiği form evrakları (ödeme emri, takip talebi, tensip zaptı, dava açılış
# formu...) `<template>` biçimindedir: CDATA gövdesinde `<field fieldName>` koşularının yerinde
# yalnız ALAN ADI yazar ("autoalacakliAdiSoyadi"), gerçek değerler belgenin sonundaki `<data>`
# bloğundadır. CDATA'yı düz okumak değerleri kaybeder (canlı bulgu 2026-09-25: 127 indirilmiş
# UDF'in 47'si böyleydi; icra kalem okumasında 35 boş önerinin 35'i bu yüzdendi).
# Tekrar eden tablo satırları (`<row rowType="dataRow" rowName="row11">`) `<data>` içindeki AYNI
# `rowName` nitelikli öğelere karşılık gelir: satır, eşleşen her veri öğesi için bir kez çizilir.
_SABLON_HTML = re.compile(r"<[^>]+>")


def sablon_verisi(ham_xml: str):
    """Şablon belgenin `<data>` kökü (ET öğesi); şablon değilse ya da `<data>` boşsa None. SAF."""
    m_data = re.search(r"<data\b[^>]*>.*</data>", ham_xml or "", re.DOTALL)
    m_el = re.search(r"<elements\b.*</elements>", ham_xml or "", re.DOTALL)
    if not m_data or not m_el or "<field " not in m_el.group(0):
        return None
    try:
        veri = ET.fromstring(m_data.group(0))
    except ET.ParseError:
        return None
    if not "".join(veri.itertext()).strip():
        return None
    return veri


def _sablon_adlari(ad: str) -> tuple[str, str]:
    """Şablondaki grup/tablo adı + `<data>`daki KODLU karşılığı. UYAP veri etiketlerinde
    rakamları harfle yazar: rakam d -> "X" + a'dan d'inci harf (`Group5PGrp` -> `GroupXfPGrp`,
    `Tablo40` -> `TabloXeXa`; canlı takip talebi 02.10). `rowName` NİTELİĞİ kodlanmaz."""
    return ad, re.sub(r"\d", lambda m: "X" + chr(ord("a") + int(m.group())), ad)


def _sablon_grup(ctx, ad: str):
    """`ad` grubunun verideki öğesi, bağlamın KENDİSİ ya da içinde; yoksa None."""
    adlar = _sablon_adlari(ad)
    return next((e for e in ctx.iter() if e.tag in adlar), None)


def sablon_kosu_metni(k: "Kosu", ctx, cdata: str) -> str:
    """Şablon belgede bir koşunun çizilecek metni - düz metin ve sayfa önizlemesinin TEK kapısı.

    Koşu `fieldGroupName` taşıyorsa GRUBA BAĞLIdır: değer o grubun içinde aranır (alan başka
    grubun değerini ALAMAZ - alacaklı satırına borçlunun TC'si düşüyordu, 02.10) ve grup veride
    YOKSA koşu hiç çizilmez (sabit metin dahil: noter belgesi yokken "nin yevmiye sayılı
    tarihli belgesi", tek vekil varken "vekilleri"). `eol` satır sonudur, `ceol` değeri
    `true` ise satır sonudur."""
    g = k.nitelikler.get("fieldGroupName")
    grp = _sablon_grup(ctx, g) if g else ctx
    if grp is None:
        return ""
    if k.etiket != "field":
        return cdata[k.start:k.start + k.uzunluk]
    ad = k.nitelikler.get("fieldName")
    if ad == "eol":
        return "\n"
    if ad == "ceol":
        e = next((d for d in grp.iter() if d.tag == "ceol"), None)
        return "\n" if e is not None and (e.text or "").strip() == "true" else ""
    return sablon_alan_degeri(grp, ad)


def sablon_alan_degeri(ctx, ad) -> str:
    """Form alanının (`<field fieldName>`) değeri, verilen VERİ BAĞLAMINDA. Bulunamazsa ''."""
    # Kökten YEDEK ARAMA YOK: grup/satır bağlamında bulunamayan alan başka bir grubun
    # değerini getirirdi (canlı bulgu: borçlu satırına alacaklının vergi numarası düşüyordu).
    e = next((d for d in ctx.iter() if d.tag == ad), None) if ad else None
    if e is None:
        return ""
    ham = _SABLON_HTML.sub(" ", "".join(e.itertext()))
    s = "\n".join(" ".join(p.split()) for p in ham.splitlines() if p.strip())
    if not s or s in ("true", "false"):            # koşul bayrağı (ceol) metin değildir
        return ""
    # Kenardaki boşluk UYAP'ın AYRACIdır (`<avukatIban> TR.. Iban Nolu Hesap </avukatIban>`;
    # şablonda bitişik alanlar arasında başka boşluk yok) - tek boşluk olarak korunur.
    return (" " if ham[:1].isspace() else "") + s + (" " if ham[-1:].isspace() else "")


def sablon_paragraflari(dugumler: list, veri) -> list[tuple["Paragraf", object]]:
    """Şablon belgenin paragrafları, her biri kendi VERİ BAĞLAMIYLA, çizim sırasıyla. TEK KAPI:
    düz metin (`sablon_metni`) ve sayfa önizlemesi/baskısı (`udf_html`) aynı genişletmeyi
    kullanır. Üstbilgi/altbilgi (`Bolum`) atlanır - çağıran onları ayrıca verir.

    AYNI `GroupName`li ardışık paragraflar bir BLOKtur ve verideki o grubun HER örneği için bir
    kez çizilir (dört borçlu = dört blok), grup veride HİÇ yoksa blok çizilmez (mirasçı yokken
    3. bent boş kalır); `rowType="dataRow"` tablo satırı eşleşen her `rowName` öğesi için bir
    kez. Grup adı verideki KODLU adıyla da eşleşir (`_sablon_adlari`)."""
    out: list[tuple[Paragraf, object]] = []

    def _duzlestir(agac: list) -> None:
        for d in agac:
            if d[0] == "p":
                out.append((d[1], d[2]))
            else:
                for _satir, hucreler in d[2]:
                    for h in hucreler:
                        _duzlestir(h)

    _duzlestir(belge_agaci(dugumler, veri))
    return out


def belge_agaci(dugumler: list, veri=None) -> list:
    """Belge gövdesinin ÇİZİM AĞACI - tablolar korunur (sayfa önizlemesi tabloyu tablo olarak
    çizer; düz metin `sablon_paragraflari` ile aynı ağacı düzleştirir). Düğüm:
      ("p", Paragraf, bağlam)                         - paragraf ve veri bağlamı
      ("tablo", Tablo, [(Satir, [hücre ağacı, ...])]) - her satır, her hücre kendi ağacı

    `veri` (şablon belgenin `<data>` kökü) verilirse grup blokları ve `dataRow` satırları
    verideki örnek sayısınca genişletilir; verilmezse (normal UDF) ağaç belgedeki gibidir.
    Üstbilgi/altbilgi (`Bolum`) atlanır - çağıran onları ayrıca verir."""
    def _cocuklar(dl: list, ctx) -> list:
        out: list = []
        i = 0
        while i < len(dl):
            d = dl[i]
            gn = (d.nitelikler.get("GroupName")
                  if veri is not None and isinstance(d, Paragraf) else None)
            if gn:
                j = i
                while j < len(dl) and isinstance(dl[j], Paragraf) \
                        and dl[j].nitelikler.get("GroupName") == gn:
                    j += 1
                adlar = _sablon_adlari(gn)
                for b in [e for e in ctx.iter() if e.tag in adlar]:
                    out.extend(("p", p, b) for p in dl[i:j])
                i = j
                continue
            if isinstance(d, Paragraf):
                out.append(("p", d, ctx))
            elif isinstance(d, Tablo):
                satirlar = []
                for s in d.satirlar:
                    ad = s.nitelikler.get("rowName")
                    baglamlar = ([e for e in ctx.iter() if e is not ctx and e.get("rowName") == ad]
                                 if veri is not None and s.nitelikler.get("rowType") == "dataRow"
                                 and ad else [])
                    for b in baglamlar or [ctx]:
                        satirlar.append((s, [_cocuklar(h.dugumler, b) for h in s.hucreler]))
                out.append(("tablo", d, satirlar))
            i += 1
        return out

    return _cocuklar(dugumler, veri)


def sablon_metni(ham_xml: str) -> str | None:
    """Şablon belgenin DEĞERLERİ doldurulmuş düz metni; şablon değilse ya da `<data>` boşsa None
    (çağıran düz CDATA okumasına düşer). SAF."""
    veri = sablon_verisi(ham_xml)
    if veri is None:
        return None
    try:
        agac = ET.fromstring(re.search(r"<elements\b.*</elements>", ham_xml, re.DOTALL).group(0))
    except ET.ParseError:
        return None
    cdata = _cdata_cikar(ham_xml)
    out: list[str] = []
    for p, ctx in sablon_paragraflari(_dugumleri_oku(agac), veri):
        parca: list[str] = []
        for k in p.kosular:
            if k.etiket in ("field", "content", "space", "tab"):
                parca.append(sablon_kosu_metni(k, ctx, cdata))
        satir = "".join(parca)
        out.append(satir if satir.endswith("\n") else satir + "\n")
    metin = "".join(out)
    return metin.strip() or None


def _content_xml(veri: bytes) -> str | None:
    """ZIP'li UDF ya da ZIP'siz `content.xml` gövdesinden ham XML; okunamazsa None."""
    try:
        if (veri or b"")[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(veri)) as zf:
                return zf.read("content.xml").decode("utf-8", errors="replace")
        if ciplak_icerik_mi(veri):
            return veri.decode("utf-8", errors="replace")
    except (KeyError, zipfile.BadZipFile, OSError):
        pass
    return None


def udf_sablon_metni(veri: bytes) -> str | None:
    """`sablon_metni` bayt kapısı: ZIP'li UDF ya da ZIP'siz `content.xml` gövdesi. Şablon
    değilse None."""
    ham = _content_xml(veri)
    return sablon_metni(ham) if ham is not None else None


def udf_sablon_verisi(veri: bytes):
    """`sablon_verisi` bayt kapısı (bkz. `udf_sablon_metni`). Şablon değilse None."""
    ham = _content_xml(veri)
    return sablon_verisi(ham) if ham is not None else None


def udf_ayristir(veri: bytes) -> UdfBelge:
    """UDF baytlarını (ZIP) `UdfBelge`'ye çevir. Yan dosyalar KORUNUR (yeniden zip için)."""
    yan: dict[str, bytes] = {}
    ham_xml = ""
    with zipfile.ZipFile(io.BytesIO(veri)) as zf:
        for ad in zf.namelist():
            icerik = zf.read(ad)
            if ad == "content.xml":
                ham_xml = icerik.decode("utf-8")
            else:
                yan[ad] = icerik
    if not ham_xml:
        raise ValueError("Geçersiz UDF: content.xml bulunamadı.")

    # Bildirimi AYNEN al (`?>` sonrasındaki boşluk dahil - bayt eşitliği için önemli).
    kalip = _kalip_cikar(ham_xml)

    metin = _cdata_cikar(ham_xml)
    # CDATA'yı ET'nin yeniden çözmesini engellemek için parse'tan önce boş bırakılabilir;
    # ancak yapı (elements/styles/properties) için ET kullanıyoruz. CDATA metnini regex'ten aldık.
    kok = ET.fromstring(ham_xml)
    format_id = kok.attrib.get("format_id", VARSAYILAN_FORMAT_ID)
    if not metin:
        # Regex tutmadıysa ET'nin content text'ine düş.
        ust_content = kok.find("content")
        metin = (ust_content.text or "") if ust_content is not None else ""

    # Gövde = elements'in DOĞRUDAN <paragraph> çocukları (findall özyinelemez -> header/footer
    # içindeki paragraflar dahil OLMAZ). Üst/alt bilgi AYRI <header>/<footer> öğelerinden okunur.
    dugumler: list = []
    elements_nitelik: dict[str, str] = {}
    elements_acilis = ""
    elements_ayirac = "\n"
    elements_ayiraclar: list = []
    elements = kok.find("elements")
    if elements is not None:
        elements_nitelik = dict(elements.attrib)
        m_el = re.search(r"<elements\b[^>]*>", ham_xml)
        elements_acilis = m_el.group(0) if m_el else ""
        elements_ayirac = _ayirac_bul(_blok_ham(ham_xml, "elements"))
        elements_ayiraclar = _ayiraclari_bul(_blok_ham(ham_xml, "elements"))
        dugumler = _dugumleri_oku(elements, bolum_kabul=True)

    _ham_etiketleri_esle(_blok_ham(ham_xml, "elements"), dugumler)

    # Kuyruk = son koşunun bittiği yerden CDATA sonuna kalan (bkz. `UdfBelge.kuyruk`).
    _kapali_son = max((k.start + k.uzunluk
                       for p in duz_paragraflar(dugumler, bolum_dahil=True)
                       for k in p.kosular), default=0)
    return UdfBelge(
        metin=metin,
        kuyruk=metin[_kapali_son:] if _kapali_son < len(metin) else "",
        dugumler=dugumler,
        format_id=format_id,
        properties_xml=_blok_ham(ham_xml, "properties") or VARSAYILAN_PROPERTIES,
        styles_xml=_blok_ham(ham_xml, "styles") or VARSAYILAN_STYLES,
        yan_dosyalar=yan,
        elements_nitelik=elements_nitelik,
        elements_acilis=elements_acilis,
        elements_ayirac=elements_ayirac,
        elements_ayiraclar=elements_ayiraclar,
        kalip=kalip,
    )


def _dugumleri_oku(kok_el, *, bolum_kabul: bool = False) -> list:
    """Bir öğenin DOĞRUDAN çocuklarını düğüm listesine çevir: `<paragraph>` -> `Paragraf`,
    `<table>` -> `Tablo`, (yalnız `<elements>` düzeyinde) `<header>`/`<footer>` -> `Bolum`.

    Sıra KORUNUR: CDATA offset düzeni belge sırasıdır, düğüm sırası da öyle olmalı - üstbilgi
    ve altbilgi de kendi yerinde kalır."""
    out: list = []
    for ce in kok_el:
        if ce.tag == "paragraph":
            out.append(_paragraf_oku(ce))
        elif ce.tag == "table":
            out.append(_tablo_oku(ce))
        elif bolum_kabul and ce.tag in ("header", "footer"):
            out.append(Bolum(etiket=ce.tag, nitelikler=dict(ce.attrib),
                             dugumler=_dugumleri_oku(ce)))
    return out


def _paragraf_oku(pe) -> Paragraf:
    """Tek `<paragraph>`: offset taşıyan TÜM çocuk öğeleri (content/field/space/tab/image) koşu
    olarak alır ve öğe adını `Kosu.etiket`te saklar."""
    p = Paragraf(nitelikler=dict(pe.attrib))
    for ce in pe:
        if ce.tag not in KOSU_ETIKETLERI:
            continue
        nit = dict(ce.attrib)
        try:
            start = int(nit.pop("startOffset", "0"))
            uzunluk = int(nit.pop("length", "0"))
        except ValueError:
            continue
        p.kosular.append(Kosu(start=start, uzunluk=uzunluk, nitelikler=nit, etiket=ce.tag))
    return p


#: Kendini kapatan koşu etiketleri (ham eşleme için).
_HAM_KOSU_DESEN = re.compile(r"<(?:" + "|".join(KOSU_ETIKETLERI) + r")\b[^>]*/>")


def _ham_etiketleri_esle(elements_blok: str, dugumler: list) -> None:
    """Ham koşu etiketlerini ayrıştırılmış koşularla eşle ve YENİDEN ÜRETİLEMEYENLERİ sakla.

    Eşleme anahtarı `(etiket, startOffset, length)` - belge içinde bir koşuyu tekilleştirir,
    dolayısıyla XML'deki bölüm sırasından bağımsız çalışır. Yeniden üretim baytı birebir
    tutuyorsa hiçbir şey saklanmaz (39 bin koşunun neredeyse tamamı böyle)."""
    if not elements_blok:
        return
    hamlar: dict[tuple, str] = {}
    for m in _HAM_KOSU_DESEN.finditer(elements_blok):
        ham = m.group(0)
        ad = ham[1:].split(None, 1)[0].rstrip('/>')
        o = _HAM_OFFSET.search(ham)
        u = _HAM_UZUNLUK.search(ham)
        if not (o and u):
            continue
        anahtar = (ad, o.group(0), u.group(0))
        if anahtar in hamlar and hamlar[anahtar] != ham:
            hamlar[anahtar] = ""      # belirsiz eşleme: dokunma
        else:
            hamlar.setdefault(anahtar, ham)
    for p in duz_paragraflar(dugumler, bolum_dahil=True):
        for k in p.kosular:
            ham = hamlar.get((k.etiket, f'startOffset="{k.start}"',
                              f'length="{k.uzunluk}"'))
            if ham and ham != _kosu_yeniden(k):
                k.ham = ham


def _kosu_yeniden(k: Kosu) -> str:
    """Koşunun `_paragraf_seri` ile üretilecek hali (ham etiketle karşılaştırmak için)."""
    n = _nitelik_seri(k.nitelikler)
    on = f'{n} ' if n else ''
    return f'<{k.etiket} {on}startOffset="{k.start}" length="{k.uzunluk}" />'


def _tablo_oku(te) -> Tablo:
    """`<table>` -> `<row>` -> `<cell>` ağacı; hücre içinde paragraf VE iç içe tablo olabilir."""
    t = Tablo(nitelikler=dict(te.attrib))
    for se in te.findall("row"):
        satir = Satir(nitelikler=dict(se.attrib))
        for he in se.findall("cell"):
            satir.hucreler.append(Hucre(nitelikler=dict(he.attrib), dugumler=_dugumleri_oku(he)))
        t.satirlar.append(satir)
    return t


#: Bir düğümün kapanışından SONRA gelen satır sonu = düğümler arası ayıraç. Blokta geçen
#: başka satır sonlarına bakılmaz: bunların çoğu öznitelik DEĞERİNİN içindedir (gömülü
#: görselin `imageData` base64'ü tek başına on binlerce satır sonu taşıyabiliyor) ve
#: ayıraçla ilgisi yoktur.
_AYIRAC_DESEN = re.compile(r"</(?:paragraph|table|header|footer)>\n")


#: `<elements>` içindeki HER etiket (açılış / kapanış / kendini kapatan).
_HAM_ETIKET_DESEN = re.compile(r"<(/?)([A-Za-z][\w.-]*)\b[^>]*?(/?)>", re.DOTALL)


def _ayiraclari_bul(elements_blok: str) -> list:
    """Üst düzey düğümlerin ARDINDAN gelen boşlukları sırayla çıkar (bkz.
    `UdfBelge.elements_ayiraclar`).

    Derinlik sayacıyla yürünür: kendini kapatan koşular (`<content ... />`) derinliği
    değiştirmez, `<paragraph>`/`<table>`/`<row>`/`<cell>`/`<header>`/`<footer>` değiştirir."""
    ic = _elements_ici(elements_blok)
    if not ic:
        return []
    ayiraclar: list = []
    derinlik = 0
    for m in _HAM_ETIKET_DESEN.finditer(ic):
        kapanis, _ad, kendini_kapatan = m.group(1), m.group(2), m.group(3)
        if kendini_kapatan:
            continue
        if kapanis:
            derinlik -= 1
            if derinlik == 0:
                son = ic[m.end():]
                bosluk = son[:len(son) - len(son.lstrip())]
                if m.end() + len(bosluk) < len(ic):   # son düğümden sonrası ayıraç değil
                    ayiraclar.append(bosluk)
        else:
            derinlik += 1
    return ayiraclar


def _elements_ici(elements_blok: str) -> str:
    """`<elements ...>` ile `</elements>` arasındaki içerik; baş/son satır sonu atılır."""
    if not elements_blok:
        return ""
    m = re.match(r"<elements\b[^>]*>", elements_blok)
    if not m:
        return ""
    ic = elements_blok[m.end():]
    if ic.endswith("</elements>"):
        ic = ic[:-len("</elements>")]
    return ic.strip("\n")


def _ayirac_bul(elements_blok: str) -> str:
    """`<elements>` bloğundaki düğüm ARASI ayıracı belgeden öğren ("" ya da satır sonu)."""
    if not elements_blok:
        return "\n"
    return "\n" if _AYIRAC_DESEN.search(_elements_ici(elements_blok)) else ""


def _blok_ham(ham_xml: str, etiket: str) -> str:
    """<etiket>...</etiket> bloğunu HAM olarak (kendi etiketleri dahil) döndür; yoksa ''."""
    m = re.search(rf"(<{etiket}\b.*?</{etiket}>|<{etiket}\b[^>]*/>)", ham_xml, re.DOTALL)
    return m.group(1) if m else ""


def alanlari_listele(belge: UdfBelge) -> list[Alan]:
    """Metindeki tüm `{{...}}` yer tutucularını (doldurulabilir alan) sırayla döndür.

    bold/grup bağlamı, yer tutucunun başladığı offseti kapsayan koşudan/paragraftan türetilir."""
    alanlar: list[Alan] = []
    for m in DEGISKEN_DESEN.finditer(belge.metin):
        start, bitis = m.start(), m.end()
        bold, grup = _baglam_bul(belge, start)
        alanlar.append(Alan(ad=m.group(1), start=start, uzunluk=bitis - start, bold=bold, grup=grup))
    return alanlar


def _baglam_bul(belge: UdfBelge, offset: int) -> tuple[bool, str]:
    """Verilen offseti kapsayan koşunun bold'u + paragrafın Alignment'ı."""
    for p in belge.paragraflar:
        for k in p.kosular:
            if k.start <= offset < k.start + k.uzunluk:
                bold = str(k.nitelikler.get("bold", "false")).lower() == "true"
                return bold, p.nitelikler.get("Alignment", "")
    return False, ""


# --- Doldurma (şablon -> belge) ---


def _deger_coz(ad: str, degiskenler: dict[str, str]) -> str | None:
    """Bir değişken adını değere çevir: önce açık sözlük, sonra özel (türetilmiş) değerler.

    None dönerse yer tutucu OLDUĞU GİBİ bırakılır (bilinmeyen alan patlatmaz - şablon dayanıklı)."""
    if ad in degiskenler and degiskenler[ad] is not None:
        return str(degiskenler[ad])
    return ozel_deger(ad)  # tek kaynak (belge.render_sablon ile aynı türetilmiş küme)


def udf_doldur(sablon: bytes, degiskenler: dict[str, str]) -> bytes:
    """Şablon UDF'i değişkenlerle doldurup GEÇERLİ UDF baytları üret.

    Algoritma: `{{...}}` alanlarını bul; TERS offset sırasında (sağdan sola) metne splice et;
    her değişimde koşu offsetlerini uzunluk farkıyla kaydır (span eden koşunun uzunluğunu düzelt).
    Ters sıra sayesinde erken offsetler işlem boyunca geçerli kalır. content.xml ELLE string
    olarak yazılır (ET.tostring DEĞİL) -> CDATA/newline bozulmaz."""
    belge = udf_ayristir(sablon)
    alanlar = alanlari_listele(belge)

    # Aynı offsette çift kayıt olmaz (yer tutucular ayrık) ama garanti: offset->ilk alan.
    goruldu: set[int] = set()
    benzersiz: list[Alan] = []
    for a in alanlar:
        if a.start in goruldu:
            continue
        goruldu.add(a.start)
        benzersiz.append(a)

    metin = belge.metin
    # Koşuları topla (mutasyon güvenli). CDATA TEK süreklidir ([üstbilgi][gövde][altbilgi]);
    # bir gövde alanı doldurulunca ALTBİLGİ koşuları da (offsetleri gövdenin SAĞINDA) kaymalı ->
    # üst+gövde+alt koşularının HEPSİ kaydırma listesine girer (Faz 194; aksi halde header/footer'lı
    # UDF'te altbilgi offseti kayardı). Kaydırma mantığı offset konumuna göre genel çalışır.
    kosular = [k for p in belge.tum_paragraflar for k in p.kosular]

    for a in sorted(benzersiz, key=lambda x: x.start, reverse=True):
        yeni = _deger_coz(a.ad, degiskenler)
        if yeni is None:
            continue  # bilinmeyen/verilmeyen alan: yer tutucu kalır
        alan_bitis = a.start + a.uzunluk
        fark = len(yeni) - a.uzunluk
        metin = metin[: a.start] + yeni + metin[alan_bitis:]
        if fark == 0:
            continue
        for k in kosular:
            k_bitis = k.start + k.uzunluk
            if k.start >= alan_bitis:
                k.start += fark  # koşu tümüyle alanın SAĞINDA -> kaydır
            elif k_bitis <= a.start:
                continue  # koşu alandan tamamen ÖNCE -> dokunma
            elif k.start <= a.start and k_bitis >= alan_bitis:
                k.uzunluk += fark  # koşu alanı KAPSIYOR -> uzunluğu düzelt
            elif k.start > a.start:
                # koşu yer tutucunun İÇİNDE başlıyor -> başlangıcı yeni değerin sonuna çek
                yeni_bitis = a.start + len(yeni)
                son = (k_bitis + fark) if k_bitis >= alan_bitis else yeni_bitis
                k.start = yeni_bitis
                k.uzunluk = max(0, son - yeni_bitis)
            else:
                # koşu alandan ÖNCE (ya da başında) başlayıp İÇİNDE bitiyor -> yeni değeri
                # KAPSAYACAK şekilde uzat (eskiden a.start'a kırpılıyordu; yer tutucu iki koşuya
                # bölünmüşse dolgu değeri HİÇBİR koşunun kapsamında kalmıyordu -> UYAP editöründe
                # biçimsiz/tanımsız bölge. Sol koşu değeri kapsar, sağ koşu değer SONUNDAN sürer
                # -> bitişiklik değişmezliği korunur).
                k.uzunluk = (a.start + len(yeni)) - k.start

    belge.metin = metin
    # HAM: belge DIŞARIDAN geldi (`udf_ayristir`), yalnız değerleri değişti. Satır sonu düzeni ve
    # kapsanmayan aralıkları kendisine aittir; kendi yazma disiplinimizi dayatmak onu reddetmek
    # olurdu (gerçek UYAP evrakının çoğu o kuralları sağlamıyor).
    return _udf_seri(belge, ham=True)


# --- Sekme durağı: künye ("ETİKET\t: değer") satırlarının etiket kolonu -------------------
# SORUN (canlı kök neden 2026-09-04, kullanıcının elle hizaladığı iki dilekçe karşılaştırıldı):
# ürettiğimiz UDF paragrafı `TabSet` YAZMIYORDU, sekme UYAP editörünün VARSAYILAN merdivenine
# düşüyordu. Durak o zaman etiketin PİKSEL genişliğine göre seçilir: "DAVACI" (50 pt) bir
# hücreye, "VEKİLLERİ" (68 pt) çoğu zaman aynısına ama "MÜŞTEKİ VEKİLİ" (104 pt) BİR SONRAKİNE
# oturur - blok bozulur ve kullanıcı her dilekçede sekme sayısını elle ayarlamak zorunda kalır.
# ÇÖZÜM: durağı TAHMİN ETME, YAZ. Paragrafa tek bir sol durak yazılır ve satırda tek sekme
# bulunur; etiket uzunluğu artık kolonun yerini DEĞİŞTİREMEZ. Bu UYAP'ın kendi mahkeme
# çıktısının deseni: `indirilenler/` altındaki 124 gerçek evrakta 3548 `TabSet` kullanımı var
# (duruşma tutanağı `TabSet="254.0:0:0"`, gerekçeli karar `TabSet="71.0:2:0"` - hepsinde satır
# başına TEK sekme). Biçim `pozisyon:hizalama:leader` (Java Swing TabStop; pozisyon PUNTO,
# hizalama 0=sol). Künye değerinin SARMA satırını kolona hizalayan asılı girinti ise ayrıca
# `Hanging` ile yazılır (aşağıda `_tabset_damga` sonu, Faz 2): işaret sözleşmesi gerçek UYAP
# evrakından doğrulandı (POZİTİF değer + `LeftIndent="0.0"` + yakın `TabSet` durağı). Ters
# yazılırsa TÜM paragraf 4.6 cm içeri kayar (bkz. RULES: alan adını/anlamını tahmin etme).

#: 1 cm kaç punto? UDF ölçüleri PUNTOdur (`pageFormat leftMargin="56.69"` = 2 cm).
CM_PT = 28.346457
#: Etiket kolonu: 4.6 cm. `udf_html` önizlemesinin `tab-size:4.6cm`i ve
#: `server._belge_yazdir_html`in `.belge-etiket{min-width:4.6cm}`i ile AYNI değer - editör,
#: önizleme, baskı ve UDF tek kolona oturur. Değiştirilirse DÖRDÜ birden değişmeli.
ETIKET_KOLONU_PT = round(4.6 * CM_PT, 2)
#: Etiketin duraktan önce bırakacağı en az nefes payı (kerning + ölçüm sapması dahil).
ETIKET_PAY_PT = 6.0
#: Kolon genişletilirken yuvarlanacak adım (yarım santim).
ETIKET_ADIM_PT = CM_PT / 2
#: Kolonun genişleyebileceği ÜST SINIR: 7 cm. Sınır yokken tek bir uzun etiket ("DAVA KONUSU
#: VERGİNİN NEV'İ VE MİKTARI" = 268 pt) bloğu 10.5 cm'ye itiyor, A4'ün 17 cm yazı alanında
#: değerlere 6.5 cm kalıyordu (föy 10454, 22.09.2026). Sığmayan etiket kolonu itmez, SARILIR
#: (bkz. `kunye_duzenle`).
ETIKET_KOLONU_AZAMI_PT = round(7.0 * CM_PT, 2)
#: A4 yazı alanı (21 cm - 2 x 2 cm kenar): uzun künye değerinin satır sayısı tahmini için.
YAZI_ALANI_PT = round(17.0 * CM_PT, 2)
#: Değeri bu kadar satırı aşan künye paragrafı ASILI GİRİNTİ almaz: "DAVA KONUSU" gibi bir
#: sayfalık değer kolonun sağındaki dar şeride hapsolup sayfalarca akıyordu.
ASILI_AZAMI_SATIR = 3

#: Times New Roman **Bold** 12 pt karakter genişlikleri (punto). GÖMÜLÜ: çalışma anında font
#: dosyası okunmaz - `udf` SAF kalır, ölçüm işletim sisteminden bağımsız ve testte belirlenimci
#: olur. Kalın ölçü kullanılır çünkü künye etiketi konvansiyon gereği kalındır ve kalın DAHA
#: GENİŞtir; ince etiket için kolon yalnız fazladan nefes almış olur (kolon dar kalmaz).
#: Üretim: `ImageFont.truetype("timesbd.ttf", 1200).getlength(ch)/100`.
_TNR_BOLD_12: dict[str, float] = {
    ' ': 3.0, '!': 4.0, '"': 6.66, '#': 6.0, '$': 6.0, '%': 12.0,
    '&': 10.0, "'": 3.33, '(': 4.0, ')': 4.0, '*': 6.0, '+': 6.84,
    ',': 3.0, '-': 4.0, '.': 3.0, '/': 3.33, '0': 6.0, '1': 6.0,
    '2': 6.0, '3': 6.0, '4': 6.0, '5': 6.0, '6': 6.0, '7': 6.0,
    '8': 6.0, '9': 6.0, ':': 4.0, ';': 4.0, '<': 6.84, '=': 6.84,
    '>': 6.84, '?': 6.0, '@': 11.16, 'A': 8.67, 'B': 8.0, 'C': 8.67,
    'D': 8.67, 'E': 8.0, 'F': 7.33, 'G': 9.33, 'H': 9.33, 'I': 4.67,
    'J': 6.0, 'K': 9.33, 'L': 8.0, 'M': 11.33, 'N': 8.67, 'O': 9.33,
    'P': 7.33, 'Q': 9.33, 'R': 8.67, 'S': 6.67, 'T': 8.0, 'U': 8.67,
    'V': 8.67, 'W': 12.0, 'X': 8.67, 'Y': 8.67, 'Z': 8.0, '[': 4.0,
    '\\': 3.33, ']': 4.0, '^': 6.97, '_': 6.0, '`': 4.0, 'a': 6.0,
    'b': 6.67, 'c': 5.33, 'd': 6.67, 'e': 5.33, 'f': 4.0, 'g': 6.0,
    'h': 6.67, 'i': 3.33, 'j': 4.0, 'k': 6.67, 'l': 3.33, 'm': 10.0,
    'n': 6.67, 'o': 6.0, 'p': 6.67, 'q': 6.67, 'r': 5.33, 's': 4.67,
    't': 4.0, 'u': 6.67, 'v': 6.0, 'w': 8.67, 'x': 6.0, 'y': 6.0,
    'z': 5.33, '{': 4.73, '|': 2.64, '}': 4.73, '~': 6.24, 'Â': 8.67,
    'Ç': 8.67, 'Î': 4.67, 'Ö': 9.33, 'Û': 8.67, 'Ü': 8.67, 'â': 6.0,
    'ç': 5.33, 'î': 3.33, 'ö': 6.0, 'û': 6.67, 'ü': 6.67, 'Ğ': 9.33,
    'ğ': 6.0, 'İ': 4.67, 'ı': 3.33, 'Ş': 6.67, 'ş': 4.67,
}
#: Tabloda olmayan karakter (ölçüm yerine kaba tahmin): büyük harf ortalaması.
_TNR_VARSAYILAN = 8.0


def metin_genisligi(metin: str) -> float:
    """`metin`in Times New Roman Bold 12 pt genişliği (punto; SAF, yaklaşık - kerning yok)."""
    return round(sum(_TNR_BOLD_12.get(ch, _TNR_VARSAYILAN) for ch in str(metin or "")), 2)


def etiket_kolonu(satirlar, taban: float | None = None) -> float:
    """Künye satırlarına bakıp SEKME DURAĞINI seç (punto; SAF).

    Sekme taşıyan her satırın İLK sekmeden önceki kısmı bir etikettir. Hepsi `taban` kolonun
    (Ayarlar > UDF Belge Ayarları, varsayılan 4.6 cm) içine sığıyorsa kolon odur - belge belgeye
    kaymasın, aynı ofisin dilekçeleri aynı kolonu kullansın. Sığmayan varsa ("İTİRAZA CEVAP
    TARİHİ" = 143 pt) kolon o bloğun TAMAMI için yarım santim adımlarla genişletilir: tek bir
    uzun etiket yüzünden yalnız o satırın kayması, blok hizasının bozulması demektir.

    Genişleme `ETIKET_KOLONU_AZAMI_PT` (ya da daha genişse taban) ile SINIRLIDIR: onu aşan
    etiket kolonu sınıra kadar iter, `kunye_duzenle` onu kolona sığacak satırlara sarar."""
    tab = ETIKET_KOLONU_PT if taban is None else float(taban)
    azami = max(tab, ETIKET_KOLONU_AZAMI_PT)
    en_genis = 0.0
    for s in satirlar:
        s = str(s or "")
        if "\t" not in s:
            continue
        en_genis = max(en_genis, metin_genisligi(s.split("\t", 1)[0]))
    gerek = en_genis + ETIKET_PAY_PT
    if gerek <= tab:
        return tab
    return min(round(math.ceil(gerek / ETIKET_ADIM_PT) * ETIKET_ADIM_PT, 2), azami)


def etiket_kolonu_cm(kolon: float | None = None) -> float:
    """Künye kolonunun CM karşılığı - CSS yüzeylerinin (önizleme, baskı, editör) TEK KAYNAĞI.

    UDF `TabSet`i punto yazar, CSS ise cm okur; iki birim arasındaki çeviri TEK yerde durur.
    Eskiden dört yüzeyde `4.6cm` elle yazılıydı ve biri güncellenmeyince editörde görülen hiza
    belgenin gerçek hizası olmuyordu."""
    return round((ETIKET_KOLONU_PT if kolon is None else float(kolon)) / CM_PT, 3)


def tabset_nitelik(kolon: float) -> str:
    """`TabSet` öznitelik değeri: tek SOL durak (`pozisyon:hizalama:leader`)."""
    return "%s:0:0" % round(float(kolon), 2)


#: Sekme çevresindeki boşluk + ardışık sekmeler. TEK sekmeye indirilir: ikinci sekmenin
#: gideceği durak YOKTUR (tek durak yazıyoruz) ve editör onu kendi varsayılanıyla atar -
#: kolon yine kayar. Elle hizalanmış belgelerdeki "DOSYA NO \t: " artığı da böyle temizlenir.
_SEKME_NORM = re.compile(r"[ \t]*\t[ \t]*")


def sekme_normalize(metin: str) -> str:
    """Sekme öbeğini TEK sekmeye indir (çevresindeki boşluklar dahil; SAF)."""
    return _SEKME_NORM.sub("\t", str(metin))


def etiket_sar(etiket: str, sinir: float) -> list[tuple[int, int]]:
    """Etiketi `sinir` punto genişliğe sığan satırlara böl; satırların `etiket` içindeki
    (başlangıç, bitiş) aralıkları döner (SAF). Tek başına sığmayan kelime kendi satırıdır.

    SONDAN doldurulur: iki noktayı taşıyan SON satır dolu kalır, artık baştaki satıra düşer
    ("DAVA KONUSU / VERGİNİN NEV'İ VE MİKTARI"; baştan doldurunca "... NEV'İ / VE MİKTARI")."""
    satirlar: list[tuple[int, int]] = []
    bas = son = None
    for m in reversed(list(re.finditer(r"\S+", etiket))):
        if bas is None:
            bas, son = m.start(), m.end()
        elif metin_genisligi(etiket[m.start():son]) <= sinir:
            bas = m.start()
        else:
            satirlar.append((bas, son))
            bas, son = m.start(), m.end()
    if bas is not None:
        satirlar.append((bas, son))
    return satirlar[::-1]


def _pg_dilim(pg: dict, a: int, b: int) -> dict:
    """Paragraf sözlüğünün [a, b) karakter aralığı; koşu biçimleri korunur (SAF)."""
    kosular = pg.get("kosular")
    if kosular is None:
        return {**pg, "metin": _dugum_metni(pg)[a:b]}
    yeni, konum = [], 0
    for r in kosular:
        t = str(r.get("metin", ""))
        x, y = max(a, konum), min(b, konum + len(t))
        if x < y:
            yeni.append({**r, "metin": t[x - konum:y - konum]})
        konum += len(t)
    return {**pg, "kosular": yeni, "metin": "".join(r["metin"] for r in yeni)}


def kunye_duzenle(dugumler: list, kolon: float) -> list:
    """Künye bloğunu seçilen kolona oturt (SAF; yalnız ÜRETİM kipinde, ham düğüme dokunmaz).

    1. Kolona sığmayan etiket SARILIR: baştaki parçaları sekmesiz ayrı satırlara iner, son
       parçası sekme + değerle kalır. Böylece iki nokta kolondan kaymaz (UYAP tek durağı aşan
       sekmeyi birkaç puntoluk boşluğa çevirir) ve kolon da etiket yüzünden genişlemez.
    2. Künye satırının hemen altındaki GİRİNTİLİ devam satırı (adres, "KDV: ..." gibi) kolona
       hizalanır. YZ taslağı buraya sabit 5 cm yazıyordu; kolon başka yerdeyse satır değerin
       hizasından kopuyordu."""
    sinir = kolon - ETIKET_PAY_PT
    out: list = []
    devam = False
    for d in dugumler:
        if not isinstance(d, dict) or "tablo" in d or "bolum" in d or d.get("nitelik") is not None:
            out.append(d)
            devam = False
            continue
        for pg in _satirlara_bol(d):
            metin = _dugum_metni(pg)
            if "\t" in metin:
                etiket = metin.split("\t", 1)[0]
                parcalar = etiket_sar(etiket, sinir)
                if metin_genisligi(etiket) > sinir and len(parcalar) > 1:
                    for a, b in parcalar[:-1]:
                        out.append({**_pg_dilim(pg, a, b), "hizala": 0})
                    pg = _pg_dilim(pg, parcalar[-1][0], len(metin))
                out.append(pg)
                devam = True
                continue
            if devam and metin.strip() and float(pg.get("solGirinti") or 0) > 0:
                pg = {**pg, "solGirinti": kolon}
            else:
                devam = False
            out.append(pg)
    return out


# --- Üretim (programatik) ---


def duz_metin_udf(metin: str, *, family: str = "Times New Roman", size: int = 12) -> bytes:
    """Düz metinden UDF üret: her satır bir <paragraph> ve metni '\\n' ile BİTER.

    Boş satır tek başına '\\n'dir (length=1 koşu). CDATA uzunluğu == koşuların bitişik toplamı
    DEĞİŞMEZ (round-trip güvenli); `dogrula` yazmadan ÖNCE bunu denetler."""
    paragraflar: list[Paragraf] = []
    metin_parcalari: list[str] = []
    imlec = 0
    kolon = etiket_kolonu(metin.split("\n"))
    for pg in kunye_duzenle([{"metin": s} for s in metin.split("\n")], kolon):
        pg.update({"hizala": 0, "family": family, "size": size, "etiket_kolonu": kolon})
        p, parca = _paragraf_dict_kosu(pg, imlec)
        paragraflar.append(p)
        metin_parcalari.append(parca)
        imlec += len(parca)
    return _udf_seri(UdfBelge(metin="".join(metin_parcalari), dugumler=paragraflar))


# UYAP altbilgi sayfa-numarası özniteliği (Boş Dilekçe referansından birebir). `<footer>`
# öğesine eklenince UYAP her sayfaya otomatik sayfa numarası basar (metinden BAĞIMSIZ).
SAYFA_NO_NITELIK = {
    "pageNumber-spec": "BSP32_72",
    "color-boundary": "-1",
    "pageNumber-color": "-16777216",
    "pageNumber-fontFace": "Arial",
    "pageNumber-fontSize": "11",
    "pageNumber-foreStr": "",
    "pageNumber-pageStartNumStr": "",
}


def _kosu_nitelik(r: dict, family: str, size: str) -> dict[str, str]:
    """Bir koşu (run) sözlüğünden UDF öznitelikleri. bold/italic DAİMA yazılır (UYAP default
    stiliyle uyum), underline yalnız True iken (Faz 238; canlı UYAP doğrulaması gerekir - offset
    değişmezliği öznitelik adından bağımsızdır).

    KAYIPSIZ KİP (v141): koşu sözlüğünde `nitelik` varsa o sözlük AYNEN döner - dışarıdan gelen
    bir UDF'in koşu öznitelikleri (`resolver`, `foreground`, `fieldName`, `width`/`height`,
    `superscript` ...) türetilmez, TAŞINIR. Bilmediğimiz bir öznitelik de korunur: UYAP'ın kendi
    çıktısı otoritedir, bizim eşleme tablomuz değil."""
    ham = r.get("nitelik")
    if ham is not None:
        return {str(k): str(v) for k, v in ham.items()}
    nit = {
        "family": str(r.get("family", family)),
        "size": str(r.get("size", size)),
        "bold": "true" if r.get("bold") else "false",
        "italic": "true" if r.get("italic") else "false",
    }
    if r.get("underline"):
        nit["underline"] = "true"
    # Faz 3 KOŞU BİÇİMİ. `bold`/`italic` DAİMA yazılır (UYAP varsayılan stiliyle uyum), bunlar
    # ise yalnız True iken - gerçek evrakta hiç kullanılmıyorlar ve her koşuya `false` basmak
    # dosyayı UYAP'ın kendi çıktısından uzaklaştırırdı.
    for anahtar, oznitelik in (("ustuCizili", "StrikeThrough"), ("ustSimge", "Superscript"),
                               ("altSimge", "Subscript")):
        if r.get(anahtar):
            nit[oznitelik] = "true"
    # Renk: UDF `foreground` JAVA SIGNED INT'tir (`java.awt.Color.getRGB()`), CSS hex değil.
    renk = renk_udf(r.get("renk"))
    if renk is not None:
        nit["foreground"] = str(renk)
    return nit


def renk_udf(hex_renk: object) -> int | None:
    """`#rrggbb` -> UDF `foreground` (Java signed int; SAF). Çözülemezse None.

    `udf_html.renk_css`in TERSİdir: orada okunan biçim burada yazılır. Java `Color.getRGB()`
    ARGB döndürür ve alfa 0xFF'tir; sayı 32 bit İŞARETLİ olduğundan sonuç negatiftir
    (ör. kırmızı `#fd0000` -> -196608)."""
    ham = str(hex_renk or "").strip().lstrip("#")
    if len(ham) != 6:
        return None
    try:
        rgb = int(ham, 16)
    except ValueError:
        return None
    n = 0xFF000000 | rgb
    return n - 0x100000000 if n >= 0x80000000 else n


def _satir_normalize(metin) -> str:
    """CR/CRLF -> LF (tek satır sonu karakteri; offset sayımı tek biçimli kalsın)."""
    return str(metin).replace("\r\n", "\n").replace("\r", "\n")


def _satirlara_bol(pg: dict) -> list[dict]:
    """Metninde satır sonu taşıyan paragraf sözlüğünü SATIR BAŞINA bir sözlüğe böl.

    Bir paragraf sözlüğü UDF'te TEK satırdır; içine gömülü '\\n' <paragraph> öğesi olmadan görünen
    bir satır yaratır ve o satır kendi hizalamasını ALAMAZ. Bölme paragraf özniteliklerini
    (hizala/family/size) ve koşu biçimlerini KORUR."""
    if pg.get("nitelik") is not None:
        # Kayıpsız kip: paragrafın özgün karakter aralığı zaten TEK satırdır ve öznitelikleri
        # ham taşınır. Bölmek özgün döşemeyi (offset tilingi) bozar -> dokunma.
        return [pg]
    kosular = pg.get("kosular")
    if kosular is None:
        govde = _satir_normalize(pg.get("metin", ""))
        if "\n" not in govde:
            return [pg]
        return [{**pg, "metin": s} for s in govde.split("\n")]
    satirlar: list[list[dict]] = [[]]
    bolundu = False
    for r in kosular:
        for i, t in enumerate(_satir_normalize(r.get("metin", "")).split("\n")):
            if i:
                satirlar.append([])
                bolundu = True
            if t:
                satirlar[-1].append({**r, "metin": t})
    if not bolundu:
        return [pg]
    return [{**pg, "kosular": s, "metin": "".join(x["metin"] for x in s)} for s in satirlar]


def _paragraf_dict_kosu(pg: dict, imlec: int) -> tuple[Paragraf, str]:
    """Bir paragraf sözlüğünü verilen imleçten başlayan koşu(lar)a çevir. İki biçim:
      - Tek koşu (geriye dönük): {metin, hizala?, bold?, family?, size?}
      - Çoklu koşu (kısmi biçim; Faz 238): {kosular:[{metin, bold?, italic?, underline?}...], hizala?}

    Metin ve koşular EŞZAMANLI üretilir (imleç ilerledikçe span kurulur - "önce metni kur, sonra
    offsetleri hesapla" YAPILMAZ). Her paragrafın metni `PARAGRAF_SONU` ('\\n') ile BİTER: görünen
    paragrafı Swing modelinde bu karakter yaratır, <paragraph> yalnız hizalamayı o aralığa bağlar.
    Satır sonu SON koşunun uzunluğuna dahildir; metinsiz paragraf tek '\\n' koşusudur (length=1).
    Boş koşular atlanır (sıfır uzunluklu koşu yazılmaz). CDATA uzunluğu == koşuların bitişik
    toplamı DEĞİŞMEZ."""
    family = str(pg.get("family", "Times New Roman"))
    size = str(pg.get("size", 12))
    # KAYIPSIZ KİP (v141): dışarıdan gelen bir UDF'ten okunan paragrafın öznitelikleri HAM taşınır
    # (`TabSet`/`ListId`/`Hanging`/`resolver`/`GroupName` ... hiçbirinin CSS karşılığı YOK; eşleme
    # tablosu yazmak kayıp demekti). `nitelik` yoksa eski türetim: hizalama + sıfır girinti.
    ham_p = pg.get("nitelik")
    if ham_p is not None:
        p = Paragraf(nitelikler={str(k): str(v) for k, v in ham_p.items()})
    else:
        p = Paragraf(nitelikler={
            "Alignment": str(pg.get("hizala", 0)),
            "LeftIndent": olcu(pg.get("solGirinti") or 0.0),
            "RightIndent": olcu(pg.get("sagGirinti") or 0.0),
        })
        # Faz 2: paragraf BİÇİMİ. Yalnız SIFIRDAN FARKLI olanlar yazılır - belge geneli
        # varsayılan `<styles>` gövde stilinde durur (üç katmanlı model) ve her paragrafa
        # `SpaceAbove="0.0"` basmak o varsayılanı EZERDİ. Anahtar adları `belge.PARAGRAF_BICIM`
        # tablosundan gelir; ikinci bir eşleme yazılmaz.
        for anahtar, oznitelik in (("ilkSatir", "FirstLineIndent"), ("asiliGirinti", "Hanging"),
                                   ("satirAraligi", "LineSpacing"), ("boslukUst", "SpaceAbove"),
                                   ("boslukAlt", "SpaceBelow")):
            try:
                deger = float(pg.get(anahtar) or 0.0)
            except (TypeError, ValueError):
                continue
            if deger:
                p.nitelikler[oznitelik] = olcu(deger)
    # Paragraf metni normalde PARAGRAF_SONU ile BİTER. Belgenin EN SON paragrafı UYAP çıktısında
    # satır sonu taşımayabilir; `satir_sonu=False` o aralığı olduğu gibi bırakır - uydurma bir
    # satır sonu eklemek metni ve ondan sonraki TÜM offsetleri kaydırırdı.
    sonu = PARAGRAF_SONU if pg.get("satir_sonu", True) else ""
    # Kayıpsız kipte CRLF normalizasyonu da YAPILMAZ: özgün CDATA neyse odur (normalize etmek
    # uzunluğu değiştirip offset döşemesini kaydırırdı).
    # Sekme öbeği TEK sekmeye indirilir (bkz. `sekme_normalize`): tek durak yazdığımız için
    # ikinci bir sekmenin oturacağı yer yoktur. Kayıpsız kipte metne DOKUNULMAZ.
    norm = ((lambda t: str(t)) if ham_p is not None
            else (lambda t: sekme_normalize(_satir_normalize(t))))
    kosular = pg.get("kosular")
    if kosular is not None:
        if ham_p is not None:
            # HAM kipte koşu ELENMEZ: özgün belgede yalnız satır sonundan ibaret bir koşu
            # (uzunluk 1) olabilir; sonu sökülünce metni boşalır ve elenirse paragraf bir koşu
            # eksik geri yazılır. Sıfır uzunluklu özgün koşu da aynen korunur.
            dolu = list(kosular)
            if not dolu:
                # Koşusuz paragraf: metni YOK, offset tüketmez (özgün belgede öyleyse öyle kalır).
                return p, ""
        else:
            dolu = [r for r in kosular if str(r.get("metin", ""))]
        if not dolu:
            # Boş satır = tek satır sonu (dolgu karakteri YOK). Özgün koşunun öznitelikleri varsa
            # KORUNUR - aksi halde boş satırlar varsayılan font/punto ile geri yazılırdı.
            ilk = kosular[0] if kosular else {}
            p.kosular.append(Kosu(start=imlec, uzunluk=len(PARAGRAF_SONU),
                                  nitelikler=_kosu_nitelik(ilk, family, size),
                                  etiket=str(ilk.get("etiket") or "content"),
                                  ham=str(ilk.get("ham") or "")))
            return p, PARAGRAF_SONU
        cur = imlec
        parcalar = []
        for i, r in enumerate(dolu):
            t = norm(r.get("metin", ""))
            if i == len(dolu) - 1:
                t += sonu                 # satır sonu SON koşuya dahil (span'lar bitişik kalsın)
            if ham_p is not None and not t and r.get("ozgun_uzunluk") is not None:
                # Metin tüketmeyen çapa koşu: özgün offset/uzunluğunu korur ve imleci
                # ilerletmez (bkz. `_paragraf_dictle`).
                p.kosular.append(Kosu(start=int(r["ozgun_start"]), uzunluk=int(r["ozgun_uzunluk"]),
                                      nitelikler=_kosu_nitelik(r, family, size),
                                      etiket=str(r.get("etiket") or "content"),
                                      ham=str(r.get("ham") or "")))
                continue
            p.kosular.append(Kosu(start=cur, uzunluk=len(t),
                                  nitelikler=_kosu_nitelik(r, family, size),
                                  etiket=str(r.get("etiket") or "content"),
                                  ham=str(r.get("ham") or "")))
            cur += len(t)
            parcalar.append(t)
        return _tabset_damga(p, "".join(parcalar), pg, ham_p), "".join(parcalar)
    govde = norm(pg.get("metin", "")) + sonu
    p.kosular.append(Kosu(start=imlec, uzunluk=len(govde),
                          nitelikler=_kosu_nitelik(pg, family, size)))
    return _tabset_damga(p, govde, pg, ham_p), govde


def _tabset_damga(p: Paragraf, metin: str, pg: dict, ham_p) -> Paragraf:
    """Sekme taşıyan paragrafa TEK SOL DURAK yaz (künye kolonu; bkz. `etiket_kolonu`).

    Kayıpsız kipte (`ham_p`) DOKUNULMAZ: o paragrafın öznitelikleri özgün belgeden gelir ve
    UYAP'ın kendi durağı bizim varsayılanımızdan otoriterdir. Kolon `belge_uret`in ön paşında
    BLOK BAZINDA hesaplanıp `pg["etiket_kolonu"]`e damgalanır; damga yoksa varsayılan 4.6 cm."""
    if ham_p is not None or "\t" not in metin or "TabSet" in p.nitelikler:
        return p
    kolon = float(pg.get("etiket_kolonu") or ETIKET_KOLONU_PT)
    p.nitelikler["TabSet"] = tabset_nitelik(kolon)
    # İki yana yaslama künye satırında ETİKETİN boşluğunu da açar ("DAVA   KONUSU"); künye
    # satırı sola yaslanır. Ortalı/sağa yaslı (kullanıcı seçimi) paragrafa dokunulmaz.
    if p.nitelikler.get("Alignment") == "3":
        p.nitelikler["Alignment"] = "0"
    # Değeri birkaç satırı aşan paragraf asılı girinti ALMAZ: sarma satırları sol kenardan tam
    # genişlikte akar (yoksa kolonun sağındaki dar şeride hapsolur).
    deger = metin.split("\t", 1)[1]
    uzun = metin_genisligi(deger) > ASILI_AZAMI_SATIR * max(YAZI_ALANI_PT - kolon, 1.0)
    # ASILI GİRİNTİ (Faz 2): künye değerinin SARMA satırı da kolona hizalanır. Uzun bir
    # "KONU : ..." değeri satır sonuna geldiğinde devamı sol kenara düşüyor, künye bloğunun
    # hizası tek satırda bozuluyordu. `Hanging` gerçek UYAP evrakında tam bu desende kullanılır:
    # POZİTİF değer, `LeftIndent="0.0"` ile birlikte ve yakınındaki `TabSet` durağıyla
    # eşleşerek (ör. `Hanging="192.0"` + `TabSet="186.0:0:0"`).
    # Kullanıcı paragrafa kendi asılı girintisini verdiyse (editör "Biçim" paneli) DOKUNULMAZ.
    if not pg.get("asiliGirinti") and not uzun:
        p.nitelikler["Hanging"] = olcu(kolon)
    return p


def paragraf_dictleri(metin: str, paragraflar: list[Paragraf]) -> list[dict]:
    """DÜZ paragraf listesini `belge_uret`in tükettiği sözlük biçimine çevir (tablo YOK).

    Yapılı belgeler için `dugum_dictleri` kullanılır; bu ince sarmalayıcı üst/alt bilgi gibi
    daima düz olan bölümler ve eski çağıranlar içindir."""
    return [_paragraf_dictle(metin, p) for p in paragraflar]


def _paragraf_dictle(metin: str, p: Paragraf) -> dict:
    """Tek `Paragraf` -> sözlük. KAYIPSIZ: paragraf/koşu özniteliklerinin TAMAMI `nitelik`
    anahtarında ham taşınır, koşu metni CDATA'dan offset/uzunlukla kesilir, koşu TÜRÜ (`etiket`)
    korunur.

    Satır sonu SON koşunun uzunluğuna dahildir (bkz. `_paragraf_dict_kosu`); burada SÖKÜLÜR ve
    `satir_sonu` bayrağına yazılır - üretici onu yeniden ekler. Sökülmeseydi her gidiş-dönüşte
    paragraf başına bir satır sonu daha eklenir, belge her turda şişerdi."""
    kosular: list[dict] = []
    for k in sorted(p.kosular, key=lambda x: x.start):
        bas = max(0, k.start)
        r = {"metin": metin[bas:bas + max(0, k.uzunluk)],
             "nitelik": dict(k.nitelikler), "etiket": k.etiket,
             # Metin TÜKETMEYEN koşu (uzunluk <= 0) bir ÇAPADIR: geometrisi kendi başına
             # anlamlıdır ve yeniden döşemeden etkilenmez -> aynen korunur.
             "ozgun_start": k.start, "ozgun_uzunluk": k.uzunluk}
        if k.ham:
            r["ham"] = k.ham        # yeniden üretilemeyen etiket (bkz. `Kosu.ham`)
        kosular.append(r)
    satir_sonu = bool(kosular) and kosular[-1]["metin"].endswith(PARAGRAF_SONU)
    if satir_sonu:
        kosular[-1]["metin"] = kosular[-1]["metin"][:-len(PARAGRAF_SONU)]
    # ÖZGÜN KONUM: paragrafın CDATA'daki yeri. Yeniden yazarken düzen BÖLÜM SIRASINA göre
    # (üst->gövde->alt) değil bu offsete göre kurulur - gerçek UYAP evrakında altbilgi
    # paragrafının offseti gövdenin ORTASINA düşebiliyor (imza satırı) ve bölüm sırasına
    # güvenmek metni yeniden diziyordu.
    # Paragrafın özgün aralığı yalnız METİN TÜKETEN koşulardan hesaplanır: sıfır (hatta gerçek
    # bir evrakta görülen NEGATİF) uzunluklu koşu aralığı yanlış yere taşırdı.
    _dolu = [k for k in p.kosular if k.uzunluk > 0]
    sira = min((k.start for k in _dolu), default=None)
    son = max((k.start + k.uzunluk for k in _dolu), default=None)
    return {
        "nitelik": dict(p.nitelikler),
        "kosular": kosular,
        "satir_sonu": satir_sonu,
        "sira": sira,
        "ozgun_son": son,
        # Geriye dönük özet alanlar (eski tüketiciler `metin`/`hizala` okur).
        "metin": "".join(k["metin"] for k in kosular),
        "hizala": _sayiya(p.nitelikler.get("Alignment"), 0),
    }


def dugum_dictleri(metin: str, dugumler: list) -> list[dict]:
    """Düğüm ağacını (Paragraf | Tablo) `belge_uret`in tükettiği sözlük ağacına çevir.

    `_dugum_kur`un TERSİDİR ve KAYIPSIZDIR. Tablo düğümü `{"tablo": {...}}` anahtarıyla ayrılır
    (paragraf sözlüğünde böyle bir anahtar yoktur)."""
    out: list[dict] = []
    for d in dugumler:
        if isinstance(d, Bolum):
            out.append({"bolum": {"etiket": d.etiket, "nitelik": dict(d.nitelikler),
                                  "dugumler": dugum_dictleri(metin, d.dugumler)}})
        elif isinstance(d, Tablo):
            out.append({"tablo": {
                "nitelik": dict(d.nitelikler),
                "satirlar": [{
                    "nitelik": dict(s.nitelikler),
                    "hucreler": [{"nitelik": dict(h.nitelikler),
                                  "dugumler": dugum_dictleri(metin, h.dugumler)}
                                 for h in s.hucreler],
                } for s in d.satirlar],
            }})
        else:
            out.append(_paragraf_dictle(metin, d))
    return out


def _sayiya(deger, varsayilan: int) -> int:
    try:
        return int(str(deger).strip())
    except (TypeError, ValueError):
        return varsayilan


def _dugum_metni(d: dict) -> str:
    """Bir paragraf sözlüğünün DÜZ metni (tek koşu ya da çoklu koşu biçimi; SAF)."""
    kosular = d.get("kosular")
    if kosular is not None:
        return "".join(str(r.get("metin", "")) for r in kosular)
    return str(d.get("metin", "") or "")


def _dugum_agaci(dugumler: list, kayit: list) -> list:
    """Sözlük ağacından `Paragraf`/`Tablo` İSKELETİNİ kur (offsetler HENÜZ atanmaz).

    Kurulan her paragraf `(Paragraf, sözlük)` çifti olarak `kayit`a eklenir; metin döşemesi
    (`_duzen_yaz`) AYRI bir paştır çünkü döşeme sırası ağaç sırasıyla AYNI OLMAYABİLİR."""
    out: list = []
    for d in dugumler:
        bolum = d.get("bolum") if isinstance(d, dict) else None
        if bolum is not None:
            out.append(Bolum(etiket=str(bolum.get("etiket") or "footer"),
                             nitelikler=dict(bolum.get("nitelik") or {}),
                             dugumler=_dugum_agaci(bolum.get("dugumler") or [], kayit)))
            continue
        tablo = d.get("tablo") if isinstance(d, dict) else None
        if tablo is not None:
            t = Tablo(nitelikler=dict(tablo.get("nitelik") or {}))
            for s in tablo.get("satirlar") or []:
                satir = Satir(nitelikler=dict(s.get("nitelik") or {}))
                for h in s.get("hucreler") or []:
                    satir.hucreler.append(Hucre(
                        nitelikler=dict(h.get("nitelik") or {}),
                        dugumler=_dugum_agaci(h.get("dugumler") or [], kayit)))
                t.satirlar.append(satir)
            out.append(t)
            continue
        for pg in _satirlara_bol(d):
            p = Paragraf()
            kayit.append((p, pg))
            out.append(p)
    return out


def _duzen_yaz(kayit: list, *, ozgun_metin: str | None) -> tuple[str, str]:
    """Paragrafları döşe: her paragrafın koşularını ve offsetlerini yerine koy, CDATA'yı kur.

    Döşeme sırası `sira` (özgün CDATA offseti) varsa ONA göredir, yoksa ağaç sırasıdır.
    `ozgun_metin` verilirse paragraflar arasında HİÇBİR koşunun kapsamadığı aralıklar da
    (bölümler arası tek satır sonu, imza dipnotu) aynen taşınır. Döner: (cdata, kuyruk)."""
    tumu = [(i, p, pg) for i, (p, pg) in enumerate(kayit)]
    if tumu and all(pg.get("sira") is not None for _, _, pg in tumu):
        tumu.sort(key=lambda t: (t[2]["sira"], t[0]))
    parcalar: list[str] = []
    imlec = 0
    ozgun_imlec = 0
    for _, p, pg in tumu:
        if ozgun_metin is not None and pg.get("sira") is not None:
            bosluk = ozgun_metin[ozgun_imlec:pg["sira"]] if pg["sira"] > ozgun_imlec else ""
            if bosluk:
                parcalar.append(bosluk)
                imlec += len(bosluk)
            # İmleç GERİYE gitmez: bozuk bir koşu yüzünden geri çekilirse aradaki metin
            # ikinci kez yazılır (gerçek bir bilirkişi raporunda altbilgi iki kez çıkmıştı).
            ozgun_imlec = max(ozgun_imlec, pg.get("ozgun_son") or pg["sira"])
        yeni_p, parca = _paragraf_dict_kosu(pg, imlec)
        p.nitelikler = yeni_p.nitelikler
        p.kosular = yeni_p.kosular
        parcalar.append(parca)
        imlec += len(parca)
    kuyruk = ""
    if ozgun_metin is not None and ozgun_imlec < len(ozgun_metin):
        kuyruk = ozgun_metin[ozgun_imlec:]
        parcalar.append(kuyruk)
    return "".join(parcalar), kuyruk


def belge_uret(paragraflar: list[dict], *, ust_satirlar: list[dict] | None = None,
               alt_satirlar: list[dict] | None = None, sayfa_no: bool = False,
               sayfa: dict | None = None, properties_xml: str | None = None,
               styles_xml: str | None = None, format_id: str | None = None,
               yan_dosyalar: dict | None = None, footer_nitelik: dict | None = None,
               ust_nitelik: dict | None = None, elements_nitelik: dict | None = None,
               ozgun_metin: str | None = None, kalip: str | None = None,
               elements_acilis: str | None = None,
               elements_ayirac: str | None = None,
               elements_ayiraclar: list | None = None,
               etiket_kolonu_taban: float | None = None) -> bytes:
    """Paragraf sözlüklerinden UDF üret. Her paragraf: {metin, hizala?, bold?, family?, size?}.

    `hizala`: 0=sol 1=orta 2=sağ 3=iki yana yasla (UDF Alignment). Koşular bitişik.
    `ust_satirlar`/`alt_satirlar`: üst/alt bilgi paragrafları (aynı sözlük biçimi) -> `<header>`/
    `<footer>` öğelerine sarılır; CDATA'da üstbilgi GÖVDEDEN ÖNCE, altbilgi SONRA gelir (offset
    sürekli). `sayfa_no`: altbilgiye sayfa numarası özniteliği ekler (metin gerektirmez).

    KAYIPSIZ KİP (v141): liste `{"tablo": {...}}` düğümleri de taşıyabilir ve `nitelik`/`etiket`
    anahtarlarıyla dışarıdan gelen bir UDF birebir geri yazılır; `properties_xml`/`styles_xml`/
    `format_id`/`yan_dosyalar`/`*_nitelik` belgenin KABINI taşır.

    DEĞİŞMEZLİK: her paragrafın metni '\n' ile BİTER (boş paragraf tek '\n'dir); span'lar CDATA'yı
    boşluksuz ve örtüşmesiz döşer. `dogrula` bunu YAZMADAN ÖNCE denetler - bozuksa hiçbir şey
    yazılmaz, `ValueError` fırlar."""
    ust = list(ust_satirlar or [])
    govde = list(paragraflar or [])
    alt = list(alt_satirlar or [])
    if not (ust or govde or alt):
        govde = [{"metin": ""}]
    # HAM kip: düğümlerden biri `nitelik`/`tablo` taşıyorsa yapı DIŞARIDAN gelmiştir (bir UDF
    # okunup geri yazılıyor). O belgenin satır sonu düzeni kendisine aittir; bizim yazma
    # disiplinimizi dayatmak metnini değiştirirdi.
    ham = any(("nitelik" in d or "tablo" in d or "bolum" in d)
              for d in (*ust, *govde, *alt) if isinstance(d, dict))

    # KÜNYE KOLONU tek paşta seçilir (bkz. `etiket_kolonu`): durak paragraf paragraf
    # hesaplansaydı uzun etiketli TEK satır kendi kolonuna kaçar, blok hizası bozulurdu.
    # Üst/alt bilgi kolonu BELİRLEMEZ (ortalanmış tek satırlardır) ama aynı kolonu kullanır.
    # HAM kipte hiç çalışmaz: dışarıdan gelen belgenin kendi `TabSet`i otoriterdir.
    if not ham:
        kolon = etiket_kolonu((satir for d in govde if isinstance(d, dict)
                               for satir in _dugum_metni(d).split("\n")),
                              etiket_kolonu_taban)
        def damga(d):
            return dict(d, etiket_kolonu=kolon) if isinstance(d, dict) else d
        ust = [damga(d) for d in ust]
        govde = [damga(d) for d in kunye_duzenle(govde, kolon)]
        alt = [damga(d) for d in alt]

    # Üstbilgi/altbilgi de birer DÜĞÜMDÜR: gövdenin başına/sonuna eklenir (dışarıdan gelen
    # bir belgede zaten `{"bolum": ...}` olarak listenin içindedir, o zaman burası çalışmaz).
    footer_nit = dict(SAYFA_NO_NITELIK) if sayfa_no else {}
    if footer_nitelik is not None:
        footer_nit = dict(footer_nitelik)
    girdi: list = []
    if ust:
        girdi.append({"bolum": {"etiket": "header", "nitelik": dict(ust_nitelik or {}),
                                "dugumler": ust}})
    girdi.extend(govde)
    if alt or footer_nit:
        girdi.append({"bolum": {"etiket": "footer", "nitelik": footer_nit,
                                "dugumler": alt}})

    # İki paş: (1) düğüm iskeletini kur, (2) metni döşeyip offsetleri ata. Döşeme sırası
    # ağaç sırası OLMAYABİLİR (bkz. `_duzen_yaz`) - bu yüzden ayrı paş.
    kayit: list = []
    dugumler = _dugum_agaci(girdi, kayit)
    cdata, kuyruk = _duzen_yaz(kayit, ozgun_metin=ozgun_metin)

    belge = UdfBelge(
        metin=cdata,
        kuyruk=kuyruk,
        dugumler=dugumler,
        elements_nitelik=dict(elements_nitelik or {}),
    )
    if sayfa is not None:
        # Basit sayfa override (kenar boşlukları vb.); verilmezse varsayılan properties.
        belge.properties_xml = _properties_uret(sayfa)
    # KAYIPSIZ KİP (v141): dışarıdan gelen bir UDF geri yazılırken belgenin KABI da taşınır -
    # sayfa biçimi (`properties`), stil tablosu (`styles`), sürüm (`format_id`) ve content.xml
    # DIŞI zip üyeleri (gömülü görsel, imza). Bunlar gövde metninin parçası değildir ama
    # düşerlerse belge "aynı" olmaz: kenar boşluğu/fontlar varsayılana döner, görsel kaybolur.
    if properties_xml:
        belge.properties_xml = properties_xml
    if styles_xml:
        belge.styles_xml = styles_xml
    if format_id:
        belge.format_id = format_id
    if yan_dosyalar:
        belge.yan_dosyalar = dict(yan_dosyalar)
    if kalip:
        belge.kalip = kalip
    if elements_acilis:
        belge.elements_acilis = elements_acilis
    if elements_ayirac is not None:
        belge.elements_ayirac = elements_ayirac
    if elements_ayiraclar:
        belge.elements_ayiraclar = list(elements_ayiraclar)
    return _udf_seri(belge, ham=ham)



def _properties_uret(sayfa: dict) -> str:
    oz = {
        "mediaSizeName": sayfa.get("mediaSizeName", "1"),
        "leftMargin": sayfa.get("leftMargin", "56.69"),
        "rightMargin": sayfa.get("rightMargin", "56.69"),
        "topMargin": sayfa.get("topMargin", "56.69"),
        "bottomMargin": sayfa.get("bottomMargin", "56.69"),
        "paperOrientation": sayfa.get("paperOrientation", "1"),
        "headerFOffset": sayfa.get("headerFOffset", "20.0"),
        "footerFOffset": sayfa.get("footerFOffset", "20.0"),
    }
    nit = " ".join(f'{k}="{_oznitelik_kacis(str(v))}"' for k, v in oz.items())
    return f"<properties>\n<pageFormat {nit} />\n</properties>"


# --- Seri hale getirme (manuel string; ET.tostring DEĞİL) ---
# (`_oznitelik_kacis` dosyanın BAŞINDA tanımlıdır: `styles_uret` onu MODÜL YÜKLENİRKEN
#  kullanır - burada kalsaydı `VARSAYILAN_STYLES` import anında NameError verirdi.)


def _cdata_sar(metin: str) -> str:
    """Metni CDATA'ya sar; içinde ']]>' geçerse böl (CDATA bölme deyimi)."""
    guvenli = metin.replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{guvenli}]]>"


def _nitelik_seri(nitelikler: dict) -> str:
    """Öznitelik sözlüğü -> `ad="deger"` dizisi (boşsa '')."""
    return " ".join(f'{ad}="{_oznitelik_kacis(str(v))}"' for ad, v in nitelikler.items())


def _etiket_ac(ad: str, nitelikler: dict) -> str:
    n = _nitelik_seri(nitelikler)
    return f"<{ad} {n}>" if n else f"<{ad}>"


def _paragraf_seri(p: Paragraf) -> str:
    """Tek bir <paragraph> düğümünü koşularıyla seri hale getir. Koşunun XML öğe adı
    `Kosu.etiket`ten gelir - UYAP form alanı `<field>`, boşluk `<space>`, sekme `<tab>`, gömülü
    görsel `<image>` olarak geri yazılır (hepsini `<content>` yapmak alanı düz metne çevirirdi)."""
    acilis = _etiket_ac("paragraph", p.nitelikler)
    kosu_parcalari = []
    for k in p.kosular:
        k_nit = _nitelik_seri(k.nitelikler)
        n = f"{k_nit} " if k_nit else ""
        etiket = k.etiket if k.etiket in KOSU_ETIKETLERI else "content"
        if k.ham:
            kosu_parcalari.append(_ham_kosu_seri(k))
            continue
        kosu_parcalari.append(f'<{etiket} {n}startOffset="{k.start}" length="{k.uzunluk}" />')
    return f"{acilis}{''.join(kosu_parcalari)}</paragraph>"


#: Ham etiket içindeki offset/uzunluk; metin değişince güncellenmeli.
_HAM_OFFSET = re.compile(r'startOffset="-?\d+"')
_HAM_UZUNLUK = re.compile(r'length="-?\d+"')


def _ham_kosu_seri(k: Kosu) -> str:
    """Ham koşu etiketini yaz; yalnız offset/uzunluk güncel değerlerle değiştirilir."""
    t = _HAM_OFFSET.sub(lambda m: f'startOffset="{k.start}"', k.ham, count=1)
    return _HAM_UZUNLUK.sub(lambda m: f'length="{k.uzunluk}"', t, count=1)


def _tablo_seri(t: Tablo) -> str:
    """`<table>` ağacını seri hale getir (satır/hücre öznitelikleri ve iç içe tablolar korunur)."""
    parcalar = [_etiket_ac("table", t.nitelikler)]
    for satir in t.satirlar:
        parcalar.append(_etiket_ac("row", satir.nitelikler))
        for h in satir.hucreler:
            parcalar.append(_etiket_ac("cell", h.nitelikler))
            parcalar.append("".join(_dugum_seri(d) for d in h.dugumler))
            parcalar.append("</cell>")
        parcalar.append("</row>")
    parcalar.append("</table>")
    return "".join(parcalar)


def _bolum_seri(b: Bolum) -> str:
    """`<header>` / `<footer>` düğümü (içeriği gövdeyle aynı biçimde yazılır)."""
    return (_etiket_ac(b.etiket, b.nitelikler)
            + "".join(_dugum_seri(d) for d in b.dugumler)
            + f"</{b.etiket}>")


def _dugum_seri(d) -> str:
    if isinstance(d, Tablo):
        return _tablo_seri(d)
    if isinstance(d, Bolum):
        return _bolum_seri(d)
    return _paragraf_seri(d)


def _elements_seri(belge: UdfBelge) -> str:
    """<elements> içeriği: düğümler ÖZGÜN sırasıyla (üstbilgi/altbilgi dahil).

    Açılış etiketi ve düğüm arası boşluklar da belgeden öğrenilmiştir (`elements_acilis`,
    `elements_ayiraclar`) - content.xml baytı korunsun diye."""
    el_nit = belge.elements_nitelik or {"resolver": "hvl-default"}
    acilis = belge.elements_acilis or _etiket_ac("elements", el_nit)
    parcalar: list[str] = []
    # Üstbilgi/altbilgi AYRI liste değil, `dugumler` içinde kendi yerinde duran birer düğüm
    # (bkz. `Bolum`) - sıra kendiliğinden doğru.
    parcalar.extend(_dugum_seri(d) for d in belge.dugumler)
    # Açılış etiketinden sonraki ve kapanıştan önceki satır sonu SABİTTİR (121 evrakta da
    # bizim üretimimizde de var); değişken olan yalnız düğümler arası ayıraç.
    return acilis + "\n" + _parcalari_birlestir(belge, parcalar) + "\n</elements>"


#: Üst düzey `<content>` bloğu. Öznitelik ALMAZ; bu yüzden desen `<content>` ile birebir
#: başlar ve paragraf içindeki `<content startOffset=... />` koşularına ASLA çarpmaz.
_KALIP_ICERIK_DESEN = re.compile(r"<content>.*?</content>", re.DOTALL)
#: Kalan üç blok: hem normal hem kendini kapatan biçim.
_KALIP_BLOKLAR = (
    ("properties", KALIP_OZELLIK),
    ("elements", KALIP_ELEMENTS),
    ("styles", KALIP_STIL),
)


def _kalip_cikar(ham_xml: str) -> str:
    """Ham content.xml'den İSKELETİ çıkar: yeniden ürettiğimiz dört bloğun yerine imle
    koyar, kalan her baytı olduğu gibi bırakır (bkz. `KALIP_ICERIK`).

    Değiştirme `lambda` ile yapılır: düz dizge verilseydi içindeki ters bölü kaçış olarak
    yorumlanırdı."""
    kalip = _KALIP_ICERIK_DESEN.sub(lambda m: KALIP_ICERIK, ham_xml, count=1)
    for ad, imle in _KALIP_BLOKLAR:
        desen = re.compile(rf"<{ad}\b[^>]*>.*?</{ad}>|<{ad}\b[^>]*/>", re.DOTALL)
        kalip = desen.sub(lambda m, i=imle: i, kalip, count=1)
    return kalip


def _varsayilan_kalip(format_id: str) -> str:
    """Programatik üretimin iskeleti (dışarıdan bir belge okunmadıysa)."""
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<template format_id="{_oznitelik_kacis(format_id)}">\n'
            f'{KALIP_ICERIK}\n{KALIP_OZELLIK}\n{KALIP_ELEMENTS}\n{KALIP_STIL}\n'
            '</template>')


def _parcalari_birlestir(belge: UdfBelge, parcalar: list) -> str:
    """Üst düzey düğümleri özgün boşluklarıyla birleştir; liste yetmezse tek ayıraca düş."""
    ayiraclar = list(belge.elements_ayiraclar or [])
    out = []
    for i, parca in enumerate(parcalar):
        if i:
            out.append(ayiraclar[i - 1] if i - 1 < len(ayiraclar) else belge.elements_ayirac)
        out.append(parca)
    return "".join(out)


def content_xml_uret(belge: UdfBelge) -> str:
    """UDF content.xml'i ELLE (string) üret - CDATA/offset birebir korunur.

    Kalıp varsa (belge dışarıdan okundu) iskelet AYNEN kullanılır ve yalnız dört blok
    yerine konur; yoksa varsayılan iskelet kurulur."""
    kalip = belge.kalip or _varsayilan_kalip(belge.format_id)
    return (kalip
            .replace(KALIP_ICERIK, f'<content>{_cdata_sar(belge.metin)}</content>')
            .replace(KALIP_OZELLIK, belge.properties_xml)
            .replace(KALIP_ELEMENTS, _elements_seri(belge))
            .replace(KALIP_STIL, belge.styles_xml))


def paragraf_araliklari(belge: UdfBelge) -> list[tuple[int, int]]:
    """Belgedeki paragrafların CDATA aralıkları (start, end) - CDATA sırasıyla: üst, gövde, alt.

    Aralık paragrafın koşularından türetilir (koşusuz paragraf atlanır: metne hiç dokunmaz).
    `dogrula` girdisi budur."""
    araliklar: list[tuple[int, int]] = []
    for p in belge.tum_paragraflar:
        dolu = [k for k in p.kosular if k.uzunluk > 0]
        if not dolu:
            continue        # koşusuz ya da yalnız çapa koşulu paragraf metne hiç dokunmaz
        araliklar.append((min(k.start for k in dolu),
                          max(k.start + k.uzunluk for k in dolu)))
    # OFFSETE göre sırala, bölüm sırasına göre değil: gerçek UYAP evrakında altbilgi
    # paragrafının offseti gövdenin ortasına düşebiliyor. Kendi ürettiğimiz belgede zaten
    # sıralıdır (sıralama işlemsiz kalır); denetim yanlış "örtüşme" saymasın diye şart.
    araliklar.sort()
    return araliklar


def dogrula(text: str, paragraflar: list[tuple[int, int]], *, ham: bool = False) -> None:
    """UDF yazma KAPISI: metin/paragraf değişmezlerini denetle; bozuksa `ValueError` fırlat.

    Denetlenen değişmezler (bkz. `PARAGRAF_SONU`):
      1. Paragraf sayısı metindeki '\\n' sayısını AŞMAZ (her paragraf bir satır sonu tüketir).
      2. Span'lar metni boşluksuz ve örtüşmesiz döşer (her paragraf bir öncekinin bittiği yerde
         başlar) - tek karakterlik kayma sonraki TÜM offsetleri bozar.
      3. Her paragrafın metni '\\n' ile biter (yoksa paragraf bir öncekine yapışır ve kendi
         hizalamasını kaybeder).
      4. Metnin sonu kapsanır (yalnız en sondaki tek '\\n' kapsanmayabilir).

    `assert` DEĞİL açık `raise` kullanılır: uygulama `-O` ile koşsa bile kapı açık kalmaz."""
    # `satir_sonu_zorunlu=False` (HAM kip): belge DIŞARIDAN geldi ve olduğu gibi geri yazılıyor.
    # Satır sonu kuralları BİZİM yazma disiplinimizdir, UDF'in kuralı değil: gerçek UYAP evrakında
    # tablo hücresinin son paragrafı satır sonu taşımaz ve eski Adalex çıktısı paragrafları hiç
    # ayırmazdı. O belgeleri "düzeltmek" metni değiştirmek olurdu - kapı yalnız DÖŞEME
    # değişmezlerini (boşluk/örtüşme/kapsama) denetler, onlar her belgede geçerlidir.
    if not ham and text.count(PARAGRAF_SONU) < len(paragraflar) - 1:
        raise ValueError("UDF: paragraf/newline uyuşmuyor "
                         f"({len(paragraflar)} paragraf, {text.count(PARAGRAF_SONU)} satır sonu)")
    prev = 0
    for i, (s, e) in enumerate(paragraflar):
        if s < prev:
            # ÖRTÜŞME her kipte hatadır: iki paragraf aynı karakterleri sahiplenirse UYAP
            # editöründe biçim çakışır ve dolgu (`udf_doldur`) yanlış aralığı kaydırır.
            raise ValueError(f"UDF: paragraf örtüşmesi @{s} (önceki bitiş {prev})")
        if s > prev and not ham:
            # BOŞLUK yalnız kendi ürettiğimiz belgede hatadır (bir paragraf sahipsiz metin
            # bırakmış demektir). Dışarıdan gelen evrakta boşluk GERÇEKTİR - bölümler arası
            # satır sonu ve imza dipnotu hiçbir paragrafa ait değildir.
            raise ValueError(f"UDF: boşluk @{s} (beklenen {prev})")
        # Satır sonu kuralı KATI kipte SON paragraf dahil geçerlidir: kendi üreticimiz her
        # paragrafa satır sonu koyar, istisna tanımak ileride bir üretim regresyonunu
        # kapıdan geçirirdi. Dışarıdan gelen belgede (ham) kural zaten kapalı - gerçek UYAP
        # evrakında son aralık ve tablo hücresinin son paragrafı satır sonu taşımıyor.
        if not ham and not text[s:e].endswith(PARAGRAF_SONU):
            raise ValueError("UDF: paragraf satır sonu ile bitmiyor: "
                             f"{text[s:e][-40:]!r}")
        prev = e
    if not ham and prev < len(text) - 1:
        raise ValueError(f"UDF: metnin sonu kapsanmadı: {prev}/{len(text)}")


def _udf_seri(belge: UdfBelge, *, ham: bool = False) -> bytes:
    """UdfBelge -> geçerli UDF ZIP baytları (content.xml İLK üye + yan dosyalar korunur).

    TEK YAZMA KAPISI: `dogrula` burada, hiçbir bayt üretilmeden ÖNCE koşar - bozuk bir belge
    diske/UYAP'a hiç çıkmaz."""
    dogrula(belge.metin, paragraf_araliklari(belge), ham=ham)
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.xml", content_xml_uret(belge).encode("utf-8"))
        for ad, veri in belge.yan_dosyalar.items():
            zf.writestr(ad, veri)
    return tampon.getvalue()


# --- UYAP e-imza gömme (Faz: evrak gönderimi) ---
# UYAP evrakGonder_brd.ajx İMZALI UDF ister; imzasız (yalnız content.xml) reddeder
# ("Elektronik İmzasız Doküman Sisteme Katılamaz"). İmzalı UDF = UDF zip + `sign.sgn` üyesi;
# `sign.sgn` = content.xml üzerine AYRIK (detached) CAdES-BES CMS imzası (2026-07-24 evrak-gonderme
# capture'ındaki gerçek imzalı UDF ile birebir doğrulandı: messageDigest == sha256(content.xml)).
UYAP_IMZA_UYE = "sign.sgn"


def udf_imzala(udf_bytes: bytes, imza_fn) -> bytes:
    """İmzalı UDF üretir: `content.xml`i AYNEN alıp `imza_fn` ile imzalar ve sonucu `sign.sgn`
    üyesi olarak ZIP'e ekler. `imza_fn(content_xml_baytlari) -> imza_baytlari` (ayrık CAdES-BES;
    SAF - kart/ağ ÇAĞIRANda, bu modül imza üretmez). content.xml BİREBİR korunur (imza tam o
    baytların digest'ine bağlıdır -> yeniden serileştirmeyiz). Var olan `sign.sgn` (yeniden
    imzalama) DEĞİŞTİRİLİR. content.xml yoksa ValueError."""
    with zipfile.ZipFile(io.BytesIO(udf_bytes)) as zf:
        adlar = zf.namelist()
        if "content.xml" not in adlar:
            raise ValueError("Geçersiz UDF: content.xml yok (imzalanamaz).")
        content = zf.read("content.xml")
        korunan = [(ad, zf.read(ad)) for ad in adlar if ad != UYAP_IMZA_UYE]
    imza = imza_fn(content)
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as zf:
        for ad, veri in korunan:          # content.xml İLK üye olarak korunur (kaynak sırası)
            zf.writestr(ad, veri)
        zf.writestr(UYAP_IMZA_UYE, imza)  # imza EN SONA
    return tampon.getvalue()


def udf_imzali_mi(udf_bytes: bytes) -> bool:
    """UDF içinde `sign.sgn` üyesi var mı (imzalı UDF). Bozuk zip/okuma hatası -> False (savunmacı)."""
    return udf_imza_bayt(udf_bytes) is not None


#: İmza özetinin kapsadığı üyeler, SABİT SIRAYLA. Sıra sözleşmenin parçasıdır: değişirse
#: defterdeki tüm özetler geçersiz olur.
IMZA_OZET_UYELERI = ("content.xml", UYAP_IMZA_UYE)


def imza_ozeti(udf_bytes: bytes) -> str | None:
    """İmzalı UDF'in KİMLİĞİ: `content.xml` + `sign.sgn` üyelerinin baytları üzerinden tek
    sha256. İmzasız/bozuk/eksik üyeli UDF'te None ("bilinmiyor" - kısıt uygulanmaz).

    **Neden ÜYE özeti, dosya özeti değil (canlı kanıt 19.08.2026):** UYAP gönderdiğimiz UDF'i
    evrak listesine koyarken DIŞ ZIP'i yeniden paketliyor - aynı belgede yerel dosya 3750 B,
    UYAP'tan inen 3782 B ve dosya sha256'ları farklı. Üye baytları ise BİRE BİR aynı
    (content.xml 7653 B ve sign.sgn 2622 B, ikisinin de özeti tutuyor). Yani dosya hash'iyle
    kurulan bir tanıma HİÇ eşleşmezdi.

    Uzunluk da özete girer (`<uzunluk>:<bayt>` çerçevesi): iki üyenin baytlarını düz uç uca
    eklemek, sınırı kaydıran farklı içerik çiftlerinin aynı özeti üretmesine kapı açardı.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(udf_bytes)) as zf:
            adlar = set(zf.namelist())
            if not set(IMZA_OZET_UYELERI) <= adlar:
                return None
            h = hashlib.sha256()
            for ad in IMZA_OZET_UYELERI:
                veri = zf.read(ad)
                h.update(f"{ad}:{len(veri)}:".encode("utf-8"))
                h.update(veri)
            return h.hexdigest()
    except (zipfile.BadZipFile, OSError, KeyError):
        return None


def udf_imza_bayt(udf_bytes: bytes) -> bytes | None:
    """İmzalı UDF'in `sign.sgn` (ayrık CAdES-BES) baytları; imzasız/bozuk UDF'te None.

    SAF: imzayı ÇÖZMEZ, yalnız çıkarır. İmzanın SAHİBİNİ okumak `pkcs11_imza.imza_sahibi_tc`
    işidir (ASN.1 çözümü orada; bu modül kripto bilmez)."""
    try:
        with zipfile.ZipFile(io.BytesIO(udf_bytes)) as zf:
            if UYAP_IMZA_UYE not in zf.namelist():
                return None
            return zf.read(UYAP_IMZA_UYE)
    except (zipfile.BadZipFile, OSError, KeyError):
        return None
