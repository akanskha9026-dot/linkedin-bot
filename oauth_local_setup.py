"""
ONE-TIME, LOCAL-ONLY script.

You (the LinkedIn account owner) run this on your own machine to complete
LinkedIn's OAuth 2.0 consent flow. Nobody else can do this step for you —
LinkedIn requires the account owner to log in and click "Allow" themselves.

What this script does:
  1. Prints an authorization URL built from your Client ID / redirect URI.
  2. You open that URL in your browser, log in to LinkedIn, and click Allow.
  3. LinkedIn redirects your browser to your redirect URI with a `code`
     query parameter attached (the page itself may show an error/blank —
     that's fine, you only need the URL bar).
  4. You paste the full redirected URL (or just the `code` value) back into
     this script.
  5. The script exchanges that code for an access token, then calls
     LinkedIn's userinfo endpoint to get your person URN.
  6. It prints both values. You copy them into GitHub Secrets — this script
     never uploads or stores them anywhere.

Prerequisites (do these on linkedin.com/developers first):
  1. Create a LinkedIn Developer App at https://www.linkedin.com/developers/apps
     (requires linking it to a Company Page — you can create a placeholder
     one if you don't have one).
  2. In the app's "Products" tab, request:
       - "Share on LinkedIn"
       - "Sign In with LinkedIn using OpenID Connect"
     (Share on LinkedIn is usually auto-approved; OpenID Connect is
     instant.)
  3. In the app's "Auth" tab:
       - Add an "Authorized redirect URL for your app", e.g.
         http://localhost:8080/callback (must exactly match
         LINKEDIN_REDIRECT_URI below / your env var).
       - Copy the "Client ID" and "Client Secret" shown there.

Run:
    export LINKEDIN_CLIENT_ID=...
    export LINKEDIN_CLIENT_SECRET=...
    export LINKEDIN_REDIRECT_URI=http://localhost:8080/callback   # optional, this is the default
    python oauth_local_setup.py
"""

import sys
import urllib.parse

import requests

import config

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"

SCOPES = "openid profile w_member_social"


def build_authorization_url() -> str:
    if not config.LINKEDIN_CLIENT_ID:
        sys.exit("LINKEDIN_CLIENT_ID is not set. Export it and try again.")

    params = {
        "response_type": "code",
        "client_id": config.LINKEDIN_CLIENT_ID,
        "redirect_uri": config.LINKEDIN_REDIRECT_URI,
        "scope": SCOPES,
        "state": "embryology-bot-setup",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def extract_code(pasted: str) -> str:
    pasted = pasted.strip()
    if pasted.startswith("http"):
        parsed = urllib.parse.urlparse(pasted)
        qs = urllib.parse.parse_qs(parsed.query)
        if "error" in qs:
            sys.exit(
                f"LinkedIn returned an error instead of a code: "
                f"{qs.get('error')} — {qs.get('error_description')}"
            )
        if "code" not in qs:
            sys.exit("Could not find a 'code' parameter in that URL. Paste the full redirected URL.")
        return qs["code"][0]
    return pasted  # assume they pasted the raw code value


def exchange_code_for_token(code: str) -> dict:
    if not config.LINKEDIN_CLIENT_SECRET:
        sys.exit("LINKEDIN_CLIENT_SECRET is not set. Export it and try again.")

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.LINKEDIN_REDIRECT_URI,
        "client_id": config.LINKEDIN_CLIENT_ID,
        "client_secret": config.LINKEDIN_CLIENT_SECRET,
    }
    resp = requests.post(TOKEN_URL, data=data, timeout=30)
    if resp.status_code != 200:
        sys.exit(f"Token exchange failed ({resp.status_code}): {resp.text}")
    return resp.json()


def fetch_person_urn(access_token: str) -> str:
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(USERINFO_URL, headers=headers, timeout=30)
    if resp.status_code != 200:
        sys.exit(f"Could not fetch userinfo ({resp.status_code}): {resp.text}")
    sub = resp.json().get("sub")
    if not sub:
        sys.exit(f"userinfo response had no 'sub' field: {resp.json()}")
    return f"urn:li:person:{sub}"


def main():
    print("=" * 70)
    print("LinkedIn OAuth one-time setup")
    print("=" * 70)

    auth_url = build_authorization_url()
    print("\n1. Open this URL in your browser, log in, and click Allow:\n")
    print(auth_url)
    print(
        "\n2. After you click Allow, LinkedIn will redirect your browser to "
        "your redirect URI (e.g. http://localhost:8080/callback?code=...). "
        "The page itself may fail to load (that's expected if nothing is "
        "listening there) — you only need to copy the URL from the address "
        "bar."
    )

    pasted = input("\n3. Paste the full redirected URL (or just the code) here: ")
    code = extract_code(pasted)

    print("\nExchanging code for an access token...")
    token_data = exchange_code_for_token(code)
    access_token = token_data["access_token"]
    expires_in = token_data.get("expires_in", "unknown")

    print("Fetching your person URN...")
    person_urn = fetch_person_urn(access_token)

    print("\n" + "=" * 70)
    print("SUCCESS. Save these as GitHub Secrets (Settings > Secrets and")
    print("variables > Actions > New repository secret):")
    print("=" * 70)
    print(f"\n  LINKEDIN_ACCESS_TOKEN = {access_token}")
    print(f"  LINKEDIN_PERSON_URN   = {person_urn}")
    print(f"\nToken expires in approximately {expires_in} seconds "
          f"(~{int(expires_in) // 86400 if str(expires_in).isdigit() else '?'} days).")
    print(
        "\nWhen the posting workflow starts failing with a LinkedInAuthError "
        "about an invalid/expired token, re-run this script and update the "
        "LINKEDIN_ACCESS_TOKEN secret."
    )


if __name__ == "__main__":
    main()
