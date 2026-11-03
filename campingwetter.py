#!/usr/bin/env python3
"""Campingwetter: lädt die neusten Prognosen von MeteoSchweiz (ICON-CH1/CH2) und ECMWF (IFS), rechnet die Eignung
zum Campen und schreibt das Dashboard nach site/index.html. Läuft automatisch auf GitHub Actions.
Die Einstellungen stehen in einstellungen.py, die Schlüssel kommen aus den GitHub-Secrets (Umgebungsvariablen)."""
import os, sys, json, math, time, pathlib

HIER = pathlib.Path(__file__).resolve().parent
os.chdir(HIER)                      # cache/, site/ und Zwischendateien liegen neben dem Skript
sys.path.insert(0, str(HIER))

import eccodes_cosmo_resources      # Definitionen von MeteoSchweiz für die ICON-Daten
os.environ.setdefault("ECCODES_DEFINITION_PATH", str(eccodes_cosmo_resources.get_definitions_path()))
os.environ["ECCODES_VERSION_CHECK_OFF"] = "1"

from einstellungen import *         # noqa: F401,F403
ORS_SCHLUESSEL = os.environ.get("ORS_SCHLUESSEL", "")
OTD_SCHLUESSEL = os.environ.get("OTD_SCHLUESSEL", "")
CF_PROJEKT = ""                     # das Veröffentlichen übernimmt der GitHub-Workflow

import numpy as np

CACHE = pathlib.Path("cache")
CACHE.mkdir(exist_ok=True)


def cache_lesen(name, start, zusatz=""):
    """Reisezeiten der letzten Tage wiederverwenden, solange Startort, Raster und Zusatz (z. B. ÖV-Abfahrt) gleich sind."""
    datei = CACHE / f"{name}.npz"
    if not start or not datei.exists():
        return None
    try:
        z = np.load(datei, allow_pickle=False)
        m = json.loads(str(z["meta"]))
        gleich = (m["start"] == [round(start["lat"], 4), round(start["lon"], 4)] and m["zusatz"] == zusatz
                  and m["raster"] == [round(LON0, 4), round(LAT0, 4), DLON, DLAT, NX, NY])
        if gleich and time.time() - m["zeit"] < REISEZEIT_TAGE * 86400:
            return z["werte"]
    except Exception as e:
        print("Zwischenspeicher nicht lesbar:", repr(e)[:120])
    return None


def cache_schreiben(name, start, werte, zusatz=""):
    m = dict(start=[round(start["lat"], 4), round(start["lon"], 4)], zusatz=zusatz, zeit=time.time(),
             raster=[round(LON0, 4), round(LAT0, 4), DLON, DLAT, NX, NY])
    np.savez_compressed(CACHE / f"{name}.npz", werte=werte, meta=json.dumps(m))


VORLAGE = (HIER / "vorlage.html").read_text(encoding="utf-8")


# ====================================================================================================
# ICON laden und aufbereiten
# ====================================================================================================
# 📥 Daten laden und auf ein regelmässiges Raster bringen
# Falls die Einstellungen übersprungen wurden oder Colab die Sitzung neu gestartet hat: Standardwerte und Pfade wiederherstellen
import sys, os, pathlib
for _name, _wert in dict(WARM_AB=18, DLAT=0.03, DLON=0.045, ZEITZONE="Europe/Zurich", TAGE_GESAMT=10, IFS_GELAENDE=True, BOEEN_BIS=50, NACHT_AB=2,
                     START_ORT="Zürich", ORS_SCHLUESSEL="", MAX_FAHRZEIT_H=4, ENSEMBLE=True,
                     OEV=True, OTD_SCHLUESSEL="", OEV_ABFAHRT="Sa 08:00", OEV_MAX_ZIELE=250, CF_PROJEKT="").items():
    globals().setdefault(_name, _wert)
for _venv in (pathlib.Path(".venv"), pathlib.Path("/content/opendata-nwp-demos/.venv")):
    _site = _venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    if _site.exists():
        site = _site
        if not {str(_site), str(_site.resolve())} & set(sys.path):
            sys.path.insert(0, str(_site.resolve()))
        os.environ.setdefault("ECCODES_DEFINITION_PATH", str((_venv / "share/eccodes-cosmo-resources/definitions").resolve()))
        os.environ.setdefault("ECCODES_VERSION_CHECK_OFF", "1")
        break

import json, zlib, base64, math, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import earthkit.data as ekd

ekd.config.set("cache-policy", "temporary")
TZ, UTC = ZoneInfo(ZEITZONE), timezone.utc
WT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
tag_text = lambda d: f"{WT[d.weekday()]} {d:%d.%m.}"
MODELLE = {
    "ch1": dict(collection="ogd-forecasting-icon-ch1", name="ICON-CH1", horizont=33),
    "ch2": dict(collection="ogd-forecasting-icon-ch2", name="ICON-CH2", horizont=120),
}


def lade(modell, variable, ref_time, stunden, perturbed=False):
    """Lädt eine Variable für die gewünschten Vorhersagestunden, den Kontrolllauf oder (perturbed=True) die gestörten Läufe.
    Rückgabe: Referenzzeit (UTC), Breite, Länge, {Stunde: Werte je Modellzelle} bzw. {Stunde: Läufe × Modellzellen}."""
    stunden = sorted(int(h) for h in stunden)
    lt = [timedelta(hours=h) for h in stunden]
    fc = ekd.from_source(
        "meteoswiss-opendata",
        collection=MODELLE[modell]["collection"],
        variable=variable,
        ref_time=ref_time,
        perturbed=perturbed,
        lead_time=lt if len(lt) > 1 else lt[0],
    )
    ds = fc.to_xarray(time_dims=["forecast_reference_time", "step"], squeeze=False)
    da = ds[variable] if variable in ds.data_vars else ds[list(ds.data_vars)[0]]
    try:
        ref = pd.to_datetime(da["forecast_reference_time"].values[0]).to_pydatetime().replace(tzinfo=UTC)
        lat = np.asarray(da["latitude"].values, dtype="float64").ravel()
        lon = np.asarray(da["longitude"].values, dtype="float64").ravel()
        st = np.asarray(da["step"].values)
    except Exception:
        print("Unerwarteter Aufbau des Datensatzes:\n", ds)
        raise
    if np.nanmax(np.abs(lat)) < 3.2:  # Koordinaten in Radiant
        lat, lon = np.degrees(lat), np.degrees(lon)
    lon = np.where(lon > 180, lon - 360, lon)
    st_h = st / np.timedelta64(1, "h") if st.dtype.kind == "m" else st.astype("float64")
    felder = {}
    for h in stunden:
        i = int(np.argmin(np.abs(st_h - h)))
        if abs(float(st_h[i]) - h) > 0.01:
            raise RuntimeError(f"{variable}: Vorhersagestunde {h} fehlt im Datensatz (vorhanden: {np.round(st_h).astype(int).tolist()[:12]})")
        x = da.isel(forecast_reference_time=0, step=i)
        if perturbed:
            mdim = next((d for d in x.dims if d in ("member", "number", "eps", "realization", "ensemble_member")), None)
            if mdim is None:
                print(ds)
                raise RuntimeError(f"{variable}: keine Ensemble-Dimension gefunden, dims={x.dims}")
            a = np.asarray(x.transpose(mdim, ...).values, dtype="float32").reshape(x.sizes[mdim], -1)
            if a.shape[1] != lat.size:
                raise RuntimeError(f"{variable}: unerwartete Datenform dims={x.dims}, Zellen={lat.size}")
            felder[h] = a
            continue
        a = np.asarray(x.values, dtype="float32").reshape(-1)
        if a.size != lat.size:
            print(ds)
            raise RuntimeError(f"{variable}: unerwartete Datenform dims={da.dims} shape={da.shape}, Zellen={lat.size}")
        felder[h] = a
    return ref, lat, lon, felder


# 1) Neuste vollständige Modellläufe finden: «latest» wählt den neusten Lauf, der die verlangte Stunde enthält.
#    Mit der letzten Stunde des Laufs ist sichergestellt, dass der Lauf fertig hochgeladen ist.
laeufe = {}
for key in ("ch2", "ch1"):
    H = MODELLE[key]["horizont"]
    try:
        ref, lat, lon, f = lade(key, "TOT_PREC", "latest", [H])
        laeufe[key] = dict(ref=ref, lat=lat, lon=lon, felder={"TOT_PREC": f})
        print(f"{MODELLE[key]['name']}: Lauf vom {ref:%d.%m.%Y %H} UTC, {lat.size:,} Zellen".replace(",", "'"))
    except Exception as e:
        if key == "ch2":
            raise
        print("ICON-CH1 ist nicht verfügbar, es wird nur ICON-CH2 verwendet:", repr(e)[:300])

# 2) Tage planen: ICON-CH1, wo es bis Mitternacht reicht, sonst ICON-CH2
jetzt = datetime.now(TZ)
plan = []
for k in range(7):
    d = jetzt.date() + timedelta(days=k)
    d1 = d + timedelta(days=1)
    a, b = datetime(d.year, d.month, d.day, tzinfo=TZ), datetime(d1.year, d1.month, d1.day, tzinfo=TZ)
    for key in ("ch1", "ch2"):
        if key not in laeufe:
            continue
        ref, H = laeufe[key]["ref"], MODELLE[key]["horizont"]
        ws = max(a, ref)                      # heute zählt erst ab Modellstart
        if (b - ref).total_seconds() / 3600 > H or ws >= b:
            continue
        if ws > a and ws.astimezone(TZ).hour > 10:
            continue                          # vom heutigen Tag bleibt zu wenig übrig
        std = lambda t, ref=ref: int(round((t - ref).total_seconds() / 3600))
        t_max = [a + timedelta(hours=h) for h in (13, 14, 15, 16)]   # Nachmittag, Ortszeit
        t_min = [b + timedelta(hours=h) for h in (5, 6, 7)]          # früher Morgen danach
        plan.append(dict(datum=d, modell=key, ws=ws, we=b, teil=ws > a, l0=std(ws), l1=std(b),
                         hx=[std(t) for t in t_max if t >= ws and std(t) <= H],
                         hn=[]))   # Nachttemperatur zählt nicht
        break
if not plan:
    raise RuntimeError("Kein Tag liegt vollständig im Vorhersagezeitraum.")
for t in plan:
    print(f"{tag_text(t['datum'])}  {MODELLE[t['modell']]['name']}  Stunden {t['l0']}–{t['l1']}" + ("  (nur Rest des Tages)" if t["teil"] else ""))

# 3) Felder laden: Regen und Sonnenscheindauer sind seit Modellstart aufsummiert, die Temperatur ist ein Momentanwert
for key, L in laeufe.items():
    tage = [t for t in plan if t["modell"] == key]
    akk = sorted({h for t in tage for h in (t["l0"], t["l1"]) if h > 0})
    tmp = sorted({h for t in tage for h in t["hx"] + t["hn"]})
    for var, hs in (("TOT_PREC", akk), ("DURSUN", akk), ("T_2M", tmp)):
        fehlt = [h for h in hs if h not in L["felder"].setdefault(var, {})]
        if fehlt:
            print(f"Lade {MODELLE[key]['name']} {var}: {len(fehlt)} Felder …")
            L["felder"][var].update(lade(key, var, L["ref"], fehlt)[3])
    # Wind für die gefühlte Temperatur, zu den gleichen Stunden wie die Temperatur
    for var in ("U_10M", "V_10M"):
        try:
            fehlt = [h for h in tmp if h not in (L["felder"].setdefault(var, {}) or {})]
            if fehlt and L["felder"][var] is not None:
                print(f"Lade {MODELLE[key]['name']} {var}: {len(fehlt)} Felder …")
                L["felder"][var].update(lade(key, var, L["ref"], fehlt)[3])
        except Exception as e:
            L["felder"]["U_10M"] = L["felder"]["V_10M"] = None
            print(f"Wind von {MODELLE[key]['name']} nicht verfügbar, die gefühlte Temperatur entspricht dann der gemessenen:", repr(e)[:200])

# 4) Regelmässiges Raster über das ganze Modellgebiet; jede Rasterzelle ist das Mittel der Modellzellen darin
basis = laeufe["ch2"]
LON0, LAT0 = math.floor(basis["lon"].min() / DLON) * DLON, math.floor(basis["lat"].min() / DLAT) * DLAT
NX, NY = int(math.ceil((basis["lon"].max() - LON0) / DLON)), int(math.ceil((basis["lat"].max() - LAT0) / DLAT))
LON2D, LAT2D = np.meshgrid(LON0 + (np.arange(NX) + 0.5) * DLON, LAT0 + (np.arange(NY) + 0.5) * DLAT)


def fuelle_luecken(g):
    """Füllt einzelne leere Zellen im Innern mit dem Mittel der Nachbarn (der Rand bleibt leer)."""
    leer = ~np.isfinite(g)
    if not leer.any():
        return g
    w, c = np.pad(np.where(leer, 0, g), 1), np.pad((~leer).astype("float32"), 1)
    s = sum(w[1 + dy:NY + 1 + dy, 1 + dx:NX + 1 + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1))
    n = sum(c[1 + dy:NY + 1 + dy, 1 + dx:NX + 1 + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1))
    out = g.copy()
    m = leer & (n >= 5)
    out[m] = s[m] / n[m]
    return out


class Raster:
    def __init__(self, lat, lon):
        ix, iy = np.floor((lon - LON0) / DLON).astype(np.int64), np.floor((lat - LAT0) / DLAT).astype(np.int64)
        self.ok = (ix >= 0) & (ix < NX) & (iy >= 0) & (iy < NY)
        self.flach = (iy * NX + ix)[self.ok]
        self.anz = np.bincount(self.flach, minlength=NX * NY)

    def __call__(self, werte):
        s = np.bincount(self.flach, weights=np.asarray(werte, dtype="float64")[self.ok], minlength=NX * NY)
        g = np.where(self.anz > 0, s / np.maximum(self.anz, 1), np.nan).astype("float32").reshape(NY, NX)
        return fuelle_luecken(g)


def moegliche_sonne(datum, ws, we):
    """Astronomisch mögliche Sonnenscheindauer in Stunden zwischen ws und we, je Rasterzelle."""
    n = datum.timetuple().tm_yday
    b = 2 * np.pi * (n - 81) / 364
    zeitgl = 9.87 * np.sin(2 * b) - 7.53 * np.cos(b) - 1.5 * np.sin(b)          # Minuten
    dek = np.radians(23.45 * np.sin(2 * np.pi * (284 + n) / 365))
    phi = np.radians(LAT2D)
    cos_h = (np.sin(np.radians(-0.833)) - np.sin(phi) * np.sin(dek)) / (np.cos(phi) * np.cos(dek))
    halb = np.degrees(np.arccos(np.clip(cos_h, -1, 1))) / 15                    # halbe Tageslänge in Stunden
    mittag = 12 - LON2D / 15 - zeitgl / 60                                      # Sonnenhöchststand, UTC
    m0 = datetime(datum.year, datum.month, datum.day, tzinfo=UTC)
    a, e = (ws - m0).total_seconds() / 3600, (we - m0).total_seconds() / 3600
    return np.clip(np.minimum(mittag + halb, e) - np.maximum(mittag - halb, a), 0, None).astype("float32")


for L in laeufe.values():
    L["raster"] = Raster(L["lat"], L["lon"])


def windchill(t_c, u, v):
    """Gefühlte Temperatur nach der Windchill-Formel; gilt bis 10 °C und ab 5 km/h Wind (Wind in 10 m Höhe)."""
    w = np.hypot(u, v) * 3.6
    w16 = np.power(np.maximum(w, 0.1), 0.16)
    wc = 13.12 + 0.6215 * t_c - 11.37 * w16 + 0.3965 * t_c * w16
    return np.where((t_c <= 10) & (w >= 4.8), np.minimum(wc, t_c), t_c)

tage = []
for t in plan:
    L = laeufe[t["modell"]]
    F, R = L["felder"], L["raster"]
    akk = lambda var, h: F[var][h] if h > 0 else 0.0
    regen = R(np.maximum(akk("TOT_PREC", t["l1"]) - akk("TOT_PREC", t["l0"]), 0))               # mm
    sonne = R(np.maximum(akk("DURSUN", t["l1"]) - akk("DURSUN", t["l0"]), 0)) / 3600            # Stunden
    wind_ok = bool(F.get("U_10M")) and bool(F.get("V_10M"))
    def temp(hs, fn, gefuehlt=False):
        if not hs:
            return np.full((NY, NX), np.nan, dtype="float32")
        werte = []
        for h in hs:
            tc = F["T_2M"][h] - 273.15 if np.nanmean(F["T_2M"][h]) > 150 else F["T_2M"][h]        # Kelvin -> °C
            werte.append(windchill(tc, F["U_10M"][h], F["V_10M"][h]) if gefuehlt else tc)
        return R(fn(werte))
    tmax, tmin = temp(t["hx"], np.maximum.reduce), temp(t["hn"], np.minimum.reduce)
    gmax = temp(t["hx"], np.maximum.reduce, True) if wind_ok else tmax
    gmin = temp(t["hn"], np.minimum.reduce, True) if wind_ok else tmin
    moegl = np.where(np.isfinite(regen), moegliche_sonne(t["datum"], t["ws"], t["we"]), np.nan)
    boeen = np.full((NY, NX), np.nan, dtype="float32")   # Böen zählen nicht
    tage.append(dict(t, trend=False, regen=regen, sonne=np.minimum(sonne, moegl), moegl=moegl, tmax=tmax, tmin=tmin, gmax=gmax, gmin=gmin, boeen=boeen))
    print(f"{tag_text(t['datum'])}: Regen im Mittel {np.nanmean(regen):.1f} mm, Sonne {np.nanmean(sonne):.1f} von {np.nanmean(moegl):.1f} h, Maximum {np.nanmean(tmax):.0f} °C")

# 5) Landesgrenzen für die Karten (ohne sie funktioniert alles, nur ohne Grenzlinien)
try:
    with urllib.request.urlopen("https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-50m.json", timeout=30) as r:
        geo = json.loads(r.read().decode("utf-8"))
except Exception as e:
    geo = None
    print("Landesgrenzen konnten nicht geladen werden:", repr(e)[:200])
# 6) Ortsliste für die Suche im Dashboard (GeoNames, Orte ab 1000 Einwohnern im Modellgebiet)
orte = []
try:
    import zipfile, io
    with urllib.request.urlopen("https://download.geonames.org/export/dump/cities1000.zip", timeout=90) as r:
        _zip = zipfile.ZipFile(io.BytesIO(r.read()))
    with _zip.open("cities1000.txt") as fh:
        for zeile in io.TextIOWrapper(fh, encoding="utf-8"):
            f = zeile.rstrip("\n").split("\t")
            try:
                la, lo, ew = float(f[4]), float(f[5]), int(f[14] or 0)
            except (ValueError, IndexError):
                continue
            if ew >= 1000 and LAT0 <= la <= LAT0 + NY * DLAT and LON0 <= lo <= LON0 + NX * DLON:
                orte.append([f[1], round(la, 3), round(lo, 3), ew, f[8]])
    orte = sorted(orte, key=lambda o: -o[3])[:15000]
    print(f"Ortsliste: {len(orte)} Orte")
except Exception as e:
    print("Ortsliste konnte nicht geladen werden, die Suche kennt dann nur grössere Städte:", repr(e)[:200])
print(f"Raster: {NX} × {NY} Zellen, {len(tage)} ICON-Tage")

# ====================================================================================================
# IFS als Trend
# ====================================================================================================
# 🔭 Trend für die Tage nach ICON-CH2: ECMWF IFS (0.25°, rund 25 km)
# Schlägt hier etwas fehl, läuft das Notebook mit den ICON-Tagen weiter.
import sys, subprocess, importlib, pathlib, warnings

MODELLE["ifs"] = dict(name="IFS")


def ifs_einbauen():
    import eccodes
    try:
        from ecmwf.opendata import Client
    except ImportError:
        ziel = ["--target", str(pathlib.Path(site).resolve())] if "site" in globals() else []
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", *ziel, "ecmwf-opendata"], check=True)
        importlib.invalidate_caches()
        from ecmwf.opendata import Client

    # Neuster Lauf, der 240 Stunden enthält (das sind die Läufe von 00 und 12 UTC)
    fehler = None
    for quelle in ("ecmwf", "aws"):
        try:
            try:
                client = Client(source=quelle, model="ifs", resol="0p25")
            except TypeError:
                client = Client(source=quelle)
            ref = client.latest(type="fc", step=240, param="tp").replace(tzinfo=UTC)
            break
        except Exception as e:
            fehler = e
    else:
        raise fehler
    print(f"IFS: Lauf vom {ref:%d.%m.%Y %H} UTC (Quelle {quelle})")
    std = lambda t: int(round((t - ref).total_seconds() / 3600))
    gueltig = lambda s: 0 <= s <= 360 and (s % 3 == 0 if s <= 144 else s % 6 == 0)

    # Tage planen. IFS liefert nur alle 3 bis 6 Stunden Werte, darum zählt ein Tag hier von 00 bis 24 UTC
    ifs_plan, d = [], max(t["datum"] for t in tage) + timedelta(days=1)
    while len(tage) + len(ifs_plan) < TAGE_GESAMT:
        a = datetime(d.year, d.month, d.day, tzinfo=UTC)
        b = a + timedelta(days=1)
        if not (gueltig(std(a)) and gueltig(std(b))):
            break
        ifs_plan.append(dict(datum=d, ws=a, we=b, l0=std(a), l1=std(b),
                             hx=[s for s in (std(a + timedelta(hours=h)) for h in (12, 15, 18)) if gueltig(s)],
                             hn=[]))   # Nachttemperatur zählt nicht
        d += timedelta(days=1)
    if not ifs_plan:
        print("IFS: keine zusätzlichen Tage nötig oder verfügbar.")
        return

    def lies_grib(pfad):
        """Liest alle Felder einer GRIB-Datei: {Endstunde: Feld (Nj × Ni)}, Breiten, Längen, Einheit."""
        felder, lat, lon, einheit = {}, None, None, ""
        with open(pfad, "rb") as fh:
            while True:
                gid = eccodes.codes_grib_new_from_file(fh)
                if gid is None:
                    break
                try:
                    ni, nj = int(eccodes.codes_get(gid, "Ni")), int(eccodes.codes_get(gid, "Nj"))
                    if lat is None:
                        lat = np.asarray(eccodes.codes_get_array(gid, "latitudes"), dtype="float64").reshape(nj, ni)[:, 0]
                        lon = np.asarray(eccodes.codes_get_array(gid, "longitudes"), dtype="float64").reshape(nj, ni)[0, :]
                        try:
                            einheit = str(eccodes.codes_get(gid, "units"))
                        except Exception:
                            pass
                    felder[int(eccodes.codes_get(gid, "endStep"))] = np.asarray(eccodes.codes_get_values(gid), dtype="float32").reshape(nj, ni)
                finally:
                    eccodes.codes_release(gid)
        return felder, lat, lon, einheit

    def auf_raster(feld, lat, lon):
        """Bilineare Interpolation vom IFS-Gitter auf das Dashboard-Raster."""
        lonn = (lon + 180) % 360 - 180
        sp = np.where((lonn >= LON0 - 0.6) & (lonn <= LON0 + NX * DLON + 0.6))[0]
        ze = np.where((lat >= LAT0 - 0.6) & (lat <= LAT0 + NY * DLAT + 0.6))[0]
        sp, ze = sp[np.argsort(lonn[sp])], ze[np.argsort(lat[ze])]
        a, la, lo = feld[np.ix_(ze, sp)].astype("float64"), lat[ze], lonn[sp]
        fy, fx = np.interp(LAT2D[:, 0], la, np.arange(la.size)), np.interp(LON2D[0, :], lo, np.arange(lo.size))
        y0, x0 = np.clip(np.floor(fy).astype(int), 0, la.size - 2), np.clip(np.floor(fx).astype(int), 0, lo.size - 2)
        wy, wx = (fy - y0)[:, None], (fx - x0)[None, :]
        return ((a[np.ix_(y0, x0)] * (1 - wx) + a[np.ix_(y0, x0 + 1)] * wx) * (1 - wy)
                + (a[np.ix_(y0 + 1, x0)] * (1 - wx) + a[np.ix_(y0 + 1, x0 + 1)] * wx) * wy).astype("float32")

    def hole(param, schritte):
        schritte = sorted(set(schritte))
        ziel = f"ifs_{param}.grib2"
        print(f"Lade IFS {param}: {len(schritte)} Felder …")
        client.retrieve(date=f"{ref:%Y%m%d}", time=ref.hour, type="fc", step=schritte, param=param, target=ziel)
        felder, lat, lon, einheit = lies_grib(ziel)
        fehlt = [s for s in schritte if s not in felder]
        if fehlt:
            raise RuntimeError(f"IFS {param}: Stunden {fehlt} fehlen (vorhanden: {sorted(felder)})")
        return {s: auf_raster(felder[s], lat, lon) for s in schritte}, einheit, float(max(np.nanmax(felder[s]) for s in schritte))

    def temperatur(schritte, art):  # Höchst- oder Tiefstwert der letzten 3 bzw. 6 Stunden; sonst der Momentanwert
        kurz, lang = [s for s in schritte if s <= 144], [s for s in schritte if s > 144]
        if not schritte:
            return {}
        try:
            f = {}
            if kurz:
                f.update(hole(f"{art}2t3", kurz)[0])
            if lang:
                f.update(hole(f"{art}2t6", lang)[0])
        except Exception as e:
            print(f"IFS {art}2t nicht verfügbar ({repr(e)[:120]}), verwende 2t")
            f = hole("2t", schritte)[0]
        return f

    akk = sorted({s for t in ifs_plan for s in (t["l0"], t["l1"]) if s > 0})
    tp, einheit, tp_max = hole("tp", akk)
    in_mm = 1000.0 if einheit == "m" or (einheit not in ("kg m**-2", "mm") and tp_max < 5) else 1.0   # Meter -> mm
    strahlung = hole("ssrd", akk)[0]                                   # Globalstrahlung, J/m², aufsummiert
    t_hoch = temperatur([s for t in ifs_plan for s in t["hx"]], "mx")
    t_tief = temperatur([s for t in ifs_plan for s in t["hn"]], "mn")
    try:   # Wind für die gefühlte Temperatur
        wind_s = sorted({s for t in ifs_plan for s in t["hx"] + t["hn"]})
        wu, wv = hole("10u", wind_s)[0], hole("10v", wind_s)[0]
    except Exception as e:
        wu = wv = None
        print("IFS-Wind nicht verfügbar, die gefühlte Temperatur entspricht dann der gemessenen:", repr(e)[:200])

    boe = None   # Böen zählen nicht

    def glaetten(g, ry, rx):
        """Mittel über ein Fenster von (2·ry+1) × (2·rx+1) Zellen, leere Zellen werden übergangen."""
        ok = np.isfinite(g)
        def summe(x):
            p = np.pad(x, ((ry + 1, ry), (rx + 1, rx))).cumsum(0).cumsum(1)
            return p[2 * ry + 1:, 2 * rx + 1:] - p[:-2 * ry - 1, 2 * rx + 1:] - p[2 * ry + 1:, :-2 * rx - 1] + p[:-2 * ry - 1, :-2 * rx - 1]
        s, n = summe(np.where(ok, g, 0).astype("float64")), summe(ok.astype("float64"))
        return np.where(n > 0, s / np.maximum(n, 1), np.nan)

    def gelaendemuster(name):
        """Feines Temperaturmuster (Täler wärmer, Gipfel kälter) aus den ganzen ICON-Tagen: Feld minus 25-km-Mittel."""
        ry, rx = max(1, round(0.125 / DLAT)), max(1, round(0.125 / DLON))
        teile = [t[name] - glaetten(t[name], ry, rx) for t in tage if not t["teil"] and np.isfinite(t[name]).any()]
        if not teile or not IFS_GELAENDE:
            return np.zeros((NY, NX), dtype="float32")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return np.nan_to_num(np.nanmean(teile, axis=0)).astype("float32")

    muster_hoch, muster_tief = gelaendemuster("tmax"), gelaendemuster("tmin")
    gebiet = np.any([np.isfinite(t["regen"]) for t in tage], axis=0)          # gleicher Ausschnitt wie ICON
    celsius = lambda g: g - 273.15 if np.nanmean(g) > 150 else g
    neu = []
    for t in ifs_plan:
        diff = lambda f: np.maximum(f[t["l1"]] - (f[t["l0"]] if t["l0"] > 0 else 0.0), 0)
        moegl = moegliche_sonne(t["datum"], t["ws"], t["we"])
        # Sonnenschein aus der Globalstrahlung geschätzt (Ångström-Prescott): Anteil = (G/G0 − 0.25) / 0.55
        j = t["datum"].timetuple().tm_yday
        phi, dek = np.radians(LAT2D), 0.409 * np.sin(2 * np.pi * j / 365 - 1.39)
        ws_ = np.arccos(np.clip(-np.tan(phi) * np.tan(dek), -1, 1))
        g0 = 24 * 60 / np.pi * 0.0820e6 * (1 + 0.033 * np.cos(2 * np.pi * j / 365)) * (ws_ * np.sin(phi) * np.sin(dek) + np.cos(phi) * np.cos(dek) * np.sin(ws_))
        anteil = np.clip((diff(strahlung) / np.maximum(g0, 1) - 0.25) / 0.55, 0, 1)
        leer = np.full((NY, NX), np.nan, dtype="float32")
        def reihe(felder, schritte, muster, fn):
            if not schritte:
                return leer, leer
            echt = [celsius(felder[s]) + muster for s in schritte]
            gef = [windchill(x, wu[s], wv[s]) for x, s in zip(echt, schritte)] if wu is not None else echt
            return fn(echt), fn(gef)
        tmax, gmax = reihe(t_hoch, t["hx"], muster_hoch, np.maximum.reduce)
        tmin, gmin = reihe(t_tief, t["hn"], muster_tief, np.minimum.reduce)
        mask = lambda g: np.where(gebiet, g, np.nan).astype("float32")
        boeen = leer
        neu.append(dict(t, modell="ifs", teil=False, trend=True, regen=mask(diff(tp) * in_mm), sonne=mask(anteil * moegl),
                        moegl=mask(moegl), tmax=mask(tmax), tmin=mask(tmin), gmax=mask(gmax), gmin=mask(gmin), boeen=mask(boeen)))
        print(f"{tag_text(t['datum'])}: Regen im Mittel {np.nanmean(neu[-1]['regen']):.1f} mm, Sonne {np.nanmean(neu[-1]['sonne']):.1f} von {np.nanmean(moegl):.1f} h, Maximum {np.nanmean(neu[-1]['tmax']):.0f} °C  (IFS, Trend)")
    tage.extend(neu)
    laeufe["ifs"] = dict(ref=ref)


if TAGE_GESAMT > len(tage):
    try:
        ifs_einbauen()
    except Exception as e:
        print("IFS konnte nicht eingebaut werden, das Dashboard zeigt nur die ICON-Tage. Meldung:")
        print("  ", repr(e)[:600])
print(f"{len(tage)} Tage insgesamt")

# ====================================================================================================
# Fahrzeit Auto
# ====================================================================================================
# 🚗 Fahrzeit ab Startort (openrouteservice)
# Gerechnet wird zu den Orten der Ortsliste (je rund 6 km der grösste Ort). Jede Rasterzelle übernimmt die Zeit
# des nächsten erreichbaren Orts plus den Rest der Strecke mit etwa 1.2 Minuten pro Kilometer.
import json, re, time, urllib.request, urllib.error

fahrzeit, start = None, None


def http(url, data=None, headers=None, timeout=200):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "campingwetter-notebook (privat)", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def km(la1, lo1, la2, lo2):
    return math.hypot((lo2 - lo1) * math.cos(math.radians((la1 + la2) / 2)) * 111.2, (la2 - la1) * 111.2)


_m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*[,; ]\s*(-?\d+(?:\.\d+)?)\s*$", START_ORT or "")
if _m:
    start = dict(name=START_ORT.strip(), lat=float(_m[1]), lon=float(_m[2]))
elif START_ORT:
    _k = START_ORT.strip().lower()
    _t = [o for o in orte if o[0].lower() == _k] or [o for o in orte if o[0].lower().startswith(_k)]
    if _t:
        _o = max(_t, key=lambda o: o[3])
        start = dict(name=_o[0], lat=_o[1], lon=_o[2])
    else:
        print(f"Startort «{START_ORT}» nicht gefunden. Bitte den Ortsnamen wie auf der Landkarte schreiben oder Koordinaten angeben.")

fahrzeit = cache_lesen("fahrzeit_auto", start)
schluessel = ORS_SCHLUESSEL
if not schluessel:
    try:
        from google.colab import userdata
        schluessel = userdata.get("ORS_SCHLUESSEL") or ""
    except Exception:
        schluessel = ""

if fahrzeit is not None:
    print(f"Fahrzeit Auto aus dem Zwischenspeicher (jünger als {REISEZEIT_TAGE} Tage)")
elif not schluessel:
    print("Kein Schlüssel für openrouteservice: Das Dashboard zeigt keine Fahrzeit.")
elif start and orte:
    try:
        reich, beste = MAX_FAHRZEIT_H * 80, {}
        for o in orte:
            if km(start["lat"], start["lon"], o[1], o[2]) <= reich:
                k_ = (int((o[1] - LAT0) / 0.055), int((o[2] - LON0) / 0.08))
                if k_ not in beste or o[3] > beste[k_][3]:
                    beste[k_] = o
        ziele = list(beste.values())
        print(f"Fahrzeit ab {start['name']} zu {len(ziele)} Orten …")
        dauer = []
        for k in range(0, len(ziele), 3000):
            teil = ziele[k:k + 3000]
            body = {"locations": [[start["lon"], start["lat"]]] + [[o[2], o[1]] for o in teil],
                    "sources": [0], "destinations": list(range(1, len(teil) + 1)), "metrics": ["duration"]}
            for versuch in range(3):
                try:
                    r = http("https://api.openrouteservice.org/v2/matrix/driving-car", data=json.dumps(body).encode(),
                             headers={"Authorization": schluessel, "Content-Type": "application/json"}, timeout=180)
                    break
                except urllib.error.HTTPError as e:
                    if e.code == 429 and versuch < 2:
                        print("   Minutenlimit von openrouteservice, warte 65 s …")
                        time.sleep(65)
                        continue
                    raise
            dauer += r["durations"][0]
            time.sleep(2)
        ok = [(o, d) for o, d in zip(ziele, dauer) if d is not None]
        if not ok:
            raise RuntimeError("openrouteservice hat für keinen Ort eine Route gefunden; liegt der Startort an einer Strasse?")
        plat = np.array([o[1] for o, _ in ok], dtype="float32")
        plon = np.array([o[2] for o, _ in ok], dtype="float32")
        pmin = np.array([d / 60 for _, d in ok], dtype="float32")
        clat, clon = LAT2D.ravel().astype("float32"), LON2D.ravel().astype("float32")
        kx = math.cos(math.radians(LAT0 + NY * DLAT / 2)) * 111.2
        fz = np.full(clat.size, np.nan, dtype="float32")
        for a in range(0, clat.size, 2000):
            dx = (clon[a:a + 2000, None] - plon[None, :]) * kx
            dy = (clat[a:a + 2000, None] - plat[None, :]) * 111.2
            d2 = dx * dx + dy * dy
            j = d2.argmin(1)
            dmin = np.sqrt(d2[np.arange(j.size), j])
            fz[a:a + 2000] = np.where(dmin <= 20, pmin[j] + dmin * 1.2, np.nan)
        fahrzeit = fz.reshape(NY, NX)
        cache_schreiben("fahrzeit_auto", start, fahrzeit)
        print(f"Fahrzeit für {len(ok)} Orte berechnet, auf die Karte übertragen")
    except Exception as e:
        print("Fahrzeit konnte nicht berechnet werden:", repr(e)[:300])

# ====================================================================================================
# Reisezeit ÖV
# ====================================================================================================
# 🚆 Reisezeit mit dem ÖV ab Startort (opentransportdata.swiss, OJP 2.0, nur Schweiz)
# Pro Ort wird die schnellste Verbindung ab der gewählten Abfahrt gesucht, von Koordinate zu Koordinate (inklusive Fussweg).
# Jede Rasterzelle übernimmt die Zeit des nächsten Orts plus den Rest zu Fuss (12 Minuten pro Kilometer, höchstens 4 km).
import re, time, urllib.request, urllib.error, xml.etree.ElementTree as ET

oev_zeit = None
OJP_URL = "https://api.opentransportdata.swiss/ojp20"
OJP_VORLAGE = """<?xml version="1.0" encoding="UTF-8"?>
<OJP xmlns="http://www.vdv.de/ojp" xmlns:siri="http://www.siri.org.uk/siri" version="2.0">
  <OJPRequest>
    <siri:ServiceRequest>
      <siri:RequestTimestamp>{jetzt}</siri:RequestTimestamp>
      <siri:RequestorRef>campingwetter_privat</siri:RequestorRef>
      <OJPTripRequest>
        <siri:RequestTimestamp>{jetzt}</siri:RequestTimestamp>
        <Origin>
          <PlaceRef>
            <GeoPosition><siri:Longitude>{lon0}</siri:Longitude><siri:Latitude>{lat0}</siri:Latitude></GeoPosition>
            <Name><Text>Start</Text></Name>
          </PlaceRef>
          <DepArrTime>{abfahrt}</DepArrTime>
        </Origin>
        <Destination>
          <PlaceRef>
            <GeoPosition><siri:Longitude>{lon1}</siri:Longitude><siri:Latitude>{lat1}</siri:Latitude></GeoPosition>
            <Name><Text>Ziel</Text></Name>
          </PlaceRef>
        </Destination>
        <Params>
          <NumberOfResults>3</NumberOfResults>
          <IncludeTrackSections>false</IncludeTrackSections>
          <IncludeLegProjection>false</IncludeLegProjection>
          <IncludeTurnDescription>false</IncludeTurnDescription>
          <IncludeIntermediateStops>false</IncludeIntermediateStops>
        </Params>
      </OJPTripRequest>
    </siri:ServiceRequest>
  </OJPRequest>
</OJP>"""


def iso_dauer_min(text):
    """PT1H23M oder P1DT2H in Minuten."""
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?)?", text.strip())
    if not m:
        return None
    d, h, mi, s = (float(x) if x else 0.0 for x in m.groups())
    return d * 1440 + h * 60 + mi + s / 60


def ojp_reisezeit(schluessel, start, ziel, abfahrt_utc):
    xml = OJP_VORLAGE.format(jetzt=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), abfahrt=abfahrt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                             lon0=start["lon"], lat0=start["lat"], lon1=ziel[2], lat1=ziel[1])
    req = urllib.request.Request(OJP_URL, data=xml.encode("utf-8"), headers={
        "Authorization": "Bearer " + schluessel, "Content-Type": "application/xml; charset=utf-8", "User-Agent": "campingwetter-notebook (privat)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        wurzel = ET.fromstring(r.read())
    # nur die Gesamtdauer der Reisen (Trip/Duration), nicht die der einzelnen Abschnitte
    reisen = [el for el in wurzel.iter() if el.tag.endswith("}Trip")]
    gesamt = []
    for trip in reisen:
        for kind in trip:
            if kind.tag.endswith("}Duration") and kind.text:
                gesamt.append(iso_dauer_min(kind.text))
    gesamt = [g for g in gesamt if g is not None]
    return min(gesamt) if gesamt else None


def naechste_abfahrt(text):
    tage_kurz = ["mo", "di", "mi", "do", "fr", "sa", "so"]
    m = re.match(r"^\s*(mo|di|mi|do|fr|sa|so)\w*\s+(\d{1,2}):(\d{2})\s*$", (text or "").strip().lower())
    wt, hh, mm = (tage_kurz.index(m[1]), int(m[2]), int(m[3])) if m else (5, 8, 0)
    jetzt_l = datetime.now(TZ)
    for k in range(8):
        d = (jetzt_l + timedelta(days=k)).date()
        t = datetime(d.year, d.month, d.day, hh, mm, tzinfo=TZ)
        if d.weekday() == wt and t > jetzt_l:
            return t
    return jetzt_l + timedelta(days=1)


otd = OTD_SCHLUESSEL
if not otd:
    try:
        from google.colab import userdata
        otd = userdata.get("OTD_SCHLUESSEL") or ""
    except Exception:
        otd = ""

oev_zeit = cache_lesen("oev", start, OEV_ABFAHRT) if OEV else None
if oev_zeit is not None:
    oev_info = dict(abfahrt=" ".join(OEV_ABFAHRT.split()[:2]))
    print(f"ÖV-Reisezeit aus dem Zwischenspeicher (jünger als {REISEZEIT_TAGE} Tage)")
elif OEV and not otd:
    print("Kein Schlüssel für opentransportdata.swiss: Das Dashboard zeigt keine ÖV-Reisezeit.")
elif OEV and not start:
    print("Ohne gültigen Startort gibt es keine ÖV-Reisezeit.")
elif OEV:
    try:
        abfahrt = naechste_abfahrt(OEV_ABFAHRT)
        # Ziele: Schweizer Orte, je rund 8 km der grösste, höchstens OEV_MAX_ZIELE (die grössten zuerst)
        beste = {}
        for o in orte:
            if len(o) > 4 and o[4] == "CH":
                k_ = (int((o[1] - LAT0) / 0.07), int((o[2] - LON0) / 0.1))
                if k_ not in beste or o[3] > beste[k_][3]:
                    beste[k_] = o
        ziele = sorted(beste.values(), key=lambda o: -o[3])[:OEV_MAX_ZIELE]
        print(f"ÖV-Reisezeit ab {start['name']}, Abfahrt {WT[abfahrt.weekday()]} {abfahrt:%d.%m. %H:%M}, zu {len(ziele)} Orten … (etwa {len(ziele) * 1.3 / 60:.0f} Minuten)")
        ok, fehler = [], 0
        for n, o in enumerate(ziele):
            for versuch in range(3):
                try:
                    d = ojp_reisezeit(otd, start, o, abfahrt.astimezone(UTC))
                    if d is not None:
                        ok.append((o, d))
                    break
                except urllib.error.HTTPError as e:
                    if e.code in (401, 403):
                        raise RuntimeError("opentransportdata.swiss lehnt den Schlüssel ab. Ist er für «OJP 2.0» freigeschaltet?")
                    if e.code == 429 and versuch < 2:
                        time.sleep(30)
                        continue
                    fehler += 1
                    break
                except Exception:
                    fehler += 1
                    break
            time.sleep(1.2)                                  # höflich bleiben: unter 50 Anfragen pro Minute
            if (n + 1) % 50 == 0:
                print(f"   {n + 1} von {len(ziele)} …")
        if not ok:
            raise RuntimeError(f"Keine einzige Verbindung gefunden ({fehler} Fehler).")
        plat = np.array([o[1] for o, _ in ok], dtype="float32")
        plon = np.array([o[2] for o, _ in ok], dtype="float32")
        pmin = np.array([d for _, d in ok], dtype="float32")
        clat, clon = LAT2D.ravel().astype("float32"), LON2D.ravel().astype("float32")
        kx = math.cos(math.radians(LAT0 + NY * DLAT / 2)) * 111.2
        oz = np.full(clat.size, np.nan, dtype="float32")
        for a in range(0, clat.size, 2000):
            dx = (clon[a:a + 2000, None] - plon[None, :]) * kx
            dy = (clat[a:a + 2000, None] - plat[None, :]) * 111.2
            d2 = dx * dx + dy * dy
            j = d2.argmin(1)
            dmin = np.sqrt(d2[np.arange(j.size), j])
            oz[a:a + 2000] = np.where(dmin <= 4, pmin[j] + dmin * 12, np.nan)
        oev_zeit = oz.reshape(NY, NX)
        oev_info = dict(abfahrt=f"{WT[abfahrt.weekday()]} {abfahrt:%H:%M}")
        cache_schreiben("oev", start, oev_zeit, OEV_ABFAHRT)
        print(f"ÖV-Reisezeit für {len(ok)} Orte gefunden ({fehler} ohne Antwort), auf die Karte übertragen")
    except Exception as e:
        print("ÖV-Reisezeit konnte nicht berechnet werden:", repr(e)[:300])

# ====================================================================================================
# Ensemble
# ====================================================================================================
# 🎲 Ensemble: Wie sicher ist die Eignung?
# Für jeden Ensemble-Lauf (Kontrolllauf + gestörte Läufe) wird die Eignung gerechnet. Daraus folgen pro Rasterzelle und Tag
# die «Chance auf Gut oder besser» und die «Chance trocken» (unter 0.5 mm Regen).
# Vereinfachungen: der Windchill kommt aus dem Kontrolllauf, «Warm ab» ist der Wert aus den Einstellungen. Nur ICON-Tage.


def eignung_np(regen, rel, tmax, tmin, warm, boeen=None):
    r = np.clip(1 - np.sqrt(np.maximum(regen - 0.2, 0) / 6), 0, 1)
    s = np.clip((rel - 0.1) / 0.7, 0, 1)
    t = np.clip((tmax - (warm - 12)) / 12, 0, 1)
    n = np.where(np.isfinite(tmin), np.clip(0.5 + 0.5 * (tmin - (NACHT_AB - 6)) / 6, 0.5, 1), 1)
    w = np.where(np.isfinite(boeen), np.clip(1 - (boeen - (BOEEN_BIS - 20)) / 40, 0, 1), 1) if boeen is not None else 1
    return r * w * n * (0.6 * s + 0.4 * t)


ens_info = {}
if ENSEMBLE:
    for key, L in laeufe.items():
        eigene = [t for t in tage if t["modell"] == key]
        if key == "ifs" or not eigene:
            continue
        try:
            R, cache = L["raster"], {}

            def felder(var, hs):
                fehlt = [h for h in hs if h > 0 and (var, h) not in cache]
                if fehlt:
                    cache.update({(var, h): a for h, a in lade(key, var, L["ref"], fehlt, perturbed=True)[3].items()})
                return [cache[(var, h)] if h > 0 else 0.0 for h in hs]

            celsius = lambda x: x - 273.15 if np.nanmean(x) > 150 else x
            waehle = lambda a, m: a if np.isscalar(a) else a[m]
            for t in eigene:
                print(f"Ensemble {MODELLE[key]['name']} {tag_text(t['datum'])} …")
                p0, p1 = felder("TOT_PREC", [t["l0"], t["l1"]])
                s0, s1 = felder("DURSUN", [t["l0"], t["l1"]])
                tx_ = felder("T_2M", t["hx"]) if t["hx"] else []
                tn_ = felder("T_2M", t["hn"]) if t["hn"] else []
                M = p1.shape[0]
                gut, trocken = np.zeros((NY, NX)), np.zeros((NY, NX))
                for m in range(-1, M):                      # −1 = Kontrolllauf (schon gerechnet), danach die gestörten Läufe
                    if m < 0:
                        regen, sonne, tmax, tmin = t["regen"], t["sonne"], t["tmax"], t["tmin"]
                    else:
                        regen = R(np.maximum(waehle(p1, m) - waehle(p0, m), 0))
                        sonne = np.minimum(R(np.maximum(waehle(s1, m) - waehle(s0, m), 0)) / 3600, t["moegl"])
                        tmax = R(np.maximum.reduce([celsius(a[m]) for a in tx_])) if tx_ else np.full((NY, NX), np.nan)
                        tmin = R(np.minimum.reduce([celsius(a[m]) for a in tn_])) if tn_ else np.full((NY, NX), np.nan)
                    rel = np.where(t["moegl"] > 0, sonne / np.maximum(t["moegl"], 0.01), 0)
                    # Windchill wie im Kontrolllauf (gefühlte Temperatur ist im Dashboard standardmässig an)
                    tmax_g = tmax + np.nan_to_num(t["gmax"] - t["tmax"]); tmin_g = tmin + np.nan_to_num(t["gmin"] - t["tmin"])
                    gut += eignung_np(regen, rel, tmax_g, tmin_g, WARM_AB) >= 0.6
                    trocken += regen < 0.5
                gueltig = np.isfinite(t["regen"])
                t["p_gut"] = np.where(gueltig, gut / (M + 1), np.nan).astype("float32")
                t["p_trocken"] = np.where(gueltig, trocken / (M + 1), np.nan).astype("float32")
                ens_info[MODELLE[key]["name"]] = M + 1
                print(f"   {M + 1} Läufe: Chance auf Gut im Mittel {np.nanmean(t['p_gut']) * 100:.0f} %, trocken {np.nanmean(t['p_trocken']) * 100:.0f} %")
                cache = {k_: v for k_, v in cache.items() if k_[0] != "T_2M" and k_[1] >= t["l1"]}
        except Exception as e:
            print(f"Ensemble von {MODELLE[key]['name']} nicht verfügbar:", repr(e)[:300])

# ====================================================================================================
# Dashboard schreiben
# ====================================================================================================
# 🏕️ Interaktives Dashboard erzeugen, anzeigen und als Datei herunterladen


def bytes_(a, fn):
    x = fn(np.asarray(a, dtype="float64"))
    return np.where(np.isfinite(x), np.clip(np.rint(x), 0, 254), 255).astype(np.uint8)


teile = []
for t in tage:  # Reihenfolge je Tag: Regen, Sonne, mögliche Sonne, Maximum, Minimum, Böen, gefühltes Maximum und Minimum, Chance gut, Chance trocken
    teile += [bytes_(t["regen"], lambda v: np.sqrt(v) * 25), bytes_(t["sonne"], lambda v: v * 10), bytes_(t["moegl"], lambda v: v * 10),
              bytes_(t["tmax"], lambda v: (v + 30) * 3), bytes_(t["tmin"], lambda v: (v + 30) * 3), bytes_(t["boeen"], lambda v: v / 0.75),
              bytes_(t["gmax"], lambda v: (v + 30) * 3), bytes_(t["gmin"], lambda v: (v + 30) * 3),
              bytes_(t.get("p_gut", np.full((NY, NX), np.nan)), lambda v: v * 200), bytes_(t.get("p_trocken", np.full((NY, NX), np.nan)), lambda v: v * 200)]
if globals().get("fahrzeit") is not None:  # Fahrzeit einmal am Schluss, in 2-Minuten-Schritten
    teile.append(bytes_(fahrzeit, lambda v: v / 2))
if globals().get("oev_zeit") is not None:  # danach die ÖV-Reisezeit
    teile.append(bytes_(oev_zeit, lambda v: v / 2))
daten_b64 = base64.b64encode(zlib.compress(np.concatenate([x.ravel() for x in teile]).tobytes(), 9)).decode("ascii")

meta = dict(
    grid=dict(lon0=LON0, lat0=LAT0, dLon=DLON, dLat=DLAT, nx=NX, ny=NY),
    days=[dict(date=t["datum"].isoformat(), model=MODELLE[t["modell"]]["name"], trend=bool(t["trend"]),
               **({"from": t["ws"].astimezone(TZ).hour} if t["teil"] else {"from": None})) for t in tage],
    runs={MODELLE[k]["name"]: f"{L['ref']:%d.%m. %H} UTC" for k, L in laeufe.items() if any(t["modell"] == k for t in tage)},
    warm=WARM_AB, boeen=BOEEN_BIS, nacht=NACHT_AB, nv=10, ens=globals().get("ens_info") or None, start=globals().get("start"), fz=globals().get("fahrzeit") is not None, fzMax=int(MAX_FAHRZEIT_H * 60), oev=globals().get("oev_zeit") is not None, oevAb=(globals().get("oev_info") or {}).get("abfahrt"),
    created=f"{jetzt:%d.%m.%Y %H:%M}",
)
seite = VORLAGE.replace("/*__META__*/null", json.dumps(meta, ensure_ascii=False))
seite = seite.replace("/*__GEO__*/null", json.dumps(geo, separators=(",", ":")).replace("</", "<\\/") if geo else "null")
seite = seite.replace('"__DATA__"', json.dumps(daten_b64))
seite = seite.replace("/*__ORTE__*/[]", json.dumps(orte, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
pathlib.Path("site").mkdir(exist_ok=True)
pathlib.Path("site/index.html").write_text(seite, encoding="utf-8")
print(f"site/index.html geschrieben ({len(seite) / 1e6:.1f} MB)")
