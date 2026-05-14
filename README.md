# FT-991A Audio-Profilmanager

Desktop-Tool zur komfortablen Steuerung des Yaesu **FT-991 / FT-991A** über
die CAT-Schnittstelle. Die Anwendung deckt alle audiobezogenen TX-Parameter
(MIC Gain, Speech Processor, parametrischer Equalizer pro Modus,
SSB-Bandbreite, Cut-Filter, Mic-Quelle …) ab und zeigt parallel die
relevanten RX- und TX-Pegel live an.

Die App wurde von Anfang an darauf ausgelegt, **gefahrlos im laufenden
Betrieb** benutzt zu werden: Während TX wird nichts an die Audiokette
geschrieben, Verbindungsabbrüche werden automatisch und still wieder
geheilt, und alle Änderungen an den GUI-Controls werden gedebounced an das
Radio gepusht statt blind in jeder Bewegung.

> *„Front-Panel-Bedienung des FT-991A ist machbar — aber wer einmal seinen
> EQ und Speech Processor in einer GUI mit Echtzeit-Pegelanzeige justieren
> durfte, will nicht wieder zurück."*

<!-- Screenshot folgt — wer Lust hat: gerne als PR hinzufügen. -->


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
  und Q werden über *zieh­bare Punkte* auf einem Graphen eingestellt, der
  hellblaue Bandbreite-Bereich lässt sich am Punkt aufziehen. Die Vorschau
  wird live ans Radio übertragen.
- **SSB TX-Bandbreite** + **SSB Low-/High-Cut** + **Mic Select pro Modus**
  (`EX104–EX107` etc.)
- **AM / FM Carrier-Level**, **DATA TX-Level**
- Pro Betriebsart (SSB / AM / FM / DATA / CW / RTTY / C4FM) wird nur das
  angezeigt, was im jeweiligen Modus auch wirkt — der Rest wird komplett
  ausgeblendet.
- **Profile** speichern, laden, exportieren, löschen — gespeichert wird in
  `data/presets.json` neben der EXE bzw. dem Projekt-Root.
- **Auto-Sync**: GUI-Änderungen werden debounced ans Radio geschrieben,
  Diffs gegen den Baseline-Profilstand vermeiden überflüssige Befehle.
  Beim Wechsel der Mode-Gruppe in der GUI wird automatisch der Operating
  Mode am Radio gesetzt und der Stand für den neuen Modus eingelesen.

### Live-Anzeige (RX + TX)

- **S-Meter** (vertikal, S-Punkt-Skala) mit Squelch-Linien-Overlay
- **AGC**, **Noise Blanker (NB)**, **Digital Noise Reduction (DNR)**,
  **Digital Notch Filter (DNF)** — je als senkrechter Slider mit
  Ein/Aus-LED und Pegel-Schieber neben dem S-Meter; vom User direkt
  bedienbar (Schreiben mit Echo-Verify).
- **AGC** zusätzlich als 4-Stufen-Slider (AUTO / FAST / MID / SLOW).
- **AF/RF-Gain** + Squelch-Level als kompakte Bars.
- **TX-Meter**: ALC, COMP, PO, SWR — vertikal, gleicher Stil wie das
  S-Meter, mit gerätespezifischen Einheiten.
- **VFO-A / VFO-B / Mode** in der Kopfzeile, **RX/TX-LED** grün
  (Empfang) bzw. rot (Sendung).

### Robustheit

- **Auto-Connect** beim Programmstart (optional, in den Einstellungen
  konfigurierbar).
- **Auto-Reconnect**: Zieht jemand das USB-Kabel oder schaltet das Radio
  aus, geht die App ohne Fehler-Popup auf „offline" (rote LED) und
  versucht im Hintergrund regelmäßig wieder Kontakt aufzunehmen. Sobald
  das Radio wieder da ist, geht die LED auf grün und die Werte werden
  einmal gelesen.
- **TX-Lock**: Während das Radio sendet, schreibt die Software **niemals**
  in die Audio-Kette. Auto-Writes warten gepuffert und werden beim
  TX→RX-Übergang nachgeholt.
- **CAT-Log-Fenster** (Datei → Ansicht): Alle gesendeten und empfangenen
  CAT-Frames live mit Zeitstempel, plus Warn-/Fehler-Meldungen — sehr
  hilfreich beim Debuggen.

### UI / Komfort

- **Dark Mode** als Standard (komplett separates Theme nach Spezifikation,
  optional Light Mode), persistiert in `data/settings.json`.
- **Einstellungs-Dialog**: COM-Port, Baudrate, Timeout, Auto-Connect,
  TX-/RX-Polling-Intervalle, „Erweiterte Einstellungen für SSB
  ausblenden".
- Spaltenbreiten/Fenster-Geometrie bleiben über Neustarts erhalten.

## Geräte-Spezifika und Hinweise

- **Werks-Baudrate** des CAT-Ports: **38400**. Im Menü 031 (`CAT RATE`)
  kann auf 4800/9600/19200/38400 umgestellt werden.
- Das FT-991A meldet sich am USB-Treiber mit **zwei** COM-Ports: einem
  *Enhanced COM Port* (CAT) und einem *Standard COM Port* (CAT TIMING /
  TX-Trigger). Der **Enhanced COM Port** ist der richtige.
- **AGC-Mapping**: das FT-991A nutzt intern das erweiterte FTDX-Schema mit
  drei AUTO-Sub-Modi (AUTO-F / AUTO-M / AUTO-S an Index 4 / 5 / 6). Die
  App akzeptiert alle drei beim Lesen, sendet beim Schreiben von „AUTO"
  `GT04;` — das Radio wählt die passende Sub-Variante dann anhand des
  aktiven Modus.
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
git clone https://github.com/<dein-user>/ft991-audio-manager.git
cd ft991-audio-manager
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Unter Linux/macOS analog (`source .venv/bin/activate`). Auf Linux braucht
der ausführende User Zugriff auf das `/dev/ttyUSB*`-Device (üblicherweise
über die Gruppe `dialout` bzw. `uucp`).

## Standalone-EXE für Windows (PyInstaller)

Im Projekt-Root liegt `build.ps1`. Aufruf:

```powershell
.\build.ps1
```

Das Skript

1. legt — falls nötig — ein eigenes Build-venv `.venv-build/` an
   (das System-Python bleibt unangetastet),
2. installiert `requirements.txt` plus PyInstaller,
3. baut mit `--windowed --onedir` ein komplettes Distribution-Bundle
   unter `dist\FT991AudioManager\`.

Den Ordner `dist\FT991AudioManager\` kannst du komplett auf einen anderen
Windows-Rechner kopieren — die EXE läuft dort ohne Python-Installation.
Beim ersten Start legt die App `data\settings.json` und
`data\presets.json` neben der EXE an (Defaults: Dark Mode an, Polling
100 ms, vier Beispiel-Profile).

Nützliche Schalter:

- `.\build.ps1 -Clean` — Vorherige `dist/`, `build/` und `*.spec` löschen.
- `.\build.ps1 -KeepConsole` — EXE mit sichtbarer Konsole bauen (Tracebacks
  landen dort, praktisch zum Debuggen).
- `.\build.ps1 -Python C:\Pfad\zu\python.exe` — explizites Python wählen.

## Projektstruktur

```
ft991_audio_manager/
├── main.py                  # Einstiegspunkt
├── build.ps1                # PyInstaller-Build (Windows, onedir)
├── requirements.txt
├── cat/                     # Serielle CAT-Schicht (Threadsafe, Log)
├── mapping/                 # Encoder/Decoder für alle CAT-Kommandos
├── model/                   # Settings, Profile, Persistierung
├── gui/                     # PySide6-Widgets (Main, Meter, Profile, ...)
├── data/
│   └── presets.json         # Default-Profile (4 Beispiele)
└── tests/                   # ~200 Unit-Tests (unittest)
```

Die `mapping/`-Schicht ist bewusst frei von PySide6/QT-Abhängigkeiten,
damit sie ohne GUI testbar ist. Die `cat/`-Schicht serialisiert alle
Zugriffe über einen `RLock` — Poller und User-Writes können parallel
laufen, ohne Frames zu zerschießen.

## Tests laufen lassen

```powershell
cd ft991_audio_manager
python -m unittest discover -s tests -v
```

Aktuell sind es 200+ Tests. Die meisten brauchen weder serielle
Hardware noch eine Qt-Display-Verbindung; einige GUI-Tests werden ohne
Display übersprungen.

## Lizenz

Apache License 2.0 — siehe [`LICENSE`](LICENSE).

Das Programm spricht über die dokumentierte CAT-Schnittstelle des Yaesu
FT-991 / FT-991A; weder die Software noch dieses Projekt sind mit Yaesu
verbunden, „Yaesu", „FT-991" und „FT-991A" sind Marken ihrer jeweiligen
Inhaber.

## Mitwirken

Bug-Reports, Logs (insbesondere zum `RM6;`-Verhalten auf verschiedenen
Bändern und zum `GT0;`-Verhalten verschiedener Firmware-Stände) sowie
Pull-Requests sind sehr willkommen. Bitte vor PRs die Tests laufen
lassen und für neue CAT-Befehle eine entsprechende Mapping-Test-Abdeckung
mitliefern.
