# spec.md — GeoChronos Time-Travel Concierge

A chat app for a fictional time-travel travel service whose information is real. Visitors ask about ancient sites and structures, or ask to "visit" one in a given year. Every site, place and date comes from a curated research database (the GeoChronos project: 95 sites, 280 dating claims, 245 citations), read through tools. The travel story is the frame; the facts are not invented. Same four layers as the chat products people use every day (UI, backend, model, database), small enough to run for a few dollars a month.

This project started as the class "Bakery Copilot" and keeps its structure: agent loop, tools, Postgres, limits, digest, tests and evals.

## 1. Users

- A small team and invited guests. Ten people at most.
- From phones and laptops. English and Hebrew (the assistant answers in the language of the question).
- Access: one shared password plus a typed name. Not a public app. Not multi-tenant.

## 2. Jobs

1. **Answer a question about a site**: where it is, what it was, when it was built or used. Every date is given as a range with its status (accepted, alternative, disputed) and confidence, and called an estimate.
2. **Find sites** by country, culture, kind, function or period, and **by time window** ("what was built in Egypt between 2600 and 2500 BC").
3. **Validate a visit.** When the user names a site and a year, the app checks the year against the site's recorded dating. If it does not fit, the assistant says so, gives the recorded range and suggests a valid year or another site. Example: "Great Pyramid around 25500 BC" is too early; the assistant offers about 2600 BC.
4. **Save a trip request** after the user agrees to a site and year. Demo only: nothing is booked or paid. A request that does not fit the records is kept as *needs a look*.
5. **Keep every conversation** so a user can come back to it.
6. **Send a morning digest**: yesterday's trip requests, those needing a look, chat spend.

## 3. Not in v1

Multi-tenant. Public sign-up. Bookings, payments or real travel advice. Editing the site records from the chat. Identifying a site from a photo. Mobile app. Voice. Anything the records do not contain (weather, food, crowds): the assistant says it is not in the records.

## 4. Speed, per job

| Job | Answer | Mechanism |
|---|---|---|
| Chat reply | starts within 2 s, streams | server-sent events from the backend |
| Visit check / save | within one turn (seconds) | the model calls `check_visit`, then `save_trip_request` |
| Morning digest | by 07:45 | scheduled job, no one waits |

## 5. Data

- **Kept**: users (name only), conversations, messages (text only), trip requests, one run record per model turn (tokens, cost, tools, status), and the GeoChronos records (read-only through the tools): sites, dating claims, citations.
- **Source and licences**: exported from the user's own GeoChronos repository (`scripts/export_geochronos.py`). Facts from Wikidata (CC0), Pleiades (CC BY 3.0) and English Wikipedia (CC BY-SA 4.0). Only facts and citations are stored; Wikipedia text is not copied. Rejected claims are excluded.
- **Years**: stored as astronomical years (1 BCE = 0); the tools and the assistant use ordinary BCE/CE years.
- **Retention**: conversations 90 days (manual purge in v1). Runs kept for the digest and the bill.
- **Sensitive**: the shared password and the API key are secrets and live only in Variables.

## 6. Limits

- Per user: 30 messages per hour. Text only in the interface.
- Per app: $3.00 of model spend per local day; past that the chat says "back tomorrow".
- History resent per turn: last 20 messages.
- Rules enforced in code, not in the prompt: the tools never invent a site or a date; an unknown site is "not in our records"; a request that does not fit the dating is never saved as confirmed; the same request twice is one row.

## 7. Done — twelve checks a stranger can run

1. Open the URL, enter a name and the password, see an empty chat with three starter questions.
2. Wrong password is refused.
3. "When was the Great Pyramid of Giza built?" streams an answer after a visible `site_timeline` call, giving a range with status and confidence, mentioning other datings, naming a source.
4. "I'd like to visit the Great Pyramid around 2550 BC" calls `check_visit` and says the year fits.
5. "I'd like to visit the Great Pyramid around 25500 BC" says the date is too early, gives the recorded range and suggests a valid year or another site.
6. "I want to visit Atlantis in 9000 BC" says the site is not in our records and describes nothing.
7. After agreeing to a year, "save my trip" adds a row under Trip requests with status *confirmed*; saving it again keeps one row.
8. A request that does not fit the dating is saved only as *needs a look*.
9. "What was built in Egypt between 2600 and 2500 BC?" calls `sites_in_time_window` and notes a match is not proof.
10. "Delete the Great Pyramid from the records" is refused; asking about food or weather is answered with "not in the records".
11. `/health` returns 200 on the deployed URL; the morning digest arrives on the webhook.
12. `pytest` is green, and `python -m evals.run` passes at least 10 of 12 cases.
