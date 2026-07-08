@AGENTS.md
@docs/HANDOFF.md

# Claude Code-notities

<p align="center">
  <a href="./CLAUDE.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/CLAUDE-简体中文-d73a49"></a>
  <a href="./CLAUDE.md"><img alt="English" src="https://img.shields.io/badge/CLAUDE-English-0969da"></a>
  <a href="./CLAUDE.nl.md"><img alt="Nederlands" src="https://img.shields.io/badge/CLAUDE-Nederlands-f58220"></a>
</p>

- Beschouw `AGENTS.md` als het stabiele projectcontract en `docs/HANDOFF.md` als de actuele operationele overdracht.
- Start vanuit de repository-root (`~/airco-tracking`) en controleer branch, working tree en laatste commit voordat je bestanden wijzigt.
- Handoff-feiten kunnen verouderen. Controleer live GitHub-, Azure- en externe-reviewstatus opnieuw voordat je op tijdgevoelige claims handelt.
- Vraag de gebruiker nooit om een API secret in chat te plakken. Gebruik een verborgen terminalprompt en Azure Key Vault voor credentials.
- `inventory.json` is nu een productiecontract dat door `~/airco-tracking-web` wordt geconsumeerd. Inspecteer de frontend validator/types en coördineer beide repositories voordat schema of semantiek wijzigt.
- Houd de inventory Blob privé. Het publieke dashboard moet deze blijven lezen via de same-origin Managed Identity API, nooit via een browser-side Storage Key of SAS-token.
- Beide repositories zijn openbaar. Houd Git author-configuratie repository-lokaal en gebruik de bestaande GitHub noreply-identiteit in plaats van een machine-afgeleide auteur.
- Elke Markdown-documentatiewijziging moet de Chinese, Engelse en Nederlandse varianten tegelijk bijwerken.
- Als het gevraagde werk een externe submission, aankoop, permissiewijziging, credential-aanmaak of nog niet geautoriseerde productiemutatie raakt, pauzeer dan vóór die actie.
- Werk `docs/HANDOFF.md` in dezelfde wijziging bij na een betekenisvolle mijlpaal of nieuw gevonden blocker.
