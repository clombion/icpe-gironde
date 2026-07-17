---
title: "Comparaison du taux de non-conformité par secteur"
question: "Quels thèmes d'inspection concentrent le plus de suites non-nulles ?"
caveat: "Les thèmes sont assignés par l'inspecteur depuis un vocabulaire semi-ouvert. Certains thèmes sont plus fréquemment inspectés que d'autres, ce qui biaise le taux brut."
---

```sql
SELECT
  COALESCE(theme, '(non parsé)') AS theme,
  COUNT(*) AS nb_fiches,
  SUM(CASE WHEN type_suite NOT LIKE '%Sans suite%' THEN 1 ELSE 0 END) AS nb_avec_suites,
  ROUND(100.0 * SUM(CASE WHEN type_suite NOT LIKE '%Sans suite%' THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_avec_suites
FROM 'fiches.parquet'
WHERE fiche_num IS NOT NULL
  AND type_suite IS NOT NULL
GROUP BY theme
HAVING COUNT(*) >= 10
ORDER BY pct_avec_suites DESC
```

## Pourquoi cet angle ?

Le taux de fiches « avec suites » (observation, mise en demeure, etc.) rapporté au total des fiches d'un thème donne une indication de la difficulté de conformité dans ce domaine. Un thème à 80 % de non-conformité est structurellement problématique.
