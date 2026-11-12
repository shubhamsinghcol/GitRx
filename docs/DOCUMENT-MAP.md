# Document map

## Using GitRx

| I want to | Read |
|---|---|
| Install and run the first query | [README](../README.md) |
| Query from the command line | [Core workflow](CORE-WORKFLOW.md) |
| Use an AI assistant | [MCP](../MCP.md) |
| Use the Git export | [Git export](GIT-EXPORT.md) |
| Understand evidence and citations | [Evidence and wording history](EVIDENCE-HISTORY.md) |
| Know what the data can and cannot show | [Limitations](../LIMITATIONS.md), [Sources](../SOURCES.md), [Known flaws in DailyMed and SrLC](SOURCE-DATA-ISSUES.md) |
| Understand query limits and paging | [Query limits](QUERY-PERFORMANCE.md) |
| Report a problem or contribute | [Contributing](../CONTRIBUTING.md), [Security](../SECURITY.md) |
| Check licensing | [LICENSING](../LICENSING.md) |

## Validation evidence (for verification)

How accurate GitRx is and how to check it. Start with the plain-language page.

- [Accuracy in plain language](ACCURACY-IN-PLAIN-LANGUAGE.md)
- [VERIFY.md](../VERIFY.md): commands and hashes to re-run the checks
- [Validation reasoning](VALIDATION-REASONING.md): how each number was computed
- [Authoritative parser plan](AUTHORITATIVE-PARSER-PLAN.md): the pre-registered plan and sealed results
- [Validation correction, 2026-11-10](VALIDATION-CORRECTION-2026-11-10.md): earlier scores were AI-graded
- [Step F gap repair](STEP-F-GAP-REPAIR.md)
- Draft proposals for external validation: [100-family protocol](VALIDATION-100-FAMILIES-PROTOCOL-DRAFT.md), [local runner](VALIDATION-100-LOCAL-RUNNER.md)

## For maintainers

Operating and development notes; users do not need these.

- [Packaging and configuration](BACKEND-PACKAGING.md), [Repository layout](REPOSITORY-LAYOUT.md)
- [Native contract](PRODUCTION-CONTRACT.md), [Qualification](QUALIFICATION.md), [Performance record](PERFORMANCE-RECORD.md)
- [Update jobs](../ops/UPDATES.md), [Evidence recovery](RECOVERY.md)
- [AGENTS.md](../AGENTS.md)

## Project records (maintainers)

Historical records, kept unchanged. A heading such as "latest", "running" or "pending" in an old record
is not current.

- [Core product roadmap](CORE-PRODUCT-ROADMAP.md)
- [Cleanup, 2026-11-11](CLEANUP-2026-11-11.md), [deletion proposal](DELETION-PROPOSAL-2026-11-11.md), [old release fingerprints](OLD-RELEASE-FINGERPRINTS-1baa5bca.md)
- [Archived guides](history/2026-11-11/README.md), [earlier navigation](history/2026-11-08/START-HERE.md), [DailyMed collection history](history/dailymed/)
- `reports/`: experiments, audits and incident records
