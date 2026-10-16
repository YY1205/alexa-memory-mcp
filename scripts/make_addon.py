"""Write addon-package/addon.json for a deployment and check it against the documented limits.

    python scripts/make_addon.py --base-url https://memory.example.com   # after deploying
    python scripts/make_addon.py --check                                 # validate as is

Only key paths shown in the Alexa+ MCP quickstart's addon.json example are used
(manifestVersion, storeListing.distributionCountries/locales.<locale>.{default, name,
shortDescription, fullDescription, examplePhrases, privacyAndCompliance, mediaAssets},
integrations[].type/config.endpoints.default). Open questions are listed in
docs/ALEXA-READINESS.md ("addon.json: draft").
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "addon-package" / "addon.json"
PLACEHOLDER = "https://portable-memory.example.com"
ICON_SIZES = (72, 64, 88, 126, 180, 241)  # all six required for icons.light

NAME = "Portable Memory"
SHORT = "A long-term memory you own: Alexa saves only what you confirm, keeps history, and you can edit the file."
FULL = """Portable Memory gives Alexa a long-term memory that belongs to you. Your memories live in one plain Markdown file, memories.md, in your own Google Drive folder, so you can read and edit it yourself at any time, and any assistant you connect works with the same file.

Nothing is saved silently. When you ask Alexa to remember something, it reads the fact back and saves it only after you say yes. If a new fact updates an old one (the contractor now starts on the 27th), Alexa asks once whether to change it; the old fact is kept as history instead of being deleted. Temporary facts such as appointments get a review date, and Alexa can ask whether they are still true.

You can ask things like when your sister's birthday is, tell Alexa what to remember, correct a fact, or ask whether anything needs updating. If you change the file by hand, Alexa uses your edit within seconds.

Setup: Portable Memory is self-hosted. You run the server, share one Google Drive folder with it, and link your account with your owner passphrase. The server stores only hashed sign-in tokens; see the privacy policy for details.

This is a hackathon prototype. Don't store passwords, financial or health information."""
PHRASES = [
    "When is my sister's birthday?",
    "Remember that my dentist appointment is Friday at 3.",
    "The kitchen contractor now starts on the 27th.",
    "Is there anything in my memory I should update?",
]


def build(base_url: str) -> dict:
    base = base_url.rstrip("/")
    asset = f"{base}/addon-assets"
    return {
        "manifestVersion": "1.0",
        "storeListing": {
            "distributionCountries": ["US"],
            "locales": {
                "en-US": {
                    "default": "DEFAULT",
                    "name": {"value": NAME,
                             "spokenForm": {"format": "ipa", "value": "ˈpɔɹtəbəl ˈmɛməɹi"}},
                    "shortDescription": SHORT,
                    "fullDescription": FULL,
                    "examplePhrases": PHRASES,
                    "privacyAndCompliance": {"privacyPolicyUrl": f"{base}/privacy",
                                             "termsOfUseUrl": f"{base}/terms"},
                    "mediaAssets": {
                        "icons": {"light": [{"size": f"{s}x{s}", "uri": f"{asset}/icon-{s}.png"}
                                            for s in ICON_SIZES],
                                  "dark": []},
                        "carouselImages": [{"uri": f"{asset}/carousel-600x900.png", "size": "600x900",
                                            "altText": "Portable Memory: your memory is a file you own. "
                                                       "Nothing is saved until you say yes."}],
                        "bannerImages": [],
                    },
                },
            },
        },
        "integrations": [{"type": "MCP", "config": {"endpoints": {"default": {"type": "HTTPS",
                                                                              "uri": f"{base}/mcp"}}}}],
    }


def check(manifest: dict) -> list[str]:
    """Problems against the limits documented in the Alexa+ MCP quickstart."""
    problems = []
    loc = manifest["storeListing"]["locales"]["en-US"]
    if len(loc["name"]["value"]) > 30:
        problems.append("name.value > 30 characters")
    if len(loc["shortDescription"]) > 123:
        problems.append(f"shortDescription is {len(loc['shortDescription'])} > 123 characters")
    if len(loc["fullDescription"]) > 4000:
        problems.append("fullDescription > 4000 characters")
    phrases = loc["examplePhrases"]
    if not 3 <= len(phrases) <= 4 or any(len(p) > 200 for p in phrases):
        problems.append("examplePhrases must be 3-4 items of <= 200 characters")
    urls = list(loc["privacyAndCompliance"].values())
    media = loc["mediaAssets"]
    sizes = {i["size"] for i in media["icons"]["light"]}
    if sizes != {f"{s}x{s}" for s in ICON_SIZES}:
        problems.append(f"icons.light must have exactly sizes {ICON_SIZES}")
    if media["icons"]["dark"] and {i["size"] for i in media["icons"]["dark"]} != sizes:
        problems.append("icons.dark, if given, needs the same six sizes")
    if not any(c["size"] == "600x900" for c in media["carouselImages"]):
        problems.append("at least one 600x900 carousel image is required")
    for image in media["carouselImages"] + media["bannerImages"]:
        if len(image.get("altText", "")) > 250:
            problems.append("altText > 250 characters")
    urls += [i["uri"] for i in media["icons"]["light"] + media["carouselImages"]]
    urls += [manifest["integrations"][0]["config"]["endpoints"]["default"]["uri"]]
    problems += [f"not https: {u}" for u in urls if urlsplit(u).scheme != "https"]
    for icon in media["icons"]["light"]:  # the files we publish exist
        if not (ROOT / "addon-package" / "assets" / Path(urlsplit(icon["uri"]).path).name).is_file():
            problems.append(f"missing asset for {icon['uri']}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base-url", default=PLACEHOLDER, help="public https URL of the deployed server")
    ap.add_argument("--check", action="store_true", help="only validate the existing addon.json")
    args = ap.parse_args()
    manifest = json.loads(MANIFEST.read_text()) if args.check else build(args.base_url)
    problems = check(manifest)
    for p in problems:
        print("PROBLEM:", p, file=sys.stderr)
    if not args.check and not problems:
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {MANIFEST.relative_to(ROOT)} for {args.base_url}")
    host = manifest["integrations"][0]["config"]["endpoints"]["default"]["uri"]
    if PLACEHOLDER in host:
        print("note: placeholder host; run with --base-url after deploying", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
