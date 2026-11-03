# ⚙️ Einstellungen für die automatische Aktualisierung
# Hier anpassen und auf GitHub speichern («Commit changes»). Der nächste Lauf übernimmt die Werte.

WARM_AB = 18               # °C: Max.-Temperatur, ab der es als «warm» zählt (im Dashboard verstellbar)
START_ORT = "Zürich"       # Startort für Auto- und ÖV-Reisezeit: Ortsname oder Koordinaten wie "47.37, 8.54"
MAX_FAHRZEIT_H = 4         # weiter entfernte Gegenden bekommen keine Auto-Fahrzeit
OEV = True                 # ÖV-Reisezeit (nur Schweiz) über opentransportdata.swiss
OEV_ABFAHRT = "Sa 08:00"   # Abfahrt für die ÖV-Verbindungen: Wochentag und Uhrzeit
OEV_MAX_ZIELE = 250        # so viele Schweizer Orte werden abgefragt (etwa 1 Sekunde pro Ort)
REISEZEIT_TAGE = 7         # Reisezeiten so viele Tage wiederverwenden, statt sie bei jedem Lauf neu abzufragen
ENSEMBLE = True            # Sicherheit aus dem Ensemble rechnen (dauert einige Minuten länger)
TAGE_GESAMT = 10           # Tage insgesamt: ICON bis Tag 5, danach IFS als Trend (5 = ohne IFS)
DLAT, DLON = 0.03, 0.045   # Rasterweite in Grad, etwa 3.3 km
ZEITZONE = "Europe/Zurich"
IFS_GELAENDE = True        # IFS-Temperatur mit dem Geländemuster von ICON verfeinern
