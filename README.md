# Devoxx transcripts

Haalt alle transcripts (ondertitels) op van de video's in een Devoxx YouTube-playlist.
Standaard zoekt het de playlist **Devoxx Belgium 2026** op het kanaal
[@DevoxxForever](https://www.youtube.com/@DevoxxForever).

## Installatie

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Gebruik

```bash
# Playlist automatisch zoeken en transcripts als .txt opslaan in ./transcripts
python devoxx_transcripts.py

# Of de playlist expliciet meegeven (URL of ID)
python devoxx_transcripts.py --playlist "https://www.youtube.com/playlist?list=PL..."

# Andere opties
python devoxx_transcripts.py --format srt          # txt | json | srt | vtt
python devoxx_transcripts.py --lang en nl fr       # voorkeurtalen, in volgorde
python devoxx_transcripts.py --delay 3             # meer pauze tegen rate limiting
python devoxx_transcripts.py --limit 5             # enkel de eerste 5 video's
python devoxx_transcripts.py --match "devoxx uk.*2026"   # andere playlist zoeken
```

## Output

- `transcripts/NNN-<titel>-<video_id>.<formaat>`: één bestand per talk
- `transcripts/index.csv`: overzicht met status per video (`ok`, `skipped`, `missing`, `error`)
- `transcripts/playlist.json`: playlisttitel, URL en alle video's

Video's met een bestaand transcriptbestand worden overgeslagen. Draai het script dus gewoon
opnieuw als er nieuwe talks geüpload zijn, of na een onderbreking. Met `--force` download je alles opnieuw.

Het script neemt eerst een handmatig transcript in een voorkeurtaal, dan een automatisch
gegenereerd, en anders het transcript dat er is.

## Tips

- Als YouTube je IP blokkeert (`IpBlocked` / `RequestBlocked`), stopt het script.
  Wacht even en draai het opnieuw met een hogere `--delay`. Cloud-IP's worden vaak geblokkeerd,
  dus draai het liefst lokaal.
- Vindt het script de playlist niet, dan toont het de beschikbare playlists op het kanaal.
  Geef dan de juiste mee met `--playlist`.
