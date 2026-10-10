# Plan: Devoxx Belgium 2026 chatbot

Een chatbot die vragen beantwoordt over de talks van Devoxx Belgium 2026, met de transcripts als basis.
Waar mogelijk vult hij aan met de GitHub-repo's van sprekers en andere bronnen. Elk antwoord verwijst naar
de talk, met een YouTube-link die op het juiste moment start, en/of naar de repo.

Voorbeeldvragen:
- "Welke talks gingen over virtual threads, en wat waren de belangrijkste tips?"
- "Wat zei de spreker van de LangChain4j-talk over tool calling? Is er voorbeeldcode?"
- "Vat de keynote samen in 5 punten."

---

## 0. Snelle tussenstap zonder code (optioneel)

Maak een **Claude Project** op claude.ai en upload de `.txt`-transcripts als project knowledge.
Bij grote kennisbanken zoekt Claude dan zelf in de bestanden. Zo heb je in 15 minuten een werkende versie
en weet je welke vragen je eigenlijk wilt stellen. Nadeel: geen links naar tijdstippen, geen GitHub-koppeling,
niet te delen als eigen app.

---

## 1. Data verzamelen

| Bron | Wat | Hoe |
|---|---|---|
| **Transcripts** | Tekst met tijdstempels | Bestaand script, maar in **JSON**-formaat: de tijdstempels zijn nodig voor links als `youtu.be/<id>?t=754`. De huidige `.txt`-bestanden hebben ze niet. |
| **YouTube-metadata** | Titel, beschrijving, duur, hoofdstukken, links in de beschrijving | `yt-dlp` (zit al in het project) |
| **Devoxx CFP** | Abstract, track, tags, niveau, sprekers met bio, bedrijf en sociale accounts | De publieke API van cfp.dev (`dvbe26.cfp.dev/api/public/talks` en `/speakers`). Velden nog te controleren. Koppelen aan de video's via (fuzzy) titelmatch. |
| **GitHub van sprekers** | README's en docs van relevante repo's | Zie hieronder |
| **Slides** | Vaak op Speaker Deck of in de CFP gelinkt | Links uit de beschrijving en de CFP halen, PDF naar tekst |

### GitHub-repo's van sprekers vinden

Dit is het lastigste deel, omdat een naam niet eenduidig naar een GitHub-account leidt. In volgorde van betrouwbaarheid:

1. **Expliciete links** in de YouTube-beschrijving, het CFP-abstract of de slides (bv. `github.com/...` of "demo code").
   Dit zijn bijna altijd de juiste repo's voor die talk.
2. **GitHub-account van de spreker** via de CFP (als er een veld voor is), of via een link op hun X-, Bluesky- of LinkedIn-profiel.
3. **Zoeken op GitHub op naam**, alleen met bevestiging: het profiel moet hetzelfde bedrijf, dezelfde website
   of hetzelfde sociale account vermelden. Twijfelgevallen in een lijst die je zelf even nakijkt.

Per spreker neem je dan alleen de repo's die over het onderwerp van de talk gaan: README, `docs/` en voorbeelden.
Niet de volledige broncode, althans niet in de eerste versie.

### Andere gerelateerde bronnen (later)
- Officiële documentatie van technologieën die vaak terugkomen (JEP's, Spring, Quarkus, LangChain4j, ...)
- Blogposts van sprekers over hun talk
- Talks van eerdere Devoxx-edities, om "wat is er veranderd sinds vorig jaar" te kunnen beantwoorden

---

## 2. Verwerken

1. **Opschonen.** Automatische transcripts hebben geen leestekens en bevatten fouten in vaktermen ("cotlin", "quark us").
   Claude Haiku 5.5 kan via de Batch API per talk leestekens en alinea's toevoegen en vaktermen corrigeren.
2. **Samenvatting per talk** (±300 woorden): kernpunten, technologieën en genoemde repo's of links. Ook dit via de Batch API.
3. **Opdelen in stukken (chunks)** van ±2-3 minuten spreektijd (±400 woorden), met wat overlap.
   Elk stuk krijgt metadata mee: talk-id, titel, spreker, track, starttijdstempel en YouTube-link.

**Omvang (schatting):** ±250 talks × ±45 min ≈ 2,5 miljoen tokens. Dat past niet in één prompt (max. 1M),
dus de chatbot moet zoeken (RAG). De **samenvattingen** zijn samen wel klein (±75k tokens) en kunnen altijd mee.

---

## 3. Zoekindex

- **Hybride zoeken:** combineer betekenis (embeddings) met trefwoorden (BM25).
  Trefwoorden zijn hier belangrijk, voor JEP-nummers, bibliotheeknamen en versienummers.
- **Embeddings:** Anthropic heeft zelf geen embeddings-model. Kies Voyage AI (aanbevolen door Anthropic) of een gratis lokaal model.
- **Opslag:** het gaat om ±10-15k stukken, dus een lokale database volstaat: SQLite met `sqlite-vec`, LanceDB of Chroma.
  Een clouddienst is niet nodig.

---

## 4. De chatbot

- **Model:** Claude Opus 5.5 (`claude-opus-5-5`) via de Anthropic Python SDK. Voor lagere kosten kan Sonnet 5.5 ook.
- **Opbouw:**
  - De **systeemprompt** bevat de catalogus van alle talks met hun samenvatting. Met prompt caching kost dat weinig
    per vraag. Zo weet het model altijd welke talks er zijn.
  - **Tools** die het model zelf aanroept:
    - `search_transcripts(query, track?, speaker?)` geeft de relevante stukken terug, met tijdstempel.
    - `get_talk(talk_id)` geeft de volledige talk.
    - `search_repos(query, speaker?)` zoekt in de README's en docs van de sprekers.
  - Het model kan meerdere zoekopdrachten na elkaar doen ("eerst de talk vinden, dan de details, dan de code").
- **Bronvermelding:** elk antwoord eindigt met de gebruikte talks als klikbare link naar het juiste tijdstip,
  plus de gebruikte repo's.
- **Taal:** je kunt in het Nederlands vragen stellen over Engelstalige talks. Het antwoord komt in de taal van de vraag.
- **Interface:** eerst een eenvoudige CLI, daarna een webinterface (Streamlit of Gradio). Later eventueel een Slack-bot.

---

## 5. Kwaliteit testen

- Stel 30-50 testvragen op, met per vraag de talk(s) die het antwoord bevatten.
- Meet of de zoekstap de juiste stukken vindt, en of de bronvermeldingen kloppen.
- Draai die test na elke wijziging aan chunking, zoeken of prompt.

---

## 6. Kosten (ruwe schatting, API-prijzen oktober 2026)

| Stap | Eenmalig / per vraag | Schatting |
|---|---|---|
| Opschonen en samenvatten, ±2,5M tokens in en uit, Haiku 5.5 via Batch (−50%) | eenmalig | < $1 |
| Hetzelfde met Opus 5.5 via Batch | eenmalig | ± $30 |
| Embeddings | eenmalig | enkele dollars, of gratis lokaal |
| Vraag met Opus 5.5 (gecachte catalogus + ±10k tokens zoekresultaten + antwoord) | per vraag | ± $0,05-0,10 |
| Dezelfde vraag met Sonnet 5.5 | per vraag | ± $0,03-0,05 |

---

## 7. Volgorde van werken

1. Transcripts opnieuw ophalen als JSON (met tijdstempels). Het script uitbreiden zodat het altijd ook JSON bewaart.
2. CFP-metadata ophalen en koppelen aan de video's.
3. Opschonen, samenvatten en chunken.
4. Index bouwen. Eerste versie van de chatbot in de CLI, alleen met de transcripts.
5. Testvragen opstellen en het zoeken verbeteren.
6. GitHub-repo's toevoegen: eerst de expliciete links, daarna de bevestigde accounts.
7. Webinterface.
8. Extra bronnen: slides, docs en eerdere edities.

## Voorgestelde structuur van de repo

```
devoxx_transcripts.py      # bestaand: transcripts ophalen
ingest/
  cfp.py                   # talks en sprekers uit de Devoxx CFP
  github.py                # repo's van sprekers zoeken en ophalen
  clean.py                 # opschonen en samenvatten (Batch API)
  chunk.py                 # opdelen in stukken met metadata
index/build.py             # embeddings en zoekindex
chatbot/
  app.py                   # CLI- en webinterface
  tools.py                 # search_transcripts, get_talk, search_repos
eval/questions.yaml        # testvragen
```

---

## Open vragen

- **Doelgroep:** alleen voor jezelf, of publiek? Bij een publieke versie: toestemming vragen aan Devoxx.
  De talks zijn van de sprekers en van Devoxx, en ook de licenties van de GitHub-repo's spelen dan mee.
- **Budget:** Opus of Sonnet voor de antwoorden? Betaalde of lokale embeddings?
- **Hosting:** lokaal op je laptop, of online (bv. een kleine server of Streamlit Community Cloud)?
