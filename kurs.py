"""Hilfsfunktionen für die Kurs-Notebooks «Data Science für Marketing» (Päckli & Co.).

Die Notebooks laden diese Datei im Setup. Ihr müsst hier nichts ändern.

    start(team, basis_url)          Teamnummer merken (für die Abgabe in den Team-Ordner)
    connect_bigquery(zugang, daten) BigQuery verbinden: Kurs-Schlüssel, eigene Projekt-ID oder "offline"
    bq(sql)                         BigQuery-Abfrage ausführen, Ergebnis als Tabelle (DataFrame)
    save(df, name)             Ergebnis im Team-Ordner auf Google Drive ablegen
    catch_up(name)                  Ergebnis des letzten Blocks laden (eures oder das Musterergebnis)
    sanity_check(name, df)             automatische Plausibilitätsprüfung
"""
import os, sys, json, urllib.request
import duckdb
import pandas as pd

__all__ = ["start", "save", "catch_up", "reference_model", "sanity_check",
           "team_folder", "STICHTAG", "connect_bigquery", "bq"]
TEAM = 0
BASIS_URL = "https://raw.githubusercontent.com/badrustanicki/fhnw-ds-marketing-2026/main/"   # start() kann es überschreiben
_geladen = False   # interne Kopie der Kursdaten geladen?
STICHTAG = "2025-08-31"
TESTKONTO = "K00001"
NICHT_PRODUKTE = ("POST", "DOT", "BANK", "M", "S")
DATEN = ("orders", "products", "customers")
_team_dir = None


class KursFehler(Exception):
    """Fehler aus kurs.py mit einer Meldung auf Deutsch. Wird wie jeder Python-Fehler ganz angezeigt."""


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


def _local_data():
    """Interne Kopie der Kursdaten (orders, products, customers, orders_clean) für Prüfungen. Die Übungen fragen BigQuery ab."""
    global _geladen
    if _geladen:
        return
    for t in DATEN:
        duckdb.sql(f"CREATE OR REPLACE TABLE {t} AS SELECT * FROM read_parquet('{_fetch(f'daten/{t}.parquet')}')")
    duckdb.sql(f"CREATE OR REPLACE TABLE orders_clean AS SELECT * FROM read_parquet('{_fetch('aufholen/orders_clean.parquet')}')")
    _geladen = True


def start(team=0, basis_url=""):
    """Merkt sich die Teamnummer (für die Abgabe in den Team-Ordner) und die Adresse des Kursmaterials."""
    global TEAM, BASIS_URL, _team_dir
    TEAM, _team_dir = int(team or 0), None
    BASIS_URL = basis_url or BASIS_URL
    print("✅ Bereit.")
    print("   Team:", TEAM if TEAM else "noch nicht gesetzt (oben eintragen und das Setup nochmals ausführen)")


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
            print(f"⚠️ Die Colab-Anmeldung hat nicht geklappt (bekannter Google-Fehler: {type(e).__name__}: {e}). Zweiter Weg:\n"
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
                             "eigenes Google-Cloud-Projekt anlegen und dessen Projekt-ID eintragen.")
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
        projekt, dataset = _bq_daten.rsplit(".", 1)   # auch `paeckli.orders` ohne Projekt und ohne Backticks, wie auf den Folien
        muster = r"`?(?:`?" + re.escape(projekt) + r"`?\.)?`?" + re.escape(dataset) + r"`?\.`?(\w+)`?"
        return duckdb.sql(re.sub(muster, r"\1", sql)).df()
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
                             "euer Google-Konto wählen und alles zulassen.")
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
                             f"oder save(eure_tabelle, \"{x}\").")
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
