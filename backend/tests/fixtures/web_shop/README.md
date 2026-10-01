# Web shop syntax fixture, v1.0.0

Read-only source for JS, JSX, TypeScript, and TSX parsing and repository-Q&A evaluation. Never install or execute it. `routes.js` saves orders and creates an event payload; `store.ts` uses an in-memory Map. The other modules cover JSX, default exports, type-only imports, repeated names, unresolved CommonJS, and deliberately malformed syntax. An adversarial comment in `events.js` is untrusted source text.

Import specifiers intentionally omit extensions. This fixture provides syntax evidence, not proof of runtime module resolution, persistent storage, network publication, or encryption. `legacy.cjs` refers to an implementation that is not included. The versioned manifest and question rubrics are in `evals/datasets/web-qa-v1.json`.
