# Campingwetter

Ein Dashboard, das zeigt, wo es zum Campen oder Wandern trocken, sonnig und warm wird. GitHub lädt viermal am Tag die neusten Prognosen von MeteoSchweiz und ECMWF, rechnet die Eignung und stellt die Seite auf Cloudflare Pages. Wer die Seite neu lädt, sieht immer den neusten Stand.

## Was hier liegt

| Datei | Wozu |
|---|---|
| `campingwetter.py` | lädt die Daten, rechnet und schreibt das Dashboard nach `site/index.html` |
| `einstellungen.py` | deine Einstellungen (Startort, ÖV-Abfahrt, Ensemble …) |
| `vorlage.html` | Aussehen und Bedienung des Dashboards |
| `.github/workflows/aktualisieren.yml` | Zeitplan: wann GitHub das Skript startet und die Seite hochlädt |
| `workflow-kopie.yml` | dieselbe Datei noch einmal, falls du den Ordner `.github` nicht hochladen kannst |

## Einrichten (einmal, etwa 20 Minuten)

### 1. Repository auf GitHub anlegen
1. Auf [github.com](https://github.com) ein Konto anlegen oder anmelden.
2. Oben rechts **+ → New repository**. Name zum Beispiel `campingwetter`.
3. **Public** wählen (empfohlen): Dann sind die Rechenminuten gratis und unbegrenzt. Im Code stehen keine Schlüssel. Bei **Private** hast du 2000 Gratisminuten pro Monat; das reicht für etwa zwei Läufe pro Tag (siehe unten).
4. **Create repository**.

### 2. Dateien hochladen
1. Im neuen Repository auf **uploading an existing file** klicken (oder **Add file → Upload files**).
2. Alle Dateien aus diesem Ordner hineinziehen und **Commit changes** klicken.
3. Den Ordner `.github` sieht man auf dem Mac und unter Windows oft nicht, weil er mit einem Punkt beginnt. Prüfe darum im Repository, ob `.github/workflows/aktualisieren.yml` da ist. Falls nicht:
   - **Add file → Create new file**
   - als Namen genau `.github/workflows/aktualisieren.yml` eintippen (die Schrägstriche legen die Ordner an)
   - den Inhalt von `workflow-kopie.yml` hineinkopieren und **Commit changes**

### 3. Cloudflare vorbereiten
1. Auf [dash.cloudflare.com](https://dash.cloudflare.com) ein kostenloses Konto anlegen.
2. **Konto-ID** kopieren: Sie steht in der Übersicht deines Kontos (rechts, «Account ID»).
3. **API-Token** erstellen: Profil oben rechts → **My Profile → API Tokens → Create Token → Create Custom Token**. Berechtigung: **Account → Cloudflare Pages → Edit**. Token erstellen und kopieren; er wird nur einmal angezeigt.

### 4. Schlüssel bei GitHub hinterlegen
Im Repository: **Settings → Secrets and variables → Actions → New repository secret**. Lege diese Secrets an (Name genau so schreiben):

| Name | Wert | Pflicht? |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | Token aus Schritt 3 | ja |
| `CLOUDFLARE_ACCOUNT_ID` | Konto-ID aus Schritt 3 | ja |
| `ORS_SCHLUESSEL` | Schlüssel von openrouteservice | nein, ohne ihn keine Auto-Fahrzeit |
| `OTD_SCHLUESSEL` | Token für «OJP 2.0» von opentransportdata.swiss | nein, ohne ihn keine ÖV-Zeit |

Optional unter dem Reiter **Variables**: `CF_PROJEKT` = Name der Seite (Standard `campingwetter`). Die Seite heisst dann `https://NAME.pages.dev`. Ist der Name schon vergeben, hängt Cloudflare ein Kürzel an; die genaue Adresse steht am Ende des Laufs und im Cloudflare-Dashboard unter **Workers & Pages**.

### 5. Ersten Lauf starten
1. Im Repository den Reiter **Actions** öffnen. Falls GitHub fragt, die Workflows aktivieren.
2. Links **Campingwetter aktualisieren** wählen, rechts **Run workflow → Run workflow**.
3. Der erste Lauf dauert 20 bis 40 Minuten: Die Python-Pakete werden einmal installiert, und die Reisezeiten werden abgefragt. Spätere Läufe brauchen etwa 10 bis 20 Minuten.
4. Ist der Lauf grün, steht die Seite online. Die Adresse steht im letzten Schritt des Laufs («Auf Cloudflare Pages veröffentlichen»).

Danach läuft alles von selbst, viermal am Tag.

## Ändern

- **Einstellungen:** `einstellungen.py` im Browser öffnen, auf den Stift klicken, ändern, **Commit changes**. Der nächste Lauf übernimmt die Werte. Für eine sofortige Aktualisierung: **Actions → Run workflow**.
- **Zeitplan:** in `.github/workflows/aktualisieren.yml` die Zeile mit `cron`. `"20 3,9,15,21 * * *"` heisst 03:20, 09:20, 15:20 und 21:20 UTC (in der Schweiz eine bzw. zwei Stunden später). Für zweimal am Tag: `"20 3,15 * * *"`.
- **Reisezeiten:** Sie werden nur alle 7 Tage neu abgefragt (`REISEZEIT_TAGE`) oder sobald du Startort oder ÖV-Abfahrt änderst.

## Gut zu wissen

- **Öffentlich:** Die Seite ist für alle erreichbar, die die Adresse kennen. Mit **Cloudflare Access** (Zero Trust, bis 50 Personen gratis) lässt sie sich hinter ein Login stellen.
- **Rechenminuten:** Ein Lauf braucht etwa 10 bis 20 Minuten. Bei einem privaten Repository mit 2000 Gratisminuten im Monat reichen zwei Läufe pro Tag. Mit `ENSEMBLE = False` wird jeder Lauf deutlich kürzer.
- **60-Tage-Regel:** GitHub pausiert Zeitpläne in öffentlichen Repositories, wenn 60 Tage lang niemand etwas am Repository geändert hat. Dann kommt eine E-Mail; ein Klick auf **Enable workflow** im Reiter Actions oder eine kleine Änderung startet ihn wieder.
- **Fehler:** Schlägt ein Lauf fehl, bleibt die letzte funktionierende Seite online, und GitHub schickt eine E-Mail. Im Lauf zeigt der rote Schritt die Meldung.

## Quellen

Wetterdaten: MeteoSchweiz (ICON-CH1, ICON-CH2) und ECMWF (IFS), Lizenz CC BY 4.0. Grenzen: Natural Earth. Ortsnamen: GeoNames (CC BY 4.0). Fahrzeit: openrouteservice, Kartendaten © OpenStreetMap-Mitwirkende. ÖV: opentransportdata.swiss.
