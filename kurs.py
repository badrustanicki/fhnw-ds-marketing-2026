"""Hilfsfunktionen für die Kurs-Notebooks «Data Science für Marketing» (Päckli & Co.).

Die Notebooks laden diese Datei im Setup. Ihr müsst hier nichts ändern.

    start(team, basis_url)          Daten laden (Tabellen orders, products, customers in DuckDB)
    start(team, basis_url, code=…)  dazu mit dem Kurs-Portal verbinden (Notebook 4): Einreichen ohne Google Drive
    connect_bigquery(projekt, daten)  bei Google anmelden, BigQuery mit eurem Projekt verbinden (Notebook 1)
    bq(sql)                         BigQuery-Abfrage ausführen, Ergebnis als Tabelle (DataFrame)
    save(df, name)             Ergebnis im Team-Ordner auf Google Drive ablegen
    catch_up(name)                  Ergebnis des letzten Blocks laden (eures oder das Musterergebnis)
    sanity_check(name, df)             automatische Plausibilitätsprüfung
    validate(df, round="pilot")     Einreichung prüfen, gratis und beliebig oft
    submit(df, round="pilot")       Einreichung abgeben, genau einmal pro Runde
    load_results(round="pilot")     Ergebnis einer Runde laden, sobald die Kursleitung es freigegeben hat
    submit_estimate(estimate, ci_low, ci_high)   Schätzung für Preis 2 abgeben
"""
import os, sys, glob, json, datetime as dt, urllib.error, urllib.request
import duckdb
import pandas as pd

__all__ = ["start", "save", "catch_up", "reference_model", "sanity_check", "validate", "submit", "load_results", "submit_estimate",
           "team_folder", "STICHTAG", "connect_bigquery", "bq"]
TEAM = 0
BASIS_URL = "https://raw.githubusercontent.com/badrustanicki/fhnw-ds-marketing-2026/main/"   # start() kann es überschreiben
_geladen = False   # interne Kopie der Kursdaten geladen?
CODE = ""          # Team-Code fürs Kurs-Portal. Leer: Einreichen und Ergebnisse laufen über Google Drive.
PORTAL_URL = ""
STICHTAG = "2025-08-31"
PILOT_MAX = 600
ARMS = ("control", "email", "email_discount")
TESTKONTO = "K00001"
NICHT_PRODUKTE = ("POST", "DOT", "BANK", "M", "S")
DATEN = ("orders", "products", "customers")
_team_dir = None


class KursFehler(Exception):
    """Fehler mit einer verständlichen Meldung, ohne langen Python-Fehlertext."""


def _blocked(e):
    """duckdb 2.x: Ein duckdb.execute(...), dessen Ergebnis nicht ganz abgeholt wurde (z. B. nur .fetchone()),
    blockiert alle weiteren Abfragen mit «Resource In Use»."""
    return (isinstance(e, getattr(duckdb, "ResourceInUseException", ())) or "Resource In Use" in str(e)
            or "unfinished result" in str(e))


def _short_message(shell, etype, value, tb, tb_offset=None):
    print(f"⚠️ {value}")


def _hint(e):
    """Kurzer Hinweis zu Fehlern, die Gemini-Code oft macht, sonst None."""
    s = str(e)
    if isinstance(e, AttributeError) and "fetchone" in s:
        return "Mit duckdb 2 gibt es .fetchone() nicht mehr. Nehmt .fetchall() oder .df(), z. B. duckdb.sql(\"...\").fetchall()[0]."
    if isinstance(e, getattr(duckdb, "CatalogException", ())) and "Table with name" in s and "does not exist" in s:
        return ("Tabelle nicht gefunden. Steht im Code duckdb.connect(...)? Dann überall duckdb.sql(...) nehmen: Eine neue "
                "Verbindung sieht die Kurs-Tabellen nicht. Sonst die Setup- und die Aufholen-Zelle nochmals ausführen.")
    if "bigquery.jobs.create" in s or ("Access Denied" in s and "Project" in s) or "Not found: Project" in s:
        return ("BigQuery: Die Projekt-ID stimmt nicht, oder BigQuery ist in eurem Projekt noch nicht eingerichtet. "
                "ID in der Cloud Console prüfen (oben links auf das Projekt klicken), eintragen und «BigQuery verbinden» "
                "nochmals ausführen.")
    if "Not found: Table" in s or "Not found: Dataset" in s:
        return ("BigQuery: Tabelle nicht gefunden. Den ganzen Namen in Backticks schreiben, wie in der Tabelle oben, "
                "z. B. `projekt.paeckli.orders`.")
    return None


def _with_duckdb_hint(showtraceback):
    """Fehleranzeige im Notebook: Ist DuckDB blockiert, wird das alte Ergebnis abgebrochen und ein kurzer Hinweis
    gezeigt. Alle anderen Fehler erscheinen wie gewohnt, bei häufigen Fehlern mit einem Hinweis darunter. (Auch Fehler
    beim Anzeigen eines duckdb.sql-Ergebnisses laufen hier durch, darum nicht über set_custom_exc.)"""
    def show(exc_tuple=None, *args, **kwargs):
        wert = (exc_tuple or sys.exc_info())[1]
        if wert is None or not _blocked(wert):
            r = showtraceback(exc_tuple, *args, **kwargs)
            if wert is not None and _hint(wert):
                print("💡", _hint(wert))
            return r
        try:
            duckdb.abort()      # gibt es erst ab duckdb 2.0
        except Exception:
            pass
        print("⚠️ DuckDB war blockiert: Ein früheres duckdb.execute(...) wurde nicht fertig abgeholt (z. B. nur "
              ".fetchone()). Ich habe es abgebrochen. Steht unten kein Ergebnis, führt die Zelle nochmals aus. "
              "Tipp: Abfragen immer mit duckdb.sql(\"...\").df() oder duckdb.sql(\"...\").fetchall().")
    show.kurs = True
    return show


def _number(x):
    """Zahl mit Schweizer Tausendertrennzeichen: 12'345."""
    return f"{x:,.0f}".replace(",", "'")


def _fetch(pfad):
    """Lädt eine Datei aus dem Kurs-Repository, falls sie noch nicht da ist."""
    if os.path.exists(pfad):
        return pfad
    if not BASIS_URL:
        raise KursFehler(f"Datei {pfad} fehlt, und BASIS_URL ist leer.")
    os.makedirs(os.path.dirname(pfad) or ".", exist_ok=True)
    urllib.request.urlretrieve(BASIS_URL + pfad, pfad)
    return pfad


def _install_messages():
    """Im Notebook: kurze Fehlermeldungen und Hinweise statt langer Python-Fehlertexte."""
    try:
        ip = get_ipython()  # noqa: F821 (nur im Notebook)
        ip.set_custom_exc((KursFehler,), _short_message)
        if not getattr(ip.showtraceback, "kurs", False):
            ip.showtraceback = _with_duckdb_hint(ip.showtraceback)
    except NameError:
        pass


def _local_data():
    """Interne Kopie der Kursdaten (orders, products, customers, orders_clean) für Prüfungen. Die Übungen fragen BigQuery ab."""
    global _geladen
    if _geladen:
        return
    for t in DATEN:
        duckdb.sql(f"CREATE OR REPLACE TABLE {t} AS SELECT * FROM read_parquet('{_fetch(f'daten/{t}.parquet')}')")
    duckdb.sql(f"CREATE OR REPLACE TABLE orders_clean AS SELECT * FROM read_parquet('{_fetch('aufholen/orders_clean.parquet')}')")
    _geladen = True


def start(team=0, basis_url="", code="", portal_url=""):
    """Setzt die Teamnummer (für Einreichungen in Notebook 4) und lädt die Daten für die Prüfungen.
    Mit code (Team-Code) laufen Einreichen und Ergebnisse über das Kurs-Portal statt über Google Drive."""
    global TEAM, BASIS_URL, _team_dir, CODE, PORTAL_URL
    TEAM, _team_dir = int(team or 0), None
    BASIS_URL = basis_url or BASIS_URL
    CODE = (code or "").strip().upper()
    PORTAL_URL = (os.environ.get("KURS_PORTAL") or portal_url or "").rstrip("/")   # KURS_PORTAL: nur für Tests
    _install_messages()
    _local_data()
    print("✅ Bereit.")
    print("   Team:", TEAM if TEAM else "noch nicht gesetzt (oben eintragen und das Setup nochmals ausführen)")
    if CODE:
        if not PORTAL_URL:
            raise KursFehler("Die Adresse des Kurs-Portals fehlt. CODE leer lassen: dann läuft alles über Google Drive.")
        name = _portal("GET", "/api/team")["team"]
        print(f"✅ Mit dem Kurs-Portal verbunden: {name}. Einreichen und Ergebnisse laufen über das Portal.")
        if TEAM and name != f"Team {TEAM}":
            print(f"⚠️ Der Code gehört zu «{name}», oben steht Team {TEAM}. Stimmt der Code?")


# ---------------- BigQuery ----------------
_bq = None          # BigQuery-Client, im Test "duckdb"
_bq_daten = ""      # Kurs-Dataset, z. B. "projekt.paeckli"


COURSE_ACCOUNT = "kurs-studierende@fhnw-marketing-2026.iam.gserviceaccount.com"   # nur Abfragen, nur das Dataset paeckli


def _key_from_primes(t):
    """Kurzer Kurs-Schlüssel (342 Zeichen) = die beiden Primzahlen p und q des RSA-Schlüssels; daraus der ganze Schlüssel."""
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    b = base64.urlsafe_b64decode(t + "==")
    p, q, e = int.from_bytes(b[:128], "big"), int.from_bytes(b[128:], "big"), 65537
    d = pow(e, -1, (p - 1) * (q - 1))
    key = rsa.RSAPrivateNumbers(p, q, d, rsa.rsa_crt_dmp1(d, p), rsa.rsa_crt_dmq1(d, q), rsa.rsa_crt_iqmp(p, q),
                                rsa.RSAPublicNumbers(e, p * q)).private_key()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    return {"type": "service_account", "project_id": COURSE_ACCOUNT.split("@")[1].split(".")[0],
            "client_email": COURSE_ACCOUNT, "private_key": pem, "token_uri": "https://oauth2.googleapis.com/token"}


def _course_key(text):
    """Kurs-Schlüssel (kurz: 342 Zeichen; lang: Base64 oder JSON eines Google-Dienstkontos) als dict, sonst None."""
    import re
    t = (text or "").strip()
    if not t:
        return None
    if re.fullmatch(r"[A-Za-z0-9_-]{342}", t):
        try:
            return _key_from_primes(t)
        except Exception:
            return None
    try:
        if not t.startswith("{"):
            import base64
            t = base64.b64decode(t + "=" * (-len(t) % 4)).decode()
        d = json.loads(t)
    except Exception:
        return None
    return d if isinstance(d, dict) and d.get("type") == "service_account" else None


def connect_bigquery(zugang, daten):
    """Verbindet BigQuery. zugang ist der Kurs-Schlüssel (von der Kursleitung, gilt nur am Kurstag) oder,
    ohne Schlüssel, die ID eines eigenen Google-Cloud-Projekts (Anmeldung mit dem eigenen Google-Konto).
    Notfall: zugang = "offline" lässt bq() über eine Kopie derselben Tabellen laufen (von GitHub, ohne Google)."""
    global _bq, _bq_daten
    zugang, _bq_daten = (zugang or "").strip(), daten
    _install_messages()
    schluessel = _course_key(zugang)
    offline = zugang.lower() == "offline"
    if offline or os.environ.get("KURS_TEST"):   # ohne Google: bq() läuft über die interne Kopie derselben Tabellen
        _local_data()
        _bq = "duckdb"
    elif schluessel:
        from google.cloud import bigquery
        from google.oauth2 import service_account
        pfad = "/tmp/kurs_schluessel.json"       # damit auch Code von Gemini und %%bigquery den Schlüssel finden
        with open(pfad, "w") as f:
            json.dump(schluessel, f)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = pfad
        os.environ["GOOGLE_CLOUD_PROJECT"] = schluessel["project_id"]
        creds = service_account.Credentials.from_service_account_info(
            schluessel, scopes=["https://www.googleapis.com/auth/bigquery"])
        _bq = bigquery.Client(project=schluessel["project_id"], credentials=creds)
    else:
        projekt = zugang
        if not projekt:
            raise KursFehler("Fügt zuerst den Kurs-Schlüssel ein (zeigt die Kursleitung) und führt die Zelle nochmals aus.")
        if " " in projekt or len(projekt) > 40:
            raise KursFehler("Das ist weder ein gültiger Kurs-Schlüssel noch eine Projekt-ID. Den Schlüssel ganz kopieren, "
                             "ohne Leerzeichen am Anfang oder Ende, und nochmals einfügen.")
        from google.colab import auth
        from google.cloud import bigquery
        os.environ["GOOGLE_CLOUD_PROJECT"] = projekt      # damit auch Code von Gemini das Projekt findet
        try:
            auth.authenticate_user()
            _bq = bigquery.Client(project=projekt)
        except Exception as e:
            if not ("credential propagation" in str(e) or "MessageError" in type(e).__name__):
                raise
            # Bekannter Colab-Fehler: Die Anmeldung kommt nicht im Notebook an. Zweiter Weg mit Link und Code.
            print("⚠️ Die Colab-Anmeldung hat nicht geklappt (bekannter Google-Fehler). Zweiter Weg:\n"
                  "   1. Den Link unten öffnen, mit demselben Google-Konto anmelden, alles erlauben.\n"
                  "   2. Den angezeigten Code kopieren und unten ins Feld einfügen, Enter.")
            try:
                import pydata_google_auth
            except ImportError:
                import subprocess
                subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pydata-google-auth"], check=True)
                import pydata_google_auth
            creds = pydata_google_auth.get_user_credentials(
                ["https://www.googleapis.com/auth/bigquery"], use_local_webserver=False)
            _bq = bigquery.Client(project=projekt, credentials=creds)
            print("✅ Angemeldet.")
    try:
        n = bq(f"SELECT COUNT(*) AS n FROM `{daten}.orders`")["n"][0]
    except Exception as e:
        if schluessel and ("invalid_grant" in str(e) or "Invalid JWT" in str(e) or "401" in str(e)):
            raise KursFehler("Der Kurs-Schlüssel gilt nicht mehr (er war nur für den Kurstag). Ohne Schlüssel: "
                             "eigenes Google-Cloud-Projekt anlegen und dessen Projekt-ID eintragen.") from None
        raise
    if offline:
        print(f"✅ Offline-Kopie der Kursdaten geladen: bq(\"...\") funktioniert wie gewohnt. `{daten}.orders` hat {_number(n)} Zeilen.")
        return
    print(f"✅ Mit BigQuery verbunden{' (Kurs-Schlüssel)' if schluessel else ''}. "
          f"Die Tabelle `{daten}.orders` hat {_number(n)} Zeilen.")


def bq(sql):
    """Führt eine BigQuery-Abfrage aus und gibt das Ergebnis als Tabelle (DataFrame) zurück."""
    if _bq is None:
        raise KursFehler("Noch nicht mit BigQuery verbunden: zuerst die Zelle «BigQuery verbinden» ausführen.")
    if _bq == "duckdb":
        import re
        return duckdb.sql(re.sub(r"`" + re.escape(_bq_daten) + r"\.(\w+)`", r"\1", sql)).df()
    return _bq.query(sql).to_dataframe()


def _bigquery_cell(line, cell):
    """%%bigquery [name]: die SQL-Abfrage der Zelle mit bq() ausführen, das Ergebnis zeigen und als name behalten.
    Ersetzt Googles %%bigquery, damit Kurs-Schlüssel, «offline» und die Fehlerhinweise auch hier gelten."""
    zeilen = [z for z in cell.splitlines() if not z.lstrip().startswith(("#@", "%%bigquery"))]   # Colab-Formular, doppelt Eingefügtes
    sql = "\n".join(zeilen)
    if not "".join(z.split("--")[0] for z in zeilen).strip():
        print("Noch keine Abfrage in dieser Zelle.")
        return None
    df = bq(sql)
    teile = line.split()
    if teile and not teile[0].startswith("-"):   # %%bigquery kunden_pro_land: Ergebnis als Variable behalten
        get_ipython().user_ns[teile[0]] = df
    return df


try:
    get_ipython().register_magic_function(_bigquery_cell, "cell", "bigquery")
except NameError:
    pass   # ohne IPython (Skript): nur bq()


# ---------------- Kurs-Portal ----------------
def _portal(method, path, payload=None):
    """Ruft das Kurs-Portal auf und gibt die Antwort zurück. Fehler kommen als kurze deutsche Meldung."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(PORTAL_URL + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", "X-Team-Key": CODE})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get("detail")
        except ValueError:
            detail = None
        if isinstance(detail, dict) and "problems" in detail:
            detail = " ".join(detail["problems"])
        raise KursFehler(detail or f"Das Kurs-Portal meldet Fehler {e.code}.") from None
    except urllib.error.URLError as e:
        raise KursFehler(f"Das Kurs-Portal ist nicht erreichbar ({e.reason}). Nochmals versuchen; "
                         "wenn es so bleibt, sagt es der Kursleitung.") from None


def _json_rows(d):
    return [{k: (None if v is None or (isinstance(v, float) and pd.isna(v)) else v) for k, v in r.items()}
            for r in d.to_dict("records")]


# ---------------- Google Drive: ein Ordner pro Team ----------------
def team_folder():
    """Pfad zum Team-Ordner. In Colab wird Google Drive beim ersten Aufruf verbunden."""
    global _team_dir
    if _team_dir:
        return _team_dir
    if not TEAM:
        raise KursFehler("Bitte oben im Setup eure Teamnummer eintragen und das Setup nochmals ausführen.")
    root = os.environ.get("KURS_DRIVE")  # nur für Tests ausserhalb von Colab
    if not root:
        from google.colab import drive
        try:
            drive.mount("/content/drive")
        except Exception as e:
            raise KursFehler(f"Google Drive ist nicht verbunden ({e}). Diese Zelle nochmals ausführen, im Dialog "
                             "euer Google-Konto wählen und alles zulassen.") from None
        root = "/content/drive/MyDrive"
    d = os.path.join(root, f"Paeckli Team {TEAM}")
    if not os.path.isdir(d):
        raise KursFehler(f"Ordner «Paeckli Team {TEAM}» nicht gefunden. Öffnet den Link zu eurem Team-Ordner, "
                         "klickt oben auf den Ordnernamen > «Organisieren» > «Verknüpfung hinzufügen» > «Meine Ablage», "
                         "dann diese Zelle nochmals ausführen.")
    _team_dir = d
    return d


def _reload():
    """Colab sieht neue Dateien anderer Personen manchmal erst nach einem erneuten Verbinden."""
    if not os.environ.get("KURS_DRIVE"):
        from google.colab import drive
        drive.mount("/content/drive", force_remount=True)


def _read(pfad):
    return pd.read_parquet(pfad) if pfad.endswith(".parquet") else pd.read_csv(pfad, dtype={"customer_id": str})


def save(df, name):
    """Speichert eine Tabelle (DataFrame oder Name einer DuckDB-Tabelle) im Team-Ordner,
    damit die nächsten Notebooks sie laden können."""
    df = _df(df)
    pfad = os.path.join(team_folder(), name + (".parquet" if name == "orders_clean" else ".csv"))
    if pfad.endswith(".parquet"):
        df.to_parquet(pfad, index=False)
    else:
        df.to_csv(pfad, index=False)
    print(f"✅ Gespeichert: {name} ({_number(len(df))} Zeilen) im Ordner «Paeckli Team {TEAM}».")


AUFHOLEN = {  # Name -> Spalten, die mindestens da sein müssen
    "orders_clean": ["invoice", "stock_code", "quantity", "invoice_date", "price", "customer_id"],
    "rfm": ["customer_id", "tage_seit_kauf", "frequenz", "umsatz"],
    "empfehlungen": ["customer_id", "rec_1", "rec_2", "rec_3"],
    "churn_scores": ["customer_id", "tage_seit_kauf", "frequenz", "umsatz", "churn_risiko"],
    "pilot_ergebnis": ["customer_id", "arm", "purchased", "revenue", "profit"],
}


def catch_up(name, eigene=True):
    """Lädt euer Ergebnis aus dem Team-Ordner. Fehlt es oder ist es unvollständig: das Musterergebnis.
    Die Tabelle steht danach auch in DuckDB unter ihrem Namen zur Verfügung."""
    datei = name + (".parquet" if name == "orders_clean" else ".csv")
    df, quelle = None, "Musterergebnis"
    if eigene and TEAM:
        try:
            pfad = os.path.join(team_folder(), datei)
            if os.path.exists(pfad):
                df, quelle = _read(pfad), "euer Ergebnis"
                fehlt = [c for c in AUFHOLEN[name] if c not in df.columns]
                if fehlt:
                    print(f"⚠️ In eurem {datei} fehlen die Spalten {fehlt}. Ich nehme das Musterergebnis.")
                    df, quelle = None, "Musterergebnis"
        except KursFehler as e:
            print(f"⚠️ {e}\n   Ich nehme das Musterergebnis.")
    if df is None:
        df = _read(_fetch(f"aufholen/{datei}"))
    if "invoice_date" in df.columns:
        df["invoice_date"] = pd.to_datetime(df["invoice_date"])
    duckdb.sql(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM df")
    print(f"✅ {name}: {_number(len(df))} Zeilen ({quelle}).")
    return df



def reference_model(train_df):
    """Aufholen in Übung 3: das Modell der Musterlösung (Schritt 3), ohne den Code im Notebook zu zeigen."""
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import train_test_split
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import FunctionTransformer, StandardScaler
    spalten = ["tage_seit_kauf", "frequenz", "umsatz"]
    train, test = train_test_split(train_df, test_size=0.3, random_state=42)
    modell = make_pipeline(FunctionTransformer(np.log1p), StandardScaler(), LogisticRegression())
    modell.fit(train[spalten], train["churn"])
    test = test.assign(risiko=modell.predict_proba(test[spalten])[:, 1])
    print("✅ Modell der Musterlösung: modell, train und test (mit risiko) sind bereit.")
    return modell, train, test

# ---------------- Plausibilitätsprüfungen ----------------
def _ok(bedingung, gut, schlecht):
    print(("✅ " + gut) if bedingung else ("⚠️ " + schlecht))
    return bool(bedingung)


def _notebook_var(name):
    try:
        return get_ipython().user_ns.get(name)  # noqa: F821 (nur im Notebook)
    except NameError:
        return vars(sys.modules["__main__"]).get(name)


def _df(x):
    if isinstance(x, pd.DataFrame):
        return x
    if isinstance(x, str):
        try:
            return duckdb.sql(f"SELECT * FROM {x}").df()
        except duckdb.CatalogException:     # keine DuckDB-Tabelle: vielleicht ein DataFrame im Notebook
            v = _notebook_var(x)
            if isinstance(v, pd.DataFrame):
                return v
            if hasattr(v, "df"):
                return v.df()
            raise KursFehler(f"Keine Tabelle «{x}» gefunden, weder in DuckDB noch als DataFrame. Heisst sie bei euch "
                             f"anders? Dann den DataFrame direkt übergeben, z. B. sanity_check(\"{x}\", eure_tabelle) "
                             f"oder save(eure_tabelle, \"{x}\").") from None
    return x.df()


def sanity_check(name, df=None):
    """Prüft ein Zwischenergebnis auf die häufigsten Fehler. Ohne df wird die DuckDB-Tabelle «name» geprüft."""
    d = _df(df if df is not None else name)
    fehlt = [c for c in AUFHOLEN.get(name, []) if c not in d.columns]
    if fehlt:
        return _ok(False, "", f"Es fehlen Spalten: {fehlt}. Erwartet: {AUFHOLEN[name]}")
    alles = []
    if name == "orders_clean":
        datum = pd.to_datetime(d["invoice_date"])
        monat = d.assign(m=datum.dt.to_period("M")).groupby("m")["price"].median()
        alles += [
            _ok(not d["invoice"].astype(str).str.startswith("C").any() and (d["quantity"] > 0).all(),
                "Keine Stornos und keine negativen Mengen.", "Es gibt noch Stornos (Rechnung beginnt mit C) oder negative Mengen."),
            _ok(d["customer_id"].notna().all(), "Jede Zeile hat eine Kundennummer.", "Es gibt noch Zeilen ohne customer_id."),
            _ok(not d.duplicated().any(), "Keine doppelten Zeilen.", "Es gibt noch exakt doppelte Zeilen."),
            _ok(not d["stock_code"].isin(NICHT_PRODUKTE).any(), "Nur echte Produkte.",
                "Es gibt noch Nicht-Produkte (Porto, Versand, Gebühren, Korrekturen, Muster)."),
            _ok((d["customer_id"] != TESTKONTO).all() and d["quantity"].max() < 10000,
                "Keine auffälligen Kunden oder Riesenbestellungen mehr.",
                "Es gibt noch einen auffälligen Kunden mit vielen Kleinstbestellungen oder eine Riesenbestellung."),
            _ok(datum.max().normalize() == pd.Timestamp(STICHTAG), "Die Daten reichen bis und mit 31. August 2025.",
                f"Es gibt noch Daten ab dem 1. September 2025 (bis {datum.max():%Y})."
                if datum.max() >= pd.Timestamp(STICHTAG) + pd.Timedelta(days=1) else
                f"Der letzte Tag ist der {datum.max():%d.%m.%Y}, nicht der 31. August 2025. invoice_date hat eine Uhrzeit: "
                "«< '2025-09-01'» statt «<= '2025-08-31'» nehmen."),
            _ok(monat.max() < 3 * monat.median() and monat.min() > monat.median() / 3, "Die Preise sind in allen Monaten ähnlich.",
                f"Im Monat {monat.idxmax()} sind die Preise auffällig hoch (Median {monat.max():.2f} CHF)."
                if monat.max() >= 3 * monat.median() else
                f"Im Monat {monat.idxmin()} sind die Preise auffällig tief (Median {monat.min():.2f} CHF). Zweimal durch 100 geteilt?"),
        ]
        print(f"   {_number(len(d))} Zeilen, {_number(d['customer_id'].nunique())} Kunden, "
              f"Umsatz {_number((d['quantity'] * d['price']).sum())} CHF.")
    elif name == "rfm":
        tage = d["tage_seit_kauf"]
        if tage.dtype.kind not in "iuf":     # keine Zahl, z. B. eine Zeitspanne «130 days» statt 130
            zahl = pd.to_numeric(tage, errors="coerce") if tage.dtype.kind == "O" else None
            if zahl is not None and zahl.notna().all():
                tage = zahl
            else:
                try:
                    tage = pd.to_timedelta(tage).dt.days
                except (ValueError, TypeError):
                    return _ok(False, "", "tage_seit_kauf muss eine Zahl sein: ganze Tage seit dem letzten Kauf.")
                alles.append(_ok(False, "", "tage_seit_kauf ist eine Zeitspanne (z. B. «130 days»), keine Zahl. Fragt "
                                 "Gemini: «Wandle tage_seit_kauf in ganze Tage (Zahl) um.» Die Prüfung unten rechnet mit ganzen Tagen."))
        alles += [
            _ok(d["customer_id"].is_unique, f"Eine Zeile pro Kunde ({_number(len(d))} Kunden).",
                "Manche Kunden kommen mehrfach vor. Gewünscht ist eine Zeile pro Kunde."),
            _ok(tage.between(0, 700).all(), "tage_seit_kauf liegt zwischen 0 und 700.",
                f"tage_seit_kauf hat unmögliche Werte (von {tage.min():.0f} bis {tage.max():.0f}). Gezählt wird bis zum "
                "31. August 2025, vom Datum ohne Uhrzeit (DATE(MAX(invoice_date)))."),
            _ok((d["frequenz"] >= 1).all() and (d["umsatz"] > 0).all(), "frequenz und umsatz sind positiv.",
                "frequenz oder umsatz ist bei manchen Kunden null oder negativ."),
            _ok(d["customer_id"].notna().all() and TESTKONTO not in set(d["customer_id"]),
                "Keine Zeile ohne Kunde, kein Testkonto.", "Es gibt eine Zeile ohne customer_id oder das Testkonto. Basiert rfm auf orders_clean?"),
        ]
        try:    # frequenz = Anzahl Rechnungen; mit COUNT(*) wären es Zeilen
            _local_data()
            rechnungen = duckdb.sql("SELECT COUNT(DISTINCT invoice) FROM orders_clean").fetchall()[0][0]
        except duckdb.Error:
            rechnungen = None
        if rechnungen:
            alles.append(_ok(d["frequenz"].sum() <= rechnungen, "frequenz zählt Rechnungen, nicht Zeilen.",
                             f"Zusammen haben die Kunden {_number(d['frequenz'].sum())} Bestellungen, orders_clean hat aber nur "
                             f"{_number(rechnungen)} Rechnungen. Zählt frequenz Zeilen? Nehmt COUNT(DISTINCT invoice)."))
        print(f"   Median: {tage.median():.0f} Tage seit dem letzten Kauf, {d['frequenz'].median():.0f} Bestellungen, "
              f"{_number(d['umsatz'].median())} CHF Umsatz.")
    elif name == "empfehlungen":
        recs = d[["rec_1", "rec_2", "rec_3"]]
        _local_data()
        codes = set(duckdb.sql("SELECT stock_code FROM products").df()["stock_code"])
        alles += [
            _ok(d["customer_id"].is_unique, f"Eine Zeile pro Kunde ({_number(len(d))} Kunden).",
                "Manche Kunden kommen mehrfach vor."),
            _ok(recs.notna().all().all(), "Jeder Kunde hat drei Empfehlungen.", "Manche Kunden haben weniger als drei Empfehlungen."),
            _ok(not recs.isin(NICHT_PRODUKTE).any().any(), "Keine Nicht-Produkte wie Porto.", "Es wird Porto o. ä. empfohlen."),
            _ok(recs.stack().dropna().isin(codes).all(), "Alle Codes gibt es in products.", "Manche Codes gibt es nicht in products."),
            _ok(d["rec_1"].nunique() > 5, f"Persönlich: {d['rec_1'].nunique()} verschiedene Top-Empfehlungen.",
                "Fast alle bekommen dieselbe Top-Empfehlung. Ist das «zusammen gekauft»?"),
        ]
        try:    # ein Produkt mit sich selbst gepaart: dann ist rec_1 fast immer etwas, das der Kunde schon hat
            gekauft = duckdb.sql("SELECT DISTINCT customer_id, stock_code AS rec_1 FROM orders_clean").df().astype(str)
            schon = len(d[["customer_id", "rec_1"]].astype(str).merge(gekauft)) / len(d) if len(d) else None
        except duckdb.Error:
            schon = None
        if schon is not None:
            alles.append(_ok(schon < 0.8, f"Kein Produkt mit sich selbst gepaart ({schon:.0%} der Top-Empfehlungen "
                             "haben die Kunden schon gekauft).",
                             f"{schon:.0%} der Top-Empfehlungen (rec_1) haben die Kunden schon gekauft. Wurde ein Produkt mit "
                             "sich selbst gepaart? Ein Paar besteht aus zwei verschiedenen Produkten (a.stock_code <> b.stock_code)."))
    elif name == "churn_scores":
        r = d["churn_risiko"]
        alles += [
            _ok(d["customer_id"].is_unique, f"Eine Zeile pro Kunde ({_number(len(d))} Kunden).",
                "Manche Kunden kommen mehrfach vor."),
            _ok(r.between(0, 1).all(), "churn_risiko liegt zwischen 0 und 1.", "churn_risiko muss eine Wahrscheinlichkeit sein (0 bis 1)."),
            _ok(r.nunique() > 10, "Die Risiken unterscheiden sich zwischen Kunden.", "Fast alle haben dasselbe Risiko."),
            _ok((r > 0.99).mean() < 0.3, "Nur wenige Kunden haben ein Risiko von fast 100 %.",
                f"{(r > 0.99).mean():.0%} der Kunden haben ein Risiko von fast 100 %. Wurde der Logarithmus beim Training von Hand "
                "gerechnet und hier vergessen? Er gehört als erster Schritt in die Pipeline (FunctionTransformer(np.log1p)). "
                "Oder ist es ein sehr tiefer Baum, der auswendig gelernt hat?"),
            _ok((pd.to_numeric(d["tage_seit_kauf"], errors="coerce") >= 0).all(), "tage_seit_kauf ist nie negativ.",
                "tage_seit_kauf ist manchmal negativ oder keine Zahl. Datum ohne Uhrzeit nehmen (DATE(MAX(invoice_date)))."),
            _ok(d[["tage_seit_kauf"]].assign(r=r).corr().iloc[0, 1] > 0,
                "Wer länger nicht gekauft hat, hat eher ein höheres Risiko.",
                "Seltsam: Wer länger nicht gekauft hat, hat ein tieferes Risiko. Richtung vertauscht?"),
        ]
        print(f"   Durchschnittliches Risiko: {r.mean():.0%}")
    else:
        raise KursFehler(f"Für «{name}» gibt es keine Prüfung.")
    return all(alles)


# ---------------- Kampagne: prüfen, einreichen, Ergebnisse ----------------
def _rows(df):
    if not isinstance(df, pd.DataFrame):
        df = df.df()
    d = df.copy()
    for k in ("rec_1", "rec_2", "rec_3"):
        if k not in d.columns:
            d[k] = None
    d = d[["customer_id", "arm", "rec_1", "rec_2", "rec_3"]].astype(object)
    d["customer_id"] = d["customer_id"].astype(str).str.strip()
    d["arm"] = d["arm"].astype(str).str.strip().str.lower()
    for k in ("rec_1", "rec_2", "rec_3"):
        d[k] = [None if v is None or pd.isna(v) or str(v).strip().lower() in ("", "nan", "none") else str(v).strip()
                for v in d[k]]
    return d


def _submitted(round):
    return sorted(glob.glob(os.path.join(team_folder(), f"{round}_einreichung_*.csv")))


def _check_submission(df, round):
    """Gleiche Regeln wie bei der Bewertung, nur ohne die versteckte Wahrheit. Gibt (Tabelle, Probleme, Hinweise)."""
    problems, notes = [], []
    if round not in ("pilot", "rollout"):
        return None, [f"Unbekannte Runde «{round}». Erlaubt: pilot, rollout."], notes
    if df is None or len(df) == 0:
        return None, ["Die Tabelle ist leer."], notes
    if not {"customer_id", "arm"} <= set(df.columns):
        return None, ["Die Tabelle braucht die Spalten customer_id, arm, rec_1, rec_2, rec_3."], notes
    d = _rows(df)
    _local_data()
    kunden = set(duckdb.sql("SELECT customer_id FROM customers").df()["customer_id"]) - {TESTKONTO}
    codes = set(duckdb.sql("SELECT stock_code FROM products").df()["stock_code"])
    unknown = sorted(set(d["customer_id"]) - kunden)
    if unknown:
        problems.append(f"{len(unknown)} unbekannte customer_id, z. B. {', '.join(unknown[:5])}.")
    dups = sorted(d.loc[d["customer_id"].duplicated(), "customer_id"].unique())
    if dups:
        problems.append(f"{len(dups)} customer_id kommen doppelt vor, z. B. {', '.join(dups[:5])}.")
    bad_arm = sorted(set(d["arm"]) - set(ARMS))
    if bad_arm:
        problems.append(f"Ungültige Werte in arm: {', '.join(bad_arm[:5])}. Erlaubt: {', '.join(ARMS)}.")
    no_rec = int((d["arm"].isin(["email", "email_discount"]) & d["rec_1"].isna()).sum())
    if no_rec:
        problems.append(f"{no_rec} E-Mail-Zeilen ohne Empfehlung in rec_1.")
    alle_recs = pd.concat([d[k] for k in ("rec_1", "rec_2", "rec_3")]).dropna()
    bad_codes = sorted(set(alle_recs) - codes)
    if bad_codes:
        problems.append(f"{len(bad_codes)} unbekannte Produktcodes, z. B. {', '.join(bad_codes[:5])}.")
    if round == "pilot" and len(d) > PILOT_MAX:
        problems.append(f"Der Pilot darf höchstens {PILOT_MAX} Kunden enthalten (ihr habt {len(d)}).")
    if round == "rollout" and TEAM and not CODE:      # mit Portal prüft das Portal
        pilot = _submitted("pilot")
        if pilot:
            used = set(pd.read_csv(pilot[0], dtype=str)["customer_id"])
            again = sorted(set(d["customer_id"]) & used)
            if again:
                problems.append(f"{len(again)} Kunden waren schon im Pilot, z. B. {', '.join(again[:5])}.")
    if not (d["arm"] == "control").any():
        notes.append("Keine Kontrollgruppe: Ihr werdet den Effekt nicht schätzen können.")
    nonprod = int(alle_recs.isin(NICHT_PRODUKTE).sum())
    if nonprod:
        notes.append(f"{nonprod} Empfehlungen sind keine Produkte (z. B. Porto). Ist das Absicht?")
    return d, problems, notes


def _check_all(df, round):
    """Lokal prüfen; mit Portal zusätzlich beim Portal (Pilot-Kunden im Rollout, Runde offen?)."""
    d, problems, notes = _check_submission(df, round)
    if not CODE or problems:
        return d, problems, notes
    r = _portal("POST", f"/api/validate/{round}", {"rows": _json_rows(d)})
    if r["ok"] and not r["round_open"]:
        r["notes"].append(f"Die Runde «{round}» ist noch nicht offen. Einreichen geht, sobald die Kursleitung sie öffnet.")
    return d, r["problems"], r["notes"]


def validate(df, round="pilot"):
    """Prüft eine Einreichung. Gratis und beliebig oft."""
    d, problems, notes = _check_all(df, round)
    for p in problems:
        print("❌", p)
    for n in notes:
        print("💬", n)
    if not problems:
        arms = d["arm"].value_counts().reindex(ARMS, fill_value=0)
        print(f"✅ Gültig für die Runde «{round}»: {len(d)} Kunden ("
              + ", ".join(f"{a}: {n}" for a, n in arms.items()) + ").")


def submit(df, round="pilot"):
    """Gibt die Einreichung ab: ans Kurs-Portal oder in den Team-Ordner. Pro Runde genau einmal."""
    d, problems, notes = _check_all(df, round)
    if problems:
        validate(df, round)
        raise KursFehler("Nicht eingereicht. Bitte zuerst die Probleme oben beheben.")
    if CODE:
        return _submit_portal(d, round, notes)
    if _submitted(round):
        alt = pd.read_csv(_submitted(round)[0], dtype=str)
        if set(zip(alt["customer_id"], alt["arm"])) == set(zip(d["customer_id"], d["arm"])):   # z. B. nach «Alle ausführen»
            print(f"✅ Diese Tabelle ist für die Runde «{round}» schon eingereicht. Alles gut, weiter mit load_results(\"{round}\").")
            return
        raise KursFehler(f"Euer Team hat die Runde «{round}» schon eingereicht. Pro Runde gibt es genau eine Einreichung.")
    if not os.environ.get("KURS_TEST"):
        antwort = input(f"{len(d)} Kunden für die Runde «{round}» einreichen? Das geht nur einmal. Tippt ja: ")
        if antwort.strip().lower() not in ("ja", "j", "yes", "y"):
            print("Nicht eingereicht.")
            return
    pfad = os.path.join(team_folder(), f"{round}_einreichung_{dt.datetime.now():%H%M%S}.csv")
    d.to_csv(pfad, index=False)
    for n in notes:
        print("💬", n)
    print(f"✅ Eingereicht: {len(d)} Kunden für die Runde «{round}». Die Kursleitung wertet aus, "
          f"danach lädt ihr das Ergebnis mit load_results(\"{round}\").")


def _submit_portal(d, round, notes):
    st = _portal("GET", "/api/team")
    if round in st["submissions"]:
        alt = _portal("GET", f"/api/team/results/{round}")["results"]
        if {(r["customer_id"], r["arm"]) for r in alt} == set(zip(d["customer_id"], d["arm"])):   # z. B. nach «Alle ausführen»
            print(f"✅ Diese Tabelle ist für die Runde «{round}» schon eingereicht. Alles gut, weiter mit load_results(\"{round}\").")
            return
        raise KursFehler(f"Euer Team hat die Runde «{round}» schon eingereicht. Pro Runde gibt es genau eine Einreichung.")
    if st["rounds"].get(round) != "open":
        raise KursFehler(f"Die Runde «{round}» ist noch nicht offen. Wartet auf die Ansage und führt die Zelle dann nochmals aus.")
    if not os.environ.get("KURS_TEST"):
        antwort = input(f"{len(d)} Kunden für die Runde «{round}» einreichen? Das geht nur einmal. Tippt ja: ")
        if antwort.strip().lower() not in ("ja", "j", "yes", "y"):
            print("Nicht eingereicht.")
            return
    _portal("POST", f"/api/submit/{round}", {"rows": _json_rows(d)})
    for n in notes:
        print("💬", n)
    print(f"✅ Eingereicht: {len(d)} Kunden für die Runde «{round}». Das Ergebnis ist schon da: load_results(\"{round}\").")


def load_results(round="pilot"):
    """Lädt das Ergebnis einer Runde: eine Zeile pro eingereichtem Kunden, mit dem realisierten Gewinn."""
    if CODE:
        try:
            df = pd.DataFrame(_portal("GET", f"/api/team/results/{round}")["results"])
        except KursFehler as e:
            raise KursFehler(f"Kein Ergebnis für «{round}»: {e}") from None
        df["customer_id"] = df["customer_id"].astype(str)
        print(f"✅ Ergebnis «{round}»: {len(df)} Kunden. Spalten: {', '.join(df.columns)}.")
        return df
    pfad = os.path.join(team_folder(), f"ergebnis_{round}.csv")
    if not os.path.exists(pfad):
        _reload()
    fehler = os.path.join(team_folder(), f"FEHLER_{round}.txt")
    if not os.path.exists(pfad) and os.path.exists(fehler):
        raise KursFehler(open(fehler, encoding="utf-8").read())
    if not os.path.exists(pfad):
        raise KursFehler(f"Das Ergebnis für «{round}» ist noch nicht da. Wartet auf die Kursleitung und versucht es dann nochmals.")
    df = pd.read_csv(pfad, dtype={"customer_id": str})
    print(f"✅ Ergebnis «{round}»: {len(df)} Kunden. Spalten: {', '.join(df.columns)}.")
    return df


def submit_estimate(estimate, ci_low, ci_high):
    """Gibt die Schätzung für Preis 2 ab: inkrementeller Gewinn des Rollouts in CHF, mit 95-%-Intervall.
    Bis zur Auflösung dürft ihr sie noch ändern (einfach nochmals abgeben)."""
    estimate, ci_low, ci_high = float(estimate), float(ci_low), float(ci_high)
    if not ci_low <= estimate <= ci_high:
        raise KursFehler("Die Schätzung muss zwischen ci_low und ci_high liegen.")
    if CODE:
        _portal("POST", "/api/estimate", {"estimate": estimate, "ci_low": ci_low, "ci_high": ci_high})
        print(f"✅ Schätzung abgegeben: {_number(estimate)} CHF, 95-%-Intervall {_number(ci_low)} bis {_number(ci_high)} CHF.")
        return
    if not _submitted("rollout"):
        raise KursFehler("Bitte zuerst den Rollout einreichen.")
    pd.DataFrame([{"estimate": estimate, "ci_low": ci_low, "ci_high": ci_high}]).to_csv(
        os.path.join(team_folder(), "schaetzung.csv"), index=False)
    print(f"✅ Schätzung abgegeben: {_number(estimate)} CHF, 95-%-Intervall {_number(ci_low)} bis {_number(ci_high)} CHF.")
