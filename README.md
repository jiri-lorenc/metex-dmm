# Metex M-3650D v počítači

Aplikace pro Windows, která z multimetru **Metex M-3650D** (řada M-3600D,
„Dual Display“) dělá měřicí přístroj v počítači. Přes sériovou linku čte
hodnoty a zobrazuje je velkým displejem se sedmisegmentovými číslicemi
a bargrafem. Kreslí živý graf a počítá statistiku. Měření zapisuje do CSV
s časem a hlídá nastavené meze.

![Aplikace se simulátorem](docs/foto/aplikace-simulator.png)

*Aplikace v režimu simulátoru: hlavní displej, klouzavý průměr na vedlejším
displeji, bargraf 0–20, statistika, záznam a graf s mezemi.*

| Přístroj | Převodník USB–RS-232 |
|---|---|
| ![Metex M-3650D v kufříku](docs/foto/pristroj-v-kufru.webp) | ![AXAGON ADS-1PQN s kabelem Metexu](docs/foto/prevodnik-zapojeny.webp) |

## Co aplikace umí

- **Displej jako na přístroji.** Ukazuje hodnotu, jednotku a režim (DC, AC,
  Ω, FREQ, CAP, hFE, dioda, logika). Rozsah odhaduje z formátu čísla.
  Bargraf má stupnici 0–20 a při přetížení O.L bliká jako originál.
  Symbol COM svítí, když přístroj vysílá sám.
- **Vedlejší displej** ukazuje klouzavý průměr za zvolené okno.
- **Živý graf** má časovou osu a volitelný úsek: 30 s až 6 h, nebo celý
  záznam. Jednotky na ose se přizpůsobují (mV, µA…). Přetížení se vyznačí
  červenými značkami, meze čárkovaně. Když vypnete „Sledovat nová data“,
  můžete graf posouvat a zvětšovat myší.
- **Statistika** se počítá za celou řadu i za klouzavé okno (10 s až 1 h):
  průměr, minimum, maximum, rozpětí, směrodatná odchylka, počet vzorků
  a počet přetížení O.L. Přidává dobu řady a počet vzorků za sekundu.
  Přetížení do průměru nevstupují.
- **Nová řada.** Když otočným přepínačem změníte měřenou veličinu
  (např. V → mA), statistika a graf začnou znovu. Pouhá změna rozsahu
  (20 mA → 200 mA) řadu nepřeruší, protože se vše počítá v základních
  jednotkách.
- **Záznam do CSV** se ovládá tlačítky Spustit, Pozastavit a Ukončit.
  Každý řádek se hned zapíše na disk, takže při pádu PC nic neztratíte.
  Formát je buď pro český Excel (středník a desetinná čárka), nebo
  standardní.
- **Export.** Data aktuální řady z paměti lze uložit do CSV (Ctrl+S), i když
  záznam neběžel. Graf lze uložit jako PNG.
- **Hlídání mezí.** Zadáte dolní a horní mez, třeba `4,5`, `15m` nebo `1,2k`.
  Při překročení se displej orámuje červeně, zvýší se počítadlo a přístroj
  volitelně pípne. Stav alarmu se zapisuje i do CSV.
- **Odolnost.** Neplatné rámce, ticho na lince i vytažení převodníku z USB
  aplikace přežije a sama se znovu připojí.
- **Simulátor.** Bez přístroje lze vše vyzkoušet: v seznamu portů zvolte
  „Simulátor“, nebo spusťte aplikaci s parametrem `--demo`.

**Bezpečnost:** do přístroje se posílá jen znak `D`, tedy dotaz na aktuální
hodnotu. V režimu „Aktivní – jen poslouchat“ se neposílá vůbec nic.
Aplikace nic nenastavuje a nic nevynucuje. **Rozsah se přepíná jen ručně
otočným přepínačem.** V proudových rozsazích přepnutí přeruší měřený obvod.
Aplikace na to upozorní červeným pruhem, kdykoli měříte proud.

## Instalace

Potřebujete:

1. **Windows 10/11** a **Python 3.11 nebo novější** z
   [python.org](https://www.python.org/downloads/). Při instalaci zaškrtněte
   „Add python.exe to PATH“.
2. **Převodník USB–RS-232 s plnohodnotným rozhraním**, který má vyvedené
   DTR a RTS. Ověřený je **AXAGON ADS-1PQN** (čip FTDI FT232R, VID:PID
   0403:6001). Ovladač FTDI se ve Windows 11 nainstaluje sám a převodník se
   objeví jako `COMx`. Podrobnosti najdete v [docs/zapojeni.md](docs/zapojeni.md).

Pak stačí dvakrát kliknout na **`Metex.bat`**. Při prvním spuštění vytvoří
složku `.venv` a nainstaluje knihovny (asi 1–2 minuty, je potřeba internet).
Potom už rovnou spouští aplikaci.

Ruční instalace v PowerShellu (ve složce repozitáře):

```powershell
py -3 -m venv .venv
```

```powershell
.venv\Scripts\python -m pip install -r requirements.txt
```

```powershell
.venv\Scripts\pythonw -m metex_dmm
```

Bez přístroje: `.venv\Scripts\pythonw -m metex_dmm --demo`.

## Zapojení a první měření

Postup podrobně a s fotkami popisuje [docs/zapojeni.md](docs/zapojeni.md).
Ve zkratce:

1. Konektor kabelu Metexu (pětikolíková vidlice) zasuňte do zdířky v boku
   přístroje.
2. DB9 samici kabelu nasuňte na DB9 převodníku AXAGON a dotáhněte šroubky.
3. Převodník zapojte do USB.
4. Zapněte multimetr a otočným přepínačem zvolte funkci a rozsah.
5. Spusťte aplikaci, vyberte port (např. `COM10 – USB Serial Port …
   0403:6001`) a klikněte **Připojit**.

Aplikace si port pamatuje a příště se připojí sama.

## Ovládání

| Prvek | Význam |
|---|---|
| **Port / Obnovit** | Výběr COM portu. Převodníky FTDI, Prolific a CH340 jsou v seznamu nahoře. |
| **Komunikace** | *Automaticky* (doporučeno): ptá se `D` a pozná, kdy přístroj vysílá sám. *Pasivní*: vždy se ptá `D`. *Aktivní*: nic neposílá, jen poslouchá. |
| **Vzorkování** | Nejkratší odstup dotazů. „Co nejrychleji“ dává asi 2,8–3 vzorky/s. Pro dlouhé záznamy stačí 1–10 s. V aktivním režimu určuje tempo přístroj (asi 1/s). |
| **Připojit / Odpojit** | Spustí nebo zastaví komunikaci. Vedle je barevný stav linky. |
| **Statistika – okno** | Délka klouzavého okna. Klouzavý průměr se ukazuje i na vedlejším displeji. |
| **Vynulovat statistiku a graf** | Začne novou řadu. Probíhající záznam do CSV běží dál. |
| **Spustit záznam… / Pozastavit / Ukončit** | Zápis do CSV. Během pauzy se řádky nezapisují. |
| **Hlídání mezí** | Meze v základních jednotkách (V, A, Ω, F, Hz) s předponami m, µ/u, n, k, M. |
| **Stavový řádek** | Počty dobrých a chybných rámců, dotazů bez odpovědi a znovupřipojení. Vpravo je poslední surový rámec. |

### Pasivní a aktivní režim přístroje

Po zapnutí a po každém otočení přepínačem je přístroj v **pasivním** režimu.
Odpovídá jen na dotaz `D`. Tlačítkem **FUNCTION** se přepne do **aktivního**
režimu: na displeji svítí **COM** a přístroj sám vysílá asi 1× za sekundu.
V režimu *Automaticky* aplikace zvládne oba případy. Po připojení a pak
jednou za minutu chvíli jen poslouchá. Přijdou-li data bez dotazu, přestane
se ptát. Když samovolné vysílání ustane, začne se ptát znovu.

### Formát CSV

| sloupec | význam |
|---|---|
| `time_iso` | čas vzorku, ISO 8601 s časovým pásmem a milisekundami |
| `elapsed_s` | sekundy od začátku souboru |
| `mode` | režim z přístroje: `DC`, `AC`, `OH`, `FR`, `CA`, `DI`, `HF`… |
| `value` | číslo z displeje; prázdné při přetížení |
| `unit` | jednotka z přístroje: `V`, `mV`, `mA`, `uA`, `KOhm`, `nF`, `KHz`… |
| `value_si` | hodnota v základní jednotce (V, A, Ω, F, Hz) |
| `unit_si` | základní jednotka |
| `range` | odhad rozsahu, např. `20 V`; při O.L poslední známý |
| `overload` | `1` = přetížení O.L |
| `alarm` | `HIGH` / `LOW` / prázdné |
| `raw` | surový rámec |

Při zaškrtnutém „Formát pro český Excel“ je oddělovačem středník a čísla
mají desetinnou čárku, soubor je v UTF-8 s BOM. Jinak je oddělovačem čárka
a čísla mají tečku.

## Řádkový logger (bez GUI)

Pro dlouhá měření bez okna, třeba přes noc:

```powershell
.venv\Scripts\python tools\metex_log.py --list
```

```powershell
.venv\Scripts\python tools\metex_log.py --port COM10 --raw --duration 10
```

```powershell
.venv\Scripts\python tools\metex_log.py --port COM10 --out mereni.csv --excel --interval 1
```

Přepínače `--poll` a `--listen` vynutí pasivní nebo aktivní režim.
`--window` nastaví klouzavé okno a `--report` určuje, jak často se vypisuje
souhrn.

## Proč Python + PySide6 (Qt) + pyqtgraph

- **pyserial** je osvědčená knihovna pro sériové porty. Umí přesně to, co
  Metex potřebuje: 7 datových bitů, 2 stop bity a ruční ovládání DTR/RTS
  pro napájení rozhraní.
- **Nativní okno místo webového UI.** Webové UI s lokálním serverem by
  přidalo další vrstvu, tedy server, port a prohlížeč, a víc míst, kde se
  může něco pokazit. Okno Qt je jeden proces, který se spustí dvojklikem.
  Sériový port drží jen on a po zavření ho spolehlivě uvolní.
- **pyqtgraph** kreslí graf s desetitisíci body plynule, umí časovou osu,
  předpony SI a zoom myší. Matplotlib by byl na živá data pomalý.
- **Python 3.11 a pyserial** už na PC byly. Jádro (`protocol`, `link`,
  `stats`, `csvlog`) na Qt nezávisí, takže ho sdílí GUI, řádkový logger
  i testy.

## Testy

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
```

```powershell
.venv\Scripts\python -m pytest
```

Testy pokrývají parser rámců: skutečné rámce z přístroje, příklady
z návodu, testovací vektory z QtDMM, přetížení a vadné rámce. Dále dělení
proudu bajtů s šumem, statistiku a CSV. Sériovou linku testují proti
simulovanému portu: pasivní i aktivní režim, šum, odpojení převodníku
a chybu v obsluze UI.

## Struktura

```
Metex.bat              spuštění dvojklikem (poprvé i instalace)
metex_dmm/
  protocol.py          rámce metex14 → hodnoty, jednotky, rozsah
  link.py              sériová linka ve vlákně, režimy, znovupřipojení
  stats.py             statistika běhu a klouzavého okna
  csvlog.py            zápis CSV
  simulator.py         simulovaný přístroj
  lcd.py               kreslený displej a bargraf
  app.py               hlavní okno
tools/metex_log.py     řádkový logger
tests/                 unit testy (pytest)
docs/                  dokumentace přístroje, protokolu a zapojení, fotky
```

## Dokumentace

- [docs/pristroj.md](docs/pristroj.md) popisuje přístroj, jeho funkce,
  rozsahy a ovládání.
- [docs/zapojeni.md](docs/zapojeni.md) popisuje kabel MT/RS232C, piny,
  převodník AXAGON a nefunkční převodníky.
- [docs/protokol.md](docs/protokol.md) popisuje komunikační protokol
  a uvádí ověřené zdroje (návod, sigrok, QtDMM).
- [docs/navod-komunikace.md](docs/navod-komunikace.md) shrnuje vlastními
  slovy kapitoly 6-12 a 6-13 návodu a popisuje původní disketu.
