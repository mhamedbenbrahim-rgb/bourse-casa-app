# 📊 États financiers — Bourse de Casablanca

Application Streamlit pour présenter les grandeurs financières (Bilan, CPC,
Flux de trésorerie) par exercice et comparer plusieurs sociétés cotées, à
partir d'une base SQLite.

## Structure du dépôt

```
bourse-app/
├── app.py                  # l'application
├── requirements.txt        # dépendances
├── .streamlit/config.toml  # thème
└── bourse.db               # ← votre base SQLite (à ajouter)
```

## Déploiement sur Streamlit Cloud (gratuit)

1. **Créer un dépôt GitHub** (par ex. `bourse-casa-app`) et y pousser les
   fichiers ci-dessus, y compris votre fichier `.db` à la racine.
   Si la base contient des données que vous ne souhaitez pas rendre
   publiques, mettez le dépôt en **privé** (Streamlit Cloud y accède quand
   même).

   ```bash
   git init
   git add .
   git commit -m "App états financiers Bourse de Casablanca"
   git branch -M main
   git remote add origin https://github.com/VOTRE_COMPTE/bourse-casa-app.git
   git push -u origin main
   ```

2. Aller sur **https://share.streamlit.io** et se connecter avec GitHub.

3. Cliquer sur **"Create app" → "Deploy a public app from GitHub"**,
   choisir le dépôt, la branche `main` et le fichier `app.py`.

4. Cliquer sur **Deploy**. L'URL publique est de la forme
   `https://votre-app.streamlit.app`.

À chaque `git push` (par exemple après une mise à jour de la base par votre
pipeline de scraping), l'application se redéploie automatiquement.

> **Limite de taille** : GitHub accepte des fichiers jusqu'à 100 Mo. Pour
> ~80 sociétés avec 3 états financiers, une base SQLite reste en général
> bien en dessous. Au-delà, utilisez Git LFS ou le chargeur de fichier
> intégré à l'application.

## Test en local

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Fonctionnement

L'application est adaptée au schéma de `financials_cse.db` (table
`etats_financiers` : Symbole, Etat, Rubrique, Exercice, Valeur, Valeur_num) :

- Sélecteur **Bilan / CPC / Flux** dans la barre latérale
- **Fiche société** : toutes les rubriques par exercice dans l'ordre de
  l'état, variation N-1→N calculée, graphiques, export CSV
- **Comparaison** : une grandeur, plusieurs sociétés, plage d'exercices,
  TCAM
- Les lignes « Growth » de la source sont masquées par défaut (recalculées
  par l'app) ; les rubriques en % (marges, rendements) sont formatées en %
- Libellés traduits en français (désactivable) ; montants en **MMAD**

Les nouvelles sociétés ajoutées par le pipeline de scraping apparaissent
automatiquement — il suffit de pousser la base mise à jour sur GitHub.

## Données OPCVM (ASFIM) — `opcvm.db`

En complément des états financiers, le dépôt collecte les **tableaux de
performances des OPCVM marocains** publiés par l'ASFIM
(https://asfim.ma/publications/tableaux-des-performances/), avec l'Actif
Net (AN) et la Valeur Liquidative (VL) de chaque fonds, jour par jour et
depuis l'historique disponible.

- `opcvm_scraper.py` : interroge l'API publique utilisée par le site
  ASFIM (`fundshare.asfim.ma`) pour lister tous les tableaux publiés,
  télécharge le fichier Excel de chaque tableau non encore intégré et
  écrit les données dans `opcvm.db`. Un journal (`rapports_traites`)
  évite de retélécharger un tableau déjà traité — le script peut donc
  être relancé tel quel aussi bien pour la récupération initiale de
  l'historique que pour le contrôle quotidien d'un nouveau tableau.

  ```bash
  pip install requests openpyxl
  python opcvm_scraper.py --db opcvm.db          # ne traite que les tableaux manquants
  python opcvm_scraper.py --db opcvm.db --max-new 20   # limiter un essai
  ```

- `.github/workflows/update_opcvm.yml` : exécute ce script chaque jour
  (20h UTC) et pousse `opcvm.db` s'il y a du nouveau, sur le même
  principe que `update_ohlc.yml` pour les cours OHLC.

Schéma (normalisé pour rester compact malgré l'historique quotidien) :
- `fonds` : une ligne par OPCVM (`code_isin`), caractéristiques
  quasi-statiques — `opcvm` (nom), `societe_gestion`, `nature_juridique`,
  `classification`, `sensibilite`, `indice_benchmark`, `periodicite_vl`,
  `souscripteurs`, `affectation_resultats`, `commission_souscription`,
  `commission_rachat`, `frais_gestion`, `depositaire`, `reseau_placeur`.
- `performances_opcvm` : une ligne par fonds et par date — `date`,
  `is_hebdo`, `code_isin`, `an` (actif net), `vl` (valeur liquidative).
  Les performances glissantes publiées par l'ASFIM (YTD, 1 mois, 1 an…)
  ne sont volontairement pas stockées : elles seraient redondantes avec
  l'historique VL, à partir duquel l'application recalcule la
  performance sur n'importe quelle période choisie par l'utilisateur.

L'onglet **📈 OPCVM (AN / VL)** de `app.py` exploite cette base : filtres
par classification et par périodicité VL, sélection de plusieurs fonds,
calcul de la performance sur une période libre, tri croissant/décroissant
et export CSV.

## Données émetteurs (AMMC) — `ammc.db`

Le dépôt collecte également l'annuaire des émetteurs et leurs états
financiers réglementaires publiés par l'**AMMC**
(https://www.ammc.ma/fr/espace-emetteurs), en complément des états
financiers Investing.com et des OPCVM ASFIM.

Contrairement à ASFIM, le site AMMC n'a pas d'API : les listes sont des
tableaux HTML paginés et chaque document financier est un **PDF**
(souvent un rapport annuel complet de plusieurs dizaines de pages, mise
en page libre par émetteur). `ammc_scraper.py` travaille donc en trois
étapes indépendantes et reprises (`--step`) :

```bash
pip install requests beautifulsoup4 lxml pdfplumber
python ammc_scraper.py --db ammc.db --step emetteurs             # annuaire + fiches
python ammc_scraper.py --db ammc.db --step documents              # catalogue des documents
python ammc_scraper.py --db ammc.db --step extraire --max-new 20  # téléchargement + extraction
```

- **emetteurs** : parcourt `/fr/espace-emetteurs/liste-des-emetteurs`
  (nom, secteur d'activité, date d'introduction en bourse) et, avec
  `--with-details`, la fiche « Caractéristiques » de chaque émetteur
  (compartiment, commissaires aux comptes, actionnariat...).
- **documents** : parcourt le catalogue complet
  `/fr/liste-etats-financiers-emetteurs` (émetteur, année, type de
  rapport) et journalise chaque document dans la table `documents`
  (statut `a_traiter`). Ce catalogue représente **plusieurs milliers de
  documents** — la commande ne fait qu'un inventaire, elle ne télécharge
  rien.
- **extraire** : pour les documents `a_traiter`, résout le lien PDF
  réel, le télécharge (avec reprise — les gros rapports coupent souvent
  en cours de route), puis tente d'en extraire les rubriques Bilan /
  CPC / ESG par repérage des intitulés du plan comptable marocain
  (CGNC) et extraction de tableaux. C'est un **best effort** : les
  rapports sont mis en page librement par chaque émetteur, donc
  l'extraction ne fonctionne pas à tous les coups. Les documents où
  aucun tableau exploitable n'a été trouvé sont journalisés dans
  `documents_non_extraits` plutôt que de produire des chiffres
  incertains ; les PDF ne sont pas conservés sur disque après
  traitement (plusieurs Mo chacun).

Schéma :
- `emetteurs` : une ligne par émetteur (`id_ammc`), nom, secteur, date
  d'introduction en bourse.
- `emetteurs_details` : paires clé/valeur de la fiche « Caractéristiques »
  (compartiment, commissaires aux comptes, actionnariat...).
- `documents` : catalogue des documents financiers publiés (émetteur,
  année, type de rapport, lien PDF résolu, statut).
- `etats_financiers_ammc` : rubriques extraites des PDF (`etat`,
  `rubrique`, `valeur`, `valeur_num`, page source).
- `documents_non_extraits` : journal des documents pour lesquels
  l'extraction automatique a échoué (à traiter manuellement si besoin).

Étant donné le volume (~6000 documents, plusieurs dizaines de Go de
PDF), ce script est prévu pour être **relancé plusieurs fois** avec
`--max-new`, pas pour tout collecter en un seul run. Aucune
automatisation GitHub Actions n'est mise en place pour l'instant —
lancement manuel uniquement.
