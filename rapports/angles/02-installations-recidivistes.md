---
title: "Installations récidivistes"
question: "Quelles installations ont les mêmes points de contrôle signalés comme 'déjà contrôlé' d'une inspection à l'autre ?"
caveat: "Le champ 'déjà contrôlé' est rempli par l'inspecteur ; il peut y avoir des oublis ou des reformulations entre deux inspections. La récidive est un signal, pas une preuve."
---

```sql
SELECT
  nom_complet,
  nom_commune,
  COUNT(*) AS nb_fiches_deja_controle,
  COUNT(DISTINCT date_inspection) AS nb_inspections_distinctes,
  MIN(date_inspection) AS premiere,
  MAX(date_inspection) AS derniere,
  STRING_AGG(DISTINCT type_suite, ', ') AS suites_proposees
FROM 'fiches.parquet'
WHERE deja_controle IS NOT NULL
  AND LOWER(deja_controle) NOT IN ('sans objet', '')
  AND fiche_num IS NOT NULL
GROUP BY nom_complet, nom_commune
HAVING COUNT(*) >= 3
ORDER BY nb_fiches_deja_controle DESC
LIMIT 30
```

## Pourquoi cet angle ?

Un point de contrôle marqué « Déjà contrôlé » (avec un contenu non vide, différent de « Sans Objet ») signifie que l'inspecteur savait que ce même sujet avait déjà été vérifié lors d'une précédente visite. Si un exploitant accumule ces signaux, il a du mal à résoudre ses non-conformités dans la durée. C'est un marqueur de résistance au changement.

Note : le champ `deja_controle` contient souvent un résumé des constats de la visite précédente (pas juste « Oui »/« Non »). Le filtre exclut « Sans Objet » et les valeurs vides.
