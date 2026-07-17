# Méthodologie — Rapports d'inspection

Cette section décrit comment les 10 599 fiches de constat que vous
explorez dans cet outil ont été produites à partir des rapports
d'inspection publiés sur Géorisques. Les sources de données, la
construction de la carte et l'audit des coordonnées sont documentés
dans la section suivante.

## 7. Téléchargement des rapports PDF

Le script `telecharger_rapports_inspection.py` télécharge
automatiquement les rapports d'inspection publiables depuis le site
Géorisques. Chaque PDF est renommé selon un format lisible :

```
{nom-du-site}_{identifiant}_{date}_{siret}.pdf
```

Par exemple : `SAFRAN-CERAMICS_3100223_2023-08-18_44051305900087.pdf`

Le téléchargement est progressif : si un fichier est déjà présent, il
n'est pas retéléchargé. Les erreurs définitives (rapport supprimé côté
Géorisques) sont mémorisées pour ne pas retenter.

**Bilan** : 1 784 rapports listés, 1 782 téléchargés, 1 en erreur
définitive (COREP), 2 fichiers vides (1 Ko et 3 Ko, probablement des
erreurs côté Géorisques).

## 8. Conversion des PDF en texte structuré

Le script `extract_rapports_markdown.py` lit chaque PDF et en
extrait le texte, qu'il convertit dans un format lisible (markdown)
avec un en-tête standardisé contenant les métadonnées du rapport.

### 8.1 Reconnaissance du type de rapport

Chaque PDF est classé automatiquement :

| Type | Part du corpus | Ce qui se passe |
|---|---:|---|
| **Gabarit DREAL standard** | 91,3 % (1 627 rapports) | Le texte suit le modèle officiel de la DREAL Nouvelle-Aquitaine. Le script reconnaît les sections (Contexte, Constats, Fiches de constat) et les découpe. |
| **Autre format** | 8,6 % (153 rapports) | Le texte est extrait tel quel mais sans découpage en sections (courriers, propositions de suites, rapports d'autres formats). |
| **Vides** | 0,1 % (2 rapports) | Fichiers source vides — le script note l'erreur et passe au suivant. |

### 8.2 Rapports scannés (sans texte numérique)

Environ 60 rapports étaient des scans (images sans texte sélectionnable).
Le script leur applique une reconnaissance optique de caractères (OCR)
avec le moteur Tesseract en français avant de les traiter. L'OCR est
fait une seule fois : les PDFs conservent ensuite leur couche texte.

La qualité de l'OCR dépend du scan original. Les rapports scannés
peuvent contenir des erreurs de reconnaissance (lettres confondues,
mots mal coupés).

### 8.3 Localisation dans le PDF source

Pour chaque fiche de constat, le script repère sa position exacte
dans le PDF : numéro de page et zone de la page (coordonnées du
rectangle contenant la fiche). C'est ce qui permet à l'outil de vous
montrer un extrait visuel du PDF au bon endroit.

**Couverture** : 98,9 % des fiches ont une position repérée. Pour les
1,1 % restantes (texte OCR trop dégradé), l'outil montre la page 1
du rapport par défaut.

### 8.4 Traçabilité

Chaque extraction est tracée dans un fichier journal (`_manifest.jsonl`)
qui enregistre l'empreinte numérique du PDF source et la version du
script. Si le même script est relancé, il ne ré-extrait que les PDFs
qui ont changé.

## 9. Construction du tableau de fiches

Le script `construire_fiches.py` lit les fiches extraites et les
assemble dans un tableau unique (`fiches.parquet`) — l'équivalent d'un
grand fichier Excel avec une ligne par fiche de constat.

### 9.1 Extraction des champs de chaque fiche

Dans le gabarit DREAL, chaque fiche contient des champs identifiés
par des intitulés fixes. Le script les repère et les sépare :

| Champ extrait | Intitulé dans le rapport | Taux d'extraction |
|---|---|---:|
| Référence réglementaire | « Référence réglementaire : » | 99,7 % |
| Thème | « Thème(s) : » | 99,8 % |
| Déjà contrôlé | « Point de contrôle déjà contrôlé : » | 99,6 % |
| Prescription | « Prescription contrôlée : » | 99,5 % |
| Constats | « Constats : » | 99,7 % |
| Type de suites | « Type de suites proposées : » | 99,8 % |
| Proposition de suites | « Proposition de suites : » | 99,4 % |

Les quelques fiches non extraites (~0,2 %) sont dues à des variations
de mise en forme dans le PDF source (espaces en trop, fautes de frappe
dans les intitulés).

### 9.2 Enrichissement par croisement

Chaque fiche est enrichie avec des informations provenant de deux
autres fichiers de données :

- **Depuis le tableau des rapports** (`rapports-inspection.csv`) :
  lien vers le PDF en ligne, lien vers la version texte, identifiant
  du fichier côté Géorisques

- **Depuis le tableau des installations** (`liste-icpe-gironde_enrichi.csv`) :
  commune, code INSEE, intercommunalité (EPCI), régime ICPE
  (Autorisation, Enregistrement…), catégorie Seveso

Le croisement se fait par l'identifiant de l'installation (code AIOT),
comme une recherche dans Excel avec `RECHERCHEV` sur une colonne commune.

### 9.3 Rapports sans fiches structurées

Les 153 rapports d'autre format et les 2 vides sont tout de même inclus
dans le tableau, chacun sur une seule ligne. Ils n'ont pas de champs
structurés (pas de thème, pas de type de suites) mais leur texte
complet est consultable via la recherche plein texte.

### 9.4 Vérification automatique

Chaque ligne du tableau est vérifiée automatiquement contre un modèle
strict (« schéma ») avant d'être écrite. Si une ligne est invalide, le
script s'arrête immédiatement et indique l'erreur — aucune donnée
incorrecte ne passe silencieusement.

### 9.5 Résultat final

| Métrique | Valeur |
|---|---:|
| Fiches structurées (gabarit DREAL) | 10 599 |
| Rapports sans fiches (autres formats + vides) | 393 |
| **Total de lignes dans le tableau** | **10 992** |
| Rapports avec au moins une fiche | 1 389 |
| Rapports sans fiche | 393 |

## 10. Limites — ce que ces données ne permettent pas de dire

1. **Tous les contrôles ne sont pas ici.** Seuls les rapports
   *publiables* sont sur Géorisques. Des inspections ont lieu sans
   rapport public : suivis informels, contrôles inopinés, procédures
   pénales confidentielles.

2. **Ce n'est pas comparable d'une région à l'autre.** Le modèle de
   rapport est propre à la DREAL Nouvelle-Aquitaine. Les autres régions
   utilisent des formats différents que notre outil ne sait pas lire.

3. **« Sans suite » ne veut pas dire « pas de problème ».** Le champ
   « Type de suites proposées » est un indicateur administratif.
   L'inspecteur peut avoir observé des manquements mineurs sans
   proposer de sanction. À l'inverse, « Mise en demeure » ne signifie
   pas « danger immédiat » — c'est un rappel formel à se conformer.

4. **L'OCR n'est pas parfait.** Les ~60 rapports scannés ont été
   convertis en texte par reconnaissance optique. La qualité dépend du
   scan original : certaines fiches peuvent contenir des erreurs
   (lettres confondues, mots mal coupés).

5. **Quelques fiches ont des champs manquants.** Environ 0,2 % des
   fiches n'ont pas tous les champs extraits, à cause de variations
   de mise en forme dans le PDF source (espaces en trop, intitulés
   mal orthographiés).

## 11. Reproduire le processus

Toute la chaîne de traitement est rejouable. Les commandes à lancer
dans l'ordre :

```
1. Télécharger l'export officiel Géorisques
2. Enrichir les libellés et les données communales
3. Télécharger les rapports d'inspection PDF
4. Convertir les PDF en texte structuré
5. Construire le tableau de fiches
6. (Optionnel) Reconstruire l'index des angles d'analyse
```

Chaque étape est idempotente : la relancer ne retélécharge et ne
ré-extrait que ce qui a changé.

Les scripts sont dans le dossier `scripts/` du dépôt. Les
dépendances logicielles (PyMuPDF, Tesseract, DuckDB) sont déclarées
dans chaque script et résolues automatiquement par l'outil `uv`.

## 12. Versions

| Composant | Version | Description |
|---|---|---|
| Extracteur de texte | 0.2.0 | Avec localisation des fiches dans le PDF |
| Constructeur du tableau | 0.1.0 | Tableau initial avec rapports non structurés inclus |
| Données Géorisques | Avril 2026 | Empreinte dans `données-georisques/PROVENANCE.txt` |

---

*Retour : [Vérifier](./) · [Analyser par angle](angles.html) · [Accueil](../)*
