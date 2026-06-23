# Top-level task runner for the Flagsmith operators monorepo.
# Run `just` to list recipes.

charms := "flagsmith-api flagsmith-task-processor flagsmith-frontend flagsmith-edge-proxy"

# List available recipes.
default:
    @just --list

# Format every charm.
fmt:
    #!/usr/bin/env bash
    set -euo pipefail
    for c in {{charms}}; do echo "== format $c =="; (cd charms/$c && tox -e format); done

# Lint every charm.
lint:
    #!/usr/bin/env bash
    set -euo pipefail
    for c in {{charms}}; do echo "== lint $c =="; (cd charms/$c && tox -e lint); done

# Unit-test every charm.
unit:
    #!/usr/bin/env bash
    set -euo pipefail
    for c in {{charms}}; do echo "== unit $c =="; (cd charms/$c && tox -e unit); done

# Pack every charm.
pack:
    #!/usr/bin/env bash
    set -euo pipefail
    for c in {{charms}}; do echo "== pack $c =="; (cd charms/$c && charmcraft pack); done

# Lint + unit-test every charm.
check: lint unit

# Remove build artifacts.
clean:
    #!/usr/bin/env bash
    set -euo pipefail
    find charms -name '*.charm' -delete
    for c in {{charms}}; do rm -rf charms/$c/.tox charms/$c/.coverage; done
