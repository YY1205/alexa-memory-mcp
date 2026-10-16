# Alexa+ add-on package (draft)

`addon.json` follows the key paths and limits shown in the Alexa+ MCP quickstart's
example. It is a **draft**: the host is a placeholder until the server is deployed, and
some details are not publicly documented (see "addon.json: draft" in
[docs/ALEXA-READINESS.md](../docs/ALEXA-READINESS.md)).

Regenerate it instead of editing by hand:

```bash
python scripts/make_addon.py --base-url https://<your deployed host>
```

Assets (`assets/`) are original artwork; the PNGs are exported from the SVGs with
`scripts/render_assets.sh` and served by the MCP server at `/addon-assets/`.
