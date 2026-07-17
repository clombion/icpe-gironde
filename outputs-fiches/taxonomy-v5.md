# Taxonomie 6 axes v5 — ICPE Gironde

## Instructions pour le classifieur

### Règles générales
1. Classifier les axes 2 (Mécanisme) et 4b (Stade) sur la section "Constats body:" UNIQUEMENT — ignorer le bloc "Prescription:" qui est du boilerplate réglementaire.
2. Un même fiche peut avoir PLUSIEURS codes sur les axes 1 et 2 (multi-label). Les axes 3-6 sont mono-label.
3. Quand le corps du constat est très court (<3 phrases), vérifier si le champ "Demande à formuler" est vide/tiret — si oui, c'est un signal fort de conformité (M08).

### Heuristiques de détection M (mécanisme) — lire dans cet ordre de priorité
1. **M08_CONFORMITE** — chercher EN PREMIER. Signaux:
   - "conforme", "respecté", "aucune remarque", "n'appelle aucun commentaire", "sans observation"
   - Négatif de non-conformité: "n'a pas constaté d'écart", "pas de constat d'écart", "aucun écart relevé"
   - Champ "Demande" vide, tiret, ou absent
   - NC résolue: le texte mentionne un ancien problème PUIS sa résolution ("Constat du jour: ... a pu être consulté", "a été mis en conformité")
   - Liste d'équipements avec dates de contrôle à jour
   - Descriptions opérationnelles sans NC ni demande
2. **M14_CONSTAT_TECHNIQUE** — NC physique observée sur site:
   - Certitude: "dégradé", "hors service", "fissuré", "absent", "non conforme" + élément physique
   - Incertitude: "n'a pas pu préciser", "n'est pas démontré", "susceptible de constituer un écart", "sans pouvoir confirmer"
   - Indirect: rapport tiers qui ne couvre pas un point, inspecteur comble la lacune
3. **M01_DOCUMENTATION** — absence OU obsolescence de document:
   - Absence: "ne dispose pas de", "n'a pas été en mesure de fournir", "n'a pas pu être consulté"
   - Obsolescence: "mise à jour nécessaire", "nécessite une mise à jour", "n'a pas été mis à jour depuis"
   - Documents non-standards: registre des plaintes, plan général des stockages, cahier d'épandage
4. **M07_CONTROLE** — vérifications périodiques (y compris non-standards):
   - Standards: "contrôle périodique", "vérification annuelle", "organisme agréé"
   - Non-standards: "autorisation de travail", "MMR", "GMAO", "contrôle d'entretien", "bon état de fonctionnement", "permis d'intervention"
5. **M17_CLASSIFICATION** — détermination du statut ICPE:
   - Seuils: "seuil de classement", "rubrique applicable", "relève de", "ne relève pas de"
   - Positionnement: "se positionner vis-à-vis de la rubrique", "vérifier l'applicabilité"
   - Résultat: "sous le seuil", "au-dessus du seuil", "non classé ICPE"
6. **M19_CESSATION** — obligations post-cessation:
   - "cessation d'activité", "R512-39-1", "mise en sécurité du site", "site à l'abandon"
   - "ATTES SECUR", "attestation de mise en sécurité", "mémoire du site"
7. Autres M codes selon signaux habituels
8. **M15_AUTRE** — EN DERNIER RECOURS seulement, si aucun M01-M14/M17/M19 ne s'applique

### Heuristiques de détection G (gravité)
- G3 AVANT G2: si le corps mentionne un équipement "hors service", "dégradé", "non fonctionnel", "fissuré", une distance/hauteur non respectée, un dispositif absent → G3 minimum
- G4 si MED active non levée + NC technique concomitante
- G2 uniquement si la NC est PUREMENT documentaire/formelle sans dimension physique

---

## Axe 1 — Domaine technique (16 + AUTRE)

### Risques accidentels
- D01_INCENDIE: Incendie, désenfumage, détection, POI, RIA, exercices, moyens de lutte, mousse, sprinklage
- D02_ATEX: Atmosphères explosives, zonage ATEX, DRPCE, certification matériels, ventilation zones explosives
- D03_SEVESO: Risque majeur, étude de dangers, effets domino, MMR/SIL, SMS, PPRT, zones d'effet

### Risques chroniques
- D04_EAUX: Rejets aqueux, eaux pluviales, eaux souterraines, piézomètres, MES/DCO/DBO5, milieu récepteur, prélèvements, forages, police de l'eau, PFAS dans les eaux
- D05_AIR_BRUIT: Rejets atmosphériques, poussières, COV, solvants, bruit, odeurs, GES/ETS/quotas carbone, dioxines/furanes
- D06_SOLS: Pollution sols, contamination historique, diagnostic, réhabilitation, ALUR, CASIAS, SUP, PFAS dans les sols
- D07_RETENTION: Rétention, confinement liquides, cuves, stockage produits dangereux, séparateurs hydrocarbures
- D08_DECHETS: Registre déchets, BSD, DAP, VHU, DEEE, filières, éco-organisme, transfert transfrontalier, terres excavées/RNDTS
- D09_BIODIVERSITE: Espèces protégées, continuité écologique, ERC, chiroptères, défrichement, espèces invasives

### Installations et équipements
- D10_ELECTRIQUE_ESP: Installations électriques, vérification, équipements sous pression (chaudières, réservoirs, compresseurs), AM 20/11/2017, protection foudre
- D11_SECURITE: Sécurité des personnes, clôture, accès, signalisation, formation, EPI, co-activité/plan de prévention, consignes d'urgence

### Administration et procédure
- D12_ADMIN: Situation administrative, nomenclature ICPE, déclaration, classement, régime, PAC, changement exploitant, antériorité
- D13_CESSATION: Cessation d'activité, sites abandonnés/orphelins, liquidation, mise en sécurité post-fermeture, déclassement, urbanisme post-ICPE

### Spécialisés
- D14_RISQUE_BIO: Légionelles, TAR, sous-produits animaux, risque biologique
- D15_SECTEUR: Sous-domaines sectoriels. Sous-tags: methanisation, carrieres, elevage, pressing, froid_frigorigenes, amiante, photovoltaique, agroalimentaire, portuaire, pyrotechnique, cogeneration, entrepot_logistique, pisciculture, silo_cereales
- D16_AUTRE: Domaine ne rentrant dans aucune catégorie

## Axe 2 — Mécanisme réglementaire (16 + modificateurs + AUTRE)

### Constat et preuve
- M01_DOCUMENTATION: Absence OU obsolescence de preuve documentaire, registres, attestations, plans, transmission. Inclut documents non-standards (registre plaintes, plan stockages).
- M02_MESURE: Qualité méthodologique — protocole inadéquat, représentativité, fréquence, paramètres non mesurés, délai réglementaire de prélèvement non respecté
- M03_REPORTING: Reporting numérique — GEREP, Trackdéchets, GIDAF

### Observation terrain
- M14_CONSTAT_TECHNIQUE: NC technique directement observée sur site — état physique dégradé, distances non respectées, équipement absent/hors service/inopérant, stockage non conforme. Inclut les constats d'incertitude ("n'a pas pu préciser/démontrer").

### Enforcement
- M04_MISE_EN_DEMEURE: APMD, levée, non-respect
- M05_SANCTION: Astreinte, amende, consignation, liquidation, travaux d'office, PV
- M06_RECIDIVE: Même NC entre inspections successives

### Vérification
- M07_CONTROLE: Contrôles périodiques, vérification organisme agréé, contre-visite, autosurveillance, thermographie, revue MMR, revue GMAO, autorisation de travail
- M08_CONFORMITE: Conformité constatée, levée de MED, validation positive, NC résolue, absence d'écart, "aucune remarque"

### Procédure administrative
- M09_PAC: Porter-à-connaissance, notification modification, étude de dangers, arrêté complémentaire, PCA
- M10_GARANTIES: Garanties financières, cautionnement
- M11_INCIDENT: Déclaration incident/accident, rapport circonstancié, perte de MMR, REX
- M12_IED_MTD: MTD, BREF, NEA-MTD, OTNOC, réexamen/bilan décennal

### Classification et cessation (NEW v5)
- M17_CLASSIFICATION: Vérification de seuil/classement ICPE, détermination de rubrique applicable, positionnement vis-à-vis d'un régime, non-classement
- M19_CESSATION: Obligations post-cessation R512-39-1, mise en sécurité site abandonné, attestation ATTES SECUR, mémoire du site

### Modificateurs
- m_DELAI: Délai correctif imposé. Accompagne un autre M.
- m_MENACE: Avertissement conditionnel. Précurseur de M04.
- M15_AUTRE: EN DERNIER RECOURS — uniquement si aucun M01-M14/M17/M19 ne s'applique après vérification

## Axe 3 — Dynamique relationnelle (12 + neutre)
R13/R14 est le DÉFAUT. N'assigner R01-R12 que sur signal clair.

### Comportement exploitant
- R01_PROACTIF: Correction spontanée, audit auto-commandé, anticipation
- R02_PROMESSE: Engagement verbal/écrit sans preuve. Signaux: "l'exploitant a indiqué/précisé/s'est engagé à" + action FUTURE non vérifiable au moment de l'inspection. NE PAS confondre avec un compte-rendu factuel ("l'exploitant a précisé que la rubrique est 2345" = factuel = R13/R14).
- R03_FACADE: Conformité apparente mais inopérante — installé mais pas fonctionnel, registre tenu mais faux
- R04_MECONNAISSANCE: Exploitant invoque ignorance

### Vides et blocages
- R05_VIDE_FACTUEL: Exploitant physiquement absent
- R06_VIDE_JURIDIQUE: Dissolution, liquidation, procédure collective
- R07_CONTRAINTE_TECHNIQUE: Impossibilité technique
- R08_CONTRAINTE_ECONOMIQUE: Coût chiffré comme frein
- R09_BLOCAGE_TIERS: Tiers bloquant — bailleur, éco-organisme, outil admin défaillant

### Tension et pouvoir
- R10_TENSION: Conflit données, diagnostic contesté
- R11_PENAL: PV procureur, menaces, exploitation illégale
- R12_PRAGMATISME: Souplesse inspection, mesure compensatoire acceptée

### Neutre (défaut)
- R13_NEUTRE_CONFORME: Conformité factuelle
- R14_NEUTRE_NC: NC factuelle sans dynamique
- R15_AUTRE: Inclassable

## Axe 4 — Acteurs et stade procédural

### 4a — Acteur principal
- A01_EXPLOITANT, A02_INSPECTION, A03_PREFET, A04_SDIS, A05_ORGANISME, A06_BET, A07_ECO_ORGANISME, A08_COLLECTIVITE, A09_ADEME, A10_JUSTICE, A11_PROPRIETAIRE, A12_TIERS, A13_AUTRE

### 4b — Stade procédural (classifier sur constats_body)
- S01_CONSTAT: Observation terrain
- S02_INJONCTION: Demande formelle avec délai
- S03_CORRECTION: Action corrective en cours/réalisée
- S04_VERIFICATION: Contre-visite, vérification post-correction
- S05_ESCALADE: Passage au niveau coercitif supérieur
- S06_CLOTURE: Levée MED, fin procédure
- S07_AUTRE: Inclassable

## Axe 5 — Gravité

- G1_OBSERVATION: Conforme, aucun enjeu
- G2_ECART_MINEUR: NC PUREMENT documentaire/formelle sans dimension physique
- G3_ECART_SIGNIFICATIF: NC technique avec risque potentiel. Signaux: "hors service", "dégradé", "non fonctionnel", "fissuré", "non réalisé" (contrôle/mesure), distance/hauteur violée, dispositif absent
- G4_RISQUE_AVERE: Danger identifié, action rapide nécessaire. MED active + NC technique, dépassement VLE confirmé, stockage dangereux sans rétention
- G5_URGENCE: Risque imminent. Délai 48h, pollution en cours, perte de MMR
- G6_INCIDENT: Événement survenu. Incendie, déversement, mortalité piscicole

## Axe 6 — Trajectoire temporelle

T1 est le DÉFAUT quand aucun historique mentionné.

- T1_PREMIER: Pas d'historique mentionné
- T2_SUIVI: Suivi explicite constat antérieur
- T3_AMELIORATION: Correction constatée depuis dernière inspection
- T4_STAGNATION: Même constat, aucune évolution
- T5_AGGRAVATION: Situation empire
- T6_ACCUMULATION: Nouvelles NC + anciennes non résolues. Signaux: "en outre", "s'ajoute", "de plus... alors que... n'a toujours pas"
- T7_CHRONIQUE: Persistance >3 ans ou >2 inspections. Signaux: "depuis 2016", "jamais depuis 1999"
