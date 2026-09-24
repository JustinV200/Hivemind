# Fixture site: login

A three-page static site the browser fast path is tested against: the browser contract suite
(`tests/contracts/test_browser_contract.py`), the Playwright backend's storage tests, and phase 6's
exit scenario ("the fixture-site login goal"). Plain HTML with a little inline JavaScript and no
external resources, so it behaves the same opened as `file://` URLs and served over http.

`hivemind.exoskeleton.browser.fake.login.login_site()` builds the same site for the fake browser.
Every title, accessible name and text below is shared with it, and
`tests/unit/exoskeleton/browser/fake/test_login.py` checks these files still carry them: change
both together.

## Pages

| Page | Title | What is on it |
|---|---|---|
| `login.html` | Log in - HiveMind fixture | Heading "Log in"; textbox "Username"; textbox "Password"; button "Log in"; link "Forgot your password?", which reveals the hidden line "Ask the beekeeper for a new one." |
| `welcome.html` | Welcome - HiveMind fixture | Heading "Welcome, alice" once logged in. Without a session it sends the visitor back to `login.html` before anything renders. |
| `long.html` | Long page - HiveMind fixture | Heading "A long page" and 84,000 characters of text, generated in the page, longer than every read's cap. |

## Behaviour

- The one account is `alice` / `honeycomb`. It is a fixture's, printed here on purpose, never a
  real credential.
- Submitting the form (the button, or Enter in either field) with those credentials stores the
  local-storage item `session` = `alice`, sets the cookie `session=alice`, and goes to
  `welcome.html`.
- Anything else shows an alert (role `alert`) reading "Wrong username or password" and stays; a
  second failure replaces the alert rather than adding one.
- The session lives in local storage because Chromium gives `file://` pages no cookies. Served over
  http, the cookie is set as well, which is what the storage tests checkpoint and restore.
- Two elements on the login page read "Log in" (the heading and the button) and two are inputs, on
  purpose: the contract suite checks that a target naming more than one element is refused.

## Using it

- As files: `(SITE_DIR / "login.html").as_uri()`, where `SITE_DIR` is this directory
  (`contracts.browser_harness.SITE_DIR`).
- Over http: serve this directory with any static server, for example
  `python -m http.server --directory packages/hivemind/tests/fixtures/sites/login`.
