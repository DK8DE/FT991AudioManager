# FT-991/A Audiomanager

**Version 1.9.7** — Desktop-Tool zur komfortablen Steuerung des Yaesu **FT-991 / FT-991A**
über die CAT-Schnittstelle. Die Anwendung deckt alle audiobezogenen TX-Parameter
(MIC Gain, Speech Processor, parametrischer Equalizer pro Modus,
SSB-Bandbreite, Cut-Filter, Mic-Quelle …) ab und zeigt parallel die
relevanten RX- und TX-Pegel live an.

Zusätzlich: **Live‑PC Funk** (Mikrofon → DSP → Funk über PC-Headset),
**Audio-Recorder** (MP3-Aufnahme + Replay über CAT-TX), **CAT-Audio-Player**
(MP3/WAV mit PTT), **Favoriten** (Funk- und Audioprofil-Snapshots),
**Rig-Bridge** für WSJT-X und andere CAT-Clients, **Speicherkanal-Editor**,
**S-Meter- und PO-Kalibrierung** sowie direkte Bedienung von VFO, Band und
Kanal aus der Hauptoberfläche.

Die App wurde von Anfang an darauf ausgelegt, **gefahrlos im laufenden
Betrieb** benutzt zu werden: Während TX werden EQ-Profile und Menüs nicht
überschrieben; **MIC Gain** und **Sendeleistung (PC)** lassen sich dagegen
auch unter Sendung anpassen. Verbindungsabbrüche werden automatisch und still
wieder geheilt, und die meisten GUI-Änderungen werden debounced ans Radio
geschrieben.

> *„Front-Panel-Bedienung des FT-991A ist machbar — aber wer einmal seinen
> EQ und Speech Processor in einer GUI mit Echtzeit-Pegelanzeige justieren
> durfte, will nicht wieder zurück."*

<!-- Screenshot folgt — wer Lust hat: gerne als PR hinzufügen. -->

## Dokumentation

Ausführliche Bedienungsanleitungen (Installation, CAT, Sound, alle Menüs,
Tastenkürzel, Fehlerbehebung):

| Sprache | Markdown (Quelle) | PDF (GitHub Release) |
|---------|-------------------|----------------------|
| Deutsch | [`Bedienungsanleitung.md`](Bedienungsanleitung.md) | `FT991AudioManager-Bedienungsanleitung-<Version>.pdf` |
| Englisch | [`UserManual_EN.md`](UserManual_EN.md) | `FT991AudioManager-UserManual-<Version>.pdf` |

- **In der App:** **Hilfe → Anleitung** öffnet das passende PDF im Browser
  (Sprache wie unter **Ansicht → Sprache** eingestellt).
- **Screenshots** für die Handbücher: [`docs/screenshots/`](docs/screenshots/)
  (getrennte Ordner `de/` und `en/`).
- **PDFs lokal erzeugen** (Pandoc + wkhtmltopdf):  
  `.\scripts\build_manual_pdfs.ps1 -Version <APP_VERSION>`

Die aktuelle Versionsnummer steht in [`version.py`](version.py) (`APP_VERSION`).

## Features

### TX-Audio (Profile, schreiben + lesen)

- **MIC Gain** (`MG`) und **Speech Processor** an/aus + Pegel (`PR0`, `PL`)
- **Mic-EQ ein/aus** (`PR1` für Normal-EQ)
- **Parametric MIC EQ — Normal** (Speech Processor aus): 3 Bänder
  *Low / Mid / High* mit Mittenfrequenz, Bandbreite und Pegel
  (`EX121–EX129`)
- **Parametric MIC EQ — Processor** (Speech Processor an): identische 3
  Bänder (`EX130–EX138`)
- **Interaktive EQ-Kurve**: keine Dropdowns, keine SpinBoxes — Mittenfrequenz
  und Q werden über *ziehbare Punkte* auf einem Graphen eingestellt, der
  hellblaue Bandbreite-Bereich lässt sich am Punkt aufziehen. Die Vorschau
  wird live ans Radio übertragen.
- **SSB TX-Bandbreite** + **SSB Low-/High-Cut** + **Mic Select pro Modus**
  (`EX104–EX107` etc.)
- **AM / FM Carrier-Level**, **DATA TX-Level**
- Pro Betriebsart (SSB / AM / FM / DATA / CW / RTTY / C4FM) wird nur das
  angezeigt, was im jeweiligen Modus auch wirkt — der Rest wird komplett
  ausgeblendet.
- **Profile** speichern, laden, exportieren, löschen — beim ersten Start
  automatisch angelegt im User-Datenordner (`presets.json`, siehe unten).
- **Auto-Sync**: GUI-Änderungen werden debounced ans Radio geschrieben,
  Diffs gegen den Baseline-Profilstand vermeiden überflüssige Befehle.
  Beim Wechsel der Mode-Gruppe in der GUI wird automatisch der Operating
  Mode am Radio gesetzt und der Stand für den neuen Modus eingelesen.
- **Equalizer-Editor** in eigenem Fenster (**Funktionen → Equalizer…**,
  `Ctrl+Shift+E`): Grundwerte, EQ-Kurve, erweiterte Menüs — das Hauptfenster
  bleibt auf Meter und Profilauswahl fokussiert.

### CAT-Audio-Player (MP3 / WAV)

- Eigenes Fenster **Funktionen → Audio-Player…**
- Ordner mit **MP3**- und **WAV**-Dateien scannen, **Playlist per Drag & Drop**
  sortieren
- **Vorlauf** und **Pause zwischen Dateien** (ms), Modi *nach jeder Datei
  stoppen* oder *alle nacheinander*
- **Lautstärke** und Wiedergabe-Gerät (Windows: `QT_MEDIA_BACKEND=windows`)
- **CAT-PTT** (`TX1;` / `TX0;`) im Hintergrund-Thread — UI bleibt flüssig
- Beim Öffnen (mit CAT): automatisch **DATA-FM** + Menü **072 → USB** für
  saubere Audio-Zuleitung; beim Schließen **Wiederherstellung** des
  vorherigen Modus
- **Live‑PC Funk** hält den **DATA-Modus** für die Sitzung aktiv (auch bei
  PTT); Wechsel im Hauptfenster wird übernommen, bis das Live-Fenster
  geschlossen wird
- Schnellzugriff-Button **Audioplayer** in der Funksteuerungsleiste

*Abhängigkeit:* `PySide6-Addons` (in `requirements.txt`) für Qt-Multimedia.

### Audio-Recorder (MP3-Aufnahme + Replay)

- Eigenes Fenster **Funktionen → Audio-Recorder…** (`Ctrl+Shift+R`)
- **Aufnahme** vom Funk-USB-CODEC (Stereo-RX) als **MP3** mit wählbarer
  Bitrate; interne Normalisierung (Soft-Compressor + Limiter) vor dem Encode
- **Replay** der Aufnahmen über **CAT-TX** im DATA-Mode (wie der Audio-Player)
- Aufnahme-Ordner wählbar, Liste der Mitschnitte (neueste oben)
- Schnellzugriff-Button **Audiorecorder** in der Funksteuerungsleiste

*Abhängigkeit:* `lameenc` (in `requirements.txt`) für MP3-Encoding.

### Live‑PC Funk (Mikrofon → Funk über PC)

- Eigenes Fenster **Funktionen → Live‑PC Funk…** (`Ctrl+Shift+L`)
- **PC-Mikrofon** über PortAudio/sounddevice in Echtzeit auf den
  **Funk-Ausgang** (USB-CODEC / SCU-17) — getrennt vom Qt-Multimedia-Pfad
  des Audio-Players
- **Siebenband-EQ**, Pegelregler für Mikrofon, Monitor und Funk-Eingang
- **Funk mithören**: RX-Audio optional auf den Monitor/Kopfhörer
- **CAT-PTT**: Tasten **PTT** / **PTT halten**, Tastatur **Strg+Y**
  (Push-to-Talk) und **Strg+X** (PTT halten)
- **RX/TX-LED** wie im Hauptfenster; während Live-TX kann der Funkeingang
  stummgeschaltet werden
- Geräte und Mithören auch unter **Funktionen → Soundeinstellung…**
  (`Ctrl+Shift+S`, Abschnitt Live-Monitoring)
- Schnellzugriff-Button **Live** in der Funksteuerungsleiste

*Abhängigkeiten:* `sounddevice`, `numpy`, `scipy` (in `requirements.txt`).

> **Wichtig — Audio-Anschluss beim Funken über PC-Headset:** Wenn Sie über
> die Software mit einem **PC-Headset** funken möchten (Live‑PC Funk oder
> vergleichbare Audio-Pfade zwischen PC und Transceiver), sollte das Funkgerät
> **galvanisch getrennt** vom PC angeschlossen werden — z. B. über ein
> Audio-Transformator-Interface, ein dediziertes Funk-USB-Interface mit
> Trennung oder vergleichbare Schutzmaßnahmen. Ohne Trennung entstehen leicht
> **Brummschleifen**, Pegelprobleme oder in Extremfällen **Schäden** an PC
> und/oder Funkgerät durch unterschiedliche Massepotenziale. Die CAT-Leitung
> (seriell) bleibt davon unberührt; betroffen sind die **Audio-Leitungen**
> zwischen PC-Soundkarte/Headset und Funk-USB-CODEC.

### Favoriten (Funk-Snapshots)

- Eigener Bereich **Favoriten** unter der unteren Leiste des Hauptfensters
- Speichert **Frequenz**, **Mode**, **EQ-Profil**, **Squelch**, **AF/RF-Gain**
  und **Sendeleistung (PC)** als benannten Snapshot
- Favorit **anwenden**, **überschreiben**, **neu anlegen** oder **löschen**
- Persistiert in `favorites.json` im User-Datenordner

### Live-Anzeige (RX + TX)

- **S-Meter** (vertikal, S-Punkt-Skala) mit Squelch-Linien-Overlay
- **SQL**, **MIC Gain**, **TX POWER (PC)** als vertikale Slider neben dem
  S-Meter — **MIC** und **POWER** auch **während TX** bedienbar
- **Noise Blanker (NB)**, **Digital Noise Reduction (DNR)**,
  **Digital Notch Filter (DNF)** — je als senkrechter Slider mit
  Ein/Aus-LED und Pegel-Schieber; in **FM** und **C4FM** ausgeblendet
  (am Gerät wirkungslos)
- **AGC** als 4-Stufen-Slider (AUTO / FAST / MID / SLOW) — nur in
  **LSB**, **USB** und **DATA-LSB** sichtbar
- **AF/RF-Gain** + Squelch-Level als kompakte Bars
- **Sendebandbreite (SH WIDTH)** unter AF/RF — symmetrischer Hz-Balken +
  P2-Slider für SSB/CW/DATA/RTTY (in **DATA-FM** und reinen FM-Modi
  ausgeblendet)
- **TX-Meter**: ALC, COMP, PO, SWR — vertikal, mit gerätespezifischen
  Einheiten; **PO** nutzt optional **Kalibrierkurven** (siehe unten)
- **VFO-A / VFO-B** editierbar (MHz/kHz/Hertz), **VFO A↔B**, **Mode** in
  der Kopfzeile, **RX/TX-LED** grün (Empfang) bzw. rot (Sendung)
- **Funksteuerung**: Tune, **Simp / RPT± / REV**, **T.CALL** (1750-Hz-Rufton,
  Taste gedrückt halten), Schnellzugriff auf Audio-Player, Recorder, Live und
  Soundeinstellungen; Speicherkanal ±, **Amateurband** ± und Band-Auswahl (CAT)

### Speicherkanäle

- **Funktionen → Speicherkanäle…** (`Ctrl+Shift+K`): Tabellen-Editor für alle
  Kanäle (Frequenz, Mode, Name, Töne …), Import/Export als JSON
- **Speicherkanal-Auswahl** in der unteren Leiste des Hauptfensters;
  Kanalliste wird nach Verbindung im Hintergrund geladen

### Kalibrierung (S-Meter und PO)

- **Datei → Einstellungen → Kalibrierung**
- **S-Meter**: SM0-Rohwerte → Anzeige-Skala, getrennt für KW und VHF/UHF
- **PO-Meter**: geführte Messung auf **10 m** (FM), Stützpunkte
  **Watt ↔ CAT-Rohwert** für die **POWER**-Anzeige und den POWER-Slider
- Ergebnisse in `smeter_calibration.json` bzw. `po_calibration.json` im
  User-Datenordner (siehe unten)

### Rig-Bridge (für andere CAT-Programme)

Unter **Datei → Einstellungen → Rig-Bridge**:

- **FLRig**-Kompatibilität (XML-RPC, Host und Port konfigurierbar)
- Gemeinsame CAT-Serial-Verbindung zum FT-991 — die Bridge serialisiert
  Zugriffe mit der Haupt-App

Damit können z. B. **WSJT-X**, **fldigi** oder **Ham Radio Deluxe** das
Radio parallel nutzen (über FLRig), während der Audiomanager läuft.

### Robustheit

- **Auto-Connect** beim Programmstart (optional, in den Einstellungen
  konfigurierbar).
- **Auto-Reconnect**: Zieht jemand das USB-Kabel oder schaltet das Radio
  aus, geht die App ohne Fehler-Popup auf „offline" (rote LED) und
  versucht im Hintergrund regelmäßig wieder Kontakt aufzunehmen. Sobald
  das Radio wieder da ist, geht die LED auf grün und die Werte werden
  einmal gelesen — auch **Audio-Player**, **Recorder** und **Live‑PC Funk**
  stellen Verbindung und Ton nach erneutem Einstecken wieder her.
- **TX-Lock für Profile/EQ**: Während das Radio sendet, werden **keine**
  EQ-Profile oder erweiterten Menüs auf das Gerät geschrieben. Geänderte
  Werte werden nach TX→RX nachgeholt.
- **CAT-Log-Fenster** (**Ansicht → CAT-Log anzeigen**): Alle gesendeten und
  empfangenen CAT-Frames live mit Zeitstempel, plus Warn-/Fehler-Meldungen.

### UI / Komfort

- **Zweisprachige Oberfläche** (**Ansicht → Sprache**: Deutsch / English) —
  Menüs, Dialoge und Meldungen in der gewählten Sprache; Einstellung wird in
  `settings.json` gespeichert.
- **Dark Mode** als Standard (eigenes Theme, optional Light Mode),
  persistiert in `settings.json` im User-Datenordner.
- **Soundeinstellung** (**Funktionen → Soundeinstellung…**, `Ctrl+Shift+S`):
  globale Audio-Rollen (Sende/PC/Input), Windows-Mixer-Integration,
  Live-Monitoring-Geräte (PortAudio)
- **Einstellungs-Dialog** (wie RotorTcpBridge): linke Tab-Liste, rechter Inhalt
  - **CAT-Verbindung**: COM-Port, Baudrate, Timeout, Auto-Connect,
    TX-/RX-Polling, EQ-Profil-Anzeige
  - **Kalibrierung**: S-Meter und PO (siehe oben)
  - **Rig-Bridge**: FLRig (siehe oben)
- **Tastenkürzel** (Auswahl): `Ctrl+Shift+E` Equalizer, `Ctrl+Shift+A`
  Audio-Player, `Ctrl+Shift+R` Audio-Recorder, `Ctrl+Shift+L` Live‑PC Funk,
  `Ctrl+Shift+K` Speicherkanäle, `Ctrl+Shift+S` Soundeinstellung,
  `Ctrl+L` CAT-Log, `Ctrl+D` Dark Mode
- **Hilfe → Version**: About-Fenster mit Versionsnummer und Lizenz
- **Hilfe → Anleitung**: Handbuch-PDF des installierten Releases auf
  [GitHub](https://github.com/DK8DE/FT991AudioManager/releases) im Browser
  (Deutsch oder Englisch je nach UI-Sprache)
- **Hilfe → Update prüfen**: Vergleich mit dem neuesten Release auf
  [GitHub Releases](https://github.com/DK8DE/FT991AudioManager/releases)
  (ein Klick zur Download-Seite, wenn eine neuere Version existiert)
- Spaltenbreiten/Fenster-Geometrie bleiben über Neustarts erhalten.

## Geräte-Spezifika und Hinweise

### Galvanische Trennung (PC-Headset / Audio-Pfade)

Für **Live‑PC Funk**, Headset-Monitoring oder jeden Betrieb, bei dem
**Audio-Leitungen** zwischen PC und FT-991/SCU-17 geschlossen werden,
empfehlen wir eine **galvanische Trennung** auf dem Audio-Pfad. Typische
Lösungen: Audio-Transformator-Box, Interface mit integrierter Trennung oder
ein sauber getrenntes Funk-USB-Interface — nicht nur ein einfaches
Klinken-Kabel PC ↔ Funk.

Gründe: unterschiedliche **Massepotenziale** (Brummschleifen, Rauschen),
**Pegelanpassung** und Schutz vor **Überspannung** zwischen Geräten. Die
serielle **CAT-Verbindung** ersetzt keine Audio-Trennung.

### CAT und Funkgerät

- **Werks-Baudrate** des CAT-Ports: **38400**. Im Menü 031 (`CAT RATE`)
  kann auf 4800/9600/19200/38400 umgestellt werden.
- Das FT-991A meldet sich am USB-Treiber mit **zwei** COM-Ports: einem
  *Enhanced COM Port* (CAT) und einem *Standard COM Port* (CAT TIMING /
  TX-Trigger). Der **Enhanced COM Port** ist der richtige.
- **AGC-Mapping**: das FT-991A nutzt intern das erweiterte FTDX-Schema mit
  drei AUTO-Sub-Modi (AUTO-F / AUTO-M / AUTO-S an Index 4 / 5 / 6). Die
  App akzeptiert alle drei beim Lesen, sendet beim Schreiben von „AUTO"
  `GT04;` — der Slider erscheint nur in LSB/USB/DATA-LSB.
- **SWR auf VHF/UHF**: Die `RM6;`-Skala des FT-991A ist auf 2 m / 70 cm
  beim Auslesen über CAT nicht zuverlässig kalibriert (liefert oft
  Vollanschlag, obwohl das Front-Panel z. B. 1:1.2 zeigt). Die App blendet
  die SWR-Bar deshalb auf VHF/UHF aus und zeigt einen klaren Hinweis;
  das *Front-Panel* bleibt verbindlich. Auf KW funktioniert die Anzeige
  normal.

## Installation aus dem Quellcode

Voraussetzung: **Python 3.10+**, ein USB-Kabel zum Radio (mit Yaesu
SCU-17 oder dem eingebauten USB-Anschluss des FT-991A).

```powershell
git clone https://github.com/DK8DE/FT991AudioManager.git
cd FT991AudioManager
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Unter Linux/macOS analog (`source .venv/bin/activate`). Auf Linux braucht
der ausführende User Zugriff auf das `/dev/ttyUSB*`-Device (üblicherweise
über die Gruppe `dialout` bzw. `uucp`).

Die Versionsnummer steht zentral in [`version.py`](version.py)
(`APP_VERSION`, Datum, Autor).

## Standalone-EXE und Windows-Installer

### PyInstaller (`build.ps1`)

```powershell
.\build.ps1
```

Das Skript legt bei Bedarf `.venv-build/` an, installiert Abhängigkeiten +
PyInstaller und erzeugt `dist\FT991AudioManager\` (onedir, ohne Konsole).
Es werden nur die benötigten Qt-Teile eingebunden (**Widgets** +
**Multimedia** für den Audio-Player, kein QML/WebEngine/3D) und danach
`qml/`, `translations/` sowie überflüssige Qt-Plugins entfernt — typisch
**ca. 80–120 MB** statt >600 MB mit dem früheren Voll-PySide6-Bundle.

Nützliche Schalter:

- `.\build.ps1 -Clean` — zusätzlich alte `.spec` entfernen und neu erzeugen
- `.\build.ps1 -KeepConsole` — EXE mit sichtbarer Konsole (Debug)
- `.\build.ps1 -Python C:\Pfad\zu\python.exe` — explizites Python

**User-Daten** (installierte EXE, Windows):

`%APPDATA%\FT991AudioManager\`

- `settings.json` (Defaults: Dark Mode an, Polling 100 ms, …)
- `presets.json`, `favorites.json` (beim ersten Start angelegt)
- optional: `po_calibration.json`, `smeter_calibration.json`,
  `memory_backups\`, `memory_exports\`, Speicherkanal-Caches

Liegt noch ein alter Ordner `data\` neben der EXE (frühere Versionen),
werden vorhandene Dateien beim ersten Start nach AppData **kopiert**.

In der **Entwicklung** (nicht gefroren) liegt der gleiche Ordner unter
`<Projekt-Root>\data\`. Im Repository ist nur `data/.gitkeep` versioniert —
persönliche Konfigurationen gehören nicht ins Git.

### Inno-Setup-Installer (`installer.ps1`)

Voraussetzung: [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`ISCC.exe`
im PATH oder Standardpfad).

```powershell
.\installer.ps1
```

Baut zuerst die EXE (wie `build.ps1`) und erzeugt danach:

`dist\installer\FT991AudioManager-Setup-<Version>.exe`

Nur Installer (EXE bereits gebaut):

```powershell
.\installer.ps1 -SkipBuild
```

**Wizard-Bilder** (optional, wie bei RotorTcpBridge): `Installer.png` und
`InstallerSmall.png` ins Projektroot legen und die entsprechenden Zeilen in
[`Installer.iss`](Installer.iss) aktivieren.

### Release-Tag (GitHub: ZIP + Windows-Installer)

```powershell
.\release.ps1
```

Liest `APP_VERSION` aus `version.py`, setzt Git-Tag `v<Version>` und pusht
nach `origin`. GitHub Actions (`.github/workflows/build-windows.yml`) baut
dann automatisch:

- `FT991AudioManager-Setup-<Version>.exe` (Inno Setup)
- `FT991AudioManager-v<Version>-Windows.zip` (portable)
- `FT991AudioManager-Bedienungsanleitung-<Version>.pdf` (Handbuch DE)
- `FT991AudioManager-UserManual-<Version>.pdf` (Handbuch EN)

Vorher Version in `version.py` erhöhen, committen und `main` pushen, damit der
Tag auf dem aktuellen Stand liegt. Fortschritt unter **Actions**, Assets unter
**Releases** auf GitHub.

## Projektstruktur

```
ft991_audio_manager/
├── main.py                  # Einstiegspunkt
├── version.py               # APP_VERSION, Metadaten
├── build.ps1                # PyInstaller-Build (Windows, onedir)
├── installer.ps1            # Inno-Setup-Installer
├── release.ps1              # Git-Tag v<Version> für Releases
├── Bedienungsanleitung.md   # Handbuch (DE, Markdown)
├── UserManual_EN.md         # Handbuch (EN, Markdown)
├── Installer.iss            # Inno-Setup-Skript (Deutsch)
├── FT991AudioManager.spec   # PyInstaller + Windows-Versionsinfo
├── requirements.txt
├── docs/                    # Handbuch-Screenshots, PDF-CSS
├── scripts/                 # Hilfsskripte (z. B. build_manual_pdfs.ps1)
├── i18n/                    # UI-Übersetzungen (de / en)
├── cat/                     # Serielle CAT-Schicht (Threadsafe, Log)
├── audio/                   # Player, Recorder, PTT, T-Call, Funk-Umschaltung
├── live/                    # Live-DSP und PortAudio-Engine (sounddevice)
├── rig_bridge/              # FLRig-TCP-Server
├── mapping/                 # Encoder/Decoder für alle CAT-Kommandos
├── model/                   # Settings, Profile, Favoriten, Persistierung
├── gui/                     # PySide6-Widgets (Main, Meter, Live, Player, …)
├── data/                    # User-Daten (Dev); nur .gitkeep im Repo
└── tests/                   # 540+ Unit-Tests (pytest / unittest)
```

Die `mapping/`-Schicht ist bewusst frei von PySide6/Qt-Abhängigkeiten,
damit sie ohne GUI testbar ist. Die `cat/`-Schicht serialisiert alle
Zugriffe über einen `RLock` — Poller, Rig-Bridge und User-Writes können
parallel laufen, ohne Frames zu zerschießen.

## Tests laufen lassen

```powershell
cd ft991_audio_manager
python -m unittest discover -s tests -v
```

Alternativ mit pytest:

```powershell
python -m pytest tests/ -q
```

Aktuell **540+** Tests. Die meisten brauchen weder serielle Hardware noch
eine Qt-Display-Verbindung; einige GUI-Tests werden ohne Display
übersprungen.

## Lizenz

Apache License 2.0 — siehe [`LICENSE`](LICENSE).

Das Programm spricht über die dokumentierte CAT-Schnittstelle des Yaesu
FT-991 / FT-991A; weder die Software noch dieses Projekt sind mit Yaesu
verbunden. „Yaesu", „FT-991" und „FT-991A" sind Marken ihrer jeweiligen
Inhaber.

## Mitwirken

Bug-Reports, Logs (insbesondere zum `RM6;`-Verhalten auf verschiedenen
Bändern und zum `GT0;`-Verhalten verschiedener Firmware-Stände) sowie
Pull-Requests sind sehr willkommen. Bitte vor PRs die Tests laufen
lassen und für neue CAT-Befehle eine entsprechende Mapping-Test-Abdeckung
mitliefern.
