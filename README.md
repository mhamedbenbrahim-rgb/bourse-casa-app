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
