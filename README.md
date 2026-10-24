# OR-automatisering
bestellingen beter en rapper ingeven

Nederlandstalig lokaal maaltijdenbeheer. De administratie blijft op de eigen pc.

## Een bestaande installatie bijwerken

1. Download **Update ophalen.cmd** uit deze repository (Download raw file).
2. Sluit de app volledig, ook het zwarte startvenster.
3. Zet het bestand in de bestaande appmap, naast Start Oranje.cmd, en dubbelklik.
4. Het bestand haalt de nieuwste update van deze repository op en controleert de download.
5. De installatie maakt een reservekopie en behoudt de administratie, contacten en instellingen.
6. Start opnieuw en vernieuw de browser. Maak bestaande maandafrekeningen opnieuw voor de nieuwe PDF-kop en leesbare code.

Er is geen Git-installatie of GitHub-account nodig op de gebruikers-pc. Werkt iets niet, bekijk github-update.log in de appmap.

## Nieuwe installatie

Download de broncode als ZIP, pak uit en installeer Python 3.10 of nieuwer met Python in PATH. Voer Installeren.cmd uit en start daarna Start Oranje.cmd. Een nieuwe installatie bevat geen bewoners; herstel zo nodig een eigen volledige back-up via Instellingen.

## Auto mail in Outlook

Bij Afrekeningen opent **Auto mail** een concept in de klassieke Outlook voor Windows op dezelfde pc. De bewindvoerder, het onderwerp, de mailtekst en de opgeslagen pdf-bijlage worden ingevuld. Controleer ook de afzendrekening in Outlook. Je verstuurt zelf en klikt daarna in de app op **Verstuurd registreren**. Een concept openen verandert de verzendstatus niet. De nieuwe Outlook en webmail ondersteunen deze lokale Windows-koppeling niet. Bij een fout controleer je eerst de map Concepten; kopiëren en pdf downloaden blijven mogelijk.

## Publicatie van een volgende versie

Pas VERSION aan en voer `python scripts/build_github.py` uit in de ontwikkelmap. Publiceer de opgeschoonde inhoud van github-publicatie als één commit, inclusief releases/latest.json en het nieuwe updatebestand. De Windows-updater haalt steeds de versie uit latest.json op. Oude updatebestanden blijven beschikbaar.

Publiceer nooit de ontwikkelmap met de echte database, runtime.json, contactseed, persoonlijke tests of back-ups. De bouwstap maakt een openbare kopie met een lege contactseed. Alle berekeningen en gegevensverwerking gebeuren lokaal.
