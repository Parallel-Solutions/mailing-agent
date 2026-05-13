# Philology Knowledge Sources

This document describes which Russian-language knowledge sources are useful for
the philologist agent, how we can use them legally and technically, and which
sources should not be treated as a magic "grammar brain".

The product workflow must stay simple: the user uploads data and templates only.
All sources below are internal infrastructure for rules, RAG explanations,
linguistic tools, tests, and admin review.

## What We Need

The philologist does not need a library of random books. It needs a controlled
knowledge stack:

- Deterministic project rules for safe fixes in КП, contracts, attachments, and
  official-business wording.
- Morphology and syntax tools for Russian word forms, names, organizations, and
  place names.
- RAG sources with short cited fragments that explain why a correction was made.
- Logs and memory for cases where the system was unsure and needs later tuning.

## Priority 1: Use Now

### 1. Project-Specific Rules

- Format: `data/knowledge/philology_rules.json`
- Use for: direct rules, safe auto-fixes, RAG explanations.
- Why: this is the most valuable layer because our documents have a narrow domain:
  commercial offers, contracts, municipalities, official names, dates, and
  attachments.

Recommended topics:

- Official names of municipalities.
- Republics, districts, urban/rural settlements.
- Head names, surnames, names, patronymics, and roles.
- Document terms: договор, приложение, техническое задание, календарный план.
- Uppercase/lowercase in contracts and running text.
- Official-business style and recurring legal wording.

### 2. Gramota.ru Reference Materials

- URL: https://gramota.ru/
- Use for: source-backed RAG snippets and manual rule extraction.
- Strong areas: spelling, punctuation, uppercase/lowercase, difficult words,
  Russian names, official document references.
- Why useful: Gramota lists dictionaries and references such as Lopatin's
  spelling rules, Rosenthal-style usage, proper-name references, and document
  formatting topics.
- Constraint: do not bulk-scrape blindly. Extract short cited snippets or convert
  repeated findings into explicit internal rules.

### 3. Orthographia / Lopatin Academic Rules

- URL: https://www.orthographia.ru/
- Use for: authoritative spelling and punctuation references.
- Strong areas: normative orthography, punctuation, uppercase/lowercase.
- Constraint: good for rule citations; less useful for municipality-specific
  inflection.

### 4. OpenCorpora

- URL: https://opencorpora.org/?page=downloads
- License: CC BY-SA 3.0 is linked on the OpenCorpora downloads page.
- Use for: morphology, dictionary support, word forms, test data.
- Strong areas: lemmas, word forms, morph tags, disambiguated corpus.
- Constraint: this is not a style guide and not an official grammar rule source.
  It supports morphology and word-form checks, not document-style decisions.

### 5. Natasha and Yargy

- Natasha: https://github.com/natasha/natasha
- Yargy: https://github.com/natasha/yargy
- Use for: Russian NLP preprocessing, named entities, morphology/syntax signals,
  and rule-based extraction of municipality names, districts, regions, and FIO.
- Why useful: these are practical Russian NLP libraries. They help the agent
  detect what a fragment is before deciding how to fix it.
- Constraint: they are tools, not normative knowledge bases. They should feed
  the decision layer, not replace our rules.

## Priority 2: Useful With Constraints

### 6. Universal Dependencies Russian SynTagRus

- URL: https://universaldependencies.org/treebanks/ru_syntagrus/index.html
- License: CC BY-NC-SA 4.0 on the UD page.
- Use for: syntax/morphology examples, tests, possible parser evaluation.
- Constraint: the non-commercial/share-alike license means we should not bundle
  it casually into a commercial product without legal review. Better as
  reference/test data.

### 7. SynTagRus License From RNC

- URL: https://ruscorpora.ru/file/license_dataset_syntagrus_eng/
- Use for: legal review before using the dataset directly.
- Constraint: treat as a separate licensing item. Do not ingest into production
  RAG until terms are explicitly accepted for our use case.

### 8. LanguageTool

- URL: https://github.com/languagetool-org/languagetool
- Use for: optional additional grammar/style signal, not primary logic.
- Strong areas: rule-based grammar and style checking.
- Constraint: useful as a local service or reference for rule ideas, but our
  legal-document rules should remain in our repository and be explainable.

## Priority 3: Mostly Not Useful For This Product

### 9. Ozhegov Dictionary

- Use for: word meaning checks if needed.
- Not useful for: official-document inflection and contract phrasing.
- Recommendation: do not prioritize.

### 10. Fasmer Etymological Dictionary

- Use for: etymology only.
- Not useful for: grammar, declension, business style.
- Recommendation: skip.

### 11. National Russian Corpus Web Interface

- URL: https://ruscorpora.ru/
- Use for: manual research and examples.
- Constraint: do not assume automated bulk ingestion is allowed. Use only after
  checking terms/API access.

## Storage Plan

### Short Rules

Use `data/knowledge/philology_rules.json`:

```json
{
  "id": "ru-mngp-001",
  "title": "Document terms in running text",
  "source": "Internal rule based on official-business style references",
  "topic": "uppercase lowercase",
  "keywords": ["договор", "приложение", "техническое задание", "календарный план"],
  "rule": "In running text, generic document terms are lowercase unless they are a formal title or sentence start.",
  "good_examples": ["техническим заданием (приложение № 1 к договору)"],
  "bad_examples": ["Техническим заданием (Приложение № 1 к Договору)"]
}
```

### Long Sources

Use the ingestion command:

```bash
python -m src.generator.ingest_philology_source path/to/source.txt \
  --title "Source title" \
  --source "Citation" \
  --topic "official-business style" \
  --keywords "договор, приложение, прописные буквы"
```

This writes chunks to `data/knowledge/philology_sources.jsonl`.

## Recommended Implementation Order

1. Add 30-50 short project rules from our real document errors.
2. Add Gramota/Lopatin snippets only where they directly support those rules.
3. Add Yargy patterns for official names of municipalities, districts, regions,
   and FIO.
4. Use Natasha/Yargy detections as evidence for the philologist decision layer.
5. Enable semantic RAG locally/server-side when model files are available.
6. Consider LanguageTool as an optional external checker after core rules are
   stable.
7. Do not add Pinecone/APInita yet. Local JSONL + local semantic index is enough.

## Why Not Pinecone/APInita Now

- Current knowledge volume is small.
- Local RAG is easier to version, test, and explain.
- External vector databases add credentials, cost, network dependency, and legal
  uncertainty around uploaded text.
- We can move to a vector DB later if the source base grows beyond local indexing.

## What To Tell Stakeholders

The agent is not "trained by books" in the human sense. Books and references are
useful only after we convert them into machine-usable assets:

- explicit rules;
- cited RAG snippets;
- dictionaries and morphology resources;
- extraction patterns;
- tests and logs of uncertain cases.

Raw books are too broad, often copyrighted, and not structured as executable
grammar logic. For this product, the highest accuracy will come from a curated
domain-specific rule base plus Russian NLP tools and source-backed explanations.
