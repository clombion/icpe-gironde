# Méthodologie — Carte interactive des ICPE en Gironde

Ce document décrit la chaîne de production des données affichées par la
carte interactive, et la méthodologie de l'audit des coordonnées mené
avant l'enquête.

---

## 1. Données sources

### 1.1 Export officiel Géorisques (source principale)

La source principale est l'**export officiel** publié par l'API
Géorisques du Ministère de la Transition Écologique :

- **Adresse** : `GET /api/v1/csv/installations_classees?departement=33`
- **Date de téléchargement** : 8 avril 2026
- **Format** : archive ZIP contenant 5 fichiers (encodage converti en
  UTF-8 à l'extraction)
- **Contenu** :
  - `InstallationClassee.csv` — 2 890 installations (table principale)
  - `inspection.csv` — historique d'inspection
  - `rubriqueIC.csv` — rubriques ICPE par installation
  - `metadataFichierInspection.csv` — métadonnées des rapports publiables
  - `metadataFichierHorsInspection.csv` — métadonnées des documents non publiables

Le script `scripts/fetch_georisques.py` automatise le téléchargement,
l'archivage dans `données-georisques/raw/`, la conversion en UTF-8, et
la comparaison avec la source historique.

### 1.2 Snapshot historique data.gouv.fr (référence secondaire)

Un export plus ancien (février 2025) est conservé dans
`carte/liste-icpe-gironde.csv` à titre de comparaison temporelle. Il
liste 2 888 installations.

La comparaison entre les deux sources montre :

| Source | Installations |
|---|---:|
| Export officiel (avril 2026) | 2 890 |
| Snapshot historique (février 2025) | 2 888 |
| En commun | 2 886 |
| Ajoutées depuis février 2025 | 4 |
| Retirées depuis février 2025 | 2 |

Les 4 ajouts correspondent à des créations ou réinscriptions récentes.
Les 2 retraits sont une radiation administrative (SEMOCTOM) et une
entrée fantôme filtrée de l'export public (PESA). La différence est
purement temporelle et attendue.

### 1.3 Réserves naturelles (IGN Géoplateforme)

Les polygones des réserves naturelles sont téléchargés via le service
WFS de la Géoplateforme de l'IGN :

- **RNN** (Réserves Naturelles Nationales) : 9 réserves en Gironde
- **RNR** (Réserves Naturelles Régionales) : 0 réserve en Gironde

### 1.4 Communes et EPCI (geo.api.gouv.fr)

Les contours communaux et les rattachements aux intercommunalités
proviennent de l'API géographique d'Etalab :

- Communes : nom, code INSEE, code EPCI
- EPCI : nom, code SIREN

Le résultat est mis en cache localement pour permettre les exécutions
sans connexion internet.

### 1.5 Rapports d'inspection (Géorisques)

Les rapports d'inspection publiables sont téléchargés depuis Géorisques
par le script `scripts/telecharger_rapports_inspection.py`.
Le téléchargement est progressif (les fichiers déjà présents ne sont pas
retéléchargés) et les erreurs définitives (rapport supprimé côté
Géorisques) sont mémorisées pour ne pas relancer de requêtes inutiles.

---

## 2. Enrichissement des données

Le script `scripts/enrichir_libelles.py` transforme l'export brut en
un fichier CSV exploitable par la carte. Il produit deux fichiers :

1. **`données-georisques/InstallationClassee_enrichi.csv`** — l'export
   enrichi (2 890 lignes)
2. **`carte/data/liste-icpe-gironde_enrichi.csv`** — le fichier final
   consommé par la carte (2 890 lignes)

### 2.1 Source de référence

La carte est construite à partir de l'**export officiel** (2 890 lignes).
Pour chaque installation, deux informations sont récupérées depuis le
snapshot historique quand elles existent : la date de création et
l'identifiant historique. Le croisement se fait par l'identifiant ICPE,
commun aux deux fichiers.

Les 4 installations présentes uniquement dans l'export officiel (pas
dans le snapshot) reçoivent des valeurs vides pour ces deux champs.

### 2.2 Standardisation des catégories

Les valeurs textuelles de l'export Géorisques sont standardisées pour
correspondre aux filtres de la carte :

**Régime ICPE :**

| Dans Géorisques | Dans la carte |
|---|---|
| Autorisation | AUTORISATION |
| Enregistrement | ENREGISTREMENT |
| Autres régimes | AUTRE |
| Non ICPE | NON_ICPE |

**Statut Seveso :**

| Dans Géorisques | Dans la carte |
|---|---|
| Non Seveso | NON_SEVESO |
| Seveso seuil bas | SEUIL_BAS |
| Seveso seuil haut | SEUIL_HAUT |
| *(vide)* | *(vide)* |

Les colonnes oui/non (`prioriteNationale`, `ied`, etc.) sont converties
de `"true"`/`"false"` en `"TRUE"`/`"FALSE"`.

Si une valeur inconnue apparaît dans un futur export (par exemple un
nouveau régime ICPE), le script émet un avertissement plutôt que de
laisser passer la valeur brute silencieusement.

### 2.3 Désambiguïsation des noms

L'export Géorisques contient des noms en doublon : par exemple, 22
entrées s'appellent « BORDEAUX METROPOLE » pour des sites différents.
Le script produit un **nom complet unique** pour chaque installation
en deux étapes :

1. **Séparation** : quand un nom contient un tiret long (ex. « SAFRAN —
   Saint-Médard-en-Jalles »), il est décomposé en nom de structure
   (« SAFRAN ») et nom d'établissement (« Saint-Médard-en-Jalles »).

2. **Désambiguïsation progressive** : pour les noms encore identiques,
   la commune puis l'adresse sont ajoutées au nom jusqu'à ce que chaque
   ligne soit unique. En dernier recours, un numéro est ajouté
   (« #1, #2, … »).

### 2.4 Ajout de la commune et de l'intercommunalité

Pour chaque installation, le code INSEE est utilisé pour ajouter :

- le nom normalisé de la commune (source : IGN via geo.api.gouv.fr)
- le nom et le code SIREN de l'intercommunalité (EPCI)

### 2.5 Coordonnées géographiques

Les colonnes `longitude` et `latitude` de l'export sont combinées en
deux formats utilisés par la carte :

- `coordonnees_lat_lon` : texte `"latitude, longitude"`
- `geometrie_geojson` : format GeoJSON Point
  `{"type": "Point", "coordinates": [longitude, latitude]}`

---

## 3. Audit des coordonnées

### 3.1 Objectif

Pour chaque installation, Géorisques fournit à la fois une **adresse
postale** et des **coordonnées géographiques** (latitude/longitude).
Quand les deux ne concordent pas, la position du site sur la carte peut
être trompeuse — en particulier quand l'écart fait basculer la réponse
à la question : « ce site est-il dans une réserve naturelle ? ».

L'audit vérifie systématiquement la cohérence entre adresse et
coordonnées pour les 2 890 installations, en croisant cinq signaux
complémentaires.

### 3.2 Les cinq vérifications

Le script `scripts/audit_coordinates.py` exécute cinq vérifications
dans l'ordre. Chaque vérification ajoute de nouvelles colonnes
d'information à chaque installation.

#### Vérification 1 — Anomalies évidentes (sans internet)

Détecte les problèmes structurels sans aucun appel réseau :

| Signal | Ce qu'on vérifie | Ce que ça signifie |
|---|---|---|
| Coordonnées à (0, 0) | Les deux coordonnées sont nulles ou manquantes | Données invalides |
| Hors Gironde | Le point est en dehors du département | Géolocalisation fausse |
| Au centre de la commune | Le point est à moins de 50 m du centre géographique de la commune | Signe qu'un outil a placé le point au centre faute de trouver l'adresse |
| Coordonnées en double | 3 sites ou plus partagent les mêmes coordonnées | Coordonnées copiées ou attribuées par défaut |

#### Vérification 2 — Le point est-il dans la bonne commune ?

On teste si le point de l'installation tombe géographiquement à
l'intérieur du contour de la commune qu'elle déclare, en utilisant les
contours communaux de l'IGN :

| Résultat | Signification |
|---|---|
| Oui | Le point est bien dans la bonne commune |
| Non | Le point est en Gironde mais dans une autre commune |
| Inconnu | Le contour de la commune n'est pas disponible |

#### Vérification 3 — Géocodage de l'adresse (adresse → coordonnées)

**Principe** : on envoie l'adresse postale de chaque installation à un
service de géocodage pour obtenir des coordonnées indépendantes, puis on
mesure la distance entre ces coordonnées et celles de Géorisques. Si
la distance est grande, les coordonnées de Géorisques sont probablement
fausses.

Le géocodage utilise trois services en cascade (si le premier ne trouve
pas, on essaie le suivant) :

**Service 1 — BAN** (Base Adresse Nationale, service public français) :

L'adresse est soumise sous trois formes successives pour maximiser les
chances de résolution :

| Forme | Exemple |
|---|---|
| Champ adresse1 seul | « 71 chemin Bord Eau » |
| Champ adresse2 seul | « Zone Industrielle Nord » |
| Les deux combinés | « 71 chemin Bord Eau Zone Industrielle Nord » |

La première forme qui donne un résultat suffisamment fiable l'emporte.

**Résultat** : la BAN a résolu **2 175 adresses** (75,3 %).

**Service 2 — OpenCage** (agrégateur mondial) :

Pour les 715 adresses que la BAN n'a pas résolues, le service OpenCage
(qui s'appuie sur OpenStreetMap et d'autres sources) est interrogé.

**Résultat** : OpenCage a résolu **714 adresses** supplémentaires.

**Service 3 — Nominatim** (OpenStreetMap) :

Dernière tentative pour les résultats encore imprécis d'OpenCage
(résolution au niveau de la commune seulement, pas de la rue).

**Résultat** : Nominatim a amélioré **1 résolution**.

**Bilan du géocodage** : 100 % de couverture (2 890 / 2 890), avec
des niveaux de précision variables :

| Précision | Nombre | Part |
|---|---:|---:|
| Numéro de rue | 842 | 29,1 % |
| Rue (sans numéro) | 1 531 | 53,0 % |
| Quartier / lieu-dit | 332 | 11,5 % |
| Commune (centre) | 185 | 6,4 % |

#### Vérification 4 — Géocodage inverse (coordonnées → adresse)

**Principe** : on envoie les coordonnées de Géorisques à la BAN pour
obtenir l'adresse qui se trouve à cet endroit. L'objectif est de
vérifier la cohérence dans l'autre sens : « qu'y a-t-il au point
enregistré ? ».

Ce résultat sert à deux choses :

1. **Détection d'erreur de commune** : si l'adresse retournée est dans
   une autre commune que celle déclarée, c'est un signal fort que les
   coordonnées sont fausses.

2. **Confirmation** : quand le géocodage de l'adresse a échoué, si le
   géocodage inverse confirme la bonne commune, c'est un signal positif
   que les coordonnées sont au moins dans le bon secteur.

**Couverture** : résultat obtenu pour **2 544 sites** (88 %).

#### Vérification 5 — Proximité des réserves naturelles

Pour chaque site, le script teste si le point de Géorisques ET le point
géocodé sont à l'intérieur d'une réserve naturelle.

Quatre signaux sont produits :

| Signal | Signification |
|---|---|
| Point Géorisques dans une réserve | Nom de la réserve, ou « aucune » |
| Point géocodé dans une réserve | Nom de la réserve, ou « aucune » |
| Désaccord | Les deux points ne sont pas d'accord sur l'appartenance |
| Proximité de limite | Un des deux points est à moins de 200 m d'une limite de réserve |

Le cas critique est le **désaccord** : le point Géorisques dit que le
site est dans une réserve mais le géocodage dit le contraire (ou
inversement). Ces cas nécessitent une vérification humaine prioritaire.

### 3.3 Classification

Après les cinq vérifications, chaque site reçoit une **classe** :

| Classe | Signification |
|---|---|
| `ok` | Coordonnées cohérentes avec l'adresse (distance < 25 m) |
| `minor` | Petit écart (25 à 100 m) |
| `suspicious` | Écart à vérifier (100 à 500 m) |
| `severe` | Écart important (500 m à 2 km) |
| `very_severe` | Écart très important (> 2 km) |
| `wrong_commune` | Le point est dans la mauvaise commune |
| `outside_gironde` | Le point est hors du département |
| `address_imprecise` | L'adresse n'a été résolue qu'au niveau de la commune |
| `address_unresolvable_commune_ok` | Adresse non trouvée mais commune confirmée |
| `address_unresolvable_isolated` | Aucun signal exploitable |
| `null_island` | Coordonnées à (0, 0) |

### 3.4 Résultats de l'audit (8 avril 2026)

| Classe | Effectif |
|---|---:|
| ok | 169 |
| minor | 323 |
| suspicious | 530 |
| severe | 584 |
| very_severe | 377 |
| outside_gironde | 8 |
| wrong_commune | 59 |
| address_unresolvable_commune_ok | 315 |
| address_unresolvable_isolated | 57 |
| address_imprecise | 468 |
| null_island | 0 |
| **Total** | **2 890** |

**Interprétation** : seuls 169 sites (5,8 %) ont des coordonnées
parfaitement cohérentes avec leur adresse. Pour la majorité des sites,
un écart mesurable existe entre l'adresse et la position enregistrée.

Cela ne signifie pas nécessairement que les coordonnées sont fausses :
un écart de quelques centaines de mètres peut refléter le fait que le
service de géocodage a trouvé la rue mais pas le numéro, ou que
l'installation est éloignée de la rue (cas fréquent pour les carrières,
les éoliennes et les installations en zone industrielle).

### 3.5 Groupes de revue

Les sites sont répartis en trois groupes pour la revue collaborative :

| Groupe | Critère | Effectif | Priorité |
|---|---|---:|---|
| **Réserves** | Désaccord sur l'appartenance à une réserve, ou point à moins de 200 m d'une limite | 1 | Critique |
| **Grands écarts** | Classes `very_severe`, `severe`, `outside_gironde`, `wrong_commune`, `address_imprecise`, `address_unresolvable_isolated` | 1 552 | Haute |
| **Petits écarts** | Classes `suspicious`, `minor`, `address_unresolvable_commune_ok` | 1 168 | Basse |
| *(non signalé)* | Classe `ok`, pas de signal réserve | 169 | — |

---

## 4. Outil de revue collaborative

### 4.1 Principe

L'outil de revue (`/audit/`) permet à plusieurs enquêteurs de vérifier
les écarts en parallèle. Les sites à vérifier sont découpés en
**paquets de 25 sites**. Chaque enquêteur prend un paquet, examine
chaque site, et enregistre son verdict.

### 4.2 Ce que voit l'enquêteur

Pour chaque site, l'outil affiche :

1. **Identité** : nom complet, SIRET, régime ICPE, statut Seveso, lien
   vers la fiche Géorisques
2. **Coordonnées enregistrées** : l'adresse et les coordonnées telles
   qu'elles figurent dans Géorisques
3. **Adresse géocodée** : le résultat de la vérification 3 (adresse →
   coordonnées), avec le niveau de précision et la distance par rapport
   aux coordonnées enregistrées
4. **Adresse au point enregistré** : le résultat de la vérification 4
   (coordonnées → adresse) — « qu'y a-t-il à cet endroit ? »
5. **Signaux d'audit** : la classe attribuée et les éventuels signaux
   de réserve naturelle

Une **mini-carte** affiche les deux points (coordonnées Géorisques en
rouge, coordonnées géocodées en bleu) avec un trait matérialisant la
distance. Deux fonds de carte sont disponibles : plan et
orthophotographie (IGN).

### 4.3 Cas de figure

#### Cas 1 — Écart significatif, les deux géocodages ont fonctionné

L'enquêteur voit les deux points sur la carte et la distance entre eux.
L'adresse inverse lui dit « ce qui se trouve réellement à l'emplacement
enregistré ». Il décide quel point est correct.

#### Cas 2 — Géocodage trop imprécis (468 sites)

Le service de géocodage n'a résolu l'adresse qu'au niveau de la commune,
pas de la rue. Le point géocodé est donc le centre de la commune — la
distance affichée n'est pas significative.

L'enquêteur doit s'appuyer sur l'adresse inverse et sur la fiche
Géorisques pour évaluer si les coordonnées enregistrées sont
vraisemblables.

#### Cas 3 — Géocodage échoué, commune confirmée (315 sites)

Aucun service de géocodage n'a résolu l'adresse. En revanche, le
géocodage inverse confirme que les coordonnées enregistrées sont dans
la bonne commune. C'est un signal faible mais positif.

#### Cas 4 — Aucun signal automatique (57 sites)

Ni le géocodage de l'adresse ni le géocodage inverse n'ont produit
de résultat. L'enquêteur voit « *(non disponible)* » et doit investiguer
manuellement : consulter la fiche Géorisques, vérifier sur un moteur de
recherche cartographique, ou marquer le site pour une visite terrain.

#### Cas 5 — Mauvaise commune (59 sites)

Les coordonnées enregistrées pointent vers une commune différente de
celle déclarée. L'enquêteur doit déterminer si c'est la commune
déclarée qui est fausse, les coordonnées qui sont fausses, ou un
problème de limites communales (site en bordure).

### 4.4 Verdicts disponibles

| Verdict | Action | Quand l'utiliser |
|---|---|---|
| **Garder les coordonnées enregistrées** | Aucune correction | Les coordonnées Géorisques sont correctes |
| **Utiliser l'adresse géocodée** | Remplacer les coordonnées | L'adresse géocodée pointe au bon endroit |
| **Placer manuellement** | L'enquêteur place un point sur la carte | Ni les coordonnées ni le géocodage ne sont satisfaisants |
| **Visite terrain** | Reporter la décision | Vérification physique nécessaire |

L'enquêteur peut aussi cocher « Pertinent pour l'enquête » pour signaler
les cas d'intérêt journalistique particulier (par exemple un site Seveso
seuil haut dont les coordonnées le placent dans une réserve naturelle).

### 4.5 Collaboration

Les verdicts sont exportés sous forme de fichiers par paquet et
enregistrés dans le dépôt GitHub. L'outil découvre automatiquement les
fichiers enregistrés par les autres enquêteurs et met à jour la
progression.

---

## 5. La carte interactive

### 5.1 Données affichées

La carte (`/carte/`) affiche les 2 890 installations comme des points
sur une carte, regroupés automatiquement quand on dézoome.

### 5.2 Filtres disponibles

| Dimension | Valeurs |
|---|---|
| Régime ICPE | AUTORISATION, ENREGISTREMENT, NON_ICPE, AUTRE |
| Statut Seveso | SEUIL_HAUT, SEUIL_BAS, NON_SEVESO |
| Priorité nationale | oui / non |
| Directive IED | oui / non |
| Secteur d'activité | industrie, carrière, autre |
| Commune / EPCI | recherche textuelle |
| Période de création | filtre mensuel |

### 5.3 Couches cartographiques

- **Fond plan** : CartoDB Voyager
- **Orthophotographie** : IGN
- **Contour du département** : Gironde
- **Communes** : polygones avec opacité réglable
- **Intercommunalités (EPCI)** : contours calculés depuis les communes
- **Réserves naturelles** : polygones RNN + RNR

---

## 6. Reproductibilité

L'ensemble de la chaîne est reproductible et rejouable :

1. `scripts/fetch_georisques.py` — télécharge l'export officiel
2. `scripts/enrichir_libelles.py` — enrichit les données et produit le
   fichier de la carte
3. `scripts/audit_coordinates.py` — exécute les 5 vérifications de
   coordonnées (nécessite une clé API OpenCage pour le service 2)
4. `scripts/telecharger_rapports_inspection.py` — télécharge les
   rapports PDF

Chaque étape vérifie ses prérequis et écrit ses résultats de façon
sécurisée (écriture dans un fichier temporaire puis remplacement) pour
éviter les états intermédiaires en cas d'interruption.
