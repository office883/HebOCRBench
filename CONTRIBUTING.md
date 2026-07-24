# Contributing

Changes to scoring behavior require:

1. a failing test demonstrating the intended Hebrew/Unicode behavior;
2. a minimal implementation;
3. a sanity fault proving the metric responds correctly;
4. documentation of backward compatibility;
5. a schema/evaluator version decision.

Never add a “helpful” normalization to the strict score without an explicit
versioned policy change. In particular, do not reverse strings, remove niqqud,
map final letters or fold Hebrew punctuation.
