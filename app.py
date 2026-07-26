# -*- coding: utf-8 -*-
"""
Bourse de Casablanca — Explorateur d'états financiers et de performances OPCVM
Bases : financials_cse.db (table `etats_financiers`) et opcvm.db
(table `performances_opcvm`, AN/VL des OPCVM marocains — source ASFIM).
"""

import os
import re
import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
st.set_page_config(
    page_title="Bourse de Casablanca — États financiers & OPCVM",
    page_icon="📊",
    layout="wide",
)

PALETTE = ["#0F4C5C", "#E36414", "#5F0F40", "#2A9D8F", "#9A8C98",
           "#E9C46A", "#264653", "#B5838D"]

TABLE = "etats_financiers"
ETATS_LABELS = {"Bilan": "🏛️ Bilan", "CPC": "📈 CPC", "Flux": "💧 Flux de trésorerie"}

# Traductions FR des rubriques les plus courantes (source Investing.com, EN)
FR = {
    "Total Assets": "Total actif",
    "Total Liabilities": "Total passif (dettes)",
    "Total Equity": "Capitaux propres",
    "Total Liabilities And Equity": "Total passif et capitaux propres",
    "Cash And Equivalents": "Trésorerie et équivalents",
    "Net Loans": "Créances nettes (prêts)",
    "Gross Loans": "Créances brutes (prêts)",
    "Total Deposits": "Dépôts de la clientèle",
    "Total Debt": "Dette totale",
    "Long-Term Debt": "Dette à long terme",
    "Total Current Assets": "Actif circulant",
    "Total Current Liabilities": "Passif circulant",
    "Total Receivables": "Créances totales",
    "Inventory": "Stocks",
    "Net Property Plant And Equipment": "Immobilisations corporelles nettes",
    "Intangible Assets": "Immobilisations incorporelles",
    "Goodwill": "Écart d'acquisition (goodwill)",
    "Retained Earnings": "Report à nouveau / réserves",
    "Minority Interest, Total": "Intérêts minoritaires",
    "Total Revenues": "Chiffre d'affaires",
    "Cost Of Revenues": "Coût des ventes",
    "Gross Profit": "Marge brute",
    "Operating Income": "Résultat d'exploitation",
    "EBITDA": "EBE (EBITDA)",
    "EBIT": "Résultat avant intérêts et impôts (EBIT)",
    "Net Income": "Résultat net",
    "Net Income to Company": "Résultat net (part du groupe incl. minoritaires)",
    "Income Tax Expense": "Impôt sur les résultats",
    "Interest Income, Total": "Produits d'intérêts",
    "Interest Expense, Total": "Charges d'intérêts",
    "Net Interest Income": "Marge nette d'intérêt (PNB bancaire partiel)",
    "Provision For Loan Losses": "Coût du risque (provisions sur créances)",
    "Basic EPS - Continuing Operations": "BPA de base",
    "Diluted EPS - Continuing Operations": "BPA dilué",
    "Dividend Per Share": "Dividende par action",
    "Cash from Operations": "Flux de trésorerie d'exploitation",
    "Cash from Investing": "Flux de trésorerie d'investissement",
    "Cash from Financing": "Flux de trésorerie de financement",
    "Net Change in Cash": "Variation nette de trésorerie",
    "Capital Expenditure": "Investissements (CAPEX)",
    "Levered Free Cash Flow": "Free cash-flow (après dette)",
    "Common & Preferred Stock Dividends Paid": "Dividendes versés",
    "Beginning Cash Balance": "Trésorerie d'ouverture",
    "Ending Cash Balance": "Trésorerie de clôture",
    "Total Depreciation, Depletion & Amortization": "Dotations aux amortissements",
}

# Rubriques proposées par défaut dans les graphiques, par état
DEFAULTS = {
    "Bilan": ["Total Assets", "Total Equity", "Total Debt", "Total Deposits"],
    "CPC": ["Total Revenues", "Operating Income", "Net Income",
            "Net Interest Income"],
    "Flux": ["Cash from Operations", "Capital Expenditure",
             "Levered Free Cash Flow"],
}

OPCVM_DB_PATH = "opcvm.db"
OPCVM_TABLE = "performances_opcvm"

st.markdown(
    """
    <style>
      .block-container {padding-top: 2rem;}
      h1 {font-weight: 700; letter-spacing: -0.02em;}
      [data-testid="stMetricValue"] {font-size: 1.5rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------------
# Chargement de la base — états financiers
# ----------------------------------------------------------------------------
def find_local_db() -> str | None:
    for f in sorted(os.listdir(".")):
        if f.lower().endswith((".db", ".sqlite", ".sqlite3")) and f != OPCVM_DB_PATH:
            return f
    return None


def parse_num(valeur_num, valeur_txt):
    """Valeur_num si disponible, sinon parse le texte ('+4.97%' → 4.97)."""
    if pd.notna(valeur_num):
        return float(valeur_num)
    if valeur_txt is None or pd.isna(valeur_txt):
        return None
    s = str(valeur_txt).strip().replace("%", "").replace("+", "")
    s = s.replace(" ", "").replace(" ", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


@st.cache_data(show_spinner="Chargement de la base…")
def load_data(path: str) -> pd.DataFrame:
    con = sqlite3.connect(path)
    df = pd.read_sql_query(
        f'SELECT rowid AS _rid, Symbole, Etat, Rubrique, Exercice, '
        f'Valeur, Valeur_num FROM "{TABLE}"', con
    )
    con.close()
    df["Exercice"] = pd.to_numeric(df["Exercice"], errors="coerce")
    df = df.dropna(subset=["Exercice"])
    df["Exercice"] = df["Exercice"].astype(int)
    df["Montant"] = [parse_num(a, b) for a, b in zip(df["Valeur_num"], df["Valeur"])]
    df = df.dropna(subset=["Montant"])
    df["est_pct"] = df["Rubrique"].str.contains(
        r"Growth|Margin|Yield|%", case=False, regex=True
    ) | df["Valeur"].astype(str).str.contains("%", na=False)
    df["est_croissance"] = df["Rubrique"].str.contains("Growth", case=False)
    df["Libelle"] = df["Rubrique"].map(lambda r: FR.get(r, r))
    return df


def rubrique_order(df: pd.DataFrame) -> list[str]:
    """Ordre naturel des rubriques tel que stocké (structure de l'état)."""
    return list(df.sort_values("_rid").drop_duplicates("Rubrique")["Rubrique"])


def fmt(x, pct=False):
    if pd.isna(x):
        return "—"
    dec = 2 if pct or abs(x) < 100 else 0
    s = f"{x:,.{dec}f}".replace(",", " ").replace(".", ",")
    return s + " %" if pct else s


# ----------------------------------------------------------------------------
# Module 1 : États financiers
# ----------------------------------------------------------------------------
def render_financials():
    st.sidebar.title("⚙️ Données")

    db_path = find_local_db()

    if db_path is None:
        st.title("📊 États financiers — Bourse de Casablanca")
        st.error("Base introuvable : ajoutez `financials_cse.db` à la racine du dépôt.")
        st.stop()

    data = load_data(db_path)
    if data.empty:
        st.error("Aucune donnée exploitable dans la table etats_financiers.")
        st.stop()

    etats = [e for e in ETATS_LABELS if e in set(data["Etat"])] or \
            sorted(data["Etat"].unique())
    etat = st.sidebar.radio(
        "État financier", etats,
        format_func=lambda e: ETATS_LABELS.get(e, e),
    )
    masquer_growth = st.sidebar.toggle(
        "Masquer les lignes de croissance (%)", value=True,
        help="Les variations N-1→N sont recalculées par l'application ; "
             "les lignes « Growth » de la source sont redondantes.",
    )
    libelles_fr = st.sidebar.toggle("Libellés en français", value=True)

    st.sidebar.caption(
        "Montants en **millions de MAD** (MMAD), tels que publiés par la source. "
        "Les rubriques « % » (marges, rendements) sont en pourcentage."
    )

    sub_etat = data[data["Etat"] == etat]
    if masquer_growth:
        sub_etat = sub_etat[~sub_etat["est_croissance"]]

    lab_col = "Libelle" if libelles_fr else "Rubrique"
    ordre = rubrique_order(sub_etat)
    label_of = dict(zip(sub_etat["Rubrique"], sub_etat[lab_col]))

    societes = sorted(data["Symbole"].unique())
    exercices = sorted(sub_etat["Exercice"].unique())

    # ------------------------------------------------------------------------
    # En-tête
    # ------------------------------------------------------------------------
    st.title("📊 États financiers — Bourse de Casablanca")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Sociétés", len(societes))
    c2.metric("Exercices", f"{min(exercices)} – {max(exercices)}")
    c3.metric("Rubriques", sub_etat["Rubrique"].nunique())
    c4.metric("État", etat)

    tab_fiche, tab_comp = st.tabs(["🏢 Fiche société", "⚖️ Comparaison"])

    # ------------------------------------------------------------------------
    # Onglet 1 : fiche société — grandeurs par exercice
    # ------------------------------------------------------------------------
    with tab_fiche:
        soc = st.selectbox("Société (symbole)", societes, key="fiche_soc")
        sub = sub_etat[sub_etat["Symbole"] == soc]

        if sub.empty:
            st.info(f"Pas de données {etat} pour {soc}.")
        else:
            ordre_soc = [r for r in rubrique_order(sub)]
            pivot = sub.pivot_table(index="Rubrique", columns="Exercice",
                                    values="Montant", aggfunc="first")
            pivot = pivot.reindex(ordre_soc)
            pct_mask = sub.drop_duplicates("Rubrique").set_index("Rubrique")["est_pct"]

            # Variation dernier exercice (uniquement pour les montants, pas les %)
            if pivot.shape[1] >= 2:
                last, prev = pivot.columns[-1], pivot.columns[-2]
                var = (pivot[last] - pivot[prev]) / pivot[prev].abs() * 100
                var[pct_mask.reindex(pivot.index).fillna(False)] = None
                delta_name = f"Δ {prev}→{last} (%)"
            else:
                var, delta_name = None, None

            disp = pd.DataFrame(index=pivot.index)
            for col in pivot.columns:
                disp[str(col)] = [
                    fmt(v, pct=bool(pct_mask.get(r, False)))
                    for r, v in pivot[col].items()
                ]
            if var is not None:
                disp[delta_name] = [fmt(v, pct=True) if pd.notna(v) else "—"
                                    for v in var]
            disp.index = [label_of.get(r, r) for r in disp.index]
            disp.index.name = f"Rubrique — {etat} (MMAD)"

            st.dataframe(disp, use_container_width=True,
                         height=min(620, 45 + 35 * len(disp)))

            csv = pivot.copy()
            csv.index = disp.index
            st.download_button(
                "⬇️ Exporter (CSV)",
                csv.to_csv().encode("utf-8-sig"),
                file_name=f"{soc}_{etat}.csv", mime="text/csv",
            )

            st.divider()
            montants = [r for r in ordre_soc if not pct_mask.get(r, False)]
            defauts = [r for r in DEFAULTS.get(etat, []) if r in montants]
            sel = st.multiselect(
                "Rubriques à tracer", montants,
                default=defauts or montants[:3],
                format_func=lambda r: label_of.get(r, r),
                key="fiche_rub",
            )
            if sel:
                plot_df = sub[sub["Rubrique"].isin(sel)].copy()
                plot_df["Rubrique"] = plot_df["Rubrique"].map(
                    lambda r: label_of.get(r, r))
                fig = px.bar(
                    plot_df, x="Exercice", y="Montant", color="Rubrique",
                    barmode="group", color_discrete_sequence=PALETTE,
                    labels={"Montant": "MMAD"},
                    title=f"{soc} — {etat} par exercice",
                )
                fig.update_layout(xaxis=dict(type="category"), height=450,
                                  legend=dict(orientation="h", y=-0.22))
                st.plotly_chart(fig, use_container_width=True)

    # ------------------------------------------------------------------------
    # Onglet 2 : comparaison multi-sociétés
    # ------------------------------------------------------------------------
    with tab_comp:
        cc1, cc2 = st.columns(2)
        with cc1:
            soc1 = st.selectbox("Valeur 1", societes, index=0, key="comp_s1")
        with cc2:
            autres = [s for s in societes if s != soc1]
            soc2 = st.selectbox("Valeur 2", autres, index=0, key="comp_s2")

        comp = sub_etat[sub_etat["Symbole"].isin([soc1, soc2])]

        if comp.empty:
            st.info("Aucune donnée pour cette sélection.")
        else:
            st.markdown(
                f"**{soc1} vs {soc2}** — {ETATS_LABELS.get(etat, etat)} complet, "
                f"année par année ({min(exercices)}–{max(exercices)})"
            )
            pivot = comp.pivot_table(
                index="Rubrique", columns=["Exercice", "Symbole"],
                values="Montant", aggfunc="first",
            )
            # Lignes : ordre naturel de l'état ; colonnes : année puis V1 | V2
            ordre_comp = [r for r in ordre if r in pivot.index]
            pivot = pivot.reindex(ordre_comp)
            annees = sorted({c[0] for c in pivot.columns})
            cols = [(a, s) for a in annees for s in (soc1, soc2)
                    if (a, s) in pivot.columns]
            pivot = pivot[cols]

            pct_mask = (comp.drop_duplicates("Rubrique")
                        .set_index("Rubrique")["est_pct"])

            disp = pd.DataFrame(index=pivot.index)
            for col in pivot.columns:
                disp[col] = [fmt(v, pct=bool(pct_mask.get(r, False)))
                             for r, v in pivot[col].items()]
            disp.columns = pd.MultiIndex.from_tuples(
                [(str(a), s) for a, s in disp.columns],
                names=["Exercice", "Société"],
            )
            disp.index = [label_of.get(r, r) for r in disp.index]
            disp.index.name = f"Rubrique — {etat} (MMAD)"

            st.dataframe(disp, use_container_width=True,
                         height=min(640, 80 + 35 * len(disp)))

            csv = pivot.copy()
            csv.columns = [f"{a}_{s}" for a, s in csv.columns]
            csv.index = disp.index
            st.download_button(
                "⬇️ Exporter la comparaison (CSV)",
                csv.to_csv().encode("utf-8-sig"),
                file_name=f"comparaison_{soc1}_vs_{soc2}_{etat}.csv",
                mime="text/csv",
            )

    st.caption(
        "Source : pipeline de collecte Investing.com "
        "Montants en millions de MAD tels que publiés ; libellés d'origine en anglais, "
        "traduction française indicative."
    )


# ----------------------------------------------------------------------------
# Module 2 : OPCVM — performances AN / VL (source ASFIM)
# ----------------------------------------------------------------------------
@st.cache_data(show_spinner="Chargement des données OPCVM…")
def load_opcvm_data(path: str) -> pd.DataFrame:
    con = sqlite3.connect(path)
    df = pd.read_sql_query(
        f'SELECT p.date, p.code_isin, f.opcvm, f.societe_gestion, f.classification, '
        f'f.periodicite_vl, p.an, p.vl '
        f'FROM "{OPCVM_TABLE}" p JOIN fonds f ON f.code_isin = p.code_isin '
        f'WHERE p.vl IS NOT NULL',
        con,
    )
    con.close()
    df["date"] = pd.to_datetime(df["date"])
    return df


def valeurs_a_la_date(sous_fonds: pd.DataFrame, cible):
    """Dernières AN/VL connues à la date cible ou avant (asof)."""
    avant = sous_fonds[sous_fonds["date"] <= pd.Timestamp(cible)]
    if avant.empty:
        return None, None, None
    ligne = avant.iloc[-1]
    return ligne["date"].date(), ligne["an"], ligne["vl"]


def render_opcvm():
    if not os.path.exists(OPCVM_DB_PATH):
        st.error(f"Base introuvable : ajoutez `{OPCVM_DB_PATH}` à la racine du dépôt.")
        st.stop()

    data = load_opcvm_data(OPCVM_DB_PATH)
    if data.empty:
        st.error("Aucune donnée exploitable dans la table performances_opcvm.")
        st.stop()

    data = data.sort_values("date")

    # Fiche la plus récente par fonds : sert aux filtres (classification,
    # périodicité) indépendamment de la période de calcul choisie ensuite.
    derniere_fiche = data.drop_duplicates("code_isin", keep="last").set_index("code_isin")

    st.sidebar.title("⚙️ Filtres OPCVM")

    classifications = sorted(derniere_fiche["classification"].dropna().unique())
    sel_classifications = st.sidebar.multiselect(
        "Classification", classifications, default=classifications,
    )

    periodicites = sorted(derniere_fiche["periodicite_vl"].dropna().unique())
    sel_periodicites = st.sidebar.multiselect(
        "Périodicité VL", periodicites, default=periodicites,
    )

    fonds_filtres = derniere_fiche[
        derniere_fiche["classification"].isin(sel_classifications)
        & derniere_fiche["periodicite_vl"].isin(sel_periodicites)
    ]
    st.sidebar.caption(f"{len(fonds_filtres)} OPCVM correspondent aux filtres.")

    # Un même nom de fonds correspond normalement à un seul code ISIN.
    isin_par_nom: dict[str, list[str]] = {}
    for isin, nom in fonds_filtres["opcvm"].items():
        isin_par_nom.setdefault(nom, []).append(isin)

    st.title("📈 OPCVM — Performances sur une période (ASFIM)")
    c1, c2, c3 = st.columns(3)
    c1.metric("OPCVM (filtre)", len(fonds_filtres))
    c2.metric("OPCVM (total)", derniere_fiche.shape[0])
    c3.metric("Historique", f"{data['date'].min().date()} – {data['date'].max().date()}")

    noms_tries = sorted(isin_par_nom)
    default_sel = noms_tries[: min(10, len(noms_tries))]
    sel_noms = st.multiselect(
        "OPCVM à comparer", noms_tries, default=default_sel,
    )
    if not sel_noms:
        st.info("Sélectionnez au moins un OPCVM dans la liste ci-dessus.")
        st.stop()
    sel_isins = [isin for nom in sel_noms for isin in isin_par_nom[nom]]

    date_min = data["date"].min().date()
    date_max = data["date"].max().date()
    default_start = max(date_min, date_max - pd.Timedelta(days=365))
    c_period, c_tri = st.columns([2, 1])
    with c_period:
        plage = st.date_input(
            "Période de calcul de la performance",
            value=(default_start, date_max),
            min_value=date_min, max_value=date_max,
        )
    with c_tri:
        ordre = st.radio(
            "Trier par performance", ["Décroissante", "Croissante"],
            horizontal=True,
        )

    if not isinstance(plage, tuple) or len(plage) != 2:
        st.info("Choisissez une date de début et une date de fin.")
        st.stop()
    date_debut, date_fin = plage
    if date_debut > date_fin:
        st.error("La date de début doit précéder la date de fin.")
        st.stop()

    sous = data[data["code_isin"].isin(sel_isins)]

    lignes = []
    for isin in sel_isins:
        sf = sous[sous["code_isin"] == isin]
        if sf.empty:
            continue
        d0, an0, vl0 = valeurs_a_la_date(sf, date_debut)
        d1, an1, vl1 = valeurs_a_la_date(sf, date_fin)
        perf = (vl1 / vl0 - 1) * 100 if vl0 and vl1 else None
        fiche = derniere_fiche.loc[isin]
        lignes.append({
            "OPCVM": fiche["opcvm"],
            "Société de gestion": fiche["societe_gestion"],
            "Classification": fiche["classification"],
            "Périodicité VL": fiche["periodicite_vl"],
            "Date début": d0,
            "AN début": an0,
            "VL début": vl0,
            "Date fin": d1,
            "AN fin": an1,
            "VL fin": vl1,
            "Performance (%)": perf,
        })

    resultats = pd.DataFrame(lignes)
    if resultats.empty:
        st.info("Aucune donnée disponible pour cette sélection et cette période.")
        st.stop()

    for col in ("AN début", "VL début", "AN fin", "VL fin", "Performance (%)"):
        resultats[col] = pd.to_numeric(resultats[col], errors="coerce")

    resultats = resultats.sort_values(
        "Performance (%)", ascending=(ordre == "Croissante"), na_position="last",
    ).reset_index(drop=True)

    def _fmt_date(d):
        return d.isoformat() if d else "—"

    def _fmt_num(x, signe=False, decimales=2):
        if pd.isna(x):
            return "—"
        return f"{x:+,.{decimales}f}" if signe else f"{x:,.{decimales}f}"

    disp = resultats.copy()
    disp["Date début"] = disp["Date début"].map(_fmt_date)
    disp["Date fin"] = disp["Date fin"].map(_fmt_date)
    disp["AN début"] = disp["AN début"].map(lambda x: _fmt_num(x, decimales=0))
    disp["AN fin"] = disp["AN fin"].map(lambda x: _fmt_num(x, decimales=0))
    disp["VL début"] = disp["VL début"].map(_fmt_num)
    disp["VL fin"] = disp["VL fin"].map(_fmt_num)
    disp["Performance (%)"] = disp["Performance (%)"].map(lambda x: _fmt_num(x, signe=True))

    st.dataframe(
        disp, use_container_width=True,
        height=min(600, 60 + 35 * len(disp)),
    )

    st.download_button(
        "⬇️ Exporter (CSV)",
        resultats.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"performances_opcvm_{date_debut}_{date_fin}.csv",
        mime="text/csv",
    )

    graphe = resultats.dropna(subset=["Performance (%)"])
    if not graphe.empty:
        fig = px.bar(
            graphe, x="Performance (%)", y="OPCVM", orientation="h",
            color="Classification", color_discrete_sequence=PALETTE,
            title=f"Performance du {date_debut} au {date_fin}",
        )
        fig.update_layout(
            height=max(350, 32 * len(graphe)),
            yaxis=dict(categoryorder="total ascending"),
            legend=dict(orientation="h", y=-0.15),
        )
        st.plotly_chart(fig, use_container_width=True)

    st.caption(
        "Source : ASFIM — tableaux des performances quotidiennes/hebdomadaires. "
        "La performance est calculée à partir des dernières VL connues à la date "
        "de début et à la date de fin choisies (VL non publiée un jour donné → "
        "dernière valeur disponible avant cette date)."
    )


# ----------------------------------------------------------------------------
# Sélecteur de module
# ----------------------------------------------------------------------------
MODULE_FINANCIER = "🏦 États financiers"
MODULE_OPCVM = "📈 OPCVM (AN / VL)"

st.sidebar.title("📊 Bourse de Casablanca")
module = st.sidebar.radio("Module", [MODULE_FINANCIER, MODULE_OPCVM], key="module")
st.sidebar.divider()

if module == MODULE_FINANCIER:
    render_financials()
else:
    render_opcvm()
