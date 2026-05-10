"""OAuth2 client builders for GitHub + Google (#299, sub-A2).

Each provider is opt-in: when the corresponding env-var pair is unset
the engine simply doesn't mount the router for that provider. This is
intentional — a self-host install can run with email+password only,
and only set up OAuth credentials once they're ready to wire up the
GitHub/Google App configurations.

Scopes:
- GitHub: ``read:user`` + ``user:email`` — minimum required to fetch
  the verified primary email. Without ``user:email`` the GitHub API
  returns a username but no email and the OAuth router fails with
  ``OAUTH_NOT_AVAILABLE_EMAIL``.
- Google: ``openid`` + ``email`` + ``profile`` — defaults baked into
  ``GoogleOAuth2``; we use the upstream defaults rather than passing
  scopes explicitly.
"""

from __future__ import annotations

from httpx_oauth.clients.github import GitHubOAuth2
from httpx_oauth.clients.google import GoogleOAuth2

# GitHub requires `user:email` to read the verified primary email.
GITHUB_DEFAULT_SCOPES = ["read:user", "user:email"]


def make_github_client(client_id: str, client_secret: str) -> GitHubOAuth2:
    """Construct a configured GitHub OAuth2 client."""
    return GitHubOAuth2(client_id, client_secret, scopes=GITHUB_DEFAULT_SCOPES)


def make_google_client(client_id: str, client_secret: str) -> GoogleOAuth2:
    """Construct a configured Google OAuth2 client.

    Google's default scopes (openid + email + profile) are sufficient
    to populate the user account; passing custom scopes here would
    require also updating the consent-screen configuration in Google
    Cloud Console.
    """
    return GoogleOAuth2(client_id, client_secret)
