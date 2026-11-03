"""Hilfsfunktionen für die Kurs-Notebooks «Data Science für Marketing» (Päckli & Co.).

Die Notebooks laden diese Datei im Setup. Ihr müsst hier nichts ändern.

    start(team, basis_url)          Daten laden (Tabellen orders, products, customers in DuckDB)
    speichern(df, name)             Ergebnis im Team-Ordner auf Google Drive ablegen
    aufholen(name)                  Ergebnis des letzten Blocks laden (eures oder das Musterergebnis)
    plausibel(name, df)             automatische Plausibilitätsprüfung
    validate(df, round="pilot")     Einreichung prüfen, gratis und beliebig oft
    submit(df, round="pilot")       Einreichung abgeben, genau einmal pro Runde
    load_results(round="pilot")     Ergebnis einer Runde laden, sobald die Kursleitung es freigegeben hat
    submit_estimate(estimate, ci_low, ci_high)   Schätzung für Preis 2 abgeben
"""
import os, sys, glob, datetime as dt, urllib.request
import duckdb
import pandas as pd

__all__ = ["start", "speichern", "aufholen", "plausibel", "validate", "submit", "load_results", "submit_estimate",
           "team_ordner", "STICHTAG"]
TEAM = 0
BASIS_URL = ""
STICHTAG = "2025-08-31"
PILOT_MAX = 600
ARMS = ("control", "email", "email_discount")
TESTKONTO = "K00001"
NICHT_PRODUKTE = ("POST", "DOT", "BANK", "M", "S")
DATEN = ("orders", "products", "customers")
_team_dir = None


class KursFehler(Exception):
    """Fehler mit einer verständlichen Meldung, ohne langen Python-Fehlertext."""


def _kurze_meldung(shell, etype, value, tb, tb_offset=None):
    print(f"⚠️ {value}")


def _zahl(x):
    """Zahl mit Schweizer Tausendertrennzeichen: 12'345."""
    return f"{x:,.0f}".replace(",", "'")


def _hole(pfad):
    """Lädt eine Datei aus dem Kurs-Repository, falls sie noch nicht da ist."""
    if os.path.exists(pfad):
        return pfad
    if not BASIS_URL:
        raise KursFehler(f"Datei {pfad} fehlt, und BASIS_URL ist leer.")
    os.makedirs(os.path.dirname(pfad) or ".", exist_ok=True)
    urllib.request.urlretrieve(BASIS_URL + pfad, pfad)
    return pfad


def start(team=0, basis_url=""):
    """Lädt die Daten in DuckDB: danach funktioniert duckdb.sql("SELECT ... FROM orders")."""
    global TEAM, BASIS_URL, _team_dir
    TEAM, BASIS_URL, _team_dir = int(team or 0), basis_url, None
    try:
        get_ipython().set_custom_exc((KursFehler,), _kurze_meldung)  # noqa: F821 (nur im Notebook)
    except NameError:
        pass
    for t in DATEN:
        duckdb.sql(f"CREATE OR REPLACE TABLE {t} AS SELECT * FROM read_parquet('{_hole(f'daten/{t}.parquet')}')")
    n = duckdb.sql("SELECT COUNT(*) FROM orders").fetchall()[0][0]
    print(f"✅ Daten geladen: Tabellen orders ({_zahl(n)} Zeilen), products, customers.")
    print("   Team:", TEAM if TEAM else "noch nicht gesetzt (braucht ihr erst zum Speichern)")


# ---------------- Google Drive: ein Ordner pro Team ----------------
def team_ordner():
    """Pfad zum Team-Ordner. In Colab wird Google Drive beim ersten Aufruf verbunden."""
    global _team_dir
    if _team_dir:
        return _team_dir
    if not TEAM:
        raise KursFehler("Bitte oben im Setup eure Teamnummer eintragen und das Setup nochmals ausführen.")
    root = os.environ.get("KURS_DRIVE")  # nur für Tests ausserhalb von Colab
    if not root:
        from google.colab import drive
        drive.mount("/content/drive")
        root = "/content/drive/MyDrive"
    d = os.path.join(root, f"Paeckli Team {TEAM}")
    if not os.path.isdir(d):
        raise KursFehler(f"Ordner «Paeckli Team {TEAM}» nicht gefunden. Öffnet den Link zu eurem Team-Ordner, "
                         "klickt auf den Ordnernamen > «Verknüpfung zu Drive hinzufügen» > «Meine Ablage», "
                         "dann diese Zelle nochmals ausführen.")
    _team_dir = d
    return d


def _neu_einlesen():
    """Colab sieht neue Dateien anderer Personen manchmal erst nach einem erneuten Verbinden."""
    if not os.environ.get("KURS_DRIVE"):
        from google.colab import drive
        drive.mount("/content/drive", force_remount=True)


def _lesen(pfad):
    return pd.read_parquet(pfad) if pfad.endswith(".parquet") else pd.read_csv(pfad, dtype={"customer_id": str})


def speichern(df, name):
    """Speichert eine Tabelle (DataFrame oder Name einer DuckDB-Tabelle) im Team-Ordner,
    damit die nächsten Notebooks sie laden können."""
    df = _df(df)
    pfad = os.path.join(team_ordner(), name + (".parquet" if name == "orders_clean" else ".csv"))
    if pfad.endswith(".parquet"):
        df.to_parquet(pfad, index=False)
    else:
        df.to_csv(pfad, index=False)
    print(f"✅ Gespeichert: {name} ({_zahl(len(df))} Zeilen) im Ordner «Paeckli Team {TEAM}».")


AUFHOLEN = {  # Name -> Spalten, die mindestens da sein müssen
    "orders_clean": ["invoice", "stock_code", "quantity", "invoice_date", "price", "customer_id"],
    "rfm": ["customer_id", "tage_seit_kauf", "frequenz", "umsatz"],
    "empfehlungen": ["customer_id", "rec_1", "rec_2", "rec_3"],
    "churn_scores": ["customer_id", "tage_seit_kauf", "frequenz", "umsatz", "churn_risiko"],
}


def aufholen(name, eigene=True):
    """Lädt euer Ergebnis aus dem Team-Ordner. Fehlt es oder ist es unvollständig: das Musterergebnis.
    Die Tabelle steht danach auch in DuckDB unter ihrem Namen zur Verfügung."""
    datei = name + (".parquet" if name == "orders_clean" else ".csv")
    df, quelle = None, "Musterergebnis"
    if eigene and TEAM:
        try:
            pfad = os.path.join(team_ordner(), datei)
            if os.path.exists(pfad):
                df, quelle = _lesen(pfad), "euer Ergebnis"
                fehlt = [c for c in AUFHOLEN[name] if c not in df.columns]
                if fehlt:
                    print(f"⚠️ In eurem {datei} fehlen die Spalten {fehlt}. Ich nehme das Musterergebnis.")
                    df, quelle = None, "Musterergebnis"
        except KursFehler as e:
            print(f"⚠️ {e}\n   Ich nehme das Musterergebnis.")
    if df is None:
        df = _lesen(_hole(f"aufholen/{datei}"))
    if "invoice_date" in df.columns:
        df["invoice_date"] = pd.to_datetime(df["invoice_date"])
    duckdb.sql(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM df")
    print(f"✅ {name}: {_zahl(len(df))} Zeilen ({quelle}).")
    return df


# ---------------- Plausibilitätsprüfungen ----------------
def _ok(bedingung, gut, schlecht):
    print(("✅ " + gut) if bedingung else ("⚠️ " + schlecht))
    return bool(bedingung)


def _df(x):
    if isinstance(x, pd.DataFrame):
        return x
    if isinstance(x, str):
        return duckdb.sql(f"SELECT * FROM {x}").df()
    return x.df()


def plausibel(name, df=None):
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
            _ok(datum.max() <= pd.Timestamp(STICHTAG + " 23:59:59"), "Kein Datum nach dem 31. August 2025.",
                f"Es gibt noch Daten nach dem 31. August 2025 (bis {datum.max():%Y})."),
            _ok(monat.max() < 3 * monat.median(), "Die Preise sind in allen Monaten ähnlich.",
                f"Im Monat {monat.idxmax()} sind die Preise auffällig hoch (Median {monat.max():.2f} CHF)."),
        ]
        print(f"   {_zahl(len(d))} Zeilen, {_zahl(d['customer_id'].nunique())} Kunden, "
              f"Umsatz {_zahl((d['quantity'] * d['price']).sum())} CHF.")
    elif name == "rfm":
        alles += [
            _ok(d["customer_id"].is_unique, f"Eine Zeile pro Kunde ({_zahl(len(d))} Kunden).",
                "Manche Kunden kommen mehrfach vor. Gewünscht ist eine Zeile pro Kunde."),
            _ok(d["tage_seit_kauf"].between(0, 700).all(), "tage_seit_kauf liegt zwischen 0 und 700.",
                "tage_seit_kauf hat unmögliche Werte. Gezählt wird bis zum 31. August 2025."),
            _ok((d["frequenz"] >= 1).all() and (d["umsatz"] > 0).all(), "frequenz und umsatz sind positiv.",
                "frequenz oder umsatz ist bei manchen Kunden null oder negativ."),
            _ok(d["customer_id"].notna().all() and TESTKONTO not in set(d["customer_id"]),
                "Keine Zeile ohne Kunde, kein Testkonto.", "Es gibt eine Zeile ohne customer_id oder das Testkonto. Basiert rfm auf orders_clean?"),
        ]
        print(f"   Median: {d['tage_seit_kauf'].median():.0f} Tage seit dem letzten Kauf, {d['frequenz'].median():.0f} Bestellungen, "
              f"{_zahl(d['umsatz'].median())} CHF Umsatz.")
    elif name == "empfehlungen":
        recs = d[["rec_1", "rec_2", "rec_3"]]
        codes = set(duckdb.sql("SELECT stock_code FROM products").df()["stock_code"])
        alles += [
            _ok(d["customer_id"].is_unique, f"Eine Zeile pro Kunde ({_zahl(len(d))} Kunden).",
                "Manche Kunden kommen mehrfach vor."),
            _ok(recs.notna().all().all(), "Jeder Kunde hat drei Empfehlungen.", "Manche Kunden haben weniger als drei Empfehlungen."),
            _ok(not recs.isin(NICHT_PRODUKTE).any().any(), "Keine Nicht-Produkte wie Porto.", "Es wird Porto o. ä. empfohlen."),
            _ok(recs.stack().isin(codes).all(), "Alle Codes gibt es in products.", "Manche Codes gibt es nicht in products."),
            _ok(d["rec_1"].nunique() > 5, f"Persönlich: {d['rec_1'].nunique()} verschiedene Top-Empfehlungen.",
                "Fast alle bekommen dieselbe Top-Empfehlung. Ist das «zusammen gekauft»?"),
        ]
    elif name == "churn_scores":
        r = d["churn_risiko"]
        alles += [
            _ok(d["customer_id"].is_unique, f"Eine Zeile pro Kunde ({_zahl(len(d))} Kunden).",
                "Manche Kunden kommen mehrfach vor."),
            _ok(r.between(0, 1).all(), "churn_risiko liegt zwischen 0 und 1.", "churn_risiko muss eine Wahrscheinlichkeit sein (0 bis 1)."),
            _ok(r.nunique() > 10, "Die Risiken unterscheiden sich zwischen Kunden.", "Fast alle haben dasselbe Risiko."),
            _ok(d[["tage_seit_kauf"]].assign(r=r).corr().iloc[0, 1] > 0,
                "Wer länger nicht gekauft hat, hat eher ein höheres Risiko.",
                "Seltsam: Wer länger nicht gekauft hat, hat ein tieferes Risiko. Richtung vertauscht?"),
        ]
        print(f"   Durchschnittliches Risiko: {r.mean():.0%}")
    else:
        raise KursFehler(f"Für «{name}» gibt es keine Prüfung.")
    return all(alles)


# ---------------- Kampagne: prüfen, einreichen, Ergebnisse ----------------
def _zeilen(df):
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


def _eingereicht(round):
    return sorted(glob.glob(os.path.join(team_ordner(), f"{round}_einreichung_*.csv")))


def _pruefen(df, round):
    """Gleiche Regeln wie bei der Bewertung, nur ohne die versteckte Wahrheit. Gibt (Tabelle, Probleme, Hinweise)."""
    problems, notes = [], []
    if round not in ("pilot", "rollout"):
        return None, [f"Unbekannte Runde «{round}». Erlaubt: pilot, rollout."], notes
    if df is None or len(df) == 0:
        return None, ["Die Tabelle ist leer."], notes
    if not {"customer_id", "arm"} <= set(df.columns):
        return None, ["Die Tabelle braucht die Spalten customer_id, arm, rec_1, rec_2, rec_3."], notes
    d = _zeilen(df)
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
    if round == "rollout" and TEAM:
        pilot = _eingereicht("pilot")
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


def validate(df, round="pilot"):
    """Prüft eine Einreichung. Gratis und beliebig oft."""
    d, problems, notes = _pruefen(df, round)
    for p in problems:
        print("❌", p)
    for n in notes:
        print("💬", n)
    if not problems:
        arms = d["arm"].value_counts().reindex(ARMS, fill_value=0)
        print(f"✅ Gültig für die Runde «{round}»: {len(d)} Kunden ("
              + ", ".join(f"{a}: {n}" for a, n in arms.items()) + ").")


def submit(df, round="pilot"):
    """Gibt die Einreichung ab: speichert sie im Team-Ordner. Pro Runde genau einmal."""
    d, problems, notes = _pruefen(df, round)
    if problems:
        validate(df, round)
        raise KursFehler("Nicht eingereicht. Bitte zuerst die Probleme oben beheben.")
    if _eingereicht(round):
        raise KursFehler(f"Euer Team hat die Runde «{round}» schon eingereicht. Pro Runde gibt es genau eine Einreichung.")
    if not os.environ.get("KURS_TEST"):
        antwort = input(f"{len(d)} Kunden für die Runde «{round}» einreichen? Das geht nur einmal. Tippt ja: ")
        if antwort.strip().lower() not in ("ja", "j", "yes", "y"):
            print("Nicht eingereicht.")
            return
    pfad = os.path.join(team_ordner(), f"{round}_einreichung_{dt.datetime.now():%H%M%S}.csv")
    d.to_csv(pfad, index=False)
    for n in notes:
        print("💬", n)
    print(f"✅ Eingereicht: {len(d)} Kunden für die Runde «{round}». Die Kursleitung wertet aus, "
          f"danach lädt ihr das Ergebnis mit load_results(\"{round}\").")


def load_results(round="pilot"):
    """Lädt das Ergebnis einer Runde: eine Zeile pro eingereichtem Kunden, mit dem realisierten Gewinn."""
    pfad = os.path.join(team_ordner(), f"ergebnis_{round}.csv")
    if not os.path.exists(pfad):
        _neu_einlesen()
    fehler = os.path.join(team_ordner(), f"FEHLER_{round}.txt")
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
    if not _eingereicht("rollout"):
        raise KursFehler("Bitte zuerst den Rollout einreichen.")
    pd.DataFrame([{"estimate": estimate, "ci_low": ci_low, "ci_high": ci_high}]).to_csv(
        os.path.join(team_ordner(), "schaetzung.csv"), index=False)
    print(f"✅ Schätzung abgegeben: {_zahl(estimate)} CHF, 95-%-Intervall {_zahl(ci_low)} bis {_zahl(ci_high)} CHF.")
