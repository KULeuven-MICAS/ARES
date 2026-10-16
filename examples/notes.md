# Example optimization notes

This file shows the format of an optimization-notes file for `--md`.
ARES sends the notes to the model as optimization directions that helped on
other designs; the model must test them against the design that it optimizes.
Each note states a direction and, when known, its result.

## Directions that lowered the figure of merit

- Compute an absolute difference with one subtraction and a conditional
  negation of the result, instead of a comparator and two subtractors.
- Share one adder tree between results that are needed in the same cycle.
- Remove logic whose result never reaches an output.

## Directions that did not help (do not repeat)

- Replacing a binary adder tree by a chain of adders increased the delay
  more than it reduced the area.
