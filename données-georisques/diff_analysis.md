# Origine de la différence bulk vs CSV manuel

**Contexte** — Le CSV manuel (`carte/liste-icpe-gironde.csv`, daté
`cdate=2025-02-10`) a été téléchargé depuis une source ICPE en février 2025.
L'export bulk officiel (`InstallationClassee.csv`) a été récupéré aujourd'hui
(2026-04-08) via l'API Géorisques V1 (`/api/v1/csv/installations_classees?departement=33`).
Les deux couvrent la Gironde mais 14 mois séparent les deux snapshots.

## Chiffres

| Source | Installations |
|---|---:|
| Bulk Géorisques (2026-04-08) | 2 890 |
| CSV manuel (2025-02-10) | 2 888 |
| En commun | 2 886 |
| Uniquement bulk | 4 |
| Uniquement manuel | 2 |

Net : +2, soit la différence brute observée (2890 − 2888).

## Les 4 installations ajoutées depuis février 2025

Toutes absentes du CSV manuel, présentes dans le bulk — il s'agit de
créations ou ré-inscriptions postérieures au snapshot de février 2025.

| codeAiot | Raison sociale | Commune | Régime |
|---|---|---|---|
| 0003107089 | GARDIER Micheline (station-service) | Bègles | Non ICPE |
| 0100303610 | EUROVIA GRANDS PROJETS FRANCE | St Christoly de Blaye | Enregistrement |
| 0100310802 | VILELA | Cubzac les Ponts | Non ICPE |
| 0100311490 | KAN'MOOV | Macau | Non ICPE |

Observation : 3 sur 4 ont le régime `Non ICPE`. Le bulk inclut ce type
d'entrée (609 au total), qui correspond à des sites suivis par la DREAL
sans être formellement classés.

## Les 2 installations retirées depuis février 2025

Présentes dans le CSV manuel, absentes du bulk actuel.

### SEMOCTOM (ident 5207675, INSEE 33433)

- Régime dans le manuel : `AUTRE`.
- Appel direct à l'API détail : `results: 0`. L'installation **n'est plus du
  tout référencée** dans Géorisques. Suppression complète de la base entre
  février 2025 et avril 2026.
- Cause probable : retrait définitif (site fermé, radiation administrative).

### PESA (ident 53326672, Saint-Genis-du-Bois)

- Régime dans le manuel : `AUTRE`.
- Appel direct à l'API par `codeAIOT=0053326672` : **1 résultat**,
  l'installation existe encore dans la base détail.
- Recherche par `raisonSociale=PESA&departement=33` : **0 résultats**.
- L'entrée possède `etatActivite: null`, `rubriques: []`,
  `documentsHorsInspection: []`, et une seule inspection de 2015 sans
  fichier joint. `date_maj: 2023-11-09`.
- Cause probable : entrée fantôme. Géorisques la conserve en base mais la
  filtre des recherches par département et de l'export bulk — probablement
  parce qu'elle n'a plus de rubriques actives et que sa dernière mise à jour
  date de fin 2023. Le CSV manuel de février 2025 avait capté l'entrée
  pendant sa dernière période de visibilité.

## Conclusion

La différence est **purement temporelle et attendue** : 14 mois séparent les
deux sources, durant lesquels 4 installations ont été ajoutées et 2 ont été
retirées de l'export public pour la Gironde. Aucune divergence n'indique un
problème de qualité ou de complétude des données.

**Recommandation** : utiliser le bulk comme source de référence canonique
pour l'enquête. Si SEMOCTOM ou PESA sont des sujets d'investigation, leurs
traces historiques restent accessibles dans le CSV manuel et, pour PESA,
via l'API détail par `codeAIOT`.

## Note sur les libellés de régime

Les deux sources encodent différemment le régime :

| Bulk | CSV manuel |
|---|---|
| `Autorisation` | `AUTORISATION` |
| `Enregistrement` | `ENREGISTREMENT` |
| `Autres régimes` | `AUTRE` |
| `Non ICPE` | `NON_ICPE` |

À normaliser si l'on veut croiser les deux sources.
