# LitBrief repository guidance

- Python 3.10+ standard-library runtime. Entry docs: README.md, docs/architecture.md, docs/journal-selection.md.
- Read the affected implementation and keep changes small. Preserve journal selection, explicit user exclusions, DOI identity checks, evidence provenance and published snapshots.
- Run `python -m unittest discover -s tests -v`. Tests use synthetic data and temporary databases; do not run paid AI or publisher collection as routine CI or code review.
- Never commit `.env`, credentials, institution cookies, local documents or anything under `outputs/`. Do not request these to review the code.
- Publisher probes default to offline configuration checks. `--live` needs a configured, entitled provider. Do not weaken TLS, bypass browser challenges or confuse a valid key with data entitlement.
- Keep AI and collection concurrency low; existing AI execution is serial. A group of papers in one request is not parallel networking.
- Do not enable automatic publication, restore retired journals, add conferences or move sources into monthly operation as a side effect of refactoring. Proposed features in docs are not implemented features.
- Public pages contain approved snapshots and generated short commentary, not raw abstracts, internal reviews or provider usage logs. The admin server remains loopback-only.
- Frontend uses native HTML/CSS/JS and self-hosted fonts. Preserve accessible names and keyboard focus for icon-only buttons, theme persistence and mobile reading.
- Synchronize nearby docs when behavior changes. Report actual checks and remaining external dependencies. No broad cleanup or speculative infrastructure.
