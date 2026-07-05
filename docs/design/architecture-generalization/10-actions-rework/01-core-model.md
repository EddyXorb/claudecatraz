# 01 — Core model: `Action`, `Criticality`, `Recognizer`, `EndpointType`

Derives from main document §2.1, §2.2, §4. Depends on: nothing. Purely
additive — no existing file is deleted or rewired in this step.

## Goal

The guard-agnostic types every namespace builds on. Core owns the *types*,
never a vocabulary: no concrete action id may appear in `core/` (tests may use
made-up ids like `"x.y"`).

## Create

* `warden/warden/core/actions.py`
  ```python
  class Criticality(IntEnum):
      READ = 0
      WRITE = 1
      IRREVERSIBLE = 2

  @dataclass(frozen=True)
  class Action:
      id: str
      criticality: Criticality
  ```
  `Action` is hashable and compared by value; ids are opaque strings to core
  (never parsed, no grammar knowledge).

* `warden/warden/core/recognizer.py`
  ```python
  IntentT = TypeVar("IntentT", bound=Intent)

  class Recognizer(ABC, Generic[IntentT]):
      id: str
      @abstractmethod
      def matches(self, intent: IntentT) -> bool: ...
      @abstractmethod
      def recognize(self, intent: IntentT) -> frozenset[Action]: ...
  ```
  Plus the one generic helper `first_match(catalog, intent)` returning the
  first matching recognizer or `None` (first match wins — catalog order is
  meaningful, most specific rows first).

  Concrete note for implementers: `recognize` may return an empty set even
  when `matches` is true — that is the fail-closed outcome for a matched
  endpoint whose fields carry an unknown value (main doc §2.2); an empty set
  always leads to deny.

* `warden/warden/core/endpoints.py`
  ```python
  @dataclass(frozen=True)
  class EndpointType:
      name: str                      # the toml `type` value, e.g. "gitlab"
      guards: tuple[str, ...]        # guard names composing this type
  ```
  Keep `guards` as names (strings), not classes — core must not import guard
  packages. Resolution from name to instance happens at assembly (step 04).

## Constraints

* No imports from `warden.guards.*` anywhere in `core/` — add nothing that
  would recreate the old backwards dependency.
* Docstring rules from `00-index.md` apply — these are brand-new files, they
  set the tone for the whole rework.

## Tests (new: `warden/tests/core/test_actions_model.py`)

* `Criticality` ordering: `READ < WRITE < IRREVERSIBLE`.
* `Action` equality/hash by value; usable in `frozenset`.
* A dummy `Recognizer` subclass over a dummy intent: `first_match` returns the
  first of two overlapping rows; returns `None` on no match.
* A matched recognizer returning `frozenset()` is a legal outcome (contract
  test — no exception raised).

## Verification

Warden commands from `00-index.md` step 4. Everything must stay green — this
step adds code without changing behavior.

## Commit

```
warden: add core action/recognizer/endpoint-type model
```

Identity/docstring rules: see `00-index.md` (hard rules). Flip this step's
Status in `00-index.md` in the same commit.
