---
name: Website server import output
description: User's LitronSSH import format, automatic metadata and privacy requirements
---
The user imports servers by pasting plain field lines starting with
`Server name:` and ending with `Certificate SHA-256:`. No heading, branding,
separator lines or footer.
Preserve the exact labels and order. Server provider must be blank.
Country, city and region should be discovered automatically, with stable
generated server and URL names; no extra location/name questions.
Server name is only the country name (e.g. France). URL name is the lowercase
country code, a dash and two random digits (e.g. fr-27), saved for stable exports.
Maximum accounts is the only additional activation question.

**Why:** the user's website imports this text instead of requiring manual
form entry. They explicitly require preserving protocol and API settings.

**How to apply:** retain existing credentials and limits, never rotate them to
display the import block. Output containing the secret belongs only in explicit
root-admin export/activation output, not ordinary status or HTTP responses.
Geolocation is approximate; warn on lookup failure rather than invent data.
