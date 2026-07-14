# -*- coding: utf-8 -*-
"""
Bourse de Casablanca — Explorateur d'états financiers
Base : financials_cse.db — table `etats_financiers`
(Symbole, Etat [Bilan/CPC/Flux], Rubrique, Exercice, Valeur, Valeur_num)

Vues : fiche société par exercice + comparaison multi-sociétés.
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
    page_title="États financiers — Bourse de Casablanca",
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
# Chargement de la base
# ----------------------------------------------------------------------------
def find_local_db() -> str | None:
    for f in sorted(os.listdir(".")):
        if f.lower().endswith((".db", ".sqlite", ".sqlite3")):
            return f
    return None


def parse_num(valeur_num, valeur_txt):
    """Valeur_num si disponible, sinon parse le texte ('+4.97%' → 4.97)."""
    if pd.notna(valeur_num):
        return float(valeur_num)
    if valeur_txt is None or pd.isna(valeur_txt):
        return None
    s = str(valeur_txt).strip().replace("%", "").replace("+", "")
    s = s.replace("\u202f", "").replace(" ", "").replace(",", "")
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
# Barre latérale
# ----------------------------------------------------------------------------
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

# ----------------------------------------------------------------------------
# En-tête
# ----------------------------------------------------------------------------
st.title("📊 États financiers — Bourse de Casablanca")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Sociétés", len(societes))
c2.metric("Exercices", f"{min(exercices)} – {max(exercices)}")
c3.metric("Rubriques", sub_etat["Rubrique"].nunique())
c4.metric("État", etat)

tab_fiche, tab_comp = st.tabs(["🏢 Fiche société", "⚖️ Comparaison"])

# ----------------------------------------------------------------------------
# Onglet 1 : fiche société — grandeurs par exercice
# ----------------------------------------------------------------------------
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

# ----------------------------------------------------------------------------
# Onglet 2 : comparaison multi-sociétés
# ----------------------------------------------------------------------------
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
