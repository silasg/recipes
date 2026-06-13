# Recipe Import Backlog

Snapshot: 2026-05-31. KptnCook items have been removed entirely.

Sections follow a Kanban flow: **Backlog** (not yet imported) → **Doing** (imported, revision in progress) → **Done** (imported and revised). **Doing** is split into pipeline stages (see that section) so a partially-revised recipe shows exactly what's left.

Revision criteria for moving out of Doing into Done (these map onto Doing stages 3–5):
- Ingredients assigned to the recipe step where they're first used (stage 3 — Mapped)
- Ingredients adopted: import-artifact foods/units merged/aliased/edited, units normalised (stage 4 — Cleaned up)
- Nutritional properties set for remaining new foods (stage 5 — Properties filled)

## Probably needs AI import

URL importer fails or publisher known to require AI extraction.

- https://www.madamecuisine.de/gelbes-thai-curry/ — HTTP 200 but parser fails ("import geht nicht")
- https://www.edeka.de/rezepte/rezept/gegrillte-suesskartoffel-mit-avocado-dip.jsp — 403 bot block (content exists in browser)
- https://www.zeit.de/zeit-magazin/2020/12/linsen-mit-pochiertem-ei-rezept-wochenmarkt
- https://www.zeit.de/zeit-magazin/2020/16/ofenkarotten-mit-erdnuss-sosse-wochenmarkt
- https://www.zeit.de/zeit-magazin/2020/20/indisches-eier-curry-wochenmarkt
- https://www.zeit.de/zeit-magazin/2022/10/gruenkohl-pumpernickel-quiche-rezept-wochenmarkt
- https://www.zeit.de/zeit-magazin/2024/17/rhabarber-kokos-mandel-streuseln-rezept-wochenmarkt
- https://www.zeit.de/zeit-magazin/essen-trinken/2018-06/bananenbrot-pfannkuchen-chocolate-chips-mahlzeit
- https://www.spiegel.de/start/guenstiges-rezept-pasta-e-ceci-italienischer-eintopf-mit-kichererbsen-fuer-1-60-euro-a-8708ab6d-bd79-4bc0-9070-b85b8c47936d

## Standard URL backlog

Expected to import via the regular URL importer. Previously HTTP 301/308 entries have been replaced with their resolved targets.

- https://nikesherztanzt.de/2017/01/15/blumenkohl-mit-kirchererbsen-tahine-granatapfelkernen/
- https://kuechenchaotin.de/vegetarisches-pad-thai/
- https://www.japandigest.de/japan-in-deutschland/rezepte/miso-ramen-mit-ingwer-und-knoblauch/
- https://www.ihr-wellness-magazin.de/essen/low-carb-rezepte/low-carb-hauptgerichte-vegetarisch/hauptgericht-vegetarisch-16.html (Gemüse-Frittata)
- https://eatsmarter.de/rezepte/gebackene-suesskartoffeln-und-rote-bete-0
- https://www.gaumenfreundin.de/polenta-pizza-ein-schnelles-kinderrezept/
- https://biancazapatka.com/de/mexikanisch-gefuellte-suesskartoffeln/
- https://www.madamecuisine.de/indisches-spinat-curry-palak-paneer/
- https://www.madamecuisine.de/nudelsalat-mit-rucola-oliven-tomaten/
- https://utopia.de/ratgeber/tofu-braten-so-wird-er-knusprig_152859/
- https://eatsmarter.de/rezepte/pasta-mit-gemuese-tomaten-pesto
- https://knusperstuebchen.net/2019/03/14/blumenkohl-curry-vegetarisches-soulfood/
- https://eatsmarter.de/rezepte/lasagne-aus-polenta-und-gemuese-0
- https://www.malteskitchen.de/bulgur-tomaten-auberginen-joghurt/
- https://www.alykkelife.com/leinsamen-das-heimische-superfood-inkl-pizzateig-rezept/
- https://unterfreundenblog.com/2021/02/11/baked-feta-pasta-aus-dem-ofen-trendrezept-one-pot-soulfood/
- https://utopia.de/ratgeber/vegetarische-kartoffelsuppe-ein-rezept-fuer-den-regionalen-klassiker_133851/
- https://utopia.de/ratgeber/kartoffel-lauch-suppe-rezept-und-vegane-variante_158145/
- https://www.lecker.de/gnocchi-mit-spinatcreme-und-jungen-moehren-79680.html
- https://emmikochteinfach.de/ratatouille-rezept-einfach-aus-dem-ofen/
- https://asiastreetfood.com/blogs/rezept/kimchijeon-kimchi-pfannkuchen
- https://www.eat-this.org/japanisches-donburi-mit-geschmorter-aubergine/
- https://schlaraffenwelt.de/miso-auberginen-nasu-dengaku-rezept/
- https://elavegan.com/de/veganes-ruehrei/
- https://asiastreetfood.com/blogs/rezept/asiatischer-gurkensalat-korea
- https://byanjushka.com/vegane-kaesesauce-2036/
- https://www.vegansociety.com/lifestyle/recipes/palak-tofu
- https://www.smarticular.net/grundrezept-fuer-veganen-brotaufstich-sonnenblumenkerncreme/
- https://eatrunhike.de/vegane-tofu-gnocchi/
- https://heavenlynnhealthy.de/der-weltbeste-tofu/
- https://biancazapatka.com/de/knuspriger-sesam-tofu-mit-sesam-erdnuss-sosse-vegan-glutenfrei/
- https://rezepte.utopia.de/tiktok-fruehstueckstrend-rezept-fuer-baked-oats-17050
- https://essalavanessa.de/rote-linsen-falafel/
- https://www.fuersie.de/kochen/koch-ratgeber/artikel/rezepte-fuer-bananenbrot
- https://rezepte.utopia.de/vegane-mousse-au-chocolat-rezept-mit-seidentofu-und-datteln-20251
- https://www.heimgourmet.com/rezept-54774-express-schoko-cookies.htm

## Dead links

Source URL returns 404 or redirects to unrelated content. Need archive.org snapshot or removal.

- https://www.springlane.de/magazin/rezeptideen/zucchininudel-erdnuss-salat/ — 404
- https://www.springlane.de/magazin/rezeptideenn/quinoa-kichererbsen-bowl/ — 404 (URL has typo "rezeptideenn"; cleaned variant also missing)
- https://www.hellofresh.de/recipes/wurziger-halloumi-auf-tabbouleh-5abbb2c2ae08b518853bacd2 — redirects to a beef variant; original Tabouleh recipe replaced
- https://veganewunder.de/2019/07/01/tofu-schmackhaft-zubereiten-tofu-sticks/ — redirects to homepage; recipe removed

## Instagram / Facebook / freeform

Require manual entry or a future Insta/FB-capable importer. Names taken from notes in `test.md`.

### Instagram

- https://www.instagram.com/reel/C1_0ETIKjZ1/ — (unbenannt; "ca. 30 Min Backofen 200°C Umluft")
- https://www.instagram.com/reel/C_azGRMoCGs/ — Bohnensalat
- https://www.instagram.com/reel/C8mnnGAKGYA/ — Paprika-Tofu-Nudelsauce
- https://www.instagram.com/reel/DO37DLMDURs/ — Kichererbsen Brownies
- https://www.instagram.com/reel/DP33g0hjCdc/ — Zitronen Pasta
- https://www.instagram.com/reel/DN3jbgxWKLw/ — Walnuss-Pilz Bolo
- https://www.instagram.com/reel/DJRZzTGKiQd/ — Erbsen Hummus
- https://www.instagram.com/reel/DIbdb68iOS8/ or https://www.instagram.com/reel/DJwnAeuiQ1r/ — Vegan Carbonara
- https://www.instagram.com/reel/DHKZN9lhGwg/ — Tofu-Kichererbsen-Pasta
- https://www.instagram.com/reel/DIbpUyQINqC/ — Chocolate Cheesecake (180°C / 40 min; bisher mittel, süßer machen)
- https://www.instagram.com/reel/DBtnleCpqEN/ — Kürbis-Kartoffel-Eintopf
- https://www.instagram.com/reel/C8CsBKEiS4I/ — Ofen-Thai-Curry
- https://www.instagram.com/reel/C7_M6hXq4M5/ — Linsen-Schoko-Waffeln
- https://www.instagram.com/reel/C61sKQby-ol/ — Kichererbsen-Snack
- https://www.instagram.com/reel/DFViIHvoOSl/ — Kichererbsen-Protein-Keksteig
- https://www.instagram.com/reel/C69EVTcICLJ/ — Kichererbsen-Tomaten-Orzo/Risoni
- https://www.instagram.com/reel/C6vppRLsDc2/ — Haferflocken-Stangen mit Quark
- https://www.instagram.com/reel/C-miaa1ySpT/ — Ramen
- https://www.instagram.com/reel/DCbPeQugWhN/ — Bohnen-Spinat-Pfanne
- https://www.instagram.com/reel/DCi2Ru6o6rq/ — Veganes Erdnussramen
- https://www.instagram.com/reel/DF5XZ9SoCMk/ — Käse-Lauch-Suppe
- https://www.instagram.com/reel/C9z59JOK5mm/ — Tofu
- https://www.instagram.com/reel/DEfl-Nqouvl/ — Verschiedene Tofu-Marinaden
- https://www.instagram.com/reel/DF8xmpWuuZr/ — Einfacher Tofu
- https://www.instagram.com/reel/DGI-3a2KNNB/ — Salat mit Ofengemüse
- https://www.instagram.com/reel/DFTS72-PZ1F/ — Nudeln mit Erbsen und Tofu
- https://www.instagram.com/reel/DLKA3a-tKk4/ — Blaubeer-Mousse
- https://www.instagram.com/reel/DHY0lmRqK5R/ — Veganer Käsekuchen

### Freeform (no URL)

- Getoastetes Brot mit Pesto/Remoulade, Rohkost und pochiertem Ei

## Doing

Imported into Tandoor, revision in progress. A recipe sits under the **furthest stage it has completed** and waits for the next. Stages, in order:

1. **Imported** — persisted via the URL importer
2. **Image attached** — hero image stored on the recipe
3. **Mapped** — ingredients distributed to the step where first used
4. **Cleaned up** — import-artifact foods/units merged/aliased/edited, orphans deleted
5. **Properties filled** — nutrition backfilled for remaining new foods → then promote to Done

### Stage 1 — Imported (awaiting image)

_(none)_

### Stage 2 — Image attached (awaiting mapping)

_(none)_

### Stage 3 — Mapped (awaiting cleanup)

- [22] Wot Rezept aus Äthiopien — https://www.fernweh-koch.de/aethiopisches-wot/
  - Next: clean up 5 new foods + 3 new units, then backfill properties. Detail: `cleanup/recipe-22-wot.md`

### Stage 4 — Cleaned up (awaiting properties)

_(none)_

### Stage 5 — Properties filled (ready to promote to Done)

_(none)_

## Done

Imported into Tandoor and fully revised. Tandoor IDs in brackets.

- [1] Bulgur-Avocado-Salat — https://eatsmarter.de/rezepte/bulgur-avocado-salat
- [2] Chili con Couscous — https://www.essen-und-trinken.de/rezepte/57223-rzpt-chili-con-couscous
- [3] Chili con Kürbis — https://www.rewe.de/rezepte/chili-con-kuerbis/
- [4] creamy gochujang and spicy tofu rigatoni — https://www.instagram.com/reel/DRKkDzLCelg/
- [20] Dattel-Safran-Risotto — https://www.chefkoch.de/rezepte/1882921306265701/Dattel-Safran-Risotto.html
- [5] High Protein Linsen-Bohnen-Suppe — https://cookidoo.de/recipes/recipe/de-DE/r824645
- [6] Huevos Rancheros mit Paprika-Chili-Salsa — https://www.paleo360.de/rezepte/huevos-rancheros/
- [7] Kichererbsen Cookies mit Erdnussmus & Tahini — https://www.facebook.com/story.php?story_fbid=4208142692788683&id=1670337339902577
- [8] Linsen-Mangold-Curry — https://www.chefkoch.de/rezepte/1530241258527897/Linsen-Mangold-Curry.html
- [9] Mediterraner Kartoffelsalat mit Oliven und Rucola — https://www.rewe.de/rezepte/mediterraner-kartoffelsalat/
- [10] MISO BLACK BEANS — https://www.instagram.com/reel/C5oIiqvKk7F/
- [11] Schnelles Rotes Linsen-Dal — https://www.kuechengoetter.de/rezepte/schnelles-rotes-linsen-dal-45424
- [12] Shakshuka mit Kichererbsen und Spinat — https://einepriselecker.de/shakshuka-mit-kichererbsen-und-spinat/
- [13] Süßkartoffel-Kartoffel-Fenchel-Auflauf — https://www.bio-wichtel.de/service-rezepte/rezepte/fenchel-süßkartoffel-kartoffel-auflauf/
- [14] Veganer Zwiebelmett aus Linsenwaffeln — https://www.facebook.com/groups/VeganesRezeptePortal/permalink/4214517772151175/
- [15] Veganes Käsefondue — https://www.instagram.com/reel/DSDFPVOjElN/
- [16] Westafrikanischer Süßkartoffel-Kichererbsen-Eintopf — https://www.facebook.com/groups/VeganesRezeptePortal/permalink/4184804151789204/
- https://www.instagram.com/reel/C8maDm1K8py/ — Veganer Feta aus Olivenglas
- [17] Yumtamtam One Pot Reis — https://yumtamtam.de/Rezepte/One-Pot-Reis.html
