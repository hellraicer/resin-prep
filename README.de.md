# Resin Prep – Deutsche Anleitung

Blender-Addon zur Vorbereitung von Resin-Druckmodellen. **Version 0.3.0 ist eine Entwicklungsfassung.**

## Installation

1. **[resin-prep-0.3.0.zip herunterladen](dist/resin-prep-0.3.0.zip)**. Auf GitHub die Datei öffnen und **Download raw file** benutzen. Die Datei nicht entpacken.
2. In Blender **Bearbeiten → Einstellungen → Erweiterungen beziehen** öffnen. Auf Englisch: **Edit → Preferences → Get Extensions**.
3. Rechts oben das Menü öffnen und **Von Festplatte installieren / Install from Disk…** wählen.
4. Das heruntergeladene `resin-prep-0.3.0.zip` auswählen.
5. **Resin Prep** bei Bedarf unter **Add-ons** aktivieren.
6. Im 3D-Viewport **N** drücken und den seitlichen Reiter **Resin Prep** öffnen.

**Wichtig:** GitHubs **Code → Download ZIP** lädt das Quellprojekt herunter. Das Blender-Installationspaket befindet sich im Projekt unter `dist/`.

Bei einer vorhandenen älteren Resin-Prep-Version zuerst die Arbeitsdatei sichern. Falls Blender die Erweiterung nicht direkt ersetzt, die alte Version in Blender deaktivieren bzw. entfernen und danach 0.3 installieren. Die 3D Print Toolbox kann parallel bleiben.

## Erster Start

- Standard: **1 Blender-Einheit = 1 mm**. Für korrekt modellierte Szeneneinheiten den Modus wechseln und ein bekanntes Maß kontrollieren.
- Bauplatte: **Welt-Z=0**, Druckrichtung **+Z**.
- Bauraum frei einstellen oder das Saturn-4-Ultra-16K-Profil verwenden.
- Modell auswählen und **Modell prüfen** ausführen.

## Automatische Reparatur

1. Die zu reparierenden Druckmeshes auswählen.
2. **Automatische Reparatur – Fehlerauswahl → Reparaturplan erstellen**.
3. Fehlerarten bzw. einzelne Löcher, Inselgruppen und Hohlräume anhaken.
4. **Ausgewählte Fehler automatisch reparieren**.
5. Vorschau und Restbefunde kontrollieren.
6. **Reparierte Kopie übernehmen** oder **Vorschau verwerfen**.

Originale bleiben erhalten. Globale Volumen-/Rasterverfahren können auch außerhalb eines einzelnen ausgewählten Befunds Details verändern. Ungeprüfte oder verbleibende Fehler werden ausgewiesen; eine automatische Druckfreigabe gibt es nicht.

## Ausführliche Dokumentation

- [Gesamte Bedienung](docs/Anleitung.html)
- [Automatische Reparatur: Verfahren und Grenzen](docs/Automatische-Reparatur.html)
- [Technik](docs/Technik.html)
- [Vergleich mit UVtools](docs/UVtools-Vergleich.html)
- [Tests und Plattformen](docs/TESTING.md)

Die HTML-Dateien lassen sich nach dem Herunterladen lokal im Browser öffnen. UVtools muss für die Endkontrolle separat installiert sein. Druckformat und Belichtung erzeugt weiterhin dein Drucker-Slicer.

Für Blender 4.2+ vorgesehen; tatsächlich unter Blender 5.2.0 LTS auf Windows getestet. Andere Plattformen sind noch nicht verifiziert. Kein realer Resin-Testdruck im Entwicklungsumfang.
