# evaluation

`dataset.py` defines and loads versioned questions, source identities, and evidence spans. Its validator reads source files and checks hashes, line ranges, and anchor text without importing or running them.

`results.py` defines future answer-run output, including usage, errors, citations, and explicit review status. `cli.py` checks dataset integrity now; answer generation and quality scoring arrive later.
