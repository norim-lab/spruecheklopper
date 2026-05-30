# [OPEN] derewo-blocked

## Symptom
- Derewo-Scraper meldet, dass alles geblockt wird.

## Hypothesen
- H1: Die neue VPN-IP ist bereits oder kurzfristig von Cloudflare/IP-Reputation betroffen.
- H2: Das frische `cf_clearance` ist formal vorhanden, aber zur neuen IP/UA-Kombination ungueltig.
- H3: Die Derewo-Last/Parallelitaet fuehrt nach kurzer Zeit in eine Challenge-/403-Flood.
- H4: `html_is_challenge()` klassifiziert Derewo-Responses erneut falsch.
- H5: Der Scraper steckt in einer Cookie-Refresh-Schleife und zaehlt deshalb effektiv nur Fehler.

## Naechste Evidenz
- Laufstatus und letzte Terminal-Ausgaben lesen
- Progress/Control/Patch-Dateien vergleichen
- Nur bei Bedarf gezielt instrumentieren

## Evidenz
- Reale 403-Flood beobachtet, keine reine Anzeige-Anomalie.
- `cf_solver` schlug im Refresh-Pfad mit `nodriver ... ProtocolException: Session with given id not found` fehl.
- Progress lag deutlich ueber Patchbestand; damit war der Resume-Pfad nicht robust genug.

## Bestaetigte Hypothesen
- H3 bestaetigt: Die Derewo-Last/Parallelitaet fuehrt nach kurzer Zeit in 403-/Refresh-Floods.
- H5 bestaetigt: Der Scraper lief in eine Cookie-Refresh-Schleife und zaehlte viele Fehler.

## Verworfen
- H4 verworfen: Es lagen echte `HTTP 403` vor, kein bloesser Challenge-Classifier-Fehler.

## Fix
- Derewo-Resume jetzt aus Patchbestand statt nur aus `completed`-Zaehler.
- Keine zufaellige Reihenfolge mehr bei Resume.
- Parallelitaet auf 2 reduziert.
- Progress wird nach jedem Batch geschrieben.
- Frueher Stop bei 403-Flood/Refresh-Fehler statt endlosem Selbstbeschuss.
- Neuer Crash belegt: `UnicodeEncodeError` durch Unicode-Zeichen (`→`) in Console-Output unter Windows.
- Fix dafuer: problematische Unicode-Ausgaben im Derewo-Scraper auf ASCII umgestellt.
