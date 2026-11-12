# Contributing

Thanks for helping. Please read the [README](README.md) first; maintainers also use [AGENTS.md](AGENTS.md).

## Report a problem

Include:

- the command or tool call, and the release (`./label-history describe-release`);
- the setid, version and section involved;
- what you expected and what you got;
- an official source or raw-file hash that supports your point.

Say whether you think the fault is in the source data, the parsing, the matching, or the docs. Similar drug
names are not the same label family, and DailyMed posting dates, label effective dates and retrieval times are
different things; please say which you mean.

## Where fixes go

- **Parsing or text extraction:** fix the parser and publish a new versioned build. Old outputs stay.
- **Matching or change rules:** describe the changed rule and the regressions it causes.
- **Source data:** keep the original response and add any better evidence as a new build.
- **CLI or MCP:** change the shared query code in `gitrx_backend`, not one interface.

Generated label text is not a place for hand-written corrections. Never rewrite stored label text to make a query pass.

## Pull requests

Describe the problem and how you checked the fix. Add focused tests, especially for gaps, repeated sections, partial
extraction, paging and limits. Tests that need the data folder skip without it. Unit-test counts do not establish clinical
validity. Please say what was human-reviewed and what was AI-assisted.

Do not open sealed validation answers or add recurring collection. Licensing choices are the owner's ([LICENSING.md](LICENSING.md)).
