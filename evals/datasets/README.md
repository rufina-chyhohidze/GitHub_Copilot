# datasets

`repository-qa-v1.json` contains 20 questions: 14 development cases and 6 held-out cases. Fourteen questions cover the local Tiny Shop fixture; six cover pinned versions of two public Python projects.

Each case records its question, expected files, supporting line ranges with anchor text, required facts, uncertainty policy, and any explicitly forbidden claims. Expected spans are sufficient supporting context, not a requirement that the model reproduce the identical citation range.

## Why these sources

- **Tiny Shop:** fully controlled examples make ambiguity, absent features, indirect calls, and prompt injection easy to assess. Every file is hashed so adding a feature cannot silently invalidate an absence case.
- **[sampleproject](https://github.com/pypa/sampleproject/tree/621e4974ca25ce531773def586ba3ed8e736b3fc):** a small real packaging example tests entrypoint and configuration lookup.
- **[itsdangerous](https://github.com/pallets/itsdangerous/tree/672971d66a2ef9f85151e53283113f33d642dabd):** signing and serialization code tests real control flow and inherited behavior. A signing library is not itself a complete application authentication system.

Public source is not vendored. The dataset pins full commit IDs and hashes the files used as evidence; verification requires an unchanged checkout at the expected commit. No dependencies from those projects are installed or executed.

## Validate locally

From `backend/`:

```sh
uv sync --locked
uv run repo-copilot-eval
```

This validates the dataset schema and local fixture, and explicitly reports the public repositories as unverified until their checkouts are supplied. It makes no network calls, requires no database or model credentials, and does not measure answer quality.

To verify public cases too, clone into an ignored directory from the repository root:

```sh
git clone https://github.com/pypa/sampleproject.git .workspaces/evals/sampleproject
git -C .workspaces/evals/sampleproject checkout --detach 621e4974ca25ce531773def586ba3ed8e736b3fc
git clone https://github.com/pallets/itsdangerous.git .workspaces/evals/itsdangerous
git -C .workspaces/evals/itsdangerous checkout --detach 672971d66a2ef9f85151e53283113f33d642dabd
cd backend
uv run repo-copilot-eval \
  --checkout sampleproject=../.workspaces/evals/sampleproject \
  --checkout itsdangerous=../.workspaces/evals/itsdangerous \
  --require-all
```

## Review rubric and split policy

Use development questions to tune retrieval and prompts. Reserve held-out questions for milestone evaluation; do not insert their labels into prompts or few-shot examples. The split is by question and shares repositories, so it measures new questions on known projects, not generalization to unseen repositories.

A reviewer marks a case as passing only when all required facts are conveyed, citations support the factual claims, the uncertainty policy is followed, and no forbidden claim appears. Equivalent wording and additional correct evidence are acceptable; matching a keyword is not enough.

For absence questions, expected files are context supporting a scoped conclusion, not proof from a failed search. For the adversarial case, retrieval should find both the misleading comment and actual implementation; the answer must follow the user's question.

Results must identify the exact dataset fingerprint, pipeline version, model, and selected split. Every selected case must appear, including failures and skips, so omitted failures cannot inflate results. `EvaluationRun` supports JSON serialization and validation against the dataset; automated metrics and a model runner are deferred to Step 7.
