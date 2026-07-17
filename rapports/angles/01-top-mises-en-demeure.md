---
title: "Top 20 des installations avec le plus de mises en demeure"
question: "Quelles installations cumulent le plus de sanctions lourdes ?"
caveat: "Compte uniquement les rapports d'inspection publiés sur Géorisques. Les installations sans rapport public peuvent avoir été sanctionnées sans apparaître ici."
---

```sql
SELECT
  nom_complet,
  nom_commune,
  regime_icpe,
  categorie_seveso,
  COUNT(*) AS nb_mises_en_demeure,
  MIN(date_inspection) AS premiere_inspection,
  MAX(date_inspection) AS derniere_inspection
FROM 'fiches.parquet'
WHERE LOWER(proposition_suite) LIKE '%mise en demeure%'
  AND fiche_num IS NOT NULL
GROUP BY nom_complet, nom_commune, regime_icpe, categorie_seveso
ORDER BY nb_mises_en_demeure DESC
LIMIT 20
```

## Pourquoi cet angle ?

Les mises en demeure sont le signal le plus fiable d'une non-conformité chronique. Un exploitant mis en demeure a reçu un rappel formel de la DREAL, avec obligation de se conformer dans un délai. Cumuler plusieurs mises en demeure suggère que les corrections n'ont pas été faites ou que de nouvelles non-conformités émergent.

Note : la mention "mise en demeure" apparaît dans le champ `proposition_suite` (le texte détaillé de la proposition de suites), pas dans le champ `type_suite` (qui utilise le vocabulaire simplifié "Sans suite / Avec suites / Susceptible de suites").
