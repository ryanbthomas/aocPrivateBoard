# aocPrivateBoard

## What is it?
A private, invite-only web app for a group of fewer than 20 people participating in
Advent of Code. It does four things:

1. **Mirrors the AoC private leaderboard** — fetched server-side, cached, rendered with our own scoring on top.
2. **Shows each day's puzzle** — fetched once, parsed, cached, rendered behind login with attribution and a link back to adventofcode.com.
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
# make dev


```

## More Info

Overall plan and design decisions are recorded in [PLAN.md](docs/PLAN.md)
