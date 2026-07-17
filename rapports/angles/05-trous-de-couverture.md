---
title: "Trous de couverture : communes sans inspection récente"
question: "Quelles communes ont des ICPE actives mais aucun rapport d'inspection publié récent ?"
caveat: "L'absence de rapport public ne signifie pas absence d'inspection — les rapports peuvent ne pas être publiés sur Géorisques. C'est un indicateur de transparence, pas de contrôle."
---

```sql
WITH communes_icpe AS (
  SELECT
    nom_commune,
    COUNT(DISTINCT id_icpe) AS nb_installations
  FROM 'fiches.parquet'
  GROUP BY nom_commune
),
communes_recentes AS (
  SELECT
    nom_commune,
    MAX(date_inspection) AS derniere_inspection,
    COUNT(DISTINCT id_icpe) AS nb_installations_inspectees
  FROM 'fiches.parquet'
  WHERE date_inspection >= '2023-01-01'
    AND fiche_num IS NOT NULL
  GROUP BY nom_commune
)
SELECT
  c.nom_commune,
  c.nb_installations,
  COALESCE(r.nb_installations_inspectees, 0) AS inspectees_depuis_2023,
  COALESCE(r.derniere_inspection, '(aucune)') AS derniere_inspection
FROM communes_icpe c
LEFT JOIN communes_recentes r ON c.nom_commune = r.nom_commune
WHERE c.nom_commune IS NOT NULL
ORDER BY c.nb_installations DESC, inspectees_depuis_2023 ASC
LIMIT 30
```

## Pourquoi cet angle ?

Certaines communes concentrent de nombreuses ICPE (Ambès, Bassens, Blanquefort) sans rapports d'inspection récents publiés. Cet angle met en lumière les zones où la transparence documentaire est la plus faible par rapport à la densité industrielle.
