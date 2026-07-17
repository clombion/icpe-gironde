# Audit des coordonnées ICPE — 2026-04-08

**Total** : 2890 installations

## Seuils utilisés

- `minor_m` = 25.0
- `suspicious_m` = 100.0
- `severe_m` = 500.0
- `very_severe_m` = 2000.0
- `score_cutoff` = 0.4

## Compte par classe

| Classe | Effectif |
|---|---:|
| ok | 169 |
| minor | 323 |
| suspicious | 530 |
| severe | 584 |
| very_severe | 377 |
| null_island | 0 |
| outside_gironde | 8 |
| wrong_commune | 59 |
| address_unresolvable_commune_ok | 315 |
| address_unresolvable_isolated | 57 |
| address_imprecise | 468 |

## Compte par groupe de revue

| Groupe | Effectif |
|---|---:|
| reserves | 1 |
| grand | 1552 |
| petit | 1168 |
| (non flagué) | 169 |

## Histogramme des distances forward

| Bucket | Effectif |
|---|---:|
| 0-100 m | 588 |
| 100-200 m | 296 |
| 1000-1100 m | 55 |
| 1100-1200 m | 62 |
| 1200-1300 m | 72 |
| 1300-1400 m | 52 |
| 1400-1500 m | 45 |
| 1500-1600 m | 50 |
| 1600-1700 m | 53 |
| 1700-1800 m | 40 |
| 1800-1900 m | 35 |
| 1900-2000 m | 47 |
| 200-300 m | 174 |
| 300-400 m | 138 |
| 400-500 m | 120 |
| 500-600 m | 100 |
| 600-700 m | 88 |
| 700-800 m | 84 |
| 800-900 m | 87 |
| 900-1000 m | 81 |
| ≥2 km | 623 |

## Top 20 offenders (forward_distance_m)

| id_icpe | nom | distance (m) | regime | url |
|---|---|---:|---|---|
| 100033999 | EDF SEI GUYANE | 6725789 | Autorisation | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0100033999) |
| 5204861 | GERLAND ROUTES — Langon | 666296 | Enregistrement | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0005204861) |
| 5200851 | BEUGNET — Langon | 666296 | Enregistrement | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0005200851) |
| 5205829 | SNECMA-Mérignac | 569156 | Autorisation | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0005205829) |
| 5301335 | CARRIERES LEROUX PHILIPPE | 546690 | Autorisation | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0005301335) |
| 5200334 | PFA LOGISTIC SCI | 491557 | Enregistrement | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0005200334) |
| 5208474 | TOTAL MARKETING FRANCE — Mérignac — Rocade Ouest - A 630 | 369306 | Autres régimes | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0005208474) |
| 3106565 | PEAB — PIECES ENTRETIEN AUTO BORDELAIS | 357831 | Autres régimes | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0003106565) |
| 100296337 | JERREL | 278348 | Autres régimes | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0100296337) |
| 5201014 | GUYENNE ENROBES — Mérignac | 164722 | Enregistrement | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0005201014) |
| 6809286 | COOPERATIVE R2D2 SARL | 117692 | Autres régimes | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0006809286) |
| 5209136 | CENTRE COMMERCIAL RIVES ARCINS KLEPIERRE | 61002 | Non ICPE | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0005209136) |
| 5205890 | MEDOC PRIMEUR | 59464 | Autres régimes | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0005205890) |
| 5208197 | SOGARA FRANCE | 50552 | Enregistrement | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0005208197) |
| 5204781 | ISB | 50175 | Autorisation | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0005204781) |
| 5200913 | TOTAL MARKETING FRANCE — Lormont | 47848 | Autres régimes | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0005200913) |
| 5209387 | CAVIGNAC Denis | 42018 | Non ICPE | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0005209387) |
| 5200382 | VEOLIA — Unité Opérationnelle de Bègles | 34560 | Autorisation | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0005200382) |
| 5208824 | PRESSING JOHNSTON | 34028 | Autres régimes | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0005208824) |
| 100289649 | CHAPELLE ALEXANDRE | 25412 | Non ICPE | [fiche](https://www.georisques.gouv.fr/risques/installations/donnees/details/0100289649) |

## Cas reserve_ambiguous (top 10)

| id_icpe | nom | stored_in | geocoded_in |
|---|---|---|---|
| 5211496 | ALMA SCI | Périmètre De Protection De La Réserve Na | none |

## Méthodologie

Cinq passes de signaux : sentinelles offline, point-in-polygon commune, 
BAN forward (api-adresse.data.gouv.fr/search/csv/), BAN reverse, 
appartenance aux réserves naturelles.