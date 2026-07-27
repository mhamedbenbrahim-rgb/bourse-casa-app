# -*- coding: utf-8 -*-
"""
Collecte des données émetteurs publiées par l'AMMC
(https://www.ammc.ma/fr/espace-emetteurs).

Le site AMMC (Drupal) n'expose pas d'API JSON : les listes sont des
tableaux HTML paginés (`?page=N`) et chaque document financier est un
PDF (souvent un rapport annuel complet de plusieurs dizaines de pages,
mise en page libre par émetteur). Le script travaille donc en 3 étapes
indépendantes et reprises (idempotentes), pilotées par --step :

  emetteurs   Liste des émetteurs (/fr/espace-emetteurs/liste-des-emetteurs)
              + fiche « Caractéristiques » de chaque émetteur
              -> tables `emetteurs`, `emetteurs_details`.

  documents   Catalogue de TOUS les documents financiers publiés
              (/fr/liste-etats-financiers-emetteurs, ~426 pages) :
              émetteur, année, type de rapport, lien vers la page du
              document -> table `documents` (statut 'a_traiter').

  extraire    Pour les documents non encore traités : résout le lien
              PDF réel, le télécharge (reprise en cas de coupure —
              fréquente sur les gros rapports), puis tente d'en extraire
              les rubriques Bilan/CPC/ESG par repérage des intitulés du
              plan comptable marocain (CGNC) et extraction de tableaux.
              -> table `etats_financiers_ammc` (best-effort).
              Les documents où aucune table exploitable n'a été trouvée
              sont journalisés dans `documents_non_extraits` plutôt que
              de produire des chiffres non fiables.

Le catalogue complet représente plusieurs milliers de documents PDF
(plusieurs dizaines de Go) : ce script est prévu pour être relancé
plusieurs fois avec --max-new, pas pour tout aspirer en un seul run.

  pip install requests beautifulsoup4 lxml pdfplumber
  python ammc_scraper.py --db ammc.db --step emetteurs
  python ammc_scraper.py --db ammc.db --step documents
  python ammc_scraper.py --db ammc.db --step extraire --max-new 20
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.ammc.ma"
LIST_EMETTEURS_URL = f"{BASE_URL}/fr/espace-emetteurs/liste-des-emetteurs"
LIST_ETATS_URL = f"{BASE_URL}/fr/liste-etats-financiers-emetteurs"
DB_PATH = "ammc.db"
PDF_DIR = Path("ammc_pdfs")
USER_AGENT = (
    "Mozilla/5.0 (compatible; bourse-casa-app/1.0; "
    "+https://github.com/mhamedbenbrahim-rgb/bourse-casa-app)"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS emetteurs (
    id_ammc                  INTEGER PRIMARY KEY,
    nom                      TEXT NOT NULL,
    secteur_activite         TEXT,
    date_introduction_bourse TEXT,
    url_fiche                TEXT,
    maj_le                   TEXT
);

CREATE TABLE IF NOT EXISTS emetteurs_details (
    id_ammc  INTEGER NOT NULL REFERENCES emetteurs(id_ammc),
    cle      TEXT NOT NULL,
    valeur   TEXT,
    PRIMARY KEY (id_ammc, cle)
);

CREATE TABLE IF NOT EXISTS documents (
    url_page      TEXT PRIMARY KEY,
    emetteur_slug TEXT NOT NULL,
    emetteur_nom  TEXT,
    annee         INTEGER,
    type_rapport  TEXT,
    url_pdf       TEXT,
    statut        TEXT NOT NULL DEFAULT 'a_traiter',
    maj_le        TEXT
);

CREATE TABLE IF NOT EXISTS etats_financiers_ammc (
    emetteur_slug TEXT NOT NULL,
    annee         INTEGER NOT NULL,
    etat          TEXT NOT NULL,
    rubrique      TEXT NOT NULL,
    valeur        TEXT,
    valeur_num    REAL,
    page_pdf      INTEGER,
    url_pdf       TEXT,
    PRIMARY KEY (emetteur_slug, annee, etat, rubrique)
);

CREATE TABLE IF NOT EXISTS documents_non_extraits (
    url_page  TEXT PRIMARY KEY,
    url_pdf   TEXT,
    raison    TEXT,
    traite_le TEXT
);
"""

# Repérage des tableaux financiers dans le texte du PDF : intitulés du
# plan comptable général marocain (CGNC), en majuscules. Une page n'est
# retenue pour un état donné que si au moins 2 intitulés y apparaissent
# (réduit les faux positifs sur les pages de commentaires).
ETAT_KEYWORDS = {
    "Bilan Actif": [
        "ACTIF IMMOBILISE", "IMMOBILISATIONS EN NON VALEURS",
        "IMMOBILISATIONS INCORPORELLES", "ACTIF CIRCULANT",
        "TRESORERIE-ACTIF", "TOTAL ACTIF",
    ],
    "Bilan Passif": [
        "FINANCEMENT PERMANENT", "CAPITAUX PROPRES", "DETTES DE FINANCEMENT",
        "PASSIF CIRCULANT", "TRESORERIE-PASSIF", "TOTAL PASSIF",
    ],
    "CPC": [
        "COMPTE DE PRODUITS ET CHARGES", "CHIFFRE D'AFFAIRES",
        "RESULTAT D'EXPLOITATION", "RESULTAT FINANCIER",
        "RESULTAT NET DE L'EXERCICE",
    ],
    "ESG": [
        "ETAT DES SOLDES DE GESTION", "VALEUR AJOUTEE",
        "EXCEDENT BRUT D'EXPLOITATION", "CAPACITE D'AUTOFINANCEMENT",
    ],
}
MIN_KEYWORD_HITS = 2
MIN_RUBRIQUES_OK = 6  # sous ce seuil, le document est jugé non exploité

NUM_RE = re.compile(r"^\(?-?[\d\s .,]+\)?$")


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf"})
    return s


def get_html(session: requests.Session, url: str, params: dict | None = None,
             retries: int = 4) -> str | None:
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, params=params, timeout=30)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            last_err = exc
            time.sleep(min(2 ** attempt, 15))
    print(f"[!] Échec HTTP sur {url} : {last_err}", file=sys.stderr)
    return None


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)


# ----------------------------------------------------------------------------
# Étape 1 : annuaire des émetteurs
# ----------------------------------------------------------------------------
def parse_emetteurs_page(html: str) -> tuple[list[dict], bool]:
    """Renvoie (lignes, a_page_suivante)."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="cols-4")
    if table is None or table.find("tbody") is None:
        return [], False

    rows = []
    for tr in table.find("tbody").find_all("tr"):
        link = tr.find("a", href=re.compile(r"/liste-des-emetteurs/\d+$"))
        if link is None:
            continue
        m = re.search(r"/liste-des-emetteurs/(\d+)$", link["href"])
        id_ammc = int(m.group(1))
        cells = tr.find_all("td")
        secteur = cells[2].get_text(" ", strip=True) if len(cells) > 2 else None
        date_intro = None
        if len(cells) > 3:
            time_tag = cells[3].find("time")
            date_intro = time_tag.get_text(strip=True) if time_tag else None
        rows.append({
            "id_ammc": id_ammc,
            "nom": link.get_text(strip=True),
            "secteur_activite": secteur or None,
            "date_introduction_bourse": date_intro,
            "url_fiche": urljoin(BASE_URL, link["href"]),
        })

    has_next = soup.find("li", class_="pager__item--next") is not None
    return rows, has_next


def parse_emetteur_details(html: str) -> dict:
    """Table clé/valeur « Caractéristiques » (générique : tout th[scope=row])."""
    soup = BeautifulSoup(html, "lxml")
    details = {}
    for th in soup.find_all("th", attrs={"scope": "row"}):
        td = th.find_next_sibling("td")
        if td is None:
            continue
        label = " ".join(th.get_text(" ", strip=True).split())
        if not label:
            continue
        value = " | ".join(s.strip() for s in td.stripped_strings if s.strip())
        if value:
            details[label] = value
    return details


def upsert_emetteur(con: sqlite3.Connection, row: dict) -> None:
    con.execute(
        "INSERT INTO emetteurs (id_ammc, nom, secteur_activite, "
        "date_introduction_bourse, url_fiche, maj_le) VALUES (?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(id_ammc) DO UPDATE SET nom=excluded.nom, "
        "secteur_activite=excluded.secteur_activite, "
        "date_introduction_bourse=excluded.date_introduction_bourse, "
        "url_fiche=excluded.url_fiche, maj_le=excluded.maj_le",
        (row["id_ammc"], row["nom"], row["secteur_activite"],
         row["date_introduction_bourse"], row["url_fiche"]),
    )


def scrape_emetteurs(session: requests.Session, con: sqlite3.Connection,
                      sleep: float, with_details: bool, quiet: bool) -> int:
    page = 0
    n = 0
    while True:
        html = get_html(session, LIST_EMETTEURS_URL, params={"page": page})
        if html is None:
            break
        rows, has_next = parse_emetteurs_page(html)
        if not rows:
            break
        for row in rows:
            upsert_emetteur(con, row)
            n += 1
        con.commit()
        if not quiet:
            print(f"  émetteurs page {page} : {len(rows)} sociétés")
        if not has_next:
            break
        page += 1
        time.sleep(sleep)

    if with_details:
        for id_ammc, url_fiche in con.execute("SELECT id_ammc, url_fiche FROM emetteurs"):
            html = get_html(session, url_fiche)
            if html is None:
                continue
            details = parse_emetteur_details(html)
            for cle, valeur in details.items():
                con.execute(
                    "INSERT INTO emetteurs_details (id_ammc, cle, valeur) VALUES (?, ?, ?) "
                    "ON CONFLICT(id_ammc, cle) DO UPDATE SET valeur=excluded.valeur",
                    (id_ammc, cle, valeur),
                )
            con.commit()
            if not quiet:
                print(f"  fiche {id_ammc} : {len(details)} champs")
            time.sleep(sleep)
    return n


# ----------------------------------------------------------------------------
# Étape 2 : catalogue des documents financiers
# ----------------------------------------------------------------------------
def parse_documents_page(html: str) -> tuple[list[dict], int | None]:
    """Renvoie (lignes, numéro de dernière page si trouvé sur cette page)."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="cols-4")
    if table is None or table.find("tbody") is None:
        return [], None

    rows = []
    for tr in table.find("tbody").find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 4:
            continue
        emetteur_cell, annee_cell, type_cell = cells[1], cells[2], cells[3]
        links = emetteur_cell.find_all("a")
        if not links:
            continue
        url_page = urljoin(BASE_URL, links[0]["href"])
        emetteur_nom = links[-1].get_text(strip=True)
        emetteur_slug = links[-1]["href"].rstrip("/").rsplit("/", 1)[-1] \
            if len(links) > 1 else None
        time_tag = annee_cell.find("time")
        annee = None
        if time_tag and time_tag.get_text(strip=True):
            m = re.search(r"\d{4}", time_tag.get_text(strip=True))
            annee = int(m.group()) if m else None
        type_rapport = type_cell.get_text(strip=True)
        rows.append({
            "url_page": url_page,
            "emetteur_slug": emetteur_slug,
            "emetteur_nom": emetteur_nom,
            "annee": annee,
            "type_rapport": type_rapport or None,
        })

    last_page = None
    last_link = soup.find("li", class_="pager__item--last")
    if last_link and last_link.find("a"):
        m = re.search(r"page=(\d+)", last_link.find("a")["href"])
        if m:
            last_page = int(m.group(1))
    return rows, last_page


def upsert_document(con: sqlite3.Connection, row: dict) -> bool:
    """Insère si nouveau. Renvoie True si la ligne était nouvelle."""
    existing = con.execute(
        "SELECT 1 FROM documents WHERE url_page = ?", (row["url_page"],)
    ).fetchone()
    if existing:
        return False
    con.execute(
        "INSERT INTO documents (url_page, emetteur_slug, emetteur_nom, annee, "
        "type_rapport, statut, maj_le) VALUES (?, ?, ?, ?, ?, 'a_traiter', datetime('now'))",
        (row["url_page"], row["emetteur_slug"], row["emetteur_nom"],
         row["annee"], row["type_rapport"]),
    )
    return True


def scrape_documents(session: requests.Session, con: sqlite3.Connection,
                      sleep: float, max_pages: int | None, quiet: bool) -> int:
    html = get_html(session, LIST_ETATS_URL, params={"page": 0})
    if html is None:
        return 0
    rows, last_page = parse_documents_page(html)
    total_new = 0
    for row in rows:
        if upsert_document(con, row):
            total_new += 1
    con.commit()

    pages_todo = range(1, (last_page or 0) + 1)
    if max_pages is not None:
        pages_todo = list(pages_todo)[: max(0, max_pages - 1)]

    if not quiet:
        print(f"  documents page 0 : {len(rows)} lignes "
              f"({total_new} nouvelles) — dernière page connue : {last_page}")

    for page in pages_todo:
        html = get_html(session, LIST_ETATS_URL, params={"page": page})
        if html is None:
            continue
        rows, _ = parse_documents_page(html)
        n_new = 0
        for row in rows:
            if upsert_document(con, row):
                n_new += 1
        con.commit()
        total_new += n_new
        if not quiet:
            print(f"  documents page {page} : {len(rows)} lignes ({n_new} nouvelles)")
        time.sleep(sleep)

    return total_new


# ----------------------------------------------------------------------------
# Étape 3 : résolution PDF, téléchargement, extraction
# ----------------------------------------------------------------------------
def resolve_pdf_url(session: requests.Session, url_page: str) -> str | None:
    html = get_html(session, url_page)
    if html is None:
        return None
    soup = BeautifulSoup(html, "lxml")
    link = soup.find("a", href=re.compile(r"\.pdf($|\?)", re.I))
    return urljoin(BASE_URL, link["href"]) if link else None


def download_pdf(session: requests.Session, url: str, dest: Path,
                  retries: int = 6) -> bool:
    """Télécharge avec reprise (Range) — les gros PDF AMMC coupent souvent
    en cours de route. Renvoie True si la taille finale correspond au
    Content-Length annoncé."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    expected_size = None
    for attempt in range(1, retries + 1):
        existing = dest.stat().st_size if dest.exists() else 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}
        try:
            with session.get(url, headers=headers, stream=True, timeout=60) as resp:
                if resp.status_code == 416:  # déjà complet
                    break
                resp.raise_for_status()
                if "Content-Range" in resp.headers:
                    expected_size = int(resp.headers["Content-Range"].split("/")[-1])
                elif "Content-Length" in resp.headers:
                    expected_size = existing + int(resp.headers["Content-Length"]) \
                        if resp.status_code == 206 else int(resp.headers["Content-Length"])
                mode = "ab" if existing and resp.status_code == 206 else "wb"
                with open(dest, mode) as f:
                    for chunk in resp.iter_content(chunk_size=1 << 16):
                        if chunk:
                            f.write(chunk)
        except requests.RequestException:
            pass
        if expected_size is not None and dest.exists() and dest.stat().st_size >= expected_size:
            return True
        time.sleep(min(2 ** attempt, 20))
    return expected_size is None or (dest.exists() and dest.stat().st_size >= expected_size)


def _parse_num(cell: str | None) -> float | None:
    if not cell:
        return None
    s = cell.strip()
    if not NUM_RE.match(s):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(" ", "").replace(" ", "")
    if s.count(",") == 1 and s.count(".") == 0:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    if s in ("", "-", "."):
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if neg else val


def extract_financials(pdf_path: Path, annee: int | None) -> list[dict]:
    """Best-effort : repère les pages Bilan/CPC/ESG via mots-clés CGNC et
    en extrait les tableaux. Renvoie une liste de rubriques (peut être
    vide si aucune table exploitable n'a été trouvée)."""
    import pdfplumber

    out = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = (page.extract_text() or "").upper()
            if not text:
                continue
            matched_etats = [
                etat for etat, kws in ETAT_KEYWORDS.items()
                if sum(1 for kw in kws if kw in text) >= MIN_KEYWORD_HITS
            ]
            if not matched_etats:
                continue
            etat = matched_etats[0]
            for table in page.extract_tables() or []:
                for r in table:
                    cells = [c.strip() if isinstance(c, str) else c for c in r]
                    cells = [c for c in cells if c is not None]
                    if len(cells) < 2:
                        continue
                    rubrique = cells[0].strip() if isinstance(cells[0], str) else None
                    if not rubrique or NUM_RE.match(rubrique):
                        continue
                    if len(re.sub(r"[^A-Za-zÀ-ÿ]", "", rubrique)) < 3:
                        continue  # exclut les marqueurs de note type "(e)"
                    valeur_num = None
                    valeur_txt = None
                    for c in cells[1:]:
                        v = _parse_num(c if isinstance(c, str) else None)
                        if v is not None:
                            valeur_num, valeur_txt = v, c
                            break
                    if valeur_num is None:
                        continue
                    out.append({
                        "etat": etat,
                        "rubrique": rubrique,
                        "valeur": valeur_txt,
                        "valeur_num": valeur_num,
                        "page_pdf": page.page_number,
                    })
    return out


def process_document(session: requests.Session, con: sqlite3.Connection,
                      doc: sqlite3.Row, quiet: bool) -> str:
    url_pdf = doc["url_pdf"] or resolve_pdf_url(session, doc["url_page"])
    if url_pdf is None:
        con.execute(
            "UPDATE documents SET statut='pas_de_pdf', maj_le=datetime('now') "
            "WHERE url_page=?", (doc["url_page"],),
        )
        con.execute(
            "INSERT OR REPLACE INTO documents_non_extraits (url_page, url_pdf, raison, traite_le) "
            "VALUES (?, NULL, 'aucun lien PDF trouvé sur la page', datetime('now'))",
            (doc["url_page"],),
        )
        con.commit()
        return "pas_de_pdf"

    dest = PDF_DIR / f"{doc['emetteur_slug'] or 'inconnu'}_{doc['annee'] or 'na'}_" \
                     f"{Path(url_pdf).name}"
    ok = download_pdf(session, url_pdf, dest)
    if not ok:
        con.execute(
            "UPDATE documents SET url_pdf=?, statut='echec_telechargement', "
            "maj_le=datetime('now') WHERE url_page=?", (url_pdf, doc["url_page"]),
        )
        con.commit()
        return "echec_telechargement"

    try:
        rubriques = extract_financials(dest, doc["annee"])
    except Exception as exc:  # noqa: BLE001 - on journalise et on continue
        rubriques = []
        if not quiet:
            print(f"  [!] extraction {dest.name} : {exc}", file=sys.stderr)

    if len(rubriques) >= MIN_RUBRIQUES_OK and doc["annee"] is not None:
        for r in rubriques:
            con.execute(
                "INSERT OR REPLACE INTO etats_financiers_ammc "
                "(emetteur_slug, annee, etat, rubrique, valeur, valeur_num, page_pdf, url_pdf) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (doc["emetteur_slug"], doc["annee"], r["etat"], r["rubrique"],
                 r["valeur"], r["valeur_num"], r["page_pdf"], url_pdf),
            )
        con.execute(
            "UPDATE documents SET url_pdf=?, statut='ok', maj_le=datetime('now') "
            "WHERE url_page=?", (url_pdf, doc["url_page"]),
        )
        statut = "ok"
    else:
        con.execute(
            "UPDATE documents SET url_pdf=?, statut='echec_extraction', maj_le=datetime('now') "
            "WHERE url_page=?", (url_pdf, doc["url_page"]),
        )
        con.execute(
            "INSERT OR REPLACE INTO documents_non_extraits (url_page, url_pdf, raison, traite_le) "
            "VALUES (?, ?, ?, datetime('now'))",
            (doc["url_page"], url_pdf,
             f"seulement {len(rubriques)} rubrique(s) détectée(s) (seuil {MIN_RUBRIQUES_OK})"),
        )
        statut = "echec_extraction"

    con.commit()
    dest.unlink(missing_ok=True)  # ne pas garder les PDF (plusieurs Mo chacun) dans le dépôt
    return statut


def extraire(session: requests.Session, con: sqlite3.Connection,
              max_new: int | None, sleep: float, quiet: bool) -> dict:
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM documents WHERE statut='a_traiter' ORDER BY annee DESC"
    ).fetchall()
    if max_new is not None:
        rows = rows[:max_new]

    compte = {"ok": 0, "echec_extraction": 0, "echec_telechargement": 0, "pas_de_pdf": 0}
    for doc in rows:
        statut = process_document(session, con, doc, quiet)
        compte[statut] = compte.get(statut, 0) + 1
        if not quiet:
            print(f"  {doc['emetteur_nom']} {doc['annee']} — {doc['type_rapport']} : {statut}")
        time.sleep(sleep)
    return compte


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DB_PATH, help="Chemin de la base SQLite")
    parser.add_argument("--step", choices=["emetteurs", "documents", "extraire", "all"],
                         default="all")
    parser.add_argument("--max-new", type=int, default=None,
                         help="Limite de documents à traiter (étape extraire) "
                              "ou de pages à parcourir (étape documents)")
    parser.add_argument("--with-details", action="store_true",
                         help="Étape emetteurs : récupère aussi la fiche détaillée de chacun")
    parser.add_argument("--sleep", type=float, default=0.4,
                         help="Délai (s) entre deux requêtes, par courtoisie envers ammc.ma")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    ensure_schema(con)
    session = make_session()

    if args.step in ("emetteurs", "all"):
        n = scrape_emetteurs(session, con, args.sleep, args.with_details, args.quiet)
        print(f"Émetteurs : {n} lignes insérées/mises à jour.")

    if args.step in ("documents", "all"):
        n = scrape_documents(session, con, args.sleep, args.max_new, args.quiet)
        print(f"Documents : {n} nouveau(x) document(s) catalogué(s).")

    if args.step == "extraire":
        compte = extraire(session, con, args.max_new, args.sleep, args.quiet)
        print(f"Extraction : {compte}")

    con.close()


if __name__ == "__main__":
    main()
