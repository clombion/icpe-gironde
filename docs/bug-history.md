# Historique des bugs

Bugs et incidents significatifs rencontrés pendant le développement : cause racine, correction, et mesure de prévention. Les entrées servent aussi à renforcer les skills et les futurs projets utilisant la même méthodologie.

## Gabarit d'entrée

Format **BUG-NNN**. Chaque entrée a des sections structurées (Résumé, Cause racine, Correction, Prévention) pour rester cherchable et réutilisable.

Prochain ID : **BUG-007**

---

## BUG-001 — Quatre jours de travail non poussés, survivants par backup

**Sévérité** : Majeure | **Classe** : Ops | **Statut** : Corrigé

**Résumé** : À la mort de la machine (avril 2026), le dernier push remontait au 10 avril mais le travail local courait jusqu'au 14 : itérations de taxonomie, codebook, 81 batches Hunter et 33 batches final — des artefacts LLM payés. Tout n'a survécu que grâce au backup disque. Le `.git` était en plus un pointeur vers un dépôt parent (`ddj/.git/modules/semaine-enquete/...`) non restauré, rendant tout git inopérant.

**Cause racine** : Aucun rituel de commit/push pendant la campagne d'agents multi-jours ; dépendance à un dépôt parent fragile plutôt qu'un dépôt autonome.

**Correction** : Dépôt autonome `clombion/icpe-gironde` créé le 2026-07-17, snapshot du backup en premier commit, push immédiat.

**Prévention** : Règle « checkpoint-commit » ajoutée aux skills (`ai-llm-harness` → corollaire checkpoint-commit ; `data-thematic-analysis` → campagnes multi-rounds, exigence 5) : commiter et pousser les sorties LLM par round, pas en fin de campagne.

---

## BUG-002 — Batch 21 sauté silencieusement dans l'audit Skeptic

**Sévérité** : Majeure | **Classe** : Suivi d'état | **Statut** : Corrigé (détection)

**Résumé** : Les batches final couvraient 01–20 puis 22–30 : le batch 21 manquait au milieu d'une plage complète, invisible à l'œil parce que ses voisins existaient. Personne ne l'avait remarqué avant la reprise du projet.

**Cause racine** : La présence de fichiers (`ls` du dossier `tags/`) était le seul état de la campagne. Aucun outil ne vérifiait la complétude de la séquence numérotée.

**Correction** : `scripts/tag_status.py` — registre de couverture qui liste les batches manquants, vérifie l'unicité et la validité de chaque fichier.

**Prévention** : Principe « completeness gates for enumerable fan-outs » ajouté à `ai-llm-harness` (un compte juste ne suffit pas : un manquant + un dupliqué donnent le bon compte) ; `merge_tags.py` refuse de tourner tant que la couverture est incomplète.

---

## BUG-003 — Champ `confidence` absent de trois batches final

**Sévérité** : Moyenne | **Classe** : Validation de sortie | **Statut** : Ouvert

**Résumé** : Les fichiers `final-23.json`, `final-46.json` et `final-50.json` ont `confidence: null` sur leurs 130 entrées chacun — l'agent Skeptic a omis le champ pour des batches entiers.

**Cause racine** : Aucune validation de schéma à l'écriture des sorties d'agent ; la dérive par batch (un agent omet un champ pour tout son lot) est passée inaperçue pendant des mois.

**Correction** : Détecté par `tag_status.py`. Reste à trancher : re-run Skeptic ciblé des 3 batches, ou backfill de `confidence` depuis les fichiers hunter correspondants.

**Prévention** : Exigence « per-batch acceptance, at write time » ajoutée à `data-thematic-analysis` (un batch ne compte comme fait qu'après validation : effectif, champs requis, codes dans le codebook) ; règle « complete record » pour les passes d'audit.

---

## BUG-004 — Codes hors taxonomie dans ~30 fichiers de tags

**Sévérité** : Moyenne | **Classe** : Vocabulaire contrôlé | **Statut** : Ouvert

**Résumé** : Les agents ont inventé des alias (`M04_MED` pour `M04_MISE_EN_DEMEURE`, `D09_PAC`, `M04_MECONNAISSANCE`), classé des modificateurs (`m_DELAI`, `m_MENACE`) dans l'axe `mechanisms`, et produit des valeurs jointes (`R13/R14`). ~30 fichiers hunter/final concernés.

**Cause racine** : Les codes n'étaient contraints que par le prompt (la taxonomie en contexte), jamais imposés par une validation enum à l'écriture. Un LLM sous contrainte purement textuelle dérive vers des abréviations plausibles.

**Correction** : Détection par `tag_status.py` (validation enum contre `codebook.json`, y compris formes courtes G1/T1). Normalisation au merge ou re-runs ciblés : décision en attente (voir decision-log § 0).

**Prévention** : Le principe existant « controlled vocabulary » de `ai-llm-harness` s'appliquait mais n'avait pas été appliqué à cette couche — la section « multi-round agent campaigns » de `data-thematic-analysis` rend désormais la validation enum explicite pour les campagnes hors harnais.

---

## BUG-005 — Fallback silencieux du merge vers les tags non audités

**Sévérité** : Majeure | **Classe** : Défaut de sécurité | **Statut** : Corrigé

**Résumé** : `merge_tags.py` utilisait la sortie Hunter brute pour tout batch sans final Skeptic, avec un simple `[warn]` console. Lancé en l'état, il aurait publié un dataset dont 48 batches sur 81 (59 %) n'étaient pas audités, sans que rien en aval ne le sache.

**Cause racine** : Défaut de commodité dans un script « glue » écrit en cours de flux, jamais considéré comme un outil CLI — donc jamais passé par la checklist de sécurité (refus par défaut sur entrée incomplète).

**Correction** : Refus par défaut avec liste des batches manquants et pointeur vers `tag_status.py` ; `--allow-hunter-fallback` comme opt-in explicite. Vérifié : le merge refuse et n'écrit rien.

**Prévention** : `dev-script-build` étendu — § 9 « Input completeness follows the same blast-radius logic » + item de checklist ; description élargie pour que les scripts de pipeline écrits en cours de tâche déclenchent la skill.

---

## BUG-006 — URL Pages dupliquée dans 5 constantes et 1 795 fichiers de données

**Sévérité** : Moyenne | **Classe** : Duplication de configuration | **Statut** : Partiellement corrigé

**Résumé** : La migration vers `clombion/icpe-gironde` a exigé la réécriture de 1 795 fichiers (constantes dans 3 scripts Python, `GH_REPO` dans 2 fichiers JS, CSV de rapports, front matters de 1 782 markdowns, README, index) puis la reconstruction de `fiches.parquet` et `fiches.sqlite` dont les 10 992 lignes portaient l'ancienne URL.

**Cause racine** : La base URL Pages est définie indépendamment en cinq endroits du code et cuite dans les artefacts de données, sans constante partagée.

**Correction** : Réécriture globale + reconstruction du pivot faites (2026-07-17). La centralisation de la base URL dans une constante unique (`_paths.py` côté Python, module partagé côté JS) reste à faire.

**Prévention** : Décision documentée dans decision-log § 5 (« URLs Pages inscrites dans les données ») pour que le coût d'un futur renommage soit connu d'avance.
