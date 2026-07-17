---
title: "Matrice régime ICPE × type de suites"
question: "Est-ce que les installations en Autorisation sont plus souvent sanctionnées que celles en Enregistrement ou Déclaration ?"
caveat: "Les installations en Déclaration sont rarement inspectées publiquement, donc leur absence dans les suites lourdes ne signifie pas absence de problème."
---

```sql
SELECT
  COALESCE(regime_icpe, '(non renseigné)') AS regime,
  COALESCE(type_suite, '(non parsé)') AS suite,
  COUNT(*) AS nb_fiches
FROM 'fiches.parquet'
WHERE fiche_num IS NOT NULL
GROUP BY regime, suite
ORDER BY regime, nb_fiches DESC
```

## Pourquoi cet angle ?

Croiser le régime ICPE (Autorisation, Enregistrement, Déclaration) avec le type de suites proposées révèle si la DREAL réserve les sanctions lourdes aux gros sites ou si les petites installations subissent aussi des mises en demeure. C'est un indicateur d'équité du contrôle.
