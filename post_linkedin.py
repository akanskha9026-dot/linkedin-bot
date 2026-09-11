"""
Posts text content to LinkedIn using the official UGC Posts API
(POST https://api.linkedin.com/v2/ugcPosts), authenticated with an OAuth 2.0
access token obtained via oauth_local_setup.py.

This deliberately does NOT use any scraping, headless-browser login, or
unofficial automation — only LinkedIn's documented REST API with a token you
authorized yourself.
"""

import logging

import requests

import config

log = logging.getLogger("post_linkedin")

UGC_POSTS_URL = "https://api.linkedin.com/v2/ugcPosts"


class LinkedInAuthError(RuntimeError):
    """Raised when the access token is missing, invalid, or expired.

    This is intentionally a distinct, loud exception: LinkedIn access tokens
    obtained this way typically expire after ~60 days, and the daily
    workflow should FAIL the GitHub Action (not silently skip posting) so
    you notice and re-run oauth_local_setup.py.
    """


def post_text_update(text: str) -> dict:
    if not config.LINKEDIN_ACCESS_TOKEN:
        raise LinkedInAuthError(
            "LINKEDIN_ACCESS_TOKEN is not set. Run oauth_local_setup.py and "
            "add the token as a GitHub Secret."
        )
    if not config.LINKEDIN_PERSON_URN:
        raise LinkedInAuthError(
            "LINKEDIN_PERSON_URN is not set. Run oauth_local_setup.py and "
            "add the URN as a GitHub Secret."
        )

    headers = {
        "Authorization": f"Bearer {config.LINKEDIN_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }

    body = {
        "author": config.LINKEDIN_PERSON_URN,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }

    resp = requests.post(UGC_POSTS_URL, headers=headers, json=body, timeout=30)

    if resp.status_code == 401:
        raise LinkedInAuthError(
            f"LinkedIn rejected the access token (401 Unauthorized). It has "
            f"very likely expired (~60 day lifetime) or was revoked. "
            f"Re-run oauth_local_setup.py and update the GitHub Secret. "
            f"Response: {resp.text[:300]}"
        )
    if resp.status_code == 403:
        raise LinkedInAuthError(
            f"LinkedIn rejected the request as forbidden (403). Check that "
            f"the app has the 'Share on LinkedIn' product approved and the "
            f"token has the w_member_social scope. Response: {resp.text[:300]}"
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"LinkedIn UGC post failed ({resp.status_code}): {resp.text[:500]}"
        )

    post_id = resp.headers.get("x-restli-id", "")
    log.info("Posted successfully. Post ID: %s", post_id)
    return {"status_code": resp.status_code, "post_id": post_id}
