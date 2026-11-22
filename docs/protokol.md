# Komunikační protokol M-3650D („metex14“)

## Parametry linky

| parametr | hodnota |
|---|---|
| rychlost | 1200 Bd |
| datové bity | 7 (ASCII) |
| parita | žádná |
| stop bity | 2 |
| řízení toku | žádné |
| DTR | **1** (kladné napětí – napájí rozhraní) |
| RTS | **0** (záporné napětí – napájí rozhraní) |

Zdroje: návod str. 37, libsigrok `"1200/7n2/rts=0/dtr=1"`, QtDMM
(1200, 7 bitů, 2 stop bity, RTS vypnuto, DTR zapnuto).

## Režimy komunikace

- **Pasivní** (výchozí po zapnutí a po každém otočení přepínačem): přístroj
  pošle jednu zprávu jako odpověď na příkaz **`D`** (ASCII 0x44, velké D).
- **Aktivní** (zapíná se tlačítkem FUNCTION, na displeji svítí **COM**):
  přístroj sám vysílá bez ohledu na to, zda je PC připojené. Návod uvádí
  zhruba 1× za sekundu.

**Ověřeno s naším M-3650D (22. 11. 2026):** v režimu COM přístroj vysílá
asi **2,5× za sekundu**, s mezerami střídavě 0,4 a 0,6 s, tedy každé
změřené číslo. **Na dotaz `D` přitom dál odpovídá**: odpověď přijde
220–375 ms po dotazu, navíc k samovolným zprávám. Aktivní režim proto nejde
poznat podle chybějících odpovědí, jen podle rámců, které přijdou, když se
software neptá. Aplikace v automatickém režimu po připojení 1,5 s a pak
každých 20 s na 0,8 s jen poslouchá.

Podle wiki sigrok (stránka Multimeter ICs) přístroj odpoví nejspíš na
jakýkoli znak. Návod ale dokumentuje jen `D`, a tato aplikace proto nic
jiného neposílá. QtDMM posílá `D` a za ním LF. libsigrok posílá samotné
`D`. Ukázka v BASICu z návodu (`PRINT #2,"D"`) pošle `D` a CR.

## Formát zprávy

14 znaků ASCII. Číslováno od 1 jako v návodu:

```
znak:   1  2  3  4  5  6  7  8  9  10 11 12 13 14
        └režim┘ └──── hodnota ─────┘ └─jednotka─┘ CR
```

| znaky | obsah | příklady |
|---|---|---|
| 1–2 | režim | `DC`, `AC`, `OH` (odpor), `FR` (frekvence), `CA` (kapacita), `DI` (dioda), `HF` (hFE), `TE` (teplota, jen jiné modely), dvě mezery = podle jednotky |
| 3–9 | hodnota se znaménkem a desetinnou tečkou, zarovnaná vpravo, s úvodními nulami | `-3.999`, `00.00`, `001.0`, `O.L` (přetížení) |
| 10–13 | jednotka, zarovnaná vpravo | `V`, `mV`, `A`, `mA`, `uA`, `Ohm`, `KOhm`, `MOhm`, `pF`, `nF`, `uF`, `Hz`, `KHz`, `MHz` |
| 14 | CR (0x0D) | |

Příklady (`␍` = CR):

```
DC -3.999   V␍      návod str. 36, příklad 1
OH  3.999MOhm␍      návod str. 36, příklad 2
DC  00.00   V␍      skutečně přijato z tohoto přístroje (rozsah 20 V)
DC -00.08   V␍      testovací vektor QtDMM
OH   OL.  Ohm␍      přetížení (testovací vektor QtDMM)
FR  0.050 KHz␍      frekvence (testovací vektor QtDMM)
```

Zpráva nenese stavové příznaky: slabou baterii, HOLD, REL ani MIN/MAX.
Neobsahuje ani vedlejší displej (FREQ). Odpor a test propojení od sebe
podle zprávy rozlišit nelze.

## Jak aplikace zprávu zpracuje

1. Přijaté bajty dělí podle CR. Z každého kusu bere posledních 14 bajtů,
   takže se po šumu nebo useknutém rámci sama znovu zachytí. Kratší kusy
   zahodí jako neplatné.
2. Rámec odmítne, když nekončí CR, obsahuje netisknutelné znaky nebo když
   režim není ze dvou písmen či mezer.
3. Hodnota `O.L`, `.OL`, `OL.` nebo `OL` (i se znaménkem) znamená přetížení.
   Totéž dělá libsigrok.
4. Jednotku převede na základní jednotku SI. Předpony rozlišují velikost
   písmen: `m` je mili, `M` je mega, `K` je kilo.
5. **Rozsah** odhadne z počtu číslic před tečkou, protože přístroj posílá
   úvodní nuly: `0.000` → 2, `00.00` → 20, `000.0` → 200. Bez tečky jde
   o nejvyšší rozsah: 1000 V DC, 750 V AC, jinak 2000.

Tempo: v pasivním režimu se nový dotaz pošle hned po přijetí odpovědi.
Odpověď trvá asi 117 ms (14 znaků × 10 bitů / 1200 Bd), přístroj navíc
potřebuje čas na měření. Prakticky to dává 2,8–3 vzorky/s. Když odpověď
nepřijde do 1 s, dotaz se zopakuje. libsigrok postupuje stejně: nový dotaz
posílá po každém platném rámci nebo po vypršení času.

## Ověřené zdroje

| zdroj | co potvrzuje |
|---|---|
| Návod k přístroji, str. 34–37 ([foto](foto/manual-str34.jpg)) | režimy, `D`, formát 14 znaků, 1200 Bd 7N2, zapojení kabelu |
| [libsigrok `src/dmm/metex14.c`](https://github.com/sigrokproject/libsigrok/blob/master/src/dmm/metex14.c), záznam „Voltcraft M-3650D“ v `src/hardware/serial-dmm/api.c` | parametry linky, `D` bez CR, platnost rámce = CR na 14. místě, seznam režimů a jednotek, O.L |
| [sigrok wiki: Voltcraft M-3650D](https://sigrok.org/wiki/Voltcraft_M-3650D) | 2000 bodů, funkce, podpora driverem serial-dmm |
| [sigrok wiki: Device cables – Metex 5-pin RS232 cable](https://sigrok.org/wiki/Device_cables#Metex_5-pin_RS232_cable) | zapojení kabelu, DTR zapnuto, RTS vypnuto |
| [sigrok wiki: Multimeter ICs – Metex 14-byte ASCII](https://sigrok.org/wiki/Multimeter_ICs#Metex_14-byte_ASCII) | bez stavových příznaků, odpověď na libovolný znak |
| [QtDMM](https://github.com/qtdmm/QtDMM) (`src/device/decoders/ascii.cpp`, `docs/protocols/spec/metex14.yaml`) | Voltcraft M-3650D: 1200, 7 bitů, 2 stop bity, RTS 0, DTR 1; testovací vektory |
| Návod Metex M-3640D (řada M-3600D, B&B Electronics 1997) | 1999 bodů, rozsahy řady, interface 1200 7N2, kabel „MT/RS-232C“ |

Rozpory a nejasnosti:

- **Počet bodů:** QtDMM uvádí 4000, návod řady a sigrok 2000 (1999).
  Rozsahy 200 mV až 200 V odpovídají 2000 bodům.
- **Číslo modelu v návodu:** v kapitole 6-12 českého návodu stojí „M-3850“.
  Text byl zřejmě převzatý z návodu jiného modelu. Příklad `-3.999`
  (4000 bodů) odpovídá spíš M-3850 než M-3650D.
- **libsigrok nezná jednotky `Hz` a `MHz`** (jen `KHz`). Na rozsazích
  2 a 20 MHz by tam frekvence mohla mít špatný řád. Tato aplikace je zná.
- **Číslování kolíků kabelu** se v různých zdrojích liší podle toho, ze
  které strany se počítá. Rozhodující je přiřazení signálů k pinům DB9 a to
  sedí (viz [zapojeni.md](zapojeni.md)).
