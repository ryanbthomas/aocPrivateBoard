# aocPrivateBoard

## What is it?
A private, invite-only web app for a group of fewer than 20 people participating in
Advent of Code. It does four things:

1. **Mirrors the AoC private leaderboard** — fetched server-side, cached, rendered with our own scoring on top.
2. **Links to each day's puzzle** — opens the official Advent of Code page in a new tab; puzzle statements are not fetched or reproduced.
3. **Accepts solution uploads** — files or pasted text, stored content-addressed, attributed to a claimed AoC identity.
4. **Compares submissions** — static analysis only: metrics, side-by-side diffs, similarity fingerprints. **Uploaded code is never executed.**

Everything is authenticated. There is no public read path.


## Getting Started

```sh
# clone git repo
# install python 3.14
# install pipx
# install uv
# install make
# copy .env.example to .env and set local values as needed
# make dev


```

For production, provide settings through `/etc/aocboard/env` with mode `0600`.
The AoC session cookie belongs only to the worker: supply it through the worker's
environment or systemd `LoadCredential=` at `$CREDENTIALS_DIRECTORY/aoc_session`.
Never commit `.env` or pass the AoC cookie to the web process.

## More Info

Overall plan and design decisions are recorded in [PLAN.md](docs/PLAN.md)
